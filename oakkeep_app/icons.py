"""Oakkeep mark for the window, taskbar, and Cinnamon menu."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ICON_DIR = APP_DIR / "icons"
ICON_FILE = APP_DIR / "oakkeep.png"
ICON_SVG = APP_DIR / "oakkeep.svg"
ICON_NAME = "oakkeep"
WM_CLASS = "Oakkeep"
SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def set_program_identity() -> None:
    """Must run before Gtk.Window is created, or Cinnamon shows the Python gear."""
    try:
        from gi.repository import GLib

        GLib.set_prgname(ICON_NAME)
    except Exception:
        pass
    try:
        from gi.repository import Gdk

        Gdk.set_program_class(WM_CLASS)
    except Exception:
        pass


def apply_window_icon(window) -> None:
    from gi.repository import Gtk

    pixbufs = _pixbufs()
    if pixbufs:
        try:
            Gtk.Window.set_default_icon_list(pixbufs)
        except Exception:
            pass
        try:
            window.set_icon_list(pixbufs)
        except Exception:
            pass
        return
    if ICON_FILE.is_file():
        try:
            Gtk.Window.set_default_icon_from_file(str(ICON_FILE))
            window.set_icon_from_file(str(ICON_FILE))
        except Exception:
            pass


def install_launcher() -> None:
    """Copy native-size marks into the icon theme and write a menu entry."""
    home = Path.home()
    hicolor = home / ".local/share/icons/hicolor"
    apps = home / ".local/share/applications"
    try:
        _write_hicolor(hicolor)
        _write_desktop(apps)
        _refresh_caches(hicolor, apps)
    except Exception:
        pass


def _sized_png(size: int) -> Path | None:
    path = ICON_DIR / f"{size}.png"
    return path if path.is_file() else None


def _pixbufs():
    try:
        from gi.repository import GdkPixbuf
    except Exception:
        return []
    out = []
    for size in SIZES:
        path = _sized_png(size)
        if path is None:
            continue
        try:
            out.append(GdkPixbuf.Pixbuf.new_from_file(str(path)))
        except Exception:
            continue
    if not out and ICON_FILE.is_file():
        try:
            out.append(GdkPixbuf.Pixbuf.new_from_file(str(ICON_FILE)))
        except Exception:
            pass
    return out


def _write_hicolor(hicolor: Path) -> None:
    pixbuf = None
    try:
        from gi.repository import GdkPixbuf

        if ICON_FILE.is_file():
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(ICON_FILE))
    except Exception:
        pixbuf = None
    for size in SIZES:
        dest_dir = hicolor / f"{size}x{size}" / "apps"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{ICON_NAME}.png"
        src = _sized_png(size)
        if src is not None:
            shutil.copy2(src, dest)
            continue
        if pixbuf is not None:
            try:
                from gi.repository import GdkPixbuf as GP

                scaled = pixbuf.scale_simple(size, size, GP.InterpType.BILINEAR)
                scaled.savev(str(dest), "png", [], [])
            except Exception:
                pass
    if ICON_SVG.is_file():
        scalable = hicolor / "scalable" / "apps"
        scalable.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ICON_SVG, scalable / f"{ICON_NAME}.svg")


def _write_desktop(apps: Path) -> None:
    apps.mkdir(parents=True, exist_ok=True)
    script = APP_DIR / "oakkeep.py"
    icon = str(_sized_png(48) or ICON_FILE)
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Oakkeep\n"
        "Comment=Local email archive\n"
        f"Exec=/usr/bin/python3 {quote(str(script))}\n"
        f"Path={APP_DIR}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Office;Email;\n"
        "StartupNotify=true\n"
        f"StartupWMClass={WM_CLASS}\n"
    )
    dest = apps / "oakkeep.desktop"
    dest.write_text(body)
    os.chmod(dest, 0o755)


def quote(path: str) -> str:
    if not path or all(c.isalnum() or c in "._-+/" for c in path):
        return path
    return '"' + path.replace('"', '\\"') + '"'


def _refresh_caches(hicolor: Path, apps: Path) -> None:
    for cmd in (
        ["gtk-update-icon-cache", "-f", "-t", str(hicolor)],
        ["xdg-desktop-menu", "forceupdate"],
        ["update-desktop-database", str(apps)],
    ):
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass
