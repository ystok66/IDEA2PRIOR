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
import os
import re
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
from pipelines.tasks.prior_art_utils import is_excluded_paper


# =============================================================================
# LLM Seed Papers and Query Generation
# =============================================================================

def seed_papers_from_llm(
    agent,
    idea_description: str,
    max_seeds: int = 10,
) -> List[Dict[str, str]]:
    """
    Use the LLM's own world knowledge to propose a small set of
    potentially relevant papers (as "seed" candidates).

    NOTE: This function does NOT use topic - paper recall is based
    entirely on the idea_description to avoid domain bias.

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

    prompt = f"""You are an expert researcher.

Your task is to recall real academic papers that are closely related
to the following research idea:

\"\"\"{idea_description}\"\"\"

Requirements:
- Only include real papers that you reasonably believe exist.
- Prefer papers that describe methods, models, or techniques that
  would be informative when evaluating the novelty of this idea.
- If you are uncertain about a detail (e.g., exact year or venue),
  you may set that field to null.

For each paper, provide:
- title: the paper's title (string)
- venue: conference or journal name, or null if unknown
- year: integer publication year, or null if unknown
- doi: string or null
- arxiv_id: string (e.g., "2104.12345") or null
- url: string or null (e.g., publisher or arXiv URL)

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

Constraints:
- At most {max_seeds} papers.
- Do NOT include any commentary outside the JSON.

JSON:
"""
    messages = [{"role": "user", "content": prompt}]
    resp = expert_model(messages)
    text = extract_text_from_llm_response(resp)

    # Parse JSON safely; fallback to empty list on error.
    try:
        data = json.loads(text)
    except Exception:
        # Try to repair JSON
        try:
            repaired = repair_json_output(text)
            data = json.loads(repaired)
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

    logger.info(f"[PriorArt] LLM proposed {len(seeds)} seed papers (unvalidated):")
    for i, seed in enumerate(seeds, 1):
        title = seed.get("title", "Unknown")[:60]
        venue = seed.get("venue", "?")
        year = seed.get("year", "?")
        logger.info(f"  [{i}] {title}... ({venue}, {year})")
    
    return seeds

def extract_arxiv_id_from_url(url: str) -> Optional[str]:
    """
    Extract arXiv ID from URL if it's an arXiv paper.
    
    Examples:
    - https://arxiv.org/abs/2301.12345 -> 2301.12345
    - https://arxiv.org/pdf/2301.12345.pdf -> 2301.12345
    - http://www.arxiv.org/abs/1901.00001v2 -> 1901.00001
    """
    import re
    if not url:
        return None
    
    # Match arxiv IDs in URLs
    patterns = [
        r'arxiv\.org/abs/(\d{4}\.\d{4,5})',
        r'arxiv\.org/pdf/(\d{4}\.\d{4,5})',
        r'arxiv\.org/abs/(\d{4}\.\d{4,5})v\d+',
        r'arxiv\.org/pdf/(\d{4}\.\d{4,5})v\d+',
        # Old format: arxiv.org/abs/cs/0001001
        r'arxiv\.org/abs/([a-z-]+/\d{7})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return None


def validate_via_semantic_scholar(
    paper_id: str,
    title: str = "",
) -> Optional[Paper]:
    """
    Validate a paper via Semantic Scholar API.
    
    Args:
        paper_id: Can be arXiv ID (e.g., "2301.12345"), DOI, or S2 ID
        title: Original title for logging
        
    Returns:
        Paper object if found, None otherwise
    """
    import requests
    
    base_url = os.getenv("Semantic_Scholar_URL", "https://api.semanticscholar.org/graph/v1")
    api_key = os.getenv("Semantic_Scholar_API_key")
    
    # For arXiv IDs, prepend ARXIV:
    if paper_id and re.match(r'^\d{4}\.\d{4,5}$', paper_id):
        paper_id = f"ARXIV:{paper_id}"
    
    url = f"{base_url}/paper/{paper_id}"
    params = {
        "fields": "title,year,venue,abstract,citationCount,externalIds,paperId"
    }
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        if response.status_code == 404:
            logger.debug(f"[ValidateSeed-S2] Paper not found: {paper_id}")
            return None
        response.raise_for_status()
        data = response.json()
        
        # Extract DOI from externalIds
        external_ids = data.get("externalIds", {})
        doi = external_ids.get("DOI")
        arxiv_id = external_ids.get("ArXiv")
        
        paper = Paper(
            title=data.get("title", ""),
            year=data.get("year"),
            venue=data.get("venue"),
            abstract=data.get("abstract"),
            doi=doi,
            citation_count=data.get("citationCount"),
            s2_id=data.get("paperId"),
        )
        
        # Store arxiv_id if available
        if arxiv_id:
            paper.arxiv_id = arxiv_id
        
        logger.debug(f"[ValidateSeed-S2] Found paper: {data.get('title', '')[:50]}...")
        return paper
        
    except Exception as e:
        logger.debug(f"[ValidateSeed-S2] Error for {paper_id}: {e}")
        return None


def validate_via_openalex_title_match(
    title: str,
    threshold: float = 0.35,
) -> Optional[Paper]:
    """
    Validate a paper via OpenAlex title search with Jaccard similarity matching.
    
    Args:
        title: Paper title to search
        threshold: Minimum Jaccard similarity to accept a match
        
    Returns:
        Paper object if found with good match, None otherwise
    """
    oa_tool = OpenAlexWorksSearchTool()
    
    try:
        res_json = oa_tool.forward(query=title, limit=5)
        if not res_json or res_json.startswith("Error"):
            return None
            
        works = json.loads(res_json)
        if not works:
            return None
        
        # Find best matching paper among candidates
        seed_words = set(title.lower().split())
        best_match = None
        best_overlap = 0.0
        
        for work in works:
            work_title = work.get("title", "")
            if not work_title:
                continue
            work_words = set(work_title.lower().split())
            
            if len(seed_words) > 0 and len(work_words) > 0:
                overlap_count = len(seed_words & work_words)
                overlap_ratio = overlap_count / len(seed_words | work_words)
                
                if overlap_ratio > best_overlap:
                    best_overlap = overlap_ratio
                    best_match = work
        
        if best_overlap < threshold:
            logger.debug(f"[ValidateSeed-OA] Low overlap ({best_overlap:.2f}) for '{title[:40]}...'")
            return None
        
        logger.debug(f"[ValidateSeed-OA] Matched '{title[:40]}...' with overlap {best_overlap:.2f}")
        
        doi = best_match.get("doi")
        if doi and "doi.org/" in doi:
            doi = doi.split("doi.org/")[-1]
            
        return Paper(
            title=best_match.get("title", ""),
            year=best_match.get("year"),
            venue=best_match.get("venue"),
            openalex_id=best_match.get("openalex_id"),
            doi=doi,
            citation_count=best_match.get("cited_by_count"),
            abstract=best_match.get("abstract"),  # Now includes abstract from OpenAlex
        )
        
    except Exception as e:
        logger.debug(f"[ValidateSeed-OA] Error for '{title[:40]}...': {e}")
        return None


def validate_llm_seeds_via_openalex(
    seeds: List[Dict[str, str]],
    max_workers: int = 4,
) -> List[Paper]:
    """
    Multi-source seed validation: tries arXiv URL, Semantic Scholar, then OpenAlex.
    
    Validation priority:
    1. If seed has an arXiv URL → extract ID, query Semantic Scholar directly (with abstract)
    2. If seed has DOI → query Semantic Scholar by DOI
    3. Fallback to OpenAlex title matching
    
    This function is responsible for removing hallucinated papers while
    preserving papers that exist but may not be in OpenAlex.
    
    Args:
        seeds: List of seed paper dicts (with 'title', optionally 'url', 'doi', 'arxiv_id')
        max_workers: Number of parallel threads
    """
    from pipelines.tasks.prior_art_utils import run_parallel

    def _validate_single_seed(seed: Dict[str, str]) -> Optional[Paper]:
        """Validate a single seed via multiple sources. Returns Paper or None."""
        title = seed.get("title", "")
        url = seed.get("url", "")
        doi = seed.get("doi")
        arxiv_id = seed.get("arxiv_id")
        
        # === Strategy 1: arXiv URL extraction ===
        if url:
            extracted_arxiv_id = extract_arxiv_id_from_url(url)
            if extracted_arxiv_id:
                logger.debug(f"[ValidateSeed] Trying arXiv ID from URL: {extracted_arxiv_id}")
                paper = validate_via_semantic_scholar(extracted_arxiv_id, title)
                if paper:
                    logger.info(f"[ValidateSeed] ✓ Validated via arXiv URL: {paper.title[:50]}...")
                    return paper
        
        # === Strategy 2: Direct arxiv_id if provided ===
        if arxiv_id:
            logger.debug(f"[ValidateSeed] Trying direct arXiv ID: {arxiv_id}")
            paper = validate_via_semantic_scholar(arxiv_id, title)
            if paper:
                logger.info(f"[ValidateSeed] ✓ Validated via arXiv ID: {paper.title[:50]}...")
                return paper
        
        # === Strategy 3: DOI lookup via Semantic Scholar ===
        if doi:
            logger.debug(f"[ValidateSeed] Trying DOI: {doi}")
            paper = validate_via_semantic_scholar(doi, title)
            if paper:
                logger.info(f"[ValidateSeed] ✓ Validated via DOI: {paper.title[:50]}...")
                return paper
        
        # === Strategy 4: OpenAlex title matching (original approach) ===
        paper = validate_via_openalex_title_match(title, threshold=0.35)
        if paper:
            logger.debug(f"[ValidateSeed] ✓ Validated via OpenAlex: {paper.title[:50]}...")
            return paper
        
        # === Strategy 5: Semantic Scholar title search as last resort ===
        # Try S2's paper search by title (less strict than OpenAlex)
        try:
            import requests
            base_url = os.getenv("Semantic_Scholar_URL", "https://api.semanticscholar.org/graph/v1")
            api_key = os.getenv("Semantic_Scholar_API_key")
            
            search_url = f"{base_url}/paper/search"
            params = {
                "query": title,
                "limit": 3,
                "fields": "title,year,venue,abstract,citationCount,externalIds,paperId"
            }
            headers = {}
            if api_key:
                headers["x-api-key"] = api_key
            
            response = requests.get(search_url, params=params, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                papers = data.get("data", [])
                
                # Check if any result is a good title match
                seed_words = set(title.lower().split())
                for p in papers:
                    p_title = p.get("title", "")
                    p_words = set(p_title.lower().split())
                    if len(seed_words) > 0 and len(p_words) > 0:
                        overlap = len(seed_words & p_words) / len(seed_words | p_words)
                        if overlap >= 0.35:
                            external_ids = p.get("externalIds", {})
                            paper = Paper(
                                title=p_title,
                                year=p.get("year"),
                                venue=p.get("venue"),
                                abstract=p.get("abstract"),
                                doi=external_ids.get("DOI"),
                                citation_count=p.get("citationCount"),
                                s2_id=p.get("paperId"),
                            )
                            logger.info(f"[ValidateSeed] ✓ Validated via S2 search: {p_title[:50]}...")
                            return paper
        except Exception as e:
            logger.debug(f"[ValidateSeed] S2 search error: {e}")
        
        logger.debug(f"[ValidateSeed] ✗ Could not validate: {title[:50]}...")
        return None

    # Use parallel execution
    validated_papers = run_parallel(
        items=seeds,
        process_func=_validate_single_seed,
        max_workers=max_workers,
        desc="ValidateSeeds",
    )
            
    logger.info(f"[PriorArt] Validated {len(validated_papers)}/{len(seeds)} seed papers.")
    return validated_papers


# =============================================================================
# SerpAPI-based Seed Paper Discovery
# =============================================================================

def generate_serpapi_queries(
    idea_description: str,
    max_queries: int = 3,
) -> List[str]:
    """
    Use expert LLM to generate web search queries purely from the idea.
    
    NOTE: This function does NOT use topic - queries are derived entirely
    from the idea_description to avoid domain bias.
    
    Args:
        idea_description: The research idea
        max_queries: Maximum number of queries to generate
        
    Returns:
        List of search query strings
    """
    model_id, api_base, api_key = get_litellm_config(role="expert")
    expert_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    prompt = f"""You are an expert researcher.

Your task is to design web search queries that will help find
academic papers related to the following research idea:

\"\"\"{idea_description}\"\"\"

Requirements:
- Each query MUST be suitable for Google or DuckDuckGo web search.
- Each query should look like what a researcher would actually type,
  combining key technical phrases from the idea.
- Focus on techniques, methods, or key concepts mentioned in the idea.
- Do NOT restrict the domain explicitly (e.g., do not add generic field names
  unless they clearly appear in the idea itself).
- Do NOT add site filters such as "site:arxiv.org" or date filters.
  These will be handled later by the system.
- **IMPORTANT: Do NOT use quotation marks ("") in queries. Write plain keywords
  without exact-match operators, as they severely limit search results.**
- Avoid overly generic queries (e.g., "deep learning method").
- Aim for diverse queries that cover different angles of the idea.
- At most {max_queries} queries.

Respond with ONLY valid JSON, no backticks:
{{"queries": ["query1", "query2", ...]}}
"""
    
    messages = [{"role": "user", "content": prompt}]
    resp = expert_model(messages)
    text = extract_text_from_llm_response(resp)
    
    try:
        data = json.loads(text)
        queries = data.get("queries", [])
    except Exception:
        try:
            repaired = repair_json_output(text)
            data = json.loads(repaired)
            queries = data.get("queries", [])
        except Exception:
            logger.warning("[SerpAPI] Failed to parse query generation JSON")
            return []
    
    logger.info(f"[SerpAPI] Generated {len(queries)} search queries:")
    for i, q in enumerate(queries, 1):
        logger.info(f"  [{i}] {q}")
    
    return queries[:max_queries]


def parse_serpapi_results_with_llm(
    idea_description: str,
    query: str,
    combined_results: Dict[str, List[Dict[str, str]]],
    max_papers: int = 10,
) -> List[Dict[str, Any]]:
    """
    Use light LLM to extract paper candidates from SerpAPI search results.
    
    NOTE: This function does NOT use topic - paper selection is based
    entirely on relevance to the idea_description.
    
    Args:
        idea_description: The research idea
        query: The search query used
        combined_results: Dict mapping mode name to list of slim results
        max_papers: Max papers to extract
        
    Returns:
        List of paper dicts with title, year, venue, url, source_mode, reason
    """
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    results_str = json.dumps(combined_results, ensure_ascii=False, indent=2)
    
    prompt = f"""You are a helpful research assistant.

You are given:
- A research idea: {idea_description}
- A web search query: {query}

You also have search results (from multiple modes), in JSON:
```json
{results_str}
```

Your task:
- From these results, identify up to {max_papers} entries that are MOST LIKELY to be academic papers relevant to the idea.
- Prefer results that clearly look like academic papers (e.g., conference proceedings, journal articles, arXiv papers, open-access repositories).
- For each selected result, output:
  - title: string (the paper title, cleaned up)
  - year: integer if you can guess from the snippet/title/URL, else null
  - venue: short string (e.g., conference name, journal name, "arXiv"), or null if unknown
  - url: the link
  - source_mode: which search mode it came from (one of: all_non_arxiv, all_arxiv, range_non_arxiv, range_arxiv)
  - reason: 1-2 sentences why this seems relevant to the idea.

Output format:
Return ONLY a JSON array of objects with the above fields. No extra commentary.

JSON:
"""
    
    messages = [{"role": "user", "content": prompt}]
    resp = light_model(messages)
    text = extract_text_from_llm_response(resp)
    
    try:
        data = json.loads(text)
    except Exception:
        try:
            repaired = repair_json_output(text)
            data = json.loads(repaired)
        except Exception:
            logger.warning("[SerpAPI] Failed to parse paper extraction JSON")
            return []
    
    if not isinstance(data, list):
        return []
    
    return data[:max_papers]


def serpapi_candidates_to_seeds(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    Convert SerpAPI paper candidates to seed paper format.
    
    Args:
        candidates: List of paper dicts from parse_serpapi_results_with_llm
        
    Returns:
        List of seed paper dicts compatible with validate_llm_seeds_via_openalex
    """
    seeds = []
    for c in candidates:
        title = c.get("title", "").strip()
        if not title:
            continue
        seeds.append({
            "title": title,
            "venue": c.get("venue"),
            "year": c.get("year"),
            "url": c.get("url"),
            "doi": None,
            "arxiv_id": None,
            "source": "serpapi",
        })
    return seeds


def merge_seeds(
    llm_seeds: List[Dict[str, str]],
    serpapi_seeds: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """
    Merge LLM-memory seeds with SerpAPI seeds, deduplicating by title.
    
    Args:
        llm_seeds: Seeds from LLM memory
        serpapi_seeds: Seeds from SerpAPI web search
        
    Returns:
        Merged and deduplicated list of seeds
    """
    def normalize_title(t: str) -> str:
        return " ".join(t.lower().split())
    
    by_title = {}
    
    # Add LLM seeds first (priority)
    for s in llm_seeds:
        key = normalize_title(s.get("title", ""))
        if key and key not in by_title:
            by_title[key] = s.copy()
    
    # Add SerpAPI seeds, merging info if duplicate
    for s in serpapi_seeds:
        key = normalize_title(s.get("title", ""))
        if not key:
            continue
        if key not in by_title:
            by_title[key] = s.copy()
        else:
            # Merge: fill in missing info
            existing = by_title[key]
            if not existing.get("year") and s.get("year"):
                existing["year"] = s["year"]
            if not existing.get("venue") and s.get("venue"):
                existing["venue"] = s["venue"]
            if not existing.get("url") and s.get("url"):
                existing["url"] = s["url"]
    
    return list(by_title.values())


def seed_papers_from_web_search(
    idea_description: str,
    year_start: int,
    year_end: int,
    cfg: "PriorArtConfig",
) -> List[Dict[str, str]]:
    """
    Discover seed papers using unified web search (SerpAPI + DuckDuckGo).
    
    NOTE: This function does NOT use topic - all queries and parsing
    are based entirely on the idea_description.
    
    Web search strategy (quota-conscious):
    - SerpAPI: 1 query × 2 modes (all_years + recent_range) = 2 API calls
    - DuckDuckGo: 3 queries × 2 modes (arxiv + non-arxiv) = 6 searches (free)
    
    All HTTP calls are parallelized for performance.
    
    Args:
        idea_description: The research idea
        year_start: Start year for date filtering
        year_end: End year for date filtering
        cfg: PriorArtConfig with web search settings
        
    Returns:
        List of seed paper dicts
    """
    from pipelines.clients.web_search_client import (
        discover_web_candidates,
        normalize_all_web_results,
    )
    
    # 1. Run unified web search (SerpAPI 2 calls + DDG 6 calls, parallel)
    logger.info("[WebSearch] Starting unified web search (SerpAPI + DuckDuckGo)...")
    raw_results = discover_web_candidates(
        idea_description=idea_description,
        year_start=year_start,
        year_end=year_end,
        cfg=cfg,
        generate_queries_fn=generate_serpapi_queries,
    )
    
    if not raw_results:
        logger.warning("[WebSearch] No results from web search")
        return []
    
    # 2. Normalize all results
    normalized = normalize_all_web_results(raw_results)
    logger.info(f"[WebSearch] Normalized {len(normalized)} total results")
    
    # 3. Group by source for LLM parsing
    # Group results into chunks for LLM parsing (avoid too long prompts)
    MAX_RESULTS_PER_LLM_CALL = 30
    
    all_candidates = []
    for start_idx in range(0, len(normalized), MAX_RESULTS_PER_LLM_CALL):
        chunk = normalized[start_idx:start_idx + MAX_RESULTS_PER_LLM_CALL]
        
        # Format for LLM
        combined_for_llm = {"web_results": [
            {
                "title": r.get("title", ""),
                "link": r.get("link", ""),
                "snippet": r.get("snippet", ""),
                "source_mode": r.get("source_mode", ""),
            }
            for r in chunk
        ]}
        
        # Parse with light LLM
        candidates = parse_web_results_with_llm(
            idea_description=idea_description,
            web_results=combined_for_llm,
            max_papers=15,
        )
        all_candidates.extend(candidates)
    
    # 4. Convert to seed format
    seeds = serpapi_candidates_to_seeds(all_candidates)
    
    logger.info(f"[WebSearch] Discovered {len(seeds)} seed papers from web search")
    for i, seed in enumerate(seeds[:5], 1):
        title = seed.get("title", "Unknown")[:50]
        logger.info(f"  [{i}] {title}...")
    if len(seeds) > 5:
        logger.info(f"  ... and {len(seeds) - 5} more")
    
    return seeds


def parse_web_results_with_llm(
    idea_description: str,
    web_results: Dict[str, List[Dict[str, str]]],
    max_papers: int = 15,
) -> List[Dict[str, Any]]:
    """
    Use light LLM to extract paper candidates from unified web search results.
    
    Args:
        idea_description: The research idea
        web_results: Dict with "web_results" key containing list of results
        max_papers: Max papers to extract
        
    Returns:
        List of paper dicts with title, year, venue, url, source_mode, reason
    """
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    results_str = json.dumps(web_results, ensure_ascii=False, indent=2)
    
    prompt = f"""You are a helpful research assistant.

You are given:
- A research idea: {idea_description}

You also have web search results in JSON:
```json
{results_str}
```

Your task:
- From these results, identify up to {max_papers} entries that are MOST LIKELY to be academic papers relevant to the idea.
- Prefer results that clearly look like academic papers (conference proceedings, journal articles, arXiv papers).
- For each selected result, output:
  - title: string (the paper title, cleaned up)
  - year: integer if you can guess from the snippet/title/URL, else null
  - venue: short string (e.g., conference name, journal name, "arXiv"), or null if unknown
  - url: the link
  - source_mode: the source_mode from the input (e.g., serpapi_all_years, ddg_arxiv)
  - reason: 1-2 sentences why this seems relevant to the idea.

Output format:
Return ONLY a JSON array of objects with the above fields. No extra commentary.

JSON:
"""
    
    messages = [{"role": "user", "content": prompt}]
    resp = light_model(messages)
    text = extract_text_from_llm_response(resp)
    
    try:
        data = json.loads(text)
    except Exception:
        try:
            repaired = repair_json_output(text)
            data = json.loads(repaired)
        except Exception:
            logger.warning("[WebSearch] Failed to parse paper extraction JSON")
            return []
    
    if not isinstance(data, list):
        if isinstance(data, dict) and "papers" in data:
            data = data["papers"]
        else:
            return []
    
    return data


# Keep old function name as alias for backwards compatibility
seed_papers_from_serpapi = seed_papers_from_web_search

def generate_search_queries_from_idea(
    agent,
    idea_description: str,
    num_queries: int = 6,
) -> List[str]:
    """
    Ask the LLM to synthesize a small set of diverse search queries
    that can be used in scholarly APIs (OpenAlex, Semantic Scholar).

    NOTE: This function does NOT use topic - queries are derived entirely
    from the idea_description to avoid domain bias.

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

    prompt = f"""You are an expert researcher.

Your task is to design bibliographic search queries for academic
paper databases (e.g., OpenAlex, Semantic Scholar) that are relevant
to the following research idea:

\"\"\"{idea_description}\"\"\"

Requirements:
- Each query should be a short keyword-style query (not a full sentence).
- Combine key technical terms from the idea, including:
  - problem/task,
  - key methods or mechanisms,
  - important properties or techniques.
- Do NOT add year or venue constraints in the query.
  (The system will handle year filters separately.)
- Do NOT add site filters (e.g., "site:arxiv.org").
- Queries should be reasonably distinct to cover different angles.

CRITICAL RULES for query formatting:
- Each query should use at MOST ONE exact phrase in quotes
- Remaining keywords should be unquoted, separated by spaces
- Avoid multiple quoted phrases in the same query (this kills recall)
- Mix broad queries (core concept) with more specific queries (concept + 1-2 keywords)
- Keep queries simple and focused

Return ONLY raw JSON list of strings:
["query 1", "query 2", ...]

Constraints:
- At most {num_queries} queries.
- Do NOT include any commentary outside the JSON.

JSON:
"""
    messages = [{"role": "user", "content": prompt}]
    resp = expert_model(messages)
    text = extract_text_from_llm_response(resp)

    try:
        data = json.loads(text)
    except Exception:
        logger.warning("[PriorArt] Failed to parse search queries JSON, returning empty list.")
        return []

    if not isinstance(data, list):
        return []

    queries = [str(q).strip() for q in data if str(q).strip()]
    if not queries:
        return []

    logger.info(f"[PriorArt] Generated {len(queries)} search queries from idea.")
    
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
            
            main_papers = _parse_openalex_results(main_results, bucket="main", query_id=f"q{q_idx}", cfg=cfg)
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
        
        # Recent bucket search (year in [recent_year_start, recent_year_end])
        # Use configurable year range from config
        recent_start = max(year_start, cfg.recent_year_start)
        recent_end = min(year_end, cfg.recent_year_end)
        if recent_start <= recent_end:
            try:
                recent_results = openalex.forward(
                    query=query,
                    from_year=recent_start,
                    to_year=recent_end,
                    limit=k_recent
                )
                
                # Debug: log raw output (first 500 chars)
                if cfg.debug_mode:
                    logger.debug(f"[Block1] Query '{query}' recent raw: {recent_results[:500]}")
                
                recent_papers = _parse_openalex_results(recent_results, bucket="recent", query_id=f"q{q_idx}", cfg=cfg)
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
    query_id: str = "",
    cfg: Optional[PriorArtConfig] = None,
) -> List[Paper]:
    """
    Parse OpenAlex tool output into Paper objects.
    
    The tool returns JSON (list of dicts), not markdown.
    Each dict has: title, year, venue, authors, cited_by_count, openalex_id, doi
    
    Args:
        results_str: Raw JSON string from OpenAlex
        bucket: 'main' or 'recent'
        query_id: Query identifier for logging
        cfg: Optional config for excluded paper filtering
    """
    papers = []
    excluded_count = 0
    
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
        
        # Excluded paper check (blind evaluation)
        if cfg and is_excluded_paper(paper, cfg):
            excluded_count += 1
            continue
        
        papers.append(paper)
    
    if excluded_count > 0:
        logger.info(f"[Block1] Excluded {excluded_count} paper(s) from OpenAlex results (blind evaluation)")
    
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
    
    # Helper to check if a paper is "recent" (within the recent year range)
    def is_recent(p: Paper) -> bool:
        return bool(p.year and cfg.recent_year_start <= p.year <= cfg.recent_year_end)
    
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

def _score_single_neighbor(
    paper_dict: Dict[str, Any],
    idea_keywords: List[str],
    strong_cues: List[str],
    enable_survey_penalty: bool = False,
) -> float:
    """
    Compute lexical relevance score for a single neighbor paper.
    
    Reuses simple_relevance_score from Block 2a for consistency.
    
    Args:
        paper_dict: Semantic Scholar paper dict (with title, abstract, etc.)
        idea_keywords: Keywords generated from idea (e.g., ["monocular 3d", "point cloud"])
        strong_cues: High-weight cues (e.g., ["rotation augmentation"])
        enable_survey_penalty: Whether to penalize survey/review papers
        
    Returns:
        Relevance score (>=0.0, higher is more relevant)
    """
    from pipelines.tasks.prior_art_scoring import simple_relevance_score
    
    title = (paper_dict.get("title") or "").lower()
    abstract = (paper_dict.get("abstract") or "").lower()
    
    return simple_relevance_score(
        paper_title=title,
        paper_abstract=abstract,
        keywords=idea_keywords,
        strong_cues=strong_cues,
        enable_survey_penalty=enable_survey_penalty,
    )


def lexical_filter_neighbors(
    neighbors: List[Dict[str, Any]],
    cfg: PriorArtConfig,
    idea_keywords: List[str],
    strong_cues: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Apply lightweight lexical filtering to citation neighbors.
    
    This function:
    1. Scores each neighbor using simple_relevance_score (parallel)
    2. Drops neighbors with score=0 (no keyword match at all)
    3. Sorts by (score, citation_count) descending
    4. Returns top-K neighbors (cfg.topk_neighbors_after_lexical)
    
    This ensures only somewhat-relevant papers from citation graph
    are added to the candidate pool, avoiding noise from weak citations.
    
    Args:
        neighbors: List of S2 paper dicts (from get_references/get_citations)
        cfg: PriorArtConfig with topk_neighbors_after_lexical
        idea_keywords: Keywords for relevance scoring
        strong_cues: Optional high-weight cues
        
    Returns:
        Filtered and sorted list of top-K neighbor dicts
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    if not neighbors:
        return []
    
    cues = strong_cues if strong_cues else []
    enable_penalty = cfg.enable_survey_penalty
    
    # 1. Parallel scoring (neighbors typically <= 100, CPU-bound, fast)
    scored: List[Tuple[float, Dict[str, Any]]] = []
    
    # Use 4-8 workers; neighbors are small so overhead is minimal
    max_workers = min(8, max(1, len(neighbors) // 5))
    
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_to_paper = {
            executor.submit(
                _score_single_neighbor,
                paper,
                idea_keywords,
                cues,
                enable_penalty,
            ): paper
            for paper in neighbors
        }
        
        for future in as_completed(future_to_paper):
            paper = future_to_paper[future]
            try:
                score = future.result()
            except Exception:
                # Defensive: on error, treat as 0 score (will be dropped)
                score = 0.0
            scored.append((score, paper))
    
    # 2. Drop papers with score=0 (no keyword match at all)
    scored = [(s, p) for s, p in scored if s > 0.0]
    
    if not scored:
        logger.info("[CitationGraph] Lexical filter: all neighbors scored 0, none kept.")
        return []
    
    # 3. Sort by (score, citation_count) descending
    scored.sort(
        key=lambda x: (
            x[0],                                    # lexical score
            x[1].get("citationCount") or 0,          # tiebreaker: citation count
        ),
        reverse=True,
    )
    
    # 4. Keep only top-K
    k = min(cfg.topk_neighbors_after_lexical, len(scored))
    kept = [p for _, p in scored[:k]]
    
    logger.info(
        f"[CitationGraph] Lexical filter: {len(neighbors)} neighbors -> "
        f"{len(scored)} with score>0 -> kept top {len(kept)}"
    )
    
    return kept

def expand_corpus_via_citation_graph(
    state: PipelineState,
    matches: List[PriorArtMatch],
    cfg: PriorArtConfig,
    idea_keywords: List[str],
    strong_cues: Optional[List[str]] = None,
    max_seed_papers: int = 5,
    max_workers: int = 4,
) -> None:
    """
    Expand the candidate corpus by traversing the citation graph.

    For the top `max_seed_papers` matches with coverage_level >= 2,
    use Semantic Scholar to:
    - fetch their references (earlier work),
    - fetch their citations (later work).
    
    NEW: Applies lexical filtering to neighbors using idea_keywords,
    keeping only top-K relevant papers per seed (cfg.topk_neighbors_after_lexical).

    This ensures:
    - Citation-expanded papers are at least somewhat relevant
    - Controlled growth of candidate pool
    - Avoids noise from weak/tangential citations
    
    Args:
        state: Pipeline state with papers dict
        matches: Scored matches from Block 2
        cfg: PriorArtConfig with max_neighbors_per_seed and topk_neighbors_after_lexical
        idea_keywords: Keywords for lexical filtering (reuse from Block 2a)
        strong_cues: Optional high-weight keyword cues
        max_seed_papers: How many top seeds to expand from
        max_workers: Parallel threads for S2 API calls
    """
    from pipelines.tasks.prior_art_utils import run_parallel
    
    logger.info(f"[CitationGraph] Expanding corpus via citation graph...")
    logger.info(f"[CitationGraph] Config: max_neighbors_per_seed={cfg.max_neighbors_per_seed}, "
                f"topk_neighbors_after_lexical={cfg.topk_neighbors_after_lexical}")

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
    
    # Pre-compute per-side limit
    per_side_limit = cfg.max_neighbors_per_seed // 2

    def _expand_single_seed(match: PriorArtMatch) -> int:
        """
        Expand citations/references for a single seed paper.
        
        Steps:
        1. Fetch references + citations from S2 (up to max_neighbors_per_seed)
        2. Apply lexical filter to keep only top-K relevant neighbors
        3. Add filtered neighbors to state.papers
        
        Returns: count of new papers added
        """
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

        # Fetch references (older papers) - up to per_side_limit
        references = local_s2_client.get_references(
            s2_id,
            fields="paperId,title,year,abstract,venue,citationCount,externalIds",
            limit=per_side_limit
        )
        
        # Fetch citations (newer papers) - up to per_side_limit
        citations = local_s2_client.get_citations(
            s2_id,
            fields="paperId,title,year,abstract,venue,citationCount,externalIds",
            limit=per_side_limit
        )
        
        # Combine all neighbors
        all_neighbors = references + citations
        logger.info(f"[CitationGraph] Seed '{paper.title[:40]}...': "
                    f"fetched {len(references)} refs + {len(citations)} cites = {len(all_neighbors)} total")
        
        if not all_neighbors:
            return 0
        
        # Apply lexical filter to keep only relevant neighbors
        filtered_neighbors = lexical_filter_neighbors(
            neighbors=all_neighbors,
            cfg=cfg,
            idea_keywords=idea_keywords,
            strong_cues=strong_cues,
        )

        # Thread-safe write to state.papers
        with state_lock:
            for neighbor_dict in filtered_neighbors:
                if _add_s2_paper_to_state(neighbor_dict, state, cfg=cfg):
                    count += 1
        
        return count

    # Use parallel executor with per-request rate limiting at client layer
    results = run_parallel(
        items=seeds,
        process_func=_expand_single_seed,
        max_workers=max_workers,
        desc="CitationGraph",
    )
    
    new_paper_count = sum(r for r in results if isinstance(r, int))
    logger.info(f"[CitationGraph] Added {new_paper_count} new papers from citation graph.")
def _add_s2_paper_to_state(
    s2_paper: Dict[str, Any], 
    state: PipelineState,
    cfg: Optional[PriorArtConfig] = None,
) -> bool:
    """
    Convert a Semantic Scholar paper dict to our Paper model and add to state.
    
    Args:
        s2_paper: Paper dict from Semantic Scholar API
        state: PipelineState to add paper to
        cfg: Optional config for excluded paper filtering
    
    Returns True if a new paper was added, False if it already existed or is excluded.
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
    
    # Excluded paper check (blind evaluation)
    if cfg and is_excluded_paper(paper, cfg):
        logger.debug(f"[ExcludedPaper] Skipping excluded paper from citation graph: {paper.title[:40]}...")
        return False

    state.papers[canonical_key] = paper
    return True
