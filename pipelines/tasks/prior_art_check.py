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
from dataclasses import dataclass, field, asdict, replace
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
import threading

from loguru import logger

# Core pipeline components
from pipelines.state import Intent, PipelineState, Paper, Chunk
from pipelines.identity import upsert_paper, normalize_state
from pipelines.assessment import build_assessment_snapshot
from pipelines.ranking import export_ranked_candidates
from pipelines.tasks.prior_art_expert_ranking import rerank_shortlist_with_expert
from pipelines.runtime import use_runtime, RunLedger, LLMRuntime, RequestScheduler, atomic_write_json, AttemptLimitError, TaskStateError
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
    coverage_label, FacetCoverageStats, generate_facet_patterns_from_idea,
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
from pipelines.tasks.candidate_selection import (
    POLICY as RELEVANCE_POLICY, select_candidates, select_shortlist, refresh_aliases,
)
from pipelines.tasks.prior_art_retrieval import (
    seed_papers_from_llm,
    validate_llm_seeds_via_openalex,
    seed_papers_from_serpapi,
    merge_seeds,
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
    prepare_fixed_fulltext_for_eval,
)
from pipelines.tasks.prior_art_fulltext import (
    build_method_experiment_snippets,
    select_key_papers_and_dump_intro_rw_full,
)
from pipelines.tasks.prior_art_synthesis import build_synthesis_input
from pipelines.tasks.prior_art_utils import is_excluded_paper, filter_excluded_papers

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

def _discover_seed_papers(agent, idea_description, years, cfg):
    """Overlap independent sources while retaining source order and one runtime."""
    if not (cfg.enable_serpapi or cfg.enable_duckduckgo):
        return seed_papers_from_llm(agent, idea_description)

    def web_seeds():
        return seed_papers_from_serpapi(
            idea_description=idea_description,
            year_start=years[0] if years else 2018,
            year_end=years[1] if years else 2025,
            cfg=cfg,
        )

    if cfg.execution_mode == 'retrieval_eval' and cfg.retrieval_eval_parallel_seed_discovery:
        from pipelines.clients.scholarly import outcome
        failures = []

        def collect_source(source, future):
            try:
                return future.result()
            except (AttemptLimitError, TaskStateError):
                # Runtime limits and invalid ledger state must still stop work.
                raise
            except Exception as error:
                failures.append(error)
                outcome('seed_discovery', 'FAILED', source=source,
                        error_type=type(error).__name__)
                return []

        with ThreadPoolExecutor(max_workers=2) as executor:
            llm_future = executor.submit(copy_context().run, seed_papers_from_llm, agent, idea_description)
            web_future = executor.submit(copy_context().run, web_seeds)
            # Completion order must not change source proposal ordering.
            llm_seeds = collect_source('llm', llm_future)
            web_results = collect_source('web', web_future)
        if failures:
            if not (llm_seeds or web_results):
                # An empty surviving source cannot hide a failed seed recall.
                raise failures[0]
            outcome('seed_discovery', 'FALLBACK', quiet=True,
                    llm_seed_count=len(llm_seeds), web_seed_count=len(web_results),
                    failed_source_count=len(failures), automatic_retry=False)
            logger.warning('[PriorArt] Seed discovery continued with surviving source results; failed sources were not retried')
    else:
        llm_seeds = seed_papers_from_llm(agent, idea_description)
        web_results = web_seeds()
    merged = merge_seeds(llm_seeds, web_results)
    logger.info(f"[PriorArt] Merged seeds: {len(llm_seeds)} LLM + {len(web_results)} WebSearch = {len(merged)} total")
    return merged


def _run_prior_art_check_impl(
    agent,
    idea_description: str,
    topic: str,
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
    max_rounds: int = 2,
    use_citation_graph: bool = True,
    debug_mode: bool = False,
    excluded_papers: Optional[List[Dict[str, str]]] = None,
    cfg: Optional[PriorArtConfig] = None,
    run_mgr: Optional[RunManager] = None,
) -> str:
    """
    High-level entry point for the prior-art check task.

    Flow:
      1) Build Intent and initial PipelineState.
      2) Run the exploration loop to collect a candidate corpus.
      3) Decompose the idea into facets.
      4) Score papers against these facets.
      5) Render a structured report.

    Args:
        excluded_papers: Optional list of papers to exclude for blind evaluation.
            Each dict can contain: title, doi, arxiv_id, s2_id, openalex_id
    """
    intent = build_prior_art_intent(
        idea_description=idea_description,
        topic=topic,
        years=years,
        venues=venues,
    )
    intent.extra_constraints["use_citation_graph"] = "true" if use_citation_graph else "false"
    state = PipelineState(intent=intent)
    cfg = cfg or PriorArtConfig(debug_mode=debug_mode, excluded_papers=excluded_papers or [])

    # NOTE: debug_mode no longer forces workers=1
    # It only reduces data scale (top_n, paragraphs, etc.) while keeping concurrency
    # This allows testing concurrent code paths even in debug mode

    # Determine fulltext refine top_k logic
    fulltext_top_k = (cfg.retrieval_eval_fulltext_limit if cfg.execution_mode == 'retrieval_eval'
                     else cfg.debug_top_k_fulltext_refine if debug_mode else cfg.top_k_fulltext_refine)

    # ========== Block 2: Run Directory Setup ==========
    run_mgr = run_mgr or RunManager(topic=topic)
    run_mgr.create_run_directory()

    stage_errors = []

    # Save run configuration
    run_config = {
        "task_type": "prior_art_check",
        "resolved_intent": intent.model_dump(),
        "effective_config": asdict(cfg),
        "idea_description": idea_description,
        "year_start": years[0] if years else 2018,
        "year_end": years[1] if years else 2025,
        "venues": intent.venues,
        "max_rounds": max_rounds,
        "use_citation_graph": use_citation_graph,
        "debug_mode": debug_mode,
        "recent_year_start": cfg.recent_year_start,
        "recent_year_end": cfg.recent_year_end,
        "recent_min_quota_ratio": cfg.recent_min_quota_ratio,
        "recent_max_quota_ratio": cfg.recent_max_quota_ratio,
        # Blind evaluation: record excluded papers for experiment traceability
        "excluded_papers_for_eval": cfg.excluded_papers if cfg.excluded_papers else None,
    }
    run_mgr.save_config(run_config)

    logger.info(
        f"[PriorArtCheck] Starting for topic='{topic}'. DebugMode={debug_mode}; "
        f"workers: openalex={cfg.max_workers_openalex}, "
        f"s2={cfg.max_workers_s2}, pdf={cfg.max_workers_pdf}; "
        f"fulltext_top_k={fulltext_top_k}; run_id={run_mgr.run_id}"
    )

    # 1) Seed paper discovery
    all_seed_dicts = _discover_seed_papers(agent, idea_description, years, cfg)

    # Filter excluded papers from seeds (blind evaluation)
    if cfg.excluded_papers:
        original_count = len(all_seed_dicts)
        all_seed_dicts = [s for s in all_seed_dicts if not is_excluded_paper(s, cfg)]
        if len(all_seed_dicts) < original_count:
            logger.info(f"[ExcludedPaper] Filtered {original_count - len(all_seed_dicts)} excluded paper(s) from seeds")

    # Step 1c: Validate all seeds via OpenAlex
    validated_seed_papers = validate_llm_seeds_via_openalex(
        all_seed_dicts,
        max_workers=cfg.max_workers_openalex,
        cfg=cfg,
    )
    seed_paper_ids: Set[str] = set()  # Track seed papers for lexical filter exemption
    for p in validated_seed_papers:
        # Filter excluded papers (blind evaluation)
        if is_excluded_paper(p, cfg):
            logger.info(f"[ExcludedPaper] Skipping excluded validated seed: {p.title[:50]}...")
            continue
        # Make sure to insert into state.papers with a stable paper_id
        # Use DOI, OpenAlex ID, or S2 ID as key (in order of preference)
        key, created = upsert_paper(state, p, source="validated_seed")
        seed_paper_ids.add(key)
        if created:
            logger.info(f"[PriorArt] Added seed paper: {p.title}")

    # 2) Ask the LLM to generate search queries (idea-only, no topic).
    search_queries = generate_search_queries_from_idea(agent, idea_description)

    state = run_exploration_loop(
        agent=agent,
        state=state,
        max_rounds=max_rounds,
        search_queries=search_queries,
        cfg=cfg,  # Pass config for excluded paper filtering
    )

    id_mapping = normalize_state(state)
    seed_paper_ids = {id_mapping.get(pid, pid) for pid in seed_paper_ids}

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
        _, created = upsert_paper(state, paper, source=paper.retrieval_source or "enhanced_retrieval")
        new_count += int(created)
    logger.info(f"[Block1] Added {new_count} new papers from enhanced retrieval. Total: {len(state.papers)}")

    refined_queries = []
    # Hierarchical refinement (skip in debug mode to save tokens)
    if cfg.enable_hierarchical and not cfg.debug_mode:
        top_papers = (select_candidates(
            state.papers, cfg, idea_description, facets, seed_paper_ids,
            limit=cfg.top_k_for_refine, years=years)[0] if cfg.staged_retrieval else sorted(
            state.papers.values(),
            key=lambda p: (p.citation_count or 0),
            reverse=True
        )[:cfg.top_k_for_refine])

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
                year_start=max(year_start, cfg.recent_year_start),  # Focus on recent
                year_end=year_end,
            )
            new_refined = 0
            for paper_id, paper in refined_papers.items():
                _, created = upsert_paper(state, paper, source=paper.retrieval_source or "refined_retrieval")
                new_refined += int(created)
            logger.info(f"[Block1] Added {new_refined} papers from refined queries. Total: {len(state.papers)}")

    # Apply quota-based selection to prioritize recent papers
    all_papers_dict = {pid: p for pid, p in state.papers.items()}
    if cfg.staged_retrieval:
        # Preserve all candidates/material versions before any budgeted selection.
        atomic_write_json(run_mgr.run_dir / "checkpoints/before_selection.json", state.model_dump())
        selected_papers, selection_audit = select_candidates(
            all_papers_dict, cfg, idea_description, facets, seed_paper_ids, years=years)
        atomic_write_json(run_mgr.run_dir / "candidate_selection.json", selection_audit)
    else:
        selected_papers = apply_citation_recency_quota(all_papers_dict, cfg)
        selection_audit = {"policy_version": cfg.selection_policy_version,
        "before_ids": list(all_papers_dict), "selected_ids": [p.paper_id for p in selected_papers],
        "dropped_ids": [pid for pid in all_papers_dict if pid not in {p.paper_id for p in selected_papers}],
        "year_scope": [year_start, year_end], "recent_scope": [cfg.recent_year_start, cfg.recent_year_end]}
    run_mgr.append_search_log("selection", selection_audit)
    # Update state with quota-selected papers
    state.papers = {p.paper_id: p for p in selected_papers}
    # Registry aliases may retain filtered identities for provenance; lookups
    # only reuse keys still present in the active candidate set.
    logger.info(f"[Block1] After quota selection: {len(state.papers)} papers")

    # ========== Pre-Block 3: Generate idea_keywords for evidence & scoring ==========
    # Generate relevance keywords early for reuse in evidence collection, scoring, and citation expansion
    idea_keywords = generate_relevance_keywords_from_idea(
        agent=agent,
        topic=topic,
        idea=idea_description,
        facets=facets,
        max_keywords=cfg.prefilter_max_keywords,
        reuse=cfg.reuse_llm_across_stages,
    )
    strong_cues = idea_keywords[:10] if idea_keywords else []  # Top 10 as strong cues
    logger.info(f"[Pre-Block3] Generated {len(idea_keywords)} idea_keywords for evidence extraction")

    # Technical facets are shared by scoring and lexical evidence patterns.
    technical_facets = [{"id": f"F{i+1}", "name": f, "description": f} for i, f in enumerate(facets)]
    if not technical_facets:
        raise ValueError("FACET_GENERATION_FAILED: no technical facets")
    state.idea_spec = {
        "schema_version": "idea-v1", "idea_description": idea_description,
        "technical_facets": technical_facets,
        "structural_features": {k: sorted(v) for k, v in idea_features.items()},
        "high_level_facets": {k: sorted(v) for k, v in idea_high_level_facets.items()},
        "domain_profile": cfg.domain_profile,
        "required_facet_ids": cfg.required_facet_ids,
    }
    skip_report_evidence = (cfg.execution_mode == 'retrieval_eval'
                            and cfg.retrieval_eval_skip_report_evidence)
    if skip_report_evidence:
        # These patterns feed only report evidence below, never abstract scoring,
        # shortlist selection, PDF snippet selection, or the joint Expert prompt.
        facet_patterns_to_use = {}
    elif cfg.enable_llm_facet_patterns:
        facet_patterns_to_use = generate_facet_patterns_from_idea(
            idea_description=idea_description, max_facets=cfg.llm_max_facets,
            max_patterns_per_facet=cfg.llm_max_patterns_per_facet, facets=technical_facets)
    else:
        facet_patterns_to_use = dict(cfg.facet_pattern_overrides)
        if set(facet_patterns_to_use) - {f["id"] for f in technical_facets}:
            raise ValueError("Configured facet patterns reference undefined technical facet IDs")
    state.idea_spec["pattern_status"] = ("SKIPPED_BY_MODE" if skip_report_evidence else
                                         "succeeded" if facet_patterns_to_use else "unavailable")
    state.idea_spec["pattern_facet_ids"] = list(facet_patterns_to_use)
    atomic_write_json(run_mgr.run_dir / "idea_spec.json", state.idea_spec)

    metadata_enriched_ids = set()
    if cfg.staged_retrieval:
        from pipelines.clients.scholarly import enrich_candidates
        # Missing abstract means uncertainty: bounded early rescue, not a zero score.
        rescue_ids = [pid for pid, p in state.papers.items()
                      if not p.abstract and p.identity_status != 'conflict'][:cfg.missing_abstract_reserve]
        enrich_candidates([state.papers[pid] for pid in rescue_ids], cfg)
        metadata_enriched_ids.update(rescue_ids)
        refresh_aliases(state)
        run_mgr.append_search_log('missing_abstract_rescue', {'selected_ids': rescue_ids})

    # Stage 1: abstract-level coarse scoring (cheap, no fulltext)
    matches = score_papers_with_abstract_only(
        agent=agent, 
        state=state, 
        facets=facets, 
        idea_features=idea_features, 
        idea_high_level_facets=idea_high_level_facets,
        idea_description=idea_description,
        cfg=cfg,
        stage="initial",
        seed_paper_ids=seed_paper_ids,  # Exempt seed papers from lexical filter
    )

    atomic_write_json(run_mgr.run_dir / "checkpoints/abstract_initial.json", {"state": state.model_dump(), "matches": [asdict(m) for m in matches], "facets": facets})

    def enrich_shortlist(current_matches, label):
        newly_enriched, rounds = [], []
        while True:
            selected_ids, audit = select_shortlist(state.papers, current_matches, cfg,
                                                   idea_description, facets, years)
            pending = [pid for pid in selected_ids if pid not in metadata_enriched_ids]
            if not pending:
                break
            enrich_candidates([state.papers[pid] for pid in pending], cfg)
            metadata_enriched_ids.update(pending)
            newly_enriched.extend(pending)
            refresh_aliases(state)
            rounds.append({'selected_ids': selected_ids, 'newly_enriched_ids': pending})
            # Verification can uncover out-of-scope years or conflicting IDs.
            # Refill those vacancies from the scored reserve before freezing the
            # fulltext pool. Every record is enriched at most once in this run.
        audit['newly_enriched_ids'] = newly_enriched
        audit['verification_rounds'] = rounds
        run_mgr.append_search_log(label, audit)
        atomic_write_json(run_mgr.run_dir / (label + '.json'), audit)
        return selected_ids

    if cfg.staged_retrieval:
        shortlist_ids = enrich_shortlist(matches, 'metadata_shortlist')
        # Exact unchanged prompts reuse valid earlier responses. Changed evidence
        # is assessed again and remains separately visible in the ledger.
        matches = score_papers_with_abstract_only(
            agent=agent, state=state, facets=facets, idea_features=idea_features,
            idea_high_level_facets=idea_high_level_facets, idea_description=idea_description,
            cfg=cfg, stage='after_metadata', seed_paper_ids=seed_paper_ids)

    # Citation graph expansion (optional)
    # Note: idea_keywords and strong_cues already generated above (Pre-Block 3)

    if use_citation_graph:
        before_citation_ids = set(state.papers)
        expand_corpus_via_citation_graph(
            state=state,
            matches=([m for m in matches if m.paper_id in shortlist_ids] if cfg.staged_retrieval else matches),
            cfg=cfg,
            idea_keywords=idea_keywords,
            strong_cues=strong_cues,
            max_seed_papers=5,
            max_workers=cfg.max_workers_s2,
            idea_description=idea_description,
            facets=facets,
            search_queries=list(dict.fromkeys(search_queries + seed_queries + refined_queries)),
            research_query=idea_description.partition("Research idea:\n")[2].strip() or idea_description,
            run_mgr=run_mgr,
        )

        if cfg.staged_retrieval:
            added = {pid: p for pid, p in state.papers.items() if pid not in before_citation_ids}
            atomic_write_json(run_mgr.run_dir / 'checkpoints/citation_candidates.json',
                              {pid: p.model_dump() for pid, p in added.items()})
            selected_new, citation_audit = select_candidates(
                added, cfg, idea_description, facets,
                limit=cfg.citation_new_candidate_limit, years=years)
            keep = before_citation_ids | {p.paper_id for p in selected_new}
            state.papers = {pid: p for pid, p in state.papers.items() if pid in keep}
            run_mgr.append_search_log('citation_selection', citation_audit)

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
            stage="after_citation",
            seed_paper_ids=seed_paper_ids,  # Exempt seed papers from lexical filter
        )

    fulltext_eligible = None
    fulltext_shortlist_ids = []
    if cfg.staged_retrieval:
        fulltext_shortlist_ids = enrich_shortlist(matches, 'fulltext_shortlist')
        fulltext_eligible = fulltext_shortlist_ids
        matches = score_papers_with_abstract_only(
            agent=agent, state=state, facets=facets, idea_features=idea_features,
            idea_high_level_facets=idea_high_level_facets, idea_description=idea_description,
            cfg=cfg, stage='before_fulltext', seed_paper_ids=seed_paper_ids)
        # Verify year only when known; keep all excluded evidence in the registry.
        fulltext_eligible = {pid for pid in fulltext_eligible if state.papers[pid].identity_status != 'conflict'
                            and not (years and state.papers[pid].year is not None
                                     and not years[0] <= state.papers[pid].year <= years[1])}

    atomic_write_json(run_mgr.run_dir / "checkpoints/before_fulltext.json", {"state": state.model_dump(), "matches": [asdict(m) for m in matches], "facets": facets})

    expert_ranking = None
    if cfg.execution_mode == 'retrieval_eval':
        if not cfg.staged_retrieval:
            fulltext_shortlist_ids, shortlist_audit = select_shortlist(
                state.papers, matches, cfg, idea_description, facets, years)
            atomic_write_json(run_mgr.run_dir / 'fulltext_shortlist.json', shortlist_audit)
        eval_candidate_ids = [pid for pid in fulltext_shortlist_ids
            if state.papers[pid].identity_status != 'conflict'
            and not (years and state.papers[pid].year is not None
                     and not years[0] <= state.papers[pid].year <= years[1])]
        attempted_fulltext_ids = prepare_fixed_fulltext_for_eval(
            state=state, matches=matches, candidate_ids=eval_candidate_ids,
            run_mgr=run_mgr, cfg=cfg, years=years)
        expert_ranking = rerank_shortlist_with_expert(
            state=state, matches=matches, candidate_ids=eval_candidate_ids,
            attempted_fulltext_ids=attempted_fulltext_ids,
            idea_description=idea_description, facets=facets,
            run_mgr=run_mgr, cfg=cfg, years=years)
        if expert_ranking['status'] == 'FAILED':
            stage_errors.append({'stage': 'expert_ranking', 'status': 'failed',
                'error_type': expert_ranking.get('error_type')})
        for issue in expert_ranking.get('material_validation_errors', []):
            stage_errors.append({'stage': 'expert_fulltext_material_validation',
                                 'status': 'failed', **issue})
    else:
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
            cfg=cfg,
            eligible_paper_ids=fulltext_eligible,
        )

    atomic_write_json(run_mgr.run_dir / "checkpoints/after_fulltext.json", {"state": state.model_dump(), "matches": [asdict(m) for m in matches], "facets": facets})

    if cfg.execution_mode == 'full_report':
        # ========== Intro + Related Work Summarization for Block 3 ==========
        # Summarize Intro+RW for papers with coverage_level >= 2 (partial or high overlap)
        block3_paper_ids = [m.paper_id for m in matches if m.scoring_status == "succeeded" and m.coverage_level is not None and m.coverage_level >= 2]
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
                summaries = summarize_intro_rw_for_block3(
                    task_root=run_mgr.run_dir,
                    papers=block3_papers_for_summary,
                    topic=topic,
                    idea_description=idea_description,
                )
                for summary in summaries or []:
                    if summary.paper_id in state.papers and summary.status == "success" and type(summary.is_survey) is bool:
                        state.papers[summary.paper_id].is_survey = summary.is_survey
            except Exception as e:
                stage_errors.append({"stage": "intro_rw", "status": "failed", "error": str(e)})
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
                stage_errors.append({"stage": "method_exp", "status": "failed", "error": str(e)})
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
                stage_errors.append({"stage": "key_papers", "status": "failed", "error": str(e)})
                logger.warning(f"[PriorArtCheck] Key papers selection failed: {e}")

    # Rebuild evidence from the final material/version set; it is not a second
    # authority that overrides the accepted model decision.
    block3_paper_records, evidence_spans = {}, []
    block3_facet_coverage, block3_facet_views = {}, {}
    if not skip_report_evidence:
        block3_paper_records = {pid: paper_to_paper_record(p, paper_id=pid) for pid, p in state.papers.items()}
        evidence_spans = collect_facet_evidence(
            list(block3_paper_records.values()), facet_patterns=facet_patterns_to_use,
            idea_keywords=idea_keywords, use_bm25=cfg.enable_bm25_evidence,
            bm25_top_k=cfg.evidence_bm25_top_k,
            min_evidence_for_bm25=cfg.min_evidence_per_paper_for_bm25)
        block3_facet_coverage = compute_facet_coverage(evidence_spans)
        block3_facet_views = build_facet_report_views(block3_facet_coverage)
    snapshot = build_assessment_snapshot(state, matches, facets, evidence=evidence_spans)
    block3_coverage_levels = {pid: item["final"]["coverage_level"] for pid, item in snapshot["entries"].items()}
    atomic_write_json(run_mgr.run_dir / "assessment_snapshot.json", snapshot)
    atomic_write_json(run_mgr.run_dir / "paper_registry.json", {"aliases": state.paper_aliases, "conflicts": state.identity_conflicts, "papers": {pid: p.model_dump() for pid, p in state.papers.items()}})
    run_mgr.append_search_log("final_assessment", {"snapshot_id": snapshot["snapshot_id"], "status_counts": snapshot["status_counts"], "bucket_counts": {k: len(v) for k, v in snapshot["buckets"].items()}})

    registry = {"aliases": state.paper_aliases, "conflicts": state.identity_conflicts,
                "papers": {pid: p.model_dump() for pid, p in state.papers.items()}}
    export_ranked_candidates(snapshot, registry, run_mgr.run_dir, years=years, expert_ranking=expert_ranking)
    report_stages = ('intro_rw', 'method_exp', 'key_papers', 'synthesis', 'report')
    mode_outcomes = {
        'execution_mode': cfg.execution_mode,
        'top5_optimizations': {
            'report_evidence': 'SKIPPED_BY_MODE' if skip_report_evidence else 'ENABLED_SUBJECT_TO_CONFIG',
            'parallel_seed_discovery': cfg.execution_mode == 'retrieval_eval'
                and cfg.retrieval_eval_parallel_seed_discovery
                and (cfg.enable_serpapi or cfg.enable_duckduckgo),
            'compact_expert_input': cfg.execution_mode == 'retrieval_eval'
                and cfg.retrieval_eval_expert_compact_input,
            'expert_max_output_tokens': cfg.retrieval_eval_expert_max_output_tokens
                if cfg.execution_mode == 'retrieval_eval' else None,
        },
        'retained_stages': ['retrieval', 'identity_verification', 'abstract_scoring'] +
            (['fixed_fulltext_acquisition', 'expert_ranking'] if cfg.execution_mode == 'retrieval_eval' else ['fulltext_refinement']),
        'fulltext_protocol': {'attempt_limit': cfg.retrieval_eval_fulltext_limit, 'replacement': False,
            'pdf_text_mode': cfg.retrieval_eval_pdf_text_mode,
            'max_snippets': cfg.retrieval_eval_pdf_max_snippets,
            'snippet_chars': cfg.retrieval_eval_pdf_snippet_chars}
            if cfg.execution_mode == 'retrieval_eval' else {'target_successes': fulltext_top_k, 'replacement': True},
        'report_stages': {name: 'SKIPPED_BY_MODE' if cfg.execution_mode == 'retrieval_eval'
                          else 'ENABLED_SUBJECT_TO_CONFIG' for name in report_stages},
        'ranking_rule': 'expert_shortlist_fulltext5_v1' if cfg.execution_mode == 'retrieval_eval' else
            'coverage_desc_then_final_candidate_order_scoped_v2' if years else 'coverage_desc_then_final_candidate_order_v1',
    }
    atomic_write_json(run_mgr.run_dir / 'mode_outcomes.json', mode_outcomes)
    run_mgr.runtime.ledger.event('execution_mode', **mode_outcomes)

    if cfg.execution_mode == 'full_report':
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
                    snapshot=snapshot,
                )
            except Exception as e:
                stage_errors.append({"stage": "synthesis", "status": "failed", "error": str(e)})
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
            snapshot=snapshot,
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
            stage_errors.append({"stage": "report", "status": "failed", "error": str(e)})
            logger.warning(f"[PriorArtCheck] Failed to save report: {e}")
    else:
        report = f"Retrieval evaluation completed. Results: {run_mgr.run_dir / 'ranked_candidates.json'}"
        logger.info('[PriorArtCheck] Retrieval evaluation: report stages skipped by mode')

    # ========== Phase 5: Fulltext Statistics Summary ==========
    # Log summary of PDF/sections status for debugging and observability
    try:
        pdf_attempted = sum(1 for p in state.papers.values() if getattr(p, 'pdf_status', 'not_attempted') != 'not_attempted')
        pdf_downloaded = sum(1 for p in state.papers.values() if getattr(p, 'pdf_status', '') == 'downloaded')
        pdf_not_found = sum(1 for p in state.papers.values() if getattr(p, 'pdf_status', '') == 'not_found')
        pdf_error = sum(1 for p in state.papers.values() if getattr(p, 'pdf_status', '') == 'error')
        sections_parsed = sum(1 for p in state.papers.values() if getattr(p, 'section_status', '') == 'parsed')
        sections_error = sum(1 for p in state.papers.values() if getattr(p, 'section_status', '') == 'parse_error')

        # Count papers with abstract-only assessment among high-relevance papers
        abstract_only_high_relevance = sum(
            1 for m in matches 
            if m.scoring_status == "succeeded" and m.coverage_level is not None and m.coverage_level >= 2 and (
                getattr(state.papers.get(m.paper_id), 'pdf_status', 'not_attempted') != 'downloaded' or
                getattr(state.papers.get(m.paper_id), 'section_status', 'not_attempted') != 'parsed'
            )
        )

        logger.info(
            f"[Fulltext Stats] PDFs: attempted={pdf_attempted}, downloaded={pdf_downloaded}, "
            f"not_found={pdf_not_found}, error={pdf_error}. "
            f"Sections: parsed={sections_parsed}, parse_error={sections_error}. "
            f"High-relevance w/ abstract-only: {abstract_only_high_relevance}"
        )

        # Also log to run directory for later analysis
        run_mgr.append_search_log("fulltext_stats", {
            "pdf_attempted": pdf_attempted,
            "pdf_downloaded": pdf_downloaded,
            "pdf_not_found": pdf_not_found,
            "pdf_error": pdf_error,
            "sections_parsed": sections_parsed,
            "sections_error": sections_error,
            "abstract_only_high_relevance": abstract_only_high_relevance,
        })
    except Exception as e:
        logger.debug(f"[PriorArtCheck] Failed to compute fulltext stats: {e}")

    issues = list(stage_errors)
    for pid, entry in snapshot["entries"].items():
        if entry["status"] in ("failed", "invalid_output"):
            issues.append({"stage": "abstract_scoring", "paper_id": pid, "status": entry["status"]})
        if entry["fulltext_assessment"]["status"] == "failed":
            issues.append({"stage": "fulltext_refinement", "paper_id": pid, "status": "failed"})
    for pid, paper in state.papers.items():
        material_errors = {name: getattr(paper, name, None) for name in (
            'pdf_status', 'pages_status', 'section_status', 'sections_status')
            if getattr(paper, name, None) in ('error', 'parse_error')}
        if material_errors:
            issues.append({'stage': 'fulltext_material', 'paper_id': pid,
                           'status': 'failed', 'material_errors': material_errors})
    for name in ("method_exp_batch_status.jsonl", "method_exp_corpus_status.jsonl", "intro_rw_summary_status.jsonl"):
        path = run_mgr.run_dir / "synthesis" / name
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if item.get("status") in ("failed", "partial", "missing", "parse_error", "error"):
                        issues.append({"stage": name, **item})
    key_selection_path = run_mgr.run_dir / "synthesis/key_papers_selection.json"
    if key_selection_path.exists():
        selection = json.loads(key_selection_path.read_text(encoding="utf-8"))
        if selection.get("selection_audit", {}).get("status") == "FALLBACK":
            issues.append({"stage": "key_paper_selection", "status": "fallback", "error": selection["selection_audit"].get("error")})
    from pipelines.clients.http_transport import summarize_attempts
    http_summary = summarize_attempts(run_mgr.run_dir)
    issues.extend(getattr(run_mgr.runtime, 'retrieval_issues', []))
    for attempt in http_summary["failed_requests"]:
        issues.append({"stage": "retrieval_http", "provider": attempt.get("provider"),
            "status": attempt["status"], "http_status": attempt.get("http_status"),
            "attempt_id": attempt.get("attempt_id"), "logical_request_id": attempt.get("logical_request_id")})
    atomic_write_json(run_mgr.run_dir / "stage_outcomes.json", {"status": "INCOMPLETE" if issues else "SUCCEEDED", "execution_mode": cfg.execution_mode, "report_stages": mode_outcomes['report_stages'], "issues": issues, "http_attempts": http_summary["attempt_count"], "http_logical_requests": http_summary["logical_request_count"]})
    return report


def run_prior_art_check(
    agent, idea_description: str, topic: str,
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
    max_rounds: int = 2, use_citation_graph: bool = True,
    debug_mode: Optional[bool] = None,
    excluded_papers: Optional[List[Dict[str, str]]] = None,
    cfg: Optional[PriorArtConfig] = None,
    run_mgr: Optional[RunManager] = None,
) -> str:
    """Run the configured pipeline with one shared request/attempt coordinator.

    Selection policies and independent model draws remain separate from cache
    recovery. Passing cfg allows serial/parallel offline comparisons without
    changing model prompts or candidate limits.
    """
    import copy
    requested_config = asdict(cfg) if cfg is not None else None
    cfg = copy.deepcopy(cfg) if cfg is not None else PriorArtConfig()
    if cfg.execution_mode not in ('full_report', 'retrieval_eval'):
        raise ValueError('Unknown execution mode; use full_report or retrieval_eval')
    if cfg.execution_mode == 'retrieval_eval' and (type(cfg.retrieval_eval_fulltext_limit) is not int or cfg.retrieval_eval_fulltext_limit != 5):
        raise ValueError('retrieval_eval uses exactly five fixed fulltext attempts without replacement')
    if cfg.execution_mode == 'retrieval_eval':
        for name in ('retrieval_eval_skip_report_evidence', 'retrieval_eval_parallel_seed_discovery',
                     'retrieval_eval_expert_compact_input'):
            if type(getattr(cfg, name)) is not bool:
                raise ValueError(f'{name} must be a boolean')
        if (type(cfg.retrieval_eval_expert_max_output_tokens) is not int
                or not 1024 <= cfg.retrieval_eval_expert_max_output_tokens <= 65536):
            raise ValueError('retrieval_eval_expert_max_output_tokens must be an integer between 1024 and 65536')
        if cfg.retrieval_eval_pdf_text_mode not in ('snippets', 'full_text'):
            raise ValueError('retrieval_eval PDF text mode must be snippets or full_text')
        if (type(cfg.retrieval_eval_pdf_max_snippets) is not int
                or not 1 <= cfg.retrieval_eval_pdf_max_snippets <= 12
                or type(cfg.retrieval_eval_pdf_snippet_chars) is not int
                or not 256 <= cfg.retrieval_eval_pdf_snippet_chars <= 4000):
            raise ValueError('retrieval_eval PDF snippet budget must be 1-12 snippets of 256-4000 characters')
    if cfg.retrieval_backend not in ('legacy_openalex', 's2_arxiv_crossref_v1'):
        raise ValueError('Unknown retrieval backend')
    if cfg.retrieval_backend == 'legacy_openalex' and not cfg.enable_openalex:
        raise ValueError('Disabling OpenAlex requires the replacement retrieval backend')
    if not 1 <= cfg.s2_batch_size <= 500 or cfg.http_max_wait_seconds <= 0:
        raise ValueError('Invalid batch size or HTTP wait budget')
    if debug_mode is not None:
        cfg.debug_mode = debug_mode
    if excluded_papers is not None:
        cfg.excluded_papers = copy.deepcopy(excluded_papers)
    if cfg.llm_tokens_per_minute is not None:
        raise ValueError("Pipeline TPM admission requires per-request token reservations; use explicit runtime reservations or RPM/in-flight limits")
    import math
    if (isinstance(cfg.expert_retrieval_timeout_seconds, bool)
            or not isinstance(cfg.expert_retrieval_timeout_seconds, (int, float))
            or not math.isfinite(cfg.expert_retrieval_timeout_seconds)
            or cfg.expert_retrieval_timeout_seconds <= 0):
        raise ValueError('expert_retrieval_timeout_seconds must be positive and finite')
    if cfg.overlap_refine_method:
        raise ValueError("Method/refinement stage overlap has not passed the frozen-corpus dependency gate")
    if cfg.citation_neighbor_filter not in {"llm", "lexical"}:
        raise ValueError("citation_neighbor_filter must be llm or lexical")
    if type(cfg.enable_crossref_references) is not bool:
        raise ValueError("enable_crossref_references must be a boolean")
    if cfg.enable_crossref_references and (not cfg.staged_retrieval
                                          or cfg.retrieval_backend != 's2_arxiv_crossref_v1'
                                          or cfg.citation_neighbor_filter != 'llm'):
        raise ValueError("Crossref reference ingestion requires staged retrieval, the replacement backend and LLM citation scoring")
    if type(cfg.citation_neighbor_batch_size) is not int or not 1 <= cfg.citation_neighbor_batch_size <= 20:
        raise ValueError("citation_neighbor_batch_size must be an integer between 1 and 20")
    if cfg.selection_policy_version not in {"legacy_observed_v1", RELEVANCE_POLICY}:
        raise ValueError("Unknown selection policy version; algorithm changes require an independent reference")
    if cfg.staged_retrieval:
        if cfg.selection_policy_version != RELEVANCE_POLICY or not cfg.reuse_llm_across_stages:
            raise ValueError('Staged retrieval requires relevance_protected_v1 and exact cross-stage reuse')
        if cfg.retrieval_backend != 's2_arxiv_crossref_v1':
            raise ValueError('Staged retrieval requires the scholarly replacement backend')
        if (cfg.max_candidates < 1 or cfg.retriever_protect_top_k < 0
                or not 0 <= cfg.protected_candidate_ratio <= 1
                or not 0 <= cfg.recent_min_quota_ratio <= cfg.recent_max_quota_ratio <= 1
                or cfg.missing_abstract_reserve < 0 or cfg.metadata_shortlist_size < 1
                or cfg.metadata_boundary_buffer < 0 or cfg.citation_new_candidate_limit < 0):
            raise ValueError('Invalid staged candidate budgets')
    elif cfg.selection_policy_version != 'legacy_observed_v1':
        raise ValueError('Relevance selection policy requires staged_retrieval')
    if cfg.resume_same_task:
        raise ValueError("Pipeline replay is not enabled; use validated runtime task recovery or saved checkpoints explicitly")
    manager = run_mgr or RunManager(topic=topic, shared_pdf_cache_dir=cfg.shared_pdf_cache_dir)
    if cfg.shared_pdf_cache_dir is not None:
        manager.pdf_pages_cache_dir = Path(cfg.shared_pdf_cache_dir).resolve()
        manager.pdf_pages_cache_dir.mkdir(parents=True, exist_ok=True)
    manager.create_run_directory()
    runtime = manager.runtime
    runtime.retrieval_policy = {
        'retrieval_backend': cfg.retrieval_backend,
        'staged_retrieval': cfg.staged_retrieval,
        'enable_openalex': cfg.enable_openalex,
        'enable_arxiv_search': cfg.enable_arxiv_search,
        'expert_retrieval_timeout_seconds': cfg.expert_retrieval_timeout_seconds,
        'http_max_wait_seconds': cfg.http_max_wait_seconds,
        'optimize_scholarly_requests': cfg.optimize_scholarly_requests,
        'shared_http_connections': cfg.shared_http_connections,
    }
    runtime.retrieval_issues = []
    runtime.scheduler = RequestScheduler(max_in_flight=cfg.max_in_flight_requests,
        max_pending=cfg.max_pending_requests, rpm=cfg.llm_requests_per_minute,
        tpm=cfg.llm_tokens_per_minute, total_attempts=cfg.max_total_attempts)
    request = {"idea_description": idea_description, "topic": topic, "years": years,
               "venues": venues, "max_rounds": max_rounds, "use_citation_graph": use_citation_graph,
               "effective_config": asdict(cfg)}
    atomic_write_json(manager.run_dir / "request_input.json", {"entry_arguments": {
        "idea_description": idea_description, "topic": topic, "years": years, "venues": venues,
        "max_rounds": max_rounds, "use_citation_graph": use_citation_graph, "debug_mode": debug_mode,
        "excluded_papers": excluded_papers, "config": requested_config}, "effective_request": request})
    runtime.ledger.write_manifest({"raw_request": request})
    try:
        with use_runtime(runtime):
            report = _run_prior_art_check_impl(agent, idea_description, topic, years, venues,
                max_rounds, use_citation_graph, cfg.debug_mode, cfg.excluded_papers, cfg, manager)
        required = [("request_input", manager.run_dir / "request_input.json"), ("idea_spec", manager.run_dir / "idea_spec.json"),
                    ("assessment_snapshot", manager.run_dir / "assessment_snapshot.json"),
                    ("paper_registry", manager.run_dir / "paper_registry.json"),
                    ("stage_outcomes", manager.run_dir / "stage_outcomes.json")]
        required.append(('mode_outcomes', manager.run_dir / 'mode_outcomes.json'))
        if cfg.execution_mode == 'retrieval_eval':
            required.extend((name, manager.run_dir / name) for name in (
                'ranked_candidates.json', 'ranked_candidates.md', 'ranking_scope.json',
                'checkpoints/before_fulltext.json', 'checkpoints/after_fulltext.json',
                'eval_fulltext_plan.json', 'expert_ranking_input.json', 'expert_ranking.json'))
            expert_path = manager.run_dir / 'expert_ranking.json'
            if expert_path.exists():
                expert_result = json.loads(expert_path.read_text(encoding='utf-8'))
                if expert_result.get('model_payload_sha256') or expert_result.get('status') == 'SUCCEEDED':
                    required.append(('expert_ranking_model_payload.json',
                                     manager.run_dir / 'expert_ranking_model_payload.json'))
        else:
            if cfg.enable_synthesis_json:
                required.append(("synthesis", manager.run_dir / "synthesis/synthesis_input.json"))
            if cfg.enable_method_exp_snippets:
                required.extend((name, manager.run_dir / "synthesis" / name) for name in (
                    "method_exp_snippets.jsonl", "method_exp_batch_status.jsonl", "method_exp_corpus_status.jsonl", "method_exp_status.json"))
            if cfg.enable_intro_rw_full:
                selection_path = manager.run_dir / "synthesis/key_papers_selection.json"
                required.append(("key_papers_selection", selection_path))
                if selection_path.exists():
                    selection = json.loads(selection_path.read_text(encoding="utf-8"))
                    required.extend(("intro_rw_full:" + pid, manager.run_dir / "synthesis/intro_rw_full" / (pid.replace("/", "_").replace(":", "_") + ".json"))
                        for pid in selection.get("per_paper_roles", {}))
            snapshot = json.loads((manager.run_dir / "assessment_snapshot.json").read_text(encoding="utf-8"))
            if snapshot["buckets"]["high_overlap"] or snapshot["buckets"]["partial_overlap"]:
                required.extend((name, manager.run_dir / "synthesis" / name) for name in ("intro_rw_summaries.jsonl", "intro_rw_summary_status.jsonl"))
            reports = list(manager.md_reports_dir.glob("*.md"))
            required.extend(("report:" + p.name, p) for p in reports)
            if not reports:
                required.append(("report", manager.md_reports_dir / "missing_report.md"))
        completion = runtime.ledger.finalize(required)
        outcomes = json.loads((manager.run_dir / "stage_outcomes.json").read_text(encoding="utf-8"))
        completion["business_outcome"] = outcomes
        completion["execution_mode"] = cfg.execution_mode
        if outcomes["status"] != "SUCCEEDED":
            completion["status"] = "INCOMPLETE"
        atomic_write_json(manager.run_dir / "completion.json", completion)
        if completion["status"] != "SUCCEEDED":
            logger.warning("Prior Art has incomplete/failed tasks; see completion.json")
        return report
    except Exception as exc:
        runtime.ledger.event("pipeline_failed", error_type=type(exc).__name__, error=str(exc))
        completion = runtime.ledger.finalize([("required_final_snapshot", manager.run_dir / "assessment_snapshot.json")])
        completion.update(status="FAILED", error_type=type(exc).__name__, error=str(exc))
        atomic_write_json(manager.run_dir / "completion.json", completion)
        raise
    finally:
        from pipelines.clients.http_transport import close_http_connections
        close_http_connections(runtime)
