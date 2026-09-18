"""Independent replay of an optimized schedule.

Given an OptimizedPlan and the same inputs the optimizer saw, replay_hourly_plan
re-derives every value from scratch and verifies:
  - Energy balance per hour (demand == grid + solar_used + discharge - charge).
  - Per-hour battery bounds and reserve floors.
  - Per-hour charge/discharge rate limits.
  - No-charge / no-discharge window enforcement.
  - Solar cap (solar_used <= effective_solar).
  - Grid cap (grid <= max_grid).
  - End-of-day neutrality (e_after[23] == initial_energy).

It returns a (ok, errors) tuple. TOL_KWH is enforced per-check, TOL_BDT only
matters for aggregate comparisons done outside this module.
"""
from __future__ import annotations

from typing import Any

from app.config import TOL_KWH
from app.optimization.constraints import CompiledConstraints
from app.optimization.optimizer import OptimizedPlan
from app.schemas import BatteryConfig, HourEntry


def replay_hourly_plan(
    plan: OptimizedPlan,
    hours: list[HourEntry],
    battery: BatteryConfig,
    constraints: CompiledConstraints,
) -> tuple[bool, list[str]]:
    """Return (ok, list_of_errors). ok is True iff zero errors."""
    errors: list[str] = []
    n = len(hours)
    if n != len(plan.grid) != len(plan.solar) != len(plan.charge) != len(plan.discharge) != len(plan.e_after):
        errors.append("plan arrays do not all have the same length (24 expected)")
        return False, errors

    capacity = float(battery.capacity_kwh)
    init = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)
    max_discharge = float(battery.max_discharge_kwh_per_hour)
    demand = [float(h.demand_kwh) for h in hours]
    eff_solar = constraints.effective_solar
    no_charge = constraints.no_charge_hours
    no_discharge = constraints.no_discharge_hours
    grid_cap = constraints.grid_cap
    reserve_min = constraints.reserve_min

    e_prev = init
    for t in range(n):
        grid = float(plan.grid[t])
        solar = float(plan.solar[t])
        charge = float(plan.charge[t])
        discharge = float(plan.discharge[t])
        e_after = float(plan.e_after[t])

        # Non-negative and per-hour caps.
        for name, value, cap in (
            ("grid", grid, math.inf if grid_cap[t] == float("inf") else grid_cap[t]),
            ("charge", charge, max_charge),
            ("discharge", discharge, max_discharge),
            ("solar", solar, eff_solar[t]),
        ):
            if value < -TOL_KWH:
                errors.append(f"hour={t} {name}={value} < 0")
            if math.isfinite(cap) and value > cap + TOL_KWH:
                errors.append(f"hour={t} {name}={value} exceeds cap {cap}")

        # Battery bounds.
        if e_after < -TOL_KWH:
            errors.append(f"hour={t} battery_energy_after={e_after} < 0")
        if e_after > capacity + TOL_KWH:
            errors.append(f"hour={t} battery_energy_after={e_after} > capacity {capacity}")
        if e_after < reserve_min[t] - TOL_KWH:
            errors.append(
                f"hour={t} battery_energy_after={e_after} < reserve_min {reserve_min[t]}"
            )

        # Forbidden windows.
        if t in no_charge and charge > TOL_KWH:
            errors.append(f"hour={t} charge={charge} during no_charge_window")
        if t in no_discharge and discharge > TOL_KWH:
            errors.append(f"hour={t} discharge={discharge} during no_discharge_window")

        # Simultaneous charge and discharge.
        if charge > TOL_KWH and discharge > TOL_KWH:
            errors.append(
                f"hour={t} simultaneous charge={charge} and discharge={discharge}"
            )

        # Battery dynamics.
        expected_e = e_prev + charge - discharge
        if abs(expected_e - e_after) > TOL_KWH:
            errors.append(
                f"hour={t} battery dynamics violated: e_prev={e_prev} + charge={charge} - "
                f"discharge={discharge} = {expected_e} but e_after={e_after}"
            )

        # Energy balance.
        supplied = grid + solar + discharge - charge
        if abs(supplied - demand[t]) > TOL_KWH:
            errors.append(
                f"hour={t} energy balance violated: demand={demand[t]} supplied={supplied}"
            )

        e_prev = e_after

    # End-of-day neutrality.
    if abs(e_prev - init) > TOL_KWH:
        errors.append(f"end-of-day battery energy {e_prev} != initial {init}")

    return (len(errors) == 0), errors


import math  # placed after function body is fine since Python reads top-down at call time
