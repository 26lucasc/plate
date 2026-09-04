"""plate — text a photo of your food, get your macros back."""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config, core, daemon, imessage, ledger, reply, vision

PLIST_LABEL = "com.plate.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _setup_logging(verbose: bool) -> None:
    config.ensure_home()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(config.LOG_PATH)],
    )


# ---------------------------------------------------------------- commands


def cmd_setup(args) -> int:
    cfg = config.load()
    handles = list(args.handle or [])
    if args.self:
        handles.extend(imessage.own_handles())
    if not handles:
        print("Usage: plate setup +14155550123 [more handles...]")
        print("       plate setup --self     (use this Mac's own iMessage address)")
        print("Use the phone number or Apple ID you'll be texting from.")
        return 1

    existing = {config.normalize_handle(h) for h in cfg["allowed_handles"]}
    for handle in handles:
        if config.normalize_handle(handle) not in existing:
            cfg["allowed_handles"].append(handle)
    config.save(cfg)

    print(f"Allowed handles: {', '.join(cfg['allowed_handles'])}")
    print(f"Config: {config.CONFIG_PATH}")
    return 0


def cmd_doctor(args) -> int:
    """Check every dependency Plate needs, and say exactly how to fix each gap."""
    ok = True

    def check(label: str, passed: bool, fix: str = "") -> None:
        nonlocal ok
        print(f"{'✓' if passed else '✗'} {label}")
        if not passed:
            ok = False
            if fix:
                print(f"    → {fix}")

    check("sips (image conversion)", shutil.which("sips") is not None, "macOS only")
    check(
        "claude CLI (the estimator)",
        shutil.which("claude") is not None,
        "install Claude Code, then re-run",
    )

    readable = True
    try:
        imessage.max_rowid()
    except imessage.MessagesAccessError:
        readable = False
    check(
        "read access to Messages",
        readable,
        "System Settings → Privacy & Security → Full Disk Access → enable your terminal "
        "(or the app running plate), then restart it",
    )

    can_send = (
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "Messages" to get id of 1st account '
                "whose service type = iMessage",
            ],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    check(
        "send access to Messages",
        can_send,
        "open Messages and sign in to iMessage; approve the automation prompt on first send",
    )

    cfg = config.load()
    check(
        "an allowed handle",
        bool(cfg.get("allowed_handles")),
        "plate setup +1XXXXXXXXXX",
    )

    print()
    print("Ready." if ok else "Fix the items above, then run `plate doctor` again.")
    return 0 if ok else 1


def cmd_run(args) -> int:
    _setup_logging(args.verbose)
    try:
        daemon.run(poll_seconds=args.interval)
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


def cmd_today(args) -> int:
    cfg = config.load()
    handle = args.handle or (cfg["allowed_handles"][0] if cfg["allowed_handles"] else "")
    if not handle:
        print("No handle configured. Run: plate setup +1XXXXXXXXXX")
        return 1

    conn = ledger.connect()
    entries = ledger.day_entries(conn, handle, args.date)
    for entry in entries:
        print(f"  {entry['logged_at'][11:16]}  {entry['dish'] or 'Meal'}  {entry['calories']} kcal")
    if entries:
        print()
    print(
        reply.status(
            ledger.day_totals(conn, handle, args.date),
            ledger.get_targets(conn, handle, cfg),
        )
    )
    return 0


def cmd_log(args) -> int:
    """Estimate a photo from disk and log it, exactly as an inbound text would."""
    _setup_logging(args.verbose)
    cfg = config.load()
    handle = args.handle or (cfg["allowed_handles"][0] if cfg["allowed_handles"] else "local")
    conn = ledger.connect()
    try:
        print(core.handle_photo(conn, handle, Path(args.image), None, cfg))
    except vision.VisionError as exc:
        print(f"Could not read that photo: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_send(args) -> int:
    """Send a test message, to confirm the outbound half works."""
    cfg = config.load()
    handle = args.handle or (cfg["allowed_handles"][0] if cfg["allowed_handles"] else "")
    if not handle:
        print("No handle configured. Run: plate setup +1XXXXXXXXXX")
        return 1
    try:
        imessage.send(handle, args.message)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Sent to {handle}.")
    return 0


def cmd_watch(args) -> int:
    """Print what Plate sees, without replying. For confirming the read half works."""
    _setup_logging(args.verbose)
    cfg = config.load()
    conn = ledger.connect()
    self_handles = {config.normalize_handle(h) for h in imessage.own_handles()}
    print(f"This Mac answers to: {', '.join(sorted(self_handles)) or '(nothing)'}")
    print(f"Allowed handles: {', '.join(cfg['allowed_handles']) or '(none)'}")

    try:
        since = imessage.max_rowid() - args.backfill
    except imessage.MessagesAccessError as exc:
        print(f"Cannot read Messages: {exc}", file=sys.stderr)
        print("Run `plate doctor` for the fix.", file=sys.stderr)
        return 1

    print(f"Watching from message {since}. Ctrl-C to stop.\n")
    try:
        while True:
            for msg in imessage.fetch_since(since):
                since = msg.rowid
                verdict = "ACT " if daemon.is_actionable(msg, cfg, self_handles) else "skip"
                direction = "me→" if msg.is_from_me else "→me"
                photos = f" [{len(msg.images)} image]" if msg.images else ""
                print(f"{verdict} {direction} {msg.conversation}: {msg.text[:60]!r}{photos}")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>plate</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key><string>{workdir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>{path}</string>
        <key>PYTHONPATH</key><string>{workdir}</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def cmd_install(args) -> int:
    config.ensure_home()
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(__file__).resolve().parent.parent
    PLIST_PATH.write_text(
        PLIST_TEMPLATE.format(
            label=PLIST_LABEL,
            python=sys.executable,
            workdir=workdir,
            path=os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
            log=config.LOG_PATH,
        )
    )
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    print(f"Plate is running in the background.\nLogs: {config.LOG_PATH}")
    return 0


def cmd_uninstall(args) -> int:
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.unlink(missing_ok=True)
    print("Background service removed.")
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plate", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="allow a phone number or Apple ID to log meals")
    p.add_argument("handle", nargs="*")
    p.add_argument("--self", action="store_true", help="allow this Mac's own iMessage address")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("doctor", help="check that everything Plate needs is in place")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("run", help="listen for photos in the foreground")
    p.add_argument("--interval", type=float, default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("today", help="print today's ledger")
    p.add_argument("--handle")
    p.add_argument("--date")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("log", help="log a photo from disk")
    p.add_argument("image")
    p.add_argument("--handle")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("send", help="send a test message")
    p.add_argument("message", nargs="?", default="Plate is listening.")
    p.add_argument("--handle")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("watch", help="show what Plate sees, without replying")
    p.add_argument("--backfill", type=int, default=0, help="also show the last N messages")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("install", help="run Plate in the background at login")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="stop running in the background")
    p.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
