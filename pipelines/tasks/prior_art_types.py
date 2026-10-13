"""
Prior Art Check - Shared Types and Constants

This module contains dataclasses, configuration, and constants shared across
all prior_art modules:
- prior_art_check.py (orchestrator)
- prior_art_retrieval.py (Block 1 search)
- prior_art_scoring.py (Block 2/3 scoring)
- prior_art_report.py (report rendering)
- prior_art_llm_utils.py (LLM helpers)

These are separated to avoid circular import issues between modules.
"""

from __future__ import annotations

from typing import List, Optional, Dict
from dataclasses import dataclass, field


# =============================================================================
# CONSTANTS
# =============================================================================

# High-level facet categories for idea/paper comparison
# Used by extract_high_level_facets_from_* functions in scoring module
HIGH_LEVEL_FACET_TYPES = [
    "problem_facets",     # What problem is it solving?
    "setting_facets",     # What is the setting/protocol?
    "mechanism_facets",   # Key mechanisms/frameworks
    "assumption_facets",  # Key assumptions
]

# Generic terms that should be down-weighted in relevance scoring
# These appear in many papers regardless of specific topic
# Used by simple_relevance_score() in scoring module
GENERIC_STOPWORDS = {
    "deep learning", "neural network", "neural networks", "machine learning",
    "artificial intelligence", "survey", "review", "comprehensive review",
    "overview", "tutorial", "benchmark", "state of the art", "sota",
    "convolutional neural network", "cnn", "transformer", "attention",
    "end to end", "end-to-end", "data-driven", "learning-based",
}


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class PriorArtMatch:
    """
    Scoring result for how much one paper overlaps with the research idea.
    
    Created by score_papers_with_abstract_only() and refined by 
    refine_top_papers_with_fulltext() in the scoring module.
    
    Attributes:
        paper_id: Unique identifier (DOI or other ID)
        coverage_level: 0=none, 1=adjacent, 2=partial, 3=high overlap
        covered_facet_labels: Raw facet labels like ["F1", "F3"] for counting
        covered_facets: Human-readable facet descriptions
        notes: LLM-generated notes explaining the match
        structural_score: Score based on dataset/metric overlap
        structural_overlap: List of overlapping features
        high_level_facet_score: Score based on facet overlap
        high_level_facet_matches: Matching facets by category
        role: Paper's role (main_contribution/key_technique/background/irrelevant)
        refined_coverage_level: Updated level after fulltext analysis
        task_similarity: 0-1 similarity of task/problem
        method_similarity: 0-1 similarity of method/approach
    """
    paper_id: str
    coverage_level: Optional[int]  # None = unassessed/failed, not "no overlap".
    # Raw facet labels like ["F1", "F3"] for reliable counting
    covered_facet_labels: List[str] = field(default_factory=list)
    # Human-readable facet descriptions (mapped from labels)
    covered_facets: List[str] = field(default_factory=list)
    notes: str = ""
    structural_score: int = 0
    structural_overlap: List[str] = field(default_factory=list)
    high_level_facet_score: int = 0
    high_level_facet_matches: Dict[str, List[str]] = field(default_factory=dict)
    role: str = "unknown"  # main_contribution | key_technique | background | irrelevant
    refined_coverage_level: Optional[int] = None
    task_similarity: float = 0.0  # 0-1 how similar the task/problem is
    method_similarity: float = 0.0  # 0-1 how similar the method/approach is
    scoring_status: str = "succeeded"
    scoring_error: Optional[str] = None
    abstract_coverage_level: Optional[int] = None
    abstract_notes: str = ""
    refinement_status: str = "not_run"
    refinement_error: Optional[str] = None
    refinement_snippets: List[str] = field(default_factory=list)


@dataclass
class PriorArtConfig:
    """
    Configuration for the Prior Art Check pipeline.
    
    This config controls all aspects of the pipeline:
    - LLM call limits
    - Prefiltering thresholds
    - Parallelization settings
    - Block 1 retrieval parameters
    - Block 3 fulltext refinement settings
    
    In debug mode, most limits are reduced for faster iteration.
    """
    
    # ========== Abstract Scoring Limits ==========
    # Maximum papers to send to LLM scoring
    execution_mode: str = 'full_report'  # full_report | retrieval_eval
    retrieval_eval_fulltext_limit: int = 5  # Fixed attempts, without replacement.
    retrieval_eval_pdf_text_mode: str = 'snippets'  # snippets | full_text
    retrieval_eval_pdf_max_snippets: int = 6
    retrieval_eval_pdf_snippet_chars: int = 1800
    # Top-5-only optimizations; disable individually for frozen-input comparisons.
    retrieval_eval_skip_report_evidence: bool = True
    retrieval_eval_parallel_seed_discovery: bool = True
    retrieval_eval_expert_compact_input: bool = True
    retrieval_eval_expert_max_output_tokens: int = 16384
    optimize_scholarly_requests: bool = False
    shared_http_connections: bool = False
    shared_pdf_cache_dir: Optional[str] = None  # Content-addressed parsed pages only.
    retrieval_backend: str = 'legacy_openalex'
    enable_openalex: bool = True
    enable_arxiv_search: bool = False  # ID lookup and PDF resolution remain available.
    expert_retrieval_timeout_seconds: float = 120.0
    s2_batch_size: int = 100
    http_max_wait_seconds: float = 120.0
    # Opt-in relevance funnel; the legacy path remains reproducible.
    staged_retrieval: bool = False
    retriever_protect_top_k: int = 5
    protected_candidate_ratio: float = 0.25
    missing_abstract_reserve: int = 10
    metadata_shortlist_size: int = 50
    metadata_boundary_buffer: int = 10
    citation_new_candidate_limit: int = 50
    max_abstract_scoring_initial: int = 80
    max_abstract_scoring_after_citation: int = 100
    
    # Debug mode (reduces all limits)
    debug_mode: bool = False
    debug_max_abstract_scoring_initial: int = 10
    debug_max_abstract_scoring_after_citation: int = 20
    
    # ========== Prefilter Config (Stage 1: Lexical) ==========
    enable_prefilter: bool = True
    prefilter_min_papers: int = 30          # Only prefilter if > 30 candidates
    prefilter_max_keep: int = 80            # Max to keep from lexical filter
    prefilter_max_keywords: int = 20
    enable_survey_penalty: bool = False     # If True, penalize survey/review papers in lexical filter
    
    # ========== Prefilter Config (Stage 2: Light LLM) ==========
    enable_llm_prefilter: bool = True       # Use light LLM for relevance check
    llm_prefilter_top_n: int = 100          # Apply LLM filter to top N
    debug_llm_prefilter_top_n: int = 30     # Reduced in debug mode
    
    # ========== Logging ==========
    progress_log_interval: int = 5
    
    # ========== Parallelization (worker counts) ==========
    # NOTE: Rate limiting is handled at client layer via SimpleRateLimiter.
    # These max_workers_* control local CPU/IO concurrency, not API request rate.
    max_workers_openalex: int = 4      # OpenAlex seed validation
    max_workers_s2: int = 4            # Semantic Scholar citation graph
    max_workers_pdf: int = 5           # PDF download/parse
    
    # ========== LLM Scoring Parallelization ==========
    max_workers_llm_scoring: int = 20  # Parallel workers for score_papers_with_abstract_only
    max_workers_llm_prefilter: int = 4 # Parallel workers for llm_relevance_prefilter
    max_workers_fulltext_refine: int = 4
    max_workers_snippet_batches: int = 4
    max_workers_web_batches: int = 4
    overlap_pdf_keywords: bool = True
    overlap_refine_method: bool = False
    max_in_flight_requests: int = 4
    max_pending_requests: int = 32
    llm_requests_per_minute: Optional[int] = None
    llm_tokens_per_minute: Optional[int] = None
    max_total_attempts: Optional[int] = None
    resume_same_task: bool = False
    reuse_llm_across_stages: bool = False
    domain_profile: Optional[str] = None
    facet_pattern_overrides: Dict[str, List[str]] = field(default_factory=dict)
    required_facet_ids: List[str] = field(default_factory=list)
    selection_policy_version: str = "legacy_observed_v1"
    
    # ========== Merged LLM Calls ==========
    enable_merged_llm_calls: bool = True  # If True, merge facet extraction + scoring into one LLM call
    
    # ========== Fulltext Refinement (Block 3) ==========
    top_k_fulltext_refine: int = 20
    debug_top_k_fulltext_refine: int = 5
    
    # ========== Block 1: Enhanced Candidate Retrieval ==========
    # Dual-bucket search (main + recent)
    # Recent bucket year range (closed interval)
    recent_year_start: int = 2024           # "Recent bucket" starts from this year
    recent_year_end: int = 2025             # "Recent bucket" ends at this year (inclusive)
    recent_min_quota_ratio: float = 0.3     # At least 30% from recent bucket
    recent_max_quota_ratio: float = 0.6     # At most 60% from recent bucket
    max_candidates: int = 200               # Total candidate limit
    
    # Scoring weights for ranking
    weight_relevance: float = 0.6
    weight_citation: float = 0.25
    weight_recency: float = 0.15
    
    # Hierarchical search (query refinement)
    enable_hierarchical: bool = True
    top_k_for_refine: int = 20
    max_refine_queries: int = 5
    max_seed_queries: int = 6
    debug_max_seed_queries: int = 3
    
    # ========== Citation Graph Expansion ==========
    # Maximum neighbors to fetch from S2 API per seed (references + citations combined)
    max_neighbors_per_seed: int = 200
    # Semantic is the default; lexical preserves the original comparison baseline.
    citation_neighbor_filter: str = "llm"
    citation_neighbor_batch_size: int = 20
    # Saved Crossref references are opt-in; the default uses five S2 seeds.
    enable_crossref_references: bool = False
    # Positive-score budget per seed. Semantic unknowns remain unassessed and
    # bypass this cap; the existing global citation budget still applies.
    # Retain the old field name for compatibility with saved profiles.
    topk_neighbors_after_lexical: int = 15
    
    # ========== Evidence Extraction ==========
    enable_bm25_evidence: bool = True             # Use BM25 to backfill low-evidence papers
    evidence_bm25_top_k: int = 3                  # BM25 sentences per paper
    min_evidence_per_paper_for_bm25: int = 2      # Threshold below which BM25 kicks in
    
    # ========== Feature Flags ==========
    enable_dataset_metric_stats: bool = False  # Disabled until reliable
    
    # ========== 1.3 Method/Experiment Snippets ==========
    enable_method_exp_snippets: bool = True
    max_paragraphs_per_facet: int = 50           # BM25 top-N per facet
    max_snippets_per_paper_facet: int = 5        # Max snippets per (paper, facet)
    debug_max_paragraphs_per_facet: int = 20     # Reduced for debug
    
    # ========== 1.4 Key Papers & Intro+RW Full ==========
    enable_intro_rw_full: bool = True
    key_paper_top_cited_k: int = 5               # Top-K by citation count
    key_paper_latest_k: int = 5                  # Top-K by year
    key_paper_survey_k: int = 5                  # Top-K surveys
    
    # ========== Web Search Configuration ==========
    # SerpAPI (quota-conscious: only 2 calls per idea instead of 12)
    enable_serpapi: bool = True                  # Use SerpAPI for web search
    serpapi_num_results_all: int = 30            # Results for all-years search
    serpapi_num_results_recent: int = 30         # Results for recent-range search
    serpapi_timeout_sec: int = 20                # Request timeout
    
    # DuckDuckGo (free, used for additional coverage)
    enable_duckduckgo: bool = True               # Use DuckDuckGo for complementary search
    ddg_num_queries: int = 3                     # Number of queries to generate for DDG
    ddg_max_results_per_query: int = 20          # Max results per DDG query per mode
    
    # Parallel web search
    max_workers_web_search: int = 6              # Thread pool size for HTTP calls
    
    # ========== LLM-Driven Query/Pattern Generation ==========
    enable_llm_idea_queries: bool = True         # Generate queries purely from idea (no topic)
    enable_llm_facet_patterns: bool = True       # Generate facet patterns from idea (replace hardcoded)
    llm_max_facets: int = 6                      # Max facets to generate
    llm_max_patterns_per_facet: int = 10         # Max patterns per facet
    seed_validation_threshold: float = 0.35     # Jaccard threshold for seed validation
    
    # ========== 2.1 Synthesis JSON Builder ==========
    enable_synthesis_json: bool = True           # Always generate synthesis_input.json
    auto_generate_intro_rw_report: bool = False  # If True, call heavy LLM to generate MD
    max_papers_per_bucket_for_prompt: int = 15   # Limit papers in prompt for heavy LLM
    
    # ========== PDF Pipeline Fallback ==========
    # DDG search for PDF URLs when other sources fail (disabled by default for determinism)
    enable_web_pdf_fallback: bool = False        # If True, search DDG as last resort for PDF URLs
    ddg_pdf_search_query_suffix: str = "filetype:pdf"  # Appended to title search
    
    # ========== Excluded Papers (Blind Evaluation) ==========
    # List of papers to exclude from the pipeline for controlled experiments.
    # Each dict can contain: title, doi, arxiv_id, s2_id, openalex_id
    # Papers matching ANY of these identifiers will be filtered out at all stages.
    excluded_papers: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class Paragraph:
    """
    A paragraph extracted from Method or Experiment sections.
    Used as the unit for BM25 indexing and LLM filtering.
    """
    paper_id: str
    section_role: str  # "method" or "experiment"
    section_heading: str
    para_id: int
    text: str
    token_count: int
    # Context for better understanding
    prev_para_text: str = ""
    next_para_text: str = ""
    section_id: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None


@dataclass
class MethodExpSnippet:
    """
    A filtered and summarized snippet from Method/Experiment sections.
    One snippet per (paper_id, facet_label) pair, with LLM-generated bullet.
    
    Written to synthesis/method_exp_snippets.jsonl
    """
    paper_id: str
    facet_label: str
    section_role: str  # "method" or "experiment"
    section_heading: str
    loc: str  # e.g., "method_para_12-13"
    bullet: str  # LLM-generated one-sentence summary
    snippet: str  # Original text excerpt (1-3 sentences)
    bm25_score: float = 0.0
    llm_relevance: float = 0.0
    section_id: str = ""
    paragraph_id: int = 0
    page_start: Optional[int] = None
    page_end: Optional[int] = None


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_max_abstract_to_score(
    cfg: PriorArtConfig,
    stage: str,  # "initial" or "after_citation"
) -> int:
    """
    Get the maximum number of papers to score based on config and stage.
    
    Args:
        cfg: Pipeline configuration
        stage: "initial" (before citation graph) or "after_citation"
        
    Returns:
        Maximum number of papers to send to LLM scoring
    """
    if cfg.debug_mode:
        return (
            cfg.debug_max_abstract_scoring_initial
            if stage == "initial"
            else cfg.debug_max_abstract_scoring_after_citation
        )
    return (
        cfg.max_abstract_scoring_initial
        if stage == "initial"
        else cfg.max_abstract_scoring_after_citation
    )
