"""One-shot e2e probe: POST the same scenario the frontend demo button
uses, hit a real /optimize-energy call, and print the summary block that
the dashboard displays. This proves the demo flow end-to-end."""
import json
from pathlib import Path

import httpx

client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=120.0)
assert client.get("/health").status_code == 200, "health not 200"

scenario = {
    "scenario_id": "frontend-demo-001",
    "battery": {
        "capacity_kwh": 20,
        "initial_energy_kwh": 8,
        "minimum_energy_kwh": 2,
        "max_charge_kwh_per_hour": 6,
        "max_discharge_kwh_per_hour": 6,
    },
    "hours": [
        {"hour": h, "demand_kwh": 9 + 4 * max(0, __import__("math").sin(((h - 6) / 24) * 2 * 3.14159)),
         "solar_kwh": max(0, 8 * __import__("math").sin(((h - 6) / 12) * 3.14159)) if 6 <= h <= 18 else 0,
         "tariff_bdt_per_kwh": 14.0 if 18 <= h <= 22 else 7.5}
        for h in range(24)
    ],
    "operator_notes": [
        "Solar output will be reduced by 80% from 1 PM to 3 PM.",
    ],
}

r = client.post("/optimize-energy", json=scenario)
print(f"HTTP {r.status_code}")
body = r.json()
print("scenario_id:        ", body.get("scenario_id"))
print("directives:         ", json.dumps(body.get("directive_interpretation"), indent=2))
print("total_grid_kwh:     ", body.get("total_grid_kwh"))
print("total_cost_bdt:     ", body.get("total_cost_bdt"))
print("peak_grid_kwh:      ", body.get("peak_grid_kwh"))
print("plan_summary:       ", body.get("plan_summary"))
print("hourly_plan len:    ", len(body.get("hourly_plan", [])))
print("first 3 hourly rows:", body.get("hourly_plan", [])[:3])
print("last  hourly row:   ", body.get("hourly_plan", [])[-1:])
client.close()
