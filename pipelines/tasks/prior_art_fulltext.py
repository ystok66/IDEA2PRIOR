"""
Prior Art Check - Fulltext Processing Module (Block 3 Enhancement)

This module provides advanced fulltext analysis for Block3 papers:

1.3 Method/Experiment Snippets:
    - Extract paragraphs from Method/Experiment sections
    - Build BM25 index for facet-based retrieval
    - Use LLM-light to filter and generate structured bullets

1.4 Key Papers & Intro+RW Full JSON:
    - Select key papers (top_cited, latest, survey)
    - Export complete Intro+RW text for synthesis

These outputs are used by heavy models for structured synthesis writing.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Set, Any, Optional, Tuple
from dataclasses import asdict
from collections import defaultdict

from loguru import logger
from rank_bm25 import BM25Okapi

from pipelines.state import PipelineState, Paper
from pipelines.run_manager import RunManager
from pipelines.llm_config import get_litellm_config

from pipelines.tasks.prior_art_types import (
    PriorArtConfig,
    Paragraph,
    MethodExpSnippet,
)
from pipelines.tasks.prior_art_llm_utils import (
    parse_json_with_repair,
    parse_json_list_with_repair,
    extract_text_from_llm_response,
)


# =============================================================================
# Intro/RW Block Extraction Helpers (Anchor + Window Collection)
# =============================================================================

# Regex to match top-level section numbers like "1.", "2.", etc.
# Strict pattern: must have digit(s) followed by a dot to avoid "3D Object Detection"
TOP_LEVEL_SECTION_PATTERN = re.compile(r'^\s*([0-9]+)\.')


def get_top_level_number(heading: str) -> Optional[int]:
    """
    Extract top-level section number from a heading.
    
    Examples:
        "1. Introduction" -> 1
        "2. Related Work" -> 2
        "2.1 Monocular Detection" -> 2  (extracts first number before the dot)
        "INTRODUCTION" -> None (no number)
        "3D Object Detection" -> None (no dot after 3)
    
    Args:
        heading: Section heading text
        
    Returns:
        Top-level section number (1, 2, 3, ...) or None if not numbered
    """
    if not heading:
        return None
    
    match = TOP_LEVEL_SECTION_PATTERN.match(heading)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _collect_block_from_anchor(
    sections: List[Dict[str, Any]],
    anchor_role: str,
    max_sections: int = 15,
) -> Tuple[str, str]:
    """
    Collect complete section block starting from anchor role until next top-level section.
    
    This handles the case where Intro/RW has subsections (1.1, 1.2, etc.) that get
    marked as 'other' by the role inference. We collect from the anchor until we
    see a heading with a higher top-level number.
    
    Args:
        sections: List of section dicts with 'role', 'heading', 'text' keys
        anchor_role: Role to search for as anchor ("intro" or "related_work")
        max_sections: Hard cap on sections to collect (safety limit)
        
    Returns:
        Tuple of (collected_text, anchor_heading)
        If anchor not found, returns ("", "")
    """
    if not sections:
        return "", ""
    
    # Log available roles for debugging
    roles_present = sorted(set(s.get("role", "unknown") for s in sections))
    logger.debug(f"[Fulltext] Section roles present: {roles_present}")
    
    # Find anchor section
    anchor_idx = None
    for i, sec in enumerate(sections):
        if sec.get("role", "").lower() == anchor_role:
            anchor_idx = i
            break
    
    if anchor_idx is None:
        logger.debug(f"[Fulltext] No anchor found for role '{anchor_role}'")
        return "", ""
    
    anchor_sec = sections[anchor_idx]
    anchor_heading = anchor_sec.get("heading", "")
    anchor_num = get_top_level_number(anchor_heading)
    
    # Collect text from anchor and subsequent sections
    collected_texts: List[str] = []
    anchor_text = anchor_sec.get("text", "").strip()
    if anchor_text:
        collected_texts.append(anchor_text)
    
    sections_collected = 1
    
    for sec in sections[anchor_idx + 1:]:
        if sections_collected >= max_sections:
            logger.debug(f"[Fulltext] Hit max_sections={max_sections} for '{anchor_role}'")
            break
        
        sec_heading = sec.get("heading", "")
        sec_num = get_top_level_number(sec_heading)
        
        # Decide whether to stop
        if anchor_num is not None:
            # Anchor has a number (e.g., "1. Introduction")
            # Stop when we see a higher top-level number (e.g., 2 > 1)
            if sec_num is not None and sec_num > anchor_num:
                logger.debug(f"[Fulltext] Stopping at '{sec_heading}' (num {sec_num} > {anchor_num})")
                break
        else:
            # Anchor has no number (e.g., "INTRODUCTION")
            # Stop when we see ANY numbered section
            if sec_num is not None:
                logger.debug(f"[Fulltext] Stopping at first numbered section '{sec_heading}'")
                break
        
        # Collect this section
        sec_text = sec.get("text", "").strip()
        if sec_text:
            collected_texts.append(sec_text)
        sections_collected += 1
    
    logger.debug(f"[Fulltext] Collected {sections_collected} sections for '{anchor_role}'")
    
    return "\n\n".join(collected_texts), anchor_heading


def extract_intro_rw_blocks(sections: List[Dict[str, Any]]) -> Tuple[str, str, str, str]:
    """
    Extract complete Introduction and Related Work blocks from parsed sections.
    
    This is the shared helper that should be used by both:
    - _build_intro_rw_full_json() for 1.4 Key Papers
    - intro_rw_summarizer for Block 3 summaries
    
    Args:
        sections: List of section dicts from pdf_sections JSON
        
    Returns:
        Tuple of (intro_text, intro_heading, rw_text, rw_heading)
    """
    intro_text, intro_heading = _collect_block_from_anchor(sections, "intro")
    rw_text, rw_heading = _collect_block_from_anchor(sections, "related_work")
    
    # Fallback headings if not found
    if intro_text and not intro_heading:
        intro_heading = "Introduction"
    if rw_text and not rw_heading:
        rw_heading = "Related Work"
    
    return intro_text, intro_heading, rw_text, rw_heading


# =============================================================================
# 1.3 Method/Experiment Snippets
# =============================================================================


def build_method_experiment_snippets(
    state: PipelineState,
    facets: List[str],
    idea_features: Dict[str, Set[str]],
    run_mgr: Optional[RunManager],
    config: PriorArtConfig,
) -> None:
    """
    Main entry point for 1.3: Build Method/Experiment snippets.
    
    For each Block3 paper and each facet, extract relevant paragraphs from
    Method/Experiment sections, filter with LLM-light, and write structured
    snippets to synthesis/method_exp_snippets.jsonl.
    """
    if not config.enable_method_exp_snippets:
        logger.info("[1.3] Method/Experiment snippets disabled by config.")
        return
    
    if not run_mgr:
        logger.warning("[1.3] No run_mgr provided, skipping snippet extraction.")
        return
    
    task_root = Path(run_mgr.run_dir)
    
    # Get Block3 paper IDs (papers with fulltext)
    paper_ids = [pid for pid, p in state.papers.items() if p.chunks]
    if not paper_ids:
        logger.warning("[1.3] No papers with fulltext found, skipping.")
        return
    
    logger.info(f"[1.3] Building Method/Experiment snippets for {len(paper_ids)} papers, {len(facets)} facets...")
    
    # Step 1: Load paragraphs from Method/Experiment sections
    paragraphs = _load_method_and_experiment_paragraphs(task_root, paper_ids)
    if not paragraphs:
        logger.warning("[1.3] No Method/Experiment paragraphs found.")
        return
    
    logger.info(f"[1.3] Loaded {len(paragraphs)} paragraphs from Method/Experiment sections.")
    
    # Step 2: Build BM25 index
    bm25_index, tokenized_paragraphs = _build_bm25_index(paragraphs)
    
    # Step 3: For each facet, search and filter
    all_snippets: List[MethodExpSnippet] = []
    max_paras = (
        config.debug_max_paragraphs_per_facet 
        if config.debug_mode 
        else config.max_paragraphs_per_facet
    )
    
    for i, facet in enumerate(facets):
        logger.debug(f"[1.3] Processing facet {i+1}/{len(facets)}: {facet[:50]}...")
        
        # Generate query from facet
        query = _make_bm25_query_for_facet(facet, idea_features)
        
        # Search BM25
        candidates = _search_paragraphs_bm25(
            bm25_index, tokenized_paragraphs, paragraphs,
            query, top_n=max_paras
        )
        
        if not candidates:
            continue
        
        # LLM filter and bullet generation
        snippets = _llm_filter_and_summarize_paragraphs(
            facet_label=facet,
            candidates=candidates,
            config=config,
        )
        
        # Deduplicate per (paper_id, facet_label)
        snippets = _dedupe_snippets(snippets, config.max_snippets_per_paper_facet)
        all_snippets.extend(snippets)
    
    # Step 4: Write to JSONL
    output_path = task_root / "synthesis" / "method_exp_snippets.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        for snip in all_snippets:
            obj = {
                "paper_id": snip.paper_id,
                "facet_label": snip.facet_label,
                "section_role": snip.section_role,
                "section_heading": snip.section_heading,
                "loc": snip.loc,
                "bullet": snip.bullet,
                "snippet": snip.snippet,
                "score": {
                    "bm25": snip.bm25_score,
                    "llm_relevance": snip.llm_relevance,
                }
            }
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    
    logger.info(f"[1.3] Wrote {len(all_snippets)} snippets to {output_path}")


def _load_method_and_experiment_paragraphs(
    task_root: Path,
    paper_ids: List[str],
) -> List[Paragraph]:
    """
    Load Method and Experiment sections from pdf_sections JSON files,
    split into paragraphs with context.
    """
    paragraphs: List[Paragraph] = []
    sections_dir = task_root / "pdf_sections"
    
    for pid in paper_ids:
        # Normalize paper_id for filename
        safe_pid = pid.replace("/", "_").replace(":", "_")
        sections_path = sections_dir / f"{safe_pid}.sections.json"
        
        if not sections_path.exists():
            # Try other patterns
            for f in sections_dir.glob(f"*{safe_pid[-20:]}*.json"):
                sections_path = f
                break
        
        if not sections_path.exists():
            continue
        
        try:
            with open(sections_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.debug(f"[1.3] Failed to load sections for {pid}: {e}")
            continue
        
        sections = data.get("sections", [])
        for sec in sections:
            role = sec.get("role", "").lower()
            if role not in {"method", "experiment", "methods", "experiments", "methodology"}:
                continue
            
            # Normalize role
            if role in {"methods", "methodology"}:
                role = "method"
            elif role == "experiments":
                role = "experiment"
            
            text = sec.get("text", "")
            heading = sec.get("heading", "")
            
            # Split into paragraphs (by double newline or significant whitespace)
            para_texts = _split_into_paragraphs(text)
            
            for i, para_text in enumerate(para_texts):
                if len(para_text.strip()) < 50:  # Skip very short paragraphs
                    continue
                
                # Get context (prev/next paragraph)
                prev_text = para_texts[i-1] if i > 0 else ""
                next_text = para_texts[i+1] if i < len(para_texts) - 1 else ""
                
                paragraphs.append(Paragraph(
                    paper_id=pid,
                    section_role=role,
                    section_heading=heading,
                    para_id=i,
                    text=para_text.strip(),
                    token_count=len(para_text.split()),
                    prev_para_text=prev_text[:500] if prev_text else "",
                    next_para_text=next_text[:500] if next_text else "",
                ))
    
    return paragraphs


def _split_into_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs, preferring blank lines."""
    # First try double newline
    paras = re.split(r'\n\s*\n', text)
    
    result = []
    for para in paras:
        para = para.strip()
        if not para:
            continue
        
        # If paragraph is too long, split by sentences
        if len(para.split()) > 200:
            # Split by sentence endings
            sentences = re.split(r'(?<=[.!?])\s+', para)
            chunk = []
            chunk_len = 0
            for sent in sentences:
                chunk.append(sent)
                chunk_len += len(sent.split())
                if chunk_len >= 100:
                    result.append(" ".join(chunk))
                    chunk = []
                    chunk_len = 0
            if chunk:
                result.append(" ".join(chunk))
        else:
            result.append(para)
    
    return result


def _build_bm25_index(
    paragraphs: List[Paragraph],
) -> Tuple[BM25Okapi, List[List[str]]]:
    """Build BM25 index over paragraph texts."""
    tokenized = []
    for para in paragraphs:
        # Simple tokenization: lowercase and split
        tokens = para.text.lower().split()
        tokenized.append(tokens)
    
    bm25 = BM25Okapi(tokenized)
    return bm25, tokenized


def _make_bm25_query_for_facet(
    facet: str,
    idea_features: Any,  # Can be Dict[str, Set[str]] or Tuple containing features
) -> str:
    """
    Generate BM25 query string from facet and idea features.
    Unified entry point for facet->query mapping.
    """
    # Start with facet text
    query_parts = [facet.lower()]
    
    # Handle different types of idea_features
    if isinstance(idea_features, dict):
        # Dict case: iterate over categories
        for category, features in idea_features.items():
            if isinstance(features, (set, list)):
                for feat in list(features)[:5]:  # Limit features
                    query_parts.append(str(feat).lower())
    elif isinstance(idea_features, (tuple, list)):
        # Tuple/list case: iterate directly
        for item in idea_features[:20]:  # Limit items
            if isinstance(item, str):
                query_parts.append(item.lower())
            elif isinstance(item, dict) and "feature" in item:
                query_parts.append(str(item["feature"]).lower())
    
    return " ".join(query_parts)


def _search_paragraphs_bm25(
    bm25: BM25Okapi,
    tokenized: List[List[str]],
    paragraphs: List[Paragraph],
    query: str,
    top_n: int = 50,
) -> List[Tuple[Paragraph, float]]:
    """Search BM25 index and return top-N paragraphs with scores."""
    query_tokens = query.lower().split()
    scores = bm25.get_scores(query_tokens)
    
    # Get top-N
    scored_paras = list(zip(paragraphs, scores))
    scored_paras.sort(key=lambda x: x[1], reverse=True)
    
    return [(p, s) for p, s in scored_paras[:top_n] if s > 0]


def _llm_filter_and_summarize_paragraphs(
    facet_label: str,
    candidates: List[Tuple[Paragraph, float]],
    config: PriorArtConfig,
) -> List[MethodExpSnippet]:
    """
    Use LLM-light to filter paragraphs for relevance and generate bullets.
    Returns only relevant paragraphs as MethodExpSnippet objects.
    """
    if not candidates:
        return []
    
    # Batch candidates for efficiency
    snippets: List[MethodExpSnippet] = []
    batch_size = 10
    
    model_id, api_base, api_key = get_litellm_config("light")
    from smolagents import LiteLLMModel
    model = LiteLLMModel(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
    )
    
    for i in range(0, min(len(candidates), 30), batch_size):  # Max 30 candidates
        batch = candidates[i:i+batch_size]
        
        # Build prompt
        para_texts = []
        for j, (para, score) in enumerate(batch):
            context = ""
            if para.prev_para_text:
                context += f"[Previous]: {para.prev_para_text[:200]}... "
            context += f"[Current]: {para.text[:500]}"
            if para.next_para_text:
                context += f" [Next]: {para.next_para_text[:200]}..."
            para_texts.append(f"Para {j+1} (paper={para.paper_id[:30]}, section={para.section_role}):\n{context}")
        
        prompt = f"""You are analyzing research paper paragraphs for relevance to a specific technical facet.

Facet being analyzed: "{facet_label}"

Paragraphs to evaluate:
{chr(10).join(para_texts)}

For each paragraph, determine if it is RELEVANT to the facet and if so, generate a concise bullet summary.

Output a JSON array with one object per paragraph. Each object must have exactly these fields:
- "para_num": the paragraph number (1, 2, 3, etc.)
- "relevant": true or false
- "bullet": a one-sentence summary (only if relevant, otherwise empty string)
- "snippet": key 1-2 sentences from the original text (only if relevant, otherwise empty string)
- "relevance_score": 0.0 to 1.0 (0 if not relevant)

Output ONLY the JSON array, no other text."""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = model(messages)
            # LiteLLMModel returns string directly
            resp_text = response if isinstance(response, str) else extract_text_from_llm_response(response)
            # Use list parser since we expect a JSON array
            results = parse_json_list_with_repair(resp_text) or []
            
            for item in results:
                if not isinstance(item, dict):
                    continue
                if not item.get("relevant", False):
                    continue
                
                para_num = item.get("para_num", 1) - 1
                if para_num < 0 or para_num >= len(batch):
                    continue
                
                para, bm25_score = batch[para_num]
                
                snippets.append(MethodExpSnippet(
                    paper_id=para.paper_id,
                    facet_label=facet_label,
                    section_role=para.section_role,
                    section_heading=para.section_heading,
                    loc=f"{para.section_role}_para_{para.para_id}",
                    bullet=item.get("bullet", "")[:500],
                    snippet=item.get("snippet", para.text[:300]),
                    bm25_score=bm25_score,
                    llm_relevance=item.get("relevance_score", 0.5),
                ))
                
        except Exception as e:
            logger.warning(f"[1.3] LLM filter failed for batch: {e}")
            continue
    
    return snippets


def _dedupe_snippets(
    snippets: List[MethodExpSnippet],
    max_per_paper_facet: int = 5,
) -> List[MethodExpSnippet]:
    """Deduplicate and limit snippets per (paper_id, facet_label)."""
    # Group by (paper_id, facet_label)
    groups: Dict[Tuple[str, str], List[MethodExpSnippet]] = defaultdict(list)
    for snip in snippets:
        key = (snip.paper_id, snip.facet_label)
        groups[key].append(snip)
    
    result = []
    for key, group in groups.items():
        # Sort by relevance score
        group.sort(key=lambda x: x.llm_relevance, reverse=True)
        
        # Dedupe by bullet similarity (simple approach)
        seen_bullets: Set[str] = set()
        kept = []
        for snip in group:
            # Normalize bullet for comparison
            norm_bullet = snip.bullet.lower().strip()
            if norm_bullet in seen_bullets:
                continue
            seen_bullets.add(norm_bullet)
            kept.append(snip)
            if len(kept) >= max_per_paper_facet:
                break
        
        result.extend(kept)
    
    return result


# =============================================================================
# 1.4 Key Papers & Intro+RW Full JSON
# =============================================================================


def select_key_papers_and_dump_intro_rw_full(
    agent,
    state: PipelineState,
    task_root: Path,
    config: PriorArtConfig,
) -> None:
    """
    Main entry point for 1.4: Select key papers and export Intro+RW full text.
    
    1. Build candidate pools (top_cited, latest, survey)
    2. Use heavy LLM to select primary/alternates for each bucket
    3. Write key_papers_selection.json
    4. For each key paper, export intro_rw_full/{paper_id}.json
    """
    if not config.enable_intro_rw_full:
        logger.info("[1.4] Intro+RW full export disabled by config.")
        return
    
    logger.info("[1.4] Selecting key papers and exporting Intro+RW full text...")
    
    # Get Block3 papers with meta
    candidates = _build_key_paper_candidates(state, config)
    if not candidates:
        logger.warning("[1.4] No candidates for key paper selection.")
        return
    
    # Build candidate pools
    top_cited_pool = sorted(candidates, key=lambda x: x.get("citation_count", 0), reverse=True)[:config.key_paper_top_cited_k]
    latest_pool = sorted(candidates, key=lambda x: x.get("year", 0), reverse=True)[:config.key_paper_latest_k]
    survey_pool = [c for c in candidates if c.get("is_survey", False)][:config.key_paper_survey_k]
    
    logger.info(f"[1.4] Candidate pools: top_cited={len(top_cited_pool)}, latest={len(latest_pool)}, survey={len(survey_pool)}")
    
    # Use LLM to select key papers (or simple heuristic in debug mode)
    buckets = _select_key_papers_with_llm(
        agent=agent,
        top_cited_pool=top_cited_pool,
        latest_pool=latest_pool,
        survey_pool=survey_pool,
        topic=state.intent.topic if state.intent else "unknown topic",
        config=config,
    )
    
    # Build per_paper_roles
    per_paper_roles: Dict[str, List[str]] = defaultdict(list)
    for bucket_name, bucket_data in buckets.items():
        primary = bucket_data.get("primary")
        if primary:
            per_paper_roles[primary].append(f"{bucket_name}_primary")
        for alt in bucket_data.get("alternates", []):
            per_paper_roles[alt].append(f"{bucket_name}_alternate")
    
    # Write key_papers_selection.json
    selection_obj = {
        "topic": state.intent.topic if state.intent else "unknown",
        "generated_at": datetime.now().isoformat() + "Z",
        "generator_version": "v0.1",
        "selection_policy": {
            "top_cited_k": config.key_paper_top_cited_k,
            "latest_k": config.key_paper_latest_k,
            "survey_k": config.key_paper_survey_k,
        },
        "buckets": buckets,
        "per_paper_roles": dict(per_paper_roles),
    }
    
    synthesis_dir = task_root / "synthesis"
    synthesis_dir.mkdir(parents=True, exist_ok=True)
    
    selection_path = synthesis_dir / "key_papers_selection.json"
    with open(selection_path, "w", encoding="utf-8") as f:
        json.dump(selection_obj, f, indent=2, ensure_ascii=False)
    
    logger.info(f"[1.4] Wrote key papers selection to {selection_path}")
    
    # Export Intro+RW full for key papers
    intro_rw_dir = synthesis_dir / "intro_rw_full"
    intro_rw_dir.mkdir(parents=True, exist_ok=True)
    
    key_paper_ids = list(per_paper_roles.keys())
    exported = 0
    
    for pid in key_paper_ids:
        paper = state.papers.get(pid)
        if not paper:
            continue
        
        intro_rw_obj = _build_intro_rw_full_json(
            paper=paper,
            paper_id=pid,  # Pass the key as paper_id
            task_root=task_root,
            roles=per_paper_roles[pid],
        )
        
        if intro_rw_obj:
            safe_pid = pid.replace("/", "_").replace(":", "_")
            output_path = intro_rw_dir / f"{safe_pid}.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(intro_rw_obj, f, indent=2, ensure_ascii=False)
            exported += 1
    
    logger.info(f"[1.4] Exported {exported} Intro+RW full JSON files to {intro_rw_dir}")


def _build_key_paper_candidates(
    state: PipelineState,
    config: PriorArtConfig,
) -> List[Dict[str, Any]]:
    """Build candidate list for key paper selection.
    
    IMPORTANT: Only includes papers with has_fulltext=True so intro_rw_full can be exported.
    Papers without fulltext would result in empty exports.
    """
    candidates = []
    skipped_no_fulltext = 0
    
    for pid, paper in state.papers.items():
        # Only consider papers with some content for ranking
        if not paper.chunks and not paper.abstract:
            continue
        
        # CRITICAL: Only include papers with successful fulltext parsing
        # This ensures intro_rw_full export will have actual content
        if not getattr(paper, "has_fulltext", False):
            skipped_no_fulltext += 1
            continue
        
        candidates.append({
            "paper_id": pid,
            "title": paper.title or "",
            "year": paper.year or 0,
            "venue": paper.venue or "",
            "citation_count": paper.citation_count or 0,
            "is_survey": getattr(paper, "is_survey", False),
            "abstract": (paper.abstract or "")[:500],
            "has_fulltext": True,  # Track for debugging
        })
    
    if skipped_no_fulltext > 0:
        logger.debug(f"[1.4] Skipped {skipped_no_fulltext} papers without fulltext")
    
    return candidates


def _select_key_papers_with_llm(
    agent,
    top_cited_pool: List[Dict],
    latest_pool: List[Dict],
    survey_pool: List[Dict],
    topic: str,
    config: PriorArtConfig,
) -> Dict[str, Dict[str, Any]]:
    """
    Use heavy LLM to select primary and alternates for each bucket.
    Falls back to simple heuristic selection if LLM fails.
    """
    # Simple heuristic selection (works for both debug and fallback)
    buckets = {
        "top_cited": {
            "primary": top_cited_pool[0]["paper_id"] if top_cited_pool else None,
            "alternates": [p["paper_id"] for p in top_cited_pool[1:3]] if len(top_cited_pool) > 1 else [],
        },
        "latest": {
            "primary": latest_pool[0]["paper_id"] if latest_pool else None,
            "alternates": [p["paper_id"] for p in latest_pool[1:3]] if len(latest_pool) > 1 else [],
        },
        "survey": {
            "primary": survey_pool[0]["paper_id"] if survey_pool else None,
            "alternates": [p["paper_id"] for p in survey_pool[1:3]] if len(survey_pool) > 1 else [],
        },
    }
    
    # In debug mode, use simple heuristic
    if config.debug_mode:
        logger.info("[1.4] Using heuristic key paper selection (debug mode)")
        return buckets
    
    # Try LLM-based selection for better results
    try:
        model_id, api_base, api_key = get_litellm_config("expert")
        from smolagents import LiteLLMModel
        model = LiteLLMModel(
            model_id=model_id,
            api_base=api_base,
            api_key=api_key,
        )
        
        # Build prompt
        def pool_to_text(pool: List[Dict], label: str) -> str:
            if not pool:
                return f"{label}: (none available)"
            lines = []
            for i, p in enumerate(pool[:5]):
                lines.append(f"  {i+1}. [{p['paper_id'][:30]}] {p['title'][:60]} ({p['year']}, cited={p['citation_count']})")
            return f"{label}:\n" + "\n".join(lines)
        
        prompt = f"""You are selecting key representative papers for a literature review on: "{topic}"

Available candidates by category:

{pool_to_text(top_cited_pool, "TOP CITED")}

{pool_to_text(latest_pool, "LATEST")}

{pool_to_text(survey_pool, "SURVEYS")}

For each category, select:
- 1 PRIMARY paper (most representative/important)
- 0-2 ALTERNATES (complementary perspectives)

Criteria:
- PRIMARY should be the most influential or representative
- ALTERNATEs should provide different perspectives, not duplicate the primary
- If a paper appears in multiple categories, prefer it as primary in the most relevant one

Output a JSON object with this exact structure:
{{
  "top_cited": {{"primary": "paper_id_here", "alternates": ["id1", "id2"]}},
  "latest": {{"primary": "paper_id_here", "alternates": []}},
  "survey": {{"primary": "paper_id_here_or_null", "alternates": []}}
}}

Output ONLY the JSON, no other text."""

        messages = [{"role": "user", "content": prompt}]
        response = model(messages)
        resp_text = response if isinstance(response, str) else extract_text_from_llm_response(response)
        result = parse_json_with_repair(resp_text)
        
        if isinstance(result, dict) and "top_cited" in result:
            logger.info("[1.4] Successfully used LLM for key paper selection")
            return result
        
    except Exception as e:
        logger.warning(f"[1.4] LLM selection failed, using heuristic: {e}")
    
    return buckets


def _build_intro_rw_full_json(
    paper: Paper,
    paper_id: str,
    task_root: Path,
    roles: List[str],
) -> Optional[Dict[str, Any]]:
    """
    Build Intro+RW full JSON for a key paper.
    Reads from pdf_sections and extracts intro/related_work sections.
    """
    pid = paper_id
    safe_pid = pid.replace("/", "_").replace(":", "_")
    sections_path = task_root / "pdf_sections" / f"{safe_pid}.sections.json"
    
    # Try to find sections file
    if not sections_path.exists():
        for f in (task_root / "pdf_sections").glob(f"*{safe_pid[-20:]}*.json"):
            sections_path = f
            break
    
    if not sections_path.exists():
        logger.warning(f"[1.4] No sections file for key paper {pid}, skipping.")
        return None
    
    try:
        with open(sections_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"[1.4] Failed to load sections for {pid}: {e}")
        return None
    
    sections_list = data.get("sections", [])
    
    # Use anchor+window collection to get complete Intro and RW blocks
    intro_text, intro_heading, rw_text, rw_heading = extract_intro_rw_blocks(sections_list)
    
    # Build section objects
    sections_obj = {}
    meta_flags = {}
    
    if intro_text:
        sections_obj["intro"] = {
            "heading": intro_heading or "Introduction",
            "text": intro_text,
        }
        logger.debug(f"[1.4] Collected intro: {len(intro_text)} chars for {pid}")
    else:
        meta_flags["missing_intro"] = True
        logger.debug(f"[1.4] No intro section found for {pid}")
    
    if rw_text:
        sections_obj["related_work"] = {
            "heading": rw_heading or "Related Work",
            "text": rw_text,
        }
        logger.debug(f"[1.4] Collected related_work: {len(rw_text)} chars for {pid}")
    else:
        # related_work can legitimately be empty (merged with intro, etc.)
        meta_flags["missing_related_work"] = True
        logger.debug(f"[1.4] No related work section found for {pid}")
    
    # Build final object
    result = {
        "paper_id": pid,
        "title": paper.title or "",
        "year": paper.year or 0,
        "venue": paper.venue or "",
        "citation_count": paper.citation_count or 0,
        "is_survey": getattr(paper, "is_survey", False),
        "source": {
            "pdf_sections_path": f"pdf_sections/{safe_pid}.sections.json",
            "generated_at": datetime.now().isoformat() + "Z",
            "generator_version": "v0.1",
        },
        "sections": sections_obj,
        "meta": {
            "selected_as": roles,
            **meta_flags,
        },
    }
    
    return result
