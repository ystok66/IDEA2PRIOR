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
)

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
import ast
from typing import Any


def _extract_text_from_llm_response(resp: Any) -> str:
    """
    Normalize an LLM response to a plain text string.

    This function handles:
    - smolagents / LiteLLM ChatMessage objects (using `.content`)
    - Arbitrary objects by falling back to `str(resp)`
    - Markdown fenced code blocks like ```json ... ```
    - Optional language hints (e.g. "json" on the first line inside the fence)

    The goal is to obtain a clean JSON string that can be passed to
    json.loads() or ast.literal_eval().
    """
    # 1. Extract raw text content
    if hasattr(resp, "content") and isinstance(getattr(resp, "content"), str):
        text = getattr(resp, "content")
    else:
        text = str(resp)

    text = text.strip()

    # 2. Strip HTML-like <code> ... </code> wrapper if present (defensive)
    if "<code>" in text and "</code>" in text:
        start = text.find("<code>") + len("<code>")
        end = text.rfind("</code>")
        if end > start:
            text = text[start:end].strip()

    # 3. Strip Markdown fenced code blocks ```...```
    if "```" in text:
        first = text.find("```")
        last = text.rfind("```")
        if last > first:
            inner = text[first + 3 : last].strip()
            # Handle optional language marker on the first line (e.g., "json")
            first_newline = inner.find("\n")
            if first_newline != -1:
                first_line = inner[:first_newline].strip().lower()
                # If the first line looks like a language hint, drop it
                if first_line in {"json", "javascript", "js", "python", "py"}:
                    inner = inner[first_newline + 1 :].lstrip()
            text = inner.strip()

    return text


def seed_papers_from_llm(
    agent,
    idea_description: str,
    topic: str,
    max_seeds: int = 10,
) -> List[Dict[str, str]]:
    """
    Use the LLM's own world knowledge to propose a small set of
    potentially relevant papers (as "seed" candidates).

    IMPORTANT:
    - The LLM is allowed to hallucinate here. These seeds must be
      validated later via OpenAlex / Semantic Scholar by title.
    - This function should only return lightweight metadata:
      title, venue, year, and optional ids (doi / arxiv_id / url).
    
    Uses 'expert' LLM role for deep knowledge recall.
    """
    # Instantiate expert model
    model_id, api_base, api_key = get_litellm_config(role="expert")
    expert_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    prompt = f"""
You are an expert researcher in {topic}.
Based only on your own knowledge (do NOT use tools),
list up to {max_seeds} real papers that are most related
to the following idea.

For each paper, provide:
- title
- venue (conference or journal)
- year (integer, or null if unknown)
- one of: doi / arxiv_id / url (if you know it, else null)

Return ONLY raw JSON, no backticks, in the following format:
[
  {{
    "title": "...",
    "venue": "...",
    "year": 2024,
    "doi": "10.xxx/yyy" or null,
    "arxiv_id": "2312.00752" or null,
    "url": "https://..." or null
  }},
  ...
]

Research idea:
{idea_description}

JSON:
"""
    messages = [{"role": "user", "content": prompt}]
    resp = expert_model(messages)
    text = _extract_text_from_llm_response(resp)

    # Parse JSON safely; fallback to empty list on error.
    try:
        data = json.loads(text)
    except Exception:
        logger.warning("[PriorArt] Failed to parse LLM seed papers JSON.")
        return []

    if not isinstance(data, list):
        return []

    seeds: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        seeds.append(
            {
                "title": title,
                "venue": str(item.get("venue", "")).strip() or None,
                "year": item.get("year", None),
                "doi": str(item.get("doi") or "").strip() or None,
                "arxiv_id": str(item.get("arxiv_id") or "").strip() or None,
                "url": str(item.get("url") or "").strip() or None,
            }
        )

    logger.info(f"[PriorArt] LLM proposed {len(seeds)} seed papers (unvalidated).")
    return seeds


def validate_llm_seeds_via_openalex(
    seeds: List[Dict[str, str]],
    max_workers: int = 1,
) -> List[Paper]:
    """
    For each LLM-proposed seed (title-based), use OpenAlex's "title.search"
    filter to find the best-matching real paper. If a good match is found,
    convert it into our internal Paper model and return.

    This function is responsible for removing hallucinated papers.
    
    Args:
        seeds: List of seed paper dicts from LLM (with 'title' key)
        max_workers: Number of parallel threads (1 = serial, >1 = parallel)
    """
    validated_papers: List[Paper] = []

    def _validate_single_seed(seed: Dict[str, str]) -> Optional[Paper]:
        """Validate a single seed paper via OpenAlex. Returns Paper or None."""
        # Instantiate tool locally for thread safety
        oa_tool = OpenAlexWorksSearchTool()
        title = seed["title"]
        try:
            res_json = oa_tool.forward(query=title, limit=1)
            if not res_json or res_json.startswith("Error"):
                return None
                
            works = json.loads(res_json)
            if not works:
                return None
                
            best_match = works[0]
            match_title = best_match.get("title", "")
            
            # Simple validation: check if titles are somewhat similar
            seed_words = set(title.lower().split())
            match_words = set(match_title.lower().split())
            overlap = len(seed_words & match_words)
            if overlap / len(seed_words) < 0.5:  # Loose threshold
                return None

            # Create Paper object
            doi = best_match.get("doi")
            if doi and "doi.org/" in doi:
                doi = doi.split("doi.org/")[-1]
                
            return Paper(
                title=match_title,
                year=best_match.get("year"),
                venue=best_match.get("venue"),
                openalex_id=best_match.get("openalex_id"),
                doi=doi,
                citation_count=best_match.get("cited_by_count")
            )
            
        except Exception as e:
            logger.warning(f"Failed to validate seed '{title}': {e}")
            return None

    # Serial execution (default, for debug mode)
    if max_workers <= 1:
        for seed in seeds:
            paper = _validate_single_seed(seed)
            if paper:
                validated_papers.append(paper)
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_seed = {executor.submit(_validate_single_seed, s): s for s in seeds}
            for fut in as_completed(future_to_seed):
                seed = future_to_seed[fut]
                try:
                    paper = fut.result()
                    if paper:
                        validated_papers.append(paper)
                except Exception as e:
                    logger.warning(f"[Parallel] Error validating seed '{seed.get('title', 'unknown')}': {e}")
            
    logger.info(f"[PriorArt] Validated {len(validated_papers)}/{len(seeds)} seed papers.")
    return validated_papers


def generate_search_queries_from_idea(
    agent,
    idea_description: str,
    topic: str,
    num_queries: int = 6,
) -> List[str]:
    """
    Ask the LLM to synthesize a small set of diverse search queries
    that can be used in scholarly APIs (OpenAlex, Semantic Scholar).

    These queries should mix:
    - core task names
    - typical keywords used in titles
    - dataset / modality names
    - plausible synonyms and phrases

    The output will later be used to drive the exploration loop.
    
    Uses 'expert' LLM role for broad domain knowledge.
    """
    # Instantiate expert model
    model_id, api_base, api_key = get_litellm_config(role="expert")
    expert_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    prompt = f"""
You are an expert in {topic}.

Generate {num_queries} diverse search queries for OpenAlex/Semantic Scholar
based on the research idea below.

CRITICAL RULES:
- Each query should use at MOST ONE exact phrase in quotes (e.g., "monocular 3D detection")
- Remaining keywords should be unquoted, separated by spaces
- Avoid multiple quoted phrases in the same query (this kills recall)
- Mix broad queries (just topic) with more specific queries (topic + 1-2 keywords)
- Keep queries simple and focused

GOOD examples:
- "monocular 3D detection"
- "monocular 3D detection" rotation augmentation
- monocular 3d object detection geometric consistency
- 3D object detection data augmentation

BAD examples (too strict):
- "monocular 3D detection" "rotation augmentation" "depth-free"
- "exact long phrase" "another exact phrase"

Return ONLY raw JSON list of strings:
["query 1", "query 2", ...]

Research idea:
{idea_description}

JSON:
"""
    messages = [{"role": "user", "content": prompt}]
    resp = expert_model(messages)
    text = _extract_text_from_llm_response(resp)

    try:
        data = json.loads(text)
    except Exception:
        logger.warning("[PriorArt] Failed to parse search queries JSON, falling back to topic.")
        return [topic]

    if not isinstance(data, list):
        return [topic]

    queries = [str(q).strip() for q in data if str(q).strip()]
    if not queries:
        return [topic]

    logger.info(f"[PriorArt] Generated {len(queries)} search queries from idea.")
    
    # Ensure we always have at least one simple topic-based query for recall
    if not any(q.count('"') <= 2 and len(q.split()) <= 5 for q in queries):
        queries.insert(0, topic)
        logger.info(f"[PriorArt] Added topic-only fallback query: '{topic}'")
    
    return queries[:num_queries]


# ========== Block 1: Enhanced Candidate Retrieval ==========

def generate_seed_queries_llm(
    agent,
    topic: str,
    idea: str,
    facets: List[str],
    cfg: PriorArtConfig,
) -> List[str]:
    """
    Use LLM to generate diversified short search queries from topic + idea + facets.
    
    Output: 4-8 short, scholar-friendly queries covering different angles:
    - Problem/task perspective
    - Method keywords
    - Recent buzzwords with year hints
    - Facet-aligned queries
    
    Uses 'expert' LLM role for broad domain knowledge.
    """
    max_queries = cfg.debug_max_seed_queries if cfg.debug_mode else cfg.max_seed_queries
    
    facet_text = "\n".join(f"- {f}" for f in facets) if facets else "N/A"
    
    prompt = f"""You are generating search queries for academic paper retrieval.

Research topic: {topic}
Research idea: {idea[:600]}

Key facets:
{facet_text}

TASK: Generate {max_queries} short, diversified search queries suitable for Google Scholar / arXiv / Semantic Scholar.

Guidelines:
- Keep each query SHORT (3-8 words, like search engine queries)
- Cover different angles: task/problem, method, technique, application
- Include at least 1-2 queries with recent keywords or year hints (2023, 2024)
- Avoid long natural language sentences
- Do NOT use quotes or boolean operators

Output strictly valid JSON list of strings:
["query1", "query2", ...]
"""
    
    # Instantiate expert model
    model_id, api_base, api_key = get_litellm_config(role="expert")
    expert_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    try:
        messages = [{"role": "user", "content": prompt}]
        resp = expert_model(messages)
        text = _extract_text_from_llm_response(resp)
        
        queries = json.loads(text)
        if isinstance(queries, list):
            cleaned = [str(q).strip() for q in queries if str(q).strip()]
            logger.info(f"[Block1] Generated {len(cleaned)} seed queries from LLM.")
            return cleaned[:max_queries]
    except Exception as e:
        logger.warning(f"[Block1] Failed to generate seed queries: {e}")
    
    # Fallback: use topic + facets
    fallback = [topic] + facets[:3]
    return fallback


def retrieve_candidates_with_buckets(
    seed_queries: List[str],
    state: PipelineState,
    cfg: PriorArtConfig,
    year_start: int,
    year_end: int,
) -> Dict[str, Paper]:
    """
    Search main + recent buckets for each seed query, merge and deduplicate.
    
    Main bucket: Full year range, standard search
    Recent bucket: year >= recent_year_threshold, prioritized
    
    Returns dict of paper_id -> Paper for deduplication.
    """
    from tools.openalex_search import OpenAlexWorksSearchTool
    
    openalex = OpenAlexWorksSearchTool()
    all_papers: Dict[str, Paper] = {}
    
    k_main = 15  # Papers per query for main bucket
    k_recent = 10  # Papers per query for recent bucket
    
    main_total = 0
    recent_total = 0
    
    for q_idx, query in enumerate(seed_queries):
        # Main bucket search
        try:
            main_results = openalex.forward(
                query=query,
                from_year=year_start,
                to_year=year_end,
                limit=k_main
            )
            
            # Debug: log raw output (first 500 chars)
            if cfg.debug_mode:
                logger.debug(f"[Block1] Query '{query}' main raw: {main_results[:500]}")
            
            main_papers = _parse_openalex_results(main_results, bucket="main", query_id=f"q{q_idx}")
            main_total += len(main_papers)
            
            new_main = 0
            for p in main_papers:
                paper_key = p.openalex_id or p.doi or p.title
                if paper_key and paper_key not in all_papers:
                    all_papers[paper_key] = p
                    new_main += 1
            
            logger.info(f"[Block1] Query '{query[:40]}...' main: parsed={len(main_papers)}, new={new_main}")
            
        except Exception as e:
            logger.warning(f"[Block1] Main bucket search failed for query '{query}': {e}")
        
        # Recent bucket search (year >= threshold)
        recent_start = max(year_start, cfg.recent_year_threshold)
        if recent_start <= year_end:
            try:
                recent_results = openalex.forward(
                    query=query,
                    from_year=recent_start,
                    to_year=year_end,
                    limit=k_recent
                )
                
                # Debug: log raw output (first 500 chars)
                if cfg.debug_mode:
                    logger.debug(f"[Block1] Query '{query}' recent raw: {recent_results[:500]}")
                
                recent_papers = _parse_openalex_results(recent_results, bucket="recent", query_id=f"q{q_idx}")
                recent_total += len(recent_papers)
                
                new_recent = 0
                for p in recent_papers:
                    paper_key = p.openalex_id or p.doi or p.title
                    if paper_key and paper_key not in all_papers:
                        all_papers[paper_key] = p
                        new_recent += 1
                
                logger.info(f"[Block1] Query '{query[:40]}...' recent: parsed={len(recent_papers)}, new={new_recent}")
                
            except Exception as e:
                logger.warning(f"[Block1] Recent bucket search failed for query '{query}': {e}")
    
    logger.info(f"[Block1] Total retrieved: {len(all_papers)} unique papers "
                f"(main_parsed={main_total}, recent_parsed={recent_total}) from {len(seed_queries)} queries")
    return all_papers


def _parse_openalex_results(
    results_str: str,
    bucket: str = "main",
    query_id: str = ""
) -> List[Paper]:
    """
    Parse OpenAlex tool output into Paper objects.
    
    The tool returns JSON (list of dicts), not markdown.
    Each dict has: title, year, venue, authors, cited_by_count, openalex_id, doi
    """
    papers = []
    
    if not results_str or results_str.startswith("Error"):
        logger.warning(f"[Block1] OpenAlex returned error or empty: {results_str[:100]}")
        return papers
    
    # Parse JSON output
    try:
        data = json.loads(results_str)
    except json.JSONDecodeError as e:
        logger.warning(f"[Block1] Failed to parse OpenAlex JSON: {e}. Raw: {results_str[:200]}")
        return papers
    
    if not isinstance(data, list):
        logger.warning(f"[Block1] OpenAlex returned non-list: {type(data)}")
        return papers
    
    for item in data:
        if not isinstance(item, dict):
            continue
        
        title = item.get("title")
        if not title:
            continue
        
        # Extract OpenAlex ID (strip URL prefix if present)
        openalex_id = item.get("openalex_id", "")
        if openalex_id and openalex_id.startswith("https://openalex.org/"):
            openalex_id = openalex_id.replace("https://openalex.org/", "")
        
        # Extract DOI (strip URL prefix if present)
        doi = item.get("doi", "")
        if doi and doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")
        
        paper = Paper(
            title=title,
            year=item.get("year"),
            venue=item.get("venue"),
            citation_count=item.get("cited_by_count", 0),
            doi=doi if doi else None,
            openalex_id=openalex_id if openalex_id else None,
            retrieval_source=f"block1_{bucket}_{query_id}",
        )
        papers.append(paper)
    
    logger.debug(f"[Block1] Parsed {len(papers)} papers from OpenAlex JSON (bucket={bucket}, query={query_id})")
    return papers


def _dict_to_paper(d: Dict[str, Any], bucket: str, query_id: str) -> Paper:
    """Convert parsed dict to Paper object."""
    return Paper(
        title=d.get("title", "Unknown"),
        year=d.get("year"),
        venue=d.get("venue"),
        citation_count=d.get("citation_count", 0),
        doi=d.get("doi"),
        openalex_id=d.get("openalex_id"),
        retrieval_source=f"openalex_{bucket}_{query_id}",
    )


def refine_queries_llm(
    agent,
    topic: str,
    idea: str,
    top_papers: List[Paper],
    facets: List[str],
    cfg: PriorArtConfig,
) -> List[str]:
    """
    Generate refined queries focusing on under-covered angles and recent work.
    
    Uses top-K initial candidates to identify gaps, then generates queries
    prioritizing the recent bucket.
    """
    if not cfg.enable_hierarchical:
        return []
    
    # Build context from top papers
    paper_titles = [p.title for p in top_papers[:cfg.top_k_for_refine]]
    paper_context = "\n".join(f"- {t}" for t in paper_titles[:10])
    facet_text = "\n".join(f"- {f}" for f in facets) if facets else "N/A"
    
    prompt = f"""You are refining search queries to find MORE relevant papers, especially RECENT ones (2023-2024).

Research topic: {topic}
Idea: {idea[:400]}

Key facets:
{facet_text}

Papers already found:
{paper_context}

TASK: Generate {cfg.max_refine_queries} additional SHORT search queries that:
1. Cover angles NOT well-represented in the papers above
2. Use synonyms or alternative terminology
3. Include recent buzzwords (foundation models, diffusion, etc. if applicable)
4. Target recent papers (add year hints like "2024" if helpful)

Output strictly valid JSON list of strings:
["query1", "query2", ...]
"""
    
    # Instantiate heavy model for refined query generation
    model_id, api_base, api_key = get_litellm_config(role="heavy")
    heavy_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    try:
        messages = [{"role": "user", "content": prompt}]
        resp = heavy_model(messages)
        text = _extract_text_from_llm_response(resp)
        
        queries = json.loads(text)
        if isinstance(queries, list):
            cleaned = [str(q).strip() for q in queries if str(q).strip()]
            logger.info(f"[Block1] Generated {len(cleaned)} refined queries.")
            return cleaned[:cfg.max_refine_queries]
    except Exception as e:
        logger.warning(f"[Block1] Failed to generate refined queries: {e}")
    
    return []


def apply_citation_recency_quota(
    papers: Dict[str, Paper],
    cfg: PriorArtConfig,
) -> List[Paper]:
    """
    Score and select candidates with quota guarantees for recent papers.
    
    Logic:
    1. Separate papers into recent (year >= threshold) and main buckets
    2. Sort each bucket by citation count (descending)
    3. Enforce minimum recent quota (30% by default)
    4. Fill remaining slots from mixed pool, capping recent at max ratio
    5. Log clear warnings if quotas cannot be satisfied
    """
    if not papers:
        return []
    
    paper_list = list(papers.values())
    
    # Helper to check if a paper is "recent"
    def is_recent(p: Paper) -> bool:
        return bool(p.year and p.year >= cfg.recent_year_threshold)
    
    # Separate into buckets
    recent_papers = [p for p in paper_list if is_recent(p)]
    main_papers = [p for p in paper_list if not is_recent(p)]
    
    # Sort each bucket by citation count (descending)
    recent_sorted = sorted(recent_papers, key=lambda p: p.citation_count or 0, reverse=True)
    main_sorted = sorted(main_papers, key=lambda p: p.citation_count or 0, reverse=True)
    
    # Compute target counts
    total_available = len(paper_list)
    max_total = min(total_available, cfg.max_candidates)
    
    target_recent_min = int(math.ceil(cfg.recent_min_quota_ratio * max_total))
    target_recent_max = int(math.floor(cfg.recent_max_quota_ratio * max_total))
    
    logger.info(f"[Block1] Quota targets: total={max_total}, recent_min={target_recent_min}, "
                f"recent_max={target_recent_max}, available_recent={len(recent_sorted)}, "
                f"available_main={len(main_sorted)}")
    
    # Build final selection
    final_list: List[Paper] = []
    
    # Step 1: Take as many recent papers as possible up to max, but at least min
    n_recent_available = len(recent_sorted)
    n_recent_to_take = min(n_recent_available, target_recent_max)
    
    # Log warning if we can't meet minimum
    if n_recent_available < target_recent_min:
        logger.warning(f"[Block1] WARNING: Only {n_recent_available} recent papers available, "
                      f"cannot meet min_quota of {target_recent_min} ({cfg.recent_min_quota_ratio*100:.0f}%). "
                      f"Will use all available recent papers.")
    
    # Add recent papers
    recent_selected = recent_sorted[:n_recent_to_take]
    final_list.extend(recent_selected)
    
    # Step 2: Fill remaining slots from main bucket
    remaining_slots = max_total - len(final_list)
    main_selected = main_sorted[:remaining_slots]
    final_list.extend(main_selected)
    
    # Compute final stats
    final_recent_count = len(recent_selected)
    final_main_count = len(main_selected)
    final_total = len(final_list)
    actual_recent_ratio = final_recent_count / max(1, final_total)
    
    logger.info(f"[Block1] Quota result: total={final_total}, "
                f"recent={final_recent_count} ({actual_recent_ratio*100:.1f}%), "
                f"main={final_main_count}")
    
    return final_list


# Need math for log in scoring
import math


def call_llm_json(agent_or_model, prompt: str) -> str:
    """
    Use the underlying chat model directly, bypassing CodeAgent.run().
    Assumes `agent.model` is a LiteLLMModel-compatible chat model.
    """
    messages = [{"role": "user", "content": prompt}]
    # smolagents.LiteLLMModel supports direct __call__(messages) -> str
    if hasattr(agent_or_model, "model"):
        return agent_or_model.model(messages)
    return agent_or_model(messages)


def decompose_idea_into_facets(agent, idea_description: str) -> List[str]:
    """
    Use the LLM to decompose the idea into 3-7 key technical facets.

    Each facet should be a short phrase describing a distinct element of the idea.
    
    Uses 'light' LLM role for efficiency.
    """
    logger.info("[PriorArt] Decomposing idea into facets...")
    
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

Given the following research idea, please extract 3-7 key technical facets that characterize this idea.

A "facet" is a distinct technical component, method, assumption, or contribution of the idea.

Research Idea:
{idea_description}

Please output ONLY a JSON list of strings, where each string is one facet. Example format:
["facet 1", "facet 2", "facet 3"]

JSON output:"""

    try:
        messages = [{"role": "user", "content": prompt}]
        result = light_model(messages)
        result_str = _extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw facet output (cleaned): {result_str[:1000]!r}")
        
        facets: List[str] = []
        try:
            parsed = json.loads(result_str)
        except Exception:
            try:
                parsed = ast.literal_eval(result_str)
            except Exception:
                logger.warning("[PriorArt] Failed to parse facets as JSON/AST, using line-split fallback")
                # Very last-resort fallback: try to get one facet per line
                for line in result_str.splitlines():
                    line = line.strip().strip("-•* \t")
                    if not line:
                        continue
                    facets.append(line)
            else:
                # ast.literal_eval succeeded
                if isinstance(parsed, list):
                    facets = [str(x).strip() for x in parsed if str(x).strip()]
                else:
                    facets = [str(parsed).strip()]
        else:
            # json.loads succeeded
            if isinstance(parsed, list):
                facets = [str(x).strip() for x in parsed if str(x).strip()]
            else:
                facets = [str(parsed).strip()]

        logger.info(f"[PriorArt] Extracted {len(facets)} facets from idea")
        return facets[:7]
        
    except Exception as e:
        logger.error(f"Failed to decompose idea into facets: {e}")
    
    # Ultimate fallback: use the whole idea as a single facet
    return [idea_description[:100]]


def extract_idea_features(agent, idea_description: str) -> Dict[str, Set[str]]:
    """
    Extract structural features (datasets, metrics, method families) from the idea description.
    
    Uses 'light' LLM role for efficiency.
    """
    logger.info("[PriorArt] Extracting structural features from idea...")
    
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

Given the following research idea, please extract the key structural features:
1. Datasets (e.g., KITTI, nuScenes, ImageNet)
2. Metrics (e.g., mAP, F1, Accuracy)
3. Method Families (e.g., Transformer, CNN, Diffusion)

Research Idea:
{idea_description}

Please output ONLY a JSON object with keys "datasets", "metrics", "method_families". Each value should be a list of strings.
Example: {{"datasets": ["KITTI"], "metrics": ["mAP"], "method_families": ["Transformer"]}}

JSON output:"""

    try:
        messages = [{"role": "user", "content": prompt}]
        result = light_model(messages)
        result_str = _extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw idea-struct output (cleaned): {result_str[:1000]!r}")
        
        datasets: Set[str] = set()
        metrics: Set[str] = set()
        families: Set[str] = set()
        
        data = None
        try:
            data = json.loads(result_str)
        except Exception:
            try:
                data = ast.literal_eval(result_str)
            except Exception:
                logger.warning("[PriorArt] Could not parse idea structural features, returning empty sets.")
                return {"datasets": set(), "metrics": set(), "method_families": set()}
        
        if isinstance(data, dict):
            for name, target in [
                ("datasets", datasets),
                ("metrics", metrics),
                ("method_families", families),
            ]:
                if name in data and isinstance(data[name], list):
                    for item in data[name]:
                        s = str(item).strip()
                        if s:
                            target.add(s.lower())
            
            logger.info(
                f"[PriorArt] Idea features parsed: "
                f"datasets={datasets}, metrics={metrics}, methods={families}"
            )
            return {
                "datasets": datasets,
                "metrics": metrics,
                "method_families": families,
            }
        
        logger.warning("[PriorArt] Parsed idea features is not a dict, returning empty sets.")
        
    except Exception as e:
        logger.warning(f"[PriorArt] Failed to extract idea features: {e}")
    
    return {"datasets": set(), "metrics": set(), "method_families": set()}


def compute_structural_overlap(idea_features: Dict[str, Set[str]], paper: Paper) -> Tuple[int, List[str]]:
    """
    Compute structural overlap score (0-3) and return details.
    """
    score = 0
    details = []
    
    # Check Datasets
    paper_datasets = set(d.lower() for d in paper.datasets)
    overlap_datasets = idea_features["datasets"] & paper_datasets
    if overlap_datasets:
        score += 1
        details.append(f"Datasets: {', '.join(overlap_datasets)}")
        
    # Check Metrics
    paper_metrics = set(m.lower() for m in paper.metrics)
    overlap_metrics = idea_features["metrics"] & paper_metrics
    if overlap_metrics:
        score += 1
        details.append(f"Metrics: {', '.join(overlap_metrics)}")
        
    # Check Method Families
    paper_families = set(f.lower() for f in paper.method_families)
    overlap_families = idea_features["method_families"] & paper_families
    if overlap_families:
        score += 1
        details.append(f"Methods: {', '.join(overlap_families)}")
        
    return score, details


def extract_high_level_facets_from_idea(
    agent,
    idea_text: str,
) -> Dict[str, Set[str]]:
    """
    Extract high-level structural facets from the idea description.
    
    Uses 'light' LLM role for efficiency.
    """
    logger.info("[PriorArt] Extracting high-level facets from idea...")
    
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.
Do NOT include any comments, trailing commas, or extra tokens. Only output valid JSON.

Given the following research idea, please extract high-level structural facets.
    
Facet Types:
1. problem_facets: What specific problem/task is it solving? (e.g., "monocular 3D detection", "long-horizon sequence modeling")
2. setting_facets: What is the setting, modality, or protocol? (e.g., "indoor RGB-D scenes", "multi-camera surround view")
3. mechanism_facets: What are the key mechanisms or frameworks? (e.g., "camera-space rotation augmentation", "SSM-based state space modeling")
4. assumption_facets: What are the key assumptions? (e.g., "calibrated pinhole camera", "no depth supervision")

Research Idea:
{idea_text}

Please output ONLY a JSON object with keys "problem_facets", "setting_facets", "mechanism_facets", "assumption_facets".
Each value should be a list of short phrases (3-7 phrases max per category).

JSON output:"""

    try:
        messages = [{"role": "user", "content": prompt}]
        result = light_model(messages)
        result_str = _extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw high-level facets (idea, cleaned): {result_str[:1000]!r}")
        
        data = None
        try:
            # Try standard parsing first
            data = json.loads(result_str)
        except Exception:
            try:
                # Try AST literal eval
                data = ast.literal_eval(result_str)
            except Exception:
                # Fallback: Try to extract JSON-like object via regex if full parse fails
                import re
                match = re.search(r"\{.*\}", result_str, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(0))
                    except:
                        pass
                
                if not data:
                    logger.warning("[PriorArt] Failed to parse high-level facets from idea, using empty sets.")
                    return {ftype: set() for ftype in HIGH_LEVEL_FACET_TYPES}
        
        if isinstance(data, dict):
            facets: Dict[str, Set[str]] = {}
            for ftype in HIGH_LEVEL_FACET_TYPES:
                raw_vals = data.get(ftype, []) or []
                facets[ftype] = {
                    str(p).lower().strip()
                    for p in raw_vals
                    if str(p).strip()
                }
            logger.info(f"[PriorArt] Parsed idea high-level facets: {facets}")
            return facets
            
        logger.warning("[PriorArt] High-level idea facets structure is not a dict, using empty sets.")
        
    except Exception as e:
        logger.warning(f"Failed to extract high-level facets from idea: {e}")
    
    return {ftype: set() for ftype in HIGH_LEVEL_FACET_TYPES}


def _repair_json_output(raw: str) -> str:
    """
    Attempt to repair common LLM JSON output issues.
    
    Handles:
    - Text before/after the JSON object
    - Trailing commas before } or ]
    - Single-line // comments
    - Unquoted newlines in strings (basic)
    """
    import re
    
    # Strip everything before first { and after last }
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1:
        return raw
    trimmed = raw[start:end+1]
    
    # Remove single-line // comments (but not inside strings - rough heuristic)
    trimmed = re.sub(r'//[^\n"]*(?=\n|$)', '', trimmed)
    
    # Remove trailing commas before } or ]
    trimmed = re.sub(r',\s*([}\]])', r'\1', trimmed)
    
    # Remove control characters that break JSON
    trimmed = re.sub(r'[\x00-\x1f\x7f]', ' ', trimmed)
    
    return trimmed


def extract_high_level_facets_from_paper(
    agent,
    paper: Paper,
) -> Dict[str, List[str]]:
    """
    Extract high-level structural facets from a paper (using title/abstract/chunks).
    Always returns Dict[str, List[str]] for compatibility with Paper model.
    """
    # Use light model for facet extraction
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    # Prepare context
    context = f"Title: {paper.title}\n"
    
    # Add a bit of chunk text if available
    if paper.chunks:
        # Use first chunk (intro usually)
        context += f"Intro Snippet: {paper.chunks[0].text[:1000]}\n"
        
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

CRITICAL: Output ONLY valid JSON. No markdown, no comments, no trailing commas, no text before or after.

Given the following paper info, please extract high-level structural facets.

Facet Types:
1. problem_facets: What specific problem/task is it solving?
2. setting_facets: What is the setting, modality, or protocol?
3. mechanism_facets: What are the key mechanisms or frameworks?
4. assumption_facets: What are the key assumptions?

Paper Info:
{context}

Please output ONLY a JSON object with keys "problem_facets", "setting_facets", "mechanism_facets", "assumption_facets".
Each value should be a list of short phrases.

JSON output:"""

    try:
        messages = [{"role": "user", "content": prompt}]
        resp = light_model(messages)
        result_str = _extract_text_from_llm_response(resp)
        logger.debug(
            f"[PriorArt] Raw high-level facets (paper={paper.title[:60]!r}, cleaned): "
            f"{result_str[:1000]!r}"
        )
        
        # 3-layer parsing: direct -> repair -> literal_eval
        data = None
        
        # Layer 1: Direct json.loads
        try:
            data = json.loads(result_str)
        except Exception:
            pass
        
        # Layer 2: Try with JSON repair
        if data is None:
            try:
                repaired = _repair_json_output(result_str)
                data = json.loads(repaired)
                logger.debug(f"[PriorArt] JSON repair succeeded for paper {paper.title[:50]!r}")
            except Exception:
                pass
        
        # Layer 3: Fallback to ast.literal_eval
        if data is None:
            try:
                data = ast.literal_eval(result_str)
            except Exception:
                logger.warning(
                    f"[PriorArt] Failed to parse high-level facets for paper {paper.title!r}, using empty lists."
                )
                return {ftype: [] for ftype in HIGH_LEVEL_FACET_TYPES}
        
        if isinstance(data, dict):
            facets: Dict[str, List[str]] = {}
            for ftype in HIGH_LEVEL_FACET_TYPES:
                raw_vals = data.get(ftype, []) or []
                clean_vals: List[str] = []
                for v in raw_vals:
                    s = str(v).strip()
                    if s:
                        clean_vals.append(s)
                facets[ftype] = clean_vals
            return facets

        logger.warning(
            f"[PriorArt] High-level facets for paper {paper.title!r} is not a dict, using empty lists."
        )

    except Exception as e:
        logger.warning(f"Failed to extract high-level facets from paper {paper.title}: {e}")
        
    return {ftype: [] for ftype in HIGH_LEVEL_FACET_TYPES}



def compute_high_level_facet_overlap(
    idea_facets: Dict[str, Set[str]],
    paper_facets: Dict[str, List[str]],
) -> Tuple[int, Dict[str, List[str]]]:
    """
    Compute overlap score (0-3) and details for high-level facets.
    """
    score = 0
    details = {}
    
    # Convert paper facets to sets for comparison
    paper_facets_sets = {k: set(v.lower() if isinstance(v, str) else str(v).lower() for v in vals) 
                        for k, vals in paper_facets.items()}
    
    # Check overlap for each type
    has_major_overlap = False
    has_minor_overlap = False
    
    for ftype in HIGH_LEVEL_FACET_TYPES:
        overlap = idea_facets.get(ftype, set()) & paper_facets_sets.get(ftype, set())
        if overlap:
            details[ftype] = list(overlap)
            if ftype in ["problem_facets", "setting_facets", "mechanism_facets"]:
                has_major_overlap = True
            else:
                has_minor_overlap = True
    
    # Simple scoring logic
    if len(details) >= 3 and has_major_overlap:
        score = 3
    elif has_major_overlap:
        score = 2
    elif has_minor_overlap:
        score = 1
        
    return score, details


def generate_relevance_keywords_from_idea(
    agent,
    topic: str,
    idea: str,
    facets: List[str],
    max_keywords: int = 20,
) -> List[str]:
    """
    Use the LLM once to propose loose relevance keywords / phrases
    to help pre-filter obviously irrelevant papers.
    
    Uses 'expert' LLM role for deep semantic understanding.
    """
    # Instantiate expert model
    model_id, api_base, api_key = get_litellm_config(role="expert")
    expert_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    prompt = f"""You are a research assistant helping to filter academic papers.
We are looking for prior art for the following idea:
Topic: {topic}
Idea: {idea}
Key Facets: {json.dumps(facets)}

Please generate a list of {max_keywords} relevance keywords or phrases.
These keywords will be used to score papers (title + abstract) to filter out OBVIOUSLY irrelevant ones (e.g., NLP, Metaverse, Materials Science).

Guidelines:
- Include specific technical terms (e.g., "monocular 3d detection", "rotation augmentation").
- Include broader domain cues (e.g., "3d vision", "point cloud", "lidar", "rgb-d", "autonomous driving").
- Include variations (e.g., "3d object detection", "3d detection").
- The goal is RECALL. If a paper contains these words, it might be relevant.

Output strictly valid JSON: ["keyword1", "keyword2", ...]
Do not output markdown code blocks.
"""
    # Use expert_model instead of agent
    try:
        resp = call_llm_json(expert_model, prompt)
        text = _extract_text_from_llm_response(resp)
        keywords = json.loads(text)
        if isinstance(keywords, list):
            return [str(k).lower().strip() for k in keywords]
    except Exception as e:
        logger.warning(f"Failed to parse relevance keywords: {e}")
    
    # Fallback
    return [topic.lower()] + [f.lower() for f in facets]


def simple_relevance_score(
    paper_title: str,
    paper_abstract: str,
    keywords: List[str],
    strong_cues: Optional[List[str]] = None,
) -> float:
    """
    Compute a cheap heuristic relevance score using keyword matches.

    This function must remain domain-agnostic. Do not hard-code 3D vision / NLP / RL
    specific keywords here. Use the `keywords` / `strong_cues` arguments to specialize.
    
    Applies penalty for generic terms (surveys, tutorials, etc.) to prevent
    broad overview papers from dominating the results.
    """
    score = 0.0
    title_norm = paper_title.lower() if paper_title else ""
    abstract_norm = paper_abstract.lower() if paper_abstract else ""
    
    # Use provided strong cues or default to empty
    cues = strong_cues if strong_cues is not None else []
    
    for cue in cues:
        # Strong cues are worth more
        if cue in title_norm:
            score += 2.0
        if cue in abstract_norm:
            score += 0.5
            
    for kw in keywords:
        if kw in title_norm:
            score += 3.0 # Keyword in title is strong
        if kw in abstract_norm:
            score += 1.0
    
    # Penalty for generic terms in title (surveys, reviews, etc.)
    for stopword in GENERIC_STOPWORDS:
        if stopword in title_norm:
            score -= 1.5  # Penalty for generic title
            break  # Only penalize once
    
    # Additional penalty for "survey" or "review" in title
    if "survey" in title_norm or "review" in title_norm:
        score -= 2.0
            
    return max(0.0, score)  # Don't go below 0


def prefilter_papers_for_llm_scoring(
    papers: List[Tuple[str, Paper]], # list of (id, Paper)
    keywords: List[str],
    min_keep: int,
    max_keep: int,
) -> List[Tuple[str, Paper]]:
    """
    Stage 1: Apply a lightweight lexical pre-filter using simple_relevance_score.
    """
    # Use top k keywords as strong cues default
    strong_cues = keywords[:10] if keywords else []

    scored_papers = []
    for pid, p in papers:
        s = simple_relevance_score(p.title, p.abstract, keywords, strong_cues=strong_cues)
        scored_papers.append((s, pid, p))
        
    # Sort descending by score
    scored_papers.sort(key=lambda x: x[0], reverse=True)
    
    kept = scored_papers[:max_keep]
    
    # Log detailed stats
    if scored_papers:
        top_score = scored_papers[0][0]
        # Avoid index error if kept is empty (unlikely given min_keep)
        lowest_kept_score = kept[-1][0] if kept else 0.0
        
        logger.info(
            f"[PreFilter-Lexical] Input: {len(papers)} papers. Kept: {len(kept)}. "
            f"Score Threshold (lowest kept): {lowest_kept_score:.2f} (Top: {top_score:.2f})"
        )
        
        if len(scored_papers) > max_keep:
            dropped_count = len(scored_papers) - len(kept)
            best_dropped_score = scored_papers[max_keep][0]
            logger.info(f"[PreFilter-Lexical] Dropped {dropped_count} papers. Best dropped score: {best_dropped_score:.2f}")
            
    return [(pid, p) for _, pid, p in kept]


def llm_relevance_prefilter(
    papers: List[Tuple[str, Paper]],
    topic: str,
    idea_description: str,
    max_workers: int = 1,
) -> List[Tuple[str, Paper]]:
    """
    Stage 2: Use a light LLM to do quick true/false relevance filtering.
    
    Args:
        papers: List of (paper_id, Paper) tuples from Stage 1
        topic: Research topic string
        idea_description: Idea being checked for prior art
        max_workers: Parallel workers (1 for serial/debug mode)
    
    Returns:
        Filtered list of papers where LLM judged is_relevant=true
    """
    if not papers:
        return papers
    
    logger.info(f"[PreFilter-LLM] Checking {len(papers)} papers for topic relevance...")
    
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    def _check_single_paper(pid_paper: Tuple[str, Paper]) -> Tuple[str, Paper, bool]:
        """Check if a single paper is potentially relevant."""
        pid, paper = pid_paper
        prompt = f"""You are an academic research assistant. Quickly determine if this paper might be relevant to the research topic below.

Research Topic: {topic}
Research Idea: {idea_description[:500]}

Paper Title: {paper.title}
Paper Abstract: {(paper.abstract or "N/A")[:800]}

Is this paper potentially relevant to the research topic/idea above?
Only output valid JSON, no explanation:
{{"is_relevant": true}} or {{"is_relevant": false}}
"""
        try:
            messages = [{"role": "user", "content": prompt}]
            resp = light_model(messages)
            text = _extract_text_from_llm_response(resp)
            result = json.loads(text)
            is_relevant = result.get("is_relevant", True)  # Default to true if parse fails
            return (pid, paper, is_relevant)
        except Exception as e:
            logger.debug(f"[PreFilter-LLM] Error checking {paper.title[:40]}: {e}")
            return (pid, paper, True)  # Keep paper on error
    
    results = []
    
    if max_workers <= 1:
        # Serial execution
        for pid_paper in papers:
            results.append(_check_single_paper(pid_paper))
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_paper = {executor.submit(_check_single_paper, pp): pp for pp in papers}
            for fut in as_completed(future_to_paper):
                try:
                    results.append(fut.result())
                except Exception as e:
                    pid, paper = future_to_paper[fut]
                    logger.warning(f"[PreFilter-LLM] Error: {e}")
                    results.append((pid, paper, True))
    
    # Filter to relevant papers
    relevant_papers = [(pid, p) for pid, p, is_relevant in results if is_relevant]
    dropped_count = len(papers) - len(relevant_papers)
    
    logger.info(
        f"[PreFilter-LLM] Kept {len(relevant_papers)}/{len(papers)} papers. "
        f"Dropped {dropped_count} as not relevant."
    )
    
    return relevant_papers


def score_papers_with_abstract_only(
    agent, 
    state: PipelineState, 
    facets: List[str], 
    idea_features: Dict[str, Set[str]], 
    idea_high_level_facets: Dict[str, Set[str]],
    idea_description: str = "",
    cfg: Optional[PriorArtConfig] = None,
    stage: str = "initial",
) -> List[PriorArtMatch]:
    """
    Cheap first-stage scoring: use only title/venue/year/citations
    and abstract (no fulltext chunks).

    This function should:
    - Rank candidate papers based on citation_count (or other signals).
    - For top `max_papers`, ask the LLM to assign a coverage_level (0-3)
      and map covered facet labels.
    - Compute structural and high-level facet overlap, as in the current
      `score_papers_against_idea`.

    The output is a list of PriorArtMatch objects, sorted by:
      coverage_level, high_level_facet_score, structural_score, citations.
    """
    # Determine max papers to score
    if cfg:
        max_papers = _get_max_abstract_to_score(cfg, stage)
    else:
        max_papers = 80 # Default fallback

    logger.info(f"[PriorArt] Scoring top {max_papers} papers (abstract-only) [Stage: {stage}]...")
    
    # Instantiate light model for scoring
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    # Sort papers by citation count
    all_papers = list(state.papers.items())
    all_papers.sort(key=lambda x: x[1].citation_count or 0, reverse=True)

    # Two-stage pre-filter logic
    # Stage 1: Lexical scoring with generic-word penalty
    if cfg and cfg.enable_prefilter and len(all_papers) > cfg.prefilter_min_papers:
        logger.info(
            "[PriorArt] Stage 1: Applying lexical pre-filter: "
            f"{len(all_papers)} candidate papers -> at most {cfg.prefilter_max_keep}."
        )
        keywords = generate_relevance_keywords_from_idea(
            agent=agent,
            topic=state.intent.topic,
            idea=idea_description,
            facets=facets,
            max_keywords=cfg.prefilter_max_keywords,
        )
        logger.info(f"[PriorArt] Generated relevance keywords: {keywords[:10]}...")  # Log first 10
        
        all_papers = prefilter_papers_for_llm_scoring(
            papers=all_papers,
            keywords=keywords,
            min_keep=10,
            max_keep=cfg.prefilter_max_keep,
        )
    
    # Stage 2: Light LLM true/false relevance filter
    if cfg and cfg.enable_llm_prefilter:
        llm_top_n = cfg.debug_llm_prefilter_top_n if cfg.debug_mode else cfg.llm_prefilter_top_n
        papers_for_llm_filter = all_papers[:llm_top_n]
        
        if len(papers_for_llm_filter) > 10:  # Only if we have enough papers to filter
            logger.info(
                f"[PriorArt] Stage 2: Applying LLM relevance filter to top {len(papers_for_llm_filter)} papers."
            )
            max_workers = 1 if cfg.debug_mode else 4
            filtered_papers = llm_relevance_prefilter(
                papers=papers_for_llm_filter,
                topic=state.intent.topic,
                idea_description=idea_description,
                max_workers=max_workers,
            )
            # Combine filtered papers with remaining papers (beyond llm_top_n)
            all_papers = filtered_papers + all_papers[llm_top_n:]
    
    # Limit to top max_papers
    papers_to_score = all_papers[:max_papers]
    
    matches = []
    
    facets_str = "\n".join([f"F{i+1}. {facet}" for i, facet in enumerate(facets)])
    
    total_to_score = len(papers_to_score)
    log_interval = cfg.progress_log_interval if cfg else 5

    for idx, (paper_id, paper) in enumerate(papers_to_score, start=1):
        # Progress logging
        if idx == 1 or idx % log_interval == 0 or idx == total_to_score:
            logger.info(f"[PriorArt] Abstract scoring progress: {idx}/{total_to_score} papers processed.")

        logger.debug(f"[PriorArt] Scoring paper: {paper.title} (id={paper_id})")
        
        # Extract high-level facets for paper (lazy extraction for top papers)
        if not paper.high_level_facets:
            paper.high_level_facets = extract_high_level_facets_from_paper(agent, paper)
            
        # Build paper context (Abstract Only)
        paper_context = f"""Title: {paper.title}
Venue: {paper.venue or 'Unknown'}
Year: {paper.year or 'Unknown'}
Citation Count: {paper.citation_count or 0}"""
        
        # Add abstract if available
        if hasattr(paper, 'abstract') and paper.abstract:
            paper_context += f"\nAbstract: {paper.abstract[:1000]}"  # Increased limit for abstract
        else:
            paper_context += "\nAbstract: N/A"
        
        prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

Given the following research idea facets and a paper, rate how much this paper overlaps with the idea.

Idea Facets:
{facets_str}

Paper:
{paper_context}

Please output a JSON object with:
- "coverage_level": integer 0-3 where:
  0 = no overlap
  1 = adjacent work (same domain but different approach)
  2 = partial overlap (covers some facets)
  3 = high overlap (covers most/all facets, potential conflict)
- "covered_facets": list of facet labels (e.g., ["F1", "F3"]) that this paper covers
- "task_similarity": float 0.0-1.0 how similar the task/problem addressed is
- "method_similarity": float 0.0-1.0 how similar the method/approach is
- "notes": brief justification (1-2 sentences)

JSON output:"""

        try:
            messages = [{"role": "user", "content": prompt}]
            resp = light_model(messages)
            result_str = _extract_text_from_llm_response(resp)
            logger.debug(f"[PriorArt] Raw scoring output for {paper.title[:30]}... (cleaned): {result_str[:500]!r}")
            
            score_data = None
            try:
                score_data = json.loads(result_str)
            except Exception:
                try:
                    score_data = ast.literal_eval(result_str)
                except Exception:
                    logger.warning(f"[PriorArt] Failed to parse scoring JSON for {paper.title}, fallback to 0.")
                    score_data = None
            
            if isinstance(score_data, dict):
                coverage_level = int(score_data.get("coverage_level", 0))
                # Raw labels from LLM, e.g. ["F1", "F3"]
                covered_facet_labels = score_data.get("covered_facets", []) or []
                notes = score_data.get("notes", "")
                
                # Extract similarity scores
                task_sim = float(score_data.get("task_similarity", 0.0))
                method_sim = float(score_data.get("method_similarity", 0.0))
                
                # Map facet labels (F1, F2, ...) to human-readable facet text
                covered_facets: List[str] = []
                for label in covered_facet_labels:
                    if isinstance(label, str) and label.startswith("F"):
                        try:
                            idx = int(label[1:]) - 1
                            if 0 <= idx < len(facets):
                                covered_facets.append(facets[idx])
                        except ValueError:
                            continue
                
                matches.append(PriorArtMatch(
                    paper_id=paper_id,
                    coverage_level=coverage_level,
                    covered_facet_labels=[str(l).strip() for l in covered_facet_labels],
                    covered_facets=covered_facets,
                    notes=notes,
                    task_similarity=task_sim,
                    method_similarity=method_sim,
                ))
                
                # Compute structural score
                struct_score, struct_details = compute_structural_overlap(idea_features, paper)
                matches[-1].structural_score = struct_score
                matches[-1].structural_overlap = struct_details
                
                # Compute high-level facet score
                hl_score, hl_details = compute_high_level_facet_overlap(idea_high_level_facets, paper.high_level_facets)
                matches[-1].high_level_facet_score = hl_score
                matches[-1].high_level_facet_matches = hl_details
                
                logger.info(f"[PriorArt] Finished scoring {paper.title}: level={coverage_level}, task_sim={task_sim:.2f}")
            else:
                # Fallback
                logger.warning(f"[PriorArt] Failed to parse scoring JSON for {paper.title}, fallback to 0.")
                matches.append(PriorArtMatch(
                    paper_id=paper_id,
                    coverage_level=0,
                    covered_facet_labels=[],
                    covered_facets=[],
                    notes="Failed to parse LLM output.",
                ))
                
        except Exception as e:
            logger.warning(f"Failed to score paper {paper.title}: {e}")
            matches.append(PriorArtMatch(
                paper_id=paper_id,
                coverage_level=0,
                covered_facet_labels=[],
                covered_facets=[],
                notes=f"Scoring error: {str(e)[:50]}",
            ))
    
    # Sort by coverage_level (desc), then similarity, then citation_count
    # This prioritizes papers with similar tasks/methods over high-citation surveys
    matches.sort(
        key=lambda m: (
            m.coverage_level,
            m.task_similarity,             # Task similarity (new)
            m.method_similarity,           # Method similarity (new)
            m.high_level_facet_score,
            min(state.papers[m.paper_id].citation_count or 0, 200)  # Cap citation influence
        ),
        reverse=True
    )
    
    logger.info(f"Scored {len(matches)} papers")
    return matches


def enrich_papers_with_fulltext(
    state: PipelineState,
    paper_ids: List[str],
    max_workers: int = 1,
    run_mgr: Optional["RunManager"] = None,
) -> None:
    """
    Ensure fulltext chunks exist for the specified papers.
    Downloads PDF/HTML if needed and parses it.
    
    Args:
        state: Pipeline state containing papers dict
        paper_ids: List of paper IDs to enrich
        max_workers: Number of parallel threads (1 = serial, >1 = parallel)
        run_mgr: Optional RunManager for linking PDFs to run directory
    """
    # Use RunManager's pdf_cache if available, else fallback to cwd
    if run_mgr:
        pdf_cache_dir = str(run_mgr.pdf_cache_dir)
    else:
        pdf_cache_dir = os.path.join(os.getcwd(), "pdf_cache")
    # Ensure cache dir exists (outside threads)
    try:
        os.makedirs(pdf_cache_dir, exist_ok=True)
    except Exception:
        pass

    def _process_one_paper(pid: str) -> None:
        """Process a single paper: resolve URL, download PDF, parse chunks."""
        # Instantiate tools locally for thread safety
        resolver = PaperResolverTool()
        parser = PdfParserTool()
        
        paper = state.papers.get(pid)
        if not paper:
            return
        
        if paper.chunks:  # Already parsed
            return
            
        logger.info(f"[Fulltext] Enriching paper: {paper.title[:50]}...")
        
        # Resolve PDF URL if missing
        if not paper.pdf_url:
            try:
                result_str = resolver.forward(
                    doi=paper.doi,
                    arxiv_id=paper.arxiv_id,
                    title=paper.title if not paper.doi and not paper.arxiv_id else None,
                )
                res_data = json.loads(result_str)
                if res_data.get("pdf_url"):
                    paper.pdf_url = res_data["pdf_url"]
            except Exception as e:
                logger.warning(f"Failed to resolve PDF for {paper.title}: {e}")

        if not paper.pdf_url:
            logger.warning(f"[Fulltext] No PDF URL found for paper: {paper.title[:50]}")
            return

        # Download PDF to local cache
        # Ensure unique filename
        # Sanitize paper_id for use as filename (DOIs contain /, S2: contains :)
        safe_name = pid.replace('/', '_').replace('\\', '_').replace(':', '_')
        if paper.doi:
            safe_name = paper.doi.replace('/', '_').replace('\\', '_').replace(':', '_')
        elif paper.openalex_id:
            safe_name = paper.openalex_id.replace('/', '_').replace('\\', '_').replace(':', '_')
            
        local_filename = f"{safe_name}.pdf"
        local_pdf_path = os.path.join(pdf_cache_dir, local_filename)
        
        if not os.path.exists(local_pdf_path):
            try:
                logger.info(f"Downloading PDF from {paper.pdf_url}...")
                response = requests.get(paper.pdf_url, timeout=30)
                response.raise_for_status()
                with open(local_pdf_path, "wb") as f:
                    f.write(response.content)
            except Exception as e:
                logger.warning(f"[Fulltext] Failed to download PDF for paper: {paper.title[:50]} - {e}")
                return
        
        paper.local_pdf_path = local_pdf_path
        
        # Link PDF to run directory if RunManager is available
        if run_mgr:
            try:
                run_mgr.link_pdf_to_run(safe_name, Path(local_pdf_path))
            except Exception as e:
                logger.debug(f"[Fulltext] Failed to link PDF to run dir: {e}")

        # Parse the PDF into chunks
        try:
            logger.info(f"Parsing PDF: {local_pdf_path}")
            parser_result_str = parser.forward(pdf_path=local_pdf_path, chunk_size=1000)
            
            # Extract text from chunks
            content_json = json.loads(parser_result_str)
            
            # Populate paper.chunks
            paper.chunks = [
                Chunk(
                    text=chunk["text"],
                    page_start=chunk.get("page_start"),
                    page_end=chunk.get("page_end")
                )
                for chunk in content_json
            ]
            
            # Parse sections with semantic roles (intro, related_work, method, etc.)
            try:
                paper_sections = parser.parse_sections(
                    pdf_path=local_pdf_path,
                    paper_id=pid,
                    title=paper.title,
                    year=paper.year or 0,
                )
                # Save sections JSON to task directory
                if run_mgr:
                    sections_path = save_paper_sections(run_mgr.run_dir, paper_sections)
                    logger.info(f"[Fulltext] Saved sections to {sections_path}")
            except Exception as e:
                logger.warning(f"[Fulltext] Failed to parse sections for {paper.title[:50]}: {e}")
            
        except Exception as e:
            logger.warning(f"Failed to parse PDF {local_pdf_path}: {e}")

    # Serial execution (default, for debug mode)
    if max_workers <= 1:
        for pid in paper_ids:
            _process_one_paper(pid)
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_pid = {executor.submit(_process_one_paper, pid): pid for pid in paper_ids}
            for fut in as_completed(future_to_pid):
                pid = future_to_pid[fut]
                try:
                    fut.result()
                except Exception as e:
                    logger.warning(f"[Parallel] Error enriching paper {pid}: {e}")


def refine_top_papers_with_fulltext(
    agent,
    state: PipelineState,
    matches: List[PriorArtMatch],
    idea_description: str,
    facets: List[str],
    idea_features: Dict[str, Set[str]],
    top_k: int = 20,
    max_workers: int = 1,
    run_mgr: Optional["RunManager"] = None,
) -> List[PriorArtMatch]:
    """
    Second-stage refinement for the most promising papers.

    For the top `top_k` matches (by coverage_level and citations):
    - Ensure their fulltext is available via `paper.chunks`
      (trigger PDF/HTML parsing if needed).
    - Use BM25 / other retriever to extract a few evidence snippets.
    - Ask the LLM again with title/abstract + snippets to refine:
      - coverage_level (optional)
      - whether the idea appears as main contribution / technique / background.
    - Update the corresponding PriorArtMatch objects in-place.
    
    Args:
        max_workers: Number of parallel threads (1 = serial, >1 = parallel)
        run_mgr: Optional RunManager for linking PDFs to run directory
    """
    logger.info(f"[PriorArt] Refining top {top_k} papers with fulltext...")

    # Instantiate light model for refinement
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    # 1. Sort and select top_k candidates
    top_candidates = sorted(
        matches,
        key=lambda m: (
            m.coverage_level,
            m.high_level_facet_score,
            m.structural_score,
            state.papers[m.paper_id].citation_count or 0,
        ),
        reverse=True,
    )[:top_k]

    # 2. Ensure fulltext chunks exist
    enrich_papers_with_fulltext(
        state,
        [m.paper_id for m in top_candidates],
        max_workers=max_workers,
        run_mgr=run_mgr,
    )

    # 3. Build evidence keyword set using LLM (domain-agnostic)
    evidence_kw_from_llm = generate_evidence_keywords_from_idea(
        agent=agent,
        topic=state.intent.topic,
        idea_description=idea_description,
        facets=facets,
        idea_features=idea_features,
        max_keywords=30,
    )
    
    # Merge LLM keywords with structural features
    evidence_keywords = make_keywords_for_evidence(
        idea_features=idea_features,
        facet_synonyms=None,  # Could add expand_facet_synonyms here later
        extra_keywords=evidence_kw_from_llm,
    )
    
    logger.info(f"[PriorArt] Evidence keywords ({len(evidence_keywords)}): {sorted(evidence_keywords)[:10]}...")

    for match in top_candidates:
        paper = state.papers[match.paper_id]
        snippets = find_evidence_snippets(paper, evidence_keywords, max_snippets=3)

        if not snippets:
            continue

        # Build refinement prompt using idea, facets, and snippets
        snippets_text = "\n".join(f"- {s}" for s in snippets)
        prompt = f"""
You are checking for potential prior art.

Research idea:
{idea_description}

Key facets:
{chr(10).join(f"- F{i+1}: {f}" for i, f in enumerate(facets))}

Paper:
Title: {paper.title}
Venue: {paper.venue or 'Unknown'}
Year: {paper.year or 'Unknown'}
Citations: {paper.citation_count or 0}
Abstract: {paper.abstract or 'N/A'}

Relevant snippets from the full text:
{snippets_text}

Based on ALL of the above, answer in JSON:

{{
  "coverage_level_refined": 0,  // integer [0-3]
  "role": "main_contribution" | "key_technique" | "background" | "irrelevant",
  "notes": "short justification"
}}

Return RAW JSON only, no backticks.
"""
        try:
            messages = [{"role": "user", "content": prompt}]
            resp = light_model(messages)
            text = _extract_text_from_llm_response(resp)
            refined = json.loads(text)

            # Update the match object in-place
            level_refined = int(refined.get("coverage_level_refined", match.coverage_level))
            role = str(refined.get("role", "unknown")).strip()
            notes = str(refined.get("notes", "")).strip()

            # Update match
            match.refined_coverage_level = level_refined
            match.coverage_level = level_refined
            match.role = role
            match.notes = f"{match.notes}\\n{notes}".strip()

        except Exception as e:
            logger.warning(f"[PriorArt] Failed to refine fulltext scoring for {paper.title}: {e}")

    # 4. Return the updated list (all matches, not only top_k)
    return matches


def expand_corpus_via_citation_graph(
    state: PipelineState,
    matches: List[PriorArtMatch],
    max_seed_papers: int = 10,
    max_neighbors_per_seed: int = 50,
    max_workers: int = 1,
) -> None:
    """
    Expand the candidate corpus by traversing the citation graph.

    For the top `max_seed_papers` matches with coverage_level >= 2,
    use Semantic Scholar to:
    - fetch their references (earlier work),
    - fetch their citations (later work).

    For each new paper discovered:
    - convert it into a `Paper` object,
    - add it into `state.papers` if not already present.

    This function should NOT do any scoring; it only enriches the corpus.
    
    Args:
        max_workers: Number of parallel threads (1 = serial, >1 = parallel)
    """
    logger.info(f"[CitationGraph] Expanding corpus via citation graph...")

    # 1. Pick seed papers (partial or high overlap, sorted by citations)
    seed_candidates = [
        m for m in matches 
        if m.coverage_level >= 2 and state.papers[m.paper_id].citation_count
    ]
    seed_candidates.sort(
        key=lambda m: state.papers[m.paper_id].citation_count or 0,
        reverse=True
    )
    seeds = seed_candidates[:max_seed_papers]
    
    if not seeds:
        logger.info("[CitationGraph] No seed papers found (coverage_level >= 2).")
        return

    logger.info(f"[CitationGraph] Using {len(seeds)} seed papers for expansion.")

    # Thread lock for safe writes to state.papers
    state_lock = threading.Lock()
    new_paper_count = 0

    def _expand_single_seed(match: PriorArtMatch) -> int:
        """Expand citations/references for a single seed paper. Returns count of new papers added."""
        # Instantiate client locally for thread safety
        local_s2_client = SemanticScholarClient()
        nonlocal state_lock
        count = 0
        paper = state.papers[match.paper_id]
        logger.info(f"[CitationGraph] Expanding from seed: {paper.title[:60]}...")

        # Determine S2 paper ID
        s2_id = paper.s2_id or paper.doi
        if not s2_id:
            logger.warning(f"[CitationGraph] No S2/DOI for paper {paper.title[:40]}, skipping.")
            return 0

        # Fetch references (older papers)
        references = local_s2_client.get_references(
            s2_id,
            fields="paperId,title,year,abstract,venue,citationCount,externalIds",
            limit=max_neighbors_per_seed // 2
        )
        
        # Fetch citations (newer papers)
        citations = local_s2_client.get_citations(
            s2_id,
            fields="paperId,title,year,abstract,venue,citationCount,externalIds",
            limit=max_neighbors_per_seed // 2
        )

        # Thread-safe write to state.papers
        with state_lock:
            for ref in references:
                if _add_s2_paper_to_state(ref, state):
                    count += 1
            for cit in citations:
                if _add_s2_paper_to_state(cit, state):
                    count += 1
        
        return count

    # Serial execution (default, for debug mode)
    if max_workers <= 1:
        for match in seeds:
            new_paper_count += _expand_single_seed(match)
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_match = {executor.submit(_expand_single_seed, m): m for m in seeds}
            for fut in as_completed(future_to_match):
                match = future_to_match[fut]
                try:
                    new_paper_count += fut.result()
                except Exception as e:
                    logger.warning(f"[Parallel] Expansion error for paper_id={match.paper_id}: {e}")

    logger.info(f"[CitationGraph] Added {new_paper_count} new papers from citation graph.")


def _add_s2_paper_to_state(s2_paper: Dict[str, Any], state: PipelineState) -> bool:
    """
    Convert a Semantic Scholar paper dict to our Paper model and add to state.
    
    Returns True if a new paper was added, False if it already existed.
    """
    paper_id = s2_paper.get("paperId")
    if not paper_id:
        return False

    # Check if already exists (by S2 ID or DOI)
    external_ids = s2_paper.get("externalIds", {})
    doi = external_ids.get("DOI")
    arxiv_id = external_ids.get("ArXiv")

    # Build canonical key (prefer DOI, fallback to S2 ID)
    canonical_key = doi if doi else f"S2:{paper_id}"

    if canonical_key in state.papers:
        return False

    # Create Paper object
    paper = Paper(
        title=s2_paper.get("title", "Unknown Title"),
        year=s2_paper.get("year"),
        venue=s2_paper.get("venue"),
        abstract=s2_paper.get("abstract"),
        s2_id=paper_id,
        doi=doi,
        arxiv_id=arxiv_id,
        citation_count=s2_paper.get("citationCount")
    )

    state.papers[canonical_key] = paper
    return True


# ----------------------------------------------------------------------
# Helper functions for report rendering
# ----------------------------------------------------------------------

from collections import Counter
from typing import Iterable, Sequence

import re

def normalize_facet_phrase(s: str) -> str:
    """
    Normalize a facet phrase for fuzzy matching.
    - Lowercase
    - Strip whitespace
    - Collapse multiple spaces
    - Standardize 2d/3d
    - Fix common typos (ffound, etc.)
    """
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    
    # Standardize 2d/3d
    s = s.replace("2d", "2D").replace("3d", "3D")
    
    # Fix common typos from LLM output
    s = s.replace("ffound", "found")
    s = s.replace("inn ", "in ")
    s = s.replace("synchronnized", "synchronized")
    
    return s


def _count_coverage(
    target_names: Set[str],
    per_paper_feature_sets: Iterable[Set[str]],
) -> Dict[str, int]:
    """
    Count how many papers cover each target feature (e.g., dataset).
    Uses approximate substring matching.
    """
    # Pre-normalize targets
    normalized_targets = {normalize_facet_phrase(n): n for n in target_names}
    counts = {orig: 0 for orig in target_names}

    for feature_set in per_paper_feature_sets:
        # Normalize paper features
        normalized_features = [normalize_facet_phrase(f) for f in feature_set]
        
        # Check each target against all paper features
        for norm_target, orig_target in normalized_targets.items():
            if not norm_target:
                continue
                
            for nf in normalized_features:
                if not nf:
                    continue
                    
                # Approximate matching: substring in either direction
                # e.g. "data augmentation" matches "geometric data augmentation"
                if norm_target in nf or nf in norm_target:
                    counts[orig_target] += 1
                    break  # Count at most once per paper

    return counts


def _bucketize(count: int) -> str:
    """Return a simple label based on count."""
    if count == 0:
        return "gap"        # ⚠ potential gap
    elif count == 1:
        return "rare"       # rare / emerging
    else:
        return "common"     # common / saturated-ish


def render_structural_coverage_section(
    idea_features: Dict[str, Set[str]],
    state: PipelineState,
) -> str:
    """
    Render a section summarizing how the idea's structural features 
    compare to the retrieved papers.
    """
    # Collect per-paper features
    paper_sets_datasets = [set(d.lower() for d in p.datasets) for p in state.papers.values()]
    paper_sets_metrics = [set(m.lower() for m in p.metrics) for p in state.papers.values()]
    paper_sets_methods = [set(f.lower() for f in p.method_families) for p in state.papers.values()]

    # Count coverage only for features present in the idea
    dataset_counts = _count_coverage(idea_features["datasets"], paper_sets_datasets)
    metric_counts = _count_coverage(idea_features["metrics"], paper_sets_metrics)
    method_counts = _count_coverage(idea_features["method_families"], paper_sets_methods)

    lines = []
    lines.append("## Structural Coverage of Your Idea")
    lines.append("Analysis of your idea's structural features against the retrieved corpus:")
    lines.append("")
    lines.append("> **Note**: Counts are based on approximate phrase matching and may under- or over-count semantically related work.")
    lines.append("")

    def add_block(title: str, counts: Dict[str, int]):
        if not counts:
            return
        lines.append(f"**{title}**")
        for name, count in sorted(counts.items(), key=lambda x: x[1]):
            bucket = _bucketize(count)
            if bucket == "gap":
                note = "→ ⚠️ potential gap"
            elif bucket == "rare":
                note = "→ rare / emerging"
            else:
                note = "→ common"
            lines.append(f"- {name} — matched in ~{count} paper(s) {note}")
        lines.append("")  # blank line

    add_block("Datasets", dataset_counts)
    add_block("Method Families", method_counts)
    add_block("Metrics", metric_counts)
    
    if len(lines) <= 5: # If no blocks were added (header + disclaimer = 5 lines)
        lines.append("*No structural features extracted from idea to analyze.*")
        lines.append("")

    return "\\n".join(lines)


def generate_evidence_keywords_from_idea(
    agent,
    topic: str,
    idea_description: str,
    facets: List[str],
    idea_features: Dict[str, Set[str]],
    max_keywords: int = 30,
) -> List[str]:
    """
    Generate evidence keywords for full-text snippet search using LLM.

    Compared to `generate_relevance_keywords_from_idea`, this function
    focuses on *technical cue words* that are likely to appear near
    the main contribution in a paper (method name, key operations,
    datasets, metrics, ablation terms, etc.).

    The output is used by BM25 to find supporting snippets.
    This function is domain-agnostic and works for any research topic.
    
    Uses 'expert' LLM role for deep semantic understanding.
    """
    # Instantiate expert model
    model_id, api_base, api_key = get_litellm_config(role="expert")
    expert_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    # Pre-format hints for the prompt
    facet_text = "\n".join(f"- {f}" for f in facets) if facets else "N/A"
    datasets = ", ".join(sorted(idea_features.get("datasets", []))) or "none"
    metrics = ", ".join(sorted(idea_features.get("metrics", []))) or "none"
    methods = ", ".join(sorted(idea_features.get("method_families", []))) or "none"

    prompt = f"""You are helping to search for *evidence snippets* in full academic papers.

We are checking prior art for the following idea:

Topic: {topic}
Idea: {idea_description[:800]}

Key facets:
{facet_text}

Structural features extracted:
- Datasets: {datasets}
- Metrics: {metrics}
- Method families: {methods}

TASK:
Generate up to {max_keywords} short keywords or phrases that are likely to appear
in the *full text* of papers that implement or closely relate to this idea.

Guidelines:
- Mix high-level task words with low-level method cues.
- Include dataset and metric names if relevant.
- Include typical phrases used in contributions and ablations.
- Each keyword should be relatively short (1-5 words).
- Avoid extremely generic words like "deep learning", "neural network", "state-of-the-art".

Output strictly valid JSON list of strings, no markdown:
["kw1", "kw2", ...]
"""
    try:
        messages = [{"role": "user", "content": prompt}]
        resp = expert_model(messages)
        text = _extract_text_from_llm_response(resp)

        kws = json.loads(text)
        if isinstance(kws, list):
            cleaned = []
            for k in kws:
                s = str(k).strip().lower()
                if s:
                    cleaned.append(s)
            logger.info(f"[PriorArt] Generated {len(cleaned)} evidence keywords from LLM.")
            return cleaned
    except Exception as e:
        logger.warning(f"[PriorArt] Failed to generate evidence keywords: {e}")

    # Fallback: use topic + facets + structural features
    fallback = [topic.lower()] + [f.lower() for f in facets]
    fallback += [d.lower() for d in idea_features.get("datasets", [])]
    fallback += [m.lower() for m in idea_features.get("metrics", [])]
    fallback += [mf.lower() for mf in idea_features.get("method_families", [])]
    return list({k.strip() for k in fallback if k.strip()})


def make_keywords_for_evidence(
    idea_features: Dict[str, Set[str]],
    facet_synonyms: Optional[Dict[str, List[str]]] = None,
    extra_keywords: Optional[List[str]] = None,
) -> List[str]:
    """
    Build a keyword list for full-text evidence retrieval.

    This function is *domain-agnostic*. It only merges:
    - structural features from the idea (datasets / metrics / method families),
    - facet synonyms (if provided),
    - any extra_keywords provided by the caller (e.g. LLM-generated).

    There should be **no hard-coded domain-specific terms** here,
    so this function can be safely reused for all research topics.
    """
    kws: Set[str] = set()

    # Structural features
    kws.update(idea_features.get("datasets", set()))
    kws.update(idea_features.get("metrics", set()))
    kws.update(idea_features.get("method_families", set()))

    # Facet synonyms (optional)
    if facet_synonyms:
        for facet, syns in facet_synonyms.items():
            kws.add(str(facet).lower())
            for s in syns:
                s_clean = str(s).strip().lower()
                if s_clean:
                    kws.add(s_clean)

    # Extra keywords from LLM or other modules (optional)
    if extra_keywords:
        for k in extra_keywords:
            s = str(k).strip().lower()
            if s:
                kws.add(s)

    # Drop overly generic tokens that are not helpful for BM25
    GENERIC_NOISE = {"deep learning", "neural network", "neural networks", "machine learning", "state of the art"}
    kws = {k for k in kws if k not in GENERIC_NOISE}

    return list(kws)


def expand_facet_synonyms(
    agent,
    facets: List[str],
    max_synonyms_per_facet: int = 6,
) -> Dict[str, List[str]]:
    """
    Use the LLM to generate synonyms and alternative phrasings
    for each facet.

    This will be used to:
    - enrich evidence-snippet keyword search
    - increase recall when scanning fulltext for mentions of the idea.

    Returns:
        A dict: facet -> list of keyword strings.
    """
    prompt = f"""
You are a research assistant.

For each of the following technical facets, generate up to {max_synonyms_per_facet}
synonyms or alternative phrasings that might be used in a paper (titles, abstracts,
or method descriptions). Include:
- technical synonyms,
- common paraphrases,
- typical phrases that describe the same concept.

Return ONLY raw JSON of the form:
{{
  "facet 1 text": ["synonym 1", "synonym 2", ...],
  "facet 2 text": [...]
}}

Facets:
{chr(10).join(f"- {f}" for f in facets)}

JSON:
"""
    resp = call_llm_json(agent, prompt)
    text = _extract_text_from_llm_response(resp)
    try:
        data = json.loads(text)
    except Exception:
        logger.warning("[PriorArt] Failed to parse facet synonyms JSON, using empty mapping.")
        return {}

    synonyms: Dict[str, List[str]] = {}
    if isinstance(data, dict):
        for facet, arr in data.items():
            if not isinstance(arr, list):
                continue
            clean = [str(x).strip() for x in arr if str(x).strip()]
            if clean:
                synonyms[facet] = clean

    logger.info(f"[PriorArt] Generated facet synonyms for {len(synonyms)} facets.")
    return synonyms


from rank_bm25 import BM25Okapi


def find_evidence_snippets(
    paper: Paper,
    keywords: Sequence[str],
    max_snippets: int = 3,
    max_chars: int = 240,
) -> List[str]:
    """
    Find relevant snippets in paper chunks using BM25.
    """
    if not getattr(paper, "chunks", None):
        return []

    kws = [k for k in keywords if k]
    if not kws:
        return []
        
    # Prepare query
    query_str = " ".join(kws).lower()
    tokenized_query = query_str.split()

    # Prepare corpus
    chunks = paper.chunks
    corpus_texts = [c.text.replace("\\n", " ") for c in chunks]
    tokenized_corpus = [t.lower().split() for t in corpus_texts]

    # BM25 Scoring
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(tokenized_query)

    # Filter and sort
    indexed_scores = [(i, s) for i, s in enumerate(scores) if s > 0]
    if not indexed_scores:
        return []

    indexed_scores.sort(key=lambda x: x[1], reverse=True)
    
    snippets = []
    for idx, score in indexed_scores[:max_snippets]:
        chunk = chunks[idx]
        text = corpus_texts[idx].strip()
        
        # Truncate if too long
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
            
        page_info = ""
        if chunk.page_start is not None:
            if chunk.page_end and chunk.page_end != chunk.page_start:
                page_info = f" (p. {chunk.page_start}–{chunk.page_end})"
            else:
                page_info = f" (p. {chunk.page_start})"
        snippets.append(f'"{text}"{page_info}')
        
    return snippets



def render_high_level_coverage_section(
    idea_facets: Dict[str, Set[str]],
    state: PipelineState,
) -> str:
    """
    Render a section summarizing high-level structural coverage.
    """
    lines = []
    lines.append("## High-level Structural Coverage")
    lines.append("Analysis of problem, setting, mechanism, and assumptions:")
    lines.append("")
    lines.append("> **Note**: Counts are based on approximate phrase matching and may under- or over-count semantically related work.")
    lines.append("")
    
    for ftype in HIGH_LEVEL_FACET_TYPES:
        # Collect all values for this facet type from papers
        paper_values = []
        for p in state.papers.values():
            paper_values.append(set(v.lower() for v in p.high_level_facets.get(ftype, [])))
            
        # Count coverage for idea facets
        counts = _count_coverage(idea_facets.get(ftype, set()), paper_values)
        
        if not counts:
            continue
            
        # Format title (e.g., "problem_facets" -> "Problem Facets")
        title = ftype.replace("_", " ").title()
        lines.append(f"### {title}")
        
        for name, count in sorted(counts.items(), key=lambda x: x[1]):
            bucket = _bucketize(count)
            if bucket == "gap":
                note = "→ ⚠️ not found in corpus"
            elif bucket == "rare":
                note = "→ rare (1 match)"
            else:
                note = "→ covered"
            lines.append(f"- `{name}` — matched in ~{count} paper(s) {note}")
        lines.append("")
        
    if len(lines) <= 5:
        lines.append("*No high-level facets extracted from idea.*")
        lines.append("")
        
    return "\\n".join(lines)


def render_high_level_coverage_section_v2(
    facet_coverage: Dict[str, FacetCoverageStats],
    facet_views: Dict[str, FacetReportView],
) -> str:
    """
    Render high-level structural coverage using FacetCoverageStats (Block 3).
    
    This replaces the old phrase-counting logic to ensure consistency
    between facet statistics and paper coverage levels.
    """
    lines = []
    lines.append("## High-level Structural Coverage")
    lines.append("Analysis of core facets based on evidence extraction:")
    lines.append("")
    
    for facet_id, view in facet_views.items():
        count_strong = view.stats.count_strong
        count_any = view.stats.count_any
        label = coverage_label(count_strong, count_any)
        
        lines.append(f"- **{view.human_label}**: {count_strong} strong / {count_any} total papers — {label}")
    
    if len(lines) <= 3:  # Only header + description
        lines.append("*No facet evidence collected.*")
    
    lines.append("")
    return "\\n".join(lines)


def _clean_note_for_report(note: str) -> str:
    """
    Clean up internal tags (like [Refined]) from notes for the final report.
    """
    if not note:
        return ""
    
    lines = []
    for line in note.splitlines():
        # Remove lines that look like internal markers
        if line.strip().startswith("[Refined]"):
            # Option: keep the text after the marker
            cleaned = line.replace("[Refined]", "").strip()
            if cleaned:
                lines.append(cleaned)
        else:
            lines.append(line)
            
    return "\n".join(lines).strip()


def render_prior_art_report(
    idea_description: str,
    facets: List[str],
    scored_papers: List[PriorArtMatch],
    state: PipelineState,
    idea_features: Dict[str, Set[str]],
    idea_high_level_facets: Dict[str, Set[str]],
    # Block 3 unified data sources (Issue 1, 3, 4, 6)
    coverage_levels: Dict[str, int] = None,
    paper_records: Dict[str, "PaperRecord"] = None,
    facet_coverage: Dict[str, FacetCoverageStats] = None,
    facet_views: Dict[str, FacetReportView] = None,
    config: PriorArtConfig = None,
) -> str:
    """
    Render a structured Markdown report for the prior-art check.

    This function is pure: it only reads from the inputs and returns a string.
    """
    lines = []
    
    # 1. Title
    lines.append(f"# Prior-Art Check: {state.intent.topic}")
    lines.append("")
    
    # 2. Idea under review
    lines.append("## 1. Idea Under Review")
    lines.append("")
    lines.append(idea_description)
    lines.append("")
    lines.append("**Key Technical Facets:**")
    for i, facet in enumerate(facets, 1):
        lines.append(f"- **F{i}**: {facet}")
    lines.append("")
    
    # 3. High-overlap candidates (level 3)
    lines.append("## 2. High-Overlap Candidates (Coverage Level = 3)")
    lines.append("⚠️ These papers may present potential conflicts or very similar approaches:")
    lines.append("")
    
    # Issue 6: Use Block 3 coverage_levels when available
    if coverage_levels:
        high_paper_ids = {pid for pid, lvl in coverage_levels.items() if lvl == 3}
        high_matches = [m for m in scored_papers if m.paper_id in high_paper_ids]
    else:
        # Fallback to LLM-assigned levels
        high_matches = [m for m in scored_papers if m.coverage_level == 3]
    
    # Generate keywords for evidence search
    evidence_keywords = make_keywords_for_evidence(idea_features)
    
    if high_matches:
        for match in high_matches:
            paper = state.papers[match.paper_id]
            lines.append(f"### {paper.title}")
            lines.append(f"- **Venue**: {paper.venue or 'Unknown'}, {paper.year or 'Unknown'}")
            lines.append(f"- **Citations**: {paper.citation_count or 'N/A'}")
            lines.append(f"- **Covered Facets**: {', '.join(match.covered_facets) if match.covered_facets else 'N/A'}")
            if match.structural_score > 0:
                lines.append(f"- **Structural Overlap**: {', '.join(match.structural_overlap)}")
            lines.append(f"- **Notes**: {_clean_note_for_report(match.notes)}")
            if paper.doi:
                lines.append(f"- **DOI**: [{paper.doi}](https://doi.org/{paper.doi})")
            
            # Evidence Snippets
            snippets = find_evidence_snippets(paper, evidence_keywords, max_snippets=3)
            if snippets:
                lines.append("")
                lines.append("**Evidence snippets**")
                for s in snippets:
                    lines.append(f"- {s}")
            
            lines.append("")
    else:
        lines.append("- None found.")
        lines.append("")
    
    # 4. Partial-overlap (level 2)
    lines.append("## 3. Partial-Overlap Prior Art (Coverage Level = 2)")
    lines.append("These papers cover some aspects of the idea:")
    lines.append("")
    
    # Issue 6: Use Block 3 coverage_levels when available
    if coverage_levels:
        partial_paper_ids = {pid for pid, lvl in coverage_levels.items() if lvl == 2}
        partial_matches = [m for m in scored_papers if m.paper_id in partial_paper_ids]
    else:
        partial_matches = [m for m in scored_papers if m.coverage_level == 2]
    if partial_matches:
        for match in partial_matches:
            paper = state.papers[match.paper_id]
            lines.append(f"### {paper.title}")
            lines.append(f"- **Venue**: {paper.venue or 'Unknown'}, {paper.year or 'Unknown'}")
            lines.append(f"- **Citations**: {paper.citation_count or 'N/A'}")
            lines.append(f"- **Covered Facets**: {', '.join(match.covered_facets) if match.covered_facets else 'N/A'}")
            if match.structural_score > 0:
                lines.append(f"- **Structural Overlap**: {', '.join(match.structural_overlap)}")
            lines.append(f"- **Notes**: {_clean_note_for_report(match.notes)}")
            lines.append("")
    else:
        lines.append("- None found.")
        lines.append("")
    
    # 5. Adjacent Work
    lines.append("## 4. Adjacent Work (Coverage Level = 1)")
    lines.append("Related work in the same domain but with different approaches:")
    lines.append("")
    
    # Issue 1: Use Block 3 coverage_levels (in_domain + level=1)
    if coverage_levels:
        adjacent_paper_ids = {pid for pid, lvl in coverage_levels.items() if lvl == 1}
        adjacent_matches = [m for m in scored_papers if m.paper_id in adjacent_paper_ids]
    else:
        adjacent_matches = [m for m in scored_papers if m.coverage_level == 1]
    if adjacent_matches:
        # Separate surveys from non-surveys, limit surveys to 3
        survey_keywords = ["survey", "review", "overview", "tutorial"]
        surveys = []
        non_surveys = []
        
        for match in adjacent_matches:
            paper = state.papers[match.paper_id]
            title_lower = paper.title.lower()
            if any(kw in title_lower for kw in survey_keywords):
                surveys.append(match)
            else:
                non_surveys.append(match)
        
        # Show non-surveys first (up to 7), then surveys (up to 3)
        shown = 0
        for match in non_surveys[:7]:
            paper = state.papers[match.paper_id]
            lines.append(f"- **{paper.title}** ({paper.venue or 'Unknown'}, {paper.year or 'Unknown'})")
            lines.append(f"  - {_clean_note_for_report(match.notes)}")
            shown += 1
        
        surveys_shown = 0
        for match in surveys[:3]:
            paper = state.papers[match.paper_id]
            lines.append(f"- **{paper.title}** ({paper.venue or 'Unknown'}, {paper.year or 'Unknown'})")
            lines.append(f"  - {_clean_note_for_report(match.notes)}")
            surveys_shown += 1
        
        if len(surveys) > 3:
            lines.append(f"- *(+ {len(surveys) - 3} additional survey/review papers omitted)*")
    else:
        lines.append("- None found.")
    lines.append("")
    
    # 5. High-level Structural Coverage (Issue 3: Use FacetCoverageStats)
    if facet_views:
        lines.append(render_high_level_coverage_section_v2(facet_coverage, facet_views))
    else:
        # Fallback to old phrase-counting logic
        lines.append(render_high_level_coverage_section(idea_high_level_facets, state))
    
    # 6. Structural Coverage Section (Issue 5: Conditionally display)
    cfg = config or PriorArtConfig()
    if cfg.enable_dataset_metric_stats:
        lines.append(render_structural_coverage_section(idea_features, state))
    else:
        lines.append("## 5. Structural Coverage of Your Idea")
        lines.append("_Dataset/metric statistics are temporarily disabled pending refinement._")
        lines.append("")
    
    # 7. Facet-Level Gap Analysis (Issue 4: Use FacetCoverageStats)
    lines.append("## 6. Facet-Level Gap Analysis")
    lines.append("Coverage of each facet in papers with partial or high overlap:")
    lines.append("")
    lines.append("> **Note**: 'Not found' means the facet was not clearly identified in retrieved papers. ")
    lines.append("> This may indicate a gap OR that relevant work exists but was not retrieved.")
    lines.append("")
    
    # Issue 4: Use FacetCoverageStats when available
    if facet_views:
        for facet_id, view in facet_views.items():
            if facet_id.startswith("F"):  # Only core facets F1-F4
                count_any = view.stats.count_any
                count_strong = view.stats.count_strong
                lines.append(f'- **{view.human_label}** — {count_strong} strong, {count_any} total paper(s)')
                if count_any == 0:
                    lines.append(
                        "  - ⚠️ **Not found in retrieved corpus** — may be a gap or uncovered in search"
                    )
    else:
        # Fallback to old LLM-based counting
        relevant_matches = [m for m in scored_papers if m.coverage_level >= 2]
        for i, facet in enumerate(facets, 1):
            label = f"F{i}"
            count = sum(1 for m in relevant_matches if label in m.covered_facet_labels)
            lines.append(f'- **{label}**: "{facet}" — covered in {count} paper(s)')
            if count == 0:
                lines.append(
                    "  - ⚠️ **Not found in retrieved corpus** — may be a gap or uncovered in search"
                )
    
    lines.append("")
    lines.append("---")
    lines.append("**⚠️ Disclaimer**: This is an automated prior-art check based on paper metadata and LLM analysis. ")
    lines.append("Results may contain errors due to: incomplete retrieval, LLM hallucination, or extraction failures. ")
    lines.append("**Always verify key references manually** before making high-stakes decisions.")
    
    return "\\n".join(lines)


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
    
    # Apply debug mode overrides for concurrency
    if debug_mode:
        cfg.max_workers_openalex = 1
        cfg.max_workers_s2 = 1
        cfg.max_workers_pdf = 1
    
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
