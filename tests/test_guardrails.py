"""Guardrail validator tests."""
from __future__ import annotations

from app.guardrails.validator import validate_interpretations


def _ok(payload, notes_count, capacity=10.0):
    out, errors = validate_interpretations(payload, notes_count=notes_count, battery_capacity=capacity)
    assert not errors, errors
    return out


def test_no_op_must_be_inert():
    payload = [
        {"note_index": 0, "applies": False, "directive_type": "no_op",
         "structured_adjustment": None, "explanation": "irrelevant"}
    ]
    out = _ok(payload, 1)
    assert out[0]["directive_type"] == "no_op"
    assert out[0]["structured_adjustment"] is None


def test_solar_reduction_validates_factor():
    payload = [
        {"note_index": 0, "applies": True, "directive_type": "solar_reduction",
         "structured_adjustment": {"hours": [10, 11, 12], "factor": 0.5},
         "explanation": "test"}
    ]
    out = _ok(payload, 1)
    assert out[0]["structured_adjustment"]["factor"] == 0.5


def test_solar_reduction_rejects_factor_gt_1():
    payload = [
        {"note_index": 0, "applies": True, "directive_type": "solar_reduction",
         "structured_adjustment": {"hours": [10, 11, 12], "factor": 1.5},
         "explanation": "test"}
    ]
    _, errors = validate_interpretations(payload, notes_count=1, battery_capacity=10.0)
    assert any("factor" in e for e in errors)


def test_minimum_battery_reserve_caps_at_capacity():
    payload = [
        {"note_index": 0, "applies": True, "directive_type": "minimum_battery_reserve",
         "structured_adjustment": {"hours": [10, 11, 12], "minimum_energy_kwh": 999.0},
         "explanation": "too high"}
    ]
    _, errors = validate_interpretations(payload, notes_count=1, battery_capacity=10.0)
    assert any("exceeds battery capacity" in e for e in errors)


def test_index_must_match_count():
    payload = [
        {"note_index": 0, "applies": True, "directive_type": "no_charge_window",
         "structured_adjustment": {"hours": [10, 11, 12]}, "explanation": "x"}
    ]
    _, errors = validate_interpretations(payload, notes_count=2, battery_capacity=10.0)
    assert any("expected exactly 2" in e for e in errors)


def test_unknown_directive_type_rejected():
    payload = [
        {"note_index": 0, "applies": True, "directive_type": "boost_grid",
         "structured_adjustment": {"hours": [10]}, "explanation": "x"}
    ]
    _, errors = validate_interpretations(payload, notes_count=1, battery_capacity=10.0)
    assert any("not in" in e for e in errors)


def test_hours_must_be_sorted_unique():
    payload = [
        {"note_index": 0, "applies": True, "directive_type": "no_charge_window",
         "structured_adjustment": {"hours": [12, 11]}, "explanation": "x"}
    ]
    _, errors = validate_interpretations(payload, notes_count=1, battery_capacity=10.0)
    assert any("ascending" in e for e in errors)


def test_hours_out_of_range():
    payload = [
        {"note_index": 0, "applies": True, "directive_type": "no_charge_window",
         "structured_adjustment": {"hours": [24, 25]}, "explanation": "x"}
    ]
    _, errors = validate_interpretations(payload, notes_count=1, battery_capacity=10.0)
    assert any("out of range" in e for e in errors)


def test_no_op_with_structured_adjustment_is_rejected():
    payload = [
        {"note_index": 0, "applies": False, "directive_type": "no_op",
         "structured_adjustment": {"hours": [3]}, "explanation": "x"}
    ]
    _, errors = validate_interpretations(payload, notes_count=1, battery_capacity=10.0)
    assert any("no_op structured_adjustment must be null" in e for e in errors)


def test_extra_keys_recorded_but_sanitized():
    payload = [
        {"note_index": 0, "applies": True, "directive_type": "solar_reduction",
         "structured_adjustment": {"hours": [12, 13], "factor": 0.5, "ignored": True},
         "explanation": "x"}
    ]
    out, errors = validate_interpretations(payload, notes_count=1, battery_capacity=10.0)
    assert any("ignored" in e for e in errors)
    assert "ignored" not in out[0]["structured_adjustment"]
