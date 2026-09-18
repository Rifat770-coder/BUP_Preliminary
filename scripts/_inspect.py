import json
d = json.load(open('BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json'))
for c in d['cases']:
    inp = c['input']
    exp = c['expected_output']
    notes = inp.get('operator_notes', [])
    di = exp.get('directive_interpretation', [])
    types = [x.get('directive_type') for x in di]
    print(f"{c['id']:12s} notes={len(notes)} di={len(di)} cost={exp['total_cost_bdt']:.2f} types={types}")
print('---')
print('note 0:', json.dumps(d['cases'][0]['input']['operator_notes']))
print('sample 0 expected di:', json.dumps(d['cases'][0]['expected_output']['directive_interpretation'], indent=2)[:800])
