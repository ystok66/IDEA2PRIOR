#!/bin/bash
# Quick script to run Evolution Mapper evaluation

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$PROJECT_ROOT"

echo "🚀 Starting Evolution Mapper Evaluation"
echo "========================================"
echo ""

# Check if OpenAI API key is set
if [ -z "$OPENAI_API_KEY" ]; then
    echo "⚠️  OPENAI_API_KEY not set, using default from code"
fi

# Run evaluation
echo "📊 Running evaluation for: transformers"
echo ""

python evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py \
    --topic transformers \
    --max-papers 15

echo ""
echo "✅ Evaluation completed!"
echo "📁 Results saved in: evaluation/evolution_mapper/results/"

