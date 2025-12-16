# pipelines/exploration_loop.py

"""
Multi-round exploration loop for discovering and enriching papers.

This module implements the core discovery logic that:
1. Discovers papers via OpenAlex
2. Enriches metadata via Semantic Scholar
3. Optionally parses key PDFs for deeper analysis
"""

from __future__ import annotations

import os
import json
import time
import shutil
from typing import List, Optional
from pathlib import Path
from datetime import datetime
import requests

from loguru import logger

from pipelines.state import PipelineState, Paper, CitationEdge, Intent, Chunk
from tools.openalex_search import OpenAlexWorksSearchTool
from tools.semantic_scholar_search import SemanticScholarPaperDetailTool, SemanticScholarCitationTool
from tools.paper_resolver import PaperResolverTool
from tools.pdf_parser import PdfParserTool
from tools.pdf_extractor import PDFExtractorTool
from tools.img_extractor import ImageExtractorTool


def save_pipeline_state_to_logs(state: PipelineState) -> None:
    """
    Save the entire PipelineState to ./logs directory with timestamp.
    
    Args:
        state: PipelineState to save
    """
    try:
        # Create logs directory if it doesn't exist
        logs_dir = Path("./logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Create filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in state.intent.topic)
        safe_topic = safe_topic.replace(' ', '_')[:50]
        filename = f"pipeline_state_{safe_topic}_{timestamp}.json"
        file_path = logs_dir / filename
        
        # Serialize PipelineState to JSON
        # Use model_dump with mode='json' to ensure proper serialization
        state_dict = state.model_dump(mode='json')
        
        # Write to file with pretty formatting
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=2, ensure_ascii=False)
        
        logger.info(f"💾 PipelineState saved to: {file_path}")
        
    except Exception as e:
        logger.error(f"❌ Failed to save PipelineState to logs: {e}")
        import traceback
        logger.debug(f"Traceback: {traceback.format_exc()}")


def run_exploration_loop(
    agent,
    state: PipelineState,
    max_rounds: int = 2,
    max_papers: int = 80,
    min_new_per_round: int = 5,
    search_queries: Optional[List[str]] = None,
) -> PipelineState:
    """
    Run the multi-round exploration loop.

    Parameters
    ----------
    agent:
        The CodeAgent instance, already configured with OpenAlex / S2 tools.
    state:
        PipelineState containing the parsed Intent. This object will be
        updated in-place and also returned.
    max_rounds:
        Maximum number of exploration rounds.
    max_papers:
        Global upper bound on the number of papers in the state.
    min_new_per_round:
        If a round discovers fewer than this many new papers, the loop stops early.

    Returns
    -------
    PipelineState
        The final state after exploration completes.
    """
    logger.info(
        f"Starting exploration loop for topic='{state.intent.topic}', "
        f"task_type='{state.intent.task_type}'"
    )

    for round_idx in range(max_rounds):
        logger.info(f"=== Exploration round {round_idx + 1}/{max_rounds} ===")
        state.discovery_round = round_idx + 1

        # 1) Discover new papers for this round, respecting max_papers limit.
        # Calculate how many papers we still need
        current_count = len(state.papers)
        papers_needed = max_papers - current_count
        
        if papers_needed <= 0:
            logger.info(
                f"Stopping exploration: already reached max_papers={max_papers} "
                f"(current={current_count})."
            )
            break
        
        newly_added = discover_papers_round(agent, state, search_queries, max_new_papers=papers_needed)

        logger.info(f"Round {round_idx + 1}: discovered {len(newly_added)} new papers.")
        logger.info(f"Total papers in state: {len(state.papers)}")

        # Only stop if we actually tried to get papers but got very few.
        # If we got fewer than min_new_per_round but we haven't reached max_papers yet,
        # it might be because papers were filtered out (missing IDs), so we should continue
        # to try getting more papers in the next round.
        if len(newly_added) < min_new_per_round:
            # Calculate how many papers we still need
            remaining_needed = max_papers - len(state.papers)
            
            # If we still need significantly more papers, continue trying
            # (papers may have been filtered due to missing IDs)
            if remaining_needed >= min_new_per_round:
                logger.info(
                    f"Got {len(newly_added)} papers (below min_new_per_round={min_new_per_round}), "
                    f"but still need {remaining_needed} more. Continuing exploration (papers may have been filtered)."
                )
                # Continue to next iteration to try getting more papers
            else:
                logger.info(
                    f"Stopping exploration: only {len(newly_added)} new papers "
                    f"(min_new_per_round={min_new_per_round}), and only need {remaining_needed} more."
                )
                break

        if len(state.papers) >= max_papers:
            logger.info(
                f"Stopping exploration: reached max_papers={max_papers} "
                f"(current={len(state.papers)})."
            )
            break

        # 2) Enrich metadata and citation graph for a subset of key papers.
        enrich_metadata_and_citations(agent, state)

        # 3) Optionally parse a small number of key PDFs for deeper signals.
        maybe_parse_key_papers(agent, state, round_idx=round_idx)

    logger.info(
        f"Exploration loop finished with {len(state.papers)} papers "
        f"after {state.discovery_round} rounds."
    )
    
    # Save PipelineState to logs directory
    save_pipeline_state_to_logs(state)
    
    return state


def discover_papers_round(
    agent, 
    state: PipelineState, 
    search_queries: Optional[List[str]] = None,
    max_new_papers: Optional[int] = None
) -> List[Paper]:
    """
    Discover a batch of new papers for the current round.
    
    Args:
        agent: CodeAgent instance
        state: PipelineState with current papers
        search_queries: Optional list of search queries
        max_new_papers: Maximum number of new papers to discover (None = no limit)
    """
    logger.info("Discovering papers via OpenAlex...")
    
    # 1. Construct query from Intent
    topic = state.intent.topic
    years = state.intent.years
    venues = state.intent.venues
    
    from_year = years[0] if years else None
    to_year = years[1] if years else None
    # OpenAlex uses | for OR in filters
    venue_str = "|".join(venues) if venues else None
    
    # 2. Call OpenAlex tool
    oa_tool = OpenAlexWorksSearchTool()
    
    queries_to_run = search_queries if search_queries else [topic]
    all_works = []
    
    # Calculate query limit based on max_new_papers if specified
    # Request more papers than needed to account for filtering (papers without IDs get skipped)
    # Use a multiplier to ensure we get enough valid papers after filtering
    if max_new_papers:
        # Request 1.5x more papers to account for filtering (papers without canonical keys)
        query_limit = int(max_new_papers * 1.5)
        # But cap at reasonable limit to avoid API issues
        query_limit = min(query_limit, 50)
    else:
        query_limit = 20
    
    for q in queries_to_run:
        # Stop if we've already collected enough papers
        if max_new_papers and len(all_works) >= max_new_papers:
            break
            
        logger.info(f"Searching OpenAlex with query: '{q}'")
        try:
            # Adjust limit based on how many we still need
            current_limit = query_limit
            if max_new_papers:
                current_limit = min(query_limit, max_new_papers - len(all_works))
            
            result_json = oa_tool.forward(
                query=q,
                from_year=from_year,
                to_year=to_year,
                venue=venue_str,
                limit=current_limit
            )
            
            if result_json.startswith("Error"):
                logger.warning(f"OpenAlex tool returned error for query '{q}': {result_json}")
                continue
            
            works_batch = json.loads(result_json)
            logger.info(f"Found {len(works_batch)} papers for query: '{q}'")
            all_works.extend(works_batch)
            
            # Stop if we've reached the limit
            if max_new_papers and len(all_works) >= max_new_papers:
                break
            
        except Exception as e:
            logger.warning(f"Failed to search OpenAlex with query '{q}': {e}")
    
    # Safety net: if all queries returned 0 results, try a simple topic-only query
    if not all_works and search_queries and topic not in search_queries:
        logger.warning(f"[Fallback] All queries returned 0 results. Trying topic-only: '{topic}'")
        try:
            fallback_limit = max_new_papers if max_new_papers else 30
            result_json = oa_tool.forward(
                query=topic,
                from_year=from_year,
                to_year=to_year,
                venue=venue_str,
                limit=fallback_limit
            )
            if not result_json.startswith("Error"):
                works_batch = json.loads(result_json)
                logger.info(f"[Fallback] Found {len(works_batch)} papers with topic query")
                all_works.extend(works_batch)
        except Exception as e:
            logger.warning(f"[Fallback] Topic query also failed: {e}")
            
    # Deduplicate works by ID before processing
    # OpenAlex tool returns "openalex_id", but raw API returns "id". Handle both.
    unique_works = {}
    for w in all_works:
        wid = w.get("openalex_id") or w.get("id")
        if wid:
            unique_works[wid] = w
            
    works = list(unique_works.values())

    newly_added = []
    skipped_no_id = 0  # Track papers skipped due to missing IDs
    skipped_duplicate = 0  # Track papers skipped due to duplicates
    
    # 3. Parse and deduplicate, respecting max_new_papers limit
    for work in works:
        # Stop if we've reached the limit
        if max_new_papers and len(newly_added) >= max_new_papers:
            break
            
        # Extract fields
        title = work.get("title", "Unknown Title")
        year = work.get("year")
        venue = work.get("venue")
        openalex_id = work.get("openalex_id")
        doi = work.get("doi")
        # Clean DOI if it's a URL
        if doi and "doi.org/" in doi:
            doi = doi.split("doi.org/")[-1]
            
        # Determine canonical key (DOI > OpenAlex ID)
        canonical_key = doi if doi else openalex_id
        
        if not canonical_key:
            skipped_no_id += 1
            logger.debug(f"Skipping paper '{title[:50]}...' - no DOI or OpenAlex ID")
            continue
            
        # Check if already exists
        if canonical_key in state.papers:
            skipped_duplicate += 1
            continue
            
        # Create Paper object
        paper = Paper(
            title=title,
            year=year,
            venue=venue,
            openalex_id=openalex_id,
            doi=doi,
            citation_count=work.get("cited_by_count")
        )
        
        # Add to state
        state.papers[canonical_key] = paper
        newly_added.append(paper)
        
        # Stop if we've reached the limit
        if max_new_papers and len(newly_added) >= max_new_papers:
            break
    
    # Log statistics about skipped papers
    if skipped_no_id > 0 or skipped_duplicate > 0:
        logger.info(f"Discovery stats: {len(newly_added)} added, {skipped_no_id} skipped (no ID), {skipped_duplicate} skipped (duplicate)")
    
    return newly_added


def enrich_metadata_and_citations(agent, state: PipelineState) -> None:
    """
    Enrich metadata and build/extend the local citation graph.
    """
    import time
    
    logger.info("Enriching metadata via Semantic Scholar...")
    
    s2_detail_tool = SemanticScholarPaperDetailTool()
    s2_citation_tool = SemanticScholarCitationTool()
    
    # Limit enrichment to avoid rate limits (e.g. first 30 papers)
    papers_to_enrich = list(state.papers.values())[:30]
    
    for idx, paper in enumerate(papers_to_enrich):
        # Skip if already enriched (simple check: has fields_of_study)
        if paper.fields_of_study:
            continue
            
        # Determine ID to query - Semantic Scholar only accepts DOI, S2ID, or ArXiv ID
        # NOT OpenAlex IDs!
        paper_id = None
        if paper.doi:
            paper_id = f"DOI:{paper.doi}" if not paper.doi.startswith("DOI:") else paper.doi
        elif paper.arxiv_id:
            paper_id = f"ARXIV:{paper.arxiv_id}" if not paper.arxiv_id.startswith("ARXIV:") else paper.arxiv_id
        elif paper.s2_id:
            paper_id = paper.s2_id
        
        if not paper_id:
            logger.debug(f"Skipping {paper.title[:30]}... - no valid ID for Semantic Scholar (need DOI/ArXiv/S2ID)")
            continue
        
        # Rate limiting: 1 request per second (with API key)
        if idx > 0:
            time.sleep(1.1)  # Slightly more than 1 second to be safe
            
        try:
            res_json = s2_detail_tool.forward(paper_id=paper_id)
            if "Paper not found" in res_json or "Error querying" in res_json:
                logger.debug(f"Semantic Scholar lookup failed for {paper.title[:30]}...: {res_json[:100]}")
                continue
                
            details = json.loads(res_json)
            
            # Update metadata
            if details.get("citation_count"):
                paper.citation_count = details["citation_count"]
            if details.get("s2_id"):
                paper.s2_id = details["s2_id"]
            if details.get("pdf_url"):
                paper.pdf_url = details["pdf_url"]
            if details.get("abstract"):
                paper.abstract = details["abstract"]
            
            logger.debug(f"Successfully enriched: {paper.title[:30]}...")
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Semantic Scholar response for {paper.title[:30]}...: {e}")
        except Exception as e:
            logger.warning(f"Failed to enrich paper {paper.title[:30]}...: {e}")
            
    # Optional: Expand citation graph for top-k papers
    sorted_papers = sorted(
        [p for p in state.papers.values() if p.citation_count is not None],
        key=lambda p: p.citation_count,
        reverse=True
    )
    
    for idx, paper in enumerate(sorted_papers[:3]):
        # Use S2ID if available, otherwise skip (citation tool needs S2ID)
        if not paper.s2_id:
            logger.debug(f"Skipping citation fetch for {paper.title[:30]}... - no S2ID")
            continue
        
        # Rate limiting for citation calls
        if idx > 0:
            time.sleep(1.1)
        
        try:
            cit_json = s2_citation_tool.forward(
                paper_id=paper.s2_id,
                type="citations",
                limit=20
            )
            # TODO: parse and add citation edges to state
            logger.debug(f"Fetched citations for {paper.title[:30]}...")
        except Exception as e:
            logger.warning(f"Failed to fetch citations for {paper.title[:30]}...: {e}")


def detect_method_families_from_text(text: str) -> List[str]:
    """
    Heuristic-based detection of common method families from text.
    
    Parameters
    ----------
    text : str
        Combined text (title + abstract + introduction, etc.)
    
    Returns
    -------
    List[str]
        List of detected method family names
    """
    text_lower = text.lower()
    families = []
    
    # Neural architectures
    if "transformer" in text_lower or "attention" in text_lower:
        families.append("Transformer")
    if "cnn" in text_lower or "convolutional" in text_lower:
        families.append("CNN")
    if "lstm" in text_lower or "gru" in text_lower or "recurrent" in text_lower:
        families.append("RNN")
    if "gan" in text_lower or "generative adversarial" in text_lower:
        families.append("GAN")
    if "vae" in text_lower or "variational autoencoder" in text_lower:
        families.append("VAE")
    if "diffusion" in text_lower and ("model" in text_lower or "process" in text_lower):
        families.append("Diffusion")
    if "mamba" in text_lower or "state space" in text_lower:
        families.append("State-Space")
    
    # 3D-specific architectures
    if "pointnet" in text_lower:
        families.append("PointNet")
    if "voxel" in text_lower and ("net" in text_lower or "cnn" in text_lower):
        families.append("Voxel-based")
    if "sparse convolution" in text_lower or "minkowski" in text_lower:
        families.append("Sparse Convolution")
    
    # Detection methods
    if "yolo" in text_lower:
        families.append("YOLO")
    if "rcnn" in text_lower or "r-cnn" in text_lower or "faster r-cnn" in text_lower:
        families.append("R-CNN")
    if "detr" in text_lower or "detection transformer" in text_lower:
        families.append("DETR")
    
    # Optimization/Training techniques
    if "data augmentation" in text_lower or "augment" in text_lower:
        families.append("Data Augmentation")
    if "normalization" in text_lower or "batch norm" in text_lower:
        families.append("Normalization")
    if "dropout" in text_lower or "regularization" in text_lower:
        families.append("Regularization")
    if "ensemble" in text_lower:
        families.append("Ensemble")
    
    # Probabilistic methods
    if "bayesian" in text_lower:
        families.append("Bayesian")
    if "flow-based" in text_lower or "normalizing flow" in text_lower:
        families.append("Flow-based")
    if "autoregressive" in text_lower:
        families.append("Autoregressive")
    
    # Learning paradigms
    if "self-supervised" in text_lower or "self supervised" in text_lower:
        families.append("Self-supervised")
    if "contrastive learning" in text_lower or ("contrastive" in text_lower and "loss" in text_lower):
        families.append("Contrastive")
    if "distillation" in text_lower or "knowledge distillation" in text_lower:
        families.append("Distillation")
    if "multi-task" in text_lower or "multitask" in text_lower:
        families.append("Multi-task")
    if "multi-modal" in text_lower or "multimodal" in text_lower:
        families.append("Multi-modal")
    
    # Return unique families
    return list(set(families))


def maybe_parse_key_papers(agent, state: PipelineState, round_idx: int) -> None:
    """
    Optionally parse a few key PDFs to extract deeper signals.
    
    Now enabled from round 0 to ensure datasets/metrics/method families
    detection actually runs and provides useful information.
    """
    logger.debug(f"maybe_parse_key_papers called at round {round_idx}.")

    if not state.papers:
        logger.debug("No papers available for PDF parsing.")
        return

    # Select top-k papers by citation_count (fallback to 0)
    all_papers = list(state.papers.values())
    all_papers.sort(key=lambda p: p.citation_count or 0, reverse=True)
    key_papers = all_papers[:3]

    resolver = PaperResolverTool()
    parser = PdfParserTool()  # Keep for text chunking
    pdf_extractor = PDFExtractorTool()  # Use mineru for full parsing
    pdf_extractor.setup()
    
    # Setup image extractor
    img_extractor = ImageExtractorTool()
    img_extractor.setup()
    
    # Setup session for PDF downloads with better headers
    download_session = requests.Session()
    download_session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/pdf,application/octet-stream,*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    })
    
    # Mineru input/output directories
    mineru_input_dir = Path("/data/wdy/AgenticPaperQA/paper-prepare/sampled_papers")
    mineru_output_dir = Path("/data/wdy/AgenticPaperQA/paper-prepare/sampled_jsons")
    mineru_input_dir.mkdir(parents=True, exist_ok=True)
    mineru_output_dir.mkdir(parents=True, exist_ok=True)

    pdf_cache_dir = Path(os.path.join(os.getcwd(), "pdf_cache"))
    pdf_cache_dir.mkdir(parents=True, exist_ok=True)

    for paper in key_papers:
        logger.info(f"[PDF] Processing key paper: {paper.title[:80]}...")
        
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
            logger.warning(f"[PDF] No PDF URL found for paper: {paper.title[:80]}")
            continue

        # Download PDF to local cache with improved error handling
        local_filename = f"{paper.doi.replace('/', '_') if paper.doi else (paper.openalex_id.split('/')[-1] if paper.openalex_id else 'unknown')}.pdf"
        local_pdf_path = pdf_cache_dir / local_filename
        
        if not local_pdf_path.exists():
            # Collect potential PDF URLs in priority order
            pdf_urls = []
            
            # Priority 1: arXiv (most reliable)
            if paper.arxiv_id:
                arxiv_url = f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
                pdf_urls.append(arxiv_url)
            
            # Priority 2: Provided pdf_url (if not arXiv)
            if paper.pdf_url and 'arxiv.org' not in paper.pdf_url:
                pdf_urls.append(paper.pdf_url)
            elif paper.pdf_url:
                # If it's arXiv, we already added it above
                pass
            
            # Priority 3: DOI-based URL
            if paper.doi and not any('arxiv.org' in url for url in pdf_urls):
                pdf_urls.append(f"https://doi.org/{paper.doi}")
            
            if not pdf_urls:
                logger.warning(f"[PDF] No PDF URL available for paper: {paper.title[:80]}")
                continue
            
            # Try downloading from each URL
            download_success = False
            last_error = None
            
            for url in pdf_urls:
                try:
                    logger.info(f"[PDF] Downloading from: {url}")
                    
                    # Set appropriate referer based on URL domain
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    referer = f"{parsed.scheme}://{parsed.netloc}/"
                    
                    headers = {'Referer': referer}
                    
                    # Retry logic with exponential backoff
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            response = download_session.get(
                                url,
                                headers=headers,
                                timeout=30,
                                allow_redirects=True,
                                stream=True
                            )
                            
                            # Handle different status codes
                            if response.status_code == 403:
                                if attempt < max_retries - 1:
                                    logger.warning(f"[PDF] 403 Forbidden, retrying with different approach...")
                                    time.sleep(2 ** attempt)  # Exponential backoff
                                    continue
                                else:
                                    last_error = "403 Forbidden - PDF may require authentication"
                                    break
                            
                            elif response.status_code == 404:
                                last_error = "404 Not Found - PDF URL is invalid"
                                break
                            
                            elif response.status_code == 429:
                                wait_time = 2 ** (attempt + 1)
                                logger.warning(f"[PDF] Rate limited, waiting {wait_time}s...")
                                time.sleep(wait_time)
                                continue
                            
                            response.raise_for_status()
                            
                            # Verify it's actually a PDF
                            content_type = response.headers.get('Content-Type', '').lower()
                            is_pdf = 'pdf' in content_type or url.endswith('.pdf')
                            
                            # Save the file
                            local_pdf_path.parent.mkdir(parents=True, exist_ok=True)
                            with open(local_pdf_path, "wb") as f:
                                for chunk in response.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            
                            # Verify the saved file is a PDF
                            with open(local_pdf_path, "rb") as f:
                                first_bytes = f.read(4)
                                if first_bytes == b'%PDF':
                                    file_size = local_pdf_path.stat().st_size
                                    logger.info(f"[PDF] Successfully downloaded: {local_pdf_path} ({file_size:,} bytes)")
                                    download_success = True
                                    break
                                else:
                                    logger.warning(f"[PDF] Downloaded file doesn't appear to be a PDF")
                                    local_pdf_path.unlink()  # Delete invalid file
                                    last_error = "Downloaded file is not a valid PDF"
                            
                        except requests.exceptions.Timeout:
                            if attempt < max_retries - 1:
                                logger.warning(f"[PDF] Timeout on attempt {attempt + 1}, retrying...")
                                time.sleep(2 ** attempt)
                                continue
                            else:
                                last_error = "Request timeout"
                        
                        except requests.exceptions.RequestException as e:
                            if attempt < max_retries - 1:
                                logger.warning(f"[PDF] Request error on attempt {attempt + 1}: {e}, retrying...")
                                time.sleep(2 ** attempt)
                                continue
                            else:
                                last_error = f"Request failed: {str(e)}"
                    
                    if download_success:
                        break
                        
                except Exception as e:
                    last_error = f"Unexpected error: {str(e)}"
                    logger.debug(f"[PDF] Error downloading from {url}: {e}")
                    continue
            
            if not download_success:
                logger.warning(f"[PDF] Failed to download PDF for paper: {paper.title[:80]} - {last_error}")
                continue
        
        paper.local_pdf_path = str(local_pdf_path)

        # Step 1: Parse PDF with mineru (for image extraction and full content)
        pdf_name_for_mineru = None
        try:
            # Extract PDF name from DOI or OpenAlex ID
            if paper.doi:
                pdf_name_for_mineru = paper.doi.replace('/', '_').replace(':', '_')
            elif paper.openalex_id:
                # Extract ID from OpenAlex URL
                pdf_name_for_mineru = paper.openalex_id.split('/')[-1]
            else:
                # Fallback to filename without extension
                pdf_name_for_mineru = os.path.splitext(os.path.basename(local_pdf_path))[0]
            
            logger.info(f"[PDF] Copying PDF to mineru input directory: {pdf_name_for_mineru}")
            mineru_pdf_path = mineru_input_dir / f"{pdf_name_for_mineru}.pdf"
            
            # Copy PDF to mineru input directory
            shutil.copy2(local_pdf_path, mineru_pdf_path)
            logger.info(f"[PDF] Copied PDF to: {mineru_pdf_path}")
            
            # Parse with mineru (this generates content_list.json)
            # Only try if mineru is available
            try:
                from tools.pdf_utils import MINERU_AVAILABLE
                if MINERU_AVAILABLE:
                    logger.info(f"[PDF] Parsing PDF with mineru: {pdf_name_for_mineru}")
                    pdf_extractor_result = pdf_extractor.forward(pdf_name=pdf_name_for_mineru)
                    
                    if pdf_extractor_result.startswith("Error:"):
                        logger.warning(f"[PDF] Mineru parsing failed: {pdf_extractor_result}")
                    else:
                        logger.info(f"[PDF] Mineru parsing completed for {pdf_name_for_mineru}")
                        
                        # Step 2: Extract images using ImageExtractorTool
                        try:
                            task_name = state.intent.task_type  # e.g., "benchmark_landscape"
                            logger.info(f"[PDF] Extracting images for {pdf_name_for_mineru}...")
                            img_result = img_extractor.forward(
                                pdf_name=pdf_name_for_mineru,
                                task_name=task_name,
                                content_types="all"
                            )
                            if not img_result.startswith("Error:"):
                                logger.info(f"[PDF] Image extraction completed: {img_result[:100]}...")
                            else:
                                logger.warning(f"[PDF] Image extraction failed: {img_result}")
                        except Exception as e:
                            logger.warning(f"[PDF] Image extraction error: {e}")
                else:
                    logger.info(f"[PDF] Skipping mineru parsing (mineru not available)")
            except ImportError:
                logger.info(f"[PDF] Skipping mineru parsing (mineru not available)")
            
        except Exception as e:
            logger.warning(f"[PDF] Mineru processing failed for {paper.title[:80]}: {e}")
            # Continue with text-only parsing as fallback
        
        # Step 3: Parse the PDF into text chunks (for RAG and analysis)
        try:
            logger.info(f"Parsing PDF for text chunks: {local_pdf_path}")
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
            
            full_text = " ".join([chunk.text for chunk in paper.chunks])
            
            # Dataset detection
            common_datasets = ["kitti", "nuscenes", "waymo", "coco", "imagenet", "cityscapes", 
                             "nyu", "sun rgb-d", "scannet", "ade20k"]
            found_datasets = [d for d in common_datasets if d in full_text.lower()]
            
            if found_datasets:
                logger.info(f"Found datasets in {paper.title}: {found_datasets}")
                # Update global stats
                for ds in found_datasets:
                    if ds not in state.datasets:
                        state.datasets[ds] = {"count": 0, "papers": []}
                    state.datasets[ds]["count"] += 1
                    state.datasets[ds]["papers"].append(paper.title)
                # Update per-paper features
                paper.datasets = list(set(paper.datasets + found_datasets))
            
            # Metrics detection
            common_metrics = [
                "map", "ap", "iou", "ap_3d", "ap_bev", "aos",  # detection
                "abs_rel", "rmse", "δ1", "δ2", "δ3", "delta1", "delta2", "delta3",  # depth
                "miou", "dice", "f1", "accuracy", "precision", "recall",  # segmentation/classification
                "psnr", "ssim", "lpips",  # image quality
                "fid", "is", "inception score"  # generative models
            ]
            
            # Normalize text for better matching
            text_lower = full_text.lower()
            found_metrics = []
            for metric in common_metrics:
                # Match whole words or with common separators
                if f" {metric} " in text_lower or f"_{metric}_" in text_lower or f"{metric}@" in text_lower:
                    found_metrics.append(metric)
            
            if found_metrics:
                logger.info(f"Found metrics in {paper.title}: {found_metrics}")
                # Update global stats
                for metric in found_metrics:
                    metric_key = f"metric_{metric}"
                    if metric_key not in state.datasets:
                        state.datasets[metric_key] = {"count": 0, "papers": []}
                    state.datasets[metric_key]["count"] += 1
                    state.datasets[metric_key]["papers"].append(paper.title)
                    
                    # Also add to paper tags for easier access
                    if metric not in paper.tags:
                        paper.tags.append(f"metric:{metric}")
                # Update per-paper features
                paper.metrics = list(set(paper.metrics + found_metrics))
            
            # Method families detection (new)
            # Combine title + first portion of PDF text for better detection
            combined_text = f"{paper.title} {full_text[:5000]}"  # Use first 5000 chars
            found_families = detect_method_families_from_text(combined_text)
            
            if found_families:
                logger.info(f"Found method families in {paper.title}: {found_families}")
                # Merge with existing families and deduplicate
                paper.method_families = list(set(paper.method_families + found_families))
                    
        except Exception as e:
            logger.warning(f"Failed to parse PDF {local_pdf_path}: {e}")
