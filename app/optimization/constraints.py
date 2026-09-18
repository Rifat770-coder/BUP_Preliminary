"""Translate validated interpretations into constraints the optimizer consumes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import EPSILON
from app.schemas import BatteryConfig, HourEntry


@dataclass
class CompiledConstraints:
    """Aggregated constraints the optimizer must satisfy.

    Attributes
    ----------
    effective_solar: list[float]
        Per-hour usable solar after applying solar_reduction directives.
    no_charge_hours: set[int]
        Hours during which the battery may not be charged (0..23).
    no_discharge_hours: set[int]
        Hours during which the battery may not be discharged (0..23).
    grid_cap: list[float]
        Per-hour maximum grid import (kWh). Defaults to +inf; restricted
        by max_grid_window directives.
    reserve_min: list[float]
        Per-hour minimum battery energy AFTER the hour (kWh). Defaults to
        battery.minimum_energy_kwh. Higher values override lower ones.
    """

    effective_solar: list[float] = field(default_factory=list)
    no_charge_hours: set[int] = field(default_factory=set)
    no_discharge_hours: set[int] = field(default_factory=set)
    grid_cap: list[float] = field(default_factory=list)
    reserve_min: list[float] = field(default_factory=list)


def compile_directives(
    interpretations: list[dict[str, Any]],
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> CompiledConstraints:
    """Convert each interpretation into one or more per-hour constraints."""
    n = len(hours)
    out = CompiledConstraints(
        effective_solar=[h.solar_kwh for h in hours],
        no_charge_hours=set(),
        no_discharge_hours=set(),
        grid_cap=[float("inf")] * n,
        reserve_min=[float(battery.minimum_energy_kwh)] * n,
    )

    for interp in interpretations:
        if not interp.get("applies", False):
            continue
        dtype = interp["directive_type"]
        adj = interp.get("structured_adjustment") or {}
        h_list = [int(h) for h in adj.get("hours", [])]
        if not h_list:
            continue
        if dtype == "solar_reduction":
            factor = float(adj.get("factor", 0.0))
            factor = max(0.0, min(1.0, factor))
            for h in h_list:
                if 0 <= h < n:
                    out.effective_solar[h] = round(
                        out.effective_solar[h] * factor, 6
                    )
        elif dtype == "minimum_battery_reserve":
            target = float(adj.get("minimum_energy_kwh", battery.minimum_energy_kwh))
            target = max(target, float(battery.minimum_energy_kwh))
            target = min(target, float(battery.capacity_kwh))
            for h in h_list:
                if 0 <= h < n:
                    out.reserve_min[h] = max(out.reserve_min[h], target)
        elif dtype == "no_charge_window":
            for h in h_list:
                if 0 <= h < n:
                    out.no_charge_hours.add(h)
        elif dtype == "no_discharge_window":
            for h in h_list:
                if 0 <= h < n:
                    out.no_discharge_hours.add(h)
        elif dtype == "max_grid_window":
            cap = float(adj.get("max_grid_kwh", 0.0))
            for h in h_list:
                if 0 <= h < n:
                    out.grid_cap[h] = min(out.grid_cap[h], cap)
        else:  # pragma: no cover - guardrails prevent this branch
            continue

    # Clamp effective solar so the optimizer never sees negative values.
    out.effective_solar = [max(0.0, round(v, 6)) for v in out.effective_solar]

    # Round reserve min and grid cap.
    out.reserve_min = [round(v, 6) for v in out.reserve_min]
    out.grid_cap = [
        round(v, 6) if v != float("inf") else float("inf") for v in out.grid_cap
    ]

    return out
