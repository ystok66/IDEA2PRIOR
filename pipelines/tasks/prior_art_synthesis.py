"""
Prior Art Check - Synthesis JSON Builder (Part 2).

This module consolidates all Block 1-3 results into a single synthesis_input.json
that can be used by heavy LLMs to generate structured reports.

Key outputs:
- synthesis_input.json: Structured JSON with all paper data and prompt templates
- The JSON includes metadata, coverage stats, per-paper data, and prompt templates

Usage:
- Default: Only generates JSON, no LLM calls
- With config.auto_generate_intro_rw_report=True: Also calls heavy LLM to generate MD
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Set, Any, Optional
from collections import defaultdict

from loguru import logger

from pipelines.state import PipelineState, Paper
from pipelines.tasks.prior_art_types import (
    PriorArtConfig,
    PriorArtMatch,
)


# =============================================================================
# Prompt Templates
# =============================================================================

FULL_REPORT_PROMPT = """
You are an expert researcher helping to write the *Introduction* and *Related Work*
sections for a technical paper.

You are given a single JSON object called SYNTHESIS that has the following structure:

- SYNTHESIS.metadata:
    - topic: the broad research area (e.g., "monocular 3D object detection")
    - idea_description: the specific research idea we want to check for prior art
    - facets: a list of fine-grained aspects F1, F2, ... of the idea
    - task: describes WHY this mapping was built (task_type, user_query, user_intent_notes, constraints)
    - audience: who the report is for (e.g., "expert_researcher")
    - usage_context: how the report will be used (e.g., ["paper_introduction", "related_work_section"])
- SYNTHESIS.global_stats:
    - high-level counts and coverage statistics (how many papers at each overlap level, etc.)
- SYNTHESIS.retrieval_summary:
    - query_seeds: search queries used to find papers
    - sources: APIs/databases used (SemanticScholar, OpenAlex, arXiv, etc.)
    - exploration_notes: how many candidates, year range, etc.
- SYNTHESIS.limitations:
    - retrieval_coverage: whether search was exhaustive
    - notes: specific caveats about the mapping
- SYNTHESIS.buckets:
    - high_overlap: paper_ids with coverage_level = 3 (if any)
    - partial_overlap: paper_ids with coverage_level = 2
    - adjacent: paper_ids with coverage_level = 1
- SYNTHESIS.papers: a list of per-paper entries, each with:
    - paper_id, title, year, venue, citation_count
    - overlap_level, overlap_bucket, facet_labels
    - key_roles: optional labels such as "top_cited_primary", "latest_primary", ...
    - intro_rw_summary: bullets that describe the domain, contributions, and positioning
    - intro_rw_full: (for key papers only) full Introduction / Related Work text
    - method_exp_snippets: short snippets from the Method / Experiment sections, grouped by facets

Your task is:

1. Carefully read the JSON.
2. Use SYNTHESIS.metadata.task to understand what the user actually wants.
3. Use the *summaries* as the main signal, and treat the full Intro/RW and method/experiment snippets
   as supporting evidence, not as something to copy verbatim.
4. Write a human-readable Markdown report for a researcher who wants to know:
   - What is the overall landscape of this topic?
   - Which papers are most similar to our idea?
   - Which papers are related but different, and how?
   - Where are the clear gaps and opportunities?

**VERY IMPORTANT STYLE & CONTENT INSTRUCTIONS**

- Do NOT hallucinate nonexistent papers.
- When you mention a paper, ALWAYS include its title and year in the text
  (e.g., "GUP Net (ICCV 2021)" or "Monocular 3D Object Detection via ... (CVPR 2020)").
- Focus on explaining *ideas and relationships*, not rewriting long method details.
- You may lightly quote phrases if needed, but avoid long verbatim quotes.
- Organize the report into clearly labelled sections and subsections.

**OUTPUT FORMAT (Markdown)**

Write your answer in English, using the following structure:

# 1. Problem Setting and Research Idea

- Restate the topic and idea using SYNTHESIS.metadata.topic and idea_description.
- Briefly paraphrase SYNTHESIS.metadata.task.user_query in your own words.
- Highlight what the user cares about most using SYNTHESIS.metadata.task.user_intent_notes.
- Explain the key facets F1, F2, ... in natural language.

# 2. Domain Overview

- Summarize the overall landscape of the field.
- Briefly mention how this mapping was built using SYNTHESIS.retrieval_summary
  (e.g., "This mapping is based on N papers retrieved from SemanticScholar/OpenAlex using queries focusing on ...").
- Use survey-like or highly cited papers (those with key_roles including "survey_primary"
  or "top_cited_primary") as anchors when possible.
- Group related works into thematic clusters (e.g., "geometry-based monocular 3D detection",
  "depth-estimation-assisted methods", "data augmentation for camera robustness", etc.).
- For each cluster, describe what these papers try to achieve and how.

# 3. Highly Similar Work (Potential Conflicts)

- Focus on papers in SYNTHESIS.buckets.high_overlap (if any).
- If this bucket is empty, explain that no clear high-overlap prior art was found,
  and use the most similar partial-overlap papers instead.
- For each such paper:
    - Summarize what it does.
    - Explain which idea facets it covers.
    - Explicitly compare it to our idea (similarities and differences).

# 4. Related Work with Partial Overlap

- Focus on papers in SYNTHESIS.buckets.partial_overlap.
- Optionally, group them by facet or theme (e.g., F2: depth estimation, F3/F4: augmentation).
- For each group, summarize the patterns and highlight 2–5 representative papers
  rather than listing everything exhaustively.

# 5. Adjacent and Broader Directions

- Use SYNTHESIS.buckets.adjacent to describe neighboring areas
  (e.g., LiDAR-based methods, multi-modal fusion, broader 3D perception).
- Explain how these works are conceptually related but do NOT directly solve the same problem.

# 6. Gaps, Opportunities, and Positioning of Our Idea

- Using the facet coverage statistics (SYNTHESIS.global_stats.facet_coverage) and your reading,
  explain which facets of our idea appear under-explored or missing in the literature.
- State clearly how our idea would fit into this landscape:
    - What is genuinely new?
    - What does it combine or extend from prior work?
    - Where are the strongest potential novelty claims?

# 7. Limitations of This Mapping

- Summarize SYNTHESIS.limitations in your own words.
- Mention any caveats about the retrieval coverage or potential LLM errors.
- Keep this section short (1 paragraph).

At the very top of your answer, include a one-paragraph "TL;DR" summarizing
whether there seems to be any obvious high-overlap prior art and what the most
important related directions are.
""".strip()

SHORT_RW_PROMPT = """
You are given a JSON object SYNTHESIS with metadata, facet definitions,
and a list of prior papers related to a research idea.

Write ONLY a "Related Work" section (without Introduction) in Markdown, with:

- 2–3 paragraphs on the overall landscape of the topic.
- 2–3 paragraphs on the most similar work (from high_overlap and partial_overlap).
- 1 paragraph on adjacent directions.

You MUST:
- Mention papers with their title and year.
- Explain similarities and differences to the target idea.
- Avoid long verbatim quotes.
- Briefly mention how papers were retrieved using SYNTHESIS.retrieval_summary (1-2 sentences).

Do not repeat the JSON itself. Just write the final text.
""".strip()


def make_prompt_templates() -> Dict[str, str]:
    """Generate prompt templates for LLM consumption."""
    return {
        "full_markdown_report": FULL_REPORT_PROMPT,
        "short_related_work_section": SHORT_RW_PROMPT,
    }


# =============================================================================
# Main Builder Function
# =============================================================================


def build_synthesis_input(
    state: PipelineState,
    config: PriorArtConfig,
    run_dir: Path,
    facets: Optional[List[Dict[str, Any]]] = None,
    user_query: Optional[str] = None,
    retrieval_queries: Optional[List[str]] = None,
    # 新增: 来自 coverage_classifier 的真值
    coverage_levels: Optional[Dict[str, int]] = None,
    # 新增: 来自 LLM scoring 的 match 信息 (包含 facet_labels)
    scored_matches: Optional[List[PriorArtMatch]] = None,
) -> Dict[str, Any]:
    """
    Consolidate all Block 1-3 results into a single synthesis_input.json.
    
    This JSON contains:
    - metadata: topic, idea, facets, run_info
    - global_stats: coverage counts, facet coverage
    - buckets: papers grouped by overlap level
    - papers: per-paper data with summaries, snippets, etc.
    - prompt_templates: prompts for heavy LLM
    
    Args:
        state: PipelineState with papers and intent
        config: PriorArtConfig
        run_dir: Path to the run directory
        facets: Optional list of facet dicts with id/label/category
        
    Returns:
        The synthesis_input dict (also written to disk)
    """
    logger.info("[Synthesis] Building synthesis_input.json...")
    
    synthesis_dir = run_dir / "synthesis"
    synthesis_dir.mkdir(parents=True, exist_ok=True)
    
    # -------------------------------------------------------------------------
    # 1. Build metadata
    # -------------------------------------------------------------------------
    topic = state.intent.topic if state.intent else "unknown topic"
    # Intent.extra_constraints may contain "idea" key, or we use topic as fallback
    idea_description = ""
    if state.intent:
        idea_description = state.intent.extra_constraints.get("idea", "")
    if not idea_description:
        idea_description = f"Research on {topic}"
    
    # Format facets
    facets_list = facets or []
    if not facets_list and state.intent:
        # Try to build facets from intent if available
        facets_list = _extract_facets_from_state(state)
    
    metadata = {
        "topic": topic,
        "idea_description": idea_description,
        "facets": facets_list,
        "task": {
            "task_type": "prior_art_check",
            "user_query": user_query or idea_description,
            "user_intent_notes": [
                "Identify papers with high overlap that may conflict with the idea.",
                "Map the landscape of related work for positioning.",
                "Find gaps and opportunities for novelty claims.",
            ],
            "constraints": {
                "target_venue": state.intent.extra_constraints.get("venue", "") if state.intent else "",
                "tone": "formal_research",
            },
        },
        "audience": "expert_researcher",
        "usage_context": ["paper_introduction", "related_work_section", "novelty_argument"],
        "run_info": {
            "run_id": run_dir.name,
            "created_at": datetime.now().isoformat() + "Z",
            "config": {
                "fulltext_top_k": getattr(config, 'top_k_fulltext_refine', 20),
                "enable_citation_graph": getattr(config, 'use_citation_graph', True),
                "debug_mode": config.debug_mode,
            }
        }
    }
    
    # -------------------------------------------------------------------------
    # 2. Build global_stats
    # -------------------------------------------------------------------------
    # 优先使用传入的 coverage_levels (来自 coverage_classifier)
    coverage_by_level = defaultdict(int)
    if coverage_levels:
        for pid, level in coverage_levels.items():
            coverage_by_level[level] += 1
    else:
        # Fallback: 尝试从 paper 对象读取 (向后兼容)
        for paper in state.papers.values():
            level = getattr(paper, 'coverage_level', 0) or 0
            coverage_by_level[level] += 1
    
    # 构建 scored_matches 映射 (用于获取 facet_labels 等)
    # 需要在 global_stats 之前构建，以便 facet_coverage 使用
    match_by_pid: Dict[str, PriorArtMatch] = {}
    if scored_matches:
        for m in scored_matches:
            match_by_pid[m.paper_id] = m
    
    global_stats = {
        "num_candidates": len(state.papers),
        "num_block3": sum(1 for p in state.papers.values() if p.chunks or getattr(p, 'has_fulltext', False)),
        "coverage_by_level": {
            "3": coverage_by_level.get(3, 0),
            "2": coverage_by_level.get(2, 0),
            "1": coverage_by_level.get(1, 0),
            "0": coverage_by_level.get(0, 0),
        },
        "facet_coverage": _build_facet_coverage_stats(facets_list, match_by_pid, coverage_levels),
    }
    
    # -------------------------------------------------------------------------
    # 3. Build buckets
    # -------------------------------------------------------------------------
    buckets = {
        "high_overlap": [],
        "partial_overlap": [],
        "adjacent": [],
        "ignored": [],
    }
    
    # 优先使用传入的 coverage_levels
    if coverage_levels:
        for pid in state.papers.keys():
            level = coverage_levels.get(pid, 0)
            if level >= 3:
                buckets["high_overlap"].append(pid)
            elif level == 2:
                buckets["partial_overlap"].append(pid)
            elif level == 1:
                buckets["adjacent"].append(pid)
            else:
                buckets["ignored"].append(pid)
    else:
        # Fallback
        for pid, paper in state.papers.items():
            level = getattr(paper, 'coverage_level', 0) or 0
            if level >= 3:
                buckets["high_overlap"].append(pid)
            elif level == 2:
                buckets["partial_overlap"].append(pid)
            elif level == 1:
                buckets["adjacent"].append(pid)
            else:
                buckets["ignored"].append(pid)
    
    # -------------------------------------------------------------------------
    # 3b. Build retrieval_summary
    # -------------------------------------------------------------------------
    query_seeds = retrieval_queries or [topic]
    if not retrieval_queries:
        # Build facet-based queries as fallback
        for facet in facets_list:
            label = facet.get("label", "")
            if label and label not in query_seeds:
                query_seeds.append(label)
    
    retrieval_summary = {
        "query_seeds": query_seeds[:10],  # Limit for readability
        "facet_queries": {
            facet.get("id", f"F{i+1}"): [facet.get("label", "")]
            for i, facet in enumerate(facets_list)
        },
        "sources": ["SemanticScholar", "OpenAlex", "arXiv", "Unpaywall"],
        "exploration_notes": {
            "num_candidates": len(state.papers),
            "num_block3": sum(1 for p in state.papers.values() if p.chunks),
            "year_range": _extract_year_range(state),
        }
    }
    
    # -------------------------------------------------------------------------
    # 3c. Build limitations
    # -------------------------------------------------------------------------
    num_missing_pdf = sum(1 for p in state.papers.values() if not p.chunks and not p.local_pdf_path)
    
    limitations = {
        "retrieval_coverage": "not_exhaustive",
        "llm_noise": True,
        "notes": [
            "Coverage levels and facet labels are assigned by LLMs and may contain errors.",
            "Non-English papers were not considered.",
        ]
    }
    if num_missing_pdf > 0:
        limitations["notes"].append(
            f"{num_missing_pdf} papers could not have PDFs downloaded; those may be underrepresented."
        )
    
    # -------------------------------------------------------------------------
    # 4. Load auxiliary data files
    # -------------------------------------------------------------------------
    
    # 4a. Load intro_rw_summaries.jsonl
    summaries_by_pid = _load_intro_rw_summaries(synthesis_dir)
    logger.debug(f"[Synthesis] Loaded {len(summaries_by_pid)} intro_rw_summaries")
    
    # 4b. Load method_exp_snippets.jsonl
    snippets_by_pid = _load_method_exp_snippets(synthesis_dir, config)
    logger.debug(f"[Synthesis] Loaded snippets for {len(snippets_by_pid)} papers")
    
    # 4c. Load key_papers_selection.json
    key_roles_by_pid = _load_key_paper_roles(synthesis_dir)
    logger.debug(f"[Synthesis] Loaded key roles for {len(key_roles_by_pid)} papers")
    
    # 4d. Load intro_rw_full JSONs
    intro_rw_full_by_pid = _load_intro_rw_full(synthesis_dir)
    logger.debug(f"[Synthesis] Loaded intro_rw_full for {len(intro_rw_full_by_pid)} papers")
    
    # -------------------------------------------------------------------------
    # 5. Build per-paper entries
    # -------------------------------------------------------------------------
    papers_list = []
    
    # match_by_pid 已在 global_stats 之前构建
    
    # Only include papers with some meaningful data (scored or fulltext)
    included_pids = set(buckets["high_overlap"] + buckets["partial_overlap"] + buckets["adjacent"])
    # Also include any paper with summaries or snippets
    included_pids.update(summaries_by_pid.keys())
    included_pids.update(snippets_by_pid.keys())
    included_pids.update(key_roles_by_pid.keys())
    
    for pid in included_pids:
        paper = state.papers.get(pid)
        if not paper:
            continue
        
        # 使用传入的 coverage_levels 获取 level
        level = coverage_levels.get(pid, 0) if coverage_levels else getattr(paper, 'coverage_level', 0) or 0
        
        # Determine overlap_bucket
        if level >= 3:
            overlap_bucket = "high_overlap"
        elif level == 2:
            overlap_bucket = "partial_overlap"
        elif level == 1:
            overlap_bucket = "adjacent"
        else:
            overlap_bucket = "ignored"
        
        # Get key_roles and determine intro_rw_policy
        key_roles = key_roles_by_pid.get(pid, [])
        has_full_intro_rw = pid in intro_rw_full_by_pid
        intro_rw_policy = "full" if has_full_intro_rw else "summary_only"
        
        # 从 scored_matches 获取评分信息
        match = match_by_pid.get(pid)
        
        # Build paper entry
        paper_entry = {
            "paper_id": pid,
            "title": paper.title or "",
            "year": paper.year or 0,
            "venue": paper.venue or "",
            "citation_count": paper.citation_count or 0,
            "overlap_level": level,
            "overlap_bucket": overlap_bucket,
            # 从 match 获取 facet_labels
            "facet_labels": match.covered_facet_labels if match and match.covered_facet_labels else [],
            # 从 match 获取相似度分数
            "task_similarity": match.task_similarity if match else 0.0,
            "method_similarity": match.method_similarity if match else 0.0,
            "key_roles": key_roles,
            "intro_rw_policy": intro_rw_policy,
        }
        
        # Add intro_rw_summary if available
        if pid in summaries_by_pid:
            paper_entry["intro_rw_summary"] = summaries_by_pid[pid]
        
        # Add intro_rw_full if available
        if has_full_intro_rw:
            paper_entry["intro_rw_full"] = intro_rw_full_by_pid[pid]
        
        # Add method_exp_snippets if available
        if pid in snippets_by_pid:
            paper_entry["method_exp_snippets"] = snippets_by_pid[pid]
        
        papers_list.append(paper_entry)
    
    # Sort papers by overlap_level (desc), then citation_count (desc)
    papers_list.sort(key=lambda x: (-x["overlap_level"], -x["citation_count"]))
    
    # -------------------------------------------------------------------------
    # 6. Assemble final structure
    # -------------------------------------------------------------------------
    synthesis_input = {
        "metadata": metadata,
        "global_stats": global_stats,
        "retrieval_summary": retrieval_summary,
        "limitations": limitations,
        "buckets": buckets,
        "papers": papers_list,
        "prompt_templates": make_prompt_templates(),
    }
    
    # -------------------------------------------------------------------------
    # 7. Write to disk
    # -------------------------------------------------------------------------
    output_path = synthesis_dir / "synthesis_input.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(synthesis_input, f, ensure_ascii=False, indent=2)
    
    logger.info(f"[Synthesis] Wrote synthesis_input.json to {output_path}")
    logger.info(f"[Synthesis] Stats: {len(papers_list)} papers, "
                f"high={len(buckets['high_overlap'])}, "
                f"partial={len(buckets['partial_overlap'])}, "
                f"adjacent={len(buckets['adjacent'])}")
    
    return synthesis_input


# =============================================================================
# Helper Functions
# =============================================================================


def _extract_facets_from_state(state: PipelineState) -> List[Dict[str, Any]]:
    """Extract facets from state if available."""
    facets = []
    # Try to get facets from various possible locations
    if hasattr(state, 'facets') and state.facets:
        for i, f in enumerate(state.facets):
            if isinstance(f, str):
                facets.append({
                    "id": f"F{i+1}",
                    "label": f,
                    "category": "general",
                })
            elif isinstance(f, dict):
                facets.append({
                    "id": f.get("id", f"F{i+1}"),
                    "label": f.get("label", str(f)),
                    "category": f.get("category", "general"),
                })
    return facets


def _extract_year_range(state: PipelineState) -> str:
    """Extract year range from papers in state."""
    years = [p.year for p in state.papers.values() if p.year]
    if not years:
        return "unknown"
    return f"{min(years)}-{max(years)}"


def _build_facet_coverage_stats(
    facets: List[Dict[str, Any]],
    match_by_pid: Dict[str, PriorArtMatch],
    coverage_levels: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Build facet coverage statistics from scored_matches."""
    facet_stats = []
    
    # Count papers covering each facet
    for facet in facets:
        facet_id = facet.get("id", "")
        facet_label = facet.get("label", "")
        
        strong_papers = 0
        total_papers = 0
        
        for pid, match in match_by_pid.items():
            covered_labels = match.covered_facet_labels or []
            
            # 模糊匹配: 检查 facet_id、facet_label、或标签中是否包含关键词
            matched = False
            if facet_id in covered_labels:
                matched = True
            elif facet_label.lower() in [l.lower() for l in covered_labels]:
                matched = True
            else:
                # 关键词模糊匹配
                facet_keywords = set(facet_label.lower().split())
                for label in covered_labels:
                    label_words = set(label.lower().split())
                    # 如果有 2+ 个词重叠，认为匹配
                    if len(facet_keywords & label_words) >= 2:
                        matched = True
                        break
            
            if matched:
                total_papers += 1
                # 使用 coverage_levels 确定 strong
                level = coverage_levels.get(pid, 0) if coverage_levels else match.coverage_level
                if level >= 2:
                    strong_papers += 1
        
        facet_stats.append({
            "facet_id": facet_id,
            "facet_label": facet_label,
            "strong_papers": strong_papers,
            "total_papers": total_papers,
        })
    
    return facet_stats


def _load_intro_rw_summaries(synthesis_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Load intro_rw_summaries.jsonl and index by paper_id."""
    summaries = {}
    summaries_path = synthesis_dir / "intro_rw_summaries.jsonl"
    
    if not summaries_path.exists():
        return summaries
    
    try:
        with open(summaries_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                pid = obj.get("paper_id")
                if pid:
                    # Extract relevant fields for the synthesis
                    summaries[pid] = {
                        "is_survey": obj.get("is_survey", False),
                        "domain_overview_bullets": obj.get("domain_overview_bullets", []),
                        "contribution_bullets": obj.get("contribution_bullets", []),
                        "positioning_bullets": obj.get("positioning_bullets", []),
                        "bullets_with_snippets": obj.get("bullets_with_snippets", []),
                    }
    except Exception as e:
        logger.warning(f"[Synthesis] Failed to load intro_rw_summaries: {e}")
    
    return summaries


def _load_method_exp_snippets(
    synthesis_dir: Path,
    config: PriorArtConfig,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load method_exp_snippets.jsonl and group by paper_id."""
    snippets_by_pid: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    snippets_path = synthesis_dir / "method_exp_snippets.jsonl"
    
    if not snippets_path.exists():
        return dict(snippets_by_pid)
    
    try:
        with open(snippets_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                pid = obj.get("paper_id")
                if pid:
                    # Extract relevant fields
                    snippet_entry = {
                        "section_role": obj.get("section_role", ""),
                        "facet_label": obj.get("facet_label", ""),
                        "bullet": obj.get("bullet", ""),
                        "snippet": obj.get("snippet", ""),
                        "loc": obj.get("loc", ""),
                    }
                    snippets_by_pid[pid].append(snippet_entry)
    except Exception as e:
        logger.warning(f"[Synthesis] Failed to load method_exp_snippets: {e}")
    
    # Apply max snippets limit per paper
    max_snippets = config.max_snippets_per_paper_facet * 3  # Allow more total per paper
    result = {}
    for pid, snippets in snippets_by_pid.items():
        result[pid] = snippets[:max_snippets]
    
    return result


def _load_key_paper_roles(synthesis_dir: Path) -> Dict[str, List[str]]:
    """Load key_papers_selection.json and extract per_paper_roles."""
    roles = {}
    selection_path = synthesis_dir / "key_papers_selection.json"
    
    if not selection_path.exists():
        return roles
    
    try:
        with open(selection_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        roles = data.get("per_paper_roles", {})
    except Exception as e:
        logger.warning(f"[Synthesis] Failed to load key_papers_selection: {e}")
    
    return roles


def _load_intro_rw_full(synthesis_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Load all intro_rw_full/{paper_id}.json files."""
    full_data = {}
    intro_rw_dir = synthesis_dir / "intro_rw_full"
    
    if not intro_rw_dir.exists():
        return full_data
    
    try:
        for json_file in intro_rw_dir.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            pid = data.get("paper_id")
            if pid:
                # Extract only the sections part for embedding
                sections = data.get("sections", {})
                full_data[pid] = sections
    except Exception as e:
        logger.warning(f"[Synthesis] Failed to load intro_rw_full files: {e}")
    
    return full_data
