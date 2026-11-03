"""Текст новеллы из папки игры: на каком она движке и можно ли достать реплики.

Читаем только файлы игры у игрока на компьютере. Сам текст никуда не уходит: из него считаются слова
(novelwords), а в модель потом идёт только список слов. Если реплики не достать, так и говорим, с причиной.

Что умеем: Ren'Py (.rpy, архивы .rpa, скомпилированные .rpyc), RPG Maker MV/MZ, TyranoScript,
NScripter/PONScripter, KiriKiri (незашифрованные .xp3), GameMaker (data.win), Unity с текстом в
StreamingAssets и просто текстовые файлы скрипта. Не умеем: ресурсы Unity, Unreal, архивы Nitroplus и minori,
RPG Maker VX/XP: там текст упакован или зашифрован под каждую игру.
"""

from __future__ import annotations

import io
import json
import logging
import mmap
import pickle
import re
import struct
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

log = logging.getLogger(__name__)

MAX_FILE = 64 * 1024 * 1024  # один текстовый файл больше этого не читаем (это уже не скрипт)
MAX_TOTAL = 400 * 1024 * 1024  # всего с диска за один разбор
PART_WORDS = 40_000  # главы не нашлись: длинный скрипт делим на части примерно по столько слов
TEXT_EXT = {
    ".txt",
    ".json",
    ".csv",
    ".tsv",
    ".xml",
    ".yaml",
    ".yml",
    ".lua",
    ".ks",
    ".rpy",
    ".scn",
    ".sc",
}
NOT_SCRIPT = re.compile(
    r"(licen[cs]e|readme|credits|changelog|steam_|eula|third.?party|notice|log\b|install)",
    re.IGNORECASE,
)


class NovelError(Exception):
    """Понятная игроку причина, почему текст не достался."""


@dataclass(frozen=True, slots=True)
class Engine:
    key: str  # renpy | rpgmaker | tyrano | nscripter | kirikiri | gamemaker | unity | unreal | nitroplus | rgss | ...
    name: str  # как показать игроку
    readable: bool
    why: str = ""  # почему нельзя (readable=False)


@dataclass(slots=True)
class Part:
    """Кусок новеллы: «Акт 1», «Часть 2». Названий сцен не показываем: они бывают спойлером."""

    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(len(s.split()) for s in self.lines)


@dataclass(slots=True)
class Script:
    engine: Engine
    parts: list[Part]
    note: str = ""

    @property
    def words(self) -> int:
        return sum(p.words for p in self.parts)


# ------------------------------------------------------------------ движок
def _first(root: Path, *patterns: str) -> Path | None:
    for pat in patterns:
        try:
            hit = next(root.glob(pat), None)
        except OSError:
            hit = None
        if hit is not None:
            return hit
    return None


def _unity_text(root: Path) -> list[Path]:
    out: list[Path] = []
    for data in root.glob("*_Data"):
        sa = data / "StreamingAssets"
        if sa.is_dir():
            out.extend(p for p in _walk(sa, 6) if p.suffix.lower() in TEXT_EXT)
    return out


def detect(root: Path) -> Engine:
    """Движок по файлам в папке игры. Быстро: только смотрим, что лежит, тяжёлое потом в extract."""
    game = root / "game"
    if game.is_dir() and (
        (root / "renpy").is_dir() or _first(game, "*.rpa", "*.rpyc", "*.rpy", "*/*.rpyc", "*/*.rpy")
    ):
        return Engine("renpy", "Ren'Py", True)
    if (root / "www" / "data" / "System.json").exists() or (
        (root / "data" / "System.json").exists() and (root / "js").is_dir()
    ):
        return Engine("rpgmaker", "RPG Maker MV/MZ", True)
    if _first(root, "*.rgss3a", "*.rgss2a", "*.rgssad", "Data/*.rvdata2", "Data/*.rxdata"):
        return Engine(
            "rgss", "RPG Maker VX/XP", False,
            "Текст лежит в упакованных файлах Ruby этой версии RPG Maker. Их я пока читать не умею.",
        )  # fmt: skip
    if (root / "data" / "scenario").is_dir() or _first(root, "resources/app.asar", "package.nw"):
        return Engine("tyrano", "TyranoScript", True)
    if _first(
        root, "nscript.dat", "pscript.dat", "0.utf", "0.u", "0.txt", "00.txt", "nscr_sec.dat"
    ):
        return Engine("nscripter", "NScripter/PONScripter", True)
    if _first(root, "*.xp3"):
        return Engine("kirikiri", "KiriKiri", True)
    if (root / "data.win").exists() or _first(root, "*.win"):
        return Engine("gamemaker", "GameMaker", True)
    if _first(root, "*.wad"):
        return Engine(
            "spike", "Spike Chunsoft (Danganronpa)", False,
            "Текст лежит в архивах .wad особого формата. Читать их я пока не умею.",
        )  # fmt: skip
    if _first(root, "*.npa"):
        return Engine(
            "nitroplus",
            "Nitroplus",
            False,
            "Архивы .npa зашифрованы ключом этой игры. Вскрыть их надёжно пока нельзя.",
        )
    if _first(root, "*.paz"):
        return Engine(
            "minori",
            "minori",
            False,
            "Архивы .paz зашифрованы ключом этой игры. Вскрыть их надёжно пока нельзя.",
        )
    if _first(root, "*_Data/globalgamemanagers", "UnityPlayer.dll"):
        if _unity_text(root):
            return Engine("unity", "Unity", True)
        return Engine(
            "unity", "Unity", False,
            "Текст спрятан внутри ресурсов игры (assets), часто ещё и зашифрован. Вытащить его надёжно пока нельзя.",
        )  # fmt: skip
    if _first(root, "*/Content/Paks/*.pak", "Engine/Binaries"):
        return Engine(
            "unreal",
            "Unreal Engine",
            False,
            "Текст в упакованных и зашифрованных файлах .pak. Вытащить его нельзя.",
        )
    return Engine("unknown", "движок не узнал", True)


def game_root(exe_dir: Path) -> Path:
    """Папка игры по папке её exe: у Ren'Py 8 и Unreal exe лежит глубже, поднимаемся до узнаваемой папки."""
    d = exe_dir
    for _ in range(4):
        if detect(d).key != "unknown":
            return d
        if d.parent == d:
            break
        d = d.parent
    return exe_dir


def extract(root: Path, engine: Engine | None = None) -> Script:
    """Реплики новеллы по частям. NovelError с причиной, если не вышло."""
    engine = engine or detect(root)
    if not engine.readable:
        raise NovelError(f"{engine.name}: {engine.why}")
    reader = _READERS.get(engine.key, _loose)
    try:
        chunks, ordered = reader(root)
    except NovelError:
        raise
    except Exception as exc:  # чужие форматы: что угодно может пойти не так
        log.exception("текст новеллы (%s) не прочитался", engine.key)
        raise NovelError(
            f"{engine.name}: файлы игры не прочитались ({exc.__class__.__name__})."
        ) from exc
    chunks = [(name, [s for s in (clean(x) for x in lines) if _texty(s)]) for name, lines in chunks]
    chunks = [(n, ls) for n, ls in chunks if ls]
    total = sum(len(s.split()) for _n, ls in chunks for s in ls)
    if total < 2000:
        if engine.key == "unknown":
            raise NovelError("Движок не узнал, и текста реплик в папке игры не нашлось.")
        raise NovelError(
            f"{engine.name}: реплик почти не нашлось ({total} слов). Скорее всего, текст у этой игры зашифрован или лежит в другом месте."
        )
    parts, note = split_parts(chunks, ordered)
    return Script(engine, parts, note)


# ------------------------------------------------------------------ части
_CHAPTER = re.compile(r"(?:^|[^a-z])(act|a|chapter|ch|episode|ep|day|part)[ _\-]?(\d{1,2})(?!\d)")
_KIND = {
    "act": "Акт",
    "a": "Акт",
    "chapter": "Глава",
    "ch": "Глава",
    "episode": "Эпизод",
    "ep": "Эпизод",
    "day": "День",
    "part": "Часть",
}


def split_parts(chunks: list[tuple[str, list[str]]], ordered: bool) -> tuple[list[Part], str]:
    """Главы по именам файлов (script-a1-monday → «Акт 1»), иначе по объёму, иначе одна часть."""
    keys: list[tuple[str, int] | None] = []
    for name, _lines in chunks:
        m = _CHAPTER.search(Path(name).stem.lower())
        keys.append((_KIND[m.group(1)], int(m.group(2))) if m else None)
    words = [sum(len(s.split()) for s in ls) for _n, ls in chunks]
    total = sum(words) or 1
    covered = sum(w for w, k in zip(words, keys, strict=True) if k)
    nums = {k[1] for k in keys if k}
    if len(nums) >= 2 and covered >= total * 0.6:
        kind = Counter(k[0] for k in keys if k).most_common(1)[0][0]
        first = min(nums)
        groups: dict[int, list[str]] = {}
        for (_name, lines), k in zip(chunks, keys, strict=True):
            groups.setdefault(k[1] if k else first, []).extend(
                lines
            )  # общее без номера: к первой части
        return [Part(f"{kind} {n}", groups[n]) for n in sorted(groups)], ""
    lines = [s for _n, ls in chunks for s in ls]
    if ordered and total > PART_WORDS * 1.5:
        n = min(10, max(2, round(total / PART_WORDS)))
        size = total / n
        parts: list[Part] = [Part("Часть 1")]
        acc = 0
        for s in lines:
            if acc >= size * len(parts) and len(parts) < n:
                parts.append(Part(f"Часть {len(parts) + 1}"))
            parts[-1].lines.append(s)
            acc += len(s.split())
        return parts, "Глав в файлах игры нет: новелла поделена на части примерно поровну."
    return [Part("Вся новелла", lines)], "Глав в файлах игры нет: слова по всей новелле сразу."


# ------------------------------------------------------------------ текст
_TAG = re.compile(r"\{[^{}]*\}")  # Ren'Py {i}, {w}
_BRACKET = re.compile(r"\[[^\[\]]*\]")  # [name] в Ren'Py, теги Tyrano и KiriKiri
_RPGM_ESC = re.compile(r"\\[A-Za-z]+\[[^\]]*\]|\\[.|!><^{}$\\]|\\[A-Za-z]")
_TILDE = re.compile(r"~[^~]{0,12}~")  # PONScripter: ~i~ и т.п.
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")  # NScripter: цвет текста #ffffff


def clean(s: str) -> str:
    s = s.replace("\\n", " ").replace('\\"', '"').replace("\\'", "'")
    s = _TAG.sub("", s)
    s = _BRACKET.sub(" ", s)
    s = _RPGM_ESC.sub("", s)
    s = _TILDE.sub("", s)
    s = _COLOR.sub("", s)
    s = s.replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip(" @\\/")


def _texty(s: str) -> bool:
    """Похоже на английскую реплику: хотя бы два слова из букв."""
    return len(re.findall(r"[A-Za-z]{2,}", s)) >= 2


def _dialogue(s: str) -> bool:
    """Строже _texty для угадывания в чужих форматах: фраза, а не код, путь или имя ресурса."""
    if len(s) < 12 or len(s) > 2000:
        return False
    words = re.findall(r"[A-Za-z']+", s)
    if len(words) < 3 or not re.search(r"[a-z]", s):
        return False
    if re.search(r"[_=<>{}|]|\w\.\w{2,4}$|\\\\|//|\(\)", s):
        return False
    letters = sum(ch.isalpha() or ch in " ,.!?'\"-…—" for ch in s)
    ascii_letters = sum(ch.isascii() and ch.isalpha() for ch in s)
    alpha = sum(ch.isalpha() for ch in s) or 1
    return letters / len(s) >= 0.85 and ascii_letters / alpha >= 0.9


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if len(raw) > 4 and raw[1] == 0 and raw[3] == 0:
        return raw.decode("utf-16-le", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")


def _readable_share(text: str) -> float:
    sample = text[:200_000]
    if not sample:
        return 0.0
    ok = sum(ch.isprintable() or ch in "\r\n\t" for ch in sample)
    return ok / len(sample)


def _walk(root: Path, depth: int) -> Iterator[Path]:
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    for p in entries:
        if p.is_dir():
            if depth > 0:
                yield from _walk(p, depth - 1)
        else:
            yield p


class _Budget:
    def __init__(self) -> None:
        self.left = MAX_TOTAL

    def read(self, path: Path) -> bytes | None:
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size > MAX_FILE or size > self.left:
            return None
        self.left -= size
        return path.read_bytes()


# ------------------------------------------------------------------ безопасный pickle (Ren'Py)
class _Stub:
    """Любой класс из чужого pickle: только хранит поля. Код игры не выполняется."""

    def __init__(self, *args: object, **kw: object) -> None:
        self._args = args

    def __setstate__(self, state: object) -> None:
        if (
            isinstance(state, tuple)
            and len(state) == 2
            and all(x is None or isinstance(x, dict) for x in state)
        ):
            for part in state:
                for k, v in (part or {}).items():
                    if isinstance(k, str):
                        self.__dict__[k] = v
        elif isinstance(state, dict):
            for k, v in state.items():
                if isinstance(k, str):
                    self.__dict__[k] = v


class _RList(list):  # renpy.python.RevertableList и т.п.: со своими полями
    pass


class _RDict(dict):
    pass


class _RSet(set):
    pass


class _PyExpr(str):
    def __new__(cls, s: object = "", *_a: object, **_k: object) -> Self:
        return str.__new__(cls, s if isinstance(s, str) else "")


_SAFE_BUILTINS = {"set": set, "frozenset": frozenset, "list": list, "dict": dict, "tuple": tuple, "str": str,
                  "unicode": str, "int": int, "long": int, "float": float, "bool": bool, "bytes": bytes, "object": object}  # fmt: skip
_STUBS: dict[str, type] = {}


def _reconstructor(cls: object, base: object, state: object = None) -> object:
    """copyreg._reconstructor старых pickle (протоколы 0 и 1): объект без вызова чужого кода."""
    try:
        obj = cls.__new__(cls) if isinstance(cls, type) else _Stub()  # type: ignore[call-overload]
    except TypeError:
        return _Stub()
    if isinstance(obj, (list, set)) and isinstance(state, (list, tuple, set)):
        obj.extend(state) if isinstance(obj, list) else obj.update(state)
    elif isinstance(obj, dict) and isinstance(state, dict):
        obj.update(state)
    return obj


def _empty_dict(*_a: object, **_k: object) -> dict:  # type: ignore[type-arg]
    return {}


_REVERTABLE = {"RevertableList": _RList, "RevertableDict": _RDict, "RevertableSet": _RSet}


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> object:
        if module in ("builtins", "__builtin__") and name in _SAFE_BUILTINS:
            return _SAFE_BUILTINS[name]
        if module in ("copy_reg", "copyreg") and name == "_reconstructor":
            return _reconstructor
        if module == "collections" and name in ("OrderedDict", "defaultdict"):
            return _empty_dict  # defaultdict(фабрика): фабрику не зовём, элементы придут следом
        if name in _REVERTABLE:
            return _REVERTABLE[name]
        if name == "PyExpr":
            return _PyExpr
        key = f"{module}.{name}"
        cls = _STUBS.get(key)
        if cls is None:
            cls = type(name, (_Stub,), {})
            _STUBS[key] = cls
        return cls


def _unpickle(raw: bytes, encoding: str = "utf-8") -> object:
    """encoding: как читать строки Python 2 (str). Для индекса .rpa latin-1: там байты, а не текст."""
    return _SafeUnpickler(io.BytesIO(raw), encoding=encoding, errors="replace").load()


def _walk_say(root: object) -> list[str]:
    """Реплики из дерева Ren'Py: Say и TranslateSay (что говорят) и пункты меню (выборы игрока)."""
    out: list[str] = []
    seen: set[int] = set()
    stack: list[object] = [root]
    while stack:
        o = stack.pop()
        if id(o) in seen:
            continue
        seen.add(id(o))
        if isinstance(o, _Stub):
            cls = type(o).__name__
            if cls in ("Say", "TranslateSay"):
                what = o.__dict__.get("what")
                if isinstance(what, str):
                    out.append(what)
            elif cls == "Menu":
                for item in o.__dict__.get("items") or []:
                    if (
                        isinstance(item, (list, tuple))
                        and len(item) >= 3
                        and isinstance(item[0], str)
                        and item[2] is not None
                    ):
                        out.append(item[0])
            stack.extend(reversed(list(o.__dict__.values())))
        elif isinstance(o, (list, tuple)):
            stack.extend(reversed(o))
        elif isinstance(o, dict):
            stack.extend(reversed(list(o.values())))
    return out


def rpyc_lines(data: bytes) -> list[str]:
    raw = data
    if data.startswith(b"RENPY RPC2"):
        pos, chunks = 10, {}
        while pos + 12 <= len(data):
            slot, start, length = struct.unpack("<III", data[pos : pos + 12])
            pos += 12
            if slot == 0:
                break
            chunks[slot] = data[start : start + length]
        raw = chunks.get(1, b"")
    obj = _unpickle(zlib.decompress(raw))
    stmts = obj[1] if isinstance(obj, tuple) and len(obj) == 2 else obj
    return _walk_say(stmts)


_RPY_SKIP = re.compile(
    r"^(?:\$|#|define|default|image|init|python|label|jump|call|return|show|scene|hide|with|play|stop|queue|pause|"
    r"window|nvl|transform|style|screen|text|textbutton|imagebutton|add|key|use|if|elif|else|while|for|translate|"
    r"old|new|voice|camera|at|config|gui|build)\b"
)
_RPY_SAY = re.compile(r'^(?:[A-Za-z_][\w.]*(?:\s+[\w.]+)*\s+)?"((?:[^"\\]|\\.)*)"')


def rpy_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or _RPY_SKIP.match(s):
            continue
        m = _RPY_SAY.match(s)
        if m:
            out.append(m.group(1))
    return out


def rpa_entries(path: Path, exts: tuple[str, ...]) -> Iterator[tuple[str, bytes]]:
    """Файлы из архива Ren'Py .rpa (2.0, 3.0, 3.2), только с нужными расширениями."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.readline(256).decode("latin-1").split()
        if not head or head[0] not in ("RPA-2.0", "RPA-3.0", "RPA-3.2"):
            return
        offset = int(head[1], 16)
        f.seek(offset)
        index = _unpickle(zlib.decompress(f.read()), "latin-1")
        if not isinstance(index, dict):
            return
        fields = [int(x, 16) for x in head[2:]]
        xor_all = 0
        for k in fields:
            xor_all ^= k
        keys = [*fields, xor_all, 0]

        def entry(e: object, key: int) -> tuple[int, int, bytes] | None:
            if not isinstance(e, (list, tuple)) or not e or not isinstance(e[0], (list, tuple)):
                return None
            off, ln, *rest = e[0]
            start = rest[0] if rest else b""
            if isinstance(start, str):
                start = start.encode("latin-1")
            off, ln = off ^ key, ln ^ key
            return (
                (off, ln, start) if 0 <= off < size and 0 <= ln <= size - off + len(start) else None
            )

        sample = [e for _n, e in zip(range(8), index.values(), strict=False)]
        key = next((k for k in keys if all(entry(e, k) is not None for e in sample)), None)
        if key is None:
            raise NovelError(
                "Ren'Py: архив .rpa этой игры собран по-своему, прочитать его не вышло."
            )
        for name, e in index.items():
            n = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
            if not n.lower().endswith(exts):
                continue
            got = entry(e, key)
            if got is None:
                continue
            off, ln, start = got
            f.seek(off)
            yield n, start + f.read(max(0, ln - len(start)))


def _renpy(root: Path) -> tuple[list[tuple[str, list[str]]], bool]:
    game = root / "game"
    budget = _Budget()
    rpy: dict[str, str] = {}
    rpyc: dict[str, bytes] = {}

    def keep(name: str) -> str | None:
        n = name.replace("\\", "/").lower()
        if n.startswith(("tl/", "cache/", "renpy/", "saves/")) or "/tl/" in n:
            return None  # переводы на другие языки и служебное
        stem = Path(n).stem
        if stem in ("screens", "options", "gui", "definitions") or stem.startswith("00"):
            return None
        return n.rsplit(".", 1)[0]

    for p in _walk(game, 8):
        suf = p.suffix.lower()
        if suf not in (".rpy", ".rpyc"):
            continue
        name = keep(str(p.relative_to(game)))
        if name is None:
            continue
        raw = budget.read(p)
        if raw is None:
            continue
        if suf == ".rpy":
            rpy[name] = _decode(raw)
        else:
            rpyc[name] = raw
    for arc in sorted(game.glob("*.rpa")):
        for n, data in rpa_entries(arc, (".rpy", ".rpyc")):
            name = keep(n)
            if name is None:
                continue
            if n.lower().endswith(".rpy"):
                rpy.setdefault(name, _decode(data))
            else:
                rpyc.setdefault(name, data)
    out: list[tuple[str, list[str]]] = []
    for name in sorted(set(rpy) | set(rpyc)):
        if name in rpy:
            out.append((name, rpy_lines(rpy[name])))
        else:
            try:
                out.append((name, rpyc_lines(rpyc[name])))
            except Exception:
                log.debug("rpyc %s не прочитался", name, exc_info=True)
    if not out:
        raise NovelError("Ren'Py: файлов скрипта в папке game не нашлось.")
    return out, False


# ------------------------------------------------------------------ RPG Maker MV/MZ
def _rpgm_commands(node: object) -> Iterator[list]:  # type: ignore[type-arg]
    if isinstance(node, dict):
        if isinstance(node.get("list"), list):
            yield node["list"]
        for key in ("pages", "events"):
            for sub in node.get(key) or []:
                yield from _rpgm_commands(sub)
    elif isinstance(node, list):
        for sub in node:
            yield from _rpgm_commands(sub)


def _rpgmaker(root: Path) -> tuple[list[tuple[str, list[str]]], bool]:
    data = root / "www" / "data" if (root / "www" / "data").is_dir() else root / "data"
    maps = sorted(data.glob("Map[0-9]*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    out = []
    budget = _Budget()
    for path in [*maps, data / "CommonEvents.json", data / "Troops.json"]:
        raw = budget.read(path) if path.exists() else None
        if raw is None:
            continue
        lines: list[str] = []
        for cmds in _rpgm_commands(json.loads(_decode(raw))):
            buf: list[str] = []
            for c in cmds:
                code = c.get("code") if isinstance(c, dict) else None
                params = c.get("parameters") or [] if isinstance(c, dict) else []
                if code in (401, 405) and params and isinstance(params[0], str):
                    buf.append(params[0])
                    continue
                if buf:
                    lines.append(" ".join(buf))
                    buf = []
                if code == 102 and params and isinstance(params[0], list):
                    lines.extend(x for x in params[0] if isinstance(x, str))
            if buf:
                lines.append(" ".join(buf))
        out.append((path.stem, lines))
    return out, False


# ------------------------------------------------------------------ TyranoScript и KiriKiri (KAG)
def kag_lines(text: str) -> list[str]:
    out: list[str] = []
    script = False
    for raw in text.splitlines():
        s = raw.strip()
        low = s.lower()
        if low.startswith(("[iscript", "@iscript")):
            script = True
        if script:
            if low.startswith(("[endscript", "@endscript")):
                script = False
            continue
        if not s or s[0] in "@;*#":
            continue
        s = _BRACKET.sub(" ", s)
        if s.strip():
            out.append(s)
    return out


def asar_entries(path: Path, prefix: str, exts: tuple[str, ...]) -> Iterator[tuple[str, bytes]]:
    """Файлы из архива Electron (app.asar): так собирают TyranoScript и другие движки на JS."""
    with open(path, "rb") as f:
        _a, hsize, _b, jlen = struct.unpack("<IIII", f.read(16))
        header = json.loads(f.read(jlen).decode("utf-8"))
        base = 8 + hsize

        def walk(node: dict, at: str) -> Iterator[tuple[str, dict]]:  # type: ignore[type-arg]
            for name, sub in (node.get("files") or {}).items():
                full = f"{at}{name}"
                if "files" in sub:
                    yield from walk(sub, full + "/")
                else:
                    yield full, sub

        for name, meta in walk(header, ""):
            if not name.startswith(prefix) or not name.lower().endswith(exts):
                continue
            if meta.get("unpacked"):
                p = path.with_name(path.name + ".unpacked") / name
                if p.exists():
                    yield name, p.read_bytes()
                continue
            f.seek(base + int(meta.get("offset", 0)))
            yield name, f.read(int(meta.get("size", 0)))


def _tyrano(root: Path) -> tuple[list[tuple[str, list[str]]], bool]:
    files: dict[str, bytes] = {}
    scen = root / "data" / "scenario"
    budget = _Budget()
    if scen.is_dir():
        for p in _walk(scen, 4):
            if p.suffix.lower() == ".ks" and (raw := budget.read(p)) is not None:
                files[str(p.relative_to(scen))] = raw
    asar = root / "resources" / "app.asar"
    if asar.exists():
        for name, raw in asar_entries(asar, "data/scenario/", (".ks",)):
            files.setdefault(name.split("data/scenario/", 1)[1], raw)
    nw = root / "package.nw"
    if nw.exists() and zipfile.is_zipfile(nw):
        with zipfile.ZipFile(nw) as z:
            for info in z.infolist():
                if info.filename.startswith("data/scenario/") and info.filename.endswith(".ks"):
                    files.setdefault(info.filename.split("data/scenario/", 1)[1], z.read(info))
    if not files:
        raise NovelError("TyranoScript: сценариев (.ks) не нашлось.")
    return [(n, kag_lines(_decode(files[n]))) for n in sorted(files)], False


def xp3_entries(path: Path, exts: tuple[str, ...]) -> Iterator[tuple[str, bytes]]:
    """Файлы из архива KiriKiri .xp3. Зашифрованные игрой придут мусором: это видно по тексту."""
    with open(path, "rb") as f:
        if f.read(11) != b"XP3\r\n \n\x1a\x8b\x67\x01":
            return
        (idx,) = struct.unpack("<Q", f.read(8))
        if idx == 0x17:  # формат 2: заголовок-«подушка», настоящий индекс дальше
            f.seek(0x17 + 4)
            if f.read(1) == b"\x80":
                f.read(8)
                (idx,) = struct.unpack("<Q", f.read(8))
        f.seek(idx)
        flag = f.read(1)[0]
        if flag & 7 == 1:
            comp, _orig = struct.unpack("<QQ", f.read(16))
            index = zlib.decompress(f.read(comp))
        else:
            (size,) = struct.unpack("<Q", f.read(8))
            index = f.read(size)
        pos = 0
        while pos + 12 <= len(index):
            tag = index[pos : pos + 4]
            (size,) = struct.unpack("<Q", index[pos + 4 : pos + 12])
            body = index[pos + 12 : pos + 12 + size]
            pos += 12 + size
            if tag != b"File":
                continue
            name, segs, q = "", [], 0
            while q + 12 <= len(body):
                t = body[q : q + 4]
                (n,) = struct.unpack("<Q", body[q + 4 : q + 12])
                chunk = body[q + 12 : q + 12 + n]
                q += 12 + n
                if t == b"info" and len(chunk) >= 22:
                    (nlen,) = struct.unpack("<H", chunk[20:22])
                    name = chunk[22 : 22 + nlen * 2].decode("utf-16-le", "replace")
                elif t == b"segm":
                    for k in range(0, len(chunk) - 27, 28):
                        sflags, start, orig, packed = struct.unpack("<IQQQ", chunk[k : k + 28])
                        segs.append((sflags, start, orig, packed))
            if not name.lower().endswith(exts) or not segs:
                continue
            data = b""
            for sflags, start, _orig, packed in segs:
                if packed > MAX_FILE:
                    break
                f.seek(start)
                blob = f.read(packed)
                data += zlib.decompress(blob) if sflags & 7 == 1 else blob
            yield name, data


def _kirikiri(root: Path) -> tuple[list[tuple[str, list[str]]], bool]:
    files: dict[str, bytes] = {}
    for p in _walk(root, 3):
        if p.suffix.lower() == ".ks" and p.stat().st_size < MAX_FILE:
            files[str(p.relative_to(root))] = p.read_bytes()
    for arc in sorted(root.glob("*.xp3")):
        try:
            for name, raw in xp3_entries(arc, (".ks",)):
                files.setdefault(name, raw)
        except (zlib.error, struct.error, OSError) as exc:
            log.info("xp3 %s не прочитался: %s", arc.name, exc)
    texts = {n: _decode(raw) for n, raw in files.items()}
    texts = {n: t for n, t in texts.items() if _readable_share(t) > 0.9}
    if not texts:
        raise NovelError(
            "KiriKiri: архивы .xp3 этой игры зашифрованы, сценарии из них не читаются."
        )
    return [(n, kag_lines(texts[n])) for n in sorted(texts)], False


# ------------------------------------------------------------------ NScripter / PONScripter
_XOR84 = bytes(b ^ 0x84 for b in range(256))
_CARET = re.compile(r"\^([^\^]{2,})\^")
_NS_FILES = ("0.utf", "0.u", "nscript.dat", "pscript.dat", "nscr_sec.dat", *(f"{i}.txt" for i in range(10)),
             *(f"{i:02d}.txt" for i in range(100)))  # fmt: skip


_NS_CMD = re.compile(r"^[a-z_][a-z0-9_]*\s+[^\s]*[,\"]")  # stralias x,"...", ld c,"..."


def nscripter_lines(text: str) -> list[str]:
    """PONScripter (Umineko и др.): английский текст между ^ ^, в строках langen. Иначе строки текста
    NScripter: с ` (однобайтный текст) или похожие на реплику, но не команды."""
    rows = [r.strip() for r in text.splitlines()]
    rows = [r for r in rows if r and not r.startswith((";", "*", "~"))]
    caret_mode = sum(r.count("^") for r in rows) >= 100  # реплик в ^ ^ много: всё остальное команды
    out: list[str] = []
    for s in rows:
        carets = _CARET.findall(s) if "^" in s else []
        if carets:
            out.extend(carets)
        elif not caret_mode and not _NS_CMD.match(s):
            s = re.sub(r"^(?:langen|langjp)\s*", "", s)
            if _dialogue(s.strip("`@\\/ ")):
                out.append(s.strip("`"))
    return out


def _nscripter(root: Path) -> tuple[list[tuple[str, list[str]]], bool]:
    out = []
    budget = _Budget()
    for name in _NS_FILES:
        p = root / name
        if not p.exists():
            continue
        raw = budget.read(p)
        if raw is None:
            continue
        text = None
        for variant in (raw, raw.translate(_XOR84)):
            t = _decode(variant)
            if _readable_share(t) > 0.95:
                text = t
                break
        if text is None:
            continue
        out.append((name, nscripter_lines(text)))
    if not out:
        raise NovelError("NScripter: скрипт этой игры зашифрован, прочитать его не вышло.")
    return out, True


# ------------------------------------------------------------------ GameMaker
def gamemaker_strings(path: Path) -> list[str]:
    """Все строки из data.win (кусок STRG). Реплики среди них отбирает _dialogue."""
    out: list[str] = []
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as m:
        if m[:4] != b"FORM":
            raise NovelError("GameMaker: data.win другого формата, строки из него не читаются.")
        pos, end = 8, len(m)
        while pos + 8 <= end:
            name = m[pos : pos + 4]
            (size,) = struct.unpack("<I", m[pos + 4 : pos + 8])
            if name == b"STRG":
                (count,) = struct.unpack("<I", m[pos + 8 : pos + 12])
                for i in range(min(count, 2_000_000)):
                    (off,) = struct.unpack("<I", m[pos + 12 + 4 * i : pos + 16 + 4 * i])
                    if off + 4 > end:
                        continue
                    (ln,) = struct.unpack("<I", m[off : off + 4])
                    if 0 < ln < 100_000 and off + 4 + ln <= end:
                        out.append(m[off + 4 : off + 4 + ln].decode("utf-8", "replace"))
                break
            pos += 8 + size
    return out


def _gamemaker(root: Path) -> tuple[list[tuple[str, list[str]]], bool]:
    win = root / "data.win" if (root / "data.win").exists() else _first(root, "*.win")
    lines: list[str] = []
    if win is not None:
        lines = [s for s in gamemaker_strings(win) for s in s.split("\n") if _dialogue(clean(s))]
    loose, _ordered = _loose(root, quiet=True)
    return [("data", lines), *loose], False


# ------------------------------------------------------------------ просто текстовые файлы (и Unity StreamingAssets)
_LITERAL = re.compile(r'"((?:[^"\\]|\\.){12,})"')


def text_file_lines(text: str, suffix: str) -> list[str]:
    if suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if data is not None:
            found: list[str] = []
            stack = [data]
            while stack:
                o = stack.pop()
                if isinstance(o, str):
                    found.append(o)
                elif isinstance(o, dict):
                    stack.extend(reversed(list(o.values())))
                elif isinstance(o, list):
                    stack.extend(reversed(o))
            return [s for s in found if _dialogue(clean(s))]
    out: list[str] = []
    for raw in text.splitlines():
        lits = [x for x in _LITERAL.findall(raw) if _dialogue(clean(x))]
        if lits:
            out.extend(lits)
        elif _dialogue(clean(raw.strip())):
            out.append(raw.strip())
    return out


OTHER_LANG = re.compile(
    r"(?:^|[/\\_.\-])(fr|de|es|it|pt|ptbr|pt-br|ru|ja|jp|zh|cn|tw|ko|kr|pl|tr|uk|ua|french|german|spanish|"
    r"italian|portuguese|russian|japanese|chinese|korean|polish|turkish)(?=$|[/\\_.\-])"
)
_STOP = frozenset(
    "the you and to of a i is it that in this what my me not was your be have do".split()
)


def english(lines: list[str]) -> bool:
    """Английский ли это текст: частых служебных слов в английском примерно каждое пятое."""
    words = [w.lower() for s in lines[:400] for w in re.findall(r"[A-Za-z']+", s)]
    return bool(words) and sum(w in _STOP for w in words) / len(words) >= 0.08


def _loose(root: Path, quiet: bool = False) -> tuple[list[tuple[str, list[str]]], bool]:
    files = _unity_text(root) or [p for p in _walk(root, 4) if p.suffix.lower() in TEXT_EXT]
    budget = _Budget()
    out = []
    for p in files:
        if NOT_SCRIPT.search(p.name):
            continue
        raw = budget.read(p)
        if raw is None:
            continue
        text = _decode(raw)
        if _readable_share(text) < 0.9:
            continue
        lines = text_file_lines(text, p.suffix.lower())
        if (
            len(lines) >= 20
            and english(lines)
            and not OTHER_LANG.search(str(p.relative_to(root)).lower())
        ):
            out.append((str(p.relative_to(root)), lines))
    if not out and not quiet:
        raise NovelError(
            "Текста реплик в файлах игры не нашлось: скорее всего, он упакован в ресурсы."
        )
    return out, False


_READERS = {
    "renpy": _renpy,
    "rpgmaker": _rpgmaker,
    "tyrano": _tyrano,
    "kirikiri": _kirikiri,
    "nscripter": _nscripter,
    "gamemaker": _gamemaker,
    "unity": _loose,
    "unknown": _loose,
}
