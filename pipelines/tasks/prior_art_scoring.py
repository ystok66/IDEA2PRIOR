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
import hashlib
import uuid
import threading
from copy import deepcopy
import requests
from pathlib import Path
from typing import List, Dict, Set, Any, Optional, Tuple, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger
from smolagents import LiteLLMModel
from rank_bm25 import BM25Okapi

from pipelines.state import PipelineState, Paper, Chunk
from pipelines.run_manager import RunManager
from pipelines.sections import save_paper_sections, load_paper_sections, _sanitize_paper_id
from pipelines.llm_config import get_litellm_config
from pipelines.rate_limiter import llm_rate_limiter, pdf_rate_limiter
from tools.paper_resolver import PaperResolverTool
from tools.pdf_parser import PdfParserTool
from pipelines.tasks.prior_art_utils import ordered_parallel_map
from pipelines.runtime import invoke_model, get_runtime, fingerprint

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
from pipelines.tasks.conservative_json import parse_json_locally


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
        return invoke_model(agent_or_model.model, messages, stage="keyword_generation")
    return invoke_model(agent_or_model, messages, stage="keyword_generation")


def _invoke_with_run_reuse(model, messages, *, stage, task_id=None, purpose,
                           validate_response, reuse=False, dependencies=None,
                           rate_limit=False):
    """Reuse only validated, byte-equivalent inference within one live run.

    This is an inference memo, not a replacement for repeated experimental trials.
    Nothing is loaded from an earlier run. Stage is excluded from the key because
    initial/after-enrichment are the same judgment when the evidence is unchanged.
    Model defaults, endpoint, credentials and caller dependencies remain in the
    fingerprint; no credential or prompt contents are emitted in reuse events.
    """
    runtime = get_runtime()

    def execute():
        if rate_limit:
            llm_rate_limiter.wait_for_slot()
        response = invoke_model(model, messages, stage=stage, task_id=task_id,
                                validate_response=validate_response)
        # Also validate when a model/mock is invoked without the runtime adapter.
        if validate_response(response) is not True:
            raise ValueError("Response validator must explicitly return True")
        return response

    if not reuse or runtime is None:
        return execute()
    key = fingerprint({
        "schema": "prior_art_inference_memo_v1", "purpose": purpose,
        "model": getattr(model, "model_id", None),
        "endpoint": getattr(model, "api_base", None),
        "model_defaults": getattr(model, "kwargs", None),
        "credential_scope": getattr(model, "api_key", None),
        "messages": messages, "dependencies": dependencies,
    })
    # One lock per exact request also prevents duplicate concurrent requests.
    with runtime._lock:
        if not hasattr(runtime, "_prior_art_inference_memo"):
            runtime._prior_art_inference_memo = {}
            runtime._prior_art_inference_locks = {}
        request_lock = runtime._prior_art_inference_locks.setdefault(key, threading.Lock())
    with request_lock:
        entry = runtime._prior_art_inference_memo.get(key)
        if entry is not None:
            validate_response(entry["text"])
            runtime.ledger.event(
                "LLM_RESPONSE_REUSED", stage=stage, task_id=task_id or runtime.next_task_id(),
                reuse_event_id=uuid.uuid4().hex,
                source_stage=entry["stage"], source_task_id=entry["task_id"],
                request_fingerprint=key, purpose=purpose, avoided_api_calls=1,
                prompt_characters=sum(len(str(m.get("content", ""))) for m in messages),
                saved_tokens=None, scope="same_live_run_exact_request",
            )
            return entry["text"]
        # Give auto-ID helpers an explicit source identity for audit linkage.
        source_task_id = task_id or runtime.next_task_id()
        task_id = source_task_id
        response = execute()
        runtime._prior_art_inference_memo[key] = {
            "text": extract_text_from_llm_response(response),
            "stage": stage, "task_id": source_task_id,
        }
        return response


def _parse_scoring_json(response, *, audit=False):
    raw = extract_text_from_llm_response(response)
    data, mode = parse_json_locally(raw)
    runtime = get_runtime()
    if audit and mode != "json" and runtime is not None:
        runtime.ledger.event("LOCAL_JSON_FORMAT_RECOVERY", parser="conservative_json_v1",
                             recovery_mode=mode, response_text_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
    return data


def _validate_facets_response(response):
    data = _parse_scoring_json(response, audit=True)
    if (not isinstance(data, list) or not 3 <= len(data) <= 7
            or any(not isinstance(value, str) or not value.strip() for value in data)):
        raise ValueError("Idea facets must be 3-7 nonempty strings")
    normalized = [" ".join(value.split()) for value in data]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Idea facets must be distinct")
    return True


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
        result = invoke_model(light_model, messages, stage='idea_facets',
                              validate_response=_validate_facets_response)
        result_str = extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw facet output (cleaned): {result_str[:1000]!r}")
        
        _validate_facets_response(result)
        facets = [" ".join(value.split()) for value in _parse_scoring_json(result)]
        logger.info(f"[PriorArt] Extracted {len(facets)} facets from idea")
        return facets
        
    except Exception as e:
        logger.error(f"Failed to decompose idea into facets: {e}")
    
        raise ValueError("Idea facet decomposition failed validation; batch scoring was not started") from e


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
        result = invoke_model(light_model, messages, stage='idea_features',
                              validate_response=lambda response: _validate_string_lists_response(
                                  response, ("datasets", "metrics", "method_families")))
        result_str = extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw idea-struct output (cleaned): {result_str[:1000]!r}")
        
        datasets: Set[str] = set()
        metrics: Set[str] = set()
        families: Set[str] = set()
        
        _validate_string_lists_response(result, ("datasets", "metrics", "method_families"))
        data = _parse_scoring_json(result)
        
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
        details.append(f"Datasets: {', '.join(sorted(overlap_datasets))}")
        
    # Check Metrics
    paper_metrics = set(m.lower() for m in paper.metrics)
    overlap_metrics = idea_features["metrics"] & paper_metrics
    if overlap_metrics:
        score += 1
        details.append(f"Metrics: {', '.join(sorted(overlap_metrics))}")
        
    # Check Method Families
    paper_families = set(f.lower() for f in paper.method_families)
    overlap_families = idea_features["method_families"] & paper_families
    if overlap_families:
        score += 1
        details.append(f"Methods: {', '.join(sorted(overlap_families))}")
        
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
        result = invoke_model(light_model, messages, stage='idea_high_level_facets',
                              validate_response=_validate_paper_facets_response)
        result_str = extract_text_from_llm_response(result)
        logger.debug(f"[PriorArt] Raw high-level facets (idea, cleaned): {result_str[:1000]!r}")
        
        _validate_paper_facets_response(result)
        data = _parse_scoring_json(result)
        
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
    reuse: bool = False,
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
        if reuse:
            resp = _invoke_with_run_reuse(
                light_model, messages, stage='paper_high_level_facets',
                purpose='paper_high_level_facets', reuse=True,
                dependencies={"document_version": paper.document_version,
                              "content_hash": paper.content_hash},
                validate_response=_validate_paper_facets_response,
            )
        else:
            resp = invoke_model(light_model, messages, stage='paper_high_level_facets',
                                validate_response=_validate_paper_facets_response)
        result_str = extract_text_from_llm_response(resp)
        logger.debug(
            f"[PriorArt] Raw high-level facets (paper={paper.title[:60]!r}, cleaned): "
            f"{result_str[:1000]!r}"
        )
        
        _validate_paper_facets_response(resp)
        data = _parse_scoring_json(resp)
        
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
            details[ftype] = sorted(overlap)
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
    reuse: bool = False,
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
        if reuse:
            resp = _invoke_with_run_reuse(
                expert_model, [{"role": "user", "content": prompt}],
                stage="keyword_generation", purpose="relevance_keywords", reuse=True,
                validate_response=_validate_keywords_response,
            )
        else:
            resp = call_llm_json(expert_model, prompt)
        text = extract_text_from_llm_response(resp)
        keywords = _parse_scoring_json(text)
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
    enable_survey_penalty: bool = False,
) -> float:
    """
    Compute a cheap heuristic relevance score using keyword matches.

    This function must remain domain-agnostic. Do not hard-code 3D vision / NLP / RL
    specific keywords here. Use the `keywords` / `strong_cues` arguments to specialize.
    
    Args:
        enable_survey_penalty: If True, apply penalty for survey/review papers.
                               Default is False (no penalty).
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
    # Only apply if enable_survey_penalty is True
    if enable_survey_penalty:
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
    enable_survey_penalty: bool = False,
    seed_paper_ids: Optional[Set[str]] = None,
) -> List[Tuple[str, Paper]]:
    """
    Stage 1: Apply a lightweight lexical pre-filter using simple_relevance_score.
    
    Args:
        enable_survey_penalty: If True, penalize survey/review papers. Default False.
        seed_paper_ids: Set of paper IDs that are seed papers (from LLM or web search).
                        These are automatically kept regardless of lexical score.
    
    Improvements:
    1. Seed papers are always kept (they were pre-validated by LLM/web search)
    2. All papers with score > 0 are kept (not just top N)
    """
    # Use top k keywords as strong cues default
    strong_cues = keywords[:10] if keywords else []
    seed_ids = seed_paper_ids or set()

    # Separate seed papers from regular papers
    seed_papers = []
    regular_papers = []
    
    for pid, p in papers:
        s = simple_relevance_score(
            p.title, p.abstract, keywords, 
            strong_cues=strong_cues,
            enable_survey_penalty=enable_survey_penalty
        )
        if pid in seed_ids:
            seed_papers.append((s, pid, p))
        else:
            regular_papers.append((s, pid, p))
    
    # Sort regular papers descending by score
    regular_papers.sort(key=lambda x: (-x[0], x[1]))
    
    # Strategy: Keep all papers with score > 0, plus all seed papers
    # This ensures we don't lose potentially relevant papers due to strict threshold
    positive_score_papers = [x for x in regular_papers if x[0] > 0]
    
    # Apply max_keep limit only to non-seed papers if needed
    if len(positive_score_papers) > max_keep:
        kept_regular = positive_score_papers[:max_keep]
    else:
        kept_regular = positive_score_papers
    
    # Combine: always include all seed papers + top regular papers
    kept = seed_papers + kept_regular
    
    # Log detailed stats
    if papers:
        top_score = max([x[0] for x in seed_papers + regular_papers]) if (seed_papers or regular_papers) else 0.0
        lowest_kept_score = min([x[0] for x in kept]) if kept else 0.0
        
        logger.info(
            f"[PreFilter-Lexical] Input: {len(papers)} papers. "
            f"Kept: {len(kept)} (seeds={len(seed_papers)}, regular={len(kept_regular)}). "
            f"Score range: [{lowest_kept_score:.2f}, {top_score:.2f}]"
        )
        
        dropped_count = len(regular_papers) - len(kept_regular)
        if dropped_count > 0:
            zero_score_count = len([x for x in regular_papers if x[0] <= 0])
            logger.info(f"[PreFilter-Lexical] Dropped {dropped_count} papers ({zero_score_count} had score=0).")
            
    return [(pid, p) for _, pid, p in kept]


def llm_relevance_prefilter(
    papers: List[Tuple[str, Paper]],
    topic: str,
    idea_description: str,
    max_workers: int = 1,
    stage: str = "initial",
    reuse: bool = False,
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
    
    def _check_single_paper(pid_paper: Tuple[str, Paper]) -> Tuple[str, Paper, bool]:
        """Check if a single paper is potentially relevant."""
        pid, paper = pid_paper
        
        # Create thread-local LLM instance (each thread must have its own)
        model_id, api_base, api_key = get_litellm_config(role="light")
        local_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
        
        prompt = f"""You are an academic research assistant. Quickly determine if this paper might be relevant to the research topic below.

Research Topic: {topic}
Research Idea: {idea_description}

Paper Title: {paper.title}
Paper Abstract: {paper.abstract or "N/A"}

Is this paper potentially relevant to the research topic/idea above?
Only output valid JSON, no explanation:
{{"is_relevant": true}} or {{"is_relevant": false}}
"""
        try:
            messages = [{"role": "user", "content": prompt}]
            resp = _invoke_with_run_reuse(
                local_model, messages, stage=f"relevance_prefilter_{stage}", task_id=pid,
                purpose="relevance_prefilter", reuse=reuse, rate_limit=True,
                dependencies={"paper_id": pid, "document_version": paper.document_version},
                validate_response=_validate_relevance_response,
            )
            text = extract_text_from_llm_response(resp)
            result = _parse_scoring_json(text)
            is_relevant = result.get("is_relevant")
            if type(is_relevant) is not bool:
                raise ValueError("is_relevant must be a JSON boolean")
            return (pid, paper, is_relevant)
        except Exception as e:
            logger.debug(f"[PreFilter-LLM] Error checking {paper.title[:40]}: {e}")
            from pipelines.runtime import get_runtime
            if get_runtime():
                get_runtime().ledger.event("PREFILTER_FAILED_KEEP", paper_id=pid, stage=stage, error=str(e))
            return (pid, paper, True)  # Keep paper on error
    
    results = ordered_parallel_map(papers, _check_single_paper, max_workers)
    
    # Filter to relevant papers
    relevant_papers = [(pid, p) for pid, p, is_relevant in results if is_relevant]
    dropped_count = len(papers) - len(relevant_papers)
    
    logger.info(
        f"[PreFilter-LLM] Kept {len(relevant_papers)}/{len(papers)} papers. "
        f"Dropped {dropped_count} as not relevant."
    )
    
    return relevant_papers


def _validate_score_response(response, facet_count, refined=False):
    data = _parse_scoring_json(response, audit=True)
    _validate_score_data(data, facet_count, refined=refined)
    return True


def _validate_relevance_response(response):
    data = _parse_scoring_json(response, audit=True)
    if not isinstance(data, dict) or type(data.get("is_relevant")) is not bool:
        raise ValueError("is_relevant must be a JSON boolean")
    return True


def _validate_keywords_response(response):
    data = _parse_scoring_json(response, audit=True)
    if not isinstance(data, list) or not data or any(not isinstance(x, str) or not x.strip() for x in data):
        raise ValueError("keywords must be a nonempty list of nonempty strings")
    return True


def _validate_paper_facets_response(response):
    return _validate_string_lists_response(response, HIGH_LEVEL_FACET_TYPES)


def _validate_string_lists_response(response, keys):
    data = _parse_scoring_json(response, audit=True)
    if not isinstance(data, dict) or any(
            key not in data or not isinstance(data[key], list)
            or any(not isinstance(x, str) for x in data[key])
            for key in keys):
        raise ValueError("Response must include all required fields as string lists")
    return True


def _validate_score_data(data, facet_count, refined=False):
    """Reject incomplete/nonfinite model judgments instead of inventing zero."""
    if not isinstance(data, dict):
        raise ValueError("score response must be an object")
    level_key = "coverage_level_refined" if refined else "coverage_level"
    level = data.get(level_key)
    if type(level) is not int or not 0 <= level <= 3:
        raise ValueError(f"{level_key} must be an integer from 0 to 3")
    if not isinstance(data.get("notes"), str):
        raise ValueError("notes must be a string")
    if refined:
        if data.get("role") not in {"main_contribution", "key_technique", "background", "irrelevant"}:
            raise ValueError("invalid refinement role")
        return data
    labels = data.get("covered_facets")
    valid_labels = {f"F{i + 1}" for i in range(facet_count)}
    if not isinstance(labels, list) or any(not isinstance(v, str) or v not in valid_labels for v in labels):
        raise ValueError("covered_facets must contain only defined facet labels")
    for field in ("task_similarity", "method_similarity"):
        value = data.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{field} must be a finite number from 0 to 1")
    if "high_level_facets" in data:
        hl = data["high_level_facets"]
        if not isinstance(hl, dict) or any(
                not isinstance(hl.get(key, []), list) or
                any(not isinstance(item, str) for item in hl.get(key, []))
                for key in HIGH_LEVEL_FACET_TYPES):
            raise ValueError("invalid high_level_facets")
    return data


def score_papers_with_abstract_only(
    agent, 
    state: PipelineState, 
    facets: List[str], 
    idea_features: Dict[str, Set[str]], 
    idea_high_level_facets: Dict[str, Set[str]],
    idea_description: str = "",
    cfg: Optional[PriorArtConfig] = None,
    stage: str = "initial",
    seed_paper_ids: Optional[Set[str]] = None,
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
    staged = bool(cfg and getattr(cfg, "staged_retrieval", False))
    reuse = bool(cfg and cfg.reuse_llm_across_stages)
    # The staged funnel already chose its bounded candidate pool. Do not apply a
    # second citation cap or redundant boolean judgment before full scoring.
    if staged:
        max_papers = len(state.papers)
    elif cfg:
        max_papers = get_max_abstract_to_score(cfg, stage)
    else:
        max_papers = 80 # Default fallback

    logger.info(f"[PriorArt] Scoring top {max_papers} papers (abstract-only) [Stage: {stage}]...")
    
    # Sort papers by citation count
    all_papers = list(state.papers.items())
    if not staged:
        all_papers.sort(key=lambda x: (-(x[1].citation_count or 0), x[0]))

    # Two-stage pre-filter logic
    # Stage 1: Lexical scoring with generic-word penalty
    if not staged and cfg and cfg.enable_prefilter and len(all_papers) > cfg.prefilter_min_papers:
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
            reuse=reuse,
        )
        logger.info(f"[PriorArt] Generated relevance keywords: {keywords[:10]}...")  # Log first 10
        
        all_papers = prefilter_papers_for_llm_scoring(
            papers=all_papers,
            keywords=keywords,
            min_keep=10,
            max_keep=cfg.prefilter_max_keep,
            enable_survey_penalty=cfg.enable_survey_penalty,
            seed_paper_ids=seed_paper_ids,  # Always keep seed papers
        )
    
    # Stage 2: Light LLM true/false relevance filter
    if not staged and cfg and cfg.enable_llm_prefilter:
        llm_top_n = cfg.debug_llm_prefilter_top_n if cfg.debug_mode else cfg.llm_prefilter_top_n
        papers_for_llm_filter = all_papers[:llm_top_n]
        
        if len(papers_for_llm_filter) > 10:  # Only if we have enough papers to filter
            logger.info(
                f"[PriorArt] Stage 2: Applying LLM relevance filter to top {len(papers_for_llm_filter)} papers."
            )
            max_workers = cfg.max_workers_llm_prefilter  # Use config instead of hardcoded 4 or forced 1
            filtered_papers = llm_relevance_prefilter(
                papers=papers_for_llm_filter,
                topic=state.intent.topic,
                idea_description=idea_description,
                max_workers=max_workers,
                stage=stage,
                reuse=reuse,
            )
            # Combine filtered papers with remaining papers (beyond llm_top_n)
            all_papers = filtered_papers + all_papers[llm_top_n:]
    
    # Limit to top max_papers
    papers_to_score = all_papers[:max_papers]
    
    matches = []
    
    facets_str = "\n".join([f"F{i+1}. {facet}" for i, facet in enumerate(facets)])
    allowed_facet_labels = json.dumps([f"F{i+1}" for i in range(len(facets))])
    
    # =========================================================================
    # Define per-paper scoring function for parallelization
    # =========================================================================
    def _score_single_paper(paper_tuple: tuple) -> PriorArtMatch:
        """
        Score a single paper with merged LLM call (facet extraction + scoring).
        Creates its own LiteLLMModel instance for thread safety.
        Returns PriorArtMatch object.
        """
        paper_id, paper = paper_tuple
        paper = deepcopy(paper)  # Workers produce isolated patches.
        if staged and paper.identity_status == 'conflict':
            return PriorArtMatch(
                paper_id=paper_id, coverage_level=None, scoring_status='identity_conflict',
                notes='Conflicting identity; retained for audit, excluded from relevance judgment.',
            ), paper.high_level_facets
        if (staged and state.intent.years and paper.year is not None
                and not state.intent.years[0] <= paper.year <= state.intent.years[1]):
            return PriorArtMatch(
                paper_id=paper_id, coverage_level=None, scoring_status='out_of_scope',
                notes='Verified year outside the requested interval; retained for audit.',
            ), paper.high_level_facets
        if staged and not (paper.abstract or "").strip():
            # Missing evidence is uncertainty, not a negative judgment. The
            # funnel can enrich this record and invoke scoring again later.
            return PriorArtMatch(
                paper_id=paper_id, coverage_level=None,
                scoring_status="missing_abstract",
                notes="Abstract unavailable; awaiting metadata enrichment.",
            ), paper.high_level_facets
        
        # Create thread-local LLM instance (each thread must have its own)
        _model_id, _api_base, _api_key = get_litellm_config(role="light")
        local_model = LiteLLMModel(model_id=_model_id, api_base=_api_base, api_key=_api_key)
        
        logger.debug(f"[PriorArt] Scoring paper: {paper.title} (id={paper_id})")
        
        # Build paper context (Abstract Only)
        paper_context = f"""Title: {paper.title}
Venue: {paper.venue or 'Unknown'}
Year: {paper.year or 'Unknown'}"""
        if not staged:
            paper_context += f"\nCitation Count: {paper.citation_count or 0}"
        
        # Add abstract if available
        if hasattr(paper, 'abstract') and paper.abstract:
            paper_context += f"\nAbstract: {paper.abstract[:1000]}"
        else:
            paper_context += "\nAbstract: N/A"
        
        # Check if we should use merged LLM call
        use_merged = cfg.enable_merged_llm_calls if cfg else True
        reuse_dependencies = {
            "paper_id": paper_id, "entity_id": paper.entity_id,
            "document_version": paper.document_version, "content_hash": paper.content_hash,
            "idea": idea_description, "topic": state.intent.topic, "facets": facets,
            "idea_features": idea_features, "idea_high_level_facets": idea_high_level_facets,
            # Keep the full abstract in the validity key even when the legacy
            # prompt uses its existing 1000-character evidence window.
            "abstract": paper.abstract, "doi": paper.doi, "arxiv_id": paper.arxiv_id,
            "staged_retrieval": staged, "merged_calls": use_merged,
        }
        
        if use_merged:
            # ===== MERGED LLM CALL: Scoring + Facet Extraction in One =====
            merged_prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

Given the following research idea facets and a paper, do TWO tasks:
1. Rate how much this paper overlaps with the idea (scoring)
2. Extract the paper's own high-level facets

Idea Facets:
{facets_str}

Paper:
{paper_context}

Please output a SINGLE JSON object with ALL of the following fields:
- "coverage_level": integer 0-3 where:
  0 = no overlap
  1 = adjacent work (same domain but different approach)
  2 = partial overlap (covers some facets)
  3 = high overlap (covers most/all facets, potential conflict)
- "covered_facets": a list containing only labels from {allowed_facet_labels} that this paper covers; use [] when none apply
- "task_similarity": float 0.0-1.0 how similar the task/problem addressed is
- "method_similarity": float 0.0-1.0 how similar the method/approach is
- "notes": brief justification (1-2 sentences)
- "high_level_facets": object with these keys (each is a list of short phrases):
  - "problem_facets": What specific problem/task is this paper solving?
  - "setting_facets": What is the setting, modality, or protocol?
  - "mechanism_facets": What are the key mechanisms or frameworks?
  - "assumption_facets": What are the key assumptions?

JSON output:"""
            
            try:
                messages = [{"role": "user", "content": merged_prompt}]
                resp = _invoke_with_run_reuse(
                    local_model, messages, stage=f"abstract_scoring_{stage}",
                    task_id=paper_id + "/merged", purpose="abstract_scoring_merged",
                    reuse=reuse, dependencies=reuse_dependencies, rate_limit=True,
                    validate_response=lambda response: _validate_score_response(response, len(facets)),
                )
                result_str = extract_text_from_llm_response(resp)
                logger.debug(f"[PriorArt] Raw merged output for {paper.title[:30]}...: {result_str[:500]!r}")
                
                score_data = _parse_scoring_json(resp)
                
                if isinstance(score_data, dict):
                    _validate_score_data(score_data, len(facets))
                    # Extract scoring fields
                    coverage_level = int(score_data.get("coverage_level", 0))
                    covered_facet_labels = score_data.get("covered_facets", []) or []
                    notes = score_data.get("notes", "")
                    task_sim = float(score_data.get("task_similarity", 0.0))
                    method_sim = float(score_data.get("method_similarity", 0.0))
                    
                    # Extract high_level_facets if present
                    hl_facets = score_data.get("high_level_facets", {})
                    if isinstance(hl_facets, dict) and hl_facets:
                        # Populate paper's high_level_facets from merged response
                        paper.high_level_facets = {
                            ftype: [str(v).strip() for v in hl_facets.get(ftype, []) if v]
                            for ftype in HIGH_LEVEL_FACET_TYPES
                        }
                    elif not paper.high_level_facets:
                        # Fallback: use separate call if merged didn't include facets
                        paper.high_level_facets = extract_high_level_facets_from_paper(None, paper, reuse=reuse)
                    
                    # Map facet labels to readable text
                    covered_facets: List[str] = []
                    for label in covered_facet_labels:
                        if isinstance(label, str) and label.startswith("F"):
                            try:
                                idx = int(label[1:]) - 1
                                if 0 <= idx < len(facets):
                                    covered_facets.append(facets[idx])
                            except ValueError:
                                continue
                    
                    match = PriorArtMatch(
                        paper_id=paper_id,
                        coverage_level=coverage_level,
                        covered_facet_labels=[str(l).strip() for l in covered_facet_labels],
                        covered_facets=covered_facets,
                        notes=notes,
                        task_similarity=task_sim,
                        method_similarity=method_sim,
                        abstract_coverage_level=coverage_level,
                        abstract_notes=notes,
                    )
                    
                    # Compute structural score
                    struct_score, struct_details = compute_structural_overlap(idea_features, paper)
                    match.structural_score = struct_score
                    match.structural_overlap = struct_details
                    
                    # Compute high-level facet score
                    hl_score, hl_details = compute_high_level_facet_overlap(idea_high_level_facets, paper.high_level_facets)
                    match.high_level_facet_score = hl_score
                    match.high_level_facet_matches = hl_details
                    
                    logger.info(f"[PriorArt] Scored {paper.title[:40]}...: level={coverage_level}, task_sim={task_sim:.2f}")
                    return match, paper.high_level_facets
                
            except Exception as e:
                logger.warning(f"[PriorArt] Merged LLM call failed for {paper.title[:40]}...: {e}")
        
        # ===== FALLBACK: Separate calls (original behavior) =====
        # Extract facets if not present
        if not paper.high_level_facets:
            paper.high_level_facets = extract_high_level_facets_from_paper(None, paper, reuse=reuse)
        
        # Scoring-only prompt
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
- "covered_facets": a list containing only labels from {allowed_facet_labels} that this paper covers; use [] when none apply
- "task_similarity": float 0.0-1.0 how similar the task/problem addressed is
- "method_similarity": float 0.0-1.0 how similar the method/approach is
- "notes": brief justification (1-2 sentences)

JSON output:"""

        try:
            messages = [{"role": "user", "content": prompt}]
            resp = _invoke_with_run_reuse(
                local_model, messages, stage=f"abstract_scoring_{stage}",
                task_id=paper_id + "/separate", purpose="abstract_scoring_separate",
                reuse=reuse, dependencies=reuse_dependencies, rate_limit=True,
                validate_response=lambda response: _validate_score_response(response, len(facets)),
            )
            result_str = extract_text_from_llm_response(resp)
            
            score_data = _parse_scoring_json(resp)
            
            if isinstance(score_data, dict):
                _validate_score_data(score_data, len(facets))
                coverage_level = int(score_data.get("coverage_level", 0))
                covered_facet_labels = score_data.get("covered_facets", []) or []
                notes = score_data.get("notes", "")
                task_sim = float(score_data.get("task_similarity", 0.0))
                method_sim = float(score_data.get("method_similarity", 0.0))
                
                covered_facets: List[str] = []
                for label in covered_facet_labels:
                    if isinstance(label, str) and label.startswith("F"):
                        try:
                            idx = int(label[1:]) - 1
                            if 0 <= idx < len(facets):
                                covered_facets.append(facets[idx])
                        except ValueError:
                            continue
                
                match = PriorArtMatch(
                    paper_id=paper_id,
                    coverage_level=coverage_level,
                    covered_facet_labels=[str(l).strip() for l in covered_facet_labels],
                    covered_facets=covered_facets,
                    notes=notes,
                    task_similarity=task_sim,
                    method_similarity=method_sim,
                    abstract_coverage_level=coverage_level,
                    abstract_notes=notes,
                )
                
                struct_score, struct_details = compute_structural_overlap(idea_features, paper)
                match.structural_score = struct_score
                match.structural_overlap = struct_details
                
                hl_score, hl_details = compute_high_level_facet_overlap(idea_high_level_facets, paper.high_level_facets)
                match.high_level_facet_score = hl_score
                match.high_level_facet_matches = hl_details
                
                logger.info(f"[PriorArt] Scored {paper.title[:40]}...: level={coverage_level}")
                return match, paper.high_level_facets
                
        except Exception as e:
            logger.warning(f"Failed to score paper {paper.title[:40]}...: {e}")
        
        # Return fallback match
        return PriorArtMatch(
            paper_id=paper_id,
            coverage_level=None,
            scoring_status="failed",
            scoring_error="No valid score response after the configured fallback path.",
            covered_facet_labels=[],
            covered_facets=[],
            notes="Scoring failed.",
        ), paper.high_level_facets
    
    # =========================================================================
    # Execute scoring with parallel workers (rate limiting at client layer)
    # =========================================================================
    from pipelines.tasks.prior_art_utils import run_parallel
    
    max_workers = cfg.max_workers_llm_scoring if cfg else 20
    
    logger.info(f"[PriorArt] Scoring {len(papers_to_score)} papers with parallel workers (workers={max_workers})")
    
    scored_results = ordered_parallel_map(
        items=papers_to_score,
        process_func=_score_single_paper,
        max_workers=max_workers,
    )
    matches = []
    for match, paper_facets in scored_results:
        state.papers[match.paper_id].high_level_facets = paper_facets
        matches.append(match)
        if reuse and get_runtime():
            get_runtime().ledger.event(
                "ABSTRACT_ASSESSMENT", stage=stage, paper_id=match.paper_id,
                status=match.scoring_status, coverage_level=match.coverage_level,
                task_similarity=match.task_similarity, method_similarity=match.method_similarity,
            )
    
    # Sort by coverage_level (desc), then similarity, then citation_count
    # This prioritizes papers with similar tasks/methods over high-citation surveys
    matches.sort(
        key=lambda m: (
            -(m.coverage_level if m.coverage_level is not None else -1),
            -m.task_similarity,
            -m.method_similarity,
            -m.high_level_facet_score,
            0 if staged else -min(state.papers[m.paper_id].citation_count or 0, 200),
            m.paper_id,
        ),
    )
    
    logger.info(f"Scored {len(matches)} papers")
    return matches


def enrich_papers_with_fulltext(
    state: PipelineState,
    paper_ids: List[str],
    max_workers: int = 5,
    run_mgr: Optional["RunManager"] = None,
) -> None:
    """
    Ensure fulltext chunks exist for the specified papers.
    Downloads PDF/HTML if needed and parses it.
    
    NOTE: Rate limiting for PDF downloads is handled via pdf_rate_limiter.
    Uses ensure_pdf_url() for unified URL resolution with caching.
    
    Args:
        state: Pipeline state containing papers dict
        paper_ids: List of paper IDs to enrich
        max_workers: Number of parallel threads
        run_mgr: Optional RunManager for linking PDFs to run directory
    """
    from pipelines.tasks.prior_art_utils import ensure_pdf_url, run_parallel, verified_pdf_alternatives
    
    # Use RunManager's pdf_cache if available, else fallback to cwd
    if run_mgr:
        pdf_cache_dir = str(run_mgr.pdf_cache_dir)
        pages_cache_dir = Path(getattr(run_mgr, "pdf_pages_cache_dir", run_mgr.pdf_cache_dir))
    else:
        pdf_cache_dir = os.path.join(os.getcwd(), "pdf_cache")
        pages_cache_dir = Path(pdf_cache_dir)
    # Ensure cache dir exists (outside threads)
    try:
        os.makedirs(pdf_cache_dir, exist_ok=True)
        pages_cache_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    no_manager_scope = uuid.uuid4().hex

    def load_cached_pages(raw_hash, parser_version):
        """Reuse extraction only after the current PDF bytes have been hashed.

        The cache contains no URL/DOI lookup and preserves native page order.
        Missing, damaged, or differently versioned entries are ordinary misses.
        """
        cache_path = pages_cache_dir / f"{raw_hash}.{parser_version}.pages.json"
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            candidate_pages = cached.get("pages")
            if (cached.get("pdf_sha256") == raw_hash
                    and cached.get("parser_version") == parser_version
                    and isinstance(candidate_pages, list) and candidate_pages
                    and all(isinstance(page, dict)
                            and type(page.get("page")) is int
                            and page["page"] == index + 1
                            and isinstance(page.get("text"), str)
                            for index, page in enumerate(candidate_pages))
                    and cached.get("pages_sha256") == fingerprint(candidate_pages)):
                return candidate_pages
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return None

    def _process_one_paper(pid: str) -> Optional[str]:
        """Process a single paper: resolve URL, download PDF, parse chunks. Returns pid if success."""
        # Native extraction is serialized inside PdfParserTool; text work is local.
        parser = PdfParserTool()
        
        paper = state.papers.get(pid)
        if not paper:
            return None
        if paper.identity_status == 'conflict':
            paper.pdf_status = 'error'
            paper.pages_status = paper.chunks_status = paper.sections_status = 'identity_conflict'
            logger.warning(f'[Fulltext] Identity conflict for {pid}; material must be adjudicated before use')
            return None
        artifact_manifest = (Path(run_mgr.run_dir) / "pdf_sections" / f"{_sanitize_paper_id(pid)}.artifacts.json") if run_mgr else None
        
        def remote_binding():
            return {"paper_id": pid, "doi": paper.doi, "arxiv_id": paper.arxiv_id,
                "document_version": paper.document_version, "url": paper.pdf_url,
                "scope": "versioned" if paper.document_version else str(run_mgr.run_dir if run_mgr else no_manager_scope)}

        # Automatic remote paths must never become explicit-local inputs merely
        # because the same Paper object was restored or passed into another run.
        remote_input = paper.pdf_origin == "remote_cache" or bool(paper.pdf_source_binding)
        if paper.local_pdf_path and paper.pdf_origin is None:
            remote_input = Path(paper.local_pdf_path).resolve().is_relative_to(Path(pdf_cache_dir).resolve())
        if remote_input and paper.pdf_source_binding != remote_binding():
            old_binding = dict(paper.pdf_source_binding)
            paper.source_records.append({"source": "superseded_remote_material",
                "binding": old_binding, "content_hash": paper.content_hash,
                "local_pdf_path": paper.local_pdf_path})
            if old_binding.get("arxiv_id") != paper.arxiv_id and paper.pdf_url == old_binding.get("url"):
                paper.pdf_url = None
                paper.pdf_resolve_failed = False
            paper.local_pdf_path = None
            paper.content_hash = None
            paper.parser_version = None
            paper.chunks = []
            paper.parsed_sections = []
            paper.has_fulltext = False
            paper.pdf_status = "not_attempted"
            paper.section_status = "not_attempted"
            paper.pages_status = paper.chunks_status = paper.sections_status = "not_attempted"

        cache_identity_verified = False
        if paper.local_pdf_path and os.path.isfile(paper.local_pdf_path) and paper.content_hash:
            from tools.pdf_parser import PARSER_VERSION
            cache_identity_verified = (
                hashlib.sha256(Path(paper.local_pdf_path).read_bytes()).hexdigest() == paper.content_hash
                and paper.parser_version == PARSER_VERSION
            )
        if paper.chunks and run_mgr and cache_identity_verified:
            try:
                existing_sections = load_paper_sections(run_mgr.run_dir, pid)
                manifest = json.loads(artifact_manifest.read_text(encoding="utf-8"))
                from pipelines.runtime import fingerprint
                if (existing_sections and existing_sections.sections
                        and existing_sections.paper_id == pid
                        and manifest.get("pdf_sha256") == paper.content_hash
                        and manifest.get("parser_version") == paper.parser_version
                        and manifest.get("sections_sha256") == fingerprint(existing_sections.to_dict())
                        and manifest.get("chunks_sha256") == fingerprint([c.model_dump() for c in paper.chunks])):
                    paper.parsed_sections = existing_sections.to_dict()["sections"]
                    paper.section_status = "parsed"
                    paper.sections_status = "parsed"
                    paper.chunks_status = "parsed"
                    paper.has_fulltext = True
                    return pid
            except (OSError, ValueError, TypeError, KeyError):
                pass  # Recover missing/corrupt sections even when chunks exist.
            
        logger.info(f"[Fulltext] Enriching paper: {paper.title[:50]}...")
        
        # Bind cached bytes to a source/version, not just a DOI-shaped filename.
        # Unversioned remote sources are reused within this run only; explicit
        # caller-provided local files remain material inputs and are fingerprinted.
        from pipelines.runtime import fingerprint, atomic_write_json
        explicit_local = bool(paper.local_pdf_path) and not remote_input
        source_binding = None
        binding_path = None
        cache_valid = explicit_local and os.path.isfile(paper.local_pdf_path)
        if explicit_local:
            local_pdf_path = paper.local_pdf_path
            safe_name = _sanitize_paper_id(pid)
        else:
            pdf_url = ensure_pdf_url(paper)
            if not pdf_url:
                logger.warning(f"[Fulltext] No PDF URL found for paper: {paper.title[:50]}")
                return None
            source_binding = remote_binding()
            safe_name = "material_" + fingerprint(source_binding)
            local_pdf_path = str(Path(pdf_cache_dir) / f"{safe_name}.pdf")
            binding_path = Path(pdf_cache_dir) / f"{safe_name}.source.json"
            if Path(local_pdf_path).is_file() and binding_path.is_file():
                try:
                    binding = json.loads(binding_path.read_text(encoding="utf-8"))
                    cache_valid = binding.get("source_binding") == source_binding and binding.get("pdf_sha256") == hashlib.sha256(Path(local_pdf_path).read_bytes()).hexdigest()
                except (OSError, ValueError, TypeError):
                    cache_valid = False
        pages = None

        if not cache_valid:
            pdf_url = ensure_pdf_url(paper)
            if not pdf_url:
                paper.pdf_status = "error"
                return None
            acquired = False
            # One independently verified alternate URL is still material for
            # this same selected paper; it never fills the slot with paper six.
            for source_attempt in range(2):
                temp_path = str(Path(local_pdf_path).with_suffix(f".{uuid.uuid4().hex}.tmp.pdf"))
                try:
                    pdf_rate_limiter.wait_for_slot()
                    logger.info(f"Downloading PDF from {paper.pdf_url}...")
                    response = requests.get(paper.pdf_url, timeout=30)
                    response.raise_for_status()
                    with open(temp_path, "wb") as f:
                        f.write(response.content)
                    from tools.pdf_parser import PARSER_VERSION
                    downloaded_hash = hashlib.sha256(Path(temp_path).read_bytes()).hexdigest()
                    pages = load_cached_pages(downloaded_hash, PARSER_VERSION)
                    if pages is None:
                        pages = parser._extract_text_pymupdf(temp_path)
                    if not pages:
                        raise ValueError("Downloaded PDF has no pages")
                    os.replace(temp_path, local_pdf_path)
                    if binding_path:
                        atomic_write_json(binding_path, {"source_binding": source_binding, "pdf_sha256": downloaded_hash})
                    paper.source_records.append({"source": "pdf_download_attempt", "url": paper.pdf_url,
                        "source_attempt": source_attempt + 1, "status": "SUCCEEDED", "pdf_sha256": downloaded_hash})
                    acquired = True
                    break
                except Exception as e:
                    failed_url = paper.pdf_url
                    paper.source_records.append({"source": "pdf_download_attempt", "url": failed_url,
                        "source_attempt": source_attempt + 1, "status": "FAILED", "error_type": type(e).__name__})
                    logger.warning(f"[Fulltext] Failed to download PDF for paper: {paper.title[:50]} - {e}")
                    if source_attempt or explicit_local:
                        break
                    try:
                        alternatives = verified_pdf_alternatives(paper, failed_url)
                    except Exception as alternative_error:
                        paper.source_records.append({"source": "pdf_alternative_identity",
                            "failed_url": failed_url, "status": "error",
                            "error_type": type(alternative_error).__name__})
                        break
                    if not alternatives:
                        break
                    alternative = alternatives[0]
                    if (alternative.get("identity_status") != "verified_arxiv_metadata"
                            or not alternative.get("url") or alternative["url"] == failed_url):
                        break
                    paper.pdf_url = alternative["url"]
                    paper.arxiv_id = alternative["arxiv_id"]
                    paper.document_version = alternative["document_version"]
                    source_binding = remote_binding()
                    safe_name = "material_" + fingerprint(source_binding)
                    local_pdf_path = str(Path(pdf_cache_dir) / f"{safe_name}.pdf")
                    binding_path = Path(pdf_cache_dir) / f"{safe_name}.source.json"
                    pages = None
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
            if not acquired:
                paper.pdf_status = "error"
                return None
        
        paper.local_pdf_path = local_pdf_path
        paper.pdf_origin = "explicit_local" if explicit_local else "remote_cache"
        if source_binding is not None:
            paper.pdf_source_binding = source_binding
        
        # Link PDF to run directory if RunManager is available
        if run_mgr:
            try:
                run_mgr.link_pdf_to_run(safe_name, Path(local_pdf_path))
            except Exception as e:
                logger.debug(f"[Fulltext] Failed to link PDF to run dir: {e}")

        # Parse the PDF into chunks
        try:
            logger.info(f"Parsing PDF: {local_pdf_path}")
            from tools.pdf_parser import PARSER_VERSION
            raw_hash = hashlib.sha256(Path(local_pdf_path).read_bytes()).hexdigest()
            pages_cache = pages_cache_dir / f"{raw_hash}.{PARSER_VERSION}.pages.json"
            if pages is None:
                pages = load_cached_pages(raw_hash, PARSER_VERSION)
            pages, content_json, paper_sections = parser.parse_artifacts(
                local_pdf_path, pid, paper.title, paper.year or 0,
                chunk_size=1000, pages=pages,
            )
            from pipelines.runtime import atomic_write_json, fingerprint
            atomic_write_json(pages_cache, {"pdf_sha256": raw_hash,
                "parser_version": PARSER_VERSION, "pages": pages, "pages_sha256": fingerprint(pages)})
            paper.pdf_status = "downloaded"
            replace_chunks = (not paper.chunks or paper.content_hash != raw_hash
                              or paper.parser_version != PARSER_VERSION)
            paper.content_hash = raw_hash
            paper.parser_version = PARSER_VERSION
            paper.pages_status = "parsed"
            
            # Populate paper.chunks
            if replace_chunks:
                paper.chunks = [
                Chunk(
                    text=chunk["text"],
                    page_start=chunk.get("page_start"),
                    page_end=chunk.get("page_end")
                )
                for chunk in content_json
                ]
            paper.chunks_status = "parsed" if paper.chunks else "empty"
            
            # Parse sections with semantic roles (intro, related_work, method, etc.)
            try:
                if not paper_sections.sections:
                    raise ValueError("Section parser returned no sections")
                # Save sections JSON to task directory
                if run_mgr:
                    sections_path = save_paper_sections(run_mgr.run_dir, paper_sections)
                    logger.info(f"[Fulltext] Saved sections to {sections_path}")
                    # Mark paper as having fulltext for downstream processing
                    paper.has_fulltext = True
                paper.section_status = "parsed"
                paper.sections_status = "parsed"
                paper.parsed_sections = paper_sections.to_dict()["sections"]
                paper.has_fulltext = bool(paper.chunks)
                if artifact_manifest:
                    atomic_write_json(artifact_manifest, {"paper_id": pid,
                        "pdf_sha256": raw_hash, "parser_version": PARSER_VERSION,
                        "sections_sha256": fingerprint(paper_sections.to_dict()),
                        "chunks_sha256": fingerprint([c.model_dump() for c in paper.chunks])})
            except Exception as e:
                paper.section_status = "parse_error"
                paper.sections_status = "parse_error"
                logger.warning(f"[Fulltext] Failed to parse sections for {paper.title[:50]}: {e}")
            
            return pid
        except Exception as e:
            paper.pdf_status = "error"
            paper.has_fulltext = False
            paper.pages_status = "parse_error"
            paper.section_status = "parse_error"
            paper.sections_status = "parse_error"
            logger.warning(f"Failed to parse PDF {local_pdf_path}: {e}")
            return None

    # Use single executor with per-request rate limiting (pdf_rate_limiter)
    run_parallel(
        items=list(dict.fromkeys(paper_ids)),
        process_func=_process_one_paper,
        max_workers=max_workers,
        desc="EnrichFulltext",
    )


def prepare_fixed_fulltext_for_eval(
    state: PipelineState,
    matches: List[PriorArtMatch],
    candidate_ids: List[str],
    run_mgr: RunManager,
    cfg: PriorArtConfig,
    years=None,
) -> List[str]:
    """Acquire a fixed five-paper material set without a second scoring pass.

    Valid abstract judgments use the existing shortlist order. Any remaining
    slots take eligible unassessed records in supplied candidate order, with
    that distinction recorded. No failed acquisition is replaced by item six.
    Abstract grades and refinement statuses remain untouched; a later unified
    Expert judgment owns the evaluation ranking.
    """
    from pipelines.assessment import valid_level, valid_similarity
    from pipelines.runtime import atomic_write_json
    from pipelines.tasks.candidate_selection import match_order

    limit = getattr(cfg, "retrieval_eval_fulltext_limit", 5)
    if type(limit) is not int or limit != 5:
        raise ValueError("Retrieval evaluation prepares exactly five candidate slots")
    if run_mgr is None:
        raise ValueError("Retrieval evaluation material preparation requires a run manager")

    scope = years if years is not None else state.intent.years
    eligible_ids = []
    excluded = []
    for pid in dict.fromkeys(candidate_ids):
        paper = state.papers.get(pid)
        reason = ("missing_record" if paper is None else
                  "identity_conflict" if paper.identity_status == "conflict" else
                  "known_year_out_of_scope" if scope and paper.year is not None
                  and not scope[0] <= paper.year <= scope[1] else None)
        if reason:
            excluded.append({"paper_id": pid, "reason": reason})
        else:
            eligible_ids.append(pid)
    eligible = set(eligible_ids)
    by_id = {match.paper_id: match for match in matches}
    assessed = sorted((match for pid, match in by_id.items() if pid in eligible
                       and match.scoring_status == "succeeded"
                       and valid_level(match.coverage_level)
                       and valid_similarity(match.task_similarity)
                       and valid_similarity(match.method_similarity)), key=match_order)
    assessed_ids = {match.paper_id for match in assessed}
    ordered = [match.paper_id for match in assessed] + [
        pid for pid in eligible_ids if pid not in assessed_ids
    ]
    attempted_ids, seen_entities, selections = [], set(), []
    for pid in ordered:
        paper = state.papers[pid]
        entity = paper.entity_id or pid
        if entity in seen_entities:
            excluded.append({"paper_id": pid, "reason": "duplicate_entity"})
            continue
        seen_entities.add(entity)
        match = by_id.get(pid)
        attempted_ids.append(pid)
        selections.append({
            "paper_id": pid,
            "selection_basis": "valid_abstract_match" if pid in assessed_ids else "unassessed_candidate_order",
            "abstract_scoring_status": getattr(match, "scoring_status", "not_scored"),
            "accepted_abstract_grade": match.coverage_level if pid in assessed_ids else None,
        })
        if len(attempted_ids) == limit:
            break

    path = Path(run_mgr.run_dir) / "eval_fulltext_plan.json"
    plan = {
        "schema_version": "fixed-eval-fulltext-v1", "status": "PLANNED",
        "budget": limit, "attempted_ids": attempted_ids, "selections": selections,
        "source_candidate_ids": list(dict.fromkeys(candidate_ids)),
        "selection_rule": "valid_abstract_match_order_then_unassessed_candidate_order_entity_unique",
        "replacement_policy": "NO_REPLACEMENT", "no_replacement": True,
        "llm_per_paper_refinement": "NOT_RUN_BY_PROTOCOL",
        "year_scope": scope, "excluded_records": excluded,
        "available_ids": [], "success_material_count": 0,
        "materials": [],
    }
    atomic_write_json(path, plan)
    collection_error = None
    try:
        if attempted_ids:
            enrich_papers_with_fulltext(
                state, attempted_ids, max_workers=cfg.max_workers_pdf, run_mgr=run_mgr,
            )
    except BaseException as error:
        collection_error = type(error).__name__
        raise
    finally:
        materials, available_ids = [], []
        for pid in attempted_ids:
            paper = state.papers[pid]
            available = (paper.pdf_status == "downloaded" and paper.pages_status == "parsed"
                         and any(chunk.text.strip() for chunk in paper.chunks))
            if available:
                available_ids.append(pid)
            pages_dir = Path(getattr(run_mgr, "pdf_pages_cache_dir", run_mgr.pdf_cache_dir))
            pages_path = (pages_dir / f"{paper.content_hash}.{paper.parser_version}.pages.json"
                          if paper.content_hash and paper.parser_version else None)
            materials.append({
                "paper_id": pid, "available": available,
                "pdf_status": paper.pdf_status, "pages_status": paper.pages_status,
                "chunks_status": paper.chunks_status, "section_status": paper.section_status,
                "sections_status": paper.sections_status,
                "content_hash": paper.content_hash, "parser_version": paper.parser_version,
                "local_pdf_path": paper.local_pdf_path,
                "page_cache_path": str(pages_path) if pages_path and pages_path.is_file() else None,
                "text_character_count": sum(len(chunk.text) for chunk in paper.chunks),
            })
        plan.update(
            status=("COLLECTION_FAILED" if collection_error else "NO_CANDIDATES" if not attempted_ids
                    else "MATERIAL_COMPLETE" if len(available_ids) == len(attempted_ids) else "PARTIAL_MATERIAL"),
            available_ids=available_ids, success_material_count=len(available_ids),
            attempted_count=len(attempted_ids), materials=materials,
            missing_material_ids=[pid for pid in attempted_ids if pid not in available_ids],
            collection_error_type=collection_error,
        )
        atomic_write_json(path, plan)
    return attempted_ids


def generate_evidence_keywords_from_idea(
    agent,
    topic: str,
    idea_description: str,
    facets: List[str],
    idea_features: Dict[str, Set[str]],
    max_keywords: int = 30,
    reuse: bool = False,
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
        reuse=reuse,
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
    keyword_list = sorted(keywords) if isinstance(keywords, (set, frozenset)) else list(keywords)
    
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
    cfg: Optional[PriorArtConfig] = None,
    eligible_paper_ids: Optional[Set[str]] = None,
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

    staged = bool(cfg and getattr(cfg, "staged_retrieval", False))
    # Restrict expensive work to the already enriched shortlist in staged mode.
    candidates = [m for m in matches if m.coverage_level is not None and m.scoring_status == "succeeded"
                  and (eligible_paper_ids is None or m.paper_id in eligible_paper_ids)
                  and (not staged or state.papers[m.paper_id].identity_status != "conflict")]
    sorted_candidates = sorted(
        candidates,
        key=lambda m: (
            -m.coverage_level,
            -m.task_similarity if staged else 0,
            -m.method_similarity if staged else 0,
            -m.high_level_facet_score,
            -m.structural_score,
            0 if staged else -(state.papers[m.paper_id].citation_count or 0),
            m.paper_id,
        ),
    )
    if not sorted_candidates or top_k <= 0:
        logger.info("[Fulltext] No eligible candidates; skipping evidence keyword request.")
        return matches

    def prepare_keywords(_):
        words = generate_evidence_keywords_from_idea(
            agent=agent, topic=state.intent.topic, idea_description=idea_description,
            facets=facets, idea_features=idea_features, max_keywords=30,
            reuse=bool(cfg and cfg.reuse_llm_across_stages),
        )
        return make_keywords_for_evidence(idea_features, extra_keywords=words)

    # This request keeps its original expert model, 30-word limit and prompt. It
    # does not depend on download outcomes and is never replaced by prior scores.
    keyword_executor = None
    keyword_future = None
    if cfg is None or cfg.overlap_pdf_keywords:
        from contextvars import copy_context
        keyword_executor = ThreadPoolExecutor(max_workers=1)
        keyword_future = keyword_executor.submit(copy_context().run, prepare_keywords, None)
    
    # 2. Retry logic: keep trying papers until we reach top_k successful downloads
    #    or exhaust all candidates
    successful_pids = []
    candidate_idx = 0
    max_attempts = min(len(sorted_candidates), top_k * 3)  # Don't try more than 3x top_k
    
    logger.info(f"[Fulltext] Target: {top_k} papers, available candidates: {len(sorted_candidates)}")
    
    while len(successful_pids) < top_k and candidate_idx < max_attempts:
        # Determine batch size: try to fill remaining slots
        remaining = top_k - len(successful_pids)
        batch_end = min(candidate_idx + remaining, len(sorted_candidates))
        batch = sorted_candidates[candidate_idx:batch_end]
        
        if not batch:
            break
        
        batch_pids = [m.paper_id for m in batch]
        
        # Try to download this batch
        enrich_papers_with_fulltext(
            state,
            batch_pids,
            max_workers=max_workers,
            run_mgr=run_mgr,
        )
        
        # Check which papers succeeded (have chunks or has_fulltext)
        for pid in batch_pids:
            paper = state.papers.get(pid)
            if paper and paper.pdf_status != 'error' and (paper.chunks or getattr(paper, 'has_fulltext', False)):
                successful_pids.append(pid)
        
        candidate_idx = batch_end
        
        logger.info(f"[Fulltext] Progress: {len(successful_pids)}/{top_k} successfully enriched")
    
    # Build top_candidates from successful papers
    top_candidates = [m for m in sorted_candidates if m.paper_id in successful_pids][:top_k]
    
    logger.info(f"[Fulltext] Final: {len(top_candidates)} papers with fulltext out of target {top_k}")

    # 3. Build evidence keyword set using LLM (domain-agnostic)
    try:
        evidence_keywords = keyword_future.result() if keyword_future else prepare_keywords(None)
    finally:
        if keyword_executor:
            keyword_executor.shutdown(wait=True)
    
    logger.info(f"[PriorArt] Evidence keywords ({len(evidence_keywords)}): {sorted(evidence_keywords)[:10]}...")

    # Freeze every prompt before dispatch. Workers return patches and never edit
    # shared match/state; the coordinator accepts them in original candidate order.
    tasks = []
    for match in top_candidates:
        paper = state.papers[match.paper_id]
        snippets = find_evidence_snippets(paper, evidence_keywords, max_snippets=3)

        if not snippets:
            match.refinement_status = "no_evidence"
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
        tasks.append((match.paper_id, prompt, tuple(snippets)))

    def refine_one(task):
        paper_id, prompt, snippets = task
        try:
            model_id, api_base, api_key = get_litellm_config(role="light")
            light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
            messages = [{"role": "user", "content": prompt}]
            resp = invoke_model(light_model, messages, stage='fulltext_refinement', task_id=paper_id, validate_response=lambda response: _validate_score_response(response, len(facets), refined=True))
            text = extract_text_from_llm_response(resp)
            refined = _validate_score_data(_parse_scoring_json(text), len(facets), refined=True)
            return paper_id, snippets, refined, None
        except Exception as e:
            logger.warning(f"[PriorArt] Failed to refine fulltext scoring for {paper_id}: {e}")
            return paper_id, snippets, None, str(e)

    patches = ordered_parallel_map(tasks, refine_one,
        cfg.max_workers_fulltext_refine if cfg else 4)
    matches_by_id = {m.paper_id: m for m in matches}
    for paper_id, snippets, refined, error in patches:
        match = matches_by_id[paper_id]
        if match.abstract_coverage_level is None:
            match.abstract_coverage_level = match.coverage_level
            match.abstract_notes = match.notes
        match.refinement_snippets = list(snippets)
        if error:
            match.refinement_status = "failed"
            match.refinement_error = error
            continue
        match.refinement_status = "succeeded"
        match.refinement_error = None
        match.refined_coverage_level = refined["coverage_level_refined"]
        match.coverage_level = refined["coverage_level_refined"]
        match.role = refined["role"]
        match.notes = f"{match.notes}\n{refined['notes']}".strip()

    # 4. Return the updated list (all matches, not only top_k)
    return matches


