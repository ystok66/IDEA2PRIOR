#!/bin/bash
# Quick script to run the graph comparison experiment

# Set default values
PDF_CACHE="${1:-./pdf_cache}"
OUTPUT_DIR="${2:-./experiments/parsed_pdfs}"
RESULTS_DIR="${3:-./experiments/results}"

echo "🚀 Running Graph Comparison Experiment"
echo "📁 PDF Cache: $PDF_CACHE"
echo "📁 Output Dir: $OUTPUT_DIR"
echo "📁 Results Dir: $RESULTS_DIR"
echo ""

# Run the experiment
python experiments/graph_comparison_experiment.py \
    --pdf-cache "$PDF_CACHE" \
    --output-dir "$OUTPUT_DIR" \
    --results-dir "$RESULTS_DIR" \
    --task-name "graph_comparison_experiment"

echo ""
echo "✅ Experiment completed. Check $RESULTS_DIR for results."

