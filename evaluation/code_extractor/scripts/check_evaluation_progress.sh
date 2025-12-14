#!/bin/bash
# Quick script to check evaluation progress

cd "$(dirname "$0")/../.."
python3 << 'PYTHON'
import json
from pathlib import Path
from datetime import datetime

results_dir = Path('evaluation/code_extractor/results')

print("📊 Code Extractor 评估进度")
print("=" * 80)

completed = []
for i in range(1, 11):
    json_file = results_dir / f'paper_{i}_evaluation.json'
    if json_file.exists():
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        comparison = data.get('comparison_evaluation', {}).get('averaged_result', {})
        score = comparison.get('overall_score', 0)
        timestamp = data.get('timestamp', '')[:19] if data.get('timestamp') else 'N/A'
        
        completed.append({
            'id': i,
            'score': score,
            'time': timestamp
        })

completed_ids = [c['id'] for c in completed]
pending = [i for i in range(1, 11) if i not in completed_ids]

print(f"\n✅ 已完成: {len(completed)}/10")
for c in sorted(completed, key=lambda x: x['id']):
    status = "✅" if c['score'] >= 5.0 else "⚠️"
    print(f"  {status} Paper {c['id']}: {c['score']:.1f}/10 (评估时间: {c['time']})")

if pending:
    print(f"\n⏳ 待评估: {len(pending)}/10")
    print(f"  Papers: {', '.join(map(str, pending))}")

if completed:
    avg_score = sum(c['score'] for c in completed) / len(completed)
    print(f"\n📈 平均分数: {avg_score:.1f}/10")
    print(f"📊 达到 5.0+ 的论文: {sum(1 for c in completed if c['score'] >= 5.0)}/{len(completed)}")
PYTHON

