"""List Thunderbird mailboxes and remember which ones to copy."""

from __future__ import annotations

from pathlib import Path

from .mail import looks_like_mailbox, mailbox_leaf


def mailbox_key(tb: Path, mailbox: Path) -> str:
    try:
        return mailbox.relative_to(tb).as_posix()
    except ValueError:
        return mailbox.name


def display_folder_path(tb: Path, mailbox: Path) -> str:
    try:
        rel = mailbox.relative_to(tb)
    except ValueError:
        return mailbox_leaf(mailbox.name, [])
    parts: list[str] = []
    for part in rel.parts[:-1]:
        name = part[:-4] if part.endswith(".sbd") else part
        parts.append(mailbox_leaf(name, []))
    parts.append(mailbox_leaf(rel.name, []))
    return "/".join(parts)


def list_account_folders(tb: Path) -> list[dict]:
    """Mailboxes under one Thunderbird account folder, same set Copy uses."""
    root = Path(tb)
    if not root.is_dir():
        return []
    try:
        paths = list(root.rglob("*"))
    except OSError:
        return []
    rows: list[dict] = []
    for path in paths:
        try:
            if not looks_like_mailbox(path):
                continue
            display = display_folder_path(root, path)
            rows.append(
                {
                    "key": mailbox_key(root, path),
                    "display": display,
                    "depth": display.count("/"),
                    "path": path,
                }
            )
        except Exception:
            continue
    rows.sort(key=lambda r: (r["display"].lower(), r["key"]))
    return rows


def folder_is_enabled(folder_enabled: dict | None, key: str) -> bool:
    """Missing key = selected. Empty/None map = every folder selected."""
    if not folder_enabled:
        return True
    return bool(folder_enabled.get(key, True))


def is_parent_display(display: str, all_displays: list[str]) -> bool:
    """True if another mailbox lives under this path ([Gmail] vs [Gmail]/Sent)."""
    prefix = display.rstrip("/") + "/"
    return any(other.startswith(prefix) for other in all_displays if other != display)


def leaf_rows(rows: list[dict]) -> list[dict]:
    displays = [r["display"] for r in rows]
    return [r for r in rows if not is_parent_display(r["display"], displays)]


def merge_folder_map(saved: dict | None, listed_keys: list[str], live: dict[str, bool]) -> dict[str, bool]:
    """Apply checkbox state. Drop stale keys so a vanished parent is not counted."""
    out: dict[str, bool] = {}
    saved_d = saved if isinstance(saved, dict) else {}
    listed = set(listed_keys)
    for key in listed_keys:
        out[key] = bool(live.get(key, saved_d.get(key, True)))
    # Keep explicit False for folders that disappeared so they stay off if they return
    for key, value in saved_d.items():
        if key not in listed and not value:
            out[str(key)] = False
    return out


def folder_summary(folders: dict | None, rows: list[dict] | None = None) -> str:
    if rows:
        leaves = leaf_rows(rows)
        keys = [r["key"] for r in leaves]
        enabled = folders or {}
        total = len(keys)
        selected = sum(1 for key in keys if enabled.get(key, True))
    else:
        if not folders:
            return "All folders"
        # Ignore keys that look like parents of another saved key
        displays = list(folders.keys())
        keys = [k for k in folders if not is_parent_display(k, displays) and not any(
            other.startswith(str(k).rstrip("/") + "/") for other in folders if other != k
        )]
        if not keys:
            keys = list(folders.keys())
        total = len(keys)
        selected = sum(1 for key in keys if folders.get(key))
    if total == 0:
        return "All folders"
    if selected == total:
        return "All folders"
    if selected == 0:
        return "No folders"
    return f"{selected} / {total} folders"


def filter_mailboxes(tb: Path, mailboxes: list[Path], folder_enabled: dict | None) -> list[Path]:
    if not folder_enabled:
        return mailboxes
    root = Path(tb)
    return [p for p in mailboxes if folder_is_enabled(folder_enabled, mailbox_key(root, p))]
