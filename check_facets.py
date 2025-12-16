import json
j = json.load(open('runs/20251209_143654_monocular_3d_object_detection/synthesis/synthesis_input.json', encoding='utf-8'))
print('facet_coverage:')
for f in j['global_stats']['facet_coverage']:
    print(f"  {f['facet_id']}: strong={f['strong_papers']}, total={f['total_papers']}")
print(f"\nnum_block3: {j['global_stats']['num_block3']}")
