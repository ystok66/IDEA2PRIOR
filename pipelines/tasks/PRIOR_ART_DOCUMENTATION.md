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
│   │  BLOCK 1: Candidate Discovery                                    │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  • LLM generates seed papers + search queries from idea          │  │
│   │  • OpenAlex retrieval with main + recent buckets                 │  │
│   │  • Citation graph expansion via Semantic Scholar                 │  │
│   │  • Output: 100-300 candidate papers in PipelineState             │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                  ↓                                      │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  BLOCK 2: Abstract-Level Scoring                                 │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  • Decompose idea into facets (problem/setting/mechanism/etc)    │  │
│   │  • Lexical pre-filter to reduce to ~80 candidates                │  │
│   │  • LLM relevance filter (30 papers -> keep ~25 relevant)         │  │
│   │  • LLM scoring: coverage_level (0-3), task/method similarity     │  │
│   │  • Output: Scored candidates with coverage classifications       │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                  ↓                                      │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  BLOCK 3: Fulltext Refinement (Top K papers)                     │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  • Download PDFs for top-scored papers                           │  │
│   │  • Parse into sections (intro, method, related_work, etc)        │  │
│   │  • Extract evidence snippets matching idea facets                │  │
│   │  • LLM Expert-level re-scoring with fulltext context             │  │
│   │  • Intro+RW summarization (parallel, max_workers=5)              │  │
│   │  • Output: Block 3 papers with evidence and summaries            │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                  ↓                                      │
│   ┌──────────────────────────────────────────────────────────────────┐  │
│   │  Report Generation                                                │  │
│   │  ────────────────────────────────────────────────────────────    │  │
│   │  • Render markdown report with coverage tables                   │  │
│   │  • Include evidence snippets with citations                      │  │
│   │  • Save to runs/<run_id>/synthesis/prior_art_report.md           │  │
│   └──────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Data Structures

### PriorArtConfig

```python
@dataclass
class PriorArtConfig:
    top_k_display: int = 20           # Papers to show in report
    fulltext_top_k: int = 10          # Papers to download PDFs for
    use_citation_graph: bool = True   # Enable citation expansion
    citation_depth: int = 1           # How deep to expand
    debug_mode: bool = False          # Limit LLM calls
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
    ├── prior_art_report.md        # Final report
    └── intro_rw_summaries.jsonl   # Block 3 summaries
```

---

## Debug Mode

When `--debug` is passed:

- Limits to 10 papers for LLM scoring
- Uses single worker for PDF downloads
- More verbose logging
- Faster iteration for development
