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
