"""Real LLM latency probe. Hits /optimize-energy 2*10=20 times with the
canonical 10 public samples and computes min/max/mean/median/p95/p99.

Run after loading .env into the shell so LLM_API_KEY is available.
Writes a JSON summary to probe_latency.out.json for downstream inspection.
"""
import json, statistics, time
from pathlib import Path

import httpx

CASES_PATH = Path("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")
OUT_PATH = Path("probe_latency.out.json")
N_PASSES = 2


def main() -> int:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=120.0)
    if client.get("/health").status_code != 200:
        print("ERROR: /health did not return 200")
        return 1

    # Warm-up so the first call's cold-start does not skew stats.
    for c in cases[:1]:
        inp = c["input"]
        sid = c.get("id")
        body = {
            "scenario_id": sid,
            "battery": inp["battery"],
            "hours": inp["hours"],
            "operator_notes": inp.get("operator_notes", []),
        }
        client.post("/optimize-energy", json=body)

    ee: list[float] = []
    for _ in range(N_PASSES):
        for c in cases:
            inp = c["input"]
            sid = c.get("id")
            body = {
                "scenario_id": sid,
                "battery": inp["battery"],
                "hours": inp["hours"],
                "operator_notes": inp.get("operator_notes", []),
            }
            t = time.perf_counter()
            r = client.post("/optimize-energy", json=body)
            dt = (time.perf_counter() - t) * 1000.0
            assert r.status_code == 200, (sid, r.status_code, r.text[:200])
            ee.append(dt)
    client.close()

    ee_sorted = sorted(ee)
    n = len(ee_sorted)

    def pct(p: float) -> float:
        k = max(0, min(n - 1, int(round(p / 100 * (n - 1)))))
        return ee_sorted[k]

    summary = {
        "requests": n,
        "end_to_end_ms": {
            "min": round(min(ee), 1),
            "max": round(max(ee), 1),
            "mean": round(statistics.mean(ee), 1),
            "median": round(statistics.median(ee), 1),
            "p95": round(pct(95), 1),
            "p99": round(pct(99), 1),
        },
    }
    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
