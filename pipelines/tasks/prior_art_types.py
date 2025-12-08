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
    coverage_level: int  # 0 = none, 1 = adjacent, 2 = partial, 3 = high
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
    
    # ========== Prefilter Config (Stage 2: Light LLM) ==========
    enable_llm_prefilter: bool = True       # Use light LLM for relevance check
    llm_prefilter_top_n: int = 100          # Apply LLM filter to top N
    debug_llm_prefilter_top_n: int = 30     # Reduced in debug mode
    
    # ========== Logging ==========
    progress_log_interval: int = 5
    
    # ========== Parallelization ==========
    max_workers_openalex: int = 4
    max_workers_s2: int = 4
    max_workers_pdf: int = 3
    
    # ========== Fulltext Refinement (Block 3) ==========
    top_k_fulltext_refine: int = 20
    debug_top_k_fulltext_refine: int = 5
    
    # ========== Block 1: Enhanced Candidate Retrieval ==========
    # Dual-bucket search (main + recent)
    recent_year_threshold: int = 2024       # "Recent bucket" starts from this year
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
    
    # ========== Feature Flags ==========
    enable_dataset_metric_stats: bool = False  # Disabled until reliable


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
