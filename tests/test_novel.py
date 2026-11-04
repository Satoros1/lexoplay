"""Текст новеллы из папки игры (novel) и слова по частям (novelwords): синтетические папки разных движков."""

from __future__ import annotations

import json
import pickle
import random
import struct
import sys
import types
import zlib
from pathlib import Path

import pytest

from lexoplay import novel, novelwords, prelearn, rarity

WORDS = (
    "the you and to of it that is was what this my me not your have know just do with but for be "
    "on are there so all like can think want go get here right now well about one how why out see "
    "time come back look really need tell something never take make way people should feel still "
    "reluctant meticulous gaze shrug linger stumble grin murmur fidget nod frown solemn"
).split()


def lines(n: int, seed: int, rare: str = "") -> list[str]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        ws = [rnd.choice(WORDS) for _ in range(rnd.randint(6, 12))]
        if rare and i % 5 == 0:
            ws.insert(2, rare)
        out.append(" ".join(ws).capitalize() + ".")
    return out


# ------------------------------------------------------------------ Ren'Py
def _renpy_classes() -> dict[str, type]:
    """Классы с именами как в Ren'Py, чтобы собрать настоящий .rpyc (тест сам себе игра)."""
    mod = sys.modules.get("renpy.ast")
    if mod is None:
        pkg = types.ModuleType("renpy")
        mod = types.ModuleType("renpy.ast")
        for name in ("Say", "Menu", "Label", "PyExpr"):
            if name == "PyExpr":
                cls = type(name, (str,), {"__module__": "renpy.ast", "__getnewargs__": lambda self: (str(self), "f", 1)})
            else:
                cls = type(name, (), {"__module__": "renpy.ast"})
            setattr(mod, name, cls)
        pkg.ast = mod  # type: ignore[attr-defined]
        sys.modules["renpy"] = pkg
        sys.modules["renpy.ast"] = mod
    return {n: getattr(mod, n) for n in ("Say", "Menu", "Label", "PyExpr")}


def make_rpyc(say_lines: list[str]) -> bytes:
    c = _renpy_classes()
    stmts = []
    for s in say_lines:
        say = c["Say"]()
        say.who, say.what = "e", s
        stmts.append(say)
    menu = c["Menu"]()
    menu.items = [("What now?", None, None), ("Stay a little longer here", c["PyExpr"]("True"), [])]
    label = c["Label"]()
    label.name, label.block = "start", [*stmts, menu]
    data = zlib.compress(pickle.dumps(({"version": 5}, [label]), protocol=2))
    head = b"RENPY RPC2"
    start = len(head) + 12 * 2
    return head + struct.pack("<III", 1, start, len(data)) + struct.pack("<III", 0, 0, 0) + data


def make_rpa(path: Path, files: dict[str, bytes], key: int = 0x42424242) -> None:
    body = b""
    index = {}
    offset = 34
    for name, data in files.items():
        index[name] = [(offset ^ key, len(data) ^ key, b"")]
        body += data
        offset += len(data)
    idx = zlib.compress(pickle.dumps(index, protocol=2))
    header = f"RPA-3.0 {offset:016x} {key:08x}\n".encode()
    assert len(header) == 34
    path.write_bytes(header + body + idx)


def test_renpy_rpy_and_rpa_rpyc_by_acts(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()
    rpy = "\n".join(
        ['label start:', '    scene bg school', '    $ x = 1', *(f'    hi "{s}"' for s in lines(300, 1, "reluctant"))]
    )
    (game / "script-a1-monday.rpy").write_text(rpy, encoding="utf-8")
    (game / "screens.rpy").write_text('screen x:\n    text "Should not count as a line here"\n', encoding="utf-8")
    make_rpa(game / "data.rpa", {"script-a2-emi.rpyc": make_rpyc(lines(300, 2, "meticulous")), "x.png": b"\x89PNG"})
    assert novel.detect(tmp_path).key == "renpy"
    script = novel.extract(tmp_path)
    assert [p.title for p in script.parts] == ["Акт 1", "Акт 2"]
    assert all("Should not count" not in s for p in script.parts for s in p.lines)
    assert "Stay a little longer here" in script.parts[1].lines
    assert "What now?" not in script.parts[1].lines  # подпись меню без блока не выбор
    assert sum("meticulous" in s for s in script.parts[1].lines) >= 60


def test_safe_unpickle_never_runs_code(tmp_path: Path) -> None:
    class Boom:
        def __reduce__(self):  # type: ignore[no-untyped-def]
            return (print, ("pwned",))

    obj = novel._unpickle(pickle.dumps([Boom()], protocol=2))
    assert isinstance(obj, list) and isinstance(obj[0], novel._Stub)


# ------------------------------------------------------------------ RPG Maker MV/MZ
def test_rpgmaker_mv(tmp_path: Path) -> None:
    data = tmp_path / "www" / "data"
    data.mkdir(parents=True)
    (data / "System.json").write_text("{}")
    cmds = [{"code": 101, "parameters": ["", 0, 0, 2]}]
    for s in lines(400, 3):
        cmds.append({"code": 401, "parameters": ["\\C[2]" + s]})
    cmds.append({"code": 102, "parameters": [["Go to the library together", "Wait outside for a while"], 1]})
    (data / "Map001.json").write_text(json.dumps({"events": [None, {"pages": [{"list": cmds}]}]}))
    assert novel.detect(tmp_path).key == "rpgmaker"
    script = novel.extract(tmp_path)
    flat = [s for p in script.parts for s in p.lines]
    assert "Go to the library together" in flat
    assert not any("\\C" in s for s in flat)


# ------------------------------------------------------------------ Tyrano (папка и app.asar)
def _ks(n: int, seed: int) -> str:
    out = ["*start", "[cm]", "#Akane"]
    for s in lines(n, seed):
        out.append(s + "[p]")
    out += ["[iscript]", "var a = 'this is code and not a line at all';", "[endscript]"]
    return "\n".join(out)


def test_tyrano_asar(tmp_path: Path) -> None:
    files = {"data/scenario/ep01.ks": _ks(250, 4).encode(), "data/scenario/ep02.ks": _ks(250, 5).encode()}
    tree: dict = {"files": {}}
    blob = b""
    for name, raw in files.items():
        node = tree
        *dirs, leaf = name.split("/")
        for d in dirs:
            node = node["files"].setdefault(d, {"files": {}})
        node["files"][leaf] = {"offset": str(len(blob)), "size": len(raw)}
        blob += raw
    js = json.dumps(tree).encode()
    head = struct.pack("<IIII", 4, len(js) + 8, len(js) + 4, len(js))
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "app.asar").write_bytes(head + js + blob)
    assert novel.detect(tmp_path).key == "tyrano"
    script = novel.extract(tmp_path)
    assert [p.title for p in script.parts] == ["Эпизод 1", "Эпизод 2"]
    assert not any("this is code" in s for p in script.parts for s in p.lines)
    assert not any("[p]" in s for p in script.parts for s in p.lines)


# ------------------------------------------------------------------ NScripter (nscript.dat, XOR 0x84)
def test_nscripter_xor_and_ponscripter(tmp_path: Path) -> None:
    body = "\n".join(["*define", "game", "*start", *(f"`{s}\\" for s in lines(400, 6)), "^Hello there, said the stranger quietly^@"])
    (tmp_path / "nscript.dat").write_bytes(bytes(b ^ 0x84 for b in body.encode()))
    assert novel.detect(tmp_path).key == "nscripter"
    flat = [s for p in novel.extract(tmp_path).parts for s in p.lines]
    assert "Hello there, said the stranger quietly" in flat
    assert len(flat) > 300


# ------------------------------------------------------------------ KiriKiri .xp3
def make_xp3(path: Path, files: dict[str, bytes]) -> None:
    head = b"XP3\r\n \n\x1a\x8b\x67\x01"
    body = b""
    entries = b""
    pos = len(head) + 8
    for name, raw in files.items():
        packed = zlib.compress(raw)
        nm = name.encode("utf-16-le")
        info = struct.pack("<IQQH", 0, len(raw), len(packed), len(name)) + nm
        segm = struct.pack("<IQQQ", 1, pos + len(body), len(raw), len(packed))
        chunk = b"info" + struct.pack("<Q", len(info)) + info + b"segm" + struct.pack("<Q", len(segm)) + segm
        entries += b"File" + struct.pack("<Q", len(chunk)) + chunk
        body += packed
    index_at = len(head) + 8 + len(body)
    idx = zlib.compress(entries)
    path.write_bytes(head + struct.pack("<Q", index_at) + body + b"\x01" + struct.pack("<QQ", len(idx), len(entries)) + idx)


def test_kirikiri_xp3_and_encrypted(tmp_path: Path) -> None:
    ks = "\n".join(["*start", "@bg storage=room", *(s + "[l][r]" for s in lines(400, 7))])
    make_xp3(tmp_path / "data.xp3", {"scenario/day1.ks": ks.encode("utf-16"), "scenario/day2.ks": ks.encode("utf-16")})
    assert novel.detect(tmp_path).key == "kirikiri"
    script = novel.extract(tmp_path)
    assert [p.title for p in script.parts] == ["День 1", "День 2"]

    enc = tmp_path / "enc"
    enc.mkdir()
    noise = bytes(random.Random(1).randrange(256) for _ in range(20000))
    make_xp3(enc / "data.xp3", {"scenario/day1.ks": noise})
    with pytest.raises(novel.NovelError, match="зашифрован"):
        novel.extract(enc)


# ------------------------------------------------------------------ GameMaker data.win
def test_gamemaker_strg(tmp_path: Path) -> None:
    strings = [*lines(400, 8), "obj_player", "spr_bar_01", "global.money = 5"]
    blobs = b""
    base = 8 + 8 + 4 + 4 * len(strings)
    offs = []
    for s in strings:
        raw = s.encode()
        offs.append(base + len(blobs))
        blobs += struct.pack("<I", len(raw)) + raw + b"\0"
    strg = struct.pack("<I", len(strings)) + b"".join(struct.pack("<I", o) for o in offs) + blobs
    form = b"STRG" + struct.pack("<I", len(strg)) + strg
    (tmp_path / "data.win").write_bytes(b"FORM" + struct.pack("<I", len(form)) + form)
    assert novel.detect(tmp_path).key == "gamemaker"
    flat = [s for p in novel.extract(tmp_path).parts for s in p.lines]
    assert "obj_player" not in flat and "global.money = 5" not in flat
    assert len(flat) >= 380


# ------------------------------------------------------------------ честное «нельзя»
@pytest.mark.parametrize(
    ("files", "key"),
    [
        (["UnityPlayer.dll", "Game_Data/globalgamemanagers"], "unity"),
        (["nss.npa"], "nitroplus"),
        (["data.paz"], "minori"),
        (["Game.rgss3a"], "rgss"),
        (["dr1_data.wad"], "spike"),
    ],
)
def test_unreadable_engines_say_why(tmp_path: Path, files: list[str], key: str) -> None:
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"x")
    eng = novel.detect(tmp_path)
    assert eng.key == key and not eng.readable and eng.why
    with pytest.raises(novel.NovelError):
        novel.extract(tmp_path)


def test_unknown_engine_loose_text_and_other_languages(tmp_path: Path) -> None:
    (tmp_path / "text").mkdir()
    (tmp_path / "text" / "chapter1.txt").write_text("\n".join(lines(300, 9)), encoding="utf-8")
    (tmp_path / "text" / "chapter1_fr.txt").write_text("\n".join(lines(300, 10)), encoding="utf-8")
    (tmp_path / "LICENSE.txt").write_text("\n".join(lines(300, 11)), encoding="utf-8")
    eng = novel.detect(tmp_path)
    assert eng.key == "unknown"
    script = novel.extract(tmp_path)
    assert script.words < 300 * 12  # только chapter1.txt
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(novel.NovelError, match="не нашлось"):
        novel.extract(empty)


def test_long_linear_script_split_in_parts() -> None:
    chunks = [("0.txt", lines(15000, 12))]  # ~135 тыс. слов
    parts, note = novel.split_parts(chunks, ordered=True)
    assert 2 <= len(parts) <= 4 and parts[0].title == "Часть 1" and note
    assert abs(parts[0].words - parts[1].words) < parts[0].words * 0.2


# ------------------------------------------------------------------ слова
def _script() -> novel.Script:
    p1 = novel.Part("Акт 1", ["Hisao looked at the reluctant cat.", *lines(200, 13)] + ["She was reluctant again and reluctant."] * 3)
    p2 = novel.Part("Акт 2", ["Hisao smiled with a meticulous grin."] * 5 + ["The meticulous plan worked, meticulous as ever."] * 2)
    return novel.Script(novel.Engine("renpy", "Ren'Py", True), [p1, p2])


def test_counts_skip_names_and_decks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEXOPLAY_HOME", str(tmp_path))
    nv = novelwords.build("ks.exe", "Katawa Shoujo", tmp_path, _script())
    assert all("hisao" not in p.counts for p in nv.parts)  # имя: со строчной ни разу
    assert nv.parts[0].counts.get("reluctant", 0) >= 7
    d = novelwords.deck(nv, 1, "b1", lambda w: False)
    assert d.words[0].lemma == "meticulous" and d.words[0].here == 9
    assert d.after > d.now and d.hits >= 9
    nv.skipped.add("meticulous")
    assert all(w.lemma != "meticulous" for w in novelwords.deck(nv, 1, "b1", lambda w: False).words)
    novelwords.save(nv)
    back = novelwords.load("ks.exe")
    assert back is not None and back.parts[1].counts == nv.parts[1].counts and "meticulous" in back.skipped
    assert "Ren'Py" in novelwords.summary(back, "b1", lambda w: False) and "2 акта" in novelwords.summary(back, "b1", lambda w: False)


def test_lemma() -> None:
    assert rarity.lemma("Stopped") == "stop"
    assert rarity.lemma("talked") == "talk"
    nv = novel.Script(novel.Engine("x", "x", True), [novel.Part("Вся новелла", ["They linger here.", "Lingering, lingered."])])
    assert novelwords.count(nv)[0].counts == {"linger": 3}  # нет в частотном списке: к форме из самой новеллы


def test_prelearn_parse_keeps_order_and_drops_junk() -> None:
    raw = (
        '{"word": "linger", "lemma": "linger", "pos": "verb", "ipa": "/ˈlɪŋɡə/", "meaning": "задерживаться — медлить", '
        '"example": "We lingered over coffee.", "example_tr": "Мы засиделись за кофе."}\n'
        '{"word": "other", "meaning": "x"}\n'
        '{"word": "gaze", "lemma": "gaze", "meaning": "взгляд", "example": "Her gaze was calm.", "example_tr": "Её взгляд был спокоен."}'
    )
    items = prelearn.parse(raw, ["gaze", "linger", "missing"])
    assert [i.word for i in items] == ["gaze", "linger"]
    assert "—" not in items[1].meaning and items[1].ipa == "ˈlɪŋɡə"
    assert "Katawa" in prelearn.build("Katawa Shoujo", ["gaze"])


# ------------------------------------------------------------------ окно «Слова новеллы»
@pytest.fixture(scope="module")
def qapp():  # type: ignore[no-untyped-def]
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class NovelCtl:
    def __init__(self, nv: novelwords.Novel) -> None:
        self.nv = nv
        self.made: list[tuple[list[str], list[str]]] = []
        self.scanned: list[str | None] = []

    def novel_info(self, exe: str) -> novelwords.NovelView:
        ready = novelwords.readiness(self.nv, "b1", lambda w: False)
        return novelwords.NovelView(
            "ready",
            summary=novelwords.summary(self.nv, "b1", lambda w: False),
            parts=[(p.title, r) for p, r in zip(self.nv.parts, ready, strict=True)],
        )

    def scan_novel(self, exe: str, folder: str | None = None) -> bool:
        self.scanned.append(folder)
        return True

    def novel_deck(self, exe: str, index: int) -> novelwords.Deck:
        return novelwords.deck(self.nv, index, "b1", lambda w: False)

    def make_novel_deck(self, exe, chosen, skipped, done) -> None:  # type: ignore[no-untyped-def]
        self.made.append((chosen, skipped))
        done(f"Готово: {len(chosen)} слов.", None)


def test_novel_dialog_picks_words(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt

    from lexoplay.store import Game
    from lexoplay.ui.novel_dialog import NovelDialog, card_text, novel_view

    nv = novelwords.build("ks.exe", "Katawa Shoujo", tmp_path, _script())
    ctl = NovelCtl(nv)
    dlg = NovelDialog(ctl, Game("ks.exe", "Katawa Shoujo"))
    assert dlg.parts.count() == 2 and "Акт 1" in dlg.parts.itemText(0)
    dlg.parts.setCurrentIndex(1)
    assert dlg.list.count() >= 1 and dlg.list.item(0).data(Qt.ItemDataRole.UserRole) == "meticulous"
    assert "—" not in dlg.deck_info.text()
    dlg.list.item(0).setCheckState(Qt.CheckState.Unchecked)
    dlg.add.click()
    chosen, skipped = ctl.made[0]
    assert skipped == ["meticulous"] and "meticulous" not in chosen
    assert dlg.result.text().startswith("Готово")
    assert card_text(novel_view(ctl, "ks.exe"))[0].startswith("Ren'Py")
    assert novel_view(object(), "x") is None
    dlg.close()
    import shiboken6

    shiboken6.delete(dlg)  # окно без родителя: удалить до QApplication, иначе падение на выходе


# ------------------------------------------------------------------ переводы на другие языки и имена
ES = "Que quieres de mí, por favor dime la verdad. Yo no sé qué pasa con ella, pero es muy raro."
DE = "Ich weiß nicht, was das ist, aber sie ist schon wieder da und mein Kopf tut weh."


def test_other_language_files_and_lines_dropped(tmp_path: Path) -> None:
    game = tmp_path / "game"
    game.mkdir()
    en = [f'    hi "{s}"' for s in lines(300, 21, "reluctant")]
    (game / "script-a1-monday.rpy").write_text(
        "\n".join(["label start:", *en, f'    hi "{DE}"', "translate spanish start_1:", f'    hi "{ES}"', '    hi "Hola amigo como estas"']),
        encoding="utf-8",
    )  # fmt: skip
    (game / "script-a1-monday-es.rpy").write_text("\n".join(f'    hi "{ES}"' for _ in range(300)), encoding="utf-8")
    script = novel.extract(tmp_path)
    flat = [s for p in script.parts for s in p.lines]
    assert not any(s in (ES, DE) or "Hola" in s for s in flat)
    assert len(flat) == 300


def test_translate_nodes_in_rpyc_skipped() -> None:
    c = _renpy_classes()
    mod = sys.modules["renpy.ast"]
    if not hasattr(mod, "Translate"):
        mod.Translate = type("Translate", (), {"__module__": "renpy.ast"})  # type: ignore[attr-defined]
    say_en, say_es = c["Say"](), c["Say"]()
    say_en.what, say_es.what = "I am not sure what you mean by that.", ES
    tr = mod.Translate()  # type: ignore[attr-defined]
    tr.language, tr.block = "spanish", [say_es]
    data = zlib.compress(pickle.dumps(({}, [say_en, tr]), protocol=2))
    assert novel.rpyc_lines(data) == ["I am not sure what you mean by that."]


def test_names_written_lowercase_now_and_then_are_still_names(tmp_path: Path) -> None:
    text = ["Shizune signed something quickly to me."] * 40 + ["the shizune look, she said."] + lines(50, 22)
    sc = novel.Script(novel.Engine("renpy", "Ren'Py", True), [novel.Part("Вся новелла", text)])
    counts = novelwords.count(sc)[0].counts
    assert "shizune" not in counts
