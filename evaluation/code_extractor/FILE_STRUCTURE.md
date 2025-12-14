## File Descriptions

### Core Documentation
- **README.md**: Usage instructions, quick start guide, and environment setup
- **DESIGN.md**: Design motivation, system architecture, and key technical decisions
- **RESULTS_ANALYSIS.md**: Evaluation result analysis, error diagnosis, and improvement recommendations

### Data Files
- **ground_truth/code_extractor_ground_truth.json**: Expert-annotated ground truth for 10 research papers

### Script Files
- **scripts/evaluate_code_extractor.py**: Main evaluation pipeline
- **scripts/setup_pdf.sh**: Instructions and utilities for setting up PDF files
- **scripts/test_ground_truth.py**: Ground truth consistency and validation script

### Result Files (Generated After Execution)
- **results/paper_*_evaluation.json**: Complete evaluation outputs in JSON format
- **results/paper_*_evaluation_summary.md**: Human-readable evaluation summaries in Markdown format

### Temporary / Ignored Files
- **temp_output/**: Temporary outputs from PDF parsing (ignored via `.gitignore`)
- **evaluation_log.txt**: Evaluation logs (ignored via `.gitignore`)
