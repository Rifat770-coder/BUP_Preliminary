"""Runs the official public sample cases through the API and validates them."""
from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "mock")

import math

from fastapi.testclient import TestClient

from app.config import TOL_BDT, TOL_KWH
from app.main import app
from app.optimization.constraints import compile_directives
from app.optimization.optimizer import optimize
from app.schemas import BatteryConfig, HourEntry
from app.validation.replay import replay_hourly_plan


client = TestClient(app)


def _to_request_body(case: dict) -> dict:
    inp = case.get("input") or case
    return {
        "scenario_id": inp.get("scenario_id") or case.get("id") or "public-sample",
        "battery": inp["battery"],
        "hours": inp["hours"],
        "operator_notes": inp.get("operator_notes", []),
    }


def _expected_cost(case: dict) -> float | None:
    exp = case.get("expected_output") or {}
    for k in ("total_cost_bdt", "expected_total_cost_bdt", "expected_cost_bdt"):
        if k in exp:
            try:
                return float(exp[k])
            except (TypeError, ValueError):
                return None
    return None


def _replay(body: dict) -> tuple[bool, list[str]]:
    hours = [HourEntry(**h) for h in body["hours"]]
    battery = BatteryConfig(**body["battery"])
    interpretations = [
        {"note_index": d["note_index"], "applies": d["applies"], "directive_type": d["directive_type"],
         "structured_adjustment": d.get("structured_adjustment"),
         "explanation": d["explanation"]}
        for d in body["directive_interpretation"]
    ]
    constraints = compile_directives(interpretations, hours, battery)
    plan = optimize(hours, battery, constraints)
    return replay_hourly_plan(plan, hours, battery, constraints)


def _recalc_cost(hourly: list[dict], hours: list[dict]) -> float:
    tariff_by_hour = {h["hour"]: h["tariff_bdt_per_kwh"] for h in hours}
    return sum(entry["grid_kwh"] * tariff_by_hour[entry["hour"]] for entry in hourly)


def test_optimize_energy_for_each_public_sample(public_sample_cases):
    if not public_sample_cases:
        pytest.skip("no public sample cases file present")
    for case in public_sample_cases:
        body = _to_request_body(case)
        r = client.post("/optimize-energy", json=body)
        assert r.status_code == 200, r.text
        response = r.json()
        assert len(response["directive_interpretation"]) == len(body["operator_notes"]), (
            f"scenario={body['scenario_id']} mismatch"
        )
        assert len(response["hourly_plan"]) == 24
        ok, errors = _replay({**body, "directive_interpretation": response["directive_interpretation"]})
        assert ok, f"replay failed for {body['scenario_id']}: {errors}"
        expected = _expected_cost(case)
        if expected is not None:
            assert math.isclose(float(expected), response["total_cost_bdt"], abs_tol=TOL_BDT), (
                f"cost mismatch scenario={body['scenario_id']}"
            )
        # Independent cost recompute must match reported total_cost_bdt.
        recomputed = _recalc_cost(response["hourly_plan"], body["hours"])
        assert math.isclose(recomputed, response["total_cost_bdt"], abs_tol=TOL_BDT + TOL_KWH * 100), (
            f"recomputed cost mismatch scenario={body['scenario_id']}"
        )
