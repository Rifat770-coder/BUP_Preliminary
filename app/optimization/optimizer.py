"""Linear-programming optimizer that minimizes grid cost over a 24-hour horizon.

Decision variables per hour `h`:
    grid[h]       - kWh drawn from the grid (>= 0)
    solar[h]      - kWh of solar used (>= 0, <= effective_solar[h])
    charge[h]     - kWh charged into the battery (>= 0)
    discharge[h]  - kWh discharged from the battery (>= 0)
    e_after[h]    - Battery state of energy AFTER hour h, in kWh.

Energy balance (equality):
    grid[h] + solar[h] + discharge[h] - charge[h] == demand[h]

Battery dynamics (equalities, written via linked rows):
    For h == 0: e_after[0] == initial_energy + charge[0] - discharge[0]
    For h >= 1: e_after[h] == e_after[h-1] + charge[h] - discharge[h]

Battery bounds (inequalities):
    0 <= e_after[h] <= capacity_kwh
    reserve_min[h] <= e_after[h]               (per-hour directive)
    0 <= charge[h] <= max_charge_kwh
    0 <= discharge[h] <= max_discharge_kwh
    charge[h] == 0 for h in no_charge_hours
    discharge[h] == 0 for h in no_discharge_hours

Grid caps (inequalities):
    grid[h] <= grid_cap[h]

End-of-day neutrality (equality):
    e_after[23] == initial_energy_kwh
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from scipy.optimize import linprog

from app.config import EPSILON
from app.optimization.constraints import CompiledConstraints
from app.schemas import BatteryConfig, HourEntry


@dataclass
class OptimizedPlan:
    """Raw output of the optimizer (before canonicalization)."""

    grid: list[float] = field(default_factory=list)
    solar: list[float] = field(default_factory=list)
    charge: list[float] = field(default_factory=list)
    discharge: list[float] = field(default_factory=list)
    e_after: list[float] = field(default_factory=list)
    status: str = "ok"
    objective: float = 0.0


class InfeasiblePlanError(RuntimeError):
    """Raised when the LP cannot find a feasible schedule."""


def optimize(
    hours: list[HourEntry],
    battery: BatteryConfig,
    constraints: CompiledConstraints,
) -> OptimizedPlan:
    """Run the LP and return the raw per-hour schedule."""
    n = len(hours)
    if n != 24:
        raise InfeasiblePlanError(f"expected 24 hours, got {n}")

    # Variable layout (5*n total)
    # idx 0..n-1          = grid
    # idx n..2n-1         = solar_used
    # idx 2n..3n-1        = charge
    # idx 3n..4n-1        = discharge
    # idx 4n..5n-1        = e_after
    G_OFF = 0
    S_OFF = n
    C_OFF = 2 * n
    D_OFF = 3 * n
    E_OFF = 4 * n
    NVARS = 5 * n

    demand = [float(h.demand_kwh) for h in hours]
    tariff = [float(h.tariff_bdt_per_kwh) for h in hours]
    eff_solar = constraints.effective_solar

    # linprog rejects inf in b_ub, so substitute a large finite sentinel.
    BIG_GRID_CAP = 1.0e9

    # Objective: minimise sum_t grid[t] * tariff[t]
    c_obj = [0.0] * NVARS
    for t in range(n):
        c_obj[G_OFF + t] = float(tariff[t])

    # Inequality constraints: A_ub x <= b_ub
    A_ub_rows: list[list[float]] = []
    b_ub: list[float] = []

    # 1) e_after[h] <= capacity_kwh
    for t in range(n):
        row = [0.0] * NVARS
        row[E_OFF + t] = 1.0
        A_ub_rows.append(row)
        b_ub.append(float(battery.capacity_kwh))

    # 2) -e_after[h] <= -reserve_min[h]
    for t in range(n):
        row = [0.0] * NVARS
        row[E_OFF + t] = -1.0
        A_ub_rows.append(row)
        b_ub.append(-float(constraints.reserve_min[t]))

    # 3) grid[h] <= grid_cap[h]  (use a large finite sentinel when "no cap")
    for t in range(n):
        row = [0.0] * NVARS
        row[G_OFF + t] = 1.0
        A_ub_rows.append(row)
        cap = constraints.grid_cap[t]
        b_ub.append(BIG_GRID_CAP if cap == float("inf") else float(cap))

    # 4) charge[h] <= max_charge (per-hour)
    for t in range(n):
        row = [0.0] * NVARS
        row[C_OFF + t] = 1.0
        A_ub_rows.append(row)
        b_ub.append(float(battery.max_charge_kwh_per_hour))

    # 5) discharge[h] <= max_discharge
    for t in range(n):
        row = [0.0] * NVARS
        row[D_OFF + t] = 1.0
        A_ub_rows.append(row)
        b_ub.append(float(battery.max_discharge_kwh_per_hour))

    # 6) solar[h] <= effective_solar[h]
    for t in range(n):
        row = [0.0] * NVARS
        row[S_OFF + t] = 1.0
        A_ub_rows.append(row)
        b_ub.append(float(eff_solar[t]))

    A_ub = A_ub_rows

    # Equality constraints: A_eq x == b_eq
    A_eq_rows: list[list[float]] = []
    b_eq: list[float] = []

    for t in range(n):
        # Energy balance: grid + solar + discharge - charge == demand
        row = [0.0] * NVARS
        row[G_OFF + t] = 1.0
        row[S_OFF + t] = 1.0
        row[D_OFF + t] = 1.0
        row[C_OFF + t] = -1.0
        A_eq_rows.append(row)
        b_eq.append(demand[t])

    # Battery dynamics.
    # h = 0: e_after[0] - charge[0] + discharge[0] == initial
    row = [0.0] * NVARS
    row[E_OFF + 0] = 1.0
    row[C_OFF + 0] = -1.0
    row[D_OFF + 0] = 1.0
    A_eq_rows.append(row)
    b_eq.append(float(battery.initial_energy_kwh))

    for t in range(1, n):
        # e_after[t] - e_after[t-1] - charge[t] + discharge[t] == 0
        row = [0.0] * NVARS
        row[E_OFF + t] = 1.0
        row[E_OFF + (t - 1)] = -1.0
        row[C_OFF + t] = -1.0
        row[D_OFF + t] = 1.0
        A_eq_rows.append(row)
        b_eq.append(0.0)

    # End-of-day neutrality.
    row = [0.0] * NVARS
    row[E_OFF + (n - 1)] = 1.0
    A_eq_rows.append(row)
    b_eq.append(float(battery.initial_energy_kwh))

    # Variable bounds.
    bounds = [(0.0, None)] * NVARS
    for t in range(n):
        # charge bounds may be zeroed by no_charge_hours
        if t in constraints.no_charge_hours:
            bounds[C_OFF + t] = (0.0, 0.0)
        else:
            bounds[C_OFF + t] = (0.0, float(battery.max_charge_kwh_per_hour))
        if t in constraints.no_discharge_hours:
            bounds[D_OFF + t] = (0.0, 0.0)
        else:
            bounds[D_OFF + t] = (0.0, float(battery.max_discharge_kwh_per_hour))
        bounds[E_OFF + t] = (
            max(0.0, float(constraints.reserve_min[t])),
            float(battery.capacity_kwh),
        )

    result = linprog(
        c=c_obj,
        A_ub=A_ub,
        b_ub=b_ub,
        A_eq=A_eq_rows,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
        options={"presolve": True, "time_limit": 15},
    )

    if not result.success or result.x is None:
        raise InfeasiblePlanError(
            f"linear program did not converge: status={result.status} message={result.message}"
        )

    x = result.x
    grid = [max(0.0, float(x[G_OFF + t])) for t in range(n)]
    solar = [max(0.0, min(eff_solar[t], float(x[S_OFF + t]))) for t in range(n)]
    charge = [max(0.0, float(x[C_OFF + t])) for t in range(n)]
    discharge = [max(0.0, float(x[D_OFF + t])) for t in range(n)]
    e_after = [max(0.0, min(float(battery.capacity_kwh), float(x[E_OFF + t]))) for t in range(n)]

    # Post-process to avoid simultaneous charge & discharge (LP can leave
    # both tiny due to solver tolerance).
    grid, solar, charge, discharge, e_after = _canonicalize(
        grid, solar, charge, discharge, e_after, battery
    )

    return OptimizedPlan(
        grid=grid,
        solar=solar,
        charge=charge,
        discharge=discharge,
        e_after=e_after,
        status=str(result.status),
        objective=float(result.fun or 0.0),
    )


def _canonicalize(
    grid: list[float],
    solar: list[float],
    charge: list[float],
    discharge: list[float],
    e_after: list[float],
    battery: BatteryConfig,
) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    """Eliminate simultaneous charge/discharge and round small residuals."""
    n = len(grid)
    capacity = float(battery.capacity_kwh)
    init = float(battery.initial_energy_kwh)
    for t in range(n):
        c, d = charge[t], discharge[t]
        if c > EPSILON and d > EPSILON:
            # Keep the one that preserves the energy balance of e_after.
            if c >= d:
                d = 0.0
                c = max(0.0, c - d)
            else:
                c = 0.0
                d = max(0.0, d - c)
            charge[t], discharge[t] = c, d
        # Snap tiny values to 0.
        if charge[t] < EPSILON:
            charge[t] = 0.0
        if discharge[t] < EPSILON:
            discharge[t] = 0.0
        if solar[t] < EPSILON:
            solar[t] = 0.0
        if grid[t] < EPSILON:
            grid[t] = 0.0
    # Force exact neutrality at end of day.
    e_after[n - 1] = init
    # Round.
    grid = [round(v, 6) for v in grid]
    solar = [round(v, 6) for v in solar]
    charge = [round(v, 6) for v in charge]
    discharge = [round(v, 6) for v in discharge]
    e_after = [round(min(capacity, max(0.0, v)), 6) for v in e_after]
    return grid, solar, charge, discharge, e_after
