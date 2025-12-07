#!/bin/bash
# Script to check evaluation status

LOG_FILE="/tmp/full_evaluation.log"
RESULTS_DIR="evaluation/evolution_mapper/results"

echo "📊 Evolution Mapper 评估状态"
echo "================================"
echo ""

# Check if process is running
if pgrep -f "evaluate_evolution_mapper" > /dev/null; then
    echo "✅ 评估进程正在运行中..."
    echo ""
    echo "📝 最新日志："
    tail -10 "$LOG_FILE" 2>/dev/null | grep -E "(Evaluating|Evaluation run|Overall Score|✅|❌)" | tail -5
else
    echo "⏸️  评估进程未运行"
fi

echo ""
echo "📁 已完成的评估结果："
ls -lh "$RESULTS_DIR"/*.json 2>/dev/null | awk '{print "  - " $9 " (" $5 ")"}'

echo ""
echo "📊 评估摘要："
for file in "$RESULTS_DIR"/*_evaluation.json; do
    if [ -f "$file" ]; then
        topic=$(basename "$file" _evaluation.json)
        score=$(grep -o '"overall_score": [0-9.]*' "$file" | head -1 | awk '{print $2}')
        num_eval=$(grep -o '"num_evaluations": [0-9]*' "$file" | head -1 | awk '{print $2}')
        successful=$(grep -o '"successful_evaluations": [0-9]*' "$file" | head -1 | awk '{print $2}')
        echo "  $topic: $score/10 ($successful/$num_eval evaluations)"
    fi
done

