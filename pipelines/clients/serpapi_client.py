"""
SerpAPI Client Module

This module provides functions to search the web using SerpAPI
(Google/DuckDuckGo) for discovering seed papers.

The 4-mode search strategy:
1. all_non_arxiv: Full time range, exclude arxiv
2. all_arxiv: Full time range, arxiv only
3. range_non_arxiv: Date range filtered, exclude arxiv  
4. range_arxiv: Date range filtered, arxiv only
"""

from __future__ import annotations

import os
import json
import requests
from typing import List, Dict, Any, Optional
from loguru import logger


def build_tbs_from_year_range(year_start: int, year_end: int) -> str:
    """
    Build Google's tbs (time-based search) parameter for date range filtering.
    
    Args:
        year_start: Start year (inclusive)
        year_end: End year (inclusive)
        
    Returns:
        tbs string for Google search API
    """
    cd_min = f"01/01/{year_start}"
    cd_max = f"12/31/{year_end}"
    return f"cdr:1,cd_min:{cd_min},cd_max:{cd_max}"


def serpapi_search(
    query: str,
    tbs: Optional[str] = None,
    num_results: int = 10,
    only_arxiv: Optional[bool] = None,
    use_duckduckgo: bool = False,
    timeout: int = 15,
) -> Dict[str, Any]:
    """
    Execute a single SerpAPI search.
    
    Args:
        query: The search query
        tbs: Optional time-based search filter (Google only)
        num_results: Number of results to return
        only_arxiv: True=only arxiv, False=exclude arxiv, None=no filter
        use_duckduckgo: If True, use DuckDuckGo instead of Google
        timeout: Request timeout in seconds
        
    Returns:
        Raw SerpAPI response as dict
    """
    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        logger.warning("[SerpAPI] SERPAPI_API_KEY not set in environment")
        return {"error": "SERPAPI_API_KEY not set", "organic_results": []}
    
    # Build query with site filter
    final_query = query
    if only_arxiv is True:
        final_query = f"{query} site:arxiv.org"
    elif only_arxiv is False:
        final_query = f"{query} -site:arxiv.org"
    
    # Select engine
    engine = "duckduckgo" if use_duckduckgo else "google"
    
    params = {
        "engine": engine,
        "q": final_query,
        "api_key": api_key,
        "num": num_results,
        "hl": "en",
    }
    
    # Google-specific params
    if not use_duckduckgo:
        params["google_domain"] = "google.com"
        if tbs:
            params["tbs"] = tbs
    
    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params=params,
            timeout=timeout
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        logger.warning(f"[SerpAPI] Request timeout for query: {query[:50]}...")
        return {"error": "timeout", "organic_results": []}
    except requests.exceptions.RequestException as e:
        logger.warning(f"[SerpAPI] Request error: {e}")
        return {"error": str(e), "organic_results": []}


def run_all_serpapi_modes(
    query: str,
    year_start: int,
    year_end: int,
    num_results: int = 10,
    use_duckduckgo: bool = False,
    timeout: int = 15,
) -> Dict[str, Dict[str, Any]]:
    """
    Run SerpAPI search in 4 modes for comprehensive coverage.
    
    Modes:
    - all_non_arxiv: No date filter, exclude arxiv (finds blog posts, papers on other sites)
    - all_arxiv: No date filter, arxiv only (finds arxiv papers of all time)
    - range_non_arxiv: Date filtered, exclude arxiv (recent non-arxiv)
    - range_arxiv: Date filtered, arxiv only (recent arxiv papers)
    
    Args:
        query: The search query
        year_start: Start year for date filtering
        year_end: End year for date filtering
        num_results: Results per mode
        use_duckduckgo: Use DuckDuckGo instead of Google
        timeout: Request timeout
        
    Returns:
        Dict mapping mode name to SerpAPI response
    """
    tbs_range = build_tbs_from_year_range(year_start, year_end)
    
    results = {}
    
    # Mode 1: All time, non-arxiv
    logger.debug(f"[SerpAPI] Searching: all_non_arxiv for '{query[:40]}...'")
    results["all_non_arxiv"] = serpapi_search(
        query=query,
        tbs=None,
        num_results=num_results,
        only_arxiv=False,
        use_duckduckgo=use_duckduckgo,
        timeout=timeout,
    )
    
    # Mode 2: All time, arxiv only
    logger.debug(f"[SerpAPI] Searching: all_arxiv for '{query[:40]}...'")
    results["all_arxiv"] = serpapi_search(
        query=query,
        tbs=None,
        num_results=num_results,
        only_arxiv=True,
        use_duckduckgo=use_duckduckgo,
        timeout=timeout,
    )
    
    # Mode 3: Date range, non-arxiv
    logger.debug(f"[SerpAPI] Searching: range_non_arxiv for '{query[:40]}...'")
    results["range_non_arxiv"] = serpapi_search(
        query=query,
        tbs=tbs_range if not use_duckduckgo else None,  # DuckDuckGo doesn't support tbs
        num_results=num_results,
        only_arxiv=False,
        use_duckduckgo=use_duckduckgo,
        timeout=timeout,
    )
    
    # Mode 4: Date range, arxiv only
    logger.debug(f"[SerpAPI] Searching: range_arxiv for '{query[:40]}...'")
    results["range_arxiv"] = serpapi_search(
        query=query,
        tbs=tbs_range if not use_duckduckgo else None,
        num_results=num_results,
        only_arxiv=True,
        use_duckduckgo=use_duckduckgo,
        timeout=timeout,
    )
    
    # Log summary
    total_results = sum(
        len(r.get("organic_results", [])) 
        for r in results.values()
    )
    logger.info(f"[SerpAPI] Query '{query[:30]}...': {total_results} total results across 4 modes")
    
    return results


def extract_organic_results(
    serpapi_response: Dict[str, Any],
    top_k: int = 10,
) -> List[Dict[str, str]]:
    """
    Extract and slim down organic results from SerpAPI response.
    
    Args:
        serpapi_response: Raw SerpAPI response
        top_k: Maximum results to extract
        
    Returns:
        List of dicts with title, link, snippet, date
    """
    organic = serpapi_response.get("organic_results", [])[:top_k]
    
    slim_results = []
    for r in organic:
        slim_results.append({
            "title": r.get("title", ""),
            "link": r.get("link", ""),
            "snippet": r.get("snippet", ""),
            "date": r.get("date"),  # May be None
        })
    
    return slim_results


def combine_all_mode_results(
    all_modes_results: Dict[str, Dict[str, Any]],
    top_k_per_mode: int = 10,
) -> Dict[str, List[Dict[str, str]]]:
    """
    Combine and slim results from all 4 search modes.
    
    Args:
        all_modes_results: Output from run_all_serpapi_modes
        top_k_per_mode: Max results per mode
        
    Returns:
        Dict mapping mode name to list of slim results
    """
    combined = {}
    for mode, response in all_modes_results.items():
        combined[mode] = extract_organic_results(response, top_k_per_mode)
    return combined
