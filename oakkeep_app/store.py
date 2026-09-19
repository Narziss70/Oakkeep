"""SQLite index next to the EML archive. Disposable — rebuild from files."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .folders import filter_mailboxes
from .mail import (
    eml_filename,
    iter_mailbox,
    looks_like_mailbox,
    mailbox_leaf,
    summarize,
    to_crlf,
)

CONFIG = Path.home() / ".config" / "oakkeep" / "config.json"
INDEX_NAME = ".oakkeep-index.sqlite"


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def save_config(data: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    current = load_config()
    current.update(data)
    CONFIG.write_text(json.dumps(current, indent=2))


def load_pairs() -> list[dict]:
    cfg = load_config()
    items = list(cfg.get("pairs") or [])
    if not items:
        tb = (cfg.get("thunderbird") or "").strip()
        ac = (cfg.get("account") or "").strip()
        if tb and ac:
            items = [{"thunderbird": tb, "account": ac}]
            save_config({"pairs": items})
    return items


def save_pairs(items: list[dict]) -> None:
    save_config({"pairs": items})


class Archive:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.db_path = self.root / INDEX_NAME
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.cancelled = False
        self._schema()

    def request_cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.cancelled = True
        try:
            self.conn.commit()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass

    def _schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY,
              path TEXT UNIQUE NOT NULL,
              folder TEXT NOT NULL,
              archive_key TEXT NOT NULL,
              message_id TEXT,
              date TEXT,
              date_sort TEXT,
              from_addr TEXT,
              from_name TEXT,
              to_addr TEXT,
              subject TEXT,
              size INTEGER,
              has_attach INTEGER,
              attach_names TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_folder_date ON messages(folder, date_sort DESC);
            CREATE INDEX IF NOT EXISTS idx_key ON messages(archive_key);
            CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
              subject, sender, recipients, attach, body, path UNINDEXED
            );
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            """
        )
        # Old databases had UNIQUE(archive_key), so a copy in an unpaired folder was
        # dropped when the same Message-ID already lived under another account.
        # Path stays unique; the same mail may exist in more than one folder.
        try:
            row = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_key'"
            ).fetchone()
            sql = (row[0] if row else "") or ""
            if "UNIQUE" in sql.upper():
                self.conn.execute("DROP INDEX IF EXISTS idx_key")
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_key ON messages(archive_key)")
        except sqlite3.DatabaseError:
            pass
        self.conn.commit()

    def integrity_ok(self) -> bool:
        try:
            row = self.conn.execute("PRAGMA quick_check").fetchone()
            return bool(row) and str(row[0]) == "ok"
        except sqlite3.DatabaseError:
            return False

    def has_eml(self) -> bool:
        for path in self.root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".eml", ".emlx"} and INDEX_NAME not in path.parts and ".notmuch" not in path.parts:
                return True
        return False

    def catch_up(self, progress=None) -> tuple[int, int]:
        """Index .eml files that are on disk but missing from the index. Does not rebuild."""
        indexed = {r[0] for r in self.conn.execute("SELECT path FROM messages")}
        seen = 0
        new = 0
        self.cancelled = False
        for path in self.root.rglob("*"):
            if self.cancelled:
                break
            if not path.is_file() or path.suffix.lower() not in {".eml", ".emlx"}:
                continue
            if INDEX_NAME in path.parts or ".notmuch" in path.parts:
                continue
            seen += 1
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            if rel in indexed:
                if progress and seen % 80 == 0:
                    progress(seen, 0, f"Checking archive… {seen:,} files ({new:,} not in index)")
                continue
            try:
                data = path.read_bytes()[: 512 * 1024]
                row = summarize(data, rel, path.stat().st_size)
                self._insert(row, replace=False)
                indexed.add(rel)
                new += 1
            except sqlite3.IntegrityError:
                indexed.add(rel)
            except Exception:
                pass
            if progress and (seen % 25 == 0):
                progress(seen, 0, f"Checking archive… {seen:,} files ({new:,} not in index)")
        self.conn.commit()
        if progress:
            progress(seen, seen or 1, f"Check done · {seen:,} files · {new:,} added to index")
        return seen, new

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def folders(self) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT folder, COUNT(*) AS n FROM messages GROUP BY folder ORDER BY folder COLLATE NOCASE"
        ).fetchall()
        return [(r["folder"] or "(root)", int(r["n"])) for r in rows]

    def account_prefixes(self) -> list[str]:
        """Top-level archive folders (pairs and unpaired archive directories)."""
        found: list[str] = []
        try:
            tops = sorted(
                [d.name for d in self.root.iterdir() if d.is_dir() and not d.name.startswith(".")],
                key=lambda s: s.lower(),
            )
        except OSError:
            tops = []
        found.extend(tops)
        for folder, _n in self.folders():
            head = folder.split("/", 1)[0]
            if head and head not in found:
                found.append(head)
        return sorted(set(found), key=lambda s: s.lower())

    def disk_folder_rows(self) -> list[tuple[str, int, int]]:
        """(rel path, depth, mode) for every mailbox dir on disk + indexed-only folders.

        mode: 1 = account root (prefix filter), 2 = nested folder (prefix filter so
        INBOX includes Salvati + Sent).
        """
        rows: list[tuple[str, int, int]] = []
        seen: set[str] = set()

        def add(rel: str, depth: int, mode: int) -> None:
            rel = rel.replace("\\", "/").strip("/")
            if not rel or rel in seen:
                return
            seen.add(rel)
            rows.append((rel, depth, mode))

        try:
            tops = sorted(
                [d for d in self.root.iterdir() if d.is_dir() and not d.name.startswith(".")],
                key=lambda p: p.name.lower(),
            )
        except OSError:
            tops = []
        for top in tops:
            add(top.name, 0, 1)

            def walk(current: Path, depth: int) -> None:
                try:
                    kids = sorted(
                        [p for p in current.iterdir() if p.is_dir() and not p.name.startswith(".")],
                        key=lambda p: p.name.lower(),
                    )
                except OSError:
                    return
                for kid in kids:
                    rel = str(kid.relative_to(self.root)).replace("\\", "/")
                    add(rel, depth, 2)
                    walk(kid, depth + 1)

            walk(top, 1)
        for folder, _n in self.folders():
            if folder in seen:
                continue
            parts = [p for p in folder.split("/") if p]
            if not parts:
                continue
            acc = parts[0]
            if acc not in seen:
                add(acc, 0, 1)
            build = acc
            depth = 1
            for part in parts[1:]:
                build = f"{build}/{part}"
                add(build, depth, 2)
                depth += 1
        return rows

    def count_prefix(self, prefix: str | None) -> int:
        if not prefix:
            return self.count()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE folder = ? OR folder LIKE ?",
            (prefix, prefix + "/%"),
        ).fetchone()
        return int(row[0])

    def list_folder(self, folder: str | None, query: str, limit: int = 2000, prefix: bool = False) -> list[sqlite3.Row]:
        q = (query or "").strip()
        folder_sql = ""
        folder_args: list = []
        if folder:
            if prefix:
                folder_sql = " AND ({col} = ? OR {col} LIKE ?)"
                folder_args = [folder, folder + "/%"]
            else:
                folder_sql = " AND {col} = ?"
                folder_args = [folder]

        if q:
            try:
                sql = """
                  SELECT m.* FROM fts
                  JOIN messages m ON m.path = fts.path
                  WHERE fts MATCH ?
                """
                sql += folder_sql.format(col="m.folder")
                args: list = [q] + folder_args
                sql += " ORDER BY m.date_sort DESC LIMIT ?"
                args.append(limit)
                return list(self.conn.execute(sql, args).fetchall())
            except sqlite3.OperationalError:
                like = f"%{q.lower()}%"
                sql = """
                  SELECT * FROM messages
                  WHERE (lower(subject) LIKE ? OR lower(from_name) LIKE ?
                         OR lower(attach_names) LIKE ? OR lower(to_addr) LIKE ?)
                """
                sql += folder_sql.format(col="folder")
                args = [like, like, like, like] + folder_args
                sql += " ORDER BY date_sort DESC LIMIT ?"
                args.append(limit)
                return list(self.conn.execute(sql, args).fetchall())
        if folder:
            sql = "SELECT * FROM messages WHERE 1=1" + folder_sql.format(col="folder")
            sql += " ORDER BY date_sort DESC LIMIT ?"
            return list(self.conn.execute(sql, folder_args + [limit]).fetchall())
        return list(
            self.conn.execute("SELECT * FROM messages ORDER BY date_sort DESC LIMIT ?", (limit,)).fetchall()
        )

    def keys(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT archive_key FROM messages")}

    def get_by_path(self, rel: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM messages WHERE path = ?", (rel,)).fetchone()

    def count_folder(self, folder: str | None, prefix: bool = False) -> int:
        if folder and prefix:
            return self.count_prefix(folder)
        if folder:
            row = self.conn.execute("SELECT COUNT(*) FROM messages WHERE folder = ?", (folder,)).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return int(row[0])

    def read_preview(self, rel: str) -> bytes:
        path = self.root / rel
        with path.open("rb") as fh:
            return fh.read(512 * 1024)

    def read_bytes(self, rel: str) -> bytes:
        return (self.root / rel).read_bytes()

    def _drop_index_rows(self, rels: list[str]) -> None:
        for rel in rels:
            self.conn.execute("DELETE FROM fts WHERE path = ?", (rel,))
            self.conn.execute("DELETE FROM messages WHERE path = ?", (rel,))
        self.conn.commit()

    def delete_paths(self, rels: list[str]) -> int:
        n = 0
        removed: list[str] = []
        for rel in rels:
            path = self.root / rel
            try:
                if path.is_file():
                    path.unlink()
                removed.append(rel)
                n += 1
            except OSError:
                # Gone from disk: still drop the index row
                removed.append(rel)
                n += 1
        self._drop_index_rows(removed)
        return n

    def trash_paths(self, rels: list[str]) -> int:
        """Move .eml files to the desktop trash and drop them from the index."""
        n = 0
        removed: list[str] = []
        for rel in rels:
            path = self.root / rel
            if self._trash_file(path):
                removed.append(rel)
                n += 1
            elif not path.exists():
                removed.append(rel)
                n += 1
        self._drop_index_rows(removed)
        return n

    def _trash_file(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            from gi.repository import Gio

            f = Gio.File.new_for_path(str(path))
            return bool(f.trash())
        except Exception:
            pass
        try:
            import subprocess

            r = subprocess.run(["gio", "trash", str(path)], check=False)
            return r.returncode == 0 and not path.exists()
        except Exception:
            return False

    def rebuild(self, progress=None) -> int:
        self.conn.execute("DELETE FROM messages")
        self.conn.execute("DELETE FROM fts")
        self.conn.commit()
        files: list[Path] = []
        scanned = 0
        self.cancelled = False
        for path in self.root.rglob("*"):
            if self.cancelled:
                break
            scanned += 1
            if path.is_file() and path.suffix.lower() in {".eml", ".emlx"} and INDEX_NAME not in path.parts and ".notmuch" not in path.parts:
                files.append(path)
            if progress and scanned % 150 == 0:
                progress(len(files), 0, f"Scanning folders… {len(files):,} messages found")
        total = len(files)
        if progress:
            progress(0, total, f"Indexing 0 / {total:,}")
        for i, path in enumerate(files, 1):
            if self.cancelled:
                break
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            try:
                data = path.read_bytes()[: 512 * 1024]
                row = summarize(data, rel, path.stat().st_size)
                self._insert(row)
            except Exception:
                pass
            if progress and (i % 10 == 0 or i == total):
                pct = int(100 * i / total) if total else 100
                progress(i, total, f"Indexing {i:,} / {total:,}  ({pct}%)")
            if i % 200 == 0:
                self.conn.commit()
        self.conn.commit()
        return total

    def index_file(self, rel: str, data: bytes, size: int) -> bool:
        try:
            row = summarize(data[: 512 * 1024], rel, size)
            self._insert(row)
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def _insert(self, row: dict, replace: bool = True) -> None:
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        self.conn.execute(
            f"""
            {verb} INTO messages
              (path, folder, archive_key, message_id, date, date_sort, from_addr, from_name,
               to_addr, subject, size, has_attach, attach_names)
            VALUES (:path, :folder, :archive_key, :message_id, :date, :date_sort, :from_addr, :from_name,
                    :to_addr, :subject, :size, :has_attach, :attach_names)
            """,
            row,
        )
        self.conn.execute("DELETE FROM fts WHERE path = ?", (row["path"],))
        self.conn.execute(
            "INSERT INTO fts (subject, sender, recipients, attach, body, path) VALUES (?,?,?,?,?,?)",
            (
                row["subject"],
                row["from_name"],
                row["to_addr"],
                row["attach_names"],
                row["body"],
                row["path"],
            ),
        )

    def copy_thunderbird(self, tb: Path, account: str, progress=None, folder_enabled: dict | None = None) -> tuple[int, int]:
        dest = self.root / account if account else self.root
        dest.mkdir(parents=True, exist_ok=True)
        keys = self.keys()
        imported = 0
        skipped = 0
        self.cancelled = False
        mailboxes = filter_mailboxes(
            tb,
            [p for p in tb.rglob("*") if looks_like_mailbox(p)],
            folder_enabled,
        )
        total = max(len(mailboxes), 1)
        last_ui = time.monotonic()
        done_msgs = 0
        for i, mailbox in enumerate(mailboxes, 1):
            if self.cancelled:
                break
            existing = [p.name for p in dest.iterdir() if p.is_dir()] if dest.is_dir() else []
            leaf = mailbox_leaf(mailbox.name, existing)
            folder = dest / leaf
            folder.mkdir(parents=True, exist_ok=True)
            if progress:
                progress(i, total, imported, skipped)
            for raw in iter_mailbox(mailbox):
                if self.cancelled:
                    break
                done_msgs += 1
                row = summarize(raw, "tmp", len(raw))
                key = row["archive_key"]
                if key in keys:
                    skipped += 1
                else:
                    name = eml_filename(row["subject"], row["date"], key)
                    rel_folder = str(folder.relative_to(self.root)).replace("\\", "/")
                    rel = f"{rel_folder}/{name}"
                    payload = to_crlf(raw)
                    out = self.root / rel
                    if out.exists():
                        skipped += 1
                    else:
                        out.write_bytes(payload)
                        row["path"] = rel
                        row["folder"] = rel_folder
                        row["size"] = out.stat().st_size
                        try:
                            self._insert(row)
                            keys.add(key)
                            imported += 1
                        except sqlite3.IntegrityError:
                            skipped += 1
                now = time.monotonic()
                if progress and (done_msgs % 15 == 0 or now - last_ui > 0.3):
                    last_ui = now
                    progress(i, total, imported, skipped)
            if progress:
                progress(i, total, imported, skipped)
        self.conn.commit()
        return imported, skipped
