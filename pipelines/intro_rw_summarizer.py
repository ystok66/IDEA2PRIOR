"""
Intro + Related Work Summarizer.

This module provides functions for extracting structured summaries
from paper Introduction and Related Work sections using LLM.
"""

import json
import re
from typing import List, Optional, Dict, Any
from dataclasses import asdict
from pathlib import Path

from loguru import logger
from smolagents import LiteLLMModel

from pipelines.sections import PaperSections, load_paper_sections
from pipelines.summaries import IntroRwSummary, save_intro_rw_summaries_jsonl
from pipelines.llm_config import get_litellm_config
from pipelines.tasks.prior_art_fulltext import extract_intro_rw_blocks
from pipelines.tasks.prior_art_utils import ordered_parallel_map
from pipelines.runtime import invoke_model, atomic_write_jsonl


def _extract_json_from_response(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract JSON object from LLM response text.
    
    Handles cases where the model adds extra text around the JSON.
    
    Args:
        text: Raw LLM response text
        
    Returns:
        Parsed JSON dict, or None if parsing fails
    """
    # Try direct parsing first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    
    # Try to find JSON block within markdown code fence
    code_block_pattern = r"```(?:json)?\s*([\s\S]*?)```"
    match = re.search(code_block_pattern, text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    
    # Try to find first { to last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(text[first_brace:last_brace + 1])
        except json.JSONDecodeError:
            pass
    
    return None


def _validate_summary_response(response):
    data = _extract_json_from_response(response.content if hasattr(response, "content") else str(response))
    if not isinstance(data, dict) or type(data.get("is_survey")) is not bool:
        raise ValueError("is_survey must be a JSON boolean")
    for field in ("domain_overview_bullets", "contribution_bullets", "positioning_bullets"):
        if not isinstance(data.get(field), list) or any(not isinstance(v, str) for v in data[field]):
            raise ValueError(f"{field} must be a list of strings")
    if not isinstance(data.get("bullets_with_snippets"), list) or any(not isinstance(v, dict) for v in data["bullets_with_snippets"]):
        raise ValueError("bullets_with_snippets must be a list of objects")
    return True


def summarize_intro_rw_for_paper(
    paper_id: str,
    title: str,
    year: int,
    paper_sections: PaperSections,
    topic: str,
    idea_description: str,
) -> IntroRwSummary:
    """
    Summarize the Introduction and Related Work sections of a single paper.
    
    Uses the Light LLM to extract:
    - Whether the paper is a survey
    - Domain overview bullets
    - Contribution bullets  
    - Positioning bullets
    - Snippets supporting each bullet
    
    Args:
        paper_id: Unique paper identifier
        title: Paper title
        year: Publication year
        paper_sections: Parsed PDF sections
        topic: Target research topic
        idea_description: Description of the research idea being checked
        
    Returns:
        IntroRwSummary with extracted information.
        Returns empty summary on failure (pipeline continues).
    """
    # 1. Extract intro and related work text using anchor+window collection
    # This captures complete sections including subsections
    sections_list = [{
        "role": s.role,
        "heading": s.heading,
        "text": s.text,
    } for s in paper_sections.sections]
    
    intro_text, _, rw_text, _ = extract_intro_rw_blocks(sections_list)
    
    # Handle missing sections
    if not intro_text and not rw_text:
        logger.warning(
            f"[IntroRwSummarizer] No intro or related_work sections found for {paper_id}. "
            "Returning empty summary."
        )
        return IntroRwSummary.empty(paper_id, title, year)
    
    # 2. Build combined text for prompt
    combined_text = ""
    if intro_text:
        combined_text += "### Introduction\n" + intro_text[:6000] + "\n\n"  # Truncate if too long
    if rw_text:
        combined_text += "### Related Work\n" + rw_text[:4000] + "\n\n"
    
    # 3. Build prompt
    prompt = f"""You are helping to analyze research papers for a prior-art check.

We focus ONLY on the Introduction and Related Work sections of a single paper.

You are given:
- The target research topic and idea.
- The paper's metadata (title, year).
- The paper's Introduction and Related Work text.

Your tasks:

1. Decide whether this paper is a SURVEY/REVIEW paper.
   - Answer with a boolean `is_survey` based on the title and introduction.
   - A survey paper usually reviews many existing works and does not propose a single main method.

2. Summarize the DOMAIN OVERVIEW (2–4 bullets) in `domain_overview_bullets`.
   - These bullets should describe the problem area and motivation as stated by this paper.
   - Focus on what the paper says the COMMUNITY cares about, not just this paper's method.

3. Summarize the paper's MAIN CONTRIBUTIONS (2–4 bullets) in `contribution_bullets`.
   - These should be things the authors claim as their own contributions.
   - Often appears in the last paragraphs of the Introduction.

4. Summarize how this paper POSITIONS itself relative to prior work (1–3 bullets) in `positioning_bullets`.
   - E.g., "extends X to Y", "differs from prior work by ...", "combines A and B".

5. For each contribution bullet, provide one supporting snippet in `bullets_with_snippets`.
   - Each entry must be a JSON object with fields:
     - "type": "contribution"
     - "bullet": (one of the contribution_bullets)
     - "snippet": a 1–3 sentence quote from the original text
     - "section_role": "intro" or "related_work"
     - "loc": a short indicator like "intro_para_5" or "rw_para_3" (best effort; approximate if needed)

Return ONLY ONE JSON object with the following fields:
{{
  "is_survey": bool,
  "domain_overview_bullets": [string],
  "contribution_bullets": [string],
  "positioning_bullets": [string],
  "bullets_with_snippets": [ {{ ... }} ]
}}

Do not add any extra commentary outside the JSON.

---

TARGET TOPIC:
{topic}

TARGET IDEA:
{idea_description[:1000]}

PAPER METADATA:
Title: {title}
Year: {year}

INTRODUCTION + RELATED WORK TEXT:
{combined_text}
"""
    
    # 4. Call Light LLM
    try:
        model_id, api_base, api_key = get_litellm_config(role="light")
        light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
        
        messages = [{"role": "user", "content": prompt}]
        response = invoke_model(light_model, messages, stage="intro_rw_summary", task_id=paper_id, validate_response=_validate_summary_response)
        
        # Extract text from response
        if hasattr(response, "content"):
            response_text = response.content
        else:
            response_text = str(response)
        
        logger.debug(f"[IntroRwSummarizer] Raw response for {paper_id}: {response_text[:500]}...")
        
    except Exception as e:
        logger.error(f"[IntroRwSummarizer] LLM call failed for {paper_id}: {e}")
        return IntroRwSummary.empty(paper_id, title, year, status="failed", error=str(e))
    
    # 5. Parse JSON response
    data = _extract_json_from_response(response_text)
    
    if data is None:
        logger.error(
            f"[IntroRwSummarizer] Failed to parse JSON for {paper_id}. "
            f"Response preview: {response_text[:300]}..."
        )
        return IntroRwSummary.empty(paper_id, title, year, status="failed", error="Invalid JSON response")
    
    # 6. Build IntroRwSummary
    try:
        if not isinstance(data, dict) or type(data.get("is_survey")) is not bool:
            raise ValueError("is_survey must be a JSON boolean")
        for field in ("domain_overview_bullets", "contribution_bullets", "positioning_bullets"):
            if not isinstance(data.get(field), list) or any(not isinstance(v, str) for v in data[field]):
                raise ValueError(f"{field} must be a list of strings")
        if not isinstance(data.get("bullets_with_snippets"), list) or any(
                not isinstance(v, dict) for v in data["bullets_with_snippets"]):
            raise ValueError("bullets_with_snippets must be a list of objects")
        summary = IntroRwSummary(
            paper_id=paper_id,
            title=title,
            year=year,
            is_survey=data["is_survey"],
            domain_overview_bullets=list(data.get("domain_overview_bullets", [])),
            contribution_bullets=list(data.get("contribution_bullets", [])),
            positioning_bullets=list(data.get("positioning_bullets", [])),
            bullets_with_snippets=list(data.get("bullets_with_snippets", [])),
        )
        
        logger.info(
            f"[IntroRwSummarizer] Summarized {paper_id}: "
            f"is_survey={summary.is_survey}, "
            f"domain={len(summary.domain_overview_bullets)} bullets, "
            f"contrib={len(summary.contribution_bullets)} bullets"
        )
        
        return summary
        
    except Exception as e:
        logger.error(f"[IntroRwSummarizer] Failed to build summary for {paper_id}: {e}")
        return IntroRwSummary.empty(paper_id, title, year, status="failed", error=str(e))


def _summarize_single_paper(
    paper_info: Dict[str, Any],
    task_root: str,
    topic: str,
    idea_description: str,
    paper_index: int,
    total_papers: int,
) -> tuple:
    """
    Thread-safe wrapper for summarizing a single paper.
    
    Returns:
        Tuple of (paper_id, summary, status) where status is one of:
        - 'success': Successfully summarized
        - 'no_sections': No sections JSON found
        - 'no_text': Sections exist but no intro/rw content
        - 'empty_bullets': LLM returned empty bullets
    """
    import time
    start_time = time.time()
    
    paper_id = paper_info.get("paper_id") or paper_info.get("id", f"unknown_{paper_index}")
    title = paper_info.get("title", "Unknown Title")
    year = paper_info.get("year", 0)
    
    logger.info(f"[IntroRwSummarizer] [{paper_index+1}/{total_papers}] Processing: {title[:60]}...")
    
    # Load sections
    paper_sections = load_paper_sections(task_root, paper_id)
    
    if paper_sections is None:
        logger.warning(
            f"[IntroRwSummarizer] No sections JSON found for {paper_id}. "
            f"Skipping - PDF was likely not downloaded."
        )
        return (paper_id, None, 'no_sections')
    
    # Check if there's actually intro/rw content using anchor+window collection
    sections_list = [{
        "role": s.role,
        "heading": s.heading,
        "text": s.text,
    } for s in paper_sections.sections]
    
    intro_text, _, rw_text, _ = extract_intro_rw_blocks(sections_list)
    
    if not intro_text and not rw_text:
        logger.warning(
            f"[IntroRwSummarizer] Paper {paper_id} has sections but no intro/rw content. "
            f"Sections found: {[s.role for s in paper_sections.sections]}"
        )
        return (paper_id, None, 'no_text')
    
    logger.info(
        f"[IntroRwSummarizer] Found content for {paper_id}: "
        f"intro={len(intro_text)} chars, rw={len(rw_text)} chars"
    )
    
    # Summarize
    summary = summarize_intro_rw_for_paper(
        paper_id=paper_id,
        title=title,
        year=year,
        paper_sections=paper_sections,
        topic=topic,
        idea_description=idea_description,
    )
    
    elapsed = time.time() - start_time
    
    if summary.status == "failed":
        return (paper_id, summary, "error")
    # Check if we got actual content
    if summary.contribution_bullets or summary.domain_overview_bullets:
        logger.info(f"[IntroRwSummarizer] Summarized {paper_id} in {elapsed:.1f}s")
        return (paper_id, summary, 'success')
    else:
        logger.warning(f"[IntroRwSummarizer] LLM returned empty bullets for {paper_id}")
        return (paper_id, None, 'empty_bullets')


def summarize_intro_rw_for_block3(
    task_root: str,
    papers: List[Dict[str, Any]],
    topic: str,
    idea_description: str,
    max_workers: int = 5,
) -> List[IntroRwSummary]:
    """
    Summarize Intro+RW for all Block 3 papers using parallel processing.
    
    Block 3 papers are those with coverage_level >= 2 (partial or high overlap).
    
    For each paper:
    1. Load its sections JSON from {task_root}/pdf_sections/{paper_id}.sections.json
    2. Call summarize_intro_rw_for_paper()
    3. Collect results
    
    After processing all papers, writes results to:
    {task_root}/synthesis/intro_rw_summaries.jsonl
    
    Args:
        task_root: Root directory for the task/run
        papers: List of paper dicts with at least 'paper_id', 'title', 'year' keys
        topic: Target research topic
        idea_description: Description of the research idea
        max_workers: Maximum number of parallel workers (default: 5)
        
    Returns:
        List of IntroRwSummary objects (also saved to disk)
    """
    import time
    import os
    
    # Get max_workers from environment if not specified
    env_workers = os.getenv("INTRO_RW_MAX_WORKERS")
    if env_workers:
        try:
            max_workers = int(env_workers)
        except ValueError:
            pass
    
    summaries: List[IntroRwSummary] = []
    skipped_no_sections = 0
    skipped_no_text = 0
    successful = 0
    
    total_papers = len(papers)
    logger.info(
        f"[IntroRwSummarizer] Starting summarization for {total_papers} Block 3 papers "
        f"with max_workers={max_workers}..."
    )
    
    start_time = time.monotonic()
    statuses = []

    def summarize_task(task):
        i, paper_info = task
        try:
            return _summarize_single_paper(
                paper_info,
                task_root,
                topic,
                idea_description,
                i,
                total_papers,
            )
        except Exception as e:
            paper_id = paper_info.get("paper_id") or paper_info.get("id", "unknown")
            logger.error(f"[IntroRwSummarizer] Thread failed for {paper_id}: {e}")
            return paper_id, None, "failed"

    for result_id, summary, status in ordered_parallel_map(list(enumerate(papers)), summarize_task, max_workers):
        statuses.append({"paper_id": result_id, "status": status, "error": getattr(summary, "error", None)})
        if status == "success" and summary is not None:
            summaries.append(summary)
            successful += 1
        elif status == "no_sections":
            skipped_no_sections += 1
        else:
            skipped_no_text += 1

    elapsed = time.monotonic() - start_time
    
    # Save to JSONL
    out_path = Path(task_root) / "synthesis" / "intro_rw_summaries.jsonl"
    atomic_write_jsonl(out_path, [summary.to_dict() for summary in summaries])
    atomic_write_jsonl(out_path.with_name("intro_rw_summary_status.jsonl"), statuses)
    
    logger.info(
        f"[IntroRwSummarizer] Completed in {elapsed:.1f}s: {successful} summarized, "
        f"{skipped_no_sections} skipped (no sections), "
        f"{skipped_no_text} skipped (no text/empty bullets). "
        f"Output: {out_path}"
    )
    
    return summaries
