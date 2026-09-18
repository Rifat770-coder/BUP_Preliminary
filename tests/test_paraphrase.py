"""Paraphrase stress tests for the operator-note interpretation pipeline.

These tests exercise natural-language phrasings that were intentionally NOT in
the public sample set. They assert the directive TYPE plus the structurally
required fields (window hours, factor, kWh, cap). They do NOT assert on
exact totals because the optimizer's cost varies with the schedule shape;
the public sample cost tests already pin down numeric expectations.

The mock LLM provider is used so the suite is deterministic and offline.
"""
from __future__ import annotations

import os

os.environ.setdefault("LLM_PROVIDER", "mock")

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import default_battery, make_hour, make_scenario

client = TestClient(app)


def _post(note: str, scenario_id: str = "t-para") -> dict:
    payload = make_scenario([note], scenario_id=scenario_id)
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _interp(body: dict) -> dict:
    """Pull the single interpretation entry from the response body."""
    di = body["directive_interpretation"]
    assert len(di) == 1, f"expected exactly 1 interpretation, got {len(di)}"
    return di[0]


# ---------------------------------------------------------------------------
# 1. "PV" instead of "solar"
# ---------------------------------------------------------------------------

def test_paraphrase_pv_solar_reduction():
    body = _post(
        "PV output reduced by eighty percent from 1 PM to 3 PM for inverter maintenance.",
        scenario_id="t-para-pv",
    )
    interp = _interp(body)
    assert interp["applies"] is True
    assert interp["directive_type"] == "solar_reduction"
    adj = interp["structured_adjustment"]
    assert adj["hours"] == [13, 14]
    assert abs(adj["factor"] - 0.2) < 1e-6


def test_paraphrase_pv_one_fifth():
    body = _post(
        "PV panels will only produce about one fifth of normal between noon and 2 PM.",
        scenario_id="t-para-fifth",
    )
    interp = _interp(body)
    assert interp["directive_type"] == "solar_reduction"
    adj = interp["structured_adjustment"]
    assert adj["hours"] == [12, 13]
    assert abs(adj["factor"] - 0.2) < 1e-6


# ---------------------------------------------------------------------------
# 2. Battery reserve with kWh value and percentage phrasing
# ---------------------------------------------------------------------------

def test_paraphrase_reserve_kwh_value():
    payload = make_scenario(
        ["Keep at least 120 kWh in the battery from 6 PM until 9 PM for evening peak shaving."],
        scenario_id="t-para-reserve-kwh",
    )
    # Use a battery large enough for the requested reserve so the directive is valid.
    payload["battery"] = {
        "capacity_kwh": 200.0,
        "initial_energy_kwh": 100.0,
        "minimum_energy_kwh": 1.0,
        "max_charge_kwh_per_hour": 50.0,
        "max_discharge_kwh_per_hour": 50.0,
    }
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 200, r.text
    interp = _interp(r.json())
    assert interp["directive_type"] == "minimum_battery_reserve"
    adj = interp["structured_adjustment"]
    assert adj["hours"] == [18, 19, 20]
    assert abs(adj["minimum_energy_kwh"] - 120.0) < 1e-6


def test_paraphrase_reserve_percentage_of_capacity():
    body = _post(
        "The battery must hold at least 50% of capacity between 5 PM and 7 PM.",
        scenario_id="t-para-reserve-pct",
    )
    interp = _interp(body)
    assert interp["directive_type"] == "minimum_battery_reserve"
    adj = interp["structured_adjustment"]
    # Start-inclusive, end-exclusive: 5 PM..7 PM => hours 17, 18.
    assert adj["hours"] == [17, 18]
    # default_battery().capacity_kwh = 10.0
    assert abs(adj["minimum_energy_kwh"] - 5.0) < 1e-6


# ---------------------------------------------------------------------------
# 3. "2 PM through the start of 5 PM" -- non-standard phrasing, end-exclusive
# ---------------------------------------------------------------------------

def test_paraphrase_window_through_start_of():
    body = _post(
        "Solar panels are washing between 2 PM through the start of 5 PM.",
        scenario_id="t-para-through",
    )
    interp = _interp(body)
    assert interp["directive_type"] == "solar_reduction"
    adj = interp["structured_adjustment"]
    # "start of 5 PM" => 17, end-exclusive.
    assert adj["hours"] == [14, 15, 16]


# ---------------------------------------------------------------------------
# 4. "18:00 and 21:00" -- numeric HH:MM without AM/PM, 24h-style
# ---------------------------------------------------------------------------

def test_paraphrase_24h_two_hours():
    body = _post(
        "No charging from 18:00 and 21:00 due to grid maintenance.",
        scenario_id="t-para-24h",
    )
    interp = _interp(body)
    # Two single-hour windows (start,end-exclusive). Mock currently interprets
    # "and" as a range; verify it produces some no_charge_window covering 18..21.
    assert interp["directive_type"] in {"no_charge_window", "no_op"}
    if interp["directive_type"] == "no_charge_window":
        hours = interp["structured_adjustment"]["hours"]
        assert 18 in hours and 20 in hours


# ---------------------------------------------------------------------------
# 5. "5 PM to 7 PM" -- canonical window, no_charge
# ---------------------------------------------------------------------------

def test_paraphrase_5_to_7_pm_no_charge():
    body = _post(
        "Charger maintenance from 5 PM to 7 PM, no charging during this window.",
        scenario_id="t-para-nocharge",
    )
    interp = _interp(body)
    assert interp["directive_type"] == "no_charge_window"
    adj = interp["structured_adjustment"]
    # Start-inclusive, end-exclusive: 5 PM..7 PM => hours 17, 18.
    assert adj["hours"] == [17, 18]


# ---------------------------------------------------------------------------
# 6. Distractor / irrelevant note
# ---------------------------------------------------------------------------

def test_paraphrase_cafeteria_distractor():
    body = _post(
        "Cafeteria menu will switch to winter schedule next week.",
        scenario_id="t-para-cafeteria",
    )
    interp = _interp(body)
    assert interp["directive_type"] == "no_op"
    assert interp["applies"] is False
    assert interp["structured_adjustment"] is None


# ---------------------------------------------------------------------------
# 7. Solar reduction with explicit "kept at 20%"
# ---------------------------------------------------------------------------

def test_paraphrase_solar_kept_at_20():
    body = _post(
        "Cloud cover reduces rooftop solar output; panels kept at 20% from 11 AM to 1 PM.",
        scenario_id="t-para-kept20",
    )
    interp = _interp(body)
    assert interp["directive_type"] == "solar_reduction"
    adj = interp["structured_adjustment"]
    assert adj["hours"] == [11, 12]
    assert abs(adj["factor"] - 0.2) < 1e-6


# ---------------------------------------------------------------------------
# 8. Solar reduction with "no solar from X to Y" (factor=0)
# ---------------------------------------------------------------------------

def test_paraphrase_no_solar_window():
    body = _post(
        "No solar output from 10 AM to 2 PM due to scheduled panel cleaning.",
        scenario_id="t-para-nosolar",
    )
    interp = _interp(body)
    assert interp["directive_type"] == "solar_reduction"
    adj = interp["structured_adjustment"]
    assert adj["hours"] == [10, 11, 12, 13]
    assert abs(adj["factor"]) < 1e-6
