import json
cases = json.load(open("BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"))["cases"]
for c in cases:
    print(f"\n=== {c['id']}: {c['label']} ===")
    print("NOTES:")
    for n in c['input']['operator_notes']:
        print("  -", n)
    print("EXPECTED DI:")
    for d in c['expected_output']['directive_interpretation']:
        print("  -", d)
