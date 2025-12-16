"""
Web Search Client Module

This module provides unified web search functionality using:
- SerpAPI (Google): 2 calls per idea (all_years + recent_range) - quota conscious
- DuckDuckGo: 6 calls per idea (3 queries × 2 modes) - free, for coverage

The design minimizes SerpAPI usage while maintaining comprehensive coverage
through DuckDuckGo complementary searches.
"""

from __future__ import annotations

import os
import json
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from pipelines.tasks.prior_art_types import PriorArtConfig


# =============================================================================
# SerpAPI Client (Quota-Conscious: 2 calls per idea)
# =============================================================================

def build_tbs_from_year_range(year_start: int, year_end: int) -> str:
    """
    Build Google's tbs (time-based search) parameter for date range filtering.
    """
    cd_min = f"01/01/{year_start}"
    cd_max = f"12/31/{year_end}"
    return f"cdr:1,cd_min:{cd_min},cd_max:{cd_max}"


def serpapi_search_google(
    query: str,
    num_results: int = 30,
    tbs: Optional[str] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Execute a single SerpAPI Google search.
    
    NOTE: No site filter (arxiv vs non-arxiv) - we let results come naturally
    and classify later by URL.
    
    Args:
        query: The search query (no site modifiers)
        num_results: Number of results to request
        tbs: Optional time-based search filter
        timeout: Request timeout in seconds
        
    Returns:
        Raw SerpAPI response as dict
    """
    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        logger.warning("[SerpAPI] SERPAPI_API_KEY not set in environment")
        return {"error": "SERPAPI_API_KEY not set", "organic_results": []}
    
    params = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": num_results,
        "hl": "en",
        "google_domain": "google.com",
    }
    
    if tbs:
        params["tbs"] = tbs
    
    try:
        logger.debug(f"[SerpAPI] Calling Google search: '{query[:50]}...' (tbs={tbs})")
        resp = requests.get(
            "https://serpapi.com/search",
            params=params,
            timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        result_count = len(data.get("organic_results", []))
        logger.debug(f"[SerpAPI] Got {result_count} results")
        return data
    except requests.exceptions.Timeout:
        logger.warning(f"[SerpAPI] Request timeout for query: {query[:50]}...")
        return {"error": "timeout", "organic_results": []}
    except requests.exceptions.RequestException as e:
        logger.warning(f"[SerpAPI] Request error: {e}")
        return {"error": str(e), "organic_results": []}


def run_serpapi_for_idea(
    main_query: str,
    year_start: Optional[int],
    year_end: Optional[int],
    cfg: "PriorArtConfig",
) -> List[Dict[str, Any]]:
    """
    Run SerpAPI for a single idea: exactly 2 API calls.
    
    Modes:
    1. serpapi_all_years: No date filter, get broad historical results
    2. serpapi_recent: Date filtered to year_start-year_end
    
    Args:
        main_query: The primary search query (generated from idea)
        year_start: Start year for recent search
        year_end: End year for recent search
        cfg: PriorArtConfig with serpapi settings
        
    Returns:
        List of result dicts with source_mode and raw results
    """
    results = []
    
    # Mode 1: All years (no tbs)
    logger.info(f"[SerpAPI] Mode 1/2: all_years for '{main_query[:40]}...'")
    all_years_raw = serpapi_search_google(
        query=main_query,
        num_results=cfg.serpapi_num_results_all,
        tbs=None,
        timeout=cfg.serpapi_timeout_sec,
    )
    results.append({
        "source": "serpapi",
        "source_mode": "serpapi_all_years",
        "query": main_query,
        "raw": all_years_raw,
    })
    
    # Mode 2: Recent range (with tbs)
    # Fallback if years not provided
    if year_start is None or year_end is None:
        current_year = datetime.now().year
        year_end = year_end or current_year
        year_start = year_start or (current_year - 3)
    
    tbs = build_tbs_from_year_range(year_start, year_end)
    logger.info(f"[SerpAPI] Mode 2/2: recent_range ({year_start}-{year_end}) for '{main_query[:40]}...'")
    recent_raw = serpapi_search_google(
        query=main_query,
        num_results=cfg.serpapi_num_results_recent,
        tbs=tbs,
        timeout=cfg.serpapi_timeout_sec,
    )
    results.append({
        "source": "serpapi",
        "source_mode": "serpapi_recent",
        "query": main_query,
        "raw": recent_raw,
    })
    
    total = sum(len(r["raw"].get("organic_results", [])) for r in results)
    logger.info(f"[SerpAPI] Total: {total} results from 2 API calls")
    
    return results


# =============================================================================
# DuckDuckGo Client (Free, for additional coverage)
# =============================================================================

def duckduckgo_search(
    query: str,
    max_results: int = 20,
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    """
    Search using DuckDuckGo HTML scraping (free, no API key needed).
    
    Uses the new ddgs library (preferred) or falls back to duckduckgo_search
    if ddgs is not installed.
    
    Args:
        query: Search query
        max_results: Maximum results to return
        timeout: Request timeout
        
    Returns:
        List of result dicts with title, link, snippet
    """
    results = []
    
    # Try new ddgs package first (preferred)
    try:
        from ddgs import DDGS
        
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        
        # Normalize to our format
        normalized = []
        for r in results:
            normalized.append({
                "title": r.get("title", ""),
                "link": r.get("href", r.get("link", "")),
                "snippet": r.get("body", r.get("snippet", "")),
            })
        
        return normalized
        
    except ImportError:
        pass  # Try old package
    except Exception as e:
        logger.debug(f"[DDG] ddgs search error: {e}")
        # Try old package as fallback
    
    # Fallback to old duckduckgo_search package
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning, 
                                    message=".*has been renamed.*")
            from duckduckgo_search import DDGS
        
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        
        # Normalize to our format
        normalized = []
        for r in results:
            normalized.append({
                "title": r.get("title", ""),
                "link": r.get("href", r.get("link", "")),
                "snippet": r.get("body", r.get("snippet", "")),
            })
        
        return normalized
        
    except ImportError:
        logger.warning("[DDG] Neither ddgs nor duckduckgo_search library installed, skipping DDG search")
        return []
    except Exception as e:
        logger.warning(f"[DDG] Search error: {e}")
        return []


def run_duckduckgo_for_idea(
    queries: List[str],
    cfg: "PriorArtConfig",
) -> List[Dict[str, Any]]:
    """
    Run DuckDuckGo searches: 3 queries × 2 modes (arxiv/non-arxiv) = 6 calls.
    
    Args:
        queries: List of search queries
        cfg: PriorArtConfig with ddg settings
        
    Returns:
        List of result dicts with source_mode and normalized results
    """
    results = []
    max_results = cfg.ddg_max_results_per_query
    
    for qi, q in enumerate(queries, 1):
        # Mode 1: arxiv only
        logger.debug(f"[DDG] Query {qi}/{len(queries)} arxiv: '{q[:40]}...'")
        arxiv_results = duckduckgo_search(
            query=f"{q} site:arxiv.org",
            max_results=max_results,
        )
        for r in arxiv_results:
            results.append({
                "source": "duckduckgo",
                "source_mode": "ddg_arxiv",
                "query": q,
                "title": r.get("title", ""),
                "link": r.get("link", ""),
                "snippet": r.get("snippet", ""),
            })
        
        # Mode 2: non-arxiv
        logger.debug(f"[DDG] Query {qi}/{len(queries)} non-arxiv: '{q[:40]}...'")
        non_arxiv_results = duckduckgo_search(
            query=f"{q} -site:arxiv.org",
            max_results=max_results,
        )
        for r in non_arxiv_results:
            results.append({
                "source": "duckduckgo",
                "source_mode": "ddg_non_arxiv",
                "query": q,
                "title": r.get("title", ""),
                "link": r.get("link", ""),
                "snippet": r.get("snippet", ""),
            })
    
    logger.info(f"[DDG] Total: {len(results)} results from {len(queries) * 2} searches")
    return results


# =============================================================================
# Unified Web Search Entry Point (Parallel Execution)
# =============================================================================

def discover_web_candidates(
    idea_description: str,
    year_start: Optional[int],
    year_end: Optional[int],
    cfg: "PriorArtConfig",
    generate_queries_fn=None,
) -> List[Dict[str, Any]]:
    """
    Unified web search entry point: SerpAPI (2 calls) + DuckDuckGo (6 calls).
    
    All HTTP calls are executed in parallel using ThreadPoolExecutor.
    
    Args:
        idea_description: The research idea
        year_start: Start year for date filtering  
        year_end: End year for date filtering
        cfg: PriorArtConfig
        generate_queries_fn: Function to generate queries from idea
        
    Returns:
        Combined list of all web search results
    """
    # Generate queries (1 for SerpAPI, 3 for DDG)
    if generate_queries_fn is None:
        # Fallback: use idea directly
        serpapi_query = idea_description[:200]
        ddg_queries = [idea_description[:200]]
    else:
        # Generate 3 queries, use first for SerpAPI, all 3 for DDG
        all_queries = generate_queries_fn(
            idea_description=idea_description,
            max_queries=cfg.ddg_num_queries,
        )
        serpapi_query = all_queries[0] if all_queries else idea_description[:200]
        ddg_queries = all_queries if all_queries else [idea_description[:200]]
    
    logger.info(f"[WebSearch] SerpAPI query: '{serpapi_query[:50]}...'")
    logger.info(f"[WebSearch] DDG queries: {len(ddg_queries)}")
    
    all_results = []
    
    with ThreadPoolExecutor(max_workers=cfg.max_workers_web_search) as executor:
        futures = []
        
        # Submit SerpAPI task (2 calls)
        if cfg.enable_serpapi:
            futures.append(executor.submit(
                run_serpapi_for_idea,
                serpapi_query,
                year_start,
                year_end,
                cfg,
            ))
        
        # Submit DuckDuckGo task (6 calls)
        if cfg.enable_duckduckgo:
            futures.append(executor.submit(
                run_duckduckgo_for_idea,
                ddg_queries,
                cfg,
            ))
        
        # Collect results
        for future in as_completed(futures):
            try:
                batch = future.result()
                all_results.extend(batch)
            except Exception as e:
                logger.exception(f"[WebSearch] Task failed: {e}")
    
    logger.info(f"[WebSearch] Total candidates: {len(all_results)}")
    return all_results


def extract_serpapi_organic_results(
    serpapi_result: Dict[str, Any],
) -> List[Dict[str, str]]:
    """
    Extract organic results from a SerpAPI response.
    
    Args:
        serpapi_result: A result dict from run_serpapi_for_idea
        
    Returns:
        List of normalized result dicts
    """
    raw = serpapi_result.get("raw", {})
    organic = raw.get("organic_results", [])
    
    normalized = []
    for r in organic:
        normalized.append({
            "source": "serpapi",
            "source_mode": serpapi_result.get("source_mode", ""),
            "query": serpapi_result.get("query", ""),
            "title": r.get("title", ""),
            "link": r.get("link", ""),
            "snippet": r.get("snippet", ""),
            "date": r.get("date"),
        })
    
    return normalized


def normalize_all_web_results(
    raw_results: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    Normalize all web search results to a unified format.
    
    Args:
        raw_results: Output from discover_web_candidates
        
    Returns:
        Flat list of normalized results with consistent fields
    """
    normalized = []
    
    for r in raw_results:
        source = r.get("source", "")
        
        if source == "serpapi":
            # SerpAPI results need extraction from raw
            extracted = extract_serpapi_organic_results(r)
            normalized.extend(extracted)
        else:
            # DDG results are already normalized
            normalized.append({
                "source": r.get("source", "duckduckgo"),
                "source_mode": r.get("source_mode", ""),
                "query": r.get("query", ""),
                "title": r.get("title", ""),
                "link": r.get("link", ""),
                "snippet": r.get("snippet", ""),
            })
    
    return normalized


# =============================================================================
# Legacy compatibility (to be removed after migration)
# =============================================================================

# Keep old function names for backwards compatibility during migration
def run_all_serpapi_modes(
    query: str,
    year_start: int,
    year_end: int,
    num_results: int = 10,
    use_duckduckgo: bool = False,
    timeout: int = 15,
) -> Dict[str, Dict[str, Any]]:
    """
    DEPRECATED: Use run_serpapi_for_idea instead.
    
    This function is kept for backwards compatibility.
    It now calls the new 2-mode SerpAPI logic internally.
    """
    logger.warning("[SerpAPI] run_all_serpapi_modes is deprecated, use run_serpapi_for_idea")
    
    # Create a minimal config-like object
    class MinimalConfig:
        serpapi_num_results_all = num_results * 2
        serpapi_num_results_recent = num_results * 2
        serpapi_timeout_sec = timeout
    
    results = run_serpapi_for_idea(query, year_start, year_end, MinimalConfig())
    
    # Convert to old format
    old_format = {}
    for r in results:
        mode = r.get("source_mode", "unknown")
        old_format[mode] = r.get("raw", {})
    
    return old_format


def combine_all_mode_results(
    all_modes_results: Dict[str, Dict[str, Any]],
    top_k_per_mode: int = 10,
) -> Dict[str, List[Dict[str, str]]]:
    """
    DEPRECATED: Use normalize_all_web_results instead.
    """
    combined = {}
    for mode, response in all_modes_results.items():
        organic = response.get("organic_results", [])[:top_k_per_mode]
        combined[mode] = [
            {
                "title": r.get("title", ""),
                "link": r.get("link", ""),
                "snippet": r.get("snippet", ""),
                "date": r.get("date"),
            }
            for r in organic
        ]
    return combined
