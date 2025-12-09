"""
Prior Art Check - Utility Functions

This module provides shared utility functions for the prior_art pipeline:
- PDF URL resolution and caching
- Simple parallel execution (no batch + sleep)

IMPORTANT: All throttling is now handled at the client layer via rate limiters.
Do NOT add batch-level sleeps here. Each task calls the appropriate rate limiter
(openalex_rate_limiter, s2_rate_limiter, etc.) before making API requests.
"""

from __future__ import annotations

import json
from typing import Optional, List, TypeVar, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from pipelines.state import Paper
from tools.paper_resolver import PaperResolverTool


T = TypeVar('T')


def ensure_pdf_url(paper: Paper) -> Optional[str]:
    """
    Unified PDF URL resolution with caching and failure tracking.
    
    This function provides a single entry point for resolving PDF URLs,
    with the following behavior:
    
    1. If paper.pdf_url already exists, return it immediately (cached)
    2. If paper.pdf_resolve_failed is True, return None (already tried and failed)
    3. Otherwise, attempt resolution via PaperResolverTool
       - On success: set paper.pdf_url and return it
       - On failure: set paper.pdf_resolve_failed = True and return None
    
    NOTE: Rate limiting is handled internally by PaperResolverTool via
    s2_rate_limiter and openalex_rate_limiter.
    
    Args:
        paper: Paper object to resolve PDF URL for
        
    Returns:
        PDF URL string if available, None if resolution failed or not possible
    """
    # Check cache: already resolved
    if paper.pdf_url:
        return paper.pdf_url
    
    # Check failure flag: don't retry if already failed
    if getattr(paper, 'pdf_resolve_failed', False):
        logger.debug(f"[ensure_pdf_url] Skipping {paper.title[:40]}... (previously failed)")
        return None
    
    # Attempt resolution (rate limiting is inside PaperResolverTool)
    resolver = PaperResolverTool()
    try:
        result_str = resolver.forward(
            doi=paper.doi,
            arxiv_id=paper.arxiv_id,
            title=paper.title if not paper.doi and not paper.arxiv_id else None,
        )
        res_data = json.loads(result_str)
        
        if res_data.get("pdf_url"):
            paper.pdf_url = res_data["pdf_url"]
            logger.info(f"[ensure_pdf_url] Resolved: {paper.title[:40]}... -> {paper.pdf_url[:60]}...")
            return paper.pdf_url
        else:
            logger.debug(f"[ensure_pdf_url] No URL found for {paper.title[:40]}...")
            paper.pdf_resolve_failed = True
            return None
            
    except Exception as e:
        logger.warning(f"[ensure_pdf_url] Failed to resolve PDF for {paper.title[:40]}...: {e}")
        paper.pdf_resolve_failed = True
        return None


def run_parallel(
    items: List[T],
    process_func: Callable[[T], Any],
    max_workers: int = 4,
    desc: str = "Processing",
) -> List[Any]:
    """
    Execute a function on items in parallel using a single ThreadPoolExecutor.
    
    IMPORTANT: This function does NOT do batch-level sleeps. All rate limiting
    must be handled INSIDE process_func via the appropriate rate limiter
    (openalex_rate_limiter, s2_rate_limiter, llm_rate_limiter, pdf_rate_limiter).
    
    Pattern:
        with ThreadPoolExecutor(max_workers=N) as ex:
            for item in items:
                ex.submit(process_func, item)
    
    Each task in process_func should call the appropriate rate limiter
    BEFORE making the actual API request.
    
    Args:
        items: List of items to process
        process_func: Function to call on each item. This function MUST call
                     the appropriate rate_limiter.wait_for_slot() before 
                     making external API calls.
        max_workers: Maximum concurrent threads
        desc: Description for logging
        
    Returns:
        List of non-None results from process_func (in arbitrary order)
    """
    if not items:
        return []
    
    results = []
    total_items = len(items)
    
    logger.info(f"[{desc}] Processing {total_items} items with max_workers={max_workers}")
    
    # Use a SINGLE ThreadPoolExecutor - no batch grouping, no batch-level sleeps
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {executor.submit(process_func, item): item for item in items}
        
        completed = 0
        for fut in as_completed(future_to_item):
            item = future_to_item[fut]
            completed += 1
            try:
                result = fut.result()
                if result is not None:
                    results.append(result)
            except Exception as e:
                logger.warning(f"[{desc}] Error processing item: {e}")
            
            # Log progress every 10 items or at the end
            if completed % 10 == 0 or completed == total_items:
                logger.info(f"[{desc}] Progress: {completed}/{total_items} complete, {len(results)} results")
    
    logger.info(f"[{desc}] All tasks complete. Total results: {len(results)}")
    return results


# DEPRECATED: The old batch+delay pattern is no longer used.
# Keeping this alias for backward compatibility during transition.
def run_with_batch_throttling(
    items: List[T],
    process_func: Callable[[T], Any],
    max_workers: int = 4,
    batch_size: int = 4,  # Ignored
    batch_delay: float = 1.0,  # Ignored
    desc: str = "Processing",
) -> List[Any]:
    """
    DEPRECATED: Use run_parallel() instead.
    
    This function now delegates to run_parallel() and ignores batch_size/batch_delay.
    Rate limiting is now handled at the client layer via SimpleRateLimiter.
    """
    logger.warning(
        f"[{desc}] run_with_batch_throttling is deprecated. "
        "Use run_parallel() and ensure process_func calls the appropriate rate limiter."
    )
    return run_parallel(items, process_func, max_workers=max_workers, desc=desc)
