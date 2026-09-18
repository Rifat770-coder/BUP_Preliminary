import asyncio, json, os
os.environ["LLM_PROVIDER"] = "mock"
from app.config import load_settings
from app.llm.interpreter import interpret_notes

settings = load_settings()

cases = json.load(open("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))["cases"]
for c in cases:
    inp = c["input"]
    notes = inp["operator_notes"]
    out = asyncio.run(interpret_notes(settings, notes, inp["battery"]["capacity_kwh"]))
    print(c["id"], "->")
    for d in out:
        print("  ", d)
