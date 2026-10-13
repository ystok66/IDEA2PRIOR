# pipelines/exploration_loop.py

"""
Multi-round exploration loop for discovering and enriching papers.

This module implements the core discovery logic that:
1. Discovers papers via OpenAlex
2. Enriches metadata via Semantic Scholar
3. Optionally parses key PDFs for deeper analysis
"""

from __future__ import annotations
from pipelines.identity import upsert_paper, paper_from_openalex

import os
import json
from typing import List, Optional
import requests

from loguru import logger

from pipelines.state import PipelineState, Paper, CitationEdge, Intent, Chunk
from tools.openalex_search import OpenAlexWorksSearchTool
from tools.semantic_scholar_search import SemanticScholarPaperDetailTool, SemanticScholarCitationTool
from tools.paper_resolver import PaperResolverTool
from tools.pdf_parser import PdfParserTool

# Conditional import for type checking
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pipelines.tasks.prior_art_types import PriorArtConfig


def run_exploration_loop(
    agent,
    state: PipelineState,
    max_rounds: int = 2,
    max_papers: int = 80,
    min_new_per_round: int = 5,
    search_queries: Optional[List[str]] = None,
    cfg: Optional["PriorArtConfig"] = None,
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

        # 1) Discover new papers for this round.
        newly_added = discover_papers_round(agent, state, search_queries, cfg=cfg)

        logger.info(f"Round {round_idx + 1}: discovered {len(newly_added)} new papers.")
        logger.info(f"Total papers in state: {len(state.papers)}")

        # Early stop if exploration is no longer adding useful papers.
        if len(newly_added) < min_new_per_round:
            logger.info(
                f"Stopping exploration: only {len(newly_added)} new papers "
                f"(min_new_per_round={min_new_per_round})."
            )
            break

        if len(state.papers) >= max_papers:
            logger.info(
                f"Stopping exploration: reached max_papers={max_papers} "
                f"(current={len(state.papers)})."
            )
            break

        # In the staged funnel, relevance selection precedes costly metadata,
        # citation and PDF work. The prior-art orchestrator owns those stages.
        if not getattr(cfg, 'staged_retrieval', False):
            enrich_metadata_and_citations(agent, state)
            maybe_parse_key_papers(agent, state, round_idx=round_idx)

    logger.info(
        f"Exploration loop finished with {len(state.papers)} papers "
        f"after {state.discovery_round} rounds."
    )
    return state


def discover_papers_round(
    agent, 
    state: PipelineState, 
    search_queries: Optional[List[str]] = None,
    cfg: Optional["PriorArtConfig"] = None,
) -> List[Paper]:
    """
    Discover a batch of new papers for the current round.
    
    Args:
        agent: The agent instance
        state: Pipeline state
        search_queries: Optional list of search queries
        cfg: Optional PriorArtConfig for excluded paper filtering
    """
    from pipelines.clients.scholarly import replacement_enabled, discover
    logger.info('Discovering papers via {}...', 'S2 + arXiv' if replacement_enabled(cfg) else 'OpenAlex')
    
    # 1. Construct query from Intent
    topic = state.intent.topic
    years = state.intent.years
    venues = state.intent.venues
    
    from_year = years[0] if years else None
    to_year = years[1] if years else None
    # OpenAlex uses | for OR in filters
    venue_str = "|".join(venues) if venues else None
    
    # 2. Call OpenAlex tool
    if replacement_enabled(cfg):
        queries = search_queries if search_queries else [topic]
        added = []
        for query in queries:
            for paper in discover(query, (from_year, to_year) if from_year and to_year else None, 20, cfg):
                if cfg:
                    from pipelines.tasks.prior_art_utils import is_excluded_paper
                    if is_excluded_paper(paper, cfg):
                        continue
                pid, created = upsert_paper(state, paper, source=paper.retrieval_source)
                if created:
                    added.append(state.papers[pid])
        return added
    oa_tool = OpenAlexWorksSearchTool()
    
    queries_to_run = search_queries if search_queries else [topic]
    all_works = []
    
    for q in queries_to_run:
        logger.info(f"Searching OpenAlex with query: '{q}'")
        try:
            result_json = oa_tool.forward(
                query=q,
                from_year=from_year,
                to_year=to_year,
                venue=venue_str,
                limit=20
            )
            
            if result_json.startswith("Error"):
                logger.warning(f"OpenAlex tool returned error for query '{q}': {result_json}")
                continue
            
            works_batch = json.loads(result_json)
            logger.info(f"Found {len(works_batch)} papers for query: '{q}'")
            all_works.extend(works_batch)
            
        except Exception as e:
            logger.warning(f"Failed to search OpenAlex with query '{q}': {e}")
    
    # Safety net: if all queries returned 0 results, try a simple topic-only query
    if not all_works and search_queries and topic not in search_queries:
        logger.warning(f"[Fallback] All queries returned 0 results. Trying topic-only: '{topic}'")
        try:
            result_json = oa_tool.forward(
                query=topic,
                from_year=from_year,
                to_year=to_year,
                venue=venue_str,
                limit=30
            )
            if not result_json.startswith("Error"):
                works_batch = json.loads(result_json)
                logger.info(f"[Fallback] Found {len(works_batch)} papers with topic query")
                all_works.extend(works_batch)
        except Exception as e:
            logger.warning(f"[Fallback] Topic query also failed: {e}")
            
    newly_added = []
    for work in all_works:
        paper = paper_from_openalex(work, source=f"exploration_round_{state.discovery_round}")
        if cfg:
            from pipelines.tasks.prior_art_utils import is_excluded_paper
            if is_excluded_paper(paper, cfg):
                continue
        pid, created = upsert_paper(state, paper, source=paper.retrieval_source)
        if created:
            newly_added.append(state.papers[pid])

    return newly_added


def enrich_metadata_and_citations(agent, state: PipelineState) -> None:
    """
    Enrich metadata and build/extend the local citation graph.
    """
    logger.info("Enriching metadata via Semantic Scholar...")
    
    s2_detail_tool = SemanticScholarPaperDetailTool()
    s2_citation_tool = SemanticScholarCitationTool()
    
    # Limit enrichment to avoid rate limits (e.g. first 30 papers)
    papers_to_enrich = list(state.papers.values())[:30]
    
    from pipelines.clients.scholarly import replacement_enabled, paper_lookup_id, batch_metadata, paper_from_s2, title_matches
    if replacement_enabled():
        targets = [p for p in papers_to_enrich if not p.fields_of_study and paper_lookup_id(p)]
        batch = batch_metadata([paper_lookup_id(p) for p in targets])
        for paper, item in zip(targets, batch):
            if item['status'] == 'SUCCEEDED':
                enriched = paper_from_s2(item['data'], 's2_batch_enrichment')
                if not title_matches(paper.title, enriched.title):
                    continue
                # Enrich this known material with work metadata; do not bind
                # an unversioned provider PDF to its explicit arXiv version.
                enriched.document_version = paper.document_version
                enriched.arxiv_id = paper.arxiv_id or enriched.arxiv_id
                if paper.document_version:
                    enriched.pdf_url = None
                enriched.paper_id = paper.paper_id
                upsert_paper(state, enriched, source='s2_batch_enrichment')
        papers_to_enrich = []
    for paper in papers_to_enrich:
        # Skip if already enriched (simple check: has fields_of_study)
        if paper.fields_of_study:
            continue
            
        # Determine ID to query
        paper_id = paper_lookup_id(paper)
        if not paper_id:
            continue
            
        try:
            res_json = s2_detail_tool.forward(paper_id=paper_id)
            if "Paper not found" in res_json:
                continue
                
            details = json.loads(res_json)
            
            enriched = Paper(title=paper.title, doi=paper.doi, openalex_id=paper.openalex_id,
                             s2_id=details.get("s2_id"), abstract=details.get("abstract"),
                             pdf_url=details.get("pdf_url"), citation_count=details.get("citation_count"),
                             authors=details.get("authors") or [], paper_id=paper.paper_id)
            upsert_paper(state, enriched, source="s2_metadata")

        except Exception as e:
            logger.warning(f"Failed to enrich paper {paper.title[:30]}...: {e}")
            
    # Optional: Expand citation graph for top-k papers
    sorted_papers = sorted(
        [p for p in state.papers.values() if p.citation_count is not None],
        key=lambda p: p.citation_count,
        reverse=True
    )
    
    for paper in sorted_papers[:3]:
        if not paper.s2_id:
            continue
        try:
            cit_json = s2_citation_tool.forward(
                paper_id=paper.s2_id,
                type="citations",
                limit=20
            )
            # TODO: parse and add citation edges to state
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
    parser = PdfParserTool()

    pdf_cache_dir = os.path.join(os.getcwd(), "pdf_cache")
    os.makedirs(pdf_cache_dir, exist_ok=True)

    for paper in key_papers:
        if paper.identity_status == 'conflict':
            logger.warning('[PDF] Skipping identity conflict: %s', paper.title)
            continue
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

        # Download PDF to local cache
        import hashlib
        material_key = '|'.join(str(value or '') for value in (
            paper.doi, paper.s2_id, paper.arxiv_id, paper.openalex_id,
            paper.document_version, paper.pdf_url))
        local_filename = hashlib.sha256(material_key.encode('utf-8')).hexdigest() + '.pdf'
        local_pdf_path = os.path.join(pdf_cache_dir, local_filename)
        
        if not os.path.exists(local_pdf_path):
            try:
                logger.info(f"Downloading PDF from {paper.pdf_url}...")
                response = requests.get(paper.pdf_url, timeout=30)
                response.raise_for_status()
                with open(local_pdf_path, "wb") as f:
                    f.write(response.content)
            except Exception as e:
                logger.warning(f"[PDF] Failed to download PDF for paper: {paper.title[:80]} - {e}")
                continue
        
        paper.local_pdf_path = local_pdf_path

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
