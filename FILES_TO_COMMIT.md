# Files to Commit - Feature Branch Preparation

## 📋 Step 1: File List

According to the Git merge guide, here is the list of files to commit.

### ✅ Core Tool Files (Must Commit)

#### 1. Code Extractor Tool
- `paperarena_hub/tools/code_extractor.py` - Core code extraction tool (~2000 lines)

#### 2. Evolution Mapper Tool  
- `paperarena_hub/tools/evolution_mapper.py` - Technology evolution map generation tool

#### 3. Planner-Worker Architecture
- `paperarena_hub/planner_worker.py` - Planner-Worker architecture implementation

### ✅ Evaluation Framework Files (Must Commit)

#### Code Extractor Evaluation
- `evaluation/code_extractor/README.md` - Usage guide
- `evaluation/code_extractor/DESIGN.md` - Design document
- `evaluation/code_extractor/RESULTS_ANALYSIS.md` - Results analysis
- `evaluation/code_extractor/FILE_STRUCTURE.md` - File structure guide
- `evaluation/code_extractor/.gitignore` - Git ignore configuration
- `evaluation/code_extractor/ground_truth/code_extractor_ground_truth.json` - Ground Truth data
- `evaluation/code_extractor/scripts/evaluate_code_extractor.py` - Evaluation script
- `evaluation/code_extractor/scripts/setup_pdf.sh` - PDF setup script
- `evaluation/code_extractor/scripts/test_ground_truth.py` - Ground Truth test
- `evaluation/code_extractor/results/paper_*_evaluation.json` - Evaluation results (6 papers)
- `evaluation/code_extractor/results/paper_*_evaluation_summary.md` - Evaluation summaries (6 papers)

#### Evolution Mapper Evaluation
- `evaluation/evolution_mapper/README.md` - Usage guide
- `evaluation/evolution_mapper/EVALUATION_SUMMARY.md` - Evaluation summary
- `evaluation/evolution_mapper/USAGE.md` - Usage guide
- `evaluation/evolution_mapper/ground_truth/*.bib` - Ground Truth data (3 BIB files)
- `evaluation/evolution_mapper/scripts/bib_parser.py` - BIB parser
- `evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py` - Evaluation script
- `evaluation/evolution_mapper/scripts/test_bib_parser.py` - Test script
- `evaluation/evolution_mapper/results/*.json` - Evaluation results
- `evaluation/evolution_mapper/results/*.md` - Evaluation summaries

### ✅ Test Script Files (Must Commit)

- `test_code_extractor_new.py` - Code Extractor test
- `test_planner_worker.py` - Planner-Worker basic test
- `test_planner_worker_demo.py` - Planner-Worker demo
- `test_planner_worker_full.py` - Planner-Worker full test
- `test_agent_evolution_mapper.py` - Evolution Mapper Agent test

### ✅ Configuration Files (Must Commit)

- `.env.example` - Environment variables template (if not already exists)
- `requirements.txt` or `requirements说明.md` - Dependencies documentation

### ✅ Documentation Files (Recommended to Commit)

- `开发记录.md/12.2 项目说明.md` - Project documentation
- `开发记录.md/Code_Extractor_完整实现方案.md` - Implementation plan
- `开发记录.md/Code_Extractor_Web_Search_Enhancement.md` - Web search enhancement documentation
- `开发记录.md/Extended_Tools_Evaluation.md` - Extended tools evaluation documentation

### ❌ Files Not to Commit

- `temp_output/` - Temporary output (already in .gitignore)
- `evaluation_log.txt` - Log file (already in .gitignore)
- `history/` - History output (optional, recommended not to commit)
- `开发记录.md/code_extractor_test_*.json` - Temporary test results
- `开发记录.md/code_extractor_test_*.md` - Temporary test results

## 📝 Step 2: Commit Command Examples

```bash
# Add core tools
git add paperarena_hub/tools/code_extractor.py
git add paperarena_hub/tools/evolution_mapper.py
git add paperarena_hub/planner_worker.py

# Add evaluation framework
git add evaluation/code_extractor/
git add evaluation/evolution_mapper/

# Add test scripts
git add test_code_extractor_new.py
git add test_planner_worker*.py
git add test_agent_evolution_mapper.py

# Add configuration files
git add .env.example
git add requirements.txt

# Add documentation (optional)
git add 开发记录.md/12.2\ 项目说明.md
```

## ⚠️ Important Notes

1. **Do NOT commit** `.env` file (only commit `.env.example`)
2. **Do NOT commit** `temp_output/` directory
3. **Do NOT commit** files containing API keys
4. **Check** all files for hardcoded API keys
