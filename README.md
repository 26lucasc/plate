# Plate

Text a photo of your food. Get a text back with the macros and what's left for the day.

```
Tofu poke-style salad bowl — 343 kcal
P 27g · C 31g · F 15g
(medium confidence — cannot tell if a dressing was added beyond the visible chili seasoning)

Today 343/2,200 — 1,857 kcal left
P 27/165g · C 31/220g · F 15/73g
1 meal logged

Reply UNDO to remove this.
```

No app to open, no account to make, no API key. It runs on your Mac, talks over
iMessage, and estimates with the `claude` CLI you already have.

## How it works

```
iPhone photo ──iMessage──▶ Mac  ──▶ sips (HEIC→JPEG, 1024px)
                                     └──▶ claude -p  (vision → JSON macros)
                                            └──▶ SQLite ledger (today's total)
                                                   └──▶ AppleScript ──iMessage──▶ iPhone
```

Everything stays on your machine except the photo, which goes to Claude for the
estimate. The ledger is a SQLite file at `~/.plate/plate.db`.

## Install

Requires macOS, Python 3.11+, and [Claude Code](https://claude.com/claude-code)
on your PATH.

```bash
git clone https://github.com/26lucasc/plate.git
cd plate
pip install -e .
```

Then grant the one permission macOS won't let a script grant itself:

**System Settings → Privacy & Security → Full Disk Access → add your terminal.**
Quit and reopen the terminal afterward. This is what lets Plate read the
Messages database; without it Plate can send but never sees your photos.

```bash
plate setup --self     # or: plate setup +14155550123
plate doctor           # every line should be a ✓
```

`plate doctor` names the exact fix for anything that isn't ready.

## Use it

```bash
plate run        # listen in the foreground
plate install    # or run in the background, starting at login
```

Now text a photo of your food. The reply lands in a few seconds.

By text:

| Send | Get |
|---|---|
| *a photo* | the meal's macros, plus the day so far |
| `TODAY` | what's left for the day |
| `UNDO` | removes the last meal |
| `TARGET 2400` | sets your calorie goal |
| `TARGET PROTEIN 180` | sets a macro goal |
| `HELP` | the list above |

From the terminal:

```bash
plate today                  # print today's ledger
plate log ~/Desktop/mac.jpg  # log a photo from disk
plate watch                  # show what Plate sees, without replying
plate uninstall              # stop the background service
```

## Texting yourself

The usual case is someone texting *you*. But if you want to log your own meals —
and your iPhone and this Mac share one Apple ID — your messages arrive here as
*outgoing*, not incoming. Plate handles that: it reads your own thread and
answers photos and commands there.

That means Plate is reading messages it also writes, so the command guard is
deliberately strict — a command must be one line and at most three words. A
normal note to yourself is ignored, and Plate never answers its own replies.
`tests/test_plate.py` asserts this for every reply Plate can send.

## What it won't do

- **Portion size is the hard part.** Every estimate says how confident it is and
  names the single thing it couldn't determine. Treat low-confidence numbers as
  a range, and reply `UNDO` when one is clearly off.
- Only 1:1 chats. Group messages are ignored.
- Only handles you list in `plate setup` are answered.
- Photos you send to *other* people are never logged as your meals.

## Configuration

`~/.plate/config.json`:

```json
{
  "allowed_handles": ["you@icloud.com"],
  "targets": { "calories": 2200, "protein": 165, "carbs": 220, "fat": 73 },
  "keep_photos": true
}
```

Per-person targets set by text override these. Environment overrides:
`PLATE_HOME`, `PLATE_MODEL`, `PLATE_POLL_SECONDS`.

## Development

```bash
pip install -e '.[dev]'
pytest
```

The tests run against a synthetic `chat.db` and a stubbed estimator, so the
whole message-handling path is covered without touching Messages or the network.
