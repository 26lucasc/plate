"""Configuration: where state lives and who is allowed to talk to Plate."""

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("PLATE_HOME", Path.home() / ".plate")).expanduser()
DB_PATH = HOME / "plate.db"
CONFIG_PATH = HOME / "config.json"
PHOTO_DIR = HOME / "photos"
LOG_PATH = HOME / "plate.log"

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

MODEL = os.environ.get("PLATE_MODEL", "claude-sonnet-5")
POLL_SECONDS = float(os.environ.get("PLATE_POLL_SECONDS", "3"))

DEFAULT_TARGETS = {
    "calories": 2200,
    "protein": 165,
    "carbs": 220,
    "fat": 73,
}

DEFAULTS = {
    # iMessage handles (phone numbers / emails) allowed to log meals.
    # Empty list means nobody -- `plate setup` fills this in.
    "allowed_handles": [],
    "targets": dict(DEFAULT_TARGETS),
    # Keep a downscaled copy of each logged photo.
    "keep_photos": True,
}


def ensure_home() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    ensure_home()
    cfg = dict(DEFAULTS)
    cfg["targets"] = dict(DEFAULT_TARGETS)
    if CONFIG_PATH.exists():
        try:
            on_disk = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            on_disk = {}
        for key, value in on_disk.items():
            if key == "targets" and isinstance(value, dict):
                cfg["targets"].update(value)
            else:
                cfg[key] = value
    return cfg


def save(cfg: dict) -> None:
    ensure_home()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def normalize_handle(handle: str) -> str:
    """Phone numbers arrive in a few shapes; compare on digits only."""
    handle = (handle or "").strip().lower()
    if "@" in handle:
        return handle
    digits = "".join(ch for ch in handle if ch.isdigit())
    # Treat a bare US 10-digit number and its +1 form as the same person.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def is_allowed(handle: str, cfg: dict) -> bool:
    target = normalize_handle(handle)
    if not target:
        return False
    return any(normalize_handle(h) == target for h in cfg.get("allowed_handles", []))
