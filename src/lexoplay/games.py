"""Что считать игрой. Чистый модуль.

Игру добавляют, выбирая окно из списка (или клавишей, если игрок её задал). Браузеры,
лаунчеры, мессенджеры и сама Windows игрой не бывают: на них F2 должен работать как
обычно, а не переводить вкладку браузера.
"""

from __future__ import annotations

from dataclasses import dataclass

NOT_GAMES = frozenset(
    {
        # Windows
        "explorer.exe", "applicationframehost.exe", "searchhost.exe", "searchapp.exe",
        "shellexperiencehost.exe", "startmenuexperiencehost.exe", "textinputhost.exe",
        "systemsettings.exe", "taskmgr.exe", "lockapp.exe", "cmd.exe", "powershell.exe",
        "pwsh.exe", "windowsterminal.exe", "openconsole.exe", "conhost.exe", "notepad.exe",
        "mmc.exe", "control.exe", "regedit.exe", "snippingtool.exe", "screenclippinghost.exe",
        # браузеры
        "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "opera_gx.exe", "brave.exe",
        "vivaldi.exe", "browser.exe", "yandex.exe", "arc.exe", "iexplore.exe",
        # лаунчеры и магазины
        "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "galaxyclient.exe",
        "eadesktop.exe", "origin.exe", "battle.net.exe", "ubisoftconnect.exe", "upc.exe",
        "riotclientux.exe", "playnite.desktopapp.exe", "playnite.fullscreenapp.exe",
        # общение, музыка, запись
        "discord.exe", "telegram.exe", "ayugram.exe", "whatsapp.exe", "slack.exe", "zoom.exe",
        "teams.exe", "ms-teams.exe", "skype.exe", "spotify.exe", "obs64.exe", "obs32.exe",
        # разработка и офис
        "code.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "winword.exe", "excel.exe",
        "powerpnt.exe", "outlook.exe", "acrobat.exe", "acrord32.exe",
        # свои и соседи
        "lexoplay.exe", "magpie.exe", "nvidia app.exe", "nvcontainer.exe", "claude.exe",
    }
)


def is_game_exe(exe: str | None) -> bool:
    return bool(exe) and exe.lower() not in NOT_GAMES


@dataclass(frozen=True, slots=True)
class WindowInfo:
    hwnd: int
    title: str
    exe: str
    path: str
    w: int
    h: int
    minimized: bool = False


def clean_title(title: str, exe: str) -> str:
    """Название игры из заголовка окна: без хвостов вроде « - Direct3D 9» и без пустоты."""
    t = " ".join(title.split())
    for tail in (" - Direct3D 9", " (DirectX 9)", " (DX11)", " - Unity", " [Unity]"):
        if t.endswith(tail):
            t = t[: -len(tail)]
    return t or exe.rsplit(".", 1)[0]


def rank(windows: list[WindowInfo]) -> list[WindowInfo]:
    """Похожие на игру выше: большие окна, потом по названию. Окна-неигры отброшены."""
    games = [w for w in windows if is_game_exe(w.exe) and w.title.strip()]
    return sorted(games, key=lambda w: (w.minimized, -(w.w * w.h), w.title.lower()))
