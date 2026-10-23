"""Обновление в один клик: новая версия с GitHub Releases без ручного скачивания exe.

1. Раз в несколько часов спрашиваем GitHub, какая версия последняя.
2. Игрок нажал «Обновить»: качаем LexoPlay.exe рядом с текущим как LexoPlay.exe.new,
   проверяем размер и SHA-256 (GitHub отдаёт его для файлов релиза).
3. Работающий exe переименовываем в .old (Windows не даёт удалить запущенный файл,
   но переименовать даёт), новый ставим на его место и запускаем с --wait-pid: он ждёт,
   пока старый процесс выйдет, и только потом занимает клавиши. Старый выходит сам.
4. Новый при старте удаляет .old.

Работает только в собранном LexoPlay.exe. Из исходников просто открываем страницу релиза.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from lexoplay import __version__

log = logging.getLogger(__name__)

REPO = "Satoros1/lexoplay"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "LexoPlay.exe"


class UpdateError(Exception):
    """message можно показать игроку."""


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    url: str  # прямая ссылка на LexoPlay.exe
    size: int
    sha256: str | None
    page: str  # страница релиза
    notes: str = ""

    @property
    def newer(self) -> bool:
        return parse_version(self.version) > parse_version(__version__)


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text.split("-")[0])
    return tuple(int(n) for n in nums[:3]) or (0,)


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_path() -> Path:
    return Path(sys.executable).resolve()


def release_from_json(data: dict) -> Release | None:
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    for asset in data.get("assets") or []:
        if asset.get("name") == ASSET:
            digest = str(asset.get("digest") or "")
            sha = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None
            return Release(
                version=tag.lstrip("v"),
                url=str(asset.get("browser_download_url")),
                size=int(asset.get("size") or 0),
                sha256=sha,
                page=str(data.get("html_url") or f"https://github.com/{REPO}/releases/latest"),
                notes=str(data.get("body") or ""),
            )
    return None


async def check(*, transport: httpx.AsyncBaseTransport | None = None, strict: bool = False) -> Release | None:
    """Последний релиз, если он новее нашей версии.

    Сеть недоступна → None (молча, для проверки по расписанию), а со strict (проверка
    по кнопке) → UpdateError: иначе человек без интернета увидел бы «последняя версия»."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"LexoPlay/{__version__}"}
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport, follow_redirects=True) as http:
            resp = await http.get(API_LATEST, headers=headers)
    except httpx.HTTPError as exc:
        log.info("проверка обновлений: нет связи (%s)", exc)
        if strict:
            raise UpdateError("Нет связи с GitHub. Проверь интернет и попробуй ещё раз.") from exc
        return None
    if resp.status_code != 200:
        log.info("проверка обновлений: GitHub ответил %s", resp.status_code)
        if strict:
            raise UpdateError(f"GitHub не ответил (код {resp.status_code}). Попробуй чуть позже.")
        return None
    rel = release_from_json(resp.json())
    if rel is None:
        return None
    log.info("последняя версия на GitHub: %s (у нас %s)", rel.version, __version__)
    return rel if rel.newer else None


def target_dir() -> Path:
    return exe_path().parent


ATTEMPTS = 6  # связь оборвалась посреди файла: докачиваем с того же места
RETRY_PAUSE_S = 1.0


class _Retry(Exception):
    pass


def part_path(folder: Path, version: str) -> Path:
    """Недокачанный файл своей версии: следующая попытка (и следующее нажатие «Обновить») продолжит его."""
    return folder / f"{ASSET}.{version}.part"


def _resume_from(part: Path, size: int) -> tuple[int, hashlib._Hash]:
    sha = hashlib.sha256()
    if not part.exists():
        return 0, sha
    if size and part.stat().st_size > size:
        part.unlink(missing_ok=True)
        return 0, sha
    got = 0
    with part.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            sha.update(block)
            got += len(block)
    return got, sha


async def download(
    rel: Release,
    progress: Callable[[int, int], None] | None = None,
    *,
    dest_dir: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    attempts: int = ATTEMPTS,
    pause: float = RETRY_PAUSE_S,
) -> Path:
    """Скачать новый exe рядом с текущим. Возвращает путь к .new.

    Связь оборвалась посреди файла: ждём и докачиваем с того же места (Range), до ATTEMPTS раз.
    Не вышло и так: недокачанное остаётся, следующее «Обновить» продолжит с него."""
    folder = dest_dir or target_dir()
    dest = folder / (ASSET + ".new")
    part = part_path(folder, rel.version)
    for other in [*folder.glob(f"{ASSET}.*part"), folder / f"{ASSET}.part"]:
        if other != part:
            other.unlink(missing_ok=True)  # от другой версии
    last: Exception | None = None
    try:
        got, sha = _resume_from(part, rel.size)
        if got:
            log.info("обновление %s: продолжаю с %s из %s байт", rel.version, got, rel.size)
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, read=60), transport=transport, follow_redirects=True) as http:
            for attempt in range(attempts):
                if rel.size and got >= rel.size:
                    break
                headers = {"User-Agent": f"LexoPlay/{__version__}"}
                if got:
                    headers["Range"] = f"bytes={got}-"
                try:
                    async with http.stream("GET", rel.url, headers=headers) as resp:
                        if resp.status_code >= 500 or resp.status_code == 429:
                            raise _Retry(f"GitHub ответил {resp.status_code}")
                        if resp.status_code not in (200, 206):
                            raise UpdateError(f"GitHub не отдал файл (ошибка {resp.status_code}).")
                        if resp.status_code == 200 and got:  # докачку не дали: заново
                            got, sha = 0, hashlib.sha256()
                        total = rel.size or got + int(resp.headers.get("content-length") or 0)
                        with part.open("ab" if got else "wb") as f:
                            async for chunk in resp.aiter_bytes():  # без буфера: при обрыве на диске всё полученное
                                f.write(chunk)
                                sha.update(chunk)
                                got += len(chunk)
                                if progress:
                                    progress(got, total)
                    break
                except (httpx.TransportError, _Retry) as exc:
                    last = exc
                    log.info("скачивание обновления прервалось на %s из %s байт: %s (попытка %s)", got, rel.size, exc, attempt + 1)
                    if attempt + 1 < attempts:
                        await asyncio.sleep(pause * min(8, 2**attempt))
            else:
                raise UpdateError(
                    "Не получилось скачать обновление: связь с GitHub всё время обрывается. "
                    "Нажми «Обновить» ещё раз, скачанное не пропадёт."
                ) from last
    except PermissionError as exc:
        raise UpdateError(
            f"Нет прав записать в папку {folder}. Перенеси LexoPlay.exe, например, на рабочий стол или в Документы."
        ) from exc
    except httpx.HTTPError as exc:
        raise UpdateError("Не получилось скачать обновление: нет связи с GitHub.") from exc
    if rel.size and got != rel.size:
        part.unlink(missing_ok=True)
        raise UpdateError("Файл обновления скачался не полностью, попробуй ещё раз.")
    if rel.sha256 and sha.hexdigest() != rel.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("Файл обновления повреждён (не сошлась контрольная сумма), попробуй ещё раз.")
    os.replace(part, dest)
    log.info("обновление %s скачано: %s байт", rel.version, got)
    return dest


def clean_env() -> dict[str, str]:
    """Окружение для нового exe: без переменных PyInstaller, иначе новый процесс
    решит, что он «дочерний» у старого, и возьмёт его временную папку."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def spawn(exe: Path, args: list[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(  # noqa: S603
        [str(exe), *args],
        env=clean_env(),
        cwd=str(exe.parent),
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def swap(new_file: Path, current: Path | None = None) -> Path:
    """Поставить новый exe на место текущего. Текущий уезжает в .old. Возвращает путь к exe."""
    current = current or exe_path()
    old = current.with_name(current.name + ".old")
    if old.exists():
        try:
            old.unlink()
        except OSError:
            old = current.with_name(f"{current.name}.{int(time.time())}.old")
    os.replace(current, old)
    try:
        os.replace(new_file, current)
    except OSError:
        os.replace(old, current)  # вернуть как было
        raise
    return current


def install(new_file: Path, extra_args: list[str] | None = None) -> None:
    """Подменить exe и запустить новый. Вызывающий после этого должен выйти."""
    try:
        exe = swap(new_file)
    except OSError as exc:
        raise UpdateError(f"Не получилось заменить LexoPlay.exe: {exc}") from exc
    spawn(exe, ["--wait-pid", str(os.getpid()), "--updated", *(extra_args or [])])
    log.info("новая версия запущена, выходим")


def wait_pid(pid: int, timeout: float = 20.0) -> None:
    """Ждём, пока старый LexoPlay выйдет: он держит клавиши и single-instance сервер."""
    if sys.platform != "win32" or pid <= 0:
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    synchronize = 0x00100000
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return  # уже вышел
    try:
        kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    finally:
        kernel32.CloseHandle(handle)


def cleanup_old(folder: Path | None = None) -> int:
    """Удалить .old и недокачанное от прошлого обновления. Возвращает, сколько удалено.

    Недокачанная более новая версия остаётся: «Обновить» продолжит её."""
    folder = folder or target_dir()
    removed = 0
    files = [*folder.glob(f"{ASSET}*.old"), *folder.glob(f"{ASSET}.part")]
    files += [p for p in folder.glob(f"{ASSET}.*.part") if parse_version(p.name[len(ASSET) + 1 : -5]) <= parse_version(__version__)]
    for p in files:
        for _ in range(10):
            try:
                p.unlink()
                removed += 1
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(0.3)  # старый процесс ещё выходит
    return removed
