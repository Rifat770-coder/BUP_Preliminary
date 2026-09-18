"""Pytest fixtures and helpers shared across the test suite."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Ensure deterministic offline behaviour even when a real API key is set.
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("LLM_API_KEY", "")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def public_sample_cases(repo_root: Path) -> list[dict]:
    """Load the official public sample cases from the repo.

    Returns a list of {"input": ..., "expected_output": ..., ...} dicts.
    """
    candidate = repo_root / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
    if not candidate.exists():
        return []
    import json

    with candidate.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "cases" in data and isinstance(data["cases"], list):
        return data["cases"]
    if isinstance(data, list):
        return data
    return []


def make_hour(hour: int, demand: float, solar: float, tariff: float) -> dict:
    return {"hour": hour, "demand_kwh": demand, "solar_kwh": solar, "tariff_bdt_per_kwh": tariff}


def default_battery() -> dict:
    return {
        "capacity_kwh": 10.0,
        "initial_energy_kwh": 5.0,
        "minimum_energy_kwh": 1.0,
        "max_charge_kwh_per_hour": 4.0,
        "max_discharge_kwh_per_hour": 4.0,
    }


def make_scenario(notes: list[str], *, hours: list[dict] | None = None, scenario_id: str = "t") -> dict:
    if hours is None:
        hours = [make_hour(h, demand=10.0, solar=4.0 if 8 <= h <= 16 else 0.0, tariff=8.0 + (h % 6)) for h in range(24)]
    return {
        "scenario_id": scenario_id,
        "battery": default_battery(),
        "hours": hours,
        "operator_notes": notes,
    }
