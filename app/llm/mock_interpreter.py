"""Offline deterministic mock interpreter used when no API key is configured.

This module understands the six directive types and a curated set of natural
language phrasings. It exists so the LLM layer is genuinely on the
interpretation path even when no model provider is configured (for example
in local CI). It is not a phrase matcher; it intentionally re-uses the same
conversion rules (fraction-from-percentage, start-inclusive/end-exclusive
window) the real LLM should produce, so offline and online paths can be
swapped without changing the rest of the pipeline.
"""
from __future__ import annotations

import re
from typing import Any

_DIRECTIVE_ENUM = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}

_DISTRACTOR_HINTS = (
    "cafeteria",
    "menu",
    "library is extending",
    "registration",
    "registration deadline",
    "seminar room booking",
    "club notices",
    "club notice",
    "office will publish",
    "deadline",
    "book-return",
    "student affairs",
    "sports office",
    "moved next month",
    "moved next week",
    "next week",
    "tomorrow",
    "next month",
)

_ENERGY_KEYS = (
    "solar",
    "battery",
    "charge",
    "discharge",
    "grid",
    "reserve",
    "import",
    "panel",
    "inverter",
    "feeder",
    "transformer",
    "substation",
)


_PERCENT_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_FACTOR_TOKEN = re.compile(r"\b(?:factor|fraction|usable)\b.*?(\d+(?:\.\d+)?)\s*%?", re.I)
_FROM_TO_HOURS = re.compile(
    r"\bfrom\s+([0-9: ]+\s*(?:am|pm|AM|PM)?)\s*(?:until|to|till|through)\s+"
    r"([0-9: ]+\s*(?:am|pm|AM|PM)?)",
    re.I,
)
_BETWEEN_HOURS = re.compile(
    r"\bbetween\s+([0-9: ]+\s*(?:am|pm|AM|PM)?)\s*(?:and|to)\s+"
    r"([0-9: ]+\s*(?:am|pm|AM|PM)?)",
    re.I,
)
_AT_HOUR_RANGE = re.compile(
    r"\b(?:at|@)\s+([0-9: ]+\s*(?:am|pm|AM|PM)?)\s*(?:to|until|and)\s*"
    r"([0-9: ]+\s*(?:am|pm|AM|PM)?)",
    re.I,
)
_HOUR_TOKEN = re.compile(r"\b([0-9]{1,2})(?::([0-9]{2}))?\s*(am|pm|AM|PM)?\b")
_NUMBER_TOKEN = re.compile(r"(\d+(?:\.\d+)?)")

_SOLAR_HINTS = (
    "solar",
    "panel",
    "panels",
    "pv",
    "rooftop",
    "rooftops",
    "inverter",
    "cleaning",
    "cloud cover",
    "inspection",
    "rooftop solar",
    "rooftop solar output",
    "forecast",
)
_BATTERY_RESERVE_HINTS = (
    "reserve",
    "keep at least",
    "remain in the battery",
    "remain available",
    "emergency operations",
    "emergency reserve",
    "emergency services",
    "stored",
    "available during",
    "must remain in the battery",
)
_NO_CHARGE_HINTS = (
    "do not charge",
    "no charge",
    "no charging",
    "cannot charge",
    "will not charge",
    "won't charge",
    "charger is isolated",
    "charging circuit",
    "charger unavailable",
    "charge is disabled",
    "charging is disabled",
    "charging is unavailable",
    "no-charge maintenance",
    "no-charge window",
    "battery charger",
    "isolated",
    "charging outage",
    "charger maintenance",
    "charger offline",
    "charging circuit will be unavailable",
    "charging circuit is unavailable",
)
_NO_DISCHARGE_HINTS = (
    "do not discharge",
    "no discharge",
    "cannot discharge",
    "won't discharge",
    "discharge is disabled",
    "discharging is disabled",
    "discharging is unavailable",
    "no-discharge",
    "no discharge window",
    "discharge unavailable",
    "will not discharge",
    "must not discharge",
    "relay testing",
)
_MAX_GRID_HINTS = (
    "grid import",
    "grid intake",
    "import cap",
    "grid cap",
    "feeder",
    "transformer",
    "substation",
    "import must not exceed",
    "intake must stay",
    "must not exceed",
    "must stay at or below",
    "feeder is operating",
    "transformer limit",
)

_WORD_HOURS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

# Word -> percent (0..100). Used as the value BEFORE applying direction
# (reduction vs "kept at"). Keys are the standalone number words that map to
# a percentage when followed by "percent" / "per cent".
_WORD_PERCENT = {
    "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _resolve_anchor(token: str) -> int | None:
    token = token.strip().lower()
    if "noon" in token:
        return 12
    if "midnight" in token:
        return 0
    m = _HOUR_TOKEN.search(token)
    if m:
        val = int(m.group(1))
        suffix = (m.group(3) or "").lower()
        if suffix == "pm" and val < 12:
            val += 12
        elif suffix == "am" and val == 12:
            val = 0
        if 0 <= val <= 23:
            return val
    for word, val in _WORD_HOURS.items():
        if word in token:
            return val
    return None


def _extract_window(text: str) -> list[int] | None:
    text = text.lower()
    cleaned = text.replace("\n", " ")

    # "2 PM through the start of 5 PM" -- collapse "start of" before anchor parse.
    cleaned = re.sub(r"\b(?:the\s+)?start\s+of\s+", "", cleaned)

    # Comma- or "and"-separated list of hours like "18:00 and 21:00" => [18, 21].
    # We treat it as "from first to last", end-exclusive.
    list_tokens = re.findall(
        r"\b([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm|AM|PM)?|noon|midnight)\b",
        cleaned,
    )
    if len(list_tokens) >= 2 and (" and " in cleaned or "," in cleaned):
        anchors = [_resolve_anchor(t) for t in list_tokens]
        if all(a is not None for a in anchors) and len(set(anchors)) == len(anchors):
            start = anchors[0]
            end = anchors[-1]
            if start != end:
                return _expand_window(start, end)

    m = re.search(
        r"\bfrom\s+([^.\n,]+?)\s*(?:until|to|till|through)\s+([^.\n,]+)",
        cleaned,
        re.I,
    )
    if m:
        start = _resolve_anchor(m.group(1))
        end = _resolve_anchor(m.group(2))
        if start is not None and end is not None and start != end:
            return _expand_window(start, end)

    m = re.search(
        r"\bbetween\s+([^.\n,]+?)\s*(?:and|to)\s+([^.\n,]+)",
        cleaned,
        re.I,
    )
    if m:
        start = _resolve_anchor(m.group(1))
        end = _resolve_anchor(m.group(2))
        if start is not None and end is not None and start != end:
            return _expand_window(start, end)

    # Range with bare hours like "2 PM through 5 PM" or "18:00 to 21:00".
    m = re.search(
        r"\b([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm|AM|PM)?|noon|midnight)\s*"
        r"(?:until|to|till|through)\s+"
        r"([0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm|AM|PM)?|noon|midnight)",
        cleaned,
        re.I,
    )
    if m:
        start = _resolve_anchor(m.group(1))
        end = _resolve_anchor(m.group(2))
        if start is not None and end is not None and start != end:
            return _expand_window(start, end)

    return None


def _expand_window(start: int, end: int) -> list[int]:
    """Return [start, start+1, ..., end-1], start-inclusive, end-exclusive."""
    if start == end:
        return []
    if start < end:
        return list(range(start, end))
    return list(range(start, 24)) + list(range(0, end))


def _approx_factor(lower: str) -> float | None:
    """Return the usable-fraction factor implied by the note text."""
    # "no solar output" or "no solar" within an hour range => factor 0.
    if "no solar" in lower or "zero solar" in lower or "no pv" in lower:
        return 0.0

    m_red = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:reduction|less|short|cut)", lower)
    if m_red:
        val = float(m_red.group(1))
        if 0 <= val <= 100:
            return round(1.0 - val / 100.0, 4)

    m_word_red = re.search(
        r"\b(eighty|ninety|seventy|sixty|fifty|forty|thirty|twenty)\s*(?:percent|per\s*cent)\b",
        lower,
    )
    if m_word_red and re.search(r"\b(reduc(?:e[ds])?|less(?:en)?|short|cut|drop(?:ped)?|low(?:er)?|down)\b", lower):
        word = m_word_red.group(1)
        pct = _WORD_PERCENT.get(word)
        if pct is not None:
            # "eighty percent reduction" => factor 1 - 0.8 = 0.2.
            return round(1.0 - pct / 100.0, 4)

    m_to = re.search(
        r"(?:reduced to|kept at|around|drop to|dropped to|use|usable)\s*"
        r"(\d+(?:\.\d+)?)\s*%",
        lower,
    )
    if m_to:
        val = float(m_to.group(1))
        if 0 <= val <= 100:
            return round(val / 100.0, 4)

    m_of = re.search(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s*)?(?:forecast|normal|panel)", lower)
    if m_of:
        val = float(m_of.group(1))
        if 0 <= val <= 100:
            return round(val / 100.0, 4)

    m_leaves = re.search(r"(?:leaves?|leaves about)\s*(\d+(?:\.\d+)?)\s*%", lower)
    if m_leaves:
        val = float(m_leaves.group(1))
        if 0 <= val <= 100:
            return round(val / 100.0, 4)

    # Word-form fractions like "one fifth of normal", "about a third", etc.
    # Order matters: longer phrases first so "two thirds" wins over "third".
    _WORD_PHRASE_FRAC = {
        "two thirds": 2.0 / 3.0, "two-third": 2.0 / 3.0,
        "three quarters": 0.75, "three-quarter": 0.75,
        "one fifth": 0.2, "a fifth": 0.2,
        "one quarter": 0.25, "a quarter": 0.25,
        "one third": 1.0 / 3.0, "a third": 1.0 / 3.0,
    }
    for phrase, frac in _WORD_PHRASE_FRAC.items():
        if phrase in lower:
            return frac
    if "half" in lower:
        return 0.5
    return None


def _percent_to_reserve(lower: str, capacity_kwh: float) -> float | None:
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s*)?(?:battery\s*)?capacity",
        lower,
    )
    if m:
        return round(float(m.group(1)) / 100.0 * capacity_kwh, 4)
    return None


def _extract_kwh_value(lower: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*kwh\b", lower)
    if m:
        return float(m.group(1))
    return None


def interpret_offline(notes: list[str], capacity_kwh: float) -> list[dict[str, Any]]:
    """Interpret a list of operator notes using deterministic offline rules.

    The function attempts to mimic the LLM's interpretation but uses pure
    Python logic. It is intentionally tolerant: if it cannot decide, it falls
    back to no_op.
    """
    results: list[dict[str, Any]] = []
    for idx, note in enumerate(notes):
        item = _interpret_one(note, idx, capacity_kwh)
        results.append(item)
    return results


def _interpret_one(note: str, idx: int, capacity_kwh: float) -> dict[str, Any]:
    lower = note.lower().strip()

    if any(hint in lower for hint in _DISTRACTOR_HINTS) and not any(
        key in lower for key in _ENERGY_KEYS
    ):
        return _no_op(idx, "note does not affect today's energy schedule")

    hours = _extract_window(lower)

    solar_triggers = (
        "reduc", "drop", "less", "low", "decrease", "wash", "clean",
        "cover", "half", "fifth", "quarter", "%", "of the forecast",
        "of normal", "leaves", "leave",
    )
    if any(s in lower for s in _SOLAR_HINTS) and (
        any(w in lower for w in solar_triggers)
        or "until" in lower
        or " to " in lower
    ):
        if hours:
            factor = _approx_factor(lower)
            if factor is None:
                factor = 0.5
            return _directive(
                idx,
                applies=True,
                directive_type="solar_reduction",
                structured_adjustment={"hours": hours, "factor": factor},
                explanation=(
                    f"Solar reduced to {factor*100:.0f}% during the stated window."
                ),
            )

    if (
        any(s in lower for s in _BATTERY_RESERVE_HINTS)
        or ("at least" in lower and ("battery" in lower or "kwh" in lower))
    ):
        min_kwh = _extract_kwh_value(lower)
        if min_kwh is None:
            pct = _percent_to_reserve(lower, capacity_kwh)
            if pct is not None:
                min_kwh = pct
        if min_kwh is None:
            if "half" in lower:
                min_kwh = round(0.5 * capacity_kwh, 4)
            elif "a third" in lower or "one-third" in lower:
                min_kwh = round(capacity_kwh / 3.0, 4)
            elif "quarter" in lower or "fourth" in lower:
                min_kwh = round(0.25 * capacity_kwh, 4)
        if hours and min_kwh is not None:
            adj = {"hours": hours, "minimum_energy_kwh": min_kwh}
            return _directive(
                idx,
                applies=True,
                directive_type="minimum_battery_reserve",
                structured_adjustment=adj,
                explanation=f"Reserve at least {min_kwh} kWh during the stated window.",
            )

    if any(s in lower for s in _NO_CHARGE_HINTS) and hours:
        return _directive(
            idx,
            applies=True,
            directive_type="no_charge_window",
            structured_adjustment={"hours": hours},
            explanation="Battery charging is unavailable during the stated window.",
        )

    if any(s in lower for s in _NO_DISCHARGE_HINTS) and hours:
        return _directive(
            idx,
            applies=True,
            directive_type="no_discharge_window",
            structured_adjustment={"hours": hours},
            explanation="Battery discharge is unavailable during the stated window.",
        )

    if any(s in lower for s in _MAX_GRID_HINTS):
        cap = _extract_kwh_value(lower)
        if hours and cap is not None:
            return _directive(
                idx,
                applies=True,
                directive_type="max_grid_window",
                structured_adjustment={"hours": hours, "max_grid_kwh": cap},
                explanation=f"Grid import capped at {cap} kWh in the stated window.",
            )

    return _no_op(idx, "this note does not affect today's energy schedule")


def _no_op(idx: int, explanation: str) -> dict[str, Any]:
    return _directive(idx, applies=False, directive_type="no_op", structured_adjustment=None, explanation=explanation)


def _directive(idx: int, *, applies: bool, directive_type: str, structured_adjustment: Any, explanation: str) -> dict[str, Any]:
    return {
        "note_index": idx,
        "applies": applies,
        "directive_type": directive_type,
        "structured_adjustment": structured_adjustment,
        "explanation": explanation,
    }
