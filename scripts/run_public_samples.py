"""Run every official public sample case through the running API and verify.

Usage:
    python -m scripts.run_public_samples [--base-url URL]

If the server is not already running, this script will spawn uvicorn on port
8000 in a subprocess, wait until /health responds, then run all cases. After
the run, the subprocess is terminated.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_healthy(base_url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def _start_server(port: int) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.setdefault("LLM_PROVIDER", "mock")
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    return subprocess.Popen(cmd, cwd=str(ROOT), env=env)


def load_cases(path: Path) -> list[dict[str, Any]]:
    """Return the list of case dictionaries.

    The official file wraps cases under {"_meta": {...}, "cases": [...]}.
    Each case has keys {id, label, input, expected_output, rationale}.
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        if "cases" in data and isinstance(data["cases"], list):
            return data["cases"]
        for v in data.values():
            if isinstance(v, list):
                return v
        return []
    if isinstance(data, list):
        return data
    return []


def expected_cost(case: dict[str, Any]) -> float | None:
    exp = case.get("expected_output") or {}
    for k in ("total_cost_bdt", "expected_total_cost_bdt", "expected_cost_bdt"):
        if k in exp:
            try:
                return float(exp[k])
            except (TypeError, ValueError):
                return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    args = parser.parse_args()

    cases_path = Path(args.cases)
    cases = load_cases(cases_path)
    if not cases:
        print(f"!! No cases loaded from {cases_path}")
        return 2

    proc: subprocess.Popen[bytes] | None = None
    if args.base_url:
        base_url = args.base_url.rstrip("/")
    else:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        proc = _start_server(port)
        if not _wait_healthy(base_url):
            print("!! Server failed to become healthy")
            if proc is not None:
                proc.terminate()
            return 2

    failures = 0
    try:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            r = client.get("/health")
            assert r.status_code == 200 and r.json().get("status") == "ok", "health check failed"

            print(f"Running {len(cases)} public sample cases against {base_url}")
            print("-" * 70)
            for idx, case in enumerate(cases):
                case_id = case.get("id") or case.get("scenario_id") or f"case-{idx}"
                inp = case.get("input") or case
                sid = inp.get("scenario_id") or case_id
                body = {
                    "scenario_id": sid,
                    "battery": inp["battery"],
                    "hours": inp["hours"],
                    "operator_notes": inp.get("operator_notes", []),
                }
                r = client.post("/optimize-energy", json=body)
                if r.status_code != 200:
                    failures += 1
                    print(f"[FAIL] {sid}: HTTP {r.status_code} body={r.text[:200]}")
                    continue
                response = r.json()

                # Structural checks.
                if len(response["directive_interpretation"]) != len(body["operator_notes"]):
                    failures += 1
                    print(f"[FAIL] {sid}: interpretation count mismatch")
                    continue
                if len(response["hourly_plan"]) != 24:
                    failures += 1
                    print(f"[FAIL] {sid}: hourly_plan length != 24")
                    continue

                # Recompute cost independently and compare.
                tariff_by_hour = {h["hour"]: h["tariff_bdt_per_kwh"] for h in body["hours"]}
                recomputed_cost = sum(
                    entry["grid_kwh"] * tariff_by_hour[entry["hour"]]
                    for entry in response["hourly_plan"]
                )
                if not math.isclose(recomputed_cost, response["total_cost_bdt"], abs_tol=1e-2):
                    failures += 1
                    print(
                        f"[FAIL] {sid}: reported cost={response['total_cost_bdt']} recomputed={recomputed_cost}"
                    )
                    continue

                expected = expected_cost(case)
                cost_msg = ""
                if expected is not None:
                    if not math.isclose(float(expected), response["total_cost_bdt"], abs_tol=1e-2):
                        failures += 1
                        cost_msg = f" (expected={expected})"
                        print(
                            f"[FAIL] {sid}: cost {response['total_cost_bdt']} != expected {expected}{cost_msg}"
                        )
                        continue
                print(
                    f"[ OK ] {sid}: notes={len(body['operator_notes'])} "
                    f"grid={response['total_grid_kwh']:.2f} kWh "
                    f"cost={response['total_cost_bdt']:.2f} BDT "
                    f"peak={response['peak_grid_kwh']:.2f} kWh{cost_msg}"
                )
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("-" * 70)
    if failures:
        print(f"!! {failures} / {len(cases)} cases FAILED")
        return 1
    print(f"All {len(cases)} public sample cases PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
