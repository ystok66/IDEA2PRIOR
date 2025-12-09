"""
Prior Art Check - Scoring Module

This module contains functions for paper scoring and analysis:
- Facet extraction and decomposition
- Structural overlap computation
- High-level facet analysis
- Relevance keyword generation and filtering
- Abstract-level scoring
- Fulltext enrichment and refinement

These functions are used by the main prior_art_check orchestrator.
"""

from __future__ import annotations

import json
import math
import re
import ast
import os
import requests
from pathlib import Path
from typing import List, Dict, Set, Any, Optional, Tuple, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger
from smolagents import LiteLLMModel
from rank_bm25 import BM25Okapi

from pipelines.state import PipelineState, Paper, Chunk
from pipelines.run_manager import RunManager
from pipelines.sections import save_paper_sections
from pipelines.llm_config import get_litellm_config
from tools.paper_resolver import PaperResolverTool
from tools.pdf_parser import PdfParserTool

from pipelines.tasks.prior_art_types import (
    PriorArtConfig, PriorArtMatch,
    HIGH_LEVEL_FACET_TYPES, GENERIC_STOPWORDS,
    get_max_abstract_to_score,
)
from pipelines.tasks.prior_art_llm_utils import (
    extract_text_from_llm_response,
    repair_json_output,
    parse_json_with_repair,
)


# =============================================================================
# LLM Helpers and Facet Extraction
# =============================================================================



# Need math for log in scoring
import math


def call_llm_json(agent_or_model, prompt: str) -> str:
    """
    Use the underlying chat model directly, bypassing CodeAgent.run().
    Assumes `agent.model` is a LiteLLMModel-compatible chat model.
    """
    messages = [{"role": "user", "content": prompt}]
    # smolagents.LiteLLMModel supports direct __call__(messages) -> str
    if hasattr(agent_or_model, "model"):
        return agent_or_model.model(messages)
    return agent_or_model(messages)


def decompose_idea_into_facets(agent, idea_description: str) -> List[str]:
    """
    Use the LLM to decompose the idea into 3-7 key technical facets.

    Each facet should be a short phrase describing a distinct element of the idea.
    
    Uses 'light' LLM role for efficiency.
    """
    logger.info("[PriorArt] Decomposing idea into facets...")
    
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

Given the following research idea, please extract 3-7 key technical facets that characterize this idea.

A "facet" is a distinct technical component, method, assumption, or contribution of the idea.

Research Idea:
{idea_description}

Please output ONLY a JSON list of strings, where each string is one facet. Example format:
["facet 1", "facet 2", "facet 3"]

JSON output:"""

    try:
        messages = [{"role": "user", "content": prompt}]
        result = light_model(messages)
        result_str = extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw facet output (cleaned): {result_str[:1000]!r}")
        
        facets: List[str] = []
        try:
            parsed = json.loads(result_str)
        except Exception:
            try:
                parsed = ast.literal_eval(result_str)
            except Exception:
                logger.warning("[PriorArt] Failed to parse facets as JSON/AST, using line-split fallback")
                # Very last-resort fallback: try to get one facet per line
                for line in result_str.splitlines():
                    line = line.strip().strip("-•* \t")
                    if not line:
                        continue
                    facets.append(line)
            else:
                # ast.literal_eval succeeded
                if isinstance(parsed, list):
                    facets = [str(x).strip() for x in parsed if str(x).strip()]
                else:
                    facets = [str(parsed).strip()]
        else:
            # json.loads succeeded
            if isinstance(parsed, list):
                facets = [str(x).strip() for x in parsed if str(x).strip()]
            else:
                facets = [str(parsed).strip()]

        logger.info(f"[PriorArt] Extracted {len(facets)} facets from idea")
        return facets[:7]
        
    except Exception as e:
        logger.error(f"Failed to decompose idea into facets: {e}")
    
    # Ultimate fallback: use the whole idea as a single facet
    return [idea_description[:100]]


def extract_idea_features(agent, idea_description: str) -> Dict[str, Set[str]]:
    """
    Extract structural features (datasets, metrics, method families) from the idea description.
    
    Uses 'light' LLM role for efficiency.
    """
    logger.info("[PriorArt] Extracting structural features from idea...")
    
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

Given the following research idea, please extract the key structural features:
1. Datasets (e.g., KITTI, nuScenes, ImageNet)
2. Metrics (e.g., mAP, F1, Accuracy)
3. Method Families (e.g., Transformer, CNN, Diffusion)

Research Idea:
{idea_description}

Please output ONLY a JSON object with keys "datasets", "metrics", "method_families". Each value should be a list of strings.
Example: {{"datasets": ["KITTI"], "metrics": ["mAP"], "method_families": ["Transformer"]}}

JSON output:"""

    try:
        messages = [{"role": "user", "content": prompt}]
        result = light_model(messages)
        result_str = extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw idea-struct output (cleaned): {result_str[:1000]!r}")
        
        datasets: Set[str] = set()
        metrics: Set[str] = set()
        families: Set[str] = set()
        
        data = None
        try:
            data = json.loads(result_str)
        except Exception:
            try:
                data = ast.literal_eval(result_str)
            except Exception:
                logger.warning("[PriorArt] Could not parse idea structural features, returning empty sets.")
                return {"datasets": set(), "metrics": set(), "method_families": set()}
        
        if isinstance(data, dict):
            for name, target in [
                ("datasets", datasets),
                ("metrics", metrics),
                ("method_families", families),
            ]:
                if name in data and isinstance(data[name], list):
                    for item in data[name]:
                        s = str(item).strip()
                        if s:
                            target.add(s.lower())
            
            logger.info(
                f"[PriorArt] Idea features parsed: "
                f"datasets={datasets}, metrics={metrics}, methods={families}"
            )
            return {
                "datasets": datasets,
                "metrics": metrics,
                "method_families": families,
            }
        
        logger.warning("[PriorArt] Parsed idea features is not a dict, returning empty sets.")
        
    except Exception as e:
        logger.warning(f"[PriorArt] Failed to extract idea features: {e}")
    
    return {"datasets": set(), "metrics": set(), "method_families": set()}


def compute_structural_overlap(idea_features: Dict[str, Set[str]], paper: Paper) -> Tuple[int, List[str]]:
    """
    Compute structural overlap score (0-3) and return details.
    """
    score = 0
    details = []
    
    # Check Datasets
    paper_datasets = set(d.lower() for d in paper.datasets)
    overlap_datasets = idea_features["datasets"] & paper_datasets
    if overlap_datasets:
        score += 1
        details.append(f"Datasets: {', '.join(overlap_datasets)}")
        
    # Check Metrics
    paper_metrics = set(m.lower() for m in paper.metrics)
    overlap_metrics = idea_features["metrics"] & paper_metrics
    if overlap_metrics:
        score += 1
        details.append(f"Metrics: {', '.join(overlap_metrics)}")
        
    # Check Method Families
    paper_families = set(f.lower() for f in paper.method_families)
    overlap_families = idea_features["method_families"] & paper_families
    if overlap_families:
        score += 1
        details.append(f"Methods: {', '.join(overlap_families)}")
        
    return score, details


def extract_high_level_facets_from_idea(
    agent,
    idea_text: str,
) -> Dict[str, Set[str]]:
    """
    Extract high-level structural facets from the idea description.
    
    Uses 'light' LLM role for efficiency.
    """
    logger.info("[PriorArt] Extracting high-level facets from idea...")
    
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.
Do NOT include any comments, trailing commas, or extra tokens. Only output valid JSON.

Given the following research idea, please extract high-level structural facets.
    
Facet Types:
1. problem_facets: What specific problem/task is it solving? (e.g., "monocular 3D detection", "long-horizon sequence modeling")
2. setting_facets: What is the setting, modality, or protocol? (e.g., "indoor RGB-D scenes", "multi-camera surround view")
3. mechanism_facets: What are the key mechanisms or frameworks? (e.g., "camera-space rotation augmentation", "SSM-based state space modeling")
4. assumption_facets: What are the key assumptions? (e.g., "calibrated pinhole camera", "no depth supervision")

Research Idea:
{idea_text}

Please output ONLY a JSON object with keys "problem_facets", "setting_facets", "mechanism_facets", "assumption_facets".
Each value should be a list of short phrases (3-7 phrases max per category).

JSON output:"""

    try:
        messages = [{"role": "user", "content": prompt}]
        result = light_model(messages)
        result_str = extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw high-level facets (idea, cleaned): {result_str[:1000]!r}")
        
        data = None
        try:
            # Try standard parsing first
            data = json.loads(result_str)
        except Exception:
            try:
                # Try AST literal eval
                data = ast.literal_eval(result_str)
            except Exception:
                # Fallback: Try to extract JSON-like object via regex if full parse fails
                import re
                match = re.search(r"\{.*\}", result_str, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(0))
                    except:
                        pass
                
                if not data:
                    logger.warning("[PriorArt] Failed to parse high-level facets from idea, using empty sets.")
                    return {ftype: set() for ftype in HIGH_LEVEL_FACET_TYPES}
        
        if isinstance(data, dict):
            facets: Dict[str, Set[str]] = {}
            for ftype in HIGH_LEVEL_FACET_TYPES:
                raw_vals = data.get(ftype, []) or []
                facets[ftype] = {
                    str(p).lower().strip()
                    for p in raw_vals
                    if str(p).strip()
                }
            logger.info(f"[PriorArt] Parsed idea high-level facets: {facets}")
            return facets
            
        logger.warning("[PriorArt] High-level idea facets structure is not a dict, using empty sets.")
        
    except Exception as e:
        logger.warning(f"Failed to extract high-level facets from idea: {e}")
    
    return {ftype: set() for ftype in HIGH_LEVEL_FACET_TYPES}


def _repair_json_output(raw: str) -> str:
    """
    Attempt to repair common LLM JSON output issues.
    
    Handles:
    - Text before/after the JSON object
    - Trailing commas before } or ]
    - Single-line // comments
    - Unquoted newlines in strings (basic)
    """
    import re
    
    # Strip everything before first { and after last }
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1:
        return raw
    trimmed = raw[start:end+1]
    
    # Remove single-line // comments (but not inside strings - rough heuristic)
    trimmed = re.sub(r'//[^\n"]*(?=\n|$)', '', trimmed)
    
    # Remove trailing commas before } or ]
    trimmed = re.sub(r',\s*([}\]])', r'\1', trimmed)
    
    # Remove control characters that break JSON
    trimmed = re.sub(r'[\x00-\x1f\x7f]', ' ', trimmed)
    
    return trimmed


def extract_high_level_facets_from_paper(
    agent,
    paper: Paper,
) -> Dict[str, List[str]]:
    """
    Extract high-level structural facets from a paper (using title/abstract/chunks).
    Always returns Dict[str, List[str]] for compatibility with Paper model.
    """
    # Use light model for facet extraction
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    # Prepare context
    context = f"Title: {paper.title}\n"
    
    # Add a bit of chunk text if available
    if paper.chunks:
        # Use first chunk (intro usually)
        context += f"Intro Snippet: {paper.chunks[0].text[:1000]}\n"
        
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

CRITICAL: Output ONLY valid JSON. No markdown, no comments, no trailing commas, no text before or after.

Given the following paper info, please extract high-level structural facets.

Facet Types:
1. problem_facets: What specific problem/task is it solving?
2. setting_facets: What is the setting, modality, or protocol?
3. mechanism_facets: What are the key mechanisms or frameworks?
4. assumption_facets: What are the key assumptions?

Paper Info:
{context}

Please output ONLY a JSON object with keys "problem_facets", "setting_facets", "mechanism_facets", "assumption_facets".
Each value should be a list of short phrases.

JSON output:"""

    try:
        messages = [{"role": "user", "content": prompt}]
        resp = light_model(messages)
        result_str = extract_text_from_llm_response(resp)
        logger.debug(
            f"[PriorArt] Raw high-level facets (paper={paper.title[:60]!r}, cleaned): "
            f"{result_str[:1000]!r}"
        )
        
        # 3-layer parsing: direct -> repair -> literal_eval
        data = None
        
        # Layer 1: Direct json.loads
        try:
            data = json.loads(result_str)
        except Exception:
            pass
        
        # Layer 2: Try with JSON repair
        if data is None:
            try:
                repaired = _repair_json_output(result_str)
                data = json.loads(repaired)
                logger.debug(f"[PriorArt] JSON repair succeeded for paper {paper.title[:50]!r}")
            except Exception:
                pass
        
        # Layer 3: Fallback to ast.literal_eval
        if data is None:
            try:
                data = ast.literal_eval(result_str)
            except Exception:
                logger.warning(
                    f"[PriorArt] Failed to parse high-level facets for paper {paper.title!r}, using empty lists."
                )
                return {ftype: [] for ftype in HIGH_LEVEL_FACET_TYPES}
        
        if isinstance(data, dict):
            facets: Dict[str, List[str]] = {}
            for ftype in HIGH_LEVEL_FACET_TYPES:
                raw_vals = data.get(ftype, []) or []
                clean_vals: List[str] = []
                for v in raw_vals:
                    s = str(v).strip()
                    if s:
                        clean_vals.append(s)
                facets[ftype] = clean_vals
            return facets

        logger.warning(
            f"[PriorArt] High-level facets for paper {paper.title!r} is not a dict, using empty lists."
        )

    except Exception as e:
        logger.warning(f"Failed to extract high-level facets from paper {paper.title}: {e}")
        
    return {ftype: [] for ftype in HIGH_LEVEL_FACET_TYPES}



def compute_high_level_facet_overlap(
    idea_facets: Dict[str, Set[str]],
    paper_facets: Dict[str, List[str]],
) -> Tuple[int, Dict[str, List[str]]]:
    """
    Compute overlap score (0-3) and details for high-level facets.
    """
    score = 0
    details = {}
    
    # Convert paper facets to sets for comparison
    paper_facets_sets = {k: set(v.lower() if isinstance(v, str) else str(v).lower() for v in vals) 
                        for k, vals in paper_facets.items()}
    
    # Check overlap for each type
    has_major_overlap = False
    has_minor_overlap = False
    
    for ftype in HIGH_LEVEL_FACET_TYPES:
        overlap = idea_facets.get(ftype, set()) & paper_facets_sets.get(ftype, set())
        if overlap:
            details[ftype] = list(overlap)
            if ftype in ["problem_facets", "setting_facets", "mechanism_facets"]:
                has_major_overlap = True
            else:
                has_minor_overlap = True
    
    # Simple scoring logic
    if len(details) >= 3 and has_major_overlap:
        score = 3
    elif has_major_overlap:
        score = 2
    elif has_minor_overlap:
        score = 1
        
    return score, details


def generate_relevance_keywords_from_idea(
    agent,
    topic: str,
    idea: str,
    facets: List[str],
    max_keywords: int = 20,
) -> List[str]:
    """
    Use the LLM once to propose loose relevance keywords / phrases
    to help pre-filter obviously irrelevant papers.
    
    Uses 'expert' LLM role for deep semantic understanding.
    """
    # Instantiate expert model
    model_id, api_base, api_key = get_litellm_config(role="expert")
    expert_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    prompt = f"""You are a research assistant helping to filter academic papers.
We are looking for prior art for the following idea:
Topic: {topic}
Idea: {idea}
Key Facets: {json.dumps(facets)}

Please generate a list of {max_keywords} relevance keywords or phrases.
These keywords will be used to score papers (title + abstract) to filter out OBVIOUSLY irrelevant ones (e.g., NLP, Metaverse, Materials Science).

Guidelines:
- Include specific technical terms (e.g., "monocular 3d detection", "rotation augmentation").
- Include broader domain cues (e.g., "3d vision", "point cloud", "lidar", "rgb-d", "autonomous driving").
- Include variations (e.g., "3d object detection", "3d detection").
- The goal is RECALL. If a paper contains these words, it might be relevant.

Output strictly valid JSON: ["keyword1", "keyword2", ...]
Do not output markdown code blocks.
"""
    # Use expert_model instead of agent
    try:
        resp = call_llm_json(expert_model, prompt)
        text = extract_text_from_llm_response(resp)
        keywords = json.loads(text)
        if isinstance(keywords, list):
            return [str(k).lower().strip() for k in keywords]
    except Exception as e:
        logger.warning(f"Failed to parse relevance keywords: {e}")
    
    # Fallback
    return [topic.lower()] + [f.lower() for f in facets]


def simple_relevance_score(
    paper_title: str,
    paper_abstract: str,
    keywords: List[str],
    strong_cues: Optional[List[str]] = None,
) -> float:
    """
    Compute a cheap heuristic relevance score using keyword matches.

    This function must remain domain-agnostic. Do not hard-code 3D vision / NLP / RL
    specific keywords here. Use the `keywords` / `strong_cues` arguments to specialize.
    
    Applies penalty for generic terms (surveys, tutorials, etc.) to prevent
    broad overview papers from dominating the results.
    """
    score = 0.0
    title_norm = paper_title.lower() if paper_title else ""
    abstract_norm = paper_abstract.lower() if paper_abstract else ""
    
    # Use provided strong cues or default to empty
    cues = strong_cues if strong_cues is not None else []
    
    for cue in cues:
        # Strong cues are worth more
        if cue in title_norm:
            score += 2.0
        if cue in abstract_norm:
            score += 0.5
            
    for kw in keywords:
        if kw in title_norm:
            score += 3.0 # Keyword in title is strong
        if kw in abstract_norm:
            score += 1.0
    
    # Penalty for generic terms in title (surveys, reviews, etc.)
    for stopword in GENERIC_STOPWORDS:
        if stopword in title_norm:
            score -= 1.5  # Penalty for generic title
            break  # Only penalize once
    
    # Additional penalty for "survey" or "review" in title
    if "survey" in title_norm or "review" in title_norm:
        score -= 2.0
            
    return max(0.0, score)  # Don't go below 0


def prefilter_papers_for_llm_scoring(
    papers: List[Tuple[str, Paper]], # list of (id, Paper)
    keywords: List[str],
    min_keep: int,
    max_keep: int,
) -> List[Tuple[str, Paper]]:
    """
    Stage 1: Apply a lightweight lexical pre-filter using simple_relevance_score.
    """
    # Use top k keywords as strong cues default
    strong_cues = keywords[:10] if keywords else []

    scored_papers = []
    for pid, p in papers:
        s = simple_relevance_score(p.title, p.abstract, keywords, strong_cues=strong_cues)
        scored_papers.append((s, pid, p))
        
    # Sort descending by score
    scored_papers.sort(key=lambda x: x[0], reverse=True)
    
    kept = scored_papers[:max_keep]
    
    # Log detailed stats
    if scored_papers:
        top_score = scored_papers[0][0]
        # Avoid index error if kept is empty (unlikely given min_keep)
        lowest_kept_score = kept[-1][0] if kept else 0.0
        
        logger.info(
            f"[PreFilter-Lexical] Input: {len(papers)} papers. Kept: {len(kept)}. "
            f"Score Threshold (lowest kept): {lowest_kept_score:.2f} (Top: {top_score:.2f})"
        )
        
        if len(scored_papers) > max_keep:
            dropped_count = len(scored_papers) - len(kept)
            best_dropped_score = scored_papers[max_keep][0]
            logger.info(f"[PreFilter-Lexical] Dropped {dropped_count} papers. Best dropped score: {best_dropped_score:.2f}")
            
    return [(pid, p) for _, pid, p in kept]


def llm_relevance_prefilter(
    papers: List[Tuple[str, Paper]],
    topic: str,
    idea_description: str,
    max_workers: int = 1,
) -> List[Tuple[str, Paper]]:
    """
    Stage 2: Use a light LLM to do quick true/false relevance filtering.
    
    Args:
        papers: List of (paper_id, Paper) tuples from Stage 1
        topic: Research topic string
        idea_description: Idea being checked for prior art
        max_workers: Parallel workers (1 for serial/debug mode)
    
    Returns:
        Filtered list of papers where LLM judged is_relevant=true
    """
    if not papers:
        return papers
    
    logger.info(f"[PreFilter-LLM] Checking {len(papers)} papers for topic relevance...")
    
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    def _check_single_paper(pid_paper: Tuple[str, Paper]) -> Tuple[str, Paper, bool]:
        """Check if a single paper is potentially relevant."""
        pid, paper = pid_paper
        prompt = f"""You are an academic research assistant. Quickly determine if this paper might be relevant to the research topic below.

Research Topic: {topic}
Research Idea: {idea_description[:500]}

Paper Title: {paper.title}
Paper Abstract: {(paper.abstract or "N/A")[:800]}

Is this paper potentially relevant to the research topic/idea above?
Only output valid JSON, no explanation:
{{"is_relevant": true}} or {{"is_relevant": false}}
"""
        try:
            messages = [{"role": "user", "content": prompt}]
            resp = light_model(messages)
            text = extract_text_from_llm_response(resp)
            result = json.loads(text)
            is_relevant = result.get("is_relevant", True)  # Default to true if parse fails
            return (pid, paper, is_relevant)
        except Exception as e:
            logger.debug(f"[PreFilter-LLM] Error checking {paper.title[:40]}: {e}")
            return (pid, paper, True)  # Keep paper on error
    
    results = []
    
    if max_workers <= 1:
        # Serial execution
        for pid_paper in papers:
            results.append(_check_single_paper(pid_paper))
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_paper = {executor.submit(_check_single_paper, pp): pp for pp in papers}
            for fut in as_completed(future_to_paper):
                try:
                    results.append(fut.result())
                except Exception as e:
                    pid, paper = future_to_paper[fut]
                    logger.warning(f"[PreFilter-LLM] Error: {e}")
                    results.append((pid, paper, True))
    
    # Filter to relevant papers
    relevant_papers = [(pid, p) for pid, p, is_relevant in results if is_relevant]
    dropped_count = len(papers) - len(relevant_papers)
    
    logger.info(
        f"[PreFilter-LLM] Kept {len(relevant_papers)}/{len(papers)} papers. "
        f"Dropped {dropped_count} as not relevant."
    )
    
    return relevant_papers


def score_papers_with_abstract_only(
    agent, 
    state: PipelineState, 
    facets: List[str], 
    idea_features: Dict[str, Set[str]], 
    idea_high_level_facets: Dict[str, Set[str]],
    idea_description: str = "",
    cfg: Optional[PriorArtConfig] = None,
    stage: str = "initial",
) -> List[PriorArtMatch]:
    """
    Cheap first-stage scoring: use only title/venue/year/citations
    and abstract (no fulltext chunks).

    This function should:
    - Rank candidate papers based on citation_count (or other signals).
    - For top `max_papers`, ask the LLM to assign a coverage_level (0-3)
      and map covered facet labels.
    - Compute structural and high-level facet overlap, as in the current
      `score_papers_against_idea`.

    The output is a list of PriorArtMatch objects, sorted by:
      coverage_level, high_level_facet_score, structural_score, citations.
    """
    # Determine max papers to score
    if cfg:
        max_papers = get_max_abstract_to_score(cfg, stage)
    else:
        max_papers = 80 # Default fallback

    logger.info(f"[PriorArt] Scoring top {max_papers} papers (abstract-only) [Stage: {stage}]...")
    
    # Instantiate light model for scoring
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    # Sort papers by citation count
    all_papers = list(state.papers.items())
    all_papers.sort(key=lambda x: x[1].citation_count or 0, reverse=True)

    # Two-stage pre-filter logic
    # Stage 1: Lexical scoring with generic-word penalty
    if cfg and cfg.enable_prefilter and len(all_papers) > cfg.prefilter_min_papers:
        logger.info(
            "[PriorArt] Stage 1: Applying lexical pre-filter: "
            f"{len(all_papers)} candidate papers -> at most {cfg.prefilter_max_keep}."
        )
        keywords = generate_relevance_keywords_from_idea(
            agent=agent,
            topic=state.intent.topic,
            idea=idea_description,
            facets=facets,
            max_keywords=cfg.prefilter_max_keywords,
        )
        logger.info(f"[PriorArt] Generated relevance keywords: {keywords[:10]}...")  # Log first 10
        
        all_papers = prefilter_papers_for_llm_scoring(
            papers=all_papers,
            keywords=keywords,
            min_keep=10,
            max_keep=cfg.prefilter_max_keep,
        )
    
    # Stage 2: Light LLM true/false relevance filter
    if cfg and cfg.enable_llm_prefilter:
        llm_top_n = cfg.debug_llm_prefilter_top_n if cfg.debug_mode else cfg.llm_prefilter_top_n
        papers_for_llm_filter = all_papers[:llm_top_n]
        
        if len(papers_for_llm_filter) > 10:  # Only if we have enough papers to filter
            logger.info(
                f"[PriorArt] Stage 2: Applying LLM relevance filter to top {len(papers_for_llm_filter)} papers."
            )
            max_workers = 1 if cfg.debug_mode else 4
            filtered_papers = llm_relevance_prefilter(
                papers=papers_for_llm_filter,
                topic=state.intent.topic,
                idea_description=idea_description,
                max_workers=max_workers,
            )
            # Combine filtered papers with remaining papers (beyond llm_top_n)
            all_papers = filtered_papers + all_papers[llm_top_n:]
    
    # Limit to top max_papers
    papers_to_score = all_papers[:max_papers]
    
    matches = []
    
    facets_str = "\n".join([f"F{i+1}. {facet}" for i, facet in enumerate(facets)])
    
    total_to_score = len(papers_to_score)
    log_interval = cfg.progress_log_interval if cfg else 5

    for idx, (paper_id, paper) in enumerate(papers_to_score, start=1):
        # Progress logging
        if idx == 1 or idx % log_interval == 0 or idx == total_to_score:
            logger.info(f"[PriorArt] Abstract scoring progress: {idx}/{total_to_score} papers processed.")

        logger.debug(f"[PriorArt] Scoring paper: {paper.title} (id={paper_id})")
        
        # Extract high-level facets for paper (lazy extraction for top papers)
        if not paper.high_level_facets:
            paper.high_level_facets = extract_high_level_facets_from_paper(agent, paper)
            
        # Build paper context (Abstract Only)
        paper_context = f"""Title: {paper.title}
Venue: {paper.venue or 'Unknown'}
Year: {paper.year or 'Unknown'}
Citation Count: {paper.citation_count or 0}"""
        
        # Add abstract if available
        if hasattr(paper, 'abstract') and paper.abstract:
            paper_context += f"\nAbstract: {paper.abstract[:1000]}"  # Increased limit for abstract
        else:
            paper_context += "\nAbstract: N/A"
        
        prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

Given the following research idea facets and a paper, rate how much this paper overlaps with the idea.

Idea Facets:
{facets_str}

Paper:
{paper_context}

Please output a JSON object with:
- "coverage_level": integer 0-3 where:
  0 = no overlap
  1 = adjacent work (same domain but different approach)
  2 = partial overlap (covers some facets)
  3 = high overlap (covers most/all facets, potential conflict)
- "covered_facets": list of facet labels (e.g., ["F1", "F3"]) that this paper covers
- "task_similarity": float 0.0-1.0 how similar the task/problem addressed is
- "method_similarity": float 0.0-1.0 how similar the method/approach is
- "notes": brief justification (1-2 sentences)

JSON output:"""

        try:
            messages = [{"role": "user", "content": prompt}]
            resp = light_model(messages)
            result_str = extract_text_from_llm_response(resp)
            logger.debug(f"[PriorArt] Raw scoring output for {paper.title[:30]}... (cleaned): {result_str[:500]!r}")
            
            score_data = None
            try:
                score_data = json.loads(result_str)
            except Exception:
                try:
                    score_data = ast.literal_eval(result_str)
                except Exception:
                    logger.warning(f"[PriorArt] Failed to parse scoring JSON for {paper.title}, fallback to 0.")
                    score_data = None
            
            if isinstance(score_data, dict):
                coverage_level = int(score_data.get("coverage_level", 0))
                # Raw labels from LLM, e.g. ["F1", "F3"]
                covered_facet_labels = score_data.get("covered_facets", []) or []
                notes = score_data.get("notes", "")
                
                # Extract similarity scores
                task_sim = float(score_data.get("task_similarity", 0.0))
                method_sim = float(score_data.get("method_similarity", 0.0))
                
                # Map facet labels (F1, F2, ...) to human-readable facet text
                covered_facets: List[str] = []
                for label in covered_facet_labels:
                    if isinstance(label, str) and label.startswith("F"):
                        try:
                            idx = int(label[1:]) - 1
                            if 0 <= idx < len(facets):
                                covered_facets.append(facets[idx])
                        except ValueError:
                            continue
                
                matches.append(PriorArtMatch(
                    paper_id=paper_id,
                    coverage_level=coverage_level,
                    covered_facet_labels=[str(l).strip() for l in covered_facet_labels],
                    covered_facets=covered_facets,
                    notes=notes,
                    task_similarity=task_sim,
                    method_similarity=method_sim,
                ))
                
                # Compute structural score
                struct_score, struct_details = compute_structural_overlap(idea_features, paper)
                matches[-1].structural_score = struct_score
                matches[-1].structural_overlap = struct_details
                
                # Compute high-level facet score
                hl_score, hl_details = compute_high_level_facet_overlap(idea_high_level_facets, paper.high_level_facets)
                matches[-1].high_level_facet_score = hl_score
                matches[-1].high_level_facet_matches = hl_details
                
                logger.info(f"[PriorArt] Finished scoring {paper.title}: level={coverage_level}, task_sim={task_sim:.2f}")
            else:
                # Fallback
                logger.warning(f"[PriorArt] Failed to parse scoring JSON for {paper.title}, fallback to 0.")
                matches.append(PriorArtMatch(
                    paper_id=paper_id,
                    coverage_level=0,
                    covered_facet_labels=[],
                    covered_facets=[],
                    notes="Failed to parse LLM output.",
                ))
                
        except Exception as e:
            logger.warning(f"Failed to score paper {paper.title}: {e}")
            matches.append(PriorArtMatch(
                paper_id=paper_id,
                coverage_level=0,
                covered_facet_labels=[],
                covered_facets=[],
                notes=f"Scoring error: {str(e)[:50]}",
            ))
    
    # Sort by coverage_level (desc), then similarity, then citation_count
    # This prioritizes papers with similar tasks/methods over high-citation surveys
    matches.sort(
        key=lambda m: (
            m.coverage_level,
            m.task_similarity,             # Task similarity (new)
            m.method_similarity,           # Method similarity (new)
            m.high_level_facet_score,
            min(state.papers[m.paper_id].citation_count or 0, 200)  # Cap citation influence
        ),
        reverse=True
    )
    
    logger.info(f"Scored {len(matches)} papers")
    return matches


def enrich_papers_with_fulltext(
    state: PipelineState,
    paper_ids: List[str],
    max_workers: int = 1,
    run_mgr: Optional["RunManager"] = None,
) -> None:
    """
    Ensure fulltext chunks exist for the specified papers.
    Downloads PDF/HTML if needed and parses it.
    
    Args:
        state: Pipeline state containing papers dict
        paper_ids: List of paper IDs to enrich
        max_workers: Number of parallel threads (1 = serial, >1 = parallel)
        run_mgr: Optional RunManager for linking PDFs to run directory
    """
    # Use RunManager's pdf_cache if available, else fallback to cwd
    if run_mgr:
        pdf_cache_dir = str(run_mgr.pdf_cache_dir)
    else:
        pdf_cache_dir = os.path.join(os.getcwd(), "pdf_cache")
    # Ensure cache dir exists (outside threads)
    try:
        os.makedirs(pdf_cache_dir, exist_ok=True)
    except Exception:
        pass

    def _process_one_paper(pid: str) -> None:
        """Process a single paper: resolve URL, download PDF, parse chunks."""
        # Instantiate tools locally for thread safety
        resolver = PaperResolverTool()
        parser = PdfParserTool()
        
        paper = state.papers.get(pid)
        if not paper:
            return
        
        if paper.chunks:  # Already parsed
            return
            
        logger.info(f"[Fulltext] Enriching paper: {paper.title[:50]}...")
        
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
            logger.warning(f"[Fulltext] No PDF URL found for paper: {paper.title[:50]}")
            return

        # Download PDF to local cache
        # Ensure unique filename
        # Sanitize paper_id for use as filename (DOIs contain /, S2: contains :)
        safe_name = pid.replace('/', '_').replace('\\', '_').replace(':', '_')
        if paper.doi:
            safe_name = paper.doi.replace('/', '_').replace('\\', '_').replace(':', '_')
        elif paper.openalex_id:
            safe_name = paper.openalex_id.replace('/', '_').replace('\\', '_').replace(':', '_')
            
        local_filename = f"{safe_name}.pdf"
        local_pdf_path = os.path.join(pdf_cache_dir, local_filename)
        
        if not os.path.exists(local_pdf_path):
            try:
                logger.info(f"Downloading PDF from {paper.pdf_url}...")
                response = requests.get(paper.pdf_url, timeout=30)
                response.raise_for_status()
                with open(local_pdf_path, "wb") as f:
                    f.write(response.content)
            except Exception as e:
                logger.warning(f"[Fulltext] Failed to download PDF for paper: {paper.title[:50]} - {e}")
                return
        
        paper.local_pdf_path = local_pdf_path
        
        # Link PDF to run directory if RunManager is available
        if run_mgr:
            try:
                run_mgr.link_pdf_to_run(safe_name, Path(local_pdf_path))
            except Exception as e:
                logger.debug(f"[Fulltext] Failed to link PDF to run dir: {e}")

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
            
            # Parse sections with semantic roles (intro, related_work, method, etc.)
            try:
                paper_sections = parser.parse_sections(
                    pdf_path=local_pdf_path,
                    paper_id=pid,
                    title=paper.title,
                    year=paper.year or 0,
                )
                # Save sections JSON to task directory
                if run_mgr:
                    sections_path = save_paper_sections(run_mgr.run_dir, paper_sections)
                    logger.info(f"[Fulltext] Saved sections to {sections_path}")
            except Exception as e:
                logger.warning(f"[Fulltext] Failed to parse sections for {paper.title[:50]}: {e}")
            
        except Exception as e:
            logger.warning(f"Failed to parse PDF {local_pdf_path}: {e}")

    # Serial execution (default, for debug mode)
    if max_workers <= 1:
        for pid in paper_ids:
            _process_one_paper(pid)
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_pid = {executor.submit(_process_one_paper, pid): pid for pid in paper_ids}
            for fut in as_completed(future_to_pid):
                pid = future_to_pid[fut]
                try:
                    fut.result()
                except Exception as e:
                    logger.warning(f"[Parallel] Error enriching paper {pid}: {e}")


def generate_evidence_keywords_from_idea(
    agent,
    topic: str,
    idea_description: str,
    facets: List[str],
    idea_features: Dict[str, Set[str]],
    max_keywords: int = 30,
) -> List[str]:
    """
    Use LLM to generate evidence keywords from idea for fulltext search.
    """
    # Reuse generate_relevance_keywords_from_idea for simplicity
    return generate_relevance_keywords_from_idea(
        agent=agent,
        topic=topic,
        idea=idea_description,
        facets=facets,
        max_keywords=max_keywords,
    )


def make_keywords_for_evidence(
    idea_features: Dict[str, Set[str]],
    facet_synonyms: Optional[Dict[str, List[str]]] = None,
    extra_keywords: Optional[List[str]] = None,
) -> Set[str]:
    """
    Combine idea features and extra keywords into a set for evidence search.
    """
    keywords: Set[str] = set()
    
    # Add all idea features
    for feature_set in idea_features.values():
        for feature in feature_set:
            keywords.add(feature.lower().strip())
    
    # Add synonyms if provided
    if facet_synonyms:
        for synonyms in facet_synonyms.values():
            for s in synonyms:
                keywords.add(s.lower().strip())
    
    # Add extra keywords from LLM
    if extra_keywords:
        for kw in extra_keywords:
            keywords.add(kw.lower().strip())
    
    return keywords


def find_evidence_snippets(
    paper: Paper,
    keywords: Set[str],
    max_snippets: int = 3,
) -> List[str]:
    """
    Find text snippets in paper chunks that match the keywords.
    Uses simple keyword matching and BM25 scoring.
    """
    if not paper.chunks:
        return []
    
    snippets: List[str] = []
    keyword_list = list(keywords)
    
    # Tokenize chunks for BM25
    tokenized_chunks = []
    for chunk in paper.chunks:
        tokens = chunk.text.lower().split()
        tokenized_chunks.append(tokens)
    
    if not tokenized_chunks:
        return []
    
    try:
        bm25 = BM25Okapi(tokenized_chunks)
        
        # Score each chunk against all keywords combined
        query_tokens = []
        for kw in keyword_list[:20]:  # Limit keywords
            query_tokens.extend(kw.lower().split())
        
        scores = bm25.get_scores(query_tokens)
        
        # Get top scoring chunks
        scored_chunks = list(zip(scores, paper.chunks))
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        
        for score, chunk in scored_chunks[:max_snippets]:
            if score > 0:
                # Extract a relevant snippet (first 500 chars)
                text = chunk.text.strip()[:500]
                if text:
                    snippets.append(text)
    except Exception as e:
        logger.debug(f"[Evidence] BM25 scoring failed: {e}")
        
        # Fallback: simple keyword matching
        for chunk in paper.chunks[:10]:
            text_lower = chunk.text.lower()
            for kw in keyword_list[:10]:
                if kw in text_lower:
                    snippets.append(chunk.text.strip()[:500])
                    break
            if len(snippets) >= max_snippets:
                break
    
    return snippets[:max_snippets]

def refine_top_papers_with_fulltext(
    agent,
    state: PipelineState,
    matches: List[PriorArtMatch],
    idea_description: str,
    facets: List[str],
    idea_features: Dict[str, Set[str]],
    top_k: int = 20,
    max_workers: int = 1,
    run_mgr: Optional["RunManager"] = None,
) -> List[PriorArtMatch]:
    """
    Second-stage refinement for the most promising papers.

    For the top `top_k` matches (by coverage_level and citations):
    - Ensure their fulltext is available via `paper.chunks`
      (trigger PDF/HTML parsing if needed).
    - Use BM25 / other retriever to extract a few evidence snippets.
    - Ask the LLM again with title/abstract + snippets to refine:
      - coverage_level (optional)
      - whether the idea appears as main contribution / technique / background.
    - Update the corresponding PriorArtMatch objects in-place.
    
    Args:
        max_workers: Number of parallel threads (1 = serial, >1 = parallel)
        run_mgr: Optional RunManager for linking PDFs to run directory
    """
    logger.info(f"[PriorArt] Refining top {top_k} papers with fulltext...")

    # Instantiate light model for refinement
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

    # 1. Sort and select top_k candidates
    top_candidates = sorted(
        matches,
        key=lambda m: (
            m.coverage_level,
            m.high_level_facet_score,
            m.structural_score,
            state.papers[m.paper_id].citation_count or 0,
        ),
        reverse=True,
    )[:top_k]

    # 2. Ensure fulltext chunks exist
    enrich_papers_with_fulltext(
        state,
        [m.paper_id for m in top_candidates],
        max_workers=max_workers,
        run_mgr=run_mgr,
    )

    # 3. Build evidence keyword set using LLM (domain-agnostic)
    evidence_kw_from_llm = generate_evidence_keywords_from_idea(
        agent=agent,
        topic=state.intent.topic,
        idea_description=idea_description,
        facets=facets,
        idea_features=idea_features,
        max_keywords=30,
    )
    
    # Merge LLM keywords with structural features
    evidence_keywords = make_keywords_for_evidence(
        idea_features=idea_features,
        facet_synonyms=None,  # Could add expand_facet_synonyms here later
        extra_keywords=evidence_kw_from_llm,
    )
    
    logger.info(f"[PriorArt] Evidence keywords ({len(evidence_keywords)}): {sorted(evidence_keywords)[:10]}...")

    for match in top_candidates:
        paper = state.papers[match.paper_id]
        snippets = find_evidence_snippets(paper, evidence_keywords, max_snippets=3)

        if not snippets:
            continue

        # Build refinement prompt using idea, facets, and snippets
        snippets_text = "\n".join(f"- {s}" for s in snippets)
        prompt = f"""
You are checking for potential prior art.

Research idea:
{idea_description}

Key facets:
{chr(10).join(f"- F{i+1}: {f}" for i, f in enumerate(facets))}

Paper:
Title: {paper.title}
Venue: {paper.venue or 'Unknown'}
Year: {paper.year or 'Unknown'}
Citations: {paper.citation_count or 0}
Abstract: {paper.abstract or 'N/A'}

Relevant snippets from the full text:
{snippets_text}

Based on ALL of the above, answer in JSON:

{{
  "coverage_level_refined": 0,  // integer [0-3]
  "role": "main_contribution" | "key_technique" | "background" | "irrelevant",
  "notes": "short justification"
}}

Return RAW JSON only, no backticks.
"""
        try:
            messages = [{"role": "user", "content": prompt}]
            resp = light_model(messages)
            text = extract_text_from_llm_response(resp)
            refined = json.loads(text)

            # Update the match object in-place
            level_refined = int(refined.get("coverage_level_refined", match.coverage_level))
            role = str(refined.get("role", "unknown")).strip()
            notes = str(refined.get("notes", "")).strip()

            # Update match
            match.refined_coverage_level = level_refined
            match.coverage_level = level_refined
            match.role = role
            match.notes = f"{match.notes}\\n{notes}".strip()

        except Exception as e:
            logger.warning(f"[PriorArt] Failed to refine fulltext scoring for {paper.title}: {e}")

    # 4. Return the updated list (all matches, not only top_k)
    return matches


