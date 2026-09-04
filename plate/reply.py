"""Formats the text message that comes back. Optimized to be read at a glance."""

MACROS = (("protein", "P"), ("carbs", "C"), ("fat", "F"))


def _n(value: int) -> str:
    return f"{int(value):,}"


def meal_line(est: dict) -> str:
    dish = est.get("dish") or "Meal"
    return f"{dish} — {_n(est['calories'])} kcal"


def macro_line(source: dict) -> str:
    return " · ".join(f"{short} {_n(source[key])}g" for key, short in MACROS)


def day_block(totals: dict, targets: dict) -> str:
    left = targets["calories"] - totals["calories"]
    if left >= 0:
        headline = f"Today {_n(totals['calories'])}/{_n(targets['calories'])} — {_n(left)} kcal left"
    else:
        headline = f"Today {_n(totals['calories'])}/{_n(targets['calories'])} — {_n(-left)} kcal over"

    macros = " · ".join(
        f"{short} {_n(totals[key])}/{_n(targets[key])}g" for key, short in MACROS
    )
    meals = totals.get("meals", 0)
    return f"{headline}\n{macros}\n{meals} meal{'' if meals == 1 else 's'} logged"


def logged(est: dict, totals: dict, targets: dict) -> str:
    lines = [meal_line(est), macro_line(est)]
    if est.get("confidence") in {"medium", "low"} and est.get("uncertainty"):
        lines.append(f"({est['confidence']} confidence — {est['uncertainty']})")
    lines.append("")
    lines.append(day_block(totals, targets))
    lines.append("")
    lines.append("Reply UNDO to remove this.")
    return "\n".join(lines)


def status(totals: dict, targets: dict) -> str:
    return day_block(totals, targets)


def undone(entry, totals: dict, targets: dict) -> str:
    dish = entry["dish"] or "Meal"
    return f"Removed {dish} (−{_n(entry['calories'])} kcal).\n\n{day_block(totals, targets)}"


def target_set(targets: dict) -> str:
    macros = " · ".join(f"{short} {_n(targets[key])}g" for key, short in MACROS)
    return f"Targets updated.\n{_n(targets['calories'])} kcal · {macros}"


def no_food(est: dict) -> str:
    return (
        (est.get("uncertainty") or "I don't see food in that photo.")
        + "\nSend a shot of the plate and I'll log it."
    )


HELP = (
    "Plate — text me a photo of your food and I'll log it.\n\n"
    "TODAY — what's left for the day\n"
    "UNDO — remove the last meal\n"
    "TARGET 2400 — set your calorie goal\n"
    "TARGET PROTEIN 180 — set a macro goal\n"
    "HELP — this message"
)
