# Code Extractor Evaluation

## Overview

This evaluation framework uses manually annotated ground truth data and LLM (ChatGPT) as a judge to evaluate the Code Extractor tool.

## 📚 Documentation

- **[DESIGN.md](DESIGN.md)**: Design philosophy, technical architecture, and key decisions
- **[RESULTS_ANALYSIS.md](RESULTS_ANALYSIS.md)**: Evaluation results analysis, problem diagnosis, and improvement recommendations
- **[FILE_STRUCTURE.md](FILE_STRUCTURE.md)**: Directory structure and file organization

## Ground Truth Data

A manually annotated dataset of 10 research papers is provided as ground truth:

- **Source**: Based on public information and paper content analysis
- **Annotation**: Manual research and reasonable inference
- **Fields**: 
  - `pseudocode_completeness` (0-2): Completeness of pseudocode
  - `reproducibility` (1-3): Reproducibility score
   - `dependencies`: List of dependencies
  - `model_structure`: Model structure details
  - `missing_components`: List of missing components
  - `notes`: Additional notes

**Note**: Some fields contain generalized information as not all papers provide complete details. This dataset serves as a manually annotated benchmark or agent evaluation gold-standard seed dataset.

## Evaluation Criteria

The LLM Judge evaluates the following dimensions:

1. **Pseudocode Completeness Match** (0-2):
   - Does our tool's completeness score match the ground truth?
   - Are the identified missing components correct?

2. **Reproducibility Score Match** (1-3):
   - Does our tool's reproducibility score match the ground truth?
   - Is the assessment accurate?

3. **Dependency Identification** (0-10):
   - Are the identified dependencies correct?
   - Are all dependencies from ground truth found?

4. **Model Structure Analysis** (0-10):
   - Does the model structure analysis match ground truth?
   - Are key details captured?

5. **Missing Components Detection** (0-10):
   - Are missing components correctly identified?
   - Do they match ground truth?

## Success Criteria

- **Pseudocode Completeness Match** > 80%: Our tool correctly identifies completeness
- **Reproducibility Score Match** > 70%: Our tool correctly assesses reproducibility
- **Dependency Identification** > 70%: Our tool correctly identifies dependencies
- **Overall Score** > 7.0/10: LLM considers our tool quality good

## Directory Structure

```
evaluation/code_extractor/
├── README.md                    # This file (usage guide)
├── DESIGN.md                    # Design document
├── RESULTS_ANALYSIS.md          # Results analysis
├── FILE_STRUCTURE.md            # File structure guide
├── ground_truth/                # Ground truth data
│   └── code_extractor_ground_truth.json
├── scripts/                     # Evaluation scripts
│   ├── evaluate_code_extractor.py
│   ├── setup_pdf.sh
│   └── test_ground_truth.py
├── results/                     # Evaluation results (generated)
│   ├── paper_*_evaluation.json
│   └── paper_*_evaluation_summary.md
└── pdfs/                        # PDF files (manually added)
    └── *.pdf
```

## Usage

```bash
# Run evaluation for all papers
python evaluation/code_extractor/scripts/evaluate_code_extractor.py

# Run evaluation for specific paper
python evaluation/code_extractor/scripts/evaluate_code_extractor.py --paper-id 1

# Run with multiple evaluations and averaging
python evaluation/code_extractor/scripts/evaluate_code_extractor.py --num-evaluations 3
```

## Environment Variables

Set the following environment variables:

```bash
# ChatGPT API key for LLM Judge
export OPENAI_API_KEY="sk-proj-..."

# Semantic Scholar API key (optional, for paper search)
export SEMANTIC_SCHOLAR_API_KEY="..."
```

## Quick Start

### 1. Setup Environment

```bash
# Set OpenAI API key for LLM Judge
export OPENAI_API_KEY="sk-proj-..."

# Optional: Set Semantic Scholar API key
export SEMANTIC_SCHOLAR_API_KEY="..."
```

### 2. Prepare PDF Files

Place PDF files in `evaluation/code_extractor/pdfs/` directory. The evaluation script will automatically match PDFs with papers based on title.

### 3. Run Evaluation

```bash
# Evaluate all papers (10 papers)
python evaluation/code_extractor/scripts/evaluate_code_extractor.py

# Evaluate specific paper
python evaluation/code_extractor/scripts/evaluate_code_extractor.py --paper-id 1

# Run with multiple evaluations and averaging (recommended)
python evaluation/code_extractor/scripts/evaluate_code_extractor.py --num-evaluations 3
```

### 4. View Results

Results are saved in `evaluation/code_extractor/results/`:
- `paper_*_evaluation.json`: Complete evaluation data
- `paper_*_evaluation_summary.md`: Human-readable summary

## Evaluation Process

1. **Load Ground Truth**: Load manually annotated data from `ground_truth/code_extractor_ground_truth.json`
2. **Find PDF**: Automatically match PDF file with paper title
3. **Run Code Extractor**: Extract and evaluate code for each paper
4. **Parse Output**: Extract key information from tool output
5. **LLM Judge Evaluation**: 
   - Standalone evaluation (independent assessment)
   - Comparison evaluation (vs ground truth)
6. **Aggregate Results**: Calculate averages from multiple evaluations
7. **Save Results**: Save JSON and Markdown summaries

## Current Evaluation Status

- **Completed Papers**: 6/10
- **Average Standalone Score**: 2.17/10
- **Average Comparison Score**: 2.50/10

See [RESULTS_ANALYSIS.md](RESULTS_ANALYSIS.md) for detailed analysis.

## Key Features

- ✅ **Intelligent PDF Matching**: Automatically matches PDF files with paper titles
- ✅ **Multiple Evaluations**: Runs multiple LLM evaluations and averages results
- ✅ **Comprehensive Metrics**: 5 evaluation dimensions
- ✅ **Detailed Reports**: Both JSON and Markdown formats
- ✅ **Range Mapping**: Automatically maps tool output ranges to ground truth ranges

## Troubleshooting

### PDF Not Found
- Ensure PDF files are in `evaluation/code_extractor/pdfs/` directory
- Check PDF filename matches paper title (fuzzy matching supported)

### Low Scores
- See [RESULTS_ANALYSIS.md](RESULTS_ANALYSIS.md) for common issues and solutions
- Check if Gemini API is available (affects LLM-assisted extraction)

### API Errors
- Verify OpenAI API key is valid
- Check API rate limits and quotas

