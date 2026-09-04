"""The decision layer: given an incoming message, produce the reply text."""

import logging
import re

from . import config, ledger, reply, vision

log = logging.getLogger("plate")

MACRO_WORDS = {
    "cal": "calories", "cals": "calories", "calories": "calories", "kcal": "calories",
    "protein": "protein", "p": "protein",
    "carb": "carbs", "carbs": "carbs", "c": "carbs",
    "fat": "fat", "f": "fat",
}


def handle_photo(conn, handle: str, image_path, msg_guid: str | None, cfg: dict) -> str:
    """Estimate a photo, log it, and describe where the day now stands."""
    prepared = vision.prepare_image(image_path)
    try:
        est = vision.estimate(prepared)
    finally:
        if not cfg.get("keep_photos", True):
            prepared.unlink(missing_ok=True)

    if not est["items"] or est["calories"] <= 0:
        return reply.no_food(est)

    inserted = ledger.add_entry(
        conn,
        {
            "msg_guid": msg_guid,
            "handle": handle,
            "dish": est["dish"],
            "calories": est["calories"],
            "protein": est["protein"],
            "carbs": est["carbs"],
            "fat": est["fat"],
            "confidence": est["confidence"],
            "uncertainty": est["uncertainty"],
            "items": [i["name"] for i in est["items"]],
            "photo": str(prepared) if cfg.get("keep_photos", True) else None,
        },
    )
    if inserted is None:
        # Same message seen twice; report the day rather than double-counting.
        log.info("skipping already-logged message %s", msg_guid)
        return reply.status(
            ledger.day_totals(conn, handle), ledger.get_targets(conn, handle, cfg)
        )

    return reply.logged(
        est,
        ledger.day_totals(conn, handle),
        ledger.get_targets(conn, handle, cfg),
    )


def handle_command(conn, handle: str, text: str, cfg: dict) -> str | None:
    """Text-only commands. Returns None when the text isn't a command."""
    words = text.strip().lower().split()
    if not words:
        return None
    verb = words[0]

    if verb in {"today", "status", "left", "remaining"}:
        return reply.status(
            ledger.day_totals(conn, handle), ledger.get_targets(conn, handle, cfg)
        )

    if verb == "undo":
        entry = ledger.undo_last(conn, handle)
        targets = ledger.get_targets(conn, handle, cfg)
        if entry is None:
            return "Nothing logged today yet.\n\n" + reply.day_block(
                ledger.day_totals(conn, handle), targets
            )
        return reply.undone(entry, ledger.day_totals(conn, handle), targets)

    if verb in {"target", "goal"}:
        return _set_target(conn, handle, words[1:], cfg)

    if verb in {"help", "?", "commands"}:
        return reply.HELP

    return None


def _set_target(conn, handle: str, args: list[str], cfg: dict) -> str:
    numbers = [w for w in args if re.fullmatch(r"\d{1,5}", w)]
    if not numbers:
        return "Try TARGET 2400, or TARGET PROTEIN 180."

    macro = "calories"
    for word in args:
        if word in MACRO_WORDS:
            macro = MACRO_WORDS[word]
            break

    ledger.set_target(conn, handle, macro, int(numbers[-1]))
    return reply.target_set(ledger.get_targets(conn, handle, cfg))


def respond(conn, handle: str, text: str, images: list, msg_guid: str | None, cfg: dict) -> str:
    """Single entry point: photo wins, then commands, then a nudge."""
    if images:
        return handle_photo(conn, handle, images[0], msg_guid, cfg)

    command = handle_command(conn, handle, text, cfg)
    if command is not None:
        return command

    return (
        "Send me a photo of your food and I'll log it.\n\n"
        + reply.status(ledger.day_totals(conn, handle), ledger.get_targets(conn, handle, cfg))
    )
