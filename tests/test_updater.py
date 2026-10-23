import asyncio
import hashlib

import httpx

from lexoplay import updater
from lexoplay.updater import Release, parse_version, release_from_json

EXE = b"MZ" + b"\x00" * 5000


def gh_release(version: str, data: bytes = EXE, digest: bool = True) -> dict:
    asset = {
        "name": "LexoPlay.exe",
        "browser_download_url": f"https://github.com/Satoros1/lexoplay/releases/download/v{version}/LexoPlay.exe",
        "size": len(data),
    }
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return {"tag_name": f"v{version}", "html_url": f"https://github.com/x/releases/tag/v{version}", "assets": [asset], "body": "notes"}


def test_versions_compare():
    assert parse_version("0.4.0") > parse_version("0.3.5")
    assert parse_version("v0.10.0") > parse_version("0.9.9")
    assert parse_version("1.0.0-beta") == (1, 0, 0)


def test_release_from_json_and_newer(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.3.5")
    rel = release_from_json(gh_release("0.4.0"))
    assert rel and rel.version == "0.4.0" and rel.newer and rel.sha256 and rel.size == len(EXE)
    assert release_from_json({"tag_name": "v1", "assets": [], "draft": False}) is None
    assert release_from_json({**gh_release("9.0.0"), "prerelease": True}) is None


def test_check_with_mock_github(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.3.5")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/repos/Satoros1/lexoplay/releases/latest"
        return httpx.Response(200, json=gh_release("0.4.0"))

    rel = asyncio.run(updater.check(transport=httpx.MockTransport(handler)))
    assert rel is not None and rel.version == "0.4.0"
    same = asyncio.run(updater.check(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=gh_release("0.3.5")))))
    assert same is None
    down = asyncio.run(updater.check(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    assert down is None


def test_manual_check_reports_no_connection():
    import pytest

    def offline(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    assert asyncio.run(updater.check(transport=httpx.MockTransport(offline))) is None  # по расписанию: молча
    with pytest.raises(updater.UpdateError, match="Нет связи"):
        asyncio.run(updater.check(transport=httpx.MockTransport(offline), strict=True))  # по кнопке: честно
    with pytest.raises(updater.UpdateError, match="403"):
        asyncio.run(updater.check(transport=httpx.MockTransport(lambda r: httpx.Response(403)), strict=True))


def test_download_checks_digest_and_size(tmp_path):
    rel = release_from_json(gh_release("0.4.0"))
    progress = []
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=EXE))
    path = asyncio.run(updater.download(rel, lambda g, t: progress.append((g, t)), dest_dir=tmp_path, transport=transport))
    assert path.read_bytes() == EXE and progress[-1][0] == len(EXE)
    bad = Release("0.4.0", rel.url, len(EXE), "0" * 64, rel.page)
    try:
        asyncio.run(updater.download(bad, dest_dir=tmp_path, transport=transport))
    except updater.UpdateError as exc:
        assert "контрольная сумма" in str(exc)
    else:
        raise AssertionError("битый файл не должен ставиться")
    assert not (tmp_path / "LexoPlay.exe.part").exists()
    assert not updater.part_path(tmp_path, "0.4.0").exists()


def _flaky(cut: int, *, ranges: bool = True, fail_always: bool = False):  # type: ignore[no-untyped-def]
    """GitHub, у которого связь рвётся после cut байт; ranges=False: докачку не даёт."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        seen.append(rng)
        start = int(rng.split("=")[1].rstrip("-")) if rng and ranges else 0

        async def body():  # type: ignore[no-untyped-def]
            yield EXE[start : start + cut]
            if fail_always or start + cut < len(EXE) and len(seen) == 1:
                raise httpx.ReadError("connection reset")
            yield EXE[start + cut :]

        return httpx.Response(206 if start else 200, content=body())

    return httpx.MockTransport(handler), seen


def test_download_resumes_after_broken_connection(tmp_path):
    rel = release_from_json(gh_release("0.4.0"))
    transport, seen = _flaky(2000)
    progress = []
    path = asyncio.run(
        updater.download(rel, lambda g, t: progress.append(g), dest_dir=tmp_path, transport=transport, pause=0)
    )
    assert path.read_bytes() == EXE
    assert seen == [None, "bytes=2000-"]  # вторая попытка с того же места, а не с нуля
    assert progress[-1] == len(EXE) and progress == sorted(progress)


def test_download_restarts_when_server_ignores_range(tmp_path):
    rel = release_from_json(gh_release("0.4.0"))
    transport, seen = _flaky(2000, ranges=False)
    path = asyncio.run(updater.download(rel, dest_dir=tmp_path, transport=transport, pause=0))
    assert path.read_bytes() == EXE and len(seen) == 2


def test_download_keeps_part_for_next_press(tmp_path):
    rel = release_from_json(gh_release("0.4.0"))
    transport, _ = _flaky(1500, fail_always=True)
    try:
        asyncio.run(updater.download(rel, dest_dir=tmp_path, transport=transport, attempts=2, pause=0))
    except updater.UpdateError as exc:
        assert "ещё раз" in str(exc)
    else:
        raise AssertionError("связь всё время рвётся: должна быть понятная ошибка")
    part = updater.part_path(tmp_path, "0.4.0")
    assert part.stat().st_size == 3000  # две попытки по 1500 байт, кусок не выброшен
    transport2, seen = _flaky(10_000)
    path = asyncio.run(updater.download(rel, dest_dir=tmp_path, transport=transport2, pause=0))
    assert path.read_bytes() == EXE and seen == ["bytes=3000-"]  # следующее «Обновить» продолжает


def test_cleanup_keeps_newer_part(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.6.23")
    for name in ("LexoPlay.exe.old", "LexoPlay.exe.part", "LexoPlay.exe.0.6.22.part", "LexoPlay.exe.0.6.24.part"):
        (tmp_path / name).write_bytes(b"x")
    assert updater.cleanup_old(tmp_path) == 3
    assert [p.name for p in tmp_path.iterdir()] == ["LexoPlay.exe.0.6.24.part"]


def test_swap_and_cleanup(tmp_path):
    cur = tmp_path / "LexoPlay.exe"
    cur.write_bytes(b"old")
    new = tmp_path / "LexoPlay.exe.new"
    new.write_bytes(b"new")
    exe = updater.swap(new, cur)
    assert exe.read_bytes() == b"new" and (tmp_path / "LexoPlay.exe.old").read_bytes() == b"old"
    assert updater.cleanup_old(tmp_path) == 1 and not (tmp_path / "LexoPlay.exe.old").exists()


def test_clean_env_drops_pyinstaller_vars(monkeypatch):
    monkeypatch.setenv("_MEIPASS2", "C:/tmp/_MEI123")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "x")
    env = updater.clean_env()
    assert "_MEIPASS2" not in env and "_PYI_APPLICATION_HOME_DIR" not in env
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
