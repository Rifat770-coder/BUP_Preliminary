"""Pydantic schemas for the request / response contract.

These mirror the canonical BUP CSE Fest 2026 Preliminary Problem Statement.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HourEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Field(ge=0)
    solar_kwh: float = Field(ge=0)
    tariff_bdt_per_kwh: float = Field(ge=0)


class BatteryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capacity_kwh: float = Field(gt=0)
    initial_energy_kwh: float = Field(ge=0)
    minimum_energy_kwh: float = Field(ge=0)
    max_charge_kwh_per_hour: float = Field(gt=0)
    max_discharge_kwh_per_hour: float = Field(gt=0)


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)
    operator_notes: List[str] = Field(min_length=1, max_length=3)
    hours: List[HourEntry] = Field(min_length=24, max_length=24)
    battery: BatteryConfig

    @field_validator("operator_notes")
    @classmethod
    def _non_empty_notes(cls, value: List[str]) -> List[str]:
        if any((not n or not n.strip()) for n in value):
            raise ValueError("operator_notes must contain non-empty strings")
        return value

    @field_validator("hours")
    @classmethod
    def _check_hours(cls, value: List[HourEntry]) -> List[HourEntry]:
        seen = {h.hour for h in value}
        if len(seen) != 24 or set(range(24)) != seen:
            raise ValueError("hours must contain exactly hours 0..23 with no duplicates")
        return value


class DirectiveAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: Optional[List[int]] = None
    factor: Optional[float] = None
    minimum_energy_kwh: Optional[float] = None
    max_grid_kwh: Optional[float] = None


class DirectiveInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_index: int = Field(ge=0)
    applies: bool
    directive_type: str
    structured_adjustment: Optional[dict] = None
    explanation: str = Field(min_length=1)


class HourlyPlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hour: int = Field(ge=0, le=23)
    grid_kwh: float = Field(ge=0)
    solar_used_kwh: float = Field(ge=0)
    battery_action: str
    battery_kwh: float = Field(ge=0)
    battery_energy_after_kwh: float = Field(ge=0)


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
