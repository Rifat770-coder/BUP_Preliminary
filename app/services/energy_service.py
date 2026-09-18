"""High-level orchestration that ties LLM, guardrails, optimizer and replay together."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.config import EPSILON, TOL_BDT, TOL_KWH
from app.guardrails.validator import GuardrailError
from app.llm.client import LLMError
from app.llm.interpreter import interpret_notes
from app.optimization.constraints import compile_directives
from app.optimization.optimizer import InfeasiblePlanError, OptimizedPlan, optimize
from app.schemas import (
    BatteryConfig,
    DirectiveInterpretation,
    HourEntry,
    HourlyPlanEntry,
    OptimizeRequest,
    OptimizeResponse,
)
from app.validation.replay import replay_hourly_plan


@dataclass
class ServiceError(Exception):
    status: int
    detail: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.detail


async def run_optimization(
    settings: Any,
    payload: OptimizeRequest,
) -> OptimizeResponse:
    """End-to-end optimisation pipeline.

    Raises ``ServiceError`` on any failure that should surface to the API
    client (LLM errors, invalid replays, etc.).
    """
    notes = [n for n in payload.operator_notes]
    battery = payload.battery
    hours = payload.hours
    try:
        interpretations = await interpret_notes(
            settings=settings,
            notes=notes,
            capacity_kwh=float(battery.capacity_kwh),
        )
    except LLMError as exc:
        raise ServiceError(status=500, detail=f"LLM interpretation failed: {exc}") from exc
    except GuardrailError as exc:
        raise ServiceError(status=500, detail=f"Interpretation guardrail failed: {exc}") from exc

    try:
        constraints = compile_directives(interpretations, hours, battery)
        plan = optimize(hours, battery, constraints)
    except InfeasiblePlanError as exc:
        raise ServiceError(status=500, detail=f"Optimization infeasible: {exc}") from exc

    ok, errors = replay_hourly_plan(plan, hours, battery, constraints)
    if not ok:
        # Last-resort safety net: this should be impossible by construction.
        raise ServiceError(status=500, detail=f"Schedule replay failed: {'; '.join(errors)}")

    hourly_plan = _build_hourly_entries(plan, hours, battery)
    totals = _aggregate(hourly_plan, hours)
    directive_interps = _build_directive_interpretations(interpretations)
    summary = _summarise(directive_interps, totals, hours)

    return OptimizeResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=directive_interps,
        hourly_plan=hourly_plan,
        total_grid_kwh=round(totals["total_grid"], 4),
        total_cost_bdt=round(totals["total_cost"], 4),
        peak_grid_kwh=round(totals["peak_grid"], 4),
        plan_summary=summary,
    )


def _build_hourly_entries(
    plan: OptimizedPlan,
    hours: list[HourEntry],
    battery: BatteryConfig,
) -> list[HourlyPlanEntry]:
    """Convert raw plan arrays into the response-shaped hourly entries."""
    out: list[HourlyPlanEntry] = []
    e_prev = float(battery.initial_energy_kwh)
    for t, hour in enumerate(hours):
        charge = float(plan.charge[t])
        discharge = float(plan.discharge[t])
        grid = float(plan.grid[t])
        solar_used = float(plan.solar[t])
        e_after = float(plan.e_after[t])

        # Choose canonical action: prefer discharge when both are tiny.
        if discharge > EPSILON and charge <= EPSILON:
            action = "discharge"
            battery_kwh = discharge
        elif charge > EPSILON and discharge <= EPSILON:
            action = "charge"
            battery_kwh = charge
        else:
            action = "idle"
            battery_kwh = 0.0

        out.append(
            HourlyPlanEntry(
                hour=hour.hour,
                grid_kwh=round(grid, 4),
                solar_used_kwh=round(solar_used, 4),
                battery_action=action,
                battery_kwh=round(battery_kwh, 4),
                battery_energy_after_kwh=round(e_after, 4),
            )
        )
        e_prev = e_after
    return out


def _aggregate(
    hourly_plan: list[HourlyPlanEntry],
    hours: list[HourEntry],
) -> dict[str, float]:
    tariff = [float(h.tariff_bdt_per_kwh) for h in hours]
    total_grid = 0.0
    total_cost = 0.0
    peak = 0.0
    for entry, hr in zip(hourly_plan, hours):
        total_grid += entry.grid_kwh
        total_cost += entry.grid_kwh * float(hr.tariff_bdt_per_kwh)
        peak = max(peak, entry.grid_kwh)
    return {"total_grid": total_grid, "total_cost": total_cost, "peak_grid": peak}


def _build_directive_interpretations(
    interpretations: list[dict[str, Any]],
) -> list[DirectiveInterpretation]:
    """Convert the validated dict list into Pydantic models."""
    out: list[DirectiveInterpretation] = []
    for item in interpretations:
        out.append(
            DirectiveInterpretation(
                note_index=int(item["note_index"]),
                applies=bool(item["applies"]),
                directive_type=str(item["directive_type"]),
                structured_adjustment=item.get("structured_adjustment"),
                explanation=str(item["explanation"]),
            )
        )
    return out


def _summarise(
    directives: list[DirectiveInterpretation],
    totals: dict[str, float],
    hours: list[HourEntry],
) -> str:
    applied = [d for d in directives if d.applies]
    if not applied:
        applied_text = "no operator directives applied"
    else:
        parts: list[str] = []
        for d in applied:
            adj = d.structured_adjustment or {}
            if d.directive_type == "solar_reduction":
                parts.append(
                    f"solar reduced to {float(adj.get('factor', 0))*100:.0f}% "
                    f"for {len(adj.get('hours', []))} hours"
                )
            elif d.directive_type == "minimum_battery_reserve":
                parts.append(
                    f"battery reserve held at >= {float(adj.get('minimum_energy_kwh', 0)):.2f} kWh"
                )
            elif d.directive_type == "no_charge_window":
                parts.append(
                    f"no charging for {len(adj.get('hours', []))} hours"
                )
            elif d.directive_type == "no_discharge_window":
                parts.append(
                    f"no discharging for {len(adj.get('hours', []))} hours"
                )
            elif d.directive_type == "max_grid_window":
                parts.append(
                    f"grid import capped at {float(adj.get('max_grid_kwh', 0)):.2f} kWh"
                )
        applied_text = "; ".join(parts) if parts else "no operator directives applied"
    avg_tariff = (
        sum(h.tariff_bdt_per_kwh for h in hours) / max(1, len(hours))
    )
    return (
        f"Optimised 24-hour plan; {applied_text}. "
        f"Grid usage {totals['total_grid']:.2f} kWh, "
        f"cost {totals['total_cost']:.2f} BDT, peak {totals['peak_grid']:.2f} kWh, "
        f"average tariff {avg_tariff:.2f} BDT/kWh."
    )
