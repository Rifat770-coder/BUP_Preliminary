"""Deterministic guardrails for LLM interpretation output.

The guardrails module is the single source of truth for what constitutes a
valid interpretation payload. It enforces structural invariants (one entry
per note, ordered by note_index), enum membership (only the six supported
directive types), numeric range (factor in [0,1], reserve kwh in [0,capacity]),
and semantics (applies flag must agree with directive_type, structured_adjustment
must be present when applies=True and null otherwise).

A validator function returns a (validated, errors) tuple so callers can either
pass through cleanly or surface validation issues back to the LLM via a
repair prompt.
"""
from __future__ import annotations

import math
from typing import Any

from app.config import ALLOWED_DIRECTIVE_TYPES


class GuardrailError(ValueError):
    """Raised when the validator cannot recover from structural problems."""


_REQUIRED_ADJUSTMENT_KEYS = {
    "solar_reduction": {"hours", "factor"},
    "minimum_battery_reserve": {"hours", "minimum_energy_kwh"},
    "no_charge_window": {"hours"},
    "no_discharge_window": {"hours"},
    "max_grid_window": {"hours", "max_grid_kwh"},
}


def validate_interpretations(
    interpretations: Any,
    *,
    notes_count: int,
    battery_capacity: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate the LLM payload and return (validated_list, errors)."""
    errors: list[str] = []
    if not isinstance(interpretations, list):
        raise GuardrailError("interpretations must be a JSON array")

    if len(interpretations) != notes_count:
        errors.append(
            f"expected exactly {notes_count} interpretations, got {len(interpretations)}"
        )

    out: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()
    notes_with_structural_problems = False

    for entry in interpretations:
        try:
            normalized = _normalize_entry(entry, battery_capacity, errors)
        except GuardrailError as exc:
            errors.append(str(exc))
            notes_with_structural_problems = True
            continue
        if normalized is None:
            continue
        idx = normalized["note_index"]
        if idx in seen_indexes:
            errors.append(f"duplicate note_index {idx}")
        seen_indexes.add(idx)
        out.append(normalized)

    # Preserve note_index ordering.
    out.sort(key=lambda x: x["note_index"])

    # Ensure each note index from 0..notes_count-1 is present.
    missing = [i for i in range(notes_count) if i not in seen_indexes]
    if missing:
        errors.append(f"missing note_index entries: {missing}")

    if notes_with_structural_problems:
        # If any entry was structurally broken we cannot trust the ordering.
        # Raise so the orchestrator can fall back.
        if not out:
            raise GuardrailError("no usable interpretations: " + "; ".join(errors))

    return out, errors


def _normalize_entry(
    entry: Any,
    battery_capacity: float,
    errors: list[str],
) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        errors.append("interpretation entry must be an object")
        return None

    if "note_index" not in entry:
        errors.append("interpretation missing note_index")
        return None
    try:
        idx = int(entry["note_index"])
    except (TypeError, ValueError):
        errors.append(f"note_index must be an integer, got {entry['note_index']!r}")
        return None

    applies_raw = entry.get("applies")
    if not isinstance(applies_raw, bool):
        errors.append(f"note_index={idx} applies must be a boolean, got {applies_raw!r}")
        return None

    directive_type = entry.get("directive_type")
    if not isinstance(directive_type, str):
        errors.append(f"note_index={idx} directive_type must be a string")
        return None
    if directive_type not in ALLOWED_DIRECTIVE_TYPES:
        errors.append(
            f"note_index={idx} directive_type {directive_type!r} not in {sorted(ALLOWED_DIRECTIVE_TYPES)}"
        )
        return None

    if directive_type == "no_op":
        if applies_raw is True:
            errors.append(f"note_index={idx} no_op cannot have applies=true")
            return None
        adjustment = entry.get("structured_adjustment")
        if adjustment is not None:
            errors.append(f"note_index={idx} no_op structured_adjustment must be null")
            return None
        explanation = entry.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            errors.append(f"note_index={idx} no_op explanation must be non-empty string")
            return None
        return {
            "note_index": idx,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": explanation.strip(),
        }

    if applies_raw is False:
        errors.append(
            f"note_index={idx} directive_type={directive_type} must have applies=true"
        )
        return None

    adjustment = entry.get("structured_adjustment")
    if not isinstance(adjustment, dict):
        errors.append(f"note_index={idx} structured_adjustment must be an object")
        return None

    required_keys = _REQUIRED_ADJUSTMENT_KEYS.get(directive_type, set())
    extra_keys = set(adjustment.keys()) - required_keys
    missing_keys = required_keys - set(adjustment.keys())
    if missing_keys:
        errors.append(
            f"note_index={idx} {directive_type} missing keys {sorted(missing_keys)}"
        )
        return None
    if extra_keys:
        # Unknown keys are tolerated but warned in errors so we don't loop.
        errors.append(
            f"note_index={idx} {directive_type} has extra keys {sorted(extra_keys)} (will be ignored)"
        )

    hours = _validate_hours(idx, adjustment.get("hours"), errors)
    if hours is None:
        return None

    extra: dict[str, Any] = {}
    if directive_type == "solar_reduction":
        factor = _validate_factor(idx, adjustment.get("factor"), errors)
        if factor is None:
            return None
        extra["factor"] = factor
    elif directive_type == "minimum_battery_reserve":
        reserve = _validate_reserve(idx, adjustment.get("minimum_energy_kwh"), battery_capacity, errors)
        if reserve is None:
            return None
        extra["minimum_energy_kwh"] = reserve
    elif directive_type == "max_grid_window":
        cap = _validate_grid_cap(idx, adjustment.get("max_grid_kwh"), errors)
        if cap is None:
            return None
        extra["max_grid_kwh"] = cap

    explanation = entry.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        explanation = f"Operator note {idx} applied as {directive_type}."

    return {
        "note_index": idx,
        "applies": True,
        "directive_type": directive_type,
        "structured_adjustment": {"hours": hours, **extra},
        "explanation": explanation.strip(),
    }


def _validate_hours(idx: int, value: Any, errors: list[str]) -> list[int] | None:
    if not isinstance(value, list):
        errors.append(f"note_index={idx} hours must be a list")
        return None
    if len(value) != len(set(value)):
        errors.append(f"note_index={idx} hours must be unique")
        return None
    if value != sorted(value):
        errors.append(f"note_index={idx} hours must be ascending")
        return None
    out: list[int] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, int):
            errors.append(f"note_index={idx} hour {v!r} must be an integer")
            return None
        if not (0 <= v <= 23):
            errors.append(f"note_index={idx} hour {v!r} out of range 0..23")
            return None
        out.append(int(v))
    if not out:
        errors.append(f"note_index={idx} hours must be non-empty")
        return None
    return out


def _validate_factor(idx: int, value: Any, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"note_index={idx} factor must be a number")
        return None
    if math.isnan(value) or math.isinf(value):
        errors.append(f"note_index={idx} factor must be finite")
        return None
    if not (0.0 <= float(value) <= 1.0):
        errors.append(f"note_index={idx} factor {value} out of [0,1]")
        return None
    return float(value)


def _validate_reserve(idx: int, value: Any, capacity: float, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"note_index={idx} minimum_energy_kwh must be a number")
        return None
    if math.isnan(value) or math.isinf(value):
        errors.append(f"note_index={idx} minimum_energy_kwh must be finite")
        return None
    val = float(value)
    if val < 0:
        errors.append(f"note_index={idx} minimum_energy_kwh {val} < 0")
        return None
    if val > capacity + 1e-9:
        errors.append(
            f"note_index={idx} minimum_energy_kwh {val} exceeds battery capacity {capacity}"
        )
        return None
    return round(val, 6)


def _validate_grid_cap(idx: int, value: Any, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"note_index={idx} max_grid_kwh must be a number")
        return None
    if math.isnan(value) or math.isinf(value):
        errors.append(f"note_index={idx} max_grid_kwh must be finite")
        return None
    val = float(value)
    if val < 0:
        errors.append(f"note_index={idx} max_grid_kwh {val} < 0")
        return None
    return round(val, 6)
