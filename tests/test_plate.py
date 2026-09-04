import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plate import config, core, imessage, ledger, reply, vision  # noqa: E402


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(config, "PHOTO_DIR", tmp_path / "photos")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "plate.db")
    return ledger.connect(tmp_path / "plate.db")


@pytest.fixture
def cfg():
    return {"allowed_handles": ["+14155550123"], "targets": dict(config.DEFAULT_TARGETS),
            "keep_photos": False}


# ---------------------------------------------------------------- handles


@pytest.mark.parametrize(
    "a,b",
    [
        ("+1 (415) 555-0123", "4155550123"),
        ("+14155550123", "415-555-0123"),
        ("Me@Example.com", "me@example.com"),
    ],
)
def test_handle_forms_match(a, b):
    assert config.normalize_handle(a) == config.normalize_handle(b)


def test_unlisted_handle_is_rejected(cfg):
    assert config.is_allowed("+1 415 555 0123", cfg)
    assert not config.is_allowed("+14155559999", cfg)
    assert not config.is_allowed("", cfg)


# ---------------------------------------------------------------- vision parsing


def test_normalize_sums_components():
    est = vision.normalize(
        {
            "dish": "Burrito bowl",
            "items": [
                {"name": "rice", "grams": 180, "calories": 240, "protein": 4, "carbs": 52, "fat": 1},
                {"name": "chicken", "grams": 140, "calories": 230, "protein": 43, "carbs": 0, "fat": 5},
            ],
            "confidence": "medium",
            "uncertainty": "rice depth",
        }
    )
    assert est["calories"] == 470
    assert est["protein"] == 47
    assert est["confidence"] == "medium"


def test_normalize_survives_junk_fields():
    est = vision.normalize(
        {"dish": "x", "items": [{"name": "a", "calories": "nope", "protein": None}],
         "confidence": "very sure"}
    )
    assert est["calories"] == 0
    assert est["confidence"] == "low"  # unknown confidence downgrades


def test_extract_json_from_fenced_output():
    raw = '```json\n{"dish": "Toast", "items": [], "confidence": "high"}\n```'
    assert vision._extract_json(raw)["dish"] == "Toast"


def test_extract_json_from_chatty_output():
    raw = 'Here you go:\n{"dish": "Toast", "items": []}\nHope that helps!'
    assert vision._extract_json(raw)["dish"] == "Toast"


def test_extract_json_raises_on_prose():
    with pytest.raises(vision.VisionError):
        vision._extract_json("I could not analyze that image.")


# ---------------------------------------------------------------- ledger


def _entry(guid, calories=500, **kw):
    base = {"msg_guid": guid, "handle": "+14155550123", "dish": "Meal",
            "calories": calories, "protein": 40, "carbs": 50, "fat": 15}
    base.update(kw)
    return base


def test_totals_accumulate(conn):
    ledger.add_entry(conn, _entry("a", 500))
    ledger.add_entry(conn, _entry("b", 700))
    totals = ledger.day_totals(conn, "+14155550123")
    assert totals["calories"] == 1200
    assert totals["meals"] == 2


def test_same_message_logs_once(conn):
    assert ledger.add_entry(conn, _entry("dup")) is not None
    assert ledger.add_entry(conn, _entry("dup")) is None
    assert ledger.day_totals(conn, "+14155550123")["meals"] == 1


def test_totals_are_per_handle(conn):
    ledger.add_entry(conn, _entry("a", 500))
    ledger.add_entry(conn, _entry("b", 900, handle="+14155559999"))
    assert ledger.day_totals(conn, "+14155550123")["calories"] == 500


def test_undo_removes_only_the_last(conn):
    ledger.add_entry(conn, _entry("a", 500))
    ledger.add_entry(conn, _entry("b", 700))
    removed = ledger.undo_last(conn, "+14155550123")
    assert removed["calories"] == 700
    assert ledger.day_totals(conn, "+14155550123")["calories"] == 500


def test_undo_on_empty_day(conn):
    assert ledger.undo_last(conn, "+14155550123") is None


def test_targets_default_then_override(conn, cfg):
    assert ledger.get_targets(conn, "+14155550123", cfg)["calories"] == 2200
    ledger.set_target(conn, "+14155550123", "calories", 2600)
    assert ledger.get_targets(conn, "+14155550123", cfg)["calories"] == 2600
    # normalized handle means the same person from a differently formatted number
    assert ledger.get_targets(conn, "4155550123", cfg)["calories"] == 2600


def test_cursor_roundtrip(conn):
    assert ledger.get_cursor(conn) == 0
    ledger.set_cursor(conn, 42)
    assert ledger.get_cursor(conn) == 42


# ---------------------------------------------------------------- commands


def test_today_command(conn, cfg):
    ledger.add_entry(conn, _entry("a", 600))
    out = core.handle_command(conn, "+14155550123", "today", cfg)
    assert "600/2,200" in out
    assert "1,600 kcal left" in out


def test_commands_are_case_insensitive(conn, cfg):
    assert core.handle_command(conn, "+14155550123", "  TODAY  ", cfg) is not None


def test_undo_command(conn, cfg):
    ledger.add_entry(conn, _entry("a", 600, dish="Bagel"))
    out = core.handle_command(conn, "+14155550123", "undo", cfg)
    assert "Removed Bagel" in out
    assert ledger.day_totals(conn, "+14155550123")["calories"] == 0


def test_target_command_sets_calories(conn, cfg):
    out = core.handle_command(conn, "+14155550123", "target 2400", cfg)
    assert "2,400 kcal" in out
    assert ledger.get_targets(conn, "+14155550123", cfg)["calories"] == 2400


def test_target_command_sets_a_macro(conn, cfg):
    core.handle_command(conn, "+14155550123", "target protein 180", cfg)
    targets = ledger.get_targets(conn, "+14155550123", cfg)
    assert targets["protein"] == 180
    assert targets["calories"] == 2200  # untouched


def test_target_command_without_a_number(conn, cfg):
    assert "TARGET 2400" in core.handle_command(conn, "+14155550123", "target", cfg)


def test_unknown_text_is_not_a_command(conn, cfg):
    assert core.handle_command(conn, "+14155550123", "thanks!", cfg) is None


def test_respond_falls_back_to_a_nudge(conn, cfg):
    out = core.respond(conn, "+14155550123", "thanks!", [], None, cfg)
    assert "photo of your food" in out


# ---------------------------------------------------------------- reply text


def test_reply_shows_remaining():
    totals = {"calories": 1600, "protein": 120, "carbs": 150, "fat": 60, "meals": 3}
    out = reply.status(totals, config.DEFAULT_TARGETS)
    assert "600 kcal left" in out
    assert "P 120/165g" in out
    assert "3 meals logged" in out


def test_reply_reports_going_over():
    totals = {"calories": 2500, "protein": 120, "carbs": 150, "fat": 60, "meals": 4}
    assert "300 kcal over" in reply.status(totals, config.DEFAULT_TARGETS)


def test_reply_singular_meal():
    totals = {"calories": 500, "protein": 1, "carbs": 1, "fat": 1, "meals": 1}
    assert "1 meal logged" in reply.status(totals, config.DEFAULT_TARGETS)


def test_logged_reply_flags_low_confidence():
    est = {"dish": "Pad thai", "calories": 800, "protein": 30, "carbs": 90, "fat": 30,
           "confidence": "low", "uncertainty": "noodle volume", "items": []}
    totals = {"calories": 800, "protein": 30, "carbs": 90, "fat": 30, "meals": 1}
    out = reply.logged(est, totals, config.DEFAULT_TARGETS)
    assert "Pad thai — 800 kcal" in out
    assert "low confidence — noodle volume" in out
    assert "UNDO" in out


def test_logged_reply_hides_caveat_when_confident():
    est = {"dish": "Eggs", "calories": 300, "protein": 20, "carbs": 2, "fat": 22,
           "confidence": "high", "uncertainty": "none", "items": []}
    totals = {"calories": 300, "protein": 20, "carbs": 2, "fat": 22, "meals": 1}
    assert "confidence" not in reply.logged(est, totals, config.DEFAULT_TARGETS)


# ---------------------------------------------------------------- chat.db reading


def _fake_chat_db(path: Path) -> Path:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
            handle_id INTEGER, is_from_me INTEGER, associated_message_type INTEGER);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, style INTEGER, chat_identifier TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT, mime_type TEXT);
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
        INSERT INTO handle VALUES (1, '+14155550123');
        INSERT INTO chat VALUES (1, 45, '+14155550123'), (2, 43, 'chat99');
        INSERT INTO message VALUES
            (1, 'g1', 'today', 1, 0, 0),
            (2, 'g2', '', 1, 0, 0),
            (3, 'g3', 'today', 1, 1, 0),
            (4, 'g4', 'liked a photo', 1, 0, 2000),
            (5, 'g5', 'in a group', 1, 0, 0);
        INSERT INTO chat_message_join VALUES (1,1),(1,2),(1,3),(1,4),(2,5);
        INSERT INTO attachment VALUES
            (1, '~/Library/Messages/Attachments/a/food.HEIC', 'image/heic'),
            (2, '~/Library/Messages/Attachments/b/note.pdf', 'application/pdf');
        INSERT INTO message_attachment_join VALUES (2,1),(2,2);
        """
    )
    db.commit()
    db.close()
    return path


def test_fetch_since_filters_and_folds(tmp_path):
    db = _fake_chat_db(tmp_path / "chat.db")
    messages = imessage.fetch_since(0, db)
    by_guid = {m.guid: m for m in messages}

    assert "g4" not in by_guid, "tapbacks are skipped"
    assert by_guid["g3"].is_from_me, "outbound messages are returned, flagged as mine"
    assert by_guid["g3"].conversation == "+14155550123"

    assert by_guid["g1"].text == "today"
    assert by_guid["g1"].images == []

    # The PDF is dropped; the HEIC survives with ~ expanded.
    assert len(by_guid["g2"].images) == 1
    assert by_guid["g2"].images[0].name == "food.HEIC"
    assert "~" not in str(by_guid["g2"].images[0])

    assert by_guid["g5"].is_group
    assert not by_guid["g1"].is_group


def test_fetch_since_respects_the_cursor(tmp_path):
    db = _fake_chat_db(tmp_path / "chat.db")
    assert [m.guid for m in imessage.fetch_since(4, db)] == ["g5"]


def test_conversation_falls_back_to_sender(tmp_path):
    msg = imessage.Incoming(1, "g", "+14155550123", "hi", 45)
    assert msg.conversation == "+14155550123"


def test_max_rowid(tmp_path):
    assert imessage.max_rowid(_fake_chat_db(tmp_path / "chat.db")) == 5


def test_missing_chat_db_raises_access_error(tmp_path):
    with pytest.raises(imessage.MessagesAccessError):
        imessage.max_rowid(tmp_path / "nope.db")


# ---------------------------------------------------------------- daemon


def test_daemon_ignores_strangers_and_groups(conn, cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(imessage, "send", lambda h, b: sent.append((h, b)))

    stranger = imessage.Incoming(1, "g", "+14155559999", "today", 45)
    group = imessage.Incoming(2, "g", "+14155550123", "today", 43)
    from plate import daemon

    assert daemon.process_one(conn, stranger, cfg) is None
    assert daemon.process_one(conn, group, cfg) is None
    assert sent == []


def test_daemon_replies_to_an_allowed_handle(conn, cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(imessage, "send", lambda h, b: sent.append((h, b)))
    from plate import daemon

    msg = imessage.Incoming(1, "g", "+14155550123", "today", 45)
    daemon.process_one(conn, msg, cfg)
    assert len(sent) == 1
    assert "kcal left" in sent[0][1]


def test_self_sent_photo_is_actionable():
    from plate import daemon

    cfg = {"allowed_handles": []}
    msg = imessage.Incoming(1, "g", "", "", 45, is_from_me=True,
                            chat_identifier="me@example.com", images=[Path("a.jpg")])
    assert daemon.is_actionable(msg, cfg, {"me@example.com"})


def test_self_sent_command_is_actionable():
    from plate import daemon

    msg = imessage.Incoming(1, "g", "", "TODAY", 45, is_from_me=True,
                            chat_identifier="me@example.com")
    assert daemon.is_actionable(msg, {"allowed_handles": []}, {"me@example.com"})


def _every_reply_plate_can_send():
    totals = {"calories": 500, "protein": 1, "carbs": 1, "fat": 1, "meals": 1}
    est = {"dish": "Today's special", "calories": 500, "protein": 1, "carbs": 1, "fat": 1,
           "confidence": "low", "uncertainty": "portion", "items": []}
    entry = {"dish": "Toast", "calories": 200}
    return [
        reply.status(totals, config.DEFAULT_TARGETS),
        reply.logged(est, totals, config.DEFAULT_TARGETS),
        reply.undone(entry, totals, config.DEFAULT_TARGETS),
        reply.target_set(config.DEFAULT_TARGETS),
        reply.no_food({"uncertainty": "No food visible."}),
        reply.HELP,
        "That one didn't go through — try sending the photo again.",
    ]


@pytest.mark.parametrize("body", _every_reply_plate_can_send())
def test_plate_does_not_answer_its_own_replies(body):
    """Plate's replies are sent from this Mac too -- if one reads as a command, it loops."""
    from plate import daemon

    msg = imessage.Incoming(1, "g", "", body, 45, is_from_me=True,
                            chat_identifier="me@example.com")
    assert not daemon.is_actionable(msg, {"allowed_handles": []}, {"me@example.com"})


@pytest.mark.parametrize("text", ["today", "  UNDO ", "target 2400", "target protein 180", "help"])
def test_real_commands_still_pass_the_guard(text):
    from plate import daemon

    assert daemon.looks_like_a_command(text)


def test_ordinary_self_notes_are_ignored():
    from plate import daemon

    msg = imessage.Incoming(1, "g", "", "remember to call mom", 45, is_from_me=True,
                            chat_identifier="me@example.com")
    assert not daemon.is_actionable(msg, {"allowed_handles": []}, {"me@example.com"})


def test_self_chat_on_someone_elses_thread_is_ignored():
    """A photo I text to a friend must not get logged as my meal."""
    from plate import daemon

    msg = imessage.Incoming(1, "g", "", "", 45, is_from_me=True,
                            chat_identifier="+14155559999", images=[Path("a.jpg")])
    assert not daemon.is_actionable(msg, {"allowed_handles": []}, {"me@example.com"})


def test_daemon_advances_cursor_past_a_failing_message(conn, cfg, monkeypatch):
    from plate import daemon

    monkeypatch.setattr(imessage, "send", lambda h, b: None)
    monkeypatch.setattr(
        imessage, "fetch_since",
        lambda since: [imessage.Incoming(7, "g", "+14155550123", "today", 45)],
    )
    monkeypatch.setattr(core, "respond", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    daemon.tick(conn, cfg)
    assert ledger.get_cursor(conn) == 7  # does not get stuck re-processing


def test_daemon_reports_a_failure_to_the_sender(conn, cfg, monkeypatch):
    from plate import daemon

    sent = []
    monkeypatch.setattr(imessage, "send", lambda h, b: sent.append(b))
    monkeypatch.setattr(core, "respond", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    daemon.process_one(conn, imessage.Incoming(1, "g", "+14155550123", "hi", 45), cfg)
    assert "try sending the photo again" in sent[0]


# ---------------------------------------------------------------- photo flow


def test_handle_photo_logs_and_reports(conn, cfg, monkeypatch, tmp_path):
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    monkeypatch.setattr(vision, "prepare_image", lambda src, dest_dir=None: photo)
    monkeypatch.setattr(
        vision, "estimate",
        lambda p, model=None: vision.normalize(
            {"dish": "Chicken bowl",
             "items": [{"name": "bowl", "grams": 400, "calories": 700, "protein": 50,
                        "carbs": 60, "fat": 25}],
             "confidence": "high", "uncertainty": ""}
        ),
    )
    out = core.handle_photo(conn, "+14155550123", photo, "guid-1", cfg)
    assert "Chicken bowl — 700 kcal" in out
    assert "1,500 kcal left" in out
    assert ledger.day_totals(conn, "+14155550123")["meals"] == 1


def test_handle_photo_with_no_food_logs_nothing(conn, cfg, monkeypatch, tmp_path):
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    monkeypatch.setattr(vision, "prepare_image", lambda src, dest_dir=None: photo)
    monkeypatch.setattr(
        vision, "estimate",
        lambda p, model=None: vision.normalize(
            {"dish": None, "items": [], "confidence": "low", "uncertainty": "No food visible."}
        ),
    )
    out = core.handle_photo(conn, "+14155550123", photo, "guid-2", cfg)
    assert "No food visible." in out
    assert ledger.day_totals(conn, "+14155550123")["meals"] == 0


def test_resending_the_same_photo_does_not_double_count(conn, cfg, monkeypatch, tmp_path):
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    monkeypatch.setattr(vision, "prepare_image", lambda src, dest_dir=None: photo)
    monkeypatch.setattr(
        vision, "estimate",
        lambda p, model=None: vision.normalize(
            {"dish": "Toast", "items": [{"name": "toast", "grams": 60, "calories": 200,
                                         "protein": 6, "carbs": 30, "fat": 4}],
             "confidence": "high"}
        ),
    )
    core.handle_photo(conn, "+14155550123", photo, "same-guid", cfg)
    core.handle_photo(conn, "+14155550123", photo, "same-guid", cfg)
    assert ledger.day_totals(conn, "+14155550123")["calories"] == 200
