"""The day's food ledger: one SQLite file, one row per logged meal."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY,
    msg_guid    TEXT UNIQUE,
    handle      TEXT NOT NULL,
    date        TEXT NOT NULL,
    logged_at   TEXT NOT NULL,
    dish        TEXT,
    calories    INTEGER NOT NULL DEFAULT 0,
    protein     INTEGER NOT NULL DEFAULT 0,
    carbs       INTEGER NOT NULL DEFAULT 0,
    fat         INTEGER NOT NULL DEFAULT 0,
    confidence  TEXT,
    uncertainty TEXT,
    items       TEXT,
    photo       TEXT
);
CREATE INDEX IF NOT EXISTS entries_by_day ON entries (handle, date);

CREATE TABLE IF NOT EXISTS settings (
    handle TEXT NOT NULL,
    key    TEXT NOT NULL,
    value  TEXT NOT NULL,
    PRIMARY KEY (handle, key)
);

CREATE TABLE IF NOT EXISTS cursor (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    config.ensure_home()
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------- cursor: how far through chat.db the daemon has read ----------


def get_cursor(conn: sqlite3.Connection, key: str = "chat_rowid") -> int:
    row = conn.execute("SELECT value FROM cursor WHERE key = ?", (key,)).fetchone()
    return int(row["value"]) if row else 0


def set_cursor(conn: sqlite3.Connection, value: int, key: str = "chat_rowid") -> None:
    conn.execute(
        "INSERT INTO cursor (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


# ---------- per-person targets ----------


def get_targets(conn: sqlite3.Connection, handle: str, cfg: dict) -> dict:
    targets = dict(cfg.get("targets") or config.DEFAULT_TARGETS)
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE handle = ? AND key LIKE 'target_%'",
        (config.normalize_handle(handle),),
    ).fetchall()
    for row in rows:
        macro = row["key"][len("target_"):]
        if macro in targets:
            targets[macro] = int(row["value"])
    return targets


def set_target(conn: sqlite3.Connection, handle: str, macro: str, value: int) -> None:
    conn.execute(
        "INSERT INTO settings (handle, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(handle, key) DO UPDATE SET value = excluded.value",
        (config.normalize_handle(handle), f"target_{macro}", str(int(value))),
    )
    conn.commit()


# ---------- entries ----------


def add_entry(conn: sqlite3.Connection, entry: dict) -> int | None:
    """Insert a meal. Returns None if this message was already logged."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO entries
            (msg_guid, handle, date, logged_at, dish, calories, protein, carbs,
             fat, confidence, uncertainty, items, photo)
        VALUES (:msg_guid, :handle, :date, :logged_at, :dish, :calories, :protein,
                :carbs, :fat, :confidence, :uncertainty, :items, :photo)
        """,
        {
            "msg_guid": entry.get("msg_guid"),
            "handle": config.normalize_handle(entry["handle"]),
            "date": entry.get("date") or today_key(),
            "logged_at": entry.get("logged_at") or datetime.now().isoformat(timespec="seconds"),
            "dish": entry.get("dish"),
            "calories": int(entry.get("calories") or 0),
            "protein": int(entry.get("protein") or 0),
            "carbs": int(entry.get("carbs") or 0),
            "fat": int(entry.get("fat") or 0),
            "confidence": entry.get("confidence"),
            "uncertainty": entry.get("uncertainty"),
            "items": json.dumps(entry.get("items") or []),
            "photo": entry.get("photo"),
        },
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def day_entries(conn: sqlite3.Connection, handle: str, date: str | None = None) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entries WHERE handle = ? AND date = ? ORDER BY id",
        (config.normalize_handle(handle), date or today_key()),
    ).fetchall()


def day_totals(conn: sqlite3.Connection, handle: str, date: str | None = None) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS meals,
               COALESCE(SUM(calories), 0) AS calories,
               COALESCE(SUM(protein), 0)  AS protein,
               COALESCE(SUM(carbs), 0)    AS carbs,
               COALESCE(SUM(fat), 0)      AS fat
        FROM entries WHERE handle = ? AND date = ?
        """,
        (config.normalize_handle(handle), date or today_key()),
    ).fetchone()
    return dict(row)


def undo_last(conn: sqlite3.Connection, handle: str, date: str | None = None) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM entries WHERE handle = ? AND date = ? ORDER BY id DESC LIMIT 1",
        (config.normalize_handle(handle), date or today_key()),
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM entries WHERE id = ?", (row["id"],))
    conn.commit()
    return row
