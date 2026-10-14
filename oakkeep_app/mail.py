"""Parse RFC822 / Thunderbird mbox. Archive is .eml files on disk."""

from __future__ import annotations

import hashlib
import re
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

SKIP_NAME = re.compile(
    r"(\.(msf|dat|sqlite|sqlite-wal|sqlite-shm|html|json|log|ini|mozmsgs)$"
    r"|^(msgfilterrules|filterlog|popstate|panacea|folder\.cache))",
    re.I,
)
ALIASES = {
    "inbox": "Inbox",
    "sent": "Sent",
    "sent-mail": "Sent",
    "drafts": "Drafts",
    "trash": "Trash",
    "junk": "Junk",
    "spam": "Junk",
    "archivio": "Archivio",
    "archives": "Archive",
}


def decode_words(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def parse_message(data: bytes):
    if data.startswith(b"From "):
        nl = data.find(b"\n")
        if nl > 0:
            data = data[nl + 1 :]
    for pol in (policy.default, policy.compat32):
        try:
            return BytesParser(policy=pol).parsebytes(data)
        except Exception:
            continue
    return BytesParser().parsebytes(data)


def addresses(msg, header: str) -> str:
    vals = msg.get_all(header, [])
    out = []
    for v in vals:
        out.append(decode_words(str(v)))
    return ", ".join(out)


def first_addr(blob: str) -> str:
    m = re.search(r"[\w.+-]+@[\w.-]+", blob or "")
    return m.group(0).lower() if m else ""


def archive_key(message_id: str, from_addr: str, date: str, subject: str) -> str:
    mid = re.sub(r"[<>\s]", "", message_id or "").lower()
    if "@" in mid and not re.search(r"@(oakkeep\.local|msg\.oakkeep\.local)$", mid):
        return f"id:{mid}"
    try:
        minute = parsedate_to_datetime(date).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        minute = (date or "")[:16]
    return f"fp:{(from_addr or '').lower()}|{minute}|{(subject or '').strip().lower()}"


def parsed_date(date: str):
    if not date:
        return None
    try:
        return parsedate_to_datetime(date)
    except Exception:
        return None


def utc_sort(date: str) -> str:
    """Absolute instant for sorting. Naive headers are left as written."""
    dt = parsed_date(date)
    if dt is None:
        return (date or "")[:19]
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_when(date: str, fallback: str = "") -> str:
    """Wall time in the computer's current timezone, matching a mailbox UI."""
    dt = parsed_date(date)
    if dt is None:
        raw = (fallback or date or "").replace("T", " ").replace("Z", "")
        return raw[:16]
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def iso_date(date: str) -> str:
    return utc_sort(date)


def iso_day(date: str) -> str:
    try:
        return parsedate_to_datetime(date).strftime("%Y-%m-%d")
    except Exception:
        return "unknown-date"


def best_date(msg) -> str:
    """Prefer Date: header; fall back to the date in the first Received: header."""
    date = decode_words(msg.get("date"))
    if date and parsed_date(date):
        return date
    for rec in msg.get_all("received", []) or []:
        rec = decode_words(str(rec))
        m = re.search(r";\s*(.+)$", rec)
        if m:
            cand = m.group(1).strip()
            if parsed_date(cand):
                return cand
    return date or ""


def mailbox_incomplete(path: Path) -> bool:
    """Practical heuristic: local mbox looks incomplete relative to its .msf index.

    Flags a folder when a non-trivial .msf exists but the mbox yields no messages,
    or the index is disproportionately large compared with the extracted content.
    """
    msf = path.with_name(path.name + ".msf")
    if not msf.is_file():
        return False
    try:
        msf_size = msf.stat().st_size
        mbox_size = path.stat().st_size
    except OSError:
        return False
    if msf_size < 2048:
        return False
    try:
        count = 0
        for _ in iter_mailbox(path):
            count += 1
            if count > 2000:
                break
    except Exception:
        return False
    if count == 0 and msf_size > 4096:
        return True
    # .msf is typically a few hundred bytes per message summary
    if count > 0 and msf_size > count * 1500 and mbox_size < max(count * 200, 1024):
        return True
    return False


def safe_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:48] or "message")


def attachment_names(msg) -> list[str]:
    names: list[str] = []
    if not getattr(msg, "walk", None):
        return names
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = str(part.get("Content-Disposition") or "")
        if "attachment" not in disp.lower() and not part.get_filename():
            ctype = str(part.get_content_type() or "")
            if ctype.startswith("text/") or ctype in {"application/pkcs7-signature", "application/pgp-signature"}:
                continue
        name = part.get_filename()
        if name:
            names.append(decode_words(name))
    return names


def body_text(msg, cap: int = 8000) -> str:
    plain = ""
    html = ""
    if not getattr(msg, "walk", None):
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            return payload.decode("utf-8", "replace")[:cap]
        return str(msg.get_payload() or "")[:cap]
    for part in msg.walk():
        ctype = part.get_content_type()
        disp = str(part.get("Content-Disposition") or "")
        if "attachment" in disp.lower():
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if not isinstance(payload, bytes):
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, "replace")
        except Exception:
            text = payload.decode("utf-8", "replace")
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not html:
            html = text
    if plain:
        return plain[:cap]
    if html:
        return re.sub(r"<[^>]+>", " ", html)[:cap]
    return ""


def html_body(msg) -> str:
    if not getattr(msg, "walk", None):
        return ""
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if isinstance(payload, bytes):
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, "replace")
                except Exception:
                    return payload.decode("utf-8", "replace")
    return ""


def summarize(data: bytes, rel_path: str, size: int) -> dict:
    msg = parse_message(data)
    subject = decode_words(msg.get("subject")) or "(no subject)"
    from_raw = addresses(msg, "from")
    to_raw = addresses(msg, "to")
    date = best_date(msg)
    mid = decode_words(msg.get("message-id"))
    atts = attachment_names(msg)
    from_addr = first_addr(from_raw)
    return {
        "path": rel_path,
        "folder": str(Path(rel_path).parent).replace("\\", "/").lstrip("./"),
        "archive_key": archive_key(mid, from_addr, date, subject),
        "message_id": mid,
        "date": date,
        "date_sort": iso_date(date),
        "from_addr": from_addr,
        "from_name": from_raw,
        "to_addr": to_raw,
        "subject": subject,
        "size": size,
        "has_attach": 1 if atts else 0,
        "attach_names": " ".join(atts),
        "body": body_text(msg),
    }


def split_mbox(data: bytes) -> list[bytes]:
    if not data.strip():
        return []
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data.startswith(b"From "):
        return [data]
    parts: list[bytes] = []
    start = 0
    while True:
        nxt = data.find(b"\nFrom ", start + 1)
        if nxt < 0:
            parts.append(data[start:])
            break
        parts.append(data[start:nxt])
        start = nxt + 1
    return [p.lstrip(b"\r\n") for p in parts if len(p.strip()) > 20]


def iter_mailbox(path: Path):
    """Yield messages from mbox or a single .eml. Streams large files."""
    size = path.stat().st_size
    suffix = path.suffix.lower()
    looks_mbox_name = suffix in {".mbox", ".mbx"} or "." not in path.name
    if size < 4_000_000:
        data = path.read_bytes()
        if data.startswith(b"From ") or looks_mbox_name:
            yield from split_mbox(data)
        else:
            yield data
        return
    with path.open("rb") as fh:
        first = fh.read(5)
        fh.seek(0)
        if first != b"From " and not looks_mbox_name:
            yield fh.read()
            return
        buf = b""
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                if len(buf.strip()) > 20:
                    yield buf
                break
            buf += chunk
            start = 0
            while True:
                nxt = buf.find(b"\nFrom ", start + 1)
                if nxt < 0:
                    if start:
                        buf = buf[start:]
                    break
                part = buf[start:nxt]
                if len(part.strip()) > 20:
                    yield part
                start = nxt + 1


def looks_like_mailbox(path: Path) -> bool:
    if not path.is_file():
        return False
    if SKIP_NAME.search(path.name):
        return False
    ext = path.suffix.lower()
    if ext in {".eml", ".emlx", ".mbox", ".mbx"}:
        return True
    return "." not in path.name


def mailbox_leaf(filename: str, existing: list[str]) -> str:
    raw = re.sub(r"\.(mbox|mbx)$", "", filename, flags=re.I)
    want = ALIASES.get(raw.lower(), raw)
    for d in existing:
        if d.lower() in {raw.lower(), want.lower()}:
            return d
    return want


def eml_filename(subject: str, date: str, key: str) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"{iso_day(date)}_{digest}_{safe_filename(subject)}.eml"


def to_crlf(raw: bytes) -> bytes:
    payload = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if payload.startswith(b"From "):
        nl = payload.find(b"\r\n")
        if nl > 0:
            payload = payload[nl + 2 :]
    if not payload.endswith(b"\r\n"):
        payload += b"\r\n"
    return payload
