"""Poll Messages for new photos, answer each one, remember where we stopped."""

import logging
import time

from . import config, core, imessage, ledger

log = logging.getLogger("plate")


COMMAND_WORDS = {"today", "status", "left", "remaining", "undo", "target", "goal", "help"}

# The longest real command is "target protein 180".
MAX_COMMAND_WORDS = 3


def looks_like_a_command(text: str) -> bool:
    """Terse, single-line, and starts with a command word.

    Deliberately strict: Plate's own replies are sent from this Mac too, so a
    loose match here would have Plate answering itself forever.
    """
    text = text.strip()
    if not text or "\n" in text:
        return False
    words = text.lower().split()
    if len(words) > MAX_COMMAND_WORDS:
        return False
    return words[0] in COMMAND_WORDS


def is_actionable(msg: imessage.Incoming, cfg: dict, self_handles: set[str]) -> bool:
    """Decide whether a message is one Plate should answer.

    Two shapes count. A message *to* this Mac from an allowed handle is the
    ordinary case. A message *from* this Mac is the "text yourself" case: your
    iPhone syncs those here as outgoing, so Plate has to read its own thread —
    but only for photos and explicit commands, or it would answer its own
    replies forever.
    """
    if msg.is_group:
        log.debug("ignoring group chat message %s", msg.guid)
        return False

    counterpart = msg.conversation
    if not config.is_allowed(counterpart, cfg) and not (
        msg.is_from_me and config.normalize_handle(counterpart) in self_handles
    ):
        log.debug("ignoring message on thread with %s", counterpart)
        return False

    if msg.is_from_me:
        if msg.images:
            return True
        return looks_like_a_command(msg.text)

    return bool(msg.images or msg.text.strip())


def process_one(
    conn, msg: imessage.Incoming, cfg: dict, self_handles: set[str] | None = None
) -> str | None:
    """Answer a single message. Returns the reply sent, or None if ignored."""
    if not is_actionable(msg, cfg, self_handles or set()):
        return None

    counterpart = msg.conversation
    try:
        body = core.respond(conn, counterpart, msg.text, msg.images, msg.guid, cfg)
    except Exception:
        log.exception("failed to handle message %s", msg.guid)
        body = "That one didn't go through — try sending the photo again."

    imessage.send(counterpart, body)
    log.info("replied to %s (message %s)", counterpart, msg.rowid)
    return body


def tick(conn, cfg: dict, self_handles: set[str] | None = None) -> int:
    """One poll cycle. Returns how many messages were answered."""
    since = ledger.get_cursor(conn)
    answered = 0
    for msg in imessage.fetch_since(since):
        try:
            if process_one(conn, msg, cfg, self_handles) is not None:
                answered += 1
        finally:
            # Advance past every message we look at, so one bad photo can't
            # wedge the queue and re-reply forever.
            ledger.set_cursor(conn, msg.rowid)
    return answered


def run(poll_seconds: float | None = None) -> None:
    cfg = config.load()
    conn = ledger.connect()

    if not cfg.get("allowed_handles"):
        log.warning("no allowed handles configured -- every message will be ignored")

    if ledger.get_cursor(conn) == 0:
        # First run: start at the present so old conversations aren't replayed.
        ledger.set_cursor(conn, imessage.max_rowid())
        log.info("starting fresh at message %s", ledger.get_cursor(conn))

    self_handles = {config.normalize_handle(h) for h in imessage.own_handles()}
    log.info("this Mac answers to %s", ", ".join(sorted(self_handles)) or "(nothing)")

    interval = poll_seconds if poll_seconds is not None else config.POLL_SECONDS
    log.info("plate is listening (every %ss)", interval)

    while True:
        try:
            tick(conn, cfg, self_handles)
        except imessage.MessagesAccessError as exc:
            log.error("cannot read Messages: %s", exc)
            time.sleep(30)
        except Exception:
            log.exception("poll cycle failed")
            time.sleep(5)
        time.sleep(interval)
