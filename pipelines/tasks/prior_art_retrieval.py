"""
Prior Art Check - Retrieval Module

This module contains functions for candidate paper discovery:
- LLM-generated seed papers and queries
- OpenAlex dual-bucket search
- Citation graph expansion via Semantic Scholar
- Recency quota management

These functions are used by the main prior_art_check orchestrator.
"""

from __future__ import annotations

import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING

from loguru import logger
from smolagents import LiteLLMModel

from pipelines.state import PipelineState, Paper
from pipelines.clients import SemanticScholarClient
from pipelines.llm_config import get_litellm_config
from tools.openalex_search import OpenAlexWorksSearchTool

from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch
from pipelines.tasks.prior_art_llm_utils import (
    extract_text_from_llm_response, 
    repair_json_output,
    parse_json_with_repair,
)


# =============================================================================
# LLM Seed Papers and Query Generation
# =============================================================================

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
    text = extract_text_from_llm_response(resp)

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
    max_workers: int = 4,
) -> List[Paper]:
    """
    For each LLM-proposed seed (title-based), use OpenAlex's "title.search"
    filter to find the best-matching real paper. If a good match is found,
    convert it into our internal Paper model and return.

    This function is responsible for removing hallucinated papers.
    
    NOTE: Rate limiting is handled at the client layer via openalex_rate_limiter.
    No batch-level sleeps are used here.
    
    Args:
        seeds: List of seed paper dicts from LLM (with 'title' key)
        max_workers: Number of parallel threads
    """
    from pipelines.tasks.prior_art_utils import run_parallel

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

    # Use single executor with per-request rate limiting at client layer
    validated_papers = run_parallel(
        items=seeds,
        process_func=_validate_single_seed,
        max_workers=max_workers,
        desc="ValidateSeeds",
    )
            
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
    text = extract_text_from_llm_response(resp)

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
        text = extract_text_from_llm_response(resp)
        
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
        text = extract_text_from_llm_response(resp)
        
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


# =============================================================================
# Citation Graph Expansion
# =============================================================================

def expand_corpus_via_citation_graph(
    state: PipelineState,
    matches: List[PriorArtMatch],
    max_seed_papers: int = 10,
    max_neighbors_per_seed: int = 50,
    max_workers: int = 4,
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
    
    NOTE: Rate limiting is handled at the client layer via s2_rate_limiter.
    No batch-level sleeps are used here.
    
    Args:
        max_workers: Number of parallel threads
    """
    from pipelines.tasks.prior_art_utils import run_parallel
    
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

    def _expand_single_seed(match: PriorArtMatch) -> int:
        """Expand citations/references for a single seed paper. Returns count of new papers added."""
        # Instantiate client locally for thread safety
        local_s2_client = SemanticScholarClient()
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

    # Use single executor with per-request rate limiting at client layer
    results = run_parallel(
        items=seeds,
        process_func=_expand_single_seed,
        max_workers=max_workers,
        desc="CitationGraph",
    )
    
    new_paper_count = sum(r for r in results if isinstance(r, int))
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
