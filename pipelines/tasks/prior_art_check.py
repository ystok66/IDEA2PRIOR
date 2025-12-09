# pipelines/tasks/prior_art_check.py

"""
Prior Art Check - Main Orchestrator

This is the main entry point for the prior-art checking pipeline.
Given an idea description and a research topic, this task:

1. Block 1 (Retrieval): Discover candidate papers via:
   - LLM-generated seed papers and search queries
   - OpenAlex dual-bucket search (main + recent)
   - Citation graph expansion via Semantic Scholar
   
2. Block 2 (Abstract Scoring): Score candidates using:
   - Lexical prefilter for efficiency
   - LLM relevance check
   - LLM coverage scoring (0-3 levels)
   
3. Block 3 (Fulltext Refinement): For top papers:
   - Download and parse PDFs
   - Extract sections (intro, method, related_work)
   - Find evidence snippets
   - Generate Intro+RW summaries
   
4. Report: Generate structured markdown report with:
   - High-overlap prior art (potential conflicts)
   - Partial-overlap related work  
   - Adjacent work
   - Facet-level gap analysis

Module Dependencies:
- prior_art_types: Shared dataclasses (PriorArtConfig, PriorArtMatch) and constants
- prior_art_llm_utils: JSON repair and LLM helper functions

See PRIOR_ART_DOCUMENTATION.md for full architecture documentation.
"""

from __future__ import annotations

from typing import List, Optional, Tuple, Dict, Set
from dataclasses import dataclass, field
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from loguru import logger

# Core pipeline components
from pipelines.state import Intent, PipelineState, Paper, Chunk
from pipelines.exploration_loop import run_exploration_loop
from pipelines.clients import SemanticScholarClient
from pipelines.run_manager import RunManager
from pipelines.llm_config import get_litellm_config

# Tools
from tools.openalex_search import OpenAlexWorksSearchTool
from tools.paper_resolver import PaperResolverTool
from tools.pdf_parser import PdfParserTool

# Pipeline modules
from pipelines.paper_record import (
    PaperRecord, paper_to_paper_record, tag_all_papers, is_in_domain_for_topic
)
from pipelines.evidence import (
    EvidenceSpan, collect_facet_evidence, compute_facet_coverage, 
    summarize_facet_coverage, FACET_PATTERNS, FacetReportView,
    build_facet_report_views, summarize_facet_for_report,
    coverage_label, FacetCoverageStats,
)
from pipelines.coverage_classifier import (
    classify_all_papers, get_papers_by_level, validate_coverage_consistency
)
from pipelines.sections import PaperSections, Section, save_paper_sections
from pipelines.intro_rw_summarizer import summarize_intro_rw_for_block3

# Shared types and LLM utilities (avoids circular imports)
from pipelines.tasks.prior_art_types import (
    PriorArtConfig, PriorArtMatch,
    HIGH_LEVEL_FACET_TYPES, GENERIC_STOPWORDS,
    get_max_abstract_to_score,
)
from pipelines.tasks.prior_art_llm_utils import (
    repair_json_output, parse_json_with_repair, clean_llm_output,
    extract_text_from_llm_response,
)
from pipelines.tasks.prior_art_report import render_prior_art_report
from pipelines.tasks.prior_art_retrieval import (
    seed_papers_from_llm,
    validate_llm_seeds_via_openalex,
    generate_search_queries_from_idea,
    generate_seed_queries_llm,
    retrieve_candidates_with_buckets,
    refine_queries_llm,
    apply_citation_recency_quota,
    expand_corpus_via_citation_graph,
)
from pipelines.tasks.prior_art_scoring import (
    decompose_idea_into_facets,
    extract_idea_features,
    extract_high_level_facets_from_idea,
    generate_relevance_keywords_from_idea,
    prefilter_papers_for_llm_scoring,
    llm_relevance_prefilter,
    score_papers_with_abstract_only,
    enrich_papers_with_fulltext,
    refine_top_papers_with_fulltext,
)
from pipelines.tasks.prior_art_fulltext import (
    build_method_experiment_snippets,
    select_key_papers_and_dump_intro_rw_full,
)
from pipelines.tasks.prior_art_synthesis import build_synthesis_input

# Standard library
import requests
import os
from pathlib import Path
from smolagents import LiteLLMModel


def guess_default_venues_for_topic(topic: str) -> List[str]:
    """
    Very simple heuristic to map a free-form topic string to a
    list of default top-tier venues.

    This is only a heuristic and should prefer recall over precision.
    """
    t = topic.lower()
    venues: List[str] = []

    if any(x in t for x in ["vision", "detection", "segmentation", "3d", "image", "video"]):
        venues.extend(["CVPR", "ICCV", "ECCV", "TPAMI", "IJCV"])
    if any(x in t for x in ["language", "nlp", "translation", "bert", "llm", "chatgpt"]):
        venues.extend(["ACL", "EMNLP", "NAACL", "COLING", "TACL"])
    if any(x in t for x in ["reinforcement learning", "rl", "control"]):
        venues.extend(["NeurIPS", "ICML", "ICLR"])
    if any(x in t for x in ["graph", "gnn"]):
        venues.extend(["NeurIPS", "ICLR", "KDD", "WWW"])
    # Always include the big 3 for general ML topics
    if any(x in t for x in ["machine learning", "deep learning", "foundation model", "transformer", "diffusion"]):
        venues.extend(["NeurIPS", "ICML", "ICLR"])

    # Deduplicate while preserving order
    seen = set()
    result = []
    for v in venues:
        v_norm = v.strip()
        if v_norm and v_norm not in seen:
            seen.add(v_norm)
            result.append(v_norm)

    return result


# =============================================================================
# NOTE: Previously defined here, now imported from prior_art_types:
# - HIGH_LEVEL_FACET_TYPES (list): Facet categories for comparison
# - GENERIC_STOPWORDS (set): Terms to down-weight in relevance scoring
# - PriorArtMatch (dataclass): Scoring result for paper overlap
# - PriorArtConfig (dataclass): Pipeline configuration
# - get_max_abstract_to_score(): Helper for config-based limits
# =============================================================================


def build_prior_art_intent(
    idea_description: str,
    topic: str,
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
) -> Intent:
    """
    Build a structured Intent for the prior-art check task.

    The `topic` captures the broader research area (e.g., 'monocular 3D detection'),
    while `idea_description` describes the concrete idea to check.
    """
    if venues is None:
        venues = guess_default_venues_for_topic(topic)

    return Intent(
        task_type="prior_art_check",
        topic=topic,
        years=years,
        venues=venues or [],
        extra_constraints={"idea_description": idea_description},
    )

def run_prior_art_check(
    agent,
    idea_description: str,
    topic: str,
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
    max_rounds: int = 2,
    use_citation_graph: bool = True,
    debug_mode: bool = False,
) -> str:
    """
    High-level entry point for the prior-art check task.

    Flow:
      1) Build Intent and initial PipelineState.
      2) Run the exploration loop to collect a candidate corpus.
      3) Decompose the idea into facets.
      4) Score papers against these facets.
      5) Render a structured report.
    """
    intent = build_prior_art_intent(
        idea_description=idea_description,
        topic=topic,
        years=years,
        venues=venues,
    )
    state = PipelineState(intent=intent)
    cfg = PriorArtConfig(debug_mode=debug_mode)
    
    # NOTE: debug_mode no longer forces workers=1
    # It only reduces data scale (top_n, paragraphs, etc.) while keeping concurrency
    # This allows testing concurrent code paths even in debug mode
    
    # Determine fulltext refine top_k logic
    fulltext_top_k = cfg.debug_top_k_fulltext_refine if debug_mode else cfg.top_k_fulltext_refine
    
    # ========== Block 2: Run Directory Setup ==========
    run_mgr = RunManager(topic=topic)
    run_mgr.create_run_directory()
    
    # Save run configuration
    run_config = {
        "task_type": "prior_art_check",
        "idea_description": idea_description[:500],  # Truncate for config
        "year_start": years[0] if years else 2018,
        "year_end": years[1] if years else 2025,
        "venues": venues,
        "max_rounds": max_rounds,
        "use_citation_graph": use_citation_graph,
        "debug_mode": debug_mode,
        "recent_year_threshold": cfg.recent_year_threshold,
        "recent_min_quota_ratio": cfg.recent_min_quota_ratio,
        "recent_max_quota_ratio": cfg.recent_max_quota_ratio,
    }
    run_mgr.save_config(run_config)

    logger.info(
        f"[PriorArtCheck] Starting for topic='{topic}'. DebugMode={debug_mode}; "
        f"workers: openalex={cfg.max_workers_openalex}, "
        f"s2={cfg.max_workers_s2}, pdf={cfg.max_workers_pdf}; "
        f"fulltext_top_k={fulltext_top_k}; run_id={run_mgr.run_id}"
    )

    # 1) Let the LLM propose seed papers (world knowledge).
    llm_seed_dicts = seed_papers_from_llm(agent, idea_description, topic)
    validated_seed_papers = validate_llm_seeds_via_openalex(
        llm_seed_dicts,
        max_workers=cfg.max_workers_openalex,
    )
    for p in validated_seed_papers:
        # Make sure to insert into state.papers with a stable paper_id
        # Use DOI or OpenAlex ID as key
        key = p.doi if p.doi else p.openalex_id
        if key and key not in state.papers:
            state.papers[key] = p
            logger.info(f"[PriorArt] Added seed paper: {p.title}")

    # 2) Ask the LLM to generate search queries.
    search_queries = generate_search_queries_from_idea(agent, idea_description, topic)

    state = run_exploration_loop(
        agent=agent,
        state=state,
        max_rounds=max_rounds,
        search_queries=search_queries,
    )

    logger.info(
        f"[PriorArtCheck] Exploration finished with "
        f"{len(state.papers)} candidate papers."
    )

    facets = decompose_idea_into_facets(agent, idea_description)
    idea_features = extract_idea_features(agent, idea_description)
    idea_high_level_facets = extract_high_level_facets_from_idea(agent, idea_description)
    
    # ========== Block 1: Enhanced Candidate Retrieval ==========
    # Generate LLM seed queries for dual-bucket search
    year_start = years[0] if years else 2018
    year_end = years[1] if years else 2025
    
    seed_queries = generate_seed_queries_llm(
        agent=agent,
        topic=topic,
        idea=idea_description,
        facets=facets,
        cfg=cfg,
    )
    logger.info(f"[Block1] Seed queries: {seed_queries}")
    
    # Log seed queries to run
    run_mgr.append_search_log("seed_queries", {"queries": seed_queries})
    
    # Dual-bucket retrieval (main + recent)
    block1_papers = retrieve_candidates_with_buckets(
        seed_queries=seed_queries,
        state=state,
        cfg=cfg,
        year_start=year_start,
        year_end=year_end,
    )
    
    # Merge into state.papers
    new_count = 0
    for paper_id, paper in block1_papers.items():
        if paper_id not in state.papers:
            state.papers[paper_id] = paper
            new_count += 1
    logger.info(f"[Block1] Added {new_count} new papers from enhanced retrieval. Total: {len(state.papers)}")
    
    # Hierarchical refinement (skip in debug mode to save tokens)
    if cfg.enable_hierarchical and not cfg.debug_mode:
        top_papers = sorted(
            state.papers.values(),
            key=lambda p: (p.citation_count or 0),
            reverse=True
        )[:cfg.top_k_for_refine]
        
        refined_queries = refine_queries_llm(
            agent=agent,
            topic=topic,
            idea=idea_description,
            top_papers=top_papers,
            facets=facets,
            cfg=cfg,
        )
        logger.info(f"[Block1] Refined queries: {refined_queries}")
        
        # Run additional recent-focused search with refined queries
        if refined_queries:
            refined_papers = retrieve_candidates_with_buckets(
                seed_queries=refined_queries,
                state=state,
                cfg=cfg,
                year_start=max(year_start, cfg.recent_year_threshold),  # Focus on recent
                year_end=year_end,
            )
            new_refined = 0
            for paper_id, paper in refined_papers.items():
                if paper_id not in state.papers:
                    state.papers[paper_id] = paper
                    new_refined += 1
            logger.info(f"[Block1] Added {new_refined} papers from refined queries. Total: {len(state.papers)}")
    
    # Apply quota-based selection to prioritize recent papers
    all_papers_dict = {pid: p for pid, p in state.papers.items()}
    selected_papers = apply_citation_recency_quota(all_papers_dict, cfg)
    
    # Update state with quota-selected papers
    state.papers = {
        (p.doi or p.openalex_id or p.title): p
        for p in selected_papers
    }
    logger.info(f"[Block1] After quota selection: {len(state.papers)} papers")
    
    # ========== Block 3: Evidence Collection & Domain Filtering ==========
    # Convert Papers to PaperRecords for structured analysis
    paper_records: Dict[str, PaperRecord] = {}
    for paper_id, paper in state.papers.items():
        record = paper_to_paper_record(paper)
        paper_records[record.paper_id] = record
    
    logger.info(f"[Block3] Created {len(paper_records)} PaperRecords")
    
    # Collect facet evidence from all papers
    evidence_spans = collect_facet_evidence(list(paper_records.values()))
    
    # Compute unified facet coverage
    facet_coverage = compute_facet_coverage(evidence_spans)
    
    # Log coverage summary
    coverage_summary = summarize_facet_coverage(facet_coverage)
    logger.info(f"[Block3] Facet coverage: {coverage_summary}")
    
    # Log to run directory
    run_mgr.append_search_log("facet_coverage", {"summary": coverage_summary})
    
    # Filter out-of-domain papers
    in_domain_count = 0
    out_domain_papers = []
    for paper_id, record in paper_records.items():
        if is_in_domain_for_topic(record, "monocular_3d_detection"):
            in_domain_count += 1
        else:
            out_domain_papers.append(paper_id)
    
    logger.info(f"[Block3] Domain filter: {in_domain_count} in-domain, {len(out_domain_papers)} out-of-domain")
    
    # Classify papers using unified coverage logic (same evidence_spans as FacetCoverageStats)
    coverage_levels = classify_all_papers(paper_records, evidence_spans, topic="monocular_3d_detection")
    
    # Validate consistency: papers in High/Partial must have strong evidence
    consistency_warnings = validate_coverage_consistency(coverage_levels, evidence_spans)
    if consistency_warnings and debug_mode:
        for warning in consistency_warnings[:5]:
            logger.warning(f"[Block3] {warning}")
    
    # Build facet report views for report generation
    facet_report_views = build_facet_report_views(facet_coverage)
    
    # Log facet summaries to run directory
    facet_summaries = {
        fid: summarize_facet_for_report(view) 
        for fid, view in facet_report_views.items()
    }
    run_mgr.append_search_log("facet_summaries", {"summaries": facet_summaries})
    
    # Store Block 3 artifacts as local variables (PipelineState is a Pydantic model)
    block3_paper_records = paper_records
    block3_facet_coverage = facet_coverage
    block3_out_domain = set(out_domain_papers)
    block3_coverage_levels = coverage_levels
    block3_facet_views = facet_report_views
    
    # Stage 1: abstract-level coarse scoring (cheap, no fulltext)
    matches = score_papers_with_abstract_only(
        agent=agent, 
        state=state, 
        facets=facets, 
        idea_features=idea_features, 
        idea_high_level_facets=idea_high_level_facets,
        idea_description=idea_description,
        cfg=cfg,
        stage="initial"
    )

    # Citation graph expansion (optional)
    if use_citation_graph:
        expand_corpus_via_citation_graph(
            state=state,
            matches=matches,
            max_seed_papers=5,
            max_neighbors_per_seed=30,
            max_workers=cfg.max_workers_s2,
        )
        
        # Re-score newly added papers at abstract level
        logger.info(f"[PriorArt] Re-scoring {len(state.papers)} total papers after citation expansion...")
        matches = score_papers_with_abstract_only(
            agent=agent,
            state=state,
            facets=facets,
            idea_features=idea_features,
            idea_high_level_facets=idea_high_level_facets,
            idea_description=idea_description,
            cfg=cfg,
            stage="after_citation"
        )

    # Stage 2: fulltext refinement for top-K candidates.
    matches = refine_top_papers_with_fulltext(
        agent=agent,
        state=state,
        matches=matches,
        idea_description=idea_description,
        facets=facets,
        idea_features=idea_features,
        top_k=fulltext_top_k,
        max_workers=cfg.max_workers_pdf,
        run_mgr=run_mgr,
    )

    # ========== Intro + Related Work Summarization for Block 3 ==========
    # Summarize Intro+RW for papers with coverage_level >= 2 (partial or high overlap)
    block3_paper_ids = [m.paper_id for m in matches if m.coverage_level >= 2]
    if block3_paper_ids and run_mgr:
        block3_papers_for_summary = [
            {
                "paper_id": pid,
                "title": state.papers[pid].title,
                "year": state.papers[pid].year or 0,
            }
            for pid in block3_paper_ids
            if pid in state.papers
        ]
        try:
            summarize_intro_rw_for_block3(
                task_root=run_mgr.run_dir,
                papers=block3_papers_for_summary,
                topic=topic,
                idea_description=idea_description,
            )
        except Exception as e:
            logger.warning(f"[PriorArtCheck] Intro+RW summarization failed: {e}")

    # ========== 1.3: Method/Experiment Snippets ==========
    if run_mgr:
        try:
            build_method_experiment_snippets(
                state=state,
                facets=facets,
                idea_features=idea_features,
                run_mgr=run_mgr,
                config=cfg,
            )
        except Exception as e:
            logger.warning(f"[PriorArtCheck] Method/Experiment snippets failed: {e}")

    # ========== 1.4: Key Papers & Intro+RW Full JSON ==========
    if run_mgr:
        try:
            select_key_papers_and_dump_intro_rw_full(
                agent=agent,
                state=state,
                task_root=Path(run_mgr.run_dir),
                config=cfg,
            )
        except Exception as e:
            logger.warning(f"[PriorArtCheck] Key papers selection failed: {e}")

    # ========== 2.1: Synthesis JSON Builder ==========
    if run_mgr and cfg.enable_synthesis_json:
        try:
            build_synthesis_input(
                state=state,
                config=cfg,
                run_dir=Path(run_mgr.run_dir),
                facets=[{"id": f"F{i+1}", "label": f, "category": "core"} for i, f in enumerate(facets)],
                user_query=idea_description,
                retrieval_queries=search_queries,
                # 新增: 传递 coverage 和 match 数据
                coverage_levels=block3_coverage_levels,
                scored_matches=matches,
            )
        except Exception as e:
            logger.warning(f"[PriorArtCheck] Synthesis JSON failed: {e}")

    report = render_prior_art_report(
        idea_description=idea_description,
        facets=facets,
        scored_papers=matches,
        state=state,
        idea_features=idea_features,
        idea_high_level_facets=idea_high_level_facets,
        # Pass Block 3 unified data sources (Issues 1, 3, 4, 6)
        coverage_levels=block3_coverage_levels,
        paper_records=block3_paper_records,
        facet_coverage=block3_facet_coverage,
        facet_views=block3_facet_views,
        config=cfg,
    )
    
    # Issue 2: Debug assertion for [Refined] artifact
    if debug_mode:
        assert "[Refined]" not in report, "Report contains [Refined] artifact - cleanup incomplete"

    # ========== Block 2: Save report to run directory ==========
    try:
        # Save to run directory (primary location)
        report_path = run_mgr.save_report(report)
        logger.info(f"[PriorArtCheck] Report saved to {report_path}")
        
        # Also save to project root in debug mode (for convenience)
        if debug_mode:
            import datetime
            import re as _re
            safe_topic = _re.sub(r"[^a-zA-Z0-9_-]+", "_", topic).strip("_")[:50]
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"prior_art_debug_{safe_topic}_{timestamp}.md"
            out_path = os.path.join(os.getcwd(), filename)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(report.replace("\\n", "\n"))
            logger.info(f"[PriorArtCheck] Debug copy saved to {out_path}")
    except Exception as e:
        logger.warning(f"[PriorArtCheck] Failed to save report: {e}")

    return report
