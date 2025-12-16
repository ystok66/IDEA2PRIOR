# Prior Art Check System Documentation

Prior Art Check is a multi-stage pipeline that systematically searches, filters, and analyzes academic literature to identify papers relevant to a research idea.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          run_prior_art_check()                          │
│                         (Main Orchestrator)                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  BLOCK 1: Candidate Discovery (~200 papers)                      │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  Step 1a: seed_papers_from_llm() - LLM回忆相关论文 (Expert)       │  │
│   │  Step 1b: seed_papers_from_web_search() - SerpAPI+DDG (可选)     │  │
│   │  Step 1c: validate_llm_seeds_via_openalex() - 5层验证策略        │  │
│   │  Step 2: generate_search_queries_from_idea() - 生成检索Query     │  │
│   │  Step 3: run_exploration_loop() - 多轮探索检索                   │  │
│   │  Step 4: retrieve_candidates_with_buckets() - 双桶检索           │  │
│   │  Step 5: refine_queries_llm() - 层级精炼 (若enable_hierarchical) │  │
│   │  Step 6: apply_citation_recency_quota() - 30-60%近期配额选择     │  │
│   │  (可选) expand_corpus_via_citation_graph() - 引用图扩展          │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                  ↓                                      │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  Facet Extraction (Block 2 前置)                                 │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  • decompose_idea_into_facets() → 3-7个技术Facet (Light LLM)     │  │
│   │  • extract_idea_features() → datasets/metrics/methods (Light)    │  │
│   │  • extract_high_level_facets_from_idea() → problem/setting/...   │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                  ↓                                      │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  BLOCK 2: Abstract-Level Scoring (score_papers_with_abstract_only)│ │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  Stage 2a: Lexical Pre-filter (prefilter_papers_for_llm_scoring) │  │
│   │    └─ simple_relevance_score(): 关键词在title +3, abstract +1    │  │
│   │    └─ 保留 score>0 的论文 + 所有seed papers                      │  │
│   │    └─ 输出: ~80篇                                                │  │
│   │  Stage 2b: LLM Relevance Filter (llm_relevance_prefilter)        │  │
│   │    └─ Light LLM: is_relevant true/false                          │  │
│   │    └─ 并行执行 (cfg.max_workers_llm_prefilter=4)                 │  │
│   │    └─ 输出: ~60-80篇 (过滤约20-30%)                              │  │
│   │  Stage 2c: LLM Coverage Scoring (核心评分循环)                   │  │
│   │    └─ Light LLM: coverage_level (0-3)                            │  │
│   │    └─ covered_facets: ["F1", "F3", ...]                          │  │
│   │    └─ task_similarity, method_similarity: 0.0-1.0                │  │
│   │    └─ high_level_facets: {problem/setting/mechanism/assumption}  │  │
│   │    └─ 并行执行 (cfg.max_workers_llm_scoring=20)                  │  │
│   │    └─ 排序: coverage > task_sim > method_sim > facet > citations │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                  ↓                                      │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  BLOCK 3: Fulltext Refinement (refine_top_papers_with_fulltext)  │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  Step 3.1: 选取Top-K (cfg.fulltext_top_k=20, debug=5)            │  │
│   │  Step 3.2: PDF下载与解析 (enrich_papers_with_fulltext)           │  │
│   │    └─ 重试逻辑: 失败则继续下一个候选, 直到达到top_k              │  │
│   │    └─ 并行执行 (cfg.max_workers_pdf=5)                           │  │
│   │  Step 3.3: 生成Evidence Keywords                                 │  │
│   │    └─ generate_evidence_keywords_from_idea() → 30个关键词        │  │
│   │    └─ make_keywords_for_evidence() → 合并facets+features         │  │
│   │  Step 3.4: 提取Evidence Snippets (find_evidence_snippets)        │  │
│   │    └─ BM25搜索paper.chunks                                       │  │
│   │    └─ 返回top-3匹配段落                                          │  │
│   │  Step 3.5: LLM Refinement (全文精炼评分)                         │  │
│   │    └─ 输入: abstract + snippets                                  │  │
│   │    └─ 输出: refined_coverage_level, role                         │  │
│   │    └─ role: main_contribution|key_technique|background|irrelevant│  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                  ↓                                      │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  Post-Processing (Block 3后处理)                                 │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  Step 3.6: Intro+RW Summarization                                │  │
│   │    └─ summarize_intro_rw_for_block3()                            │  │
│   │    └─ 对coverage_level>=2的论文提取完整Intro+RW                  │  │
│   │  1.3: Method/Experiment Snippets                                 │  │
│   │    └─ build_method_experiment_snippets()                         │  │
│   │    └─ BM25+LLM按facet提取Method/Experiment段落                   │  │
│   │    └─ 输出: synthesis/method_exp_snippets.jsonl                  │  │
│   │  1.4: Key Papers Selection & Intro+RW Full                       │  │
│   │    └─ select_key_papers_and_dump_intro_rw_full()                 │  │
│   │    └─ 3个桶: top_cited (1+2), latest (1+2), survey (1+2)         │  │
│   │    └─ 输出: synthesis/key_papers_selection.json                  │  │
│   │    └─ 输出: synthesis/intro_rw_full/{paper_id}.json (完整全文)   │  │
│   │  2.1: Synthesis JSON Builder                                     │  │
│   │    └─ build_synthesis_input()                                    │  │
│   │    └─ 输出: synthesis/synthesis_input.json                       │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                  ↓                                      │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  Report Generation (render_prior_art_report)                     │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  • 分桶: LLM coverage_level为主 (high/partial/adjacent)          │  │
│   │  • 排序: task_similarity → citation_count                        │  │
│   │  • 渲染: Title, Venue, Year, DOI, Covered Facets, Evidence       │  │
│   │  • Facet Gap Analysis: 每个facet的覆盖统计                       │  │
│   │  • 保存: runs/<run_id>/md_reports/prior_art_*.md                 │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Module Structure (Refactored Dec 2024)

The prior art system is organized into modular files with clear separation of concerns:

```
pipelines/tasks/
├── prior_art_check.py       # Main orchestrator (3 functions)
├── prior_art_retrieval.py   # Block 1: Candidate discovery (11 functions)
├── prior_art_scoring.py     # Block 2+3: Scoring & evidence (17 functions)
├── prior_art_fulltext.py    # Block 3: Fulltext enhancements (1.3/1.4) 🆕
├── prior_art_synthesis.py   # Part 2: Synthesis JSON builder (2.1) 🆕
├── prior_art_report.py      # Block 4: Report rendering (8 functions)
├── prior_art_types.py       # Shared dataclasses & constants
└── prior_art_llm_utils.py   # LLM helper functions
```

### Module Responsibilities

| Module                     | Responsibility                                                               | Key Functions                                                                                            |
| -------------------------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| **prior_art_check.py**     | Main orchestrator, entry point                                               | `run_prior_art_check()`, `build_prior_art_intent()`                                                      |
| **prior_art_retrieval.py** | LLM seed generation, OpenAlex search, citation graph                         | `seed_papers_from_llm()`, `retrieve_candidates_with_buckets()`, `expand_corpus_via_citation_graph()`     |
| **prior_art_scoring.py**   | Facet extraction, pre-filtering, abstract/fulltext scoring, evidence finding | `decompose_idea_into_facets()`, `score_papers_with_abstract_only()`, `refine_top_papers_with_fulltext()` |
| **prior_art_fulltext.py**  | 1.3 Method/Exp snippets, 1.4 Key Papers & Intro+RW Full                      | `build_method_experiment_snippets()`, `select_key_papers_and_dump_intro_rw_full()`                       |
| **prior_art_synthesis.py** | 2.1 Synthesis JSON builder with prompt templates                             | `build_synthesis_input()`, `make_prompt_templates()`                                                     |
| **prior_art_report.py**    | Pure rendering functions for Markdown report                                 | `render_prior_art_report()`, `render_structural_coverage_section()`                                      |
| **prior_art_types.py**     | `PriorArtConfig`, `PriorArtMatch`, constants                                 | `HIGH_LEVEL_FACET_TYPES`, `GENERIC_STOPWORDS`                                                            |
| **prior_art_llm_utils.py** | JSON repair, text extraction from LLM responses                              | `extract_text_from_llm_response()`, `repair_json_output()`                                               |

### Dependency Rules

- **Orchestrator** (`prior_art_check.py`) imports all submodules
- **Submodules** do NOT import `prior_art_check` (no circular dependencies)
- **Report module** has no LLM/network calls (pure rendering)
- **Types module** is imported by all other modules

---

## Key Data Structures

### PriorArtConfig

```python
@dataclass
class PriorArtConfig:
    # Display
    top_k_display: int = 20           # Papers to show in report
    fulltext_top_k: int = 10          # Papers to download PDFs for
    debug_mode: bool = False          # Limit data scale (not workers)

    # Concurrency (local workers, NOT API rate)
    max_workers_openalex: int = 4     # OpenAlex seed validation
    max_workers_s2: int = 4           # Semantic Scholar citation graph
    max_workers_pdf: int = 5          # PDF download/parse
    max_workers_llm_scoring: int = 20 # LLM scoring parallelism
    max_workers_llm_prefilter: int = 4

    # Dual-bucket retrieval (citation vs recency balance)
    recent_year_threshold: int = 2024     # "Recent bucket" = 2024+
    recent_min_quota_ratio: float = 0.3   # At least 30% from recent
    recent_max_quota_ratio: float = 0.6   # At most 60% from recent

    # Scoring weights for ranking
    weight_relevance: float = 0.6   # Relevance score weight
    weight_citation: float = 0.25   # Citation count weight
    weight_recency: float = 0.15    # Recency weight
```

### Rate Limiting (Dec 2024) 🆕

API throttling is handled at the **client layer** via sliding-window rate limiters, not batch-level sleeps:

| Service              | Location                                       | Max Calls | Period | Notes              |
| -------------------- | ---------------------------------------------- | --------- | ------ | ------------------ |
| **OpenAlex**         | `tools/openalex_search.py`                     | 8         | 1.0s   | Polite rate        |
| **Semantic Scholar** | `pipelines/clients/semantic_scholar_client.py` | 1         | 2.0s   | Conservative       |
| **LLM**              | `pipelines/tasks/prior_art_scoring.py`         | 20        | 1.0s   | Safety net         |
| **PDF Download**     | `pipelines/tasks/prior_art_scoring.py`         | 5         | 1.0s   | Avoid server block |

**Implementation**: `pipelines/rate_limiter.py` - Thread-safe `SimpleRateLimiter` class with sliding-window logic.

**Pattern**:

```python
# Each task calls wait_for_slot() before making API request
rate_limiter.wait_for_slot()  # Blocks if rate exceeded
response = requests.get(...)  # Then make request
```

### PipelineState (from pipelines/pipeline_state.py)

- `state.papers: Dict[str, Paper]` - All discovered papers
- `state.paper_ids: List[str]` - Ordered list of IDs

### Paper (from pipelines/paper.py)

- Core fields: `id`, `title`, `year`, `abstract`, `doi`, `arxiv_id`
- Scoring: `coverage_level` (0-3), `task_similarity`, `method_similarity`
- Fulltext: `chunks`, `local_pdf_path`

---

## Function Groups

### 1. LLM Utilities

| Function                            | Purpose                                            |
| ----------------------------------- | -------------------------------------------------- |
| `_extract_text_from_llm_response()` | Extract clean text from LLM responses              |
| `_repair_json_output()`             | Fix common JSON issues (trailing commas, comments) |
| `call_llm_json()`                   | Call LLM and parse JSON response with repair       |

### 2. Seed & Query Generation

| Function                              | Purpose                                 |
| ------------------------------------- | --------------------------------------- |
| `seed_papers_from_llm()`              | Ask LLM for known relevant paper titles |
| `validate_llm_seeds_via_openalex()`   | Verify seed papers exist in OpenAlex    |
| `generate_search_queries_from_idea()` | Generate 5-10 search queries from idea  |
| `generate_seed_queries_llm()`         | Alternative query generation method     |

### 3. Block 1 Retrieval

| Function                             | Purpose                                      |
| ------------------------------------ | -------------------------------------------- |
| `retrieve_candidates_with_buckets()` | Run queries against OpenAlex (main + recent) |
| `_parse_openalex_results()`          | Parse raw OpenAlex JSON to Paper objects     |
| `apply_citation_recency_quota()`     | Balance old/new papers (30% recent)          |
| `expand_corpus_via_citation_graph()` | Add refs/citations via Semantic Scholar      |
| `_add_s2_paper_to_state()`           | Add S2 paper to state                        |

### 4. Facet Extraction

| Function                                 | Purpose                                    |
| ---------------------------------------- | ------------------------------------------ |
| `decompose_idea_into_facets()`           | Extract 5 fine-grained facets from idea    |
| `extract_idea_features()`                | Extract datasets, metrics, method families |
| `extract_high_level_facets_from_idea()`  | Get problem/setting/mechanism/assumption   |
| `extract_high_level_facets_from_paper()` | Same but for a paper                       |
| `compute_structural_overlap()`           | Compare idea features to paper             |
| `compute_high_level_facet_overlap()`     | Compare high-level facets                  |

### 5. Scoring & Filtering

| Function                                  | Purpose                               |
| ----------------------------------------- | ------------------------------------- |
| `generate_relevance_keywords_from_idea()` | Generate 10-15 keywords for filtering |
| `simple_relevance_score()`                | Fast lexical scoring for prefilter    |
| `prefilter_papers_for_llm_scoring()`      | Reduce candidates to 80               |
| `llm_relevance_prefilter()`               | LLM decides if paper is on-topic      |
| `score_papers_with_abstract_only()`       | Main LLM scoring (coverage_level 0-3) |

### 6. Fulltext Processing

| Function                                 | Purpose                              |
| ---------------------------------------- | ------------------------------------ |
| `enrich_papers_with_fulltext()`          | Download PDFs, parse sections        |
| `generate_evidence_keywords_from_idea()` | Keywords for evidence search         |
| `make_keywords_for_evidence()`           | Expand keywords with synonyms        |
| `find_evidence_snippets()`               | Find text snippets matching keywords |
| `refine_top_papers_with_fulltext()`      | Re-score with fulltext context       |

### 7. Report Rendering

| Function                               | Purpose                                       |
| -------------------------------------- | --------------------------------------------- |
| `render_structural_coverage_section()` | Render datasets/metrics coverage table        |
| `render_high_level_coverage_section()` | Render facet coverage table                   |
| `_count_coverage()`                    | Count papers by coverage level                |
| `_bucketize()`                         | Assign coverage label (High/Partial/Adjacent) |
| `_clean_note_for_report()`             | Clean LLM notes for display                   |
| `render_prior_art_report()`            | Generate full markdown report                 |

---

## Coverage Levels

| Level | Label        | Meaning                                      |
| ----- | ------------ | -------------------------------------------- |
| 3     | High Overlap | Paper directly addresses same problem+method |
| 2     | Partial      | Shares some facets but different approach    |
| 1     | Adjacent     | Related domain but different focus           |
| 0     | Ignored      | Not relevant enough to display               |

---

## Block 2 详解: Abstract-Level Scoring

Block 2 (`score_papers_with_abstract_only`) 是评分的核心阶段，包含 **3 个子阶段**：

### 整体流程

```
┌──────────────────────────────────────────────────────────────────────────┐
│  输入: ~200 papers (按引用数排序)                                        │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Stage 2a: Lexical Prefilter (prefilter_papers_for_llm_scoring)          │
│  • 用 LLM 生成 20 个关键词 (generate_relevance_keywords_from_idea)       │
│  • 对每篇论文计算 simple_relevance_score (标题+摘要匹配)                  │
│  • 保留 score > 0 的论文 (最多 cfg.prefilter_max_keep=80) + 所有seeds    │
│  • 输出: ~80 papers                                                       │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Stage 2b: LLM Relevance Filter (llm_relevance_prefilter)                │
│  • 输入: top N papers (cfg.llm_prefilter_top_n=100)                       │
│  • Light LLM 判断 is_relevant: true/false                                 │
│  • 并行执行 (cfg.max_workers_llm_prefilter=4)                             │
│  • 输出: ~60-80 papers (过滤掉约 20-30%)                                  │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Stage 2c: LLM Coverage Scoring                                          │
│  • 限制到 max_papers (初始 80, citation 扩展后 100)                       │
│  • Merged LLM Call 同时完成: 评分 + Facet提取                             │
│  • 并行执行 (cfg.max_workers_llm_scoring=20)                              │
│  • 输出: List[PriorArtMatch] 排序后                                       │
└──────────────────────────────────────────────────────────────────────────┘
```

### Stage 2a: Lexical Scoring Algorithm

`simple_relevance_score()` 评分规则：

| 匹配类型                   | 得分 |
| -------------------------- | ---- |
| 关键词在标题中             | +3.0 |
| 关键词在摘要中             | +1.0 |
| 强线索词在标题中           | +2.0 |
| 强线索词在摘要中           | +0.5 |
| Survey/Review (若启用惩罚) | -2.0 |

**特殊逻辑**: Seed papers (来自 LLM 或 Web Search 的种子) **始终保留**，不受分数影响。

### Stage 2b: LLM Relevance Filter

`llm_relevance_prefilter()` 使用 Light LLM 进行快速相关性判断：

**输入**: Paper title + abstract + idea description
**输出**: `is_relevant: true/false`

```
Prompt 结构:
"Given the research idea: {idea_description}

Determine if this paper is relevant to the idea.
Paper: {title}
Abstract: {abstract}

Answer with ONLY 'true' or 'false'."
```

**筛选规则**:

- `is_relevant=true` → 保留
- `is_relevant=false` → 过滤掉
- 解析失败 → 保守保留

**并行执行**: `cfg.max_workers_llm_prefilter=4`

### Stage 2c: LLM 评分输出结构

```python
{
  "coverage_level": 0-3,           # 核心: 与idea的重叠程度
  "covered_facets": ["F1", "F3"],  # 覆盖了哪些 facet
  "task_similarity": 0.0-1.0,      # 任务相似度
  "method_similarity": 0.0-1.0,    # 方法相似度
  "notes": "...",                   # 简短说明
  "high_level_facets": {           # 论文自身的结构facet
    "problem_facets": [...],
    "setting_facets": [...],
    "mechanism_facets": [...],
    "assumption_facets": [...]
  }
}
```

### 最终排序规则

```python
matches.sort(
    key=lambda m: (
        m.coverage_level,              # 第一优先: 覆盖等级
        m.task_similarity,             # 第二优先: 任务相似度
        m.method_similarity,           # 第三优先: 方法相似度
        m.high_level_facet_score,      # 第四优先: facet 匹配分
        min(citation_count, 200)       # 第五优先: 引用数(上限200)
    ),
    reverse=True
)
```

### Block 2 配置参数

| 参数                                  | 默认值 | 说明                   |
| ------------------------------------- | ------ | ---------------------- |
| `enable_prefilter`                    | `True` | 是否启用词法预过滤     |
| `prefilter_max_keep`                  | `80`   | 词法过滤后最多保留     |
| `prefilter_max_keywords`              | `20`   | 生成的关键词数量       |
| `enable_llm_prefilter`                | `True` | 是否启用 LLM 预过滤    |
| `llm_prefilter_top_n`                 | `100`  | LLM 预过滤处理的论文数 |
| `max_abstract_scoring_initial`        | `80`   | 初始阶段评分论文数     |
| `max_abstract_scoring_after_citation` | `100`  | 引用扩展后评分论文数   |
| `max_workers_llm_scoring`             | `20`   | 评分并行 worker 数     |
| `max_workers_llm_prefilter`           | `4`    | 预过滤并行 worker 数   |

### 打分策略对比 (Scoring Strategy Summary)

| 阶段               | 方法            | 输入                      | 输出                       | 速度    | 精度 |
| ------------------ | --------------- | ------------------------- | -------------------------- | ------- | ---- |
| **2a Lexical**     | BM25/关键词匹配 | Title + Abstract          | score (0-N)                | ⚡ 最快 | 低   |
| **2b LLM Filter**  | Light LLM       | Title + Abstract + Idea   | true/false                 | 中      | 中   |
| **2c LLM Scoring** | Light LLM       | Title + Abstract + Facets | coverage 0-3, similarity   | 慢      | 高   |
| **3 Fulltext**     | Light LLM       | Abstract + **Snippets**   | refined coverage, **role** | 最慢    | 最高 |

---

## Block 3 详解: Fulltext Refinement

Block 3 (`refine_top_papers_with_fulltext`) 对 Top-K 论文进行全文分析：

### 整体流程

```
┌──────────────────────────────────────────────────────────────────────────┐
│  输入: Block 2 评分后的 matches (已按分数排序)                            │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Step 3.1: 选取 Top-K 候选论文                                           │
│  • 按 coverage_level, facet_score, structural_score, citations 排序     │
│  • 目标 top_k = cfg.fulltext_top_k (默认20, debug=5)                     │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Step 3.2: PDF 下载与解析 (enrich_papers_with_fulltext)                  │
│  • URL解析: arXiv URL → S2 → OpenAlex → Unpaywall → DDG                  │
│  • 重试逻辑: 如果 PDF 失败, 继续尝试下一个候选                            │
│  • 并行执行 (cfg.max_workers_pdf=5)                                       │
│  • 解析: PDF → sections (intro/method/experiments/related_work/...)      │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Step 3.3: 生成 Evidence Keywords                                        │
│  • generate_evidence_keywords_from_idea() → 30 个关键词 (Expert LLM)      │
│  • make_keywords_for_evidence() → 合并 facets + features                 │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Step 3.4: 提取 Evidence Snippets (find_evidence_snippets)               │
│  • 对每篇论文的 chunks 进行 BM25 搜索                                     │
│  • 提取 top 3 个匹配 evidence 的句子/段落                                 │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Step 3.5: LLM Refinement (全文精炼评分)                                  │
│  • 输入: abstract + evidence snippets                                     │
│  • 输出:                                                                  │
│    - refined_coverage_level: 0-3 (可能调整原有评分)                       │
│    - role: main_contribution | key_technique | background | irrelevant   │
│    - notes: 精炼后的说明                                                  │
└──────────────────────────────────────────────────────────────────────────┘
```

### Role 字段含义

| Role                | 含义                                             |
| ------------------- | ------------------------------------------------ |
| `main_contribution` | 论文主要贡献与 idea 高度重叠（最危险，潜在冲突） |
| `key_technique`     | 论文使用了类似的关键技术                         |
| `background`        | 作为背景参考，需引用                             |
| `irrelevant`        | 经全文分析后判定不相关                           |

### Block 2 vs Block 3 对比

| 维度             | Block 2 (Abstract-Only) | Block 3 (Fulltext)               |
| ---------------- | ----------------------- | -------------------------------- |
| **输入**         | Title + Abstract        | Title + Abstract + Snippets      |
| **处理数量**     | 80-100 篇               | 20 篇 (top_k)                    |
| **输出**         | coverage_level, facets  | refined_coverage_level, **role** |
| **LLM 调用次数** | 多 (全部候选)           | 少 (仅 top-K)                    |
| **成本**         | 中                      | 高 (PDF 下载+解析)               |

### Block 3 配置参数

| 参数                   | 默认值 | 说明                    |
| ---------------------- | ------ | ----------------------- |
| `fulltext_top_k`       | `20`   | Block 3 处理论文数      |
| `debug_fulltext_top_k` | `5`    | Debug 模式数量          |
| `max_workers_pdf`      | `5`    | PDF 下载并行数          |
| `evidence_bm25_top_k`  | `3`    | 每篇论文提取 snippet 数 |

---

## 1.4 Key Papers Selection 详解

`select_key_papers_and_dump_intro_rw_full()` 选择关键论文并导出完整 Intro+RW：

### 选择流程

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Step 1: 构建 3 个候选池                                                  │
│  • top_cited_pool: 按引用数排序前 K 篇 (cfg.key_paper_top_cited_k=5)      │
│  • latest_pool: 按年份排序最新 K 篇    (cfg.key_paper_latest_k=5)         │
│  • survey_pool: 标题含 survey/review   (cfg.key_paper_survey_k=5)         │
│  • 仅包含 has_fulltext=True 的论文                                        │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Step 2: LLM/启发式 选择 primary + alternates                            │
│  对每个 bucket:                                                           │
│  • primary: 1 篇最具代表性                                                │
│  • alternates: 0-2 篇补充视角                                             │
│  • Debug 模式: 使用简单启发式 (第1篇为primary, 2-3篇为alternates)         │
└──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Step 3: 导出 Intro+RW 全文 JSON                                         │
│  • 使用 Anchor+Window 算法提取完整 Introduction 和 Related Work 章节      │
│  • 保存到: synthesis/intro_rw_full/{paper_id}.json                        │
└──────────────────────────────────────────────────────────────────────────┘
```

### 实际选出的论文数量

- 每个 bucket 最多选 **3 篇** (1 primary + 2 alternates)
- 3 个 bucket 总共最多 **9 篇**
- 由于论文可能在多个 bucket 中重复出现，会去重
- 实际导出完整 Intro+RW 全文的论文数量通常在 **5-9 篇**

### 输出文件结构

```
runs/{timestamp}_{topic}/
└── synthesis/
    ├── key_papers_selection.json     # 选择结果元数据
    └── intro_rw_full/
        ├── paper_id_1.json           # 完整 Intro+RW
        ├── paper_id_2.json
        └── paper_id_3.json
```

### key_papers_selection.json 示例

```json
{
  "topic": "monocular_3d_object_detection",
  "buckets": {
    "top_cited": {
      "primary": "10.1234/xxx",
      "alternates": ["10.5678/yyy", "10.9012/zzz"]
    },
    "latest": {
      "primary": "arXiv:2412.12345",
      "alternates": []
    },
    "survey": {
      "primary": "10.1111/survey123",
      "alternates": []
    }
  },
  "per_paper_roles": {
    "10.1234/xxx": ["top_cited_primary"],
    "arXiv:2412.12345": ["latest_primary"]
  }
}
```

### intro_rw_full/{paper_id}.json 结构

```json
{
  "paper_id": "10.1234/xxx",
  "title": "Paper Title",
  "year": 2024,
  "venue": "CVPR",
  "citation_count": 150,
  "sections": {
    "intro": {
      "heading": "1. Introduction",
      "text": "完整的 Introduction 章节全文..."
    },
    "related_work": {
      "heading": "2. Related Work",
      "text": "完整的 Related Work 章节全文..."
    }
  },
  "meta": {
    "selected_as": ["top_cited_primary"],
    "missing_intro": false,
    "missing_related_work": false
  }
}
```

---

## Block 3 Enhancements (Dec 2024) 🆕

### Overview

Block 3 provides advanced fulltext processing for top-scored papers:

| Feature                      | Description                                                                   | Output                                              |
| ---------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------- |
| **1.3 Method/Exp Snippets**  | Extract paragraphs from Method/Experiment sections using BM25 + LLM filtering | `method_exp_snippets.jsonl`                         |
| **1.4 Key Papers Selection** | Select top_cited / latest / survey papers for detailed analysis               | `key_papers_selection.json`, `intro_rw_full/*.json` |
| **2.1 Synthesis JSON**       | Consolidate all results into a single JSON with prompt templates              | `synthesis_input.json`                              |

### 1.3 Method/Experiment Snippets

Uses BM25 to find relevant paragraphs, then LLM-light to filter and summarize:

```python
build_method_experiment_snippets(state, facets, idea_features, run_mgr, config)
```

**Output**: `synthesis/method_exp_snippets.jsonl`

```json
{
  "paper_id": "...",
  "facet_label": "F1",
  "bullet": "...",
  "snippet": "...",
  "section_role": "method"
}
```

### 1.4 Key Papers & Intro+RW Full

Selects key papers (top_cited, latest, survey) and exports full Intro+RW text:

```python
select_key_papers_and_dump_intro_rw_full(agent, state, task_root, config)
```

**Anchor + Window Collection**: The Intro/RW extraction uses "anchor + window" logic to capture complete sections including subsections. For example, "1. Introduction" → collects until "2. Related Work" is detected.

**Outputs**:

- `synthesis/key_papers_selection.json` - Buckets and per-paper roles
- `synthesis/intro_rw_full/{paper_id}.json` - Full Intro + Related Work text

### 2.1 Synthesis JSON Builder

Consolidates all Block 1-3 results into a single self-contained JSON:

```python
build_synthesis_input(
    state,
    config,
    run_dir,
    facets,
    user_query,
    retrieval_queries,
    coverage_levels,    # Dict[str, int] from coverage_classifier
    scored_matches,     # List[PriorArtMatch] from LLM scoring
)
```

**Key Data Flow (Dec 2024 Fix)**:

- `coverage_levels` from `classify_all_papers()` is the **single source of truth** for bucket assignment
- `scored_matches` provides `facet_labels`, `task_similarity`, `method_similarity`
- `facet_coverage` uses fuzzy keyword matching (2+ word overlap) to map LLM-generated labels to facet IDs

**Output**: `synthesis/synthesis_input.json`

**Structure**:

```json
{
  "metadata": {
    "topic": "...",
    "idea_description": "...",
    "facets": [...],
    "task": {"task_type": "prior_art_check", "user_query": "...", "user_intent_notes": [...], "constraints": {}},
    "audience": "expert_researcher",
    "usage_context": ["paper_introduction", "related_work_section"]
  },
  "global_stats": {
    "num_candidates": 86,
    "num_block3": 5,
    "coverage_by_level": {"3": 0, "2": 2, "1": 3, "0": 81},
    "facet_coverage": [
      {"facet_id": "F1", "facet_label": "...", "strong_papers": 2, "total_papers": 4}
    ]
  },
  "retrieval_summary": {"query_seeds": [...], "facet_queries": {...}, "sources": [...], "exploration_notes": {...}},
  "limitations": {"retrieval_coverage": "not_exhaustive", "llm_noise": true, "notes": [...]},
  "buckets": {"high_overlap": [...], "partial_overlap": [...], "adjacent": [...], "ignored": [...]},
  "papers": [
    {
      "paper_id": "...",
      "title": "...",
      "overlap_level": 2,
      "overlap_bucket": "partial_overlap",
      "facet_labels": ["F1", "F3"],
      "task_similarity": 0.8,
      "method_similarity": 0.7,
      "intro_rw_full": {...},
      "method_exp_snippets": [...]
    }
  ],
  "prompt_templates": {"full_markdown_report": "...", "short_related_work_section": "..."}
}
```

**Usage**: The JSON is self-contained. Any LLM/tool can use `prompt_templates.full_markdown_report` + the JSON to generate a full prior art report.

---

## PDF Download Robustness (Dec 2024) 🆕

### Overview

Enhanced PDF download mechanisms to maximize fulltext acquisition rate:

| Enhancement                 | Description                                                   | Location                            |
| --------------------------- | ------------------------------------------------------------- | ----------------------------------- |
| **Retry Logic**             | Auto-retry additional papers if some fail to download         | `refine_top_papers_with_fulltext()` |
| **Abstract Fallback**       | Include abstract in synthesis_input.json even if PDF fails    | `build_synthesis_input()`           |
| **OpenAlex ID Passthrough** | Pass openalex_id to resolver for better OA coverage           | `ensure_pdf_url()`                  |
| **Debug Logging**           | Log each PDF source attempt (arXiv→S2→OpenAlex→Unpaywall→DDG) | `resolve_pdf_and_metadata()`        |

### PDF Download Retry Logic

If the initial `fulltext_top_k` papers have download failures, the system automatically tries subsequent papers:

```python
# refine_top_papers_with_fulltext() in prior_art_scoring.py
while len(successful_pids) < top_k and candidate_idx < max_attempts:
    # Try batch of papers
    enrich_papers_with_fulltext(state, batch_pids)

    # Check which succeeded
    for pid in batch_pids:
        if paper.chunks or paper.has_fulltext:
            successful_pids.append(pid)

    # Continue with next batch if needed
    candidate_idx = batch_end
```

**Result**: Target `fulltext_top_k=20` can still be achieved even if some initial papers fail.

### synthesis_input.json Abstract Field

Each paper entry now **always** includes the abstract:

```json
{
  "paper_id": "10.1109/CVPR.2021.00845",
  "title": "Categorical Depth Distribution Network...",
  "abstract": "Monocular 3D object detection is a key problem...", // 🆕 Always present
  "overlap_level": 2,
  "intro_rw_policy": "summary_only"
}
```

This ensures LLMs have context even when PDF parsing fails.

### Multi-Source PDF Resolution Order

`PaperResolverTool.resolve_pdf_and_metadata()` attempts sources in priority:

1. **arXiv** (direct PDF): Fastest, highest confidence
2. **Semantic Scholar**: Rich metadata + OA links
3. **OpenAlex**: Good OA coverage via `best_oa_location`
4. **Unpaywall**: DOI-based fallback
5. **DDG Search** (optional): Last resort, requires `enable_web_pdf_fallback=True`

Debug logging shows each attempt:

```
[PaperResolver] Trying source 1/5: arXiv (id=2408.11958)
[ensure_pdf_url] Resolved: CARLA Drone... -> https://arxiv.org/pdf/2408.11958.pdf
```

---

## Output Artifacts

Each run creates:

```
runs/<run_id>/
├── config.json                    # Run configuration
├── state.json                     # Full pipeline state
├── pdfs/                          # Symlinks to cached PDFs
├── pdf_sections/                  # Parsed section JSONs
│   └── {paper_id}.sections.json
└── synthesis/
    ├── prior_art_report.md           # Final markdown report
    ├── intro_rw_summaries.jsonl      # Block 3 Intro/RW summaries
    ├── method_exp_snippets.jsonl     # 1.3 Method/Experiment snippets 🆕
    ├── key_papers_selection.json     # 1.4 Key papers with roles 🆕
    ├── intro_rw_full/                # 1.4 Full Intro+RW for key papers 🆕
    │   └── {paper_id}.json
    └── synthesis_input.json          # 2.1 Consolidated JSON + prompts 🆕
```

---

## Debug Mode

When `--debug` is passed:

- Limits to 10 papers for LLM scoring (via `get_max_abstract_to_score()`)
- Reduces fulltext refinement to top 5 papers
- Uses fewer seed queries (3 instead of 6)
- **Does NOT reduce worker counts** - concurrency is maintained for testing
- More verbose logging for troubleshooting

**Note**: Rate limiting is always active via `SimpleRateLimiter`, regardless of debug mode.

---

## Concurrency & Rate Limiting Summary

| Stage           | Workers | Rate Limiter   |
| --------------- | ------- | -------------- |
| Seed Validation | 4       | openalex: 8/1s |
| Citation Graph  | 4       | s2: 1/2s       |
| PDF Download    | 5       | pdf: 5/1s      |
| LLM Scoring     | 20      | llm: 20/1s     |
| LLM Prefilter   | 4       | llm: 20/1s     |

**Pattern**: Single `ThreadPoolExecutor` per stage, each task calls `rate_limiter.wait_for_slot()` before API request.

---

## Excluded Paper Feature (Blind Evaluation)

### Purpose

The excluded paper feature enables "blind evaluation" experiments where a target paper (and its ideas) are held out from the pipeline. This allows testing the system's ability to independently discover similar work without prior knowledge of the excluded paper.

### Configuration

Add excluded papers to `PriorArtConfig`:

```python
cfg = PriorArtConfig(
    excluded_papers=[
        {
            "title": "3DRot: 3D Rotation Augmentation for RGB-Based 3D Tasks",
            "doi": "10.48550/arXiv.2401.12345",
            "arxiv_id": "2401.12345",
            "s2_id": "xxx",
            "openalex_id": "W123456789",
        }
    ]
)
```

### Matching Strategies

The `is_excluded_paper()` function uses multiple strategies:

1. **Exact ID Match**: DOI, arXiv ID, S2 ID, OpenAlex ID (case-insensitive)
2. **Normalized Title Match**: Unicode normalization, lowercase, alphanumeric only
3. **Substring Match**: For long titles (>20 chars), handles subtitles

### Integration Points

Filtering is applied at 10 locations across the pipeline:

| Module                   | Function                    | Description                              |
| ------------------------ | --------------------------- | ---------------------------------------- |
| `prior_art_retrieval.py` | `_parse_openalex_results()` | Filters OpenAlex search results          |
| `prior_art_retrieval.py` | `_add_s2_paper_to_state()`  | Filters citation graph neighbors         |
| `exploration_loop.py`    | `discover_papers_round()`   | Filters exploration loop results         |
| `prior_art_check.py`     | seed filtering              | Filters before and after seed validation |
| `prior_art_synthesis.py` | `build_synthesis_input()`   | Filters papers in synthesis JSON         |
| `prior_art_report.py`    | `render_prior_art_report()` | Filters papers in final report           |

### Experiment Output

When excluded papers are configured:

1. **config.json** includes `excluded_papers_for_eval` for traceability
2. Log messages show filtered papers: `[ExcludedPaper] Filtered X paper(s) from ...`
3. Reports and synthesis JSON will not contain excluded papers

### Utility Functions

Located in `prior_art_utils.py`:

```python
normalize_title_for_matching(title: str) -> str
is_excluded_paper(paper, config: PriorArtConfig) -> bool
filter_excluded_papers(papers, config, desc="papers") -> list
```
