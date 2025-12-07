#!/bin/bash
# Script to help set up PDF files for evaluation

PDF_DIR="$(cd "$(dirname "$0")/.." && pwd)/pdfs"

echo "📁 Code Extractor PDF Setup"
echo "============================"
echo ""
echo "PDF Directory: $PDF_DIR"
echo ""

# Create directory if it doesn't exist
mkdir -p "$PDF_DIR"

echo "📋 Instructions:"
echo "1. Copy your PDF files to: $PDF_DIR"
echo "2. PDF files should be named similar to paper titles"
echo "3. Examples:"
echo "   - Integrating_Machine_Learning_with_Human_Knowledge.pdf"
echo "   - Attention_Is_All_You_Need.pdf"
echo "   - AutoML_Zero_Evolving_Machine_Learning_Algorithms_From_Scratch.pdf"
echo ""
echo "📊 Current PDF files:"
ls -lh "$PDF_DIR"/*.pdf 2>/dev/null | awk '{print "  - " $9}' || echo "  (No PDF files found)"
echo ""
echo "✅ After adding PDFs, run:"
echo "   python evaluation/code_extractor/scripts/evaluate_code_extractor.py"

