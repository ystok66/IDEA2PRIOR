"""
Prior Art Check - Utility Functions

This module provides shared utility functions for the prior_art pipeline:
- PDF URL resolution and caching
- Simple parallel execution (no batch + sleep)
- Safe filename generation from paper IDs

IMPORTANT: All throttling is now handled at the client layer via rate limiters.
Do NOT add batch-level sleeps here. Each task calls the appropriate rate limiter
(openalex_rate_limiter, s2_rate_limiter, etc.) before making API requests.
"""

from __future__ import annotations

import json
import re
from typing import Optional, List, TypeVar, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from pipelines.state import Paper
from tools.paper_resolver import PaperResolverTool

# Import for type hint
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pipelines.tasks.prior_art_types import PriorArtConfig


T = TypeVar('T')

# Regex for characters that are NOT safe in filenames
UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


# =============================================================================
# Excluded Paper Utilities (Blind Evaluation)
# =============================================================================

def normalize_title_for_matching(title: str) -> str:
    """
    Normalize a paper title for fuzzy matching.
    
    Handles:
    - Unicode variations (é -> e)
    - Punctuation and special characters
    - Multiple spaces and case variations
    - Common patterns like "3D-Rot" vs "3DRot"
    
    Args:
        title: Raw paper title
        
    Returns:
        Normalized lowercase title with only alphanumeric chars and spaces
        
    Examples:
        - "3D-Rot: A Method" -> "3d rot a method"
        - "Café Neural Net" -> "cafe neural net"
    """
    import unicodedata
    
    if not title:
        return ""
    
    # 1. Unicode normalize (decompose accents, etc.)
    title = unicodedata.normalize("NFKD", title)
    
    # 2. Encode to ASCII (drops remaining accents)
    title = title.encode("ascii", "ignore").decode("ascii")
    
    # 3. Lowercase
    title = title.lower()
    
    # 4. Replace non-alphanumeric with space
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    
    # 5. Collapse multiple spaces
    title = re.sub(r"\s+", " ", title).strip()
    
    return title


def is_excluded_paper(paper, config: "PriorArtConfig") -> bool:
    """
    Check if a paper matches any of the excluded papers in config.
    
    Uses multiple matching strategies:
    1. Exact ID matching (DOI, arXiv ID, S2 ID, OpenAlex ID)
    2. Normalized title matching
    
    Args:
        paper: Paper object (or dict with paper fields)
        config: PriorArtConfig with excluded_papers list
        
    Returns:
        True if paper should be excluded, False otherwise
    """
    if not config.excluded_papers:
        return False
    
    # Get paper identifiers (support both Paper objects and dicts)
    def get_field(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
    
    paper_doi = get_field(paper, "doi")
    paper_arxiv = get_field(paper, "arxiv_id")
    paper_s2 = get_field(paper, "s2_id")
    paper_oalex = get_field(paper, "openalex_id")
    paper_title = get_field(paper, "title", "")
    paper_title_norm = normalize_title_for_matching(paper_title)
    
    for ep in config.excluded_papers:
        # 1. ID matching (most reliable, case-insensitive)
        for key, paper_val in [
            ("doi", paper_doi),
            ("arxiv_id", paper_arxiv),
            ("s2_id", paper_s2),
            ("openalex_id", paper_oalex),
        ]:
            ep_val = ep.get(key)
            if paper_val and ep_val:
                if paper_val.strip().lower() == ep_val.strip().lower():
                    logger.debug(f"[ExcludedPaper] Match by {key}: {paper_title[:40]}...")
                    return True
        
        # 2. Normalized title matching
        ep_title = ep.get("title", "")
        ep_title_norm = normalize_title_for_matching(ep_title)
        
        if paper_title_norm and ep_title_norm:
            if paper_title_norm == ep_title_norm:
                logger.debug(f"[ExcludedPaper] Match by title: {paper_title[:40]}...")
                return True
            
            # 3. Substring match for long titles (handle subtitles)
            # Only if both are reasonably long to avoid false positives
            if len(ep_title_norm) > 20 and len(paper_title_norm) > 20:
                if ep_title_norm in paper_title_norm or paper_title_norm in ep_title_norm:
                    logger.debug(f"[ExcludedPaper] Substring match: {paper_title[:40]}...")
                    return True
    
    return False


def filter_excluded_papers(papers, config: "PriorArtConfig", desc: str = "papers") -> list:
    """
    Filter out excluded papers from a list.
    
    Args:
        papers: List of Paper objects or dicts
        config: PriorArtConfig with excluded_papers list
        desc: Description for logging
        
    Returns:
        Filtered list with excluded papers removed
    """
    if not config.excluded_papers:
        return papers
    
    original_count = len(papers)
    filtered = [p for p in papers if not is_excluded_paper(p, config)]
    excluded_count = original_count - len(filtered)
    
    if excluded_count > 0:
        logger.info(f"[ExcludedPaper] Filtered {excluded_count} excluded paper(s) from {desc}")
    
    return filtered


def paper_storage_key(paper: Paper) -> str:
    """
    Generate a safe storage key (filename) from a paper's identifiers.
    
    This handles DOIs like '10.48550/arxiv.2508.01423' which contain slashes
    that would create unwanted directory structures. All unsafe characters
    are replaced with underscores.
    
    Priority order: arxiv_id > doi > s2_id > paper_id
    
    Args:
        paper: Paper object with identifier fields
        
    Returns:
        Safe string suitable for use as a filename without extension
        
    Examples:
        - arxiv_id='2508.01423' -> '2508.01423'
        - doi='10.48550/arxiv.2508.01423' -> '10.48550_arxiv.2508.01423'
        - paper_id='some/weird/id' -> 'some_weird_id'
    """
    # Use the most stable/canonical ID available
    base = (
        paper.arxiv_id or 
        paper.doi or 
        paper.s2_id or 
        getattr(paper, 'paper_id', None) or
        getattr(paper, 'id', None) or
        paper.title[:50]  # Last resort: truncated title
    )
    
    # Replace unsafe characters with underscore
    safe_key = UNSAFE_FILENAME_CHARS.sub("_", base)
    
    # Remove leading/trailing underscores
    safe_key = safe_key.strip("_")
    
    return safe_key


def ensure_pdf_url(paper: Paper) -> Optional[str]:
    """
    Unified PDF URL resolution with caching and failure tracking.
    
    This function provides a single entry point for resolving PDF URLs,
    with the following behavior:
    
    1. If paper.pdf_url already exists, return it immediately (cached)
    2. If paper.pdf_resolve_failed is True, return None (already tried and failed)
    3. Otherwise, attempt resolution via PaperResolverTool
       - On success: set paper.pdf_url and pdf_status='downloaded', return it
       - On failure: set paper.pdf_resolve_failed = True, pdf_status='not_found' or 'error'
    
    NOTE: Rate limiting is handled internally by PaperResolverTool via
    s2_rate_limiter and openalex_rate_limiter.
    
    Args:
        paper: Paper object to resolve PDF URL for
        
    Returns:
        PDF URL string if available, None if resolution failed or not possible
    """
    # Check cache: already resolved
    if paper.pdf_url:
        # Ensure pdf_status is consistent
        if hasattr(paper, 'pdf_status') and paper.pdf_status == "not_attempted":
            paper.pdf_status = "downloaded"
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
            openalex_id=paper.openalex_id,
            title=paper.title if not paper.doi and not paper.arxiv_id else None,
        )
        res_data = json.loads(result_str)
        
        if res_data.get("pdf_url"):
            paper.pdf_url = res_data["pdf_url"]
            if hasattr(paper, 'pdf_status'):
                paper.pdf_status = "downloaded"
            logger.info(f"[ensure_pdf_url] Resolved: {paper.title[:40]}... -> {paper.pdf_url[:60]}...")
            return paper.pdf_url
        else:
            logger.debug(f"[ensure_pdf_url] No URL found for {paper.title[:40]}...")
            paper.pdf_resolve_failed = True
            if hasattr(paper, 'pdf_status'):
                paper.pdf_status = "not_found"
            return None
            
    except Exception as e:
        logger.warning(f"[ensure_pdf_url] Failed to resolve PDF for {paper.title[:40]}...: {e}")
        paper.pdf_resolve_failed = True
        if hasattr(paper, 'pdf_status'):
            paper.pdf_status = "error"
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
