"""Optimizer tests."""
from __future__ import annotations

import math

from app.optimization.constraints import compile_directives
from app.optimization.optimizer import InfeasiblePlanError, optimize
from app.schemas import BatteryConfig, HourEntry
from tests.conftest import default_battery, make_hour


def _scenario() -> tuple[list[HourEntry], BatteryConfig]:
    hours = [
        HourEntry(hour=h, demand_kwh=10.0, solar_kwh=4.0 if 8 <= h <= 16 else 0.0, tariff_bdt_per_kwh=8.0 + (h % 6))
        for h in range(24)
    ]
    return hours, BatteryConfig(**default_battery())


def test_optimize_basic_schedule_is_feasible():
    hours, battery = _scenario()
    plan = optimize(hours, battery, compile_directives([], hours, battery))
    assert len(plan.grid) == 24
    # Demand is constant at 10 kWh; total grid should be near demand minus
    # solar contributions. Check balance numerically.
    for t in range(24):
        supply = plan.grid[t] + plan.solar[t] + plan.discharge[t] - plan.charge[t]
        assert math.isclose(supply, 10.0, abs_tol=1e-4)
    # End-of-day neutrality.
    assert math.isclose(plan.e_after[23], battery.initial_energy_kwh, abs_tol=1e-4)


def test_optimize_respects_no_charge_window():
    hours, battery = _scenario()
    interp = [{
        "note_index": 0, "applies": True, "directive_type": "no_charge_window",
        "structured_adjustment": {"hours": list(range(0, 6))},
        "explanation": "x",
    }]
    plan = optimize(hours, battery, compile_directives(interp, hours, battery))
    for t in range(0, 6):
        assert plan.charge[t] == 0.0


def test_optimize_respects_no_discharge_window():
    hours, battery = _scenario()
    interp = [{
        "note_index": 0, "applies": True, "directive_type": "no_discharge_window",
        "structured_adjustment": {"hours": list(range(18, 24))},
        "explanation": "x",
    }]
    plan = optimize(hours, battery, compile_directives(interp, hours, battery))
    for t in range(18, 24):
        assert plan.discharge[t] == 0.0


def test_optimize_min_reserve_floors_state():
    hours, battery = _scenario()
    interp = [{
        "note_index": 0, "applies": True, "directive_type": "minimum_battery_reserve",
        "structured_adjustment": {"hours": [12, 13, 14], "minimum_energy_kwh": 7.0},
        "explanation": "x",
    }]
    plan = optimize(hours, battery, compile_directives(interp, hours, battery))
    for t in (12, 13, 14):
        assert plan.e_after[t] >= 7.0 - 1e-4


def test_optimize_grid_cap_enforced():
    hours, battery = _scenario()
    interp = [{
        "note_index": 0, "applies": True, "directive_type": "max_grid_window",
        "structured_adjustment": {"hours": list(range(12, 18)), "max_grid_kwh": 6.0},
        "explanation": "x",
    }]
    plan = optimize(hours, battery, compile_directives(interp, hours, battery))
    for t in range(12, 18):
        assert plan.grid[t] <= 6.0 + 1e-4


def test_optimize_solar_reduction_lowers_solar_used():
    hours, battery = _scenario()
    interp = [{
        "note_index": 0, "applies": True, "directive_type": "solar_reduction",
        "structured_adjustment": {"hours": [10, 11, 12], "factor": 0.2},
        "explanation": "x",
    }]
    plan = optimize(hours, battery, compile_directives(interp, hours, battery))
    for t in (10, 11, 12):
        assert plan.solar[t] <= 4.0 * 0.2 + 1e-4


def test_optimize_end_of_day_neutrality():
    hours, battery = _scenario()
    plan = optimize(hours, battery, compile_directives([], hours, battery))
    assert math.isclose(plan.e_after[23], battery.initial_energy_kwh, abs_tol=1e-4)
