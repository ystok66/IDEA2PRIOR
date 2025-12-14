# Code Extractor Evaluation - Design Document

## 1. Design Objectives

Design a comprehensive evaluation framework to assess the Code Extractor tool's performance in the following aspects:
- Code/pseudocode extraction quality
- Completeness assessment accuracy
- Reproducibility assessment accuracy
- Dependency identification accuracy
- Model structure analysis accuracy
- Missing components detection accuracy

## 2. Evaluation Architecture

### 2.1 Three-Layer Evaluation System

```
┌─────────────────────────────────────┐
│   Ground Truth (Manually Annotated) │
│   - 10 papers annotated data        │
│   - Includes completeness, repro,   │
│     dependencies, etc.              │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   Code Extractor Tool               │
│   - Extract code/pseudocode from PDF│
│   - Assess completeness & repro     │
│   - Identify dependencies & frameworks│
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   LLM Judge (ChatGPT)               │
│   - Standalone Evaluation           │
│   - Comparison Evaluation            │
└─────────────────────────────────────┘
```

### 2.2 Core Components

#### 2.2.1 Ground Truth Data
- **Source**: Manually annotated data for 10 papers
- **Fields**:
  - `pseudocode_completeness` (0-2): Pseudocode completeness
  - `reproducibility` (1-3): Reproducibility score
  - `dependencies`: List of dependencies
  - `model_structure`: Model structure details
  - `missing_components`: List of missing components

#### 2.2.2 Code Extractor Tool
- **Functionality**: Extract code/pseudocode from PDFs, assess completeness and reproducibility
- **Output Format**: Markdown report containing:
  - Completeness score (0-1.0)
  - Reproducibility score (0-1.0)
  - Identified dependencies
  - Model structure
  - Missing components

#### 2.2.3 Output Parser
- **Functionality**: Parse Code Extractor output
- **Key Fixes**:
  - **Range Mapping**: Map tool output 0-1.0 to ground truth 0-2 (completeness) and 1-3 (reproducibility)
  - **Model Structure Extraction**: Extract JSON-format model structure from code blocks
  - **Missing Components Optimization**: Prioritize extraction from "LLM Evaluation Results" section

#### 2.2.4 LLM Judge
- **Model**: ChatGPT (gpt-4o-mini)
- **Evaluation Types**:
  1. **Standalone Evaluation**: Independent assessment of tool output quality (without ground truth)
  2. **Comparison Evaluation**: Comparison with ground truth
- **Evaluation Metrics** (0-10 points):
  - Pseudocode Completeness Match
  - Reproducibility Score Match
  - Dependency Identification
  - Model Structure Analysis
  - Missing Components Detection

## 3. Key Technical Decisions

### 3.1 PDF Matching Algorithm

**Problem**: Paper titles and PDF filenames have inconsistent formats, requiring intelligent matching

**Solution**: Three-layer matching strategy
1. **Exact Match**: Complete match after text normalization
2. **Substring Match**: 80% similarity threshold
3. **Word Weight Matching**: Weighted matching based on word length and importance, minimum 60% match

**Implementation**:
```python
def normalize_text(text: str) -> str:
    # Handle special characters, hyphens, spaces, etc.
    # Convert to lowercase, normalize spaces

def find_pdf_for_paper(paper_title: str) -> Optional[str]:
    # 1. Exact match
    # 2. Substring match (80% threshold)
    # 3. Word weight match (60% threshold)
```

### 3.2 Range Mapping Issue

**Problem**: 
- Tool Output: Completeness (0-1.0), Reproducibility (0-1.0)
- Ground Truth: Completeness (0-2), Reproducibility (1-3)

**Solution**: Range mapping in Parser
- Completeness: `value * 2` (0-1.0 → 0-2)
- Reproducibility: `value * 2 + 1` (0-1.0 → 1-3)

### 3.3 Multiple Evaluations Averaging

**Purpose**: Improve reliability and stability of evaluation results

**Implementation**: 
- Default: Run 3 LLM Judge evaluations
- Calculate mean and standard deviation
- Aggregate feedback and recommendations from all evaluations

## 4. Evaluation Process

### 4.1 Single Paper Evaluation Process

```
1. Load Ground Truth
   ↓
2. Find PDF file (intelligent matching)
   ↓
3. Run Code Extractor
   - Extract code/pseudocode
   - Assess completeness and reproducibility
   - Identify dependencies and frameworks
   ↓
4. Parse tool output
   - Extract key fields
   - Range mapping
   ↓
5. Standalone LLM Judge evaluation (3 times)
   - Independent assessment of tool output quality
   ↓
6. Comparison LLM Judge evaluation (3 times)
   - Compare with Ground Truth
   ↓
7. Aggregate results
   - Calculate mean and standard deviation
   - Generate summary report
   ↓
8. Save results
   - JSON format (complete data)
   - Markdown format (human-readable)
```

### 4.2 Batch Evaluation

- Support evaluating all papers or specified papers
- Support parallel evaluation (future optimization)

## 5. Output Format

### 5.1 JSON Output
```json
{
  "paper_id": 1,
  "paper_title": "...",
  "standalone_evaluation": {
    "averaged_result": {
      "overall_score": 3.0,
      "scores": {...}
    },
    "individual_results": [...]
  },
  "comparison_evaluation": {
    "averaged_result": {...},
    "individual_results": [...]
  }
}
```

### 5.2 Markdown Summary
- Paper information
- Ground Truth summary
- Standalone evaluation results
- Comparison evaluation results
- Detailed scores, strengths, weaknesses, recommendations

## 6. Success Criteria

- **Pseudocode Completeness Match** > 80% (8.0/10)
- **Reproducibility Score Match** > 70% (7.0/10)
- **Dependency Identification** > 70% (7.0/10)
- **Model Structure Analysis** > 70% (7.0/10)
- **Missing Components Detection** > 70% (7.0/10)
- **Overall Score** > 7.0/10

## 7. Known Limitations and Future Improvements

### 7.1 Current Limitations
1. **Gemini API Dependency**: LLM-assisted extraction depends on Gemini API; if API is suspended, functionality degrades
2. **PDF Parsing Quality**: Depends on PDFExtractorRAGTool parsing quality
3. **Evaluation Time**: 2-5 minutes per paper (including multiple LLM evaluations)

### 7.2 Future Improvements
1. **Web Search Enhancement**: Integrate web search functionality to supplement missing information in PDFs
2. **Caching Mechanism**: Cache PDF parsing results and evaluation results
3. **Parallel Evaluation**: Support parallel evaluation of multiple papers
4. **Smarter Matching**: Use semantic similarity for PDF matching
5. **Evaluation Metrics Optimization**: Adjust evaluation metric weights based on actual usage

## 8. File Structure

```
evaluation/code_extractor/
├── README.md                    # Usage guide
├── DESIGN.md                    # This document (design rationale)
├── RESULTS_ANALYSIS.md          # Results analysis
├── ground_truth/                # Ground Truth data
│   └── code_extractor_ground_truth.json
├── scripts/                     # Evaluation scripts
│   ├── evaluate_code_extractor.py
│   └── setup_pdf.sh
├── results/                     # Evaluation results
│   ├── paper_*_evaluation.json
│   └── paper_*_evaluation_summary.md
└── pdfs/                        # PDF files
    └── *.pdf
```

## 9. Dependencies

- **Code Extractor Tool**: Core tool
- **PDF Extractor**: PDF parsing
- **Semantic Scholar Tool**: Paper search (optional)
- **OpenAI API**: LLM Judge
- **Gemini API**: LLM-assisted extraction (optional)

## 10. Usage Examples

```bash
# Evaluate all papers
python evaluation/code_extractor/scripts/evaluate_code_extractor.py

# Evaluate specific paper
python evaluation/code_extractor/scripts/evaluate_code_extractor.py --paper-id 1

# Multiple evaluations with averaging
python evaluation/code_extractor/scripts/evaluate_code_extractor.py --num-evaluations 5
```

