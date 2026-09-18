"""Targeted paraphrase probe against the real LLM server.

Patterns (verbatim from the user's checklist):
  - "80% reduction"        -> factor 0.2
  - "reduced to 80%"       -> factor 0.8
  - "1 PM to 3 PM"         -> hours [13, 14]
  - irrelevant note        -> applies=false, directive_type=no_op, structured_adjustment=null
"""
import json
import time
import httpx

BASE = "http://127.0.0.1:8000"


def minimal_hours() -> list[dict]:
    return [
        {
            "hour": h,
            "demand_kwh": 100.0,
            "solar_kwh": 0.0 if h < 6 or h > 18 else 50.0,
            "tariff_bdt_per_kwh": 10.0,
        }
        for h in range(24)
    ]


def battery() -> dict:
    return {
        "capacity_kwh": 100.0,
        "initial_energy_kwh": 50.0,
        "minimum_energy_kwh": 0.0,
        "max_charge_kwh_per_hour": 50.0,
        "max_discharge_kwh_per_hour": 50.0,
    }


def post(note: str) -> dict:
    body = {
        "scenario_id": "t-para",
        "battery": battery(),
        "hours": minimal_hours(),
        "operator_notes": [note],
    }
    client = httpx.Client(base_url=BASE, timeout=60.0)
    try:
        t = time.perf_counter()
        r = client.post("/optimize-energy", json=body)
        dt = (time.perf_counter() - t) * 1000
        if r.status_code != 200:
            return {"status": r.status_code, "error": r.text[:200], "elapsed_ms": dt}
        j = r.json()
        di = j["directive_interpretation"][0]
        return {
            "status": 200,
            "applies": di["applies"],
            "directive_type": di["directive_type"],
            "adjustment": di["structured_adjustment"],
            "elapsed_ms": dt,
        }
    finally:
        client.close()


def main() -> None:
    r = httpx.get(f"{BASE}/health", timeout=10.0)
    assert r.status_code == 200 and r.json().get("status") == "ok", "health"

    tests = [
        (
            "80% reduction",
            "Reduce solar output by 80% from noon to 2 PM for maintenance.",
            {"directive_type": "solar_reduction", "factor": 0.2, "hours": [12, 13]},
        ),
        (
            "reduced to 80%",
            "Solar output should be reduced to 80% between 1 PM and 3 PM today.",
            {"directive_type": "solar_reduction", "factor": 0.8, "hours": [13, 14]},
        ),
        (
            "1 PM to 3 PM window (no_charge)",
            "Apply no-charge window from 1 PM to 3 PM for the sports hall.",
            {"directive_type": "no_charge_window", "hours": [13, 14]},
        ),
        (
            "irrelevant note",
            "The cafeteria is changing its menu next week.",
            {"directive_type": "no_op", "applies": False, "structured_adjustment": None},
        ),
    ]

    pass_count = 0
    for name, note, expected in tests:
        result = post(note)
        print(f"\n[{name}] note: {note}")
        print(f"  HTTP {result['status']} in {result['elapsed_ms']:.0f} ms")
        if result["status"] != 200:
            print(f"  ERR: {result['error']}")
            continue

        adj = result["adjustment"]
        actual = {
            "directive_type": result["directive_type"],
            "factor": (adj or {}).get("factor"),
            "hours": (adj or {}).get("hours"),
            "applies": result["applies"],
            "structured_adjustment": adj,
        }

        # Compare against expectation.
        ok = True
        if "directive_type" in expected and actual["directive_type"] != expected["directive_type"]:
            ok = False
        if "factor" in expected and abs((actual["factor"] or -1) - expected["factor"]) > 0.05:
            ok = False
        if "hours" in expected and actual["hours"] != expected["hours"]:
            ok = False
        if "applies" in expected and actual["applies"] != expected["applies"]:
            ok = False
        if "structured_adjustment" in expected and (
            (expected["structured_adjustment"] is None) != (adj is None)
        ):
            ok = False

        verdict = "PASS" if ok else "FAIL"
        print(f"  -> {verdict} | applies={actual['applies']} type={actual['directive_type']} adj={actual['structured_adjustment']}")
        if ok:
            pass_count += 1

    print(f"\nParaphrase results: {pass_count}/{len(tests)} pass")


if __name__ == "__main__":
    main()
