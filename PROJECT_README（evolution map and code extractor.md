# PaperArena - Extended Tools & Evaluation Framework

## 📋 Project Overview

This project extends the original PaperArena with two core tools and a complete evaluation framework:

1. **Code Extractor Tool** - Code/pseudocode extraction and reproducibility evaluation tool
2. **Evolution Mapper Tool** - Technology evolution map generation tool
3. **Planner-Worker Architecture** - Autonomous task planning and execution architecture
4. **Evaluation Framework** - Complete evaluation framework (Code Extractor and Evolution Mapper)

## 🎯 Core Features

### 1. Code Extractor Tool

**Functionality**: Extract code/pseudocode from research paper PDFs and evaluate completeness and reproducibility

**Core Capabilities**:
- ✅ Automatic code existence detection (code blocks, pseudocode blocks)
- ✅ Automatic code completeness check (input/output, step completeness)
- ✅ LLM evaluation of pseudocode (reproducibility, completeness, framework, dependencies)
- ✅ Parse frameworks and dependencies (PyTorch, TensorFlow, JAX, etc.)
- ✅ Analyze model structure (number of layers, parameters, etc.)
- ✅ Automatic unit tests or accuracy tests
- ✅ Paper classification (📗 Complete code / 📙 Partial code / 📕 No code)
- ✅ Web search enhancement (GitHub repositories, code implementations, framework information)

**Output Format**:
- Markdown report (with detailed analysis)
- JSON format (structured data)

### 2. Evolution Mapper Tool

**Functionality**: Generate technology evolution maps based on citation relationships

**Core Capabilities**:
- ✅ Get paper citation networks from Semantic Scholar
- ✅ Generate evolution maps in Mermaid/Graphviz format
- ✅ Visualize timeline
- ✅ Detailed node annotations (description for each paper)
- ✅ Support custom topics and paper counts

**Output Format**:
- Mermaid diagrams (Markdown format)
- Graphviz diagrams (optional)
- Timeline visualization

### 3. Planner-Worker Architecture

**Functionality**: Autonomous task planning and execution architecture

**Core Components**:
- **Planner**: Analyze tasks, generate execution plans, select tools
- **Worker**: Execute plans, call tools
- **Critic**: Evaluate execution results, provide feedback, decide whether to re-execute

**Features**:
- ✅ Autonomous tool selection
- ✅ Automatic task decomposition
- ✅ Execution result evaluation
- ✅ Automatic re-execution (based on Critic feedback)

### 4. Evaluation Framework

**Functionality**: Evaluate tool quality using Ground Truth and LLM Judge

**Evaluation Dimensions**:

#### Code Extractor Evaluation
- Pseudocode Completeness Match (0-10)
- Reproducibility Score Match (0-10)
- Dependency Identification (0-10)
- Model Structure Analysis (0-10)
- Missing Components Detection (0-10)

#### Evolution Mapper Evaluation
- Paper Discovery Coverage (0-10)
- Citation Relationship Accuracy (0-10)
- Network Structure Similarity (0-10)
- Additional Value (0-10)

## 📁 Project File Structure

```
PaperArena/
├── paperarena_hub/
│   ├── tools/
│   │   ├── code_extractor.py          # Code Extractor tool (core)
│   │   ├── evolution_mapper.py        # Evolution Mapper tool (core)
│   │   ├── database_search.py         # Database search tool (dependency)
│   │   ├── llm_chat.py                # LLM chat tool (dependency)
│   │   └── ...
│   └── planner_worker.py              # Planner-Worker architecture (core)
│
├── evaluation/
│   ├── code_extractor/                # Code Extractor evaluation framework
│   │   ├── README.md                  # Usage guide
│   │   ├── DESIGN.md                  # Design document
│   │   ├── RESULTS_ANALYSIS.md        # Results analysis
│   │   ├── FILE_STRUCTURE.md          # File structure guide
│   │   ├── ground_truth/
│   │   │   └── code_extractor_ground_truth.json  # Ground Truth data (10 papers)
│   │   ├── scripts/
│   │   │   ├── evaluate_code_extractor.py        # Evaluation script
│   │   │   ├── setup_pdf.sh                      # PDF setup script
│   │   │   └── test_ground_truth.py               # Ground Truth test
│   │   └── results/                   # Evaluation results (generated after running)
│   │       ├── paper_*_evaluation.json
│   │       └── paper_*_evaluation_summary.md
│   │
│   └── evolution_mapper/               # Evolution Mapper evaluation framework
│       ├── README.md                  # Usage guide
│       ├── EVALUATION_SUMMARY.md      # Evaluation summary
│       ├── USAGE.md                   # Usage guide
│       ├── ground_truth/               # Ground Truth data (BIB files)
│       │   ├── ConnectedPapers-for-Attention-is-All-you-Need.bib
│       │   ├── ConnectedPapers-for-Generative-Adversarial-Nets.bib
│       │   └── ConnectedPapers-for-ImageNet-classification-with-deep-convolutional-neural-networks.bib
│       ├── scripts/
│       │   ├── bib_parser.py          # BIB parser
│       │   ├── evaluate_evolution_mapper.py  # Evaluation script
│       │   └── test_bib_parser.py     # Test script
│       └── results/                    # Evaluation results (generated after running)
│
├── test_code_extractor_new.py         # Code Extractor test script
├── test_planner_worker.py             # Planner-Worker basic test
├── test_planner_worker_demo.py         # Planner-Worker demo
├── test_planner_worker_full.py         # Planner-Worker full test
├── test_agent_evolution_mapper.py      # Evolution Mapper Agent test
│
├── .env.example                        # Environment variables template
├── requirements.txt                    # Dependencies list
└── PROJECT_README.md                   # This file
```

## 🔧 Technology Stack

### Core Frameworks
- **smolagents**: Agent framework (ReAct architecture)
- **OpenAI API**: LLM Judge (ChatGPT gpt-4o-mini)
- **Gemini API**: Main LLM model (via OpenAI-compatible interface)
- **OpenAI Embedding API**: Vector retrieval

### Tool Libraries
- **mineru**: PDF parsing
- **Semantic Scholar API**: Paper search and citation networks
- **LocalPythonExecutor**: Code execution (secure sandbox)
- **networkx**: Graph algorithms (optional)

### Evaluation Framework
- **LLM Judge**: ChatGPT as evaluator
- **Ground Truth**: Manually annotated data
- **Multiple evaluations and averaging**: Improve reliability

## 📝 Prompts Used

### 1. Code Extractor - Pseudocode Evaluation Prompt

**Location**: `paperarena_hub/tools/code_extractor.py` (lines 167-192)

```python
PSEUDOCODE_EVALUATION_PROMPT = """You are a code evaluation expert. Please evaluate the pseudocode in the following paper:

{pseudocode}

Please analyze:
1. Completeness of pseudocode (whether it has input/output, complete steps)
2. Reproducibility (whether it contains enough information to reproduce)
3. Framework used (PyTorch, TensorFlow, JAX, etc.)
4. Dependencies (numpy, torch, sklearn, etc.)
5. Model structure (number of layers, parameters, etc.)
6. Missing components (training loop, data preprocessing, etc.)

Please return in JSON format:
{{
    "reproducibility_score": 0.0-1.0,
    "completeness_score": 0.0-1.0,
    "identified_framework": "framework name or unknown",
    "identified_dependencies": ["dependency1", "dependency2"],
    "model_structure": {{
        "layers": ["layer1", "layer2"],
        "parameters": "parameter description"
    }},
    "conversion_difficulty": "easy/medium/hard/unknown",
    "missing_components": ["missing component1", "missing component2"]
}}
"""
```

### 2. Code Extractor - LLM-Assisted Extraction Prompt

**Location**: `paperarena_hub/tools/code_extractor.py` (lines 1221-1258)

```python
prompt = f"""You are an expert code extractor. Extract ALL code and pseudocode from this paper section.

CRITICAL INSTRUCTIONS:
1. Look in ALL sections: abstract, introduction, methods, experiments, appendix, supplementary
2. Extract even incomplete code/pseudocode
3. Look for:
   - Code blocks with ``` markers
   - Function/class definitions
   - Algorithm descriptions (even without "Algorithm X" label)
   - Procedure definitions
   - Step-by-step instructions
   - Mathematical algorithms in formulas
   - Input/Output format descriptions
4. If you find ANY code or pseudocode, extract it (even if incomplete)

Paper content (section {chunk_idx + 1}/{len(chunks)}):
{chunk[:10000]}

Return JSON format:
{{
  "pseudocode_blocks": [
    {{
      "algorithm_name": "Algorithm name or description",
      "steps": ["step1", "step2", "step3"],
      "input": "input description if mentioned",
      "output": "output description if mentioned"
    }}
  ],
  "framework": "PyTorch or TensorFlow or JAX or Keras or empty string if not found",
  "dependencies": ["numpy", "torch", "tensorflow", "scipy", etc.],
  "model_structure": {{
    "description": "model description",
    "layers": "layer information",
    "parameters": "parameter information"
  }}
}}

**IMPORTANT**: You MUST return valid JSON. If you find no information, return empty arrays/strings, but still return valid JSON."""
```

### 3. Code Extractor - Framework and Dependency Extraction Prompt

**Location**: `paperarena_hub/tools/code_extractor.py` (lines 1404-1433)

```python
prompt = f"""You are an expert in analyzing research papers. Analyze this paper text and extract:

1. **Framework name**: Look for mentions of PyTorch, TensorFlow, JAX, Keras, or other deep learning frameworks. Check for phrases like "implemented in", "using", "based on", etc. Return the framework name or empty string if not found.

2. **Dependencies/libraries**: Extract ALL mentioned Python libraries, packages, or dependencies. Look for:
   - Import statements mentioned in text
   - Library names in methodology sections
   - Dependencies in implementation details
   Examples: numpy, scipy, torch, tensorflow, sklearn, pandas, matplotlib, etc.

3. **Model structure information**: Extract any information about:
   - Model architecture description
   - Layer types and configurations
   - Parameter counts or model size

**Text**:
{content_sample}

**IMPORTANT**: You MUST return valid JSON. Be thorough in extracting dependencies - even common ones like numpy, scipy should be included if the paper uses them.

Return JSON:
{{
  "framework": "PyTorch or TensorFlow or JAX or Keras or empty string",
  "dependencies": ["numpy", "scipy", "torch", "tensorflow", etc.],
  "model_structure": {{
    "description": "model description",
    "layers": "layer information",
    "parameters": "parameter information"
  }}
}}"""
```

### 4. Planner - Task Planning Prompt

**Location**: `paperarena_hub/planner_worker.py` (lines 105-137)

```python
_default_planning_prompt = """You are a task planner for a research paper analysis agent. Your job is to analyze the task and create a detailed execution plan.

Available Tools:
{tool_descriptions}

Task: {task}

Please analyze the task and create a detailed plan. Your response should be a JSON object with the following structure:
{{
    "analysis": "Detailed analysis of what needs to be done",
    "selected_tools": [
        {{
            "tool_name": "name_of_tool",
            "reason": "Why this tool is needed",
            "parameters": {{"param1": "value1"}},
            "order": 1
        }}
    ],
    "steps": [
        "Step 1: ...",
        "Step 2: ..."
    ],
    "expected_output": "What the final output should look like"
}}

Important:
1. Only select tools that are actually needed for the task
2. Order the tools logically (what needs to be done first)
3. Provide clear reasons for each tool selection
4. Make sure the plan is executable"""
```

### 5. Critic - Execution Result Evaluation Prompt

**Location**: `paperarena_hub/planner_worker.py` (lines 424-456)

```python
_default_evaluation_prompt = """You are a quality critic for a research paper analysis agent. Your job is to evaluate the execution results and provide feedback.

Task: {task}

Original Plan:
{plan_summary}

Execution Results:
{execution_results}

Please evaluate the execution results and provide a comprehensive assessment. Your response should be a JSON object with the following structure:
{{
    "quality_score": 0.0-1.0,  // Overall quality score (0.0 = poor, 1.0 = excellent)
    "completeness_score": 0.0-1.0,  // How complete the results are (0.0 = incomplete, 1.0 = complete)
    "correctness_score": 0.0-1.0,  // How correct the results are (0.0 = incorrect, 1.0 = correct)
    "meets_requirements": true/false,  // Whether the results meet the task requirements
    "strengths": [
        "Strength 1",
        "Strength 2"
    ],
    "weaknesses": [
        "Weakness 1",
        "Weakness 2"
    ],
    "improvements": [
        "Improvement suggestion 1",
        "Improvement suggestion 2"
    ],
    "needs_reexecution": true/false,  // Whether the task needs to be re-executed
    "reexecution_reason": "Reason if needs_reexecution is true",
    "overall_feedback": "Overall feedback on the execution"
}}"""
```

### 6. Code Extractor Evaluation - Standalone Evaluation Prompt

**Location**: `evaluation/code_extractor/scripts/evaluate_code_extractor.py` (lines 75-151)

```python
_load_standalone_evaluation_prompt = """You are an expert in code analysis and research paper reproducibility evaluation.
Please evaluate the Code Extractor tool output for the following paper:

**Paper Title**: {paper_title}

**Code Extractor Output**:
{our_output}

**Evaluation Criteria** (0-10 scale for each):

1. **Code/Pseudocode Extraction Quality** (0-10):
   - Are code blocks OR pseudocode blocks correctly identified and extracted?
   - Is the code syntax correct (if executable code exists)?
   - Are code/pseudocode blocks complete and usable?
   - **IMPORTANT**: If pseudocode is extracted (even if incomplete), give at least 3 points. Only give 0-2 points if NO code or pseudocode is found at all.
   - Score guidelines:
     * 0-2: No code or pseudocode extracted, or extraction completely failed
     * 3-4: Pseudocode extracted but very incomplete (missing key steps) - MINIMUM score if any pseudocode found
     * 5-6: Pseudocode extracted and partially complete (has main steps but missing details)
     * 7-8: Pseudocode extracted and mostly complete, OR incomplete code blocks extracted
     * 9-10: Complete pseudocode with all details, OR complete executable code blocks

2. **Pseudocode Completeness** (0-10):
   - Are pseudocode blocks identified?
   - Do they have clear input/output?
   - Are the steps complete and logical?

3. **Reproducibility Assessment** (0-10):
   - Can the code/pseudocode be used to reproduce the paper?
   - Are there enough details for implementation?
   - What is the estimated reproducibility score (1-3 scale)?

4. **Dependency Identification** (0-10):
   - Are dependencies accurately listed?
   - Are the identifications comprehensive?

5. **Model Structure Analysis** (0-10):
   - Is the model structure correctly analyzed?
   - Are key details (layers, parameters) captured?
   - Is the analysis detailed and accurate?

6. **Missing Components Detection** (0-10):
   - Are missing components correctly identified?
   - Is the list comprehensive?
   - Are the identifications accurate?

7. **Overall Quality** (0-10):
   - Overall quality of the extraction and analysis
   - How useful is the output for researchers?

**Output Format**: JSON
{{
  "scores": {{
    "code_pseudocode_extraction_quality": 0-10,
    "pseudocode_completeness": 0-10,
    "reproducibility_assessment": 0-10,
    "dependency_identification": 0-10,
    "model_structure_analysis": 0-10,
    "missing_components_detection": 0-10,
    "overall_quality": 0-10
  }},
  "overall_score": 0-10,
  "estimated_pseudocode_completeness": 0-2,
  "estimated_reproducibility": 1-3,
  "identified_dependencies": ["dep1", "dep2"],
  "identified_missing_components": ["component1", "component2"],
  "model_structure_summary": {{
    "description": "description",
    "layers": "value",
    "parameters": "value"
  }},
  "strengths": ["strength1", "strength2"],
  "weaknesses": ["weakness1", "weakness2"],
  "recommendations": ["recommendation1", "recommendation2"]
}}"""
```

### 7. Code Extractor Evaluation - Comparison Evaluation Prompt

**Location**: `evaluation/code_extractor/scripts/evaluate_code_extractor.py` (lines 153-227)

```python
_load_evaluation_prompt = """You are an expert in code analysis and research paper reproducibility evaluation.
Please compare the Code Extractor tool output with the ground truth annotation:

**Ground Truth**:
{ground_truth}

**Our Tool Output**:
{our_output}

**Evaluation Criteria**:
1. **Pseudocode Completeness Match** (0-10):
   - Does our tool's completeness assessment match the ground truth pseudocode_completeness (0-2)?
   - Are the identified missing components correct?
   - Score: 10 if perfect match, 5 if partial match, 0 if no match

2. **Reproducibility Score Match** (0-10):
   - Does our tool's reproducibility score match the ground truth reproducibility (1-3)?
   - Is the assessment accurate?
   - Score: 10 if perfect match, 5 if close match, 0 if no match

3. **Dependency Identification** (0-10):
   - Are the identified dependencies correct?
   - Are all dependencies from ground truth found?
   - Score based on precision and recall

5. **Model Structure Analysis** (0-10):
   - Does the model structure analysis match ground truth?
   - Are key details (description, layers, parameters) captured?
   - Score based on how well it matches

6. **Missing Components Detection** (0-10):
   - Are missing components correctly identified?
   - Do they match ground truth missing_components?
   - Score based on precision and recall

**Output Format**: JSON
{{
  "scores": {{
    "pseudocode_completeness_match": 0-10,
    "reproducibility_score_match": 0-10,
    "dependency_identification": 0-10,
    "model_structure_analysis": 0-10,
    "missing_components_detection": 0-10
  }},
  "overall_score": 0-10,
  "detailed_comparison": {{
    "pseudocode_completeness": {{
      "ground_truth": 0-2,
      "our_tool": "value or N/A",
      "match": true/false
    }},
    "reproducibility": {{
      "ground_truth": 1-3,
      "our_tool": "value or N/A",
      "match": true/false
    }},
    "dependencies": {{
      "ground_truth": ["dep1", "dep2"],
      "our_tool": ["dep1"],
      "precision": 0.0-1.0,
      "recall": 0.0-1.0
    }},
    "missing_components": {{
      "ground_truth": ["component1"],
      "our_tool": ["component1"],
      "precision": 0.0-1.0,
      "recall": 0.0-1.0
    }}
  }},
  "strengths": ["strength1", "strength2"],
  "weaknesses": ["weakness1", "weakness2"],
  "recommendations": ["recommendation1", "recommendation2"]
}}"""
```

### 8. Evolution Mapper Evaluation - Evaluation Prompt

**Location**: `evaluation/evolution_mapper/scripts/evaluate_evolution_mapper.py` (lines 42-93)

```python
_load_evaluation_prompt = """You are an expert in academic research and citation network analysis. 
Please compare two technology evolution maps:

**Ground Truth (Connected Papers)**:
{ground_truth}

**Our Tool Output (Evolution Mapper)**:
{our_output}

**Evaluation Criteria**:
1. **Paper Discovery Coverage** (0-10):
   - How many papers from Connected Papers are also in our output?
   - Are key papers from Connected Papers included?
   - Are there important papers missing?

2. **Citation Relationship Accuracy** (0-10):
   - Do citation relationships match Connected Papers?
   - Are the relationships correctly represented?
   - Are there incorrect relationships?

3. **Network Structure Similarity** (0-10):
   - Does the network structure match Connected Papers?
   - Are key papers (high citations) correctly identified?
   - Is the overall topology similar?

4. **Additional Value** (0-10):
   - Does our tool provide additional insights beyond Connected Papers?
   - Are there unique features (e.g., timeline, annotations)?
   - Is the output format more useful?

**Output Format**: JSON
{{
  "scores": {{
    "paper_coverage": 0-10,
    "citation_accuracy": 0-10,
    "network_similarity": 0-10,
    "additional_value": 0-10
  }},
  "overall_score": 0-10,
  "detailed_comparison": {{
    "papers_in_both": ["paper1", "paper2"],
    "papers_only_in_connected_papers": ["paper3"],
    "papers_only_in_our_tool": ["paper4"],
    "citation_relationships_match": true/false,
    "key_differences": ["difference1", "difference2"]
  }},
  "strengths": ["strength1", "strength2"],
  "weaknesses": ["weakness1", "weakness2"],
  "recommendations": ["recommendation1", "recommendation2"]
}}"""
```

## 🚀 Quick Start

### 1. Environment Setup

```bash
# Copy environment variables template
cp .env.example .env

# Edit .env file and fill in your API keys
# - OPENAI_API_KEY: Gemini API key (via OpenAI-compatible interface)
# - OPENAI_EMBEDDING_API_KEY: OpenAI Embedding API key
# - SEMANTIC_SCHOLAR_API_KEY: Semantic Scholar API key (optional)
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run Tests

```bash
# Test Code Extractor
python test_code_extractor_new.py

# Test Evolution Mapper
python test_agent_evolution_mapper.py

# Test Planner-Worker
python test_planner_worker_full.py
```

### 4. Run Evaluation

```bash
# Code Extractor evaluation
cd evaluation/code_extractor
python scripts/evaluate_code_extractor.py --num-evaluations 3

# Evolution Mapper evaluation
cd evaluation/evolution_mapper
python scripts/evaluate_evolution_mapper.py --topic all --max-papers 15 --num-evaluations 3
```

## 📊 Evaluation Results

### Code Extractor Evaluation Status

- **Completed**: 6/10 papers
- **Average Standalone Score**: 2.17/10
- **Average Comparison Score**: 2.50/10
- **Best Performance**: Missing Components Detection (7.50/10)
- **Worst Performance**: Reproducibility Score Match (0.00/10)

Detailed results: `evaluation/code_extractor/RESULTS_ANALYSIS.md`

### Evolution Mapper Evaluation Status

- **Completed**: 3 topics (GAN, Transformers, ImageNet)
- Detailed results: `evaluation/evolution_mapper/results/EVALUATION_REPORT.md`

## 🔍 Evaluation Design

### Code Extractor Evaluation Design

**Three-Layer Evaluation System**:
1. **Ground Truth**: Manually annotated data for 10 papers
2. **Code Extractor Tool**: Extract code/pseudocode from PDFs and evaluate
3. **LLM Judge**: ChatGPT as evaluator (Standalone + Comparison)

**Evaluation Metrics**:
- Pseudocode Completeness Match
- Reproducibility Score Match
- Dependency Identification
- Model Structure Analysis
- Missing Components Detection

**Success Criteria**:
- Each metric > 7.0/10
- Overall Score > 7.0/10

### Evolution Mapper Evaluation Design

**Evaluation System**:
1. **Ground Truth**: BIB files from Connected Papers
2. **Evolution Mapper Tool**: Generate technology evolution maps
3. **LLM Judge**: ChatGPT as evaluator

**Evaluation Metrics**:
- Paper Discovery Coverage
- Citation Relationship Accuracy
- Network Structure Similarity
- Additional Value

**Success Criteria**:
- Each metric > 7.0/10
- Overall Score > 7.0/10

## 📚 Related Documentation

- **Code Extractor Design Document**: `evaluation/code_extractor/DESIGN.md`
- **Code Extractor Results Analysis**: `evaluation/code_extractor/RESULTS_ANALYSIS.md`
- **Evolution Mapper Evaluation Summary**: `evaluation/evolution_mapper/EVALUATION_SUMMARY.md`
- **Project Documentation**: `开发记录.md/12.2 项目说明.md` (Development Records)

## ⚠️ Important Notes

1. **API Keys**: Do not commit `.env` file, only commit `.env.example`
2. **Output Path**: All outputs unified to `./history/<timestamp>/`
3. **Temporary Files**: `temp_output/` directory has been added to `.gitignore`
4. **Evaluation Time**: Each paper evaluation takes 2-5 minutes (including multiple LLM evaluations)

## 📝 License

This project extends the original PaperArena project and follows the original project's license.

## 🤝 Contributing

This project is part of a team project and follows the team's Git workflow:
- Create feature branches from `main`
- Manage code through Issues and PRs
- Details: `开发记录.md/Git合并指南.md` (Git Merge Guide)

