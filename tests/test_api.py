"""End-to-end FastAPI tests using the in-process mock LLM provider."""
from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "mock")

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import default_battery, make_hour, make_scenario


client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_optimize_with_no_op_note_returns_plan():
    payload = make_scenario(["Student affairs is closed this Friday."], scenario_id="t-noop-api")
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario_id"] == "t-noop-api"
    assert len(body["directive_interpretation"]) == 1
    assert body["directive_interpretation"][0]["directive_type"] == "no_op"
    assert body["directive_interpretation"][0]["applies"] is False
    assert len(body["hourly_plan"]) == 24
    for entry in body["hourly_plan"]:
        assert entry["grid_kwh"] >= 0
        assert entry["battery_energy_after_kwh"] >= 0
        assert entry["battery_action"] in ("charge", "discharge", "idle")


def test_optimize_with_solar_reduction_note():
    payload = make_scenario(
        ["Cut solar panels output to 50% from 10 AM to 2 PM for inverter inspection."],
        scenario_id="t-solar",
    )
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["directive_interpretation"]) == 1
    interp = body["directive_interpretation"][0]
    assert interp["applies"] is True
    assert interp["directive_type"] == "solar_reduction"


def test_optimize_with_irrelevant_note_is_no_op():
    payload = make_scenario(
        ["Library is extending book-return deadline next week."],
        scenario_id="t-noop",
    )
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["directive_interpretation"][0]["directive_type"] == "no_op"
    assert body["directive_interpretation"][0]["applies"] is False


def test_optimize_rejects_malformed_hours():
    payload = make_scenario(["hello"], scenario_id="t-bad")
    payload["hours"] = payload["hours"][:5]
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 400


def test_optimize_rejects_extra_field():
    payload = make_scenario(["hello"], scenario_id="t-extra")
    payload["unexpected"] = True
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 400
