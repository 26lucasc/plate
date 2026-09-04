"""Reading incoming iMessages and sending replies back out.

Reads are done directly against the Messages SQLite database (read-only);
sends go through the Messages app via AppleScript.
"""

import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import config

# Messages stores timestamps as nanoseconds since 2001-01-01.
APPLE_EPOCH_OFFSET = 978_307_200

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".tiff", ".bmp"}

GROUP_CHAT_STYLE = 43


class MessagesAccessError(RuntimeError):
    """chat.db could not be read -- almost always missing Full Disk Access."""


@dataclass
class Incoming:
    rowid: int
    guid: str
    handle: str
    text: str
    chat_style: int | None
    is_from_me: bool = False
    chat_identifier: str = ""
    images: list[Path] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return self.chat_style == GROUP_CHAT_STYLE

    @property
    def conversation(self) -> str:
        """Who this thread is with -- the sender, or the recipient if I sent it."""
        return self.chat_identifier or self.handle


QUERY = """
SELECT
    m.ROWID                AS rowid,
    m.guid                 AS guid,
    COALESCE(h.id, '')     AS handle,
    COALESCE(m.text, '')   AS text,
    c.style                AS chat_style,
    COALESCE(c.chat_identifier, '') AS chat_identifier,
    m.is_from_me           AS is_from_me,
    a.filename             AS filename,
    a.mime_type            AS mime_type
FROM message m
LEFT JOIN handle h              ON h.ROWID = m.handle_id
LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat c                ON c.ROWID = cmj.chat_id
LEFT JOIN message_attachment_join maj ON maj.message_id = m.ROWID
LEFT JOIN attachment a          ON a.ROWID = maj.attachment_id
WHERE m.ROWID > ?
  AND COALESCE(m.associated_message_type, 0) = 0
ORDER BY m.ROWID
"""


def _open_chat_db(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.CHAT_DB
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise MessagesAccessError(str(exc)) from exc
    conn.row_factory = sqlite3.Row
    return conn


def _is_image(filename: str | None, mime_type: str | None) -> bool:
    if mime_type and mime_type.startswith("image/"):
        return True
    if filename and Path(filename).suffix.lower() in IMAGE_SUFFIXES:
        return True
    return False


def max_rowid(path: Path | None = None) -> int:
    """Where the message table currently ends -- used to skip history on first run."""
    conn = _open_chat_db(path)
    try:
        row = conn.execute("SELECT COALESCE(MAX(ROWID), 0) AS n FROM message").fetchone()
        return int(row["n"])
    except sqlite3.OperationalError as exc:
        raise MessagesAccessError(str(exc)) from exc
    finally:
        conn.close()


def fetch_since(since_rowid: int, path: Path | None = None) -> list[Incoming]:
    """Every inbound message after `since_rowid`, with its image attachments attached."""
    conn = _open_chat_db(path)
    try:
        rows = conn.execute(QUERY, (since_rowid,)).fetchall()
    except sqlite3.OperationalError as exc:
        raise MessagesAccessError(str(exc)) from exc
    finally:
        conn.close()

    # One row per attachment, so fold them back into one message each.
    messages: dict[int, Incoming] = {}
    for row in rows:
        msg = messages.get(row["rowid"])
        if msg is None:
            msg = Incoming(
                rowid=row["rowid"],
                guid=row["guid"],
                handle=row["handle"],
                text=row["text"] or "",
                chat_style=row["chat_style"],
                is_from_me=bool(row["is_from_me"]),
                chat_identifier=row["chat_identifier"] or "",
            )
            messages[row["rowid"]] = msg
        if _is_image(row["filename"], row["mime_type"]) and row["filename"]:
            msg.images.append(Path(row["filename"]).expanduser())

    return [messages[k] for k in sorted(messages)]


def own_handles() -> set[str]:
    """The addresses this Mac's own iMessage account answers to.

    Messages exposes them as an account description like "E:me@example.com".
    Needed because a message you send from your own iPhone syncs here as
    outgoing, not incoming.
    """
    osascript = shutil.which("osascript")
    if not osascript:
        return set()

    handles: set[str] = set()
    for index in range(1, 9):
        result = subprocess.run(
            [osascript, "-e", f'tell application "Messages" to get description of account {index}'],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue
        value = result.stdout.strip()
        if not value or value == "missing value":
            continue
        for part in value.split(","):
            part = part.strip()
            if part[:2] in {"E:", "P:"}:
                part = part[2:]
            if part:
                handles.add(part)
    return handles


SEND_SCRIPT = """
on run argv
    set theHandle to item 1 of argv
    set theBody to item 2 of argv
    tell application "Messages"
        try
            set theService to 1st account whose service type = iMessage
            set theBuddy to participant theHandle of theService
            send theBody to theBuddy
        on error
            set theService to 1st account whose service type = SMS
            set theBuddy to participant theHandle of theService
            send theBody to theBuddy
        end try
    end tell
end run
"""


def send(handle: str, body: str) -> None:
    """Send a message back. Raises RuntimeError if Messages refuses."""
    osascript = shutil.which("osascript")
    if not osascript:
        raise RuntimeError("osascript not found")

    result = subprocess.run(
        [osascript, "-", handle, body],
        input=SEND_SCRIPT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Messages refused the send: {result.stderr.strip()[:300]}")
