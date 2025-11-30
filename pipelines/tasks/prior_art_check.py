# pipelines/tasks/prior_art_check.py

"""
Task-level orchestrator for prior-art checking.

Given an idea description and a research topic, this task:
1. Decomposes the idea into key technical facets
2. Runs the exploration loop to find related papers
3. Scores each paper against the idea facets (0-3 coverage level)
4. Generates a structured report highlighting:
   - High-overlap prior art (potential conflicts)
   - Partial-overlap related work
   - Adjacent work
   - Facet-level gap analysis
"""

from __future__ import annotations

from typing import List, Optional, Tuple, Dict, Set
from dataclasses import dataclass, field
import json

from loguru import logger

from pipelines.state import Intent, PipelineState, Paper, Chunk
from pipelines.exploration_loop import run_exploration_loop
from pipelines.clients import SemanticScholarClient
from tools.openalex_search import OpenAlexWorksSearchTool
from tools.paper_resolver import PaperResolverTool
from tools.pdf_parser import PdfParserTool
import requests
import os
from smolagents import LiteLLMModel
from pipelines.llm_config import get_litellm_config


def guess_default_venues_for_topic(topic: str) -> List[str]:
    """
    Very simple heuristic to map a free-form topic string to a
    list of default top-tier venues.

    This is only a heuristic and should prefer recall over precision.
    """
    t = topic.lower()
    venues: List[str] = []

    if any(x in t for x in ["vision", "detection", "segmentation", "3d", "image", "video"]):
        venues.extend(["CVPR", "ICCV", "ECCV", "TPAMI", "IJCV"])
    if any(x in t for x in ["language", "nlp", "translation", "bert", "llm", "chatgpt"]):
        venues.extend(["ACL", "EMNLP", "NAACL", "COLING", "TACL"])
    if any(x in t for x in ["reinforcement learning", "rl", "control"]):
        venues.extend(["NeurIPS", "ICML", "ICLR"])
    if any(x in t for x in ["graph", "gnn"]):
        venues.extend(["NeurIPS", "ICLR", "KDD", "WWW"])
    # Always include the big 3 for general ML topics
    if any(x in t for x in ["machine learning", "deep learning", "foundation model", "transformer", "diffusion"]):
        venues.extend(["NeurIPS", "ICML", "ICLR"])

    # Deduplicate while preserving order
    seen = set()
    result = []
    for v in venues:
        v_norm = v.strip()
        if v_norm and v_norm not in seen:
            seen.add(v_norm)
            result.append(v_norm)

    return result


HIGH_LEVEL_FACET_TYPES = [
    "problem_facets",     # What problem is it solving?
    "setting_facets",     # What is the setting/protocol?
    "mechanism_facets",   # Key mechanisms/frameworks
    "assumption_facets",  # Key assumptions
]


@dataclass
class PriorArtMatch:
    """Scoring result for how much one paper overlaps with the idea."""
    paper_id: str
    coverage_level: int  # 0 = none, 1 = adjacent, 2 = partial, 3 = high
    covered_facets: List[str]
    notes: str
    structural_score: int = 0
    structural_overlap: List[str] = field(default_factory=list)
    high_level_facet_score: int = 0
    high_level_facet_matches: Dict[str, List[str]] = field(default_factory=dict)
    role: str = "unknown"  # main_contribution | key_technique | background | irrelevant
    refined_coverage_level: Optional[int] = None


@dataclass
class PriorArtConfig:
    """Configuration for the Prior Art Check pipeline."""
    # Maximum number of papers to send to abstract-only LLM scoring
    # in the initial exploration stage (before citation graph expansion).
    max_abstract_scoring_initial: int = 80

    # Maximum number of papers to send after citation graph expansion.
    max_abstract_scoring_after_citation: int = 100

    # In debug mode, we aggressively limit LLM calls:
    debug_mode: bool = False

    # Debug mode caps (for iterative development):
    debug_max_abstract_scoring_initial: int = 10
    debug_max_abstract_scoring_after_citation: int = 20

    # Prefilter config
    enable_prefilter: bool = True
    prefilter_min_papers: int = 30          # only prefilter if > 30 candidates
    prefilter_max_keep: int = 80            # or overridden by debug caps
    prefilter_max_keywords: int = 20
    
    # Logging
    progress_log_interval: int = 5


def _get_max_abstract_to_score(
    cfg: PriorArtConfig,
    stage: str, # "initial" or "after_citation"
) -> int:
    if cfg.debug_mode:
        return (
            cfg.debug_max_abstract_scoring_initial
            if stage == "initial"
            else cfg.debug_max_abstract_scoring_after_citation
        )
    return (
        cfg.max_abstract_scoring_initial
        if stage == "initial"
        else cfg.max_abstract_scoring_after_citation
    )


def build_prior_art_intent(
    idea_description: str,
    topic: str,
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
) -> Intent:
    """
    Build a structured Intent for the prior-art check task.

    The `topic` captures the broader research area (e.g., 'monocular 3D detection'),
    while `idea_description` describes the concrete idea to check.
    """
    if venues is None:
        venues = guess_default_venues_for_topic(topic)

    return Intent(
        task_type="prior_art_check",
        topic=topic,
        years=years,
        venues=venues or [],
        extra_constraints={"idea_description": idea_description},
    )
import ast
from typing import Any


def _extract_text_from_llm_response(resp: Any) -> str:
    """
    Normalize an LLM response to a plain text string.

    This function handles:
    - smolagents / LiteLLM ChatMessage objects (using `.content`)
    - Arbitrary objects by falling back to `str(resp)`
    - Markdown fenced code blocks like ```json ... ```
    - Optional language hints (e.g. "json" on the first line inside the fence)

    The goal is to obtain a clean JSON string that can be passed to
    json.loads() or ast.literal_eval().
    """
    # 1. Extract raw text content
    if hasattr(resp, "content") and isinstance(getattr(resp, "content"), str):
        text = getattr(resp, "content")
    else:
        text = str(resp)

    text = text.strip()

    # 2. Strip HTML-like <code> ... </code> wrapper if present (defensive)
    if "<code>" in text and "</code>" in text:
        start = text.find("<code>") + len("<code>")
        end = text.rfind("</code>")
        if end > start:
            text = text[start:end].strip()

    # 3. Strip Markdown fenced code blocks ```...```
    if "```" in text:
        first = text.find("```")
        last = text.rfind("```")
        if last > first:
            inner = text[first + 3 : last].strip()
            # Handle optional language marker on the first line (e.g., "json")
            first_newline = inner.find("\n")
            if first_newline != -1:
                first_line = inner[:first_newline].strip().lower()
                # If the first line looks like a language hint, drop it
                if first_line in {"json", "javascript", "js", "python", "py"}:
                    inner = inner[first_newline + 1 :].lstrip()
            text = inner.strip()

    return text


def seed_papers_from_llm(
    agent,
    idea_description: str,
    topic: str,
    max_seeds: int = 10,
) -> List[Dict[str, str]]:
    """
    Use the LLM's own world knowledge to propose a small set of
    potentially relevant papers (as "seed" candidates).

    IMPORTANT:
    - The LLM is allowed to hallucinate here. These seeds must be
      validated later via OpenAlex / Semantic Scholar by title.
    - This function should only return lightweight metadata:
      title, venue, year, and optional ids (doi / arxiv_id / url).
    """
    prompt = f"""
You are an expert researcher in {topic}.
Based only on your own knowledge (do NOT use tools),
list up to {max_seeds} real papers that are most related
to the following idea.

For each paper, provide:
- title
- venue (conference or journal)
- year (integer, or null if unknown)
- one of: doi / arxiv_id / url (if you know it, else null)

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

Research idea:
{idea_description}

JSON:
"""
    resp = call_llm_json(agent, prompt)
    text = _extract_text_from_llm_response(resp)

    # Parse JSON safely; fallback to empty list on error.
    try:
        data = json.loads(text)
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

    logger.info(f"[PriorArt] LLM proposed {len(seeds)} seed papers (unvalidated).")
    return seeds


def validate_llm_seeds_via_openalex(
    seeds: List[Dict[str, str]],
) -> List[Paper]:
    """
    For each LLM-proposed seed (title-based), use OpenAlex's "title.search"
    filter to find the best-matching real paper. If a good match is found,
    convert it into our internal Paper model and return.

    This function is responsible for removing hallucinated papers.
    """
    validated_papers = []
    oa_tool = OpenAlexWorksSearchTool()

    for seed in seeds:
        title = seed["title"]
        # Use OpenAlex to search by title
        # We use the 'query' parameter which maps to 'search' in OpenAlex for works
        try:
            # We search specifically for this title
            # Note: OpenAlexWorksSearchTool uses 'search' parameter in its underlying call if we pass 'query'
            # But to be more precise we might want title.search if the tool supported it.
            # The current tool implementation maps 'query' to 'search' param in OpenAlex API.
            # Let's try to use the query param with "title.search:" prefix if the tool supports raw query passing,
            # OR just pass the title as query and hope for the best (OpenAlex search is decent).
            # Looking at OpenAlexWorksSearchTool implementation (from docs/memory):
            # It constructs `filter` from years/venue, and uses `search` for query.
            
            res_json = oa_tool.forward(query=title, limit=1)
            if not res_json or res_json.startswith("Error"):
                continue
                
            works = json.loads(res_json)
            if not works:
                continue
                
            best_match = works[0]
            match_title = best_match.get("title", "")
            
            # Simple validation: check if titles are somewhat similar
            # (e.g. containment or length ratio)
            # For now, just check if the seed title words are mostly in the match title
            seed_words = set(title.lower().split())
            match_words = set(match_title.lower().split())
            overlap = len(seed_words & match_words)
            if overlap / len(seed_words) < 0.5: # Loose threshold
                continue

            # Create Paper object
            doi = best_match.get("doi")
            if doi and "doi.org/" in doi:
                doi = doi.split("doi.org/")[-1]
                
            paper = Paper(
                title=match_title,
                year=best_match.get("year"),
                venue=best_match.get("venue"),
                openalex_id=best_match.get("openalex_id"),
                doi=doi,
                citation_count=best_match.get("cited_by_count")
            )
            validated_papers.append(paper)
            
        except Exception as e:
            logger.warning(f"Failed to validate seed '{title}': {e}")
            continue
            
    logger.info(f"[PriorArt] Validated {len(validated_papers)}/{len(seeds)} seed papers.")
    return validated_papers


def generate_search_queries_from_idea(
    agent,
    idea_description: str,
    topic: str,
    num_queries: int = 6,
) -> List[str]:
    """
    Ask the LLM to synthesize a small set of diverse search queries
    that can be used in scholarly APIs (OpenAlex, Semantic Scholar).

    These queries should mix:
    - core task names
    - typical keywords used in titles
    - dataset / modality names
    - plausible synonyms and phrases

    The output will later be used to drive the exploration loop.
    """
    prompt = f"""
You are an expert in {topic}.

Generate {num_queries} diverse search queries for OpenAlex/Semantic Scholar
based on the research idea below.

CRITICAL RULES:
- Each query should use at MOST ONE exact phrase in quotes (e.g., "monocular 3D detection")
- Remaining keywords should be unquoted, separated by spaces
- Avoid multiple quoted phrases in the same query (this kills recall)
- Mix broad queries (just topic) with more specific queries (topic + 1-2 keywords)
- Keep queries simple and focused

GOOD examples:
- "monocular 3D detection"
- "monocular 3D detection" rotation augmentation
- monocular 3d object detection geometric consistency
- 3D object detection data augmentation

BAD examples (too strict):
- "monocular 3D detection" "rotation augmentation" "depth-free"
- "exact long phrase" "another exact phrase"

Return ONLY raw JSON list of strings:
["query 1", "query 2", ...]

Research idea:
{idea_description}

JSON:
"""
    resp = call_llm_json(agent, prompt)
    text = _extract_text_from_llm_response(resp)

    try:
        data = json.loads(text)
    except Exception:
        logger.warning("[PriorArt] Failed to parse search queries JSON, falling back to topic.")
        return [topic]

    if not isinstance(data, list):
        return [topic]

    queries = [str(q).strip() for q in data if str(q).strip()]
    if not queries:
        return [topic]

    logger.info(f"[PriorArt] Generated {len(queries)} search queries from idea.")
    
    # Ensure we always have at least one simple topic-based query for recall
    if not any(q.count('"') <= 2 and len(q.split()) <= 5 for q in queries):
        queries.insert(0, topic)
        logger.info(f"[PriorArt] Added topic-only fallback query: '{topic}'")
    
    return queries[:num_queries]


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
    """
    logger.info("[PriorArt] Decomposing idea into facets...")
    
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
        result = call_llm_json(agent, prompt)
        result_str = _extract_text_from_llm_response(result)
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
    """
    logger.info("[PriorArt] Extracting structural features from idea...")
    
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
        result = call_llm_json(agent, prompt)
        result_str = _extract_text_from_llm_response(result)
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
    """
    logger.info("[PriorArt] Extracting high-level facets from idea...")
    
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
        result = call_llm_json(agent, prompt)
        result_str = _extract_text_from_llm_response(result)
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


def extract_high_level_facets_from_paper(
    agent,
    paper: Paper,
) -> Dict[str, List[str]]:
    """
    Extract high-level structural facets from a paper (using title/abstract/chunks).
    Always returns Dict[str, List[str]] for compatibility with Paper model.
    """
    # Prepare context
    context = f"Title: {paper.title}\n"
    
    # Add a bit of chunk text if available
    if paper.chunks:
        # Use first chunk (intro usually)
        context += f"Intro Snippet: {paper.chunks[0].text[:1000]}\n"
        
    prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.
Return raw JSON only, without backticks or any surrounding text.

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
        result = call_llm_json(agent, prompt)
        result_str = _extract_text_from_llm_response(result)
        logger.debug(
            f"[PriorArt] Raw high-level facets (paper={paper.title[:60]!r}, cleaned): "
            f"{result_str[:1000]!r}"
        )
        
        data = None
        try:
            data = json.loads(result_str)
        except Exception:
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
    
    Uses 'light' LLM role for speed/cost.
    """
    # Instantiate light model
    model_id, api_base, api_key = get_litellm_config(role="light")
    light_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)

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
    # Use light_model instead of agent
    resp = call_llm_json(light_model, prompt)
    text = _extract_text_from_llm_response(resp)
    try:
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
) -> float:
    """
    Compute a cheap heuristic relevance score using keyword matches.
    """
    score = 0.0
    title_norm = paper_title.lower() if paper_title else ""
    abstract_norm = paper_abstract.lower() if paper_abstract else ""
    
    # Strong signals
    strong_cues = ["3d", "monocular", "depth", "point cloud", "lidar", "rgb-d", "detection", "vision"]
    
    for cue in strong_cues:
        if cue in title_norm:
            score += 2.0
        if cue in abstract_norm:
            score += 0.5
            
    for kw in keywords:
        if kw in title_norm:
            score += 3.0 # Keyword in title is strong
        if kw in abstract_norm:
            score += 1.0
            
    return score


def prefilter_papers_for_llm_scoring(
    papers: List[Tuple[str, Paper]], # list of (id, Paper)
    keywords: List[str],
    min_keep: int,
    max_keep: int,
) -> List[Tuple[str, Paper]]:
    """
    Apply a lightweight pre-filter using simple_relevance_score.
    """
    scored_papers = []
    for pid, p in papers:
        s = simple_relevance_score(p.title, p.abstract, keywords)
        scored_papers.append((s, pid, p))
        
    # Sort descending by score
    scored_papers.sort(key=lambda x: x[0], reverse=True)
    
    # Keep logic
    # 1. Always keep top min_keep
    # 2. Keep more if score is decent (heuristic threshold?)
    # For now, just simple top-K clipping based on max_keep, 
    # but respecting min_keep (which is basically implied if max_keep >= min_keep)
    
    # We can be a bit smarter: if we have many papers with score 0, maybe drop them?
    # But for safety, let's just stick to the requested logic:
    # "Sort descending... Keep top-K based on min/max"
    
    # Actually, let's just take top max_keep. 
    # The caller will handle the "only prefilter if > min_papers" check.
    
    kept = scored_papers[:max_keep]
    
    # Log some stats
    if scored_papers:
        logger.info(f"[PreFilter] Top score: {scored_papers[0][0]}, Bottom kept score: {kept[-1][0]}")
        if len(scored_papers) > max_keep:
            logger.info(f"[PreFilter] Dropped {len(scored_papers) - len(kept)} papers. Best dropped score: {scored_papers[max_keep][0]}")
            
    return [(pid, p) for _, pid, p in kept]


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
        max_papers = _get_max_abstract_to_score(cfg, stage)
    else:
        max_papers = 80 # Default fallback

    logger.info(f"[PriorArt] Scoring top {max_papers} papers (abstract-only) [Stage: {stage}]...")
    
    # Sort papers by citation count
    all_papers = list(state.papers.items())
    all_papers.sort(key=lambda x: x[1].citation_count or 0, reverse=True)

    # Pre-filter logic
    if cfg and cfg.enable_prefilter and len(all_papers) > cfg.prefilter_min_papers:
        logger.info(
            "[PriorArt] Applying lightweight pre-filter before LLM scoring: "
            f"{len(all_papers)} candidate papers -> at most {cfg.prefilter_max_keep} for this round."
        )
        keywords = generate_relevance_keywords_from_idea(
            agent=agent,
            topic=state.intent.topic,
            idea=idea_description,
            facets=facets,
            max_keywords=cfg.prefilter_max_keywords,
        )
        logger.info(f"[PriorArt] Generated relevance keywords: {keywords}")
        
        all_papers = prefilter_papers_for_llm_scoring(
            papers=all_papers,
            keywords=keywords,
            min_keep=10,
            max_keep=cfg.prefilter_max_keep,
        )
    
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
- "notes": brief justification (1-2 sentences)

JSON output:"""

        try:
            result = call_llm_json(agent, prompt)
            result_str = _extract_text_from_llm_response(result)
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
                coverage_level = int(score_data.get('coverage_level', 0))
                covered_facets_labels = score_data.get('covered_facets', [])
                notes = score_data.get('notes', '')
                
                # Convert facet labels (F1, F2) to actual facet text
                covered_facets = []
                for label in covered_facets_labels:
                    if isinstance(label, str) and label.startswith('F'):
                        try:
                            idx = int(label[1:]) - 1
                            if 0 <= idx < len(facets):
                                covered_facets.append(facets[idx])
                        except ValueError:
                            pass
                
                matches.append(PriorArtMatch(
                    paper_id=paper_id,
                    coverage_level=coverage_level,
                    covered_facets=covered_facets,
                    notes=notes
                ))
                
                # Compute structural score
                struct_score, struct_details = compute_structural_overlap(idea_features, paper)
                matches[-1].structural_score = struct_score
                matches[-1].structural_overlap = struct_details
                
                # Compute high-level facet score
                hl_score, hl_details = compute_high_level_facet_overlap(idea_high_level_facets, paper.high_level_facets)
                matches[-1].high_level_facet_score = hl_score
                matches[-1].high_level_facet_matches = hl_details
                
                logger.info(f"[PriorArt] Finished scoring {paper.title}: level={coverage_level}")
            else:
                # Fallback
                logger.warning(f"[PriorArt] Failed to parse scoring JSON for {paper.title}, fallback to 0.")
                matches.append(PriorArtMatch(
                    paper_id=paper_id,
                    coverage_level=0,
                    covered_facets=[],
                    notes="Failed to parse LLM output."
                ))
                
        except Exception as e:
            logger.warning(f"Failed to score paper {paper.title}: {e}")
            matches.append(PriorArtMatch(
                paper_id=paper_id,
                coverage_level=0,
                covered_facets=[],
                notes=f"Scoring error: {str(e)[:50]}"
            ))
    
    # Sort by coverage_level (desc), then citation_count (desc)
    matches.sort(
        key=lambda m: (
            m.coverage_level,
            m.high_level_facet_score, # High-level structural overlap
            m.structural_score,       # Low-level structural overlap
            state.papers[m.paper_id].citation_count or 0
        ),
        reverse=True
    )
    
    logger.info(f"Scored {len(matches)} papers")
    return matches


def enrich_papers_with_fulltext(state: PipelineState, paper_ids: List[str]) -> None:
    """
    Ensure fulltext chunks exist for the specified papers.
    Downloads PDF/HTML if needed and parses it.
    """
    resolver = PaperResolverTool()
    parser = PdfParserTool()
    pdf_cache_dir = os.path.join(os.getcwd(), "pdf_cache")
    os.makedirs(pdf_cache_dir, exist_ok=True)

    for pid in paper_ids:
        paper = state.papers.get(pid)
        if not paper:
            continue
        
        if paper.chunks: # Already parsed
            continue
            
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
            continue

        # Download PDF to local cache
        local_filename = f"{paper.doi.replace('/', '_') if paper.doi else paper.openalex_id}.pdf"
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
            
        except Exception as e:
            logger.warning(f"Failed to parse PDF {local_pdf_path}: {e}")


def refine_top_papers_with_fulltext(
    agent,
    state: PipelineState,
    matches: List[PriorArtMatch],
    idea_description: str,
    facets: List[str],
    idea_features: Dict[str, Set[str]],
    top_k: int = 20,
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
    """
    logger.info(f"[PriorArt] Refining top {top_k} papers with fulltext...")

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
    enrich_papers_with_fulltext(state, [m.paper_id for m in top_candidates])

    # 3. Generate evidence snippets and refine scoring
    evidence_keywords = make_keywords_for_evidence(idea_features)

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
            resp = call_llm_json(agent, prompt)
            text = _extract_text_from_llm_response(resp)
            refined = json.loads(text)

            # Update the match object in-place
            level_refined = int(refined.get("coverage_level_refined", match.coverage_level))
            role = str(refined.get("role", "unknown")).strip()
            notes = str(refined.get("notes", "")).strip()

            # Update match
            match.refined_coverage_level = level_refined
            match.coverage_level = level_refined
            match.role = role
            match.notes = f"{match.notes}\\n[Refined] {notes}".strip()

        except Exception as e:
            logger.warning(f"[PriorArt] Failed to refine fulltext scoring for {paper.title}: {e}")

    # 4. Return the updated list (all matches, not only top_k)
    return matches


def expand_corpus_via_citation_graph(
    s2_client: SemanticScholarClient,
    state: PipelineState,
    matches: List[PriorArtMatch],
    max_seed_papers: int = 10,
    max_neighbors_per_seed: int = 50,
) -> None:
    """
    Expand the candidate corpus by traversing the citation graph.

    For the top `max_seed_papers` matches with coverage_level >= 2,
    use Semantic Scholar to:
    - fetch their references (earlier work),
    - fetch their citations (later work).

    For each new paper discovered:
    - convert it into a `Paper` object,
    - add it into `state.papers` if not already present.

    This function should NOT do any scoring; it only enriches the corpus.
    """
    logger.info(f"[CitationGraph] Expanding corpus via citation graph...")

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

    new_paper_count = 0
    for match in seeds:
        paper = state.papers[match.paper_id]
        logger.info(f"[CitationGraph] Expanding from seed: {paper.title[:60]}...")

        # Determine S2 paper ID
        s2_id = paper.s2_id or paper.doi
        if not s2_id:
            logger.warning(f"[CitationGraph] No S2/DOI for paper {paper.title[:40]}, skipping.")
            continue

        # 2. Fetch references (older papers)
        references = s2_client.get_references(
            s2_id,
            fields="paperId,title,year,abstract,venue,citationCount,externalIds",
            limit=max_neighbors_per_seed // 2
        )
        for ref in references:
            if _add_s2_paper_to_state(ref, state):
                new_paper_count += 1

        # 3. Fetch citations (newer papers)
        citations = s2_client.get_citations(
            s2_id,
            fields="paperId,title,year,abstract,venue,citationCount,externalIds",
            limit=max_neighbors_per_seed // 2
        )
        for cit in citations:
            if _add_s2_paper_to_state(cit, state):
                new_paper_count += 1

    logger.info(f"[CitationGraph] Added {new_paper_count} new papers from citation graph.")


def _add_s2_paper_to_state(s2_paper: Dict[str, Any], state: PipelineState) -> bool:
    """
    Convert a Semantic Scholar paper dict to our Paper model and add to state.
    
    Returns True if a new paper was added, False if it already existed.
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

    state.papers[canonical_key] = paper
    return True


# ----------------------------------------------------------------------
# Helper functions for report rendering
# ----------------------------------------------------------------------

from collections import Counter
from typing import Iterable, Sequence

import re

def normalize_facet_phrase(s: str) -> str:
    """
    Normalize a facet phrase for fuzzy matching.
    - Lowercase
    - Strip whitespace
    - Collapse multiple spaces
    - Standardize 2d/3d
    - Fix common typos (ffound, etc.)
    """
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    
    # Standardize 2d/3d
    s = s.replace("2d", "2D").replace("3d", "3D")
    
    # Fix common typos from LLM output
    s = s.replace("ffound", "found")
    s = s.replace("inn ", "in ")
    s = s.replace("synchronnized", "synchronized")
    
    return s


def _count_coverage(
    target_names: Set[str],
    per_paper_feature_sets: Iterable[Set[str]],
) -> Dict[str, int]:
    """
    Count how many papers cover each target feature (e.g., dataset).
    Uses approximate substring matching.
    """
    # Pre-normalize targets
    normalized_targets = {normalize_facet_phrase(n): n for n in target_names}
    counts = {orig: 0 for orig in target_names}

    for feature_set in per_paper_feature_sets:
        # Normalize paper features
        normalized_features = [normalize_facet_phrase(f) for f in feature_set]
        
        # Check each target against all paper features
        for norm_target, orig_target in normalized_targets.items():
            if not norm_target:
                continue
                
            for nf in normalized_features:
                if not nf:
                    continue
                    
                # Approximate matching: substring in either direction
                # e.g. "data augmentation" matches "geometric data augmentation"
                if norm_target in nf or nf in norm_target:
                    counts[orig_target] += 1
                    break  # Count at most once per paper

    return counts


def _bucketize(count: int) -> str:
    """Return a simple label based on count."""
    if count == 0:
        return "gap"        # ⚠ potential gap
    elif count == 1:
        return "rare"       # rare / emerging
    else:
        return "common"     # common / saturated-ish


def render_structural_coverage_section(
    idea_features: Dict[str, Set[str]],
    state: PipelineState,
) -> str:
    """
    Render a section summarizing how the idea's structural features 
    compare to the retrieved papers.
    """
    # Collect per-paper features
    paper_sets_datasets = [set(d.lower() for d in p.datasets) for p in state.papers.values()]
    paper_sets_metrics = [set(m.lower() for m in p.metrics) for p in state.papers.values()]
    paper_sets_methods = [set(f.lower() for f in p.method_families) for p in state.papers.values()]

    # Count coverage only for features present in the idea
    dataset_counts = _count_coverage(idea_features["datasets"], paper_sets_datasets)
    metric_counts = _count_coverage(idea_features["metrics"], paper_sets_metrics)
    method_counts = _count_coverage(idea_features["method_families"], paper_sets_methods)

    lines = []
    lines.append("## Structural Coverage of Your Idea")
    lines.append("Analysis of your idea's structural features against the retrieved corpus:")
    lines.append("")
    lines.append("> **Note**: Counts are based on approximate phrase matching and may under- or over-count semantically related work.")
    lines.append("")

    def add_block(title: str, counts: Dict[str, int]):
        if not counts:
            return
        lines.append(f"**{title}**")
        for name, count in sorted(counts.items(), key=lambda x: x[1]):
            bucket = _bucketize(count)
            if bucket == "gap":
                note = "→ ⚠️ potential gap"
            elif bucket == "rare":
                note = "→ rare / emerging"
            else:
                note = "→ common"
            lines.append(f"- {name} — matched in ~{count} paper(s) {note}")
        lines.append("")  # blank line

    add_block("Datasets", dataset_counts)
    add_block("Method Families", method_counts)
    add_block("Metrics", metric_counts)
    
    if len(lines) <= 5: # If no blocks were added (header + disclaimer = 5 lines)
        lines.append("*No structural features extracted from idea to analyze.*")
        lines.append("")

    return "\\n".join(lines)


def make_keywords_for_evidence(
    idea_features: Dict[str, Set[str]],
    facet_synonyms: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """
    Generate keywords for searching evidence snippets.

    Now extended to include:
    - datasets, metrics, method_families (structural features),
    - facet synonyms / paraphrases (if provided),
    - a few generic augmentation keywords.
    """
    kws = set()
    kws.update(idea_features["datasets"])
    kws.update(idea_features["metrics"])
    kws.update(idea_features["method_families"])

    if facet_synonyms:
        for facet, syns in facet_synonyms.items():
            kws.add(facet.lower())
            kws.update([s.lower() for s in syns])

    # Add generic keywords
    extra = ["camera space", "optical center", "augmentation", "rotation", "depth"]
    kws.update(extra)
    
    return list(kws)


def expand_facet_synonyms(
    agent,
    facets: List[str],
    max_synonyms_per_facet: int = 6,
) -> Dict[str, List[str]]:
    """
    Use the LLM to generate synonyms and alternative phrasings
    for each facet.

    This will be used to:
    - enrich evidence-snippet keyword search
    - increase recall when scanning fulltext for mentions of the idea.

    Returns:
        A dict: facet -> list of keyword strings.
    """
    prompt = f"""
You are a research assistant.

For each of the following technical facets, generate up to {max_synonyms_per_facet}
synonyms or alternative phrasings that might be used in a paper (titles, abstracts,
or method descriptions). Include:
- technical synonyms,
- common paraphrases,
- typical phrases that describe the same concept.

Return ONLY raw JSON of the form:
{{
  "facet 1 text": ["synonym 1", "synonym 2", ...],
  "facet 2 text": [...]
}}

Facets:
{chr(10).join(f"- {f}" for f in facets)}

JSON:
"""
    resp = call_llm_json(agent, prompt)
    text = _extract_text_from_llm_response(resp)
    try:
        data = json.loads(text)
    except Exception:
        logger.warning("[PriorArt] Failed to parse facet synonyms JSON, using empty mapping.")
        return {}

    synonyms: Dict[str, List[str]] = {}
    if isinstance(data, dict):
        for facet, arr in data.items():
            if not isinstance(arr, list):
                continue
            clean = [str(x).strip() for x in arr if str(x).strip()]
            if clean:
                synonyms[facet] = clean

    logger.info(f"[PriorArt] Generated facet synonyms for {len(synonyms)} facets.")
    return synonyms


from rank_bm25 import BM25Okapi


def find_evidence_snippets(
    paper: Paper,
    keywords: Sequence[str],
    max_snippets: int = 3,
    max_chars: int = 240,
) -> List[str]:
    """
    Find relevant snippets in paper chunks using BM25.
    """
    if not getattr(paper, "chunks", None):
        return []

    kws = [k for k in keywords if k]
    if not kws:
        return []
        
    # Prepare query
    query_str = " ".join(kws).lower()
    tokenized_query = query_str.split()

    # Prepare corpus
    chunks = paper.chunks
    corpus_texts = [c.text.replace("\\n", " ") for c in chunks]
    tokenized_corpus = [t.lower().split() for t in corpus_texts]

    # BM25 Scoring
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(tokenized_query)

    # Filter and sort
    indexed_scores = [(i, s) for i, s in enumerate(scores) if s > 0]
    if not indexed_scores:
        return []

    indexed_scores.sort(key=lambda x: x[1], reverse=True)
    
    snippets = []
    for idx, score in indexed_scores[:max_snippets]:
        chunk = chunks[idx]
        text = corpus_texts[idx].strip()
        
        # Truncate if too long
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
            
        page_info = ""
        if chunk.page_start is not None:
            if chunk.page_end and chunk.page_end != chunk.page_start:
                page_info = f" (p. {chunk.page_start}–{chunk.page_end})"
            else:
                page_info = f" (p. {chunk.page_start})"
        snippets.append(f'"{text}"{page_info}')
        
    return snippets



def render_high_level_coverage_section(
    idea_facets: Dict[str, Set[str]],
    state: PipelineState,
) -> str:
    """
    Render a section summarizing high-level structural coverage.
    """
    lines = []
    lines.append("## High-level Structural Coverage")
    lines.append("Analysis of problem, setting, mechanism, and assumptions:")
    lines.append("")
    lines.append("> **Note**: Counts are based on approximate phrase matching and may under- or over-count semantically related work.")
    lines.append("")
    
    for ftype in HIGH_LEVEL_FACET_TYPES:
        # Collect all values for this facet type from papers
        paper_values = []
        for p in state.papers.values():
            paper_values.append(set(v.lower() for v in p.high_level_facets.get(ftype, [])))
            
        # Count coverage for idea facets
        counts = _count_coverage(idea_facets.get(ftype, set()), paper_values)
        
        if not counts:
            continue
            
        # Format title (e.g., "problem_facets" -> "Problem Facets")
        title = ftype.replace("_", " ").title()
        lines.append(f"### {title}")
        
        for name, count in sorted(counts.items(), key=lambda x: x[1]):
            bucket = _bucketize(count)
            if bucket == "gap":
                note = "→ ⚠️ potential gap"
            elif bucket == "rare":
                note = "→ rare / emerging"
            else:
                note = "→ common"
            lines.append(f"- `{name}` — matched in ~{count} paper(s) {note}")
        lines.append("")
        
    if len(lines) <= 5:
        lines.append("*No high-level facets extracted from idea.*")
        lines.append("")
        
    return "\\n".join(lines)


def _clean_note_for_report(note: str) -> str:
    """
    Clean up internal tags (like [Refined]) from notes for the final report.
    """
    if not note:
        return ""
    
    lines = []
    for line in note.splitlines():
        # Remove lines that look like internal markers
        if line.strip().startswith("[Refined]"):
            # Option: keep the text after the marker
            cleaned = line.replace("[Refined]", "").strip()
            if cleaned:
                lines.append(cleaned)
        else:
            lines.append(line)
            
    return "\n".join(lines).strip()


def render_prior_art_report(
    idea_description: str,
    facets: List[str],
    scored_papers: List[PriorArtMatch],
    state: PipelineState,
    idea_features: Dict[str, Set[str]],
    idea_high_level_facets: Dict[str, Set[str]],
) -> str:
    """
    Render a structured Markdown report for the prior-art check.

    This function is pure: it only reads from the inputs and returns a string.
    """
    lines = []
    
    # 1. Title
    lines.append(f"# Prior-Art Check: {state.intent.topic}")
    lines.append("")
    
    # 2. Idea under review
    lines.append("## 1. Idea Under Review")
    lines.append("")
    lines.append(idea_description)
    lines.append("")
    lines.append("**Key Technical Facets:**")
    for i, facet in enumerate(facets, 1):
        lines.append(f"- **F{i}**: {facet}")
    lines.append("")
    
    # 3. High-overlap candidates (level 3)
    lines.append("## 2. High-Overlap Candidates (Coverage Level = 3)")
    lines.append("⚠️ These papers may present potential conflicts or very similar approaches:")
    lines.append("")
    
    high_matches = [m for m in scored_papers if m.coverage_level == 3]
    
    # Generate keywords for evidence search
    evidence_keywords = make_keywords_for_evidence(idea_features)
    
    if high_matches:
        for match in high_matches:
            paper = state.papers[match.paper_id]
            lines.append(f"### {paper.title}")
            lines.append(f"- **Venue**: {paper.venue or 'Unknown'}, {paper.year or 'Unknown'}")
            lines.append(f"- **Citations**: {paper.citation_count or 'N/A'}")
            lines.append(f"- **Covered Facets**: {', '.join(match.covered_facets) if match.covered_facets else 'N/A'}")
            if match.structural_score > 0:
                lines.append(f"- **Structural Overlap**: {', '.join(match.structural_overlap)}")
            lines.append(f"- **Notes**: {_clean_note_for_report(match.notes)}")
            if paper.doi:
                lines.append(f"- **DOI**: [{paper.doi}](https://doi.org/{paper.doi})")
            
            # Evidence Snippets
            snippets = find_evidence_snippets(paper, evidence_keywords, max_snippets=3)
            if snippets:
                lines.append("")
                lines.append("**Evidence snippets**")
                for s in snippets:
                    lines.append(f"- {s}")
            
            lines.append("")
    else:
        lines.append("- None found.")
        lines.append("")
    
    # 4. Partial-overlap (level 2)
    lines.append("## 3. Partial-Overlap Prior Art (Coverage Level = 2)")
    lines.append("These papers cover some aspects of the idea:")
    lines.append("")
    
    partial_matches = [m for m in scored_papers if m.coverage_level == 2]
    if partial_matches:
        for match in partial_matches:
            paper = state.papers[match.paper_id]
            lines.append(f"### {paper.title}")
            lines.append(f"- **Venue**: {paper.venue or 'Unknown'}, {paper.year or 'Unknown'}")
            lines.append(f"- **Citations**: {paper.citation_count or 'N/A'}")
            lines.append(f"- **Covered Facets**: {', '.join(match.covered_facets) if match.covered_facets else 'N/A'}")
            if match.structural_score > 0:
                lines.append(f"- **Structural Overlap**: {', '.join(match.structural_overlap)}")
            lines.append(f"- **Notes**: {_clean_note_for_report(match.notes)}")
            lines.append("")
    else:
        lines.append("- None found.")
        lines.append("")
    
    # 5. Adjacent Work
    lines.append("## 4. Adjacent Work (Coverage Level = 1)")
    lines.append("Related work in the same domain but with different approaches:")
    lines.append("")
    
    adjacent_matches = [m for m in scored_papers if m.coverage_level == 1]
    if adjacent_matches:
        for match in adjacent_matches[:10]:  # Limit to top 10
            paper = state.papers[match.paper_id]
            lines.append(f"- **{paper.title}** ({paper.venue or 'Unknown'}, {paper.year or 'Unknown'})")
            lines.append(f"  - {_clean_note_for_report(match.notes)}")
    else:
        lines.append("- None found.")
    lines.append("")
    
    # 5. High-level Structural Coverage (New)
    lines.append(render_high_level_coverage_section(idea_high_level_facets, state))
    
    # 6. Structural Coverage Section (Existing - datasets/metrics/methods)
    lines.append(render_structural_coverage_section(idea_features, state))
    
    # 7. Facet-Level Gap Analysis
    lines.append("## 6. Facet-Level Gap Analysis")
    lines.append("Coverage of each facet in papers with partial or high overlap:")
    lines.append("")
    
    # Count coverage for each facet (only in level 2 and 3)
    relevant_matches = [m for m in scored_papers if m.coverage_level >= 2]
    
    for i, facet in enumerate(facets, 1):
        count = sum(1 for m in relevant_matches if facet in m.covered_facets)
        lines.append(f"- **F{i}**: \"{facet}\" — covered in {count} paper(s)")
        if count == 0:
            lines.append(f"  - ⭐ **Potential gap**: Not clearly covered by retrieved papers")
    
    lines.append("")
    lines.append("---")
    lines.append("**Disclaimer**: This is an automated prior-art check based on paper metadata and LLM analysis. "
                "Please manually verify key references and read full papers before making high-stakes decisions.")
    
    return "\\n".join(lines)


def run_prior_art_check(
    agent,
    idea_description: str,
    topic: str,
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
    max_rounds: int = 2,
    use_citation_graph: bool = True,
    debug_mode: bool = False,
) -> str:
    """
    High-level entry point for the prior-art check task.

    Flow:
      1) Build Intent and initial PipelineState.
      2) Run the exploration loop to collect a candidate corpus.
      3) Decompose the idea into facets.
      4) Score papers against these facets.
      5) Render a structured report.
    """
    intent = build_prior_art_intent(
        idea_description=idea_description,
        topic=topic,
        years=years,
        venues=venues,
    )
    state = PipelineState(intent=intent)
    cfg = PriorArtConfig(debug_mode=debug_mode)

    logger.info(f"[PriorArtCheck] Starting for topic='{topic}'. DebugMode={debug_mode}")

    # 1) Let the LLM propose seed papers (world knowledge).
    llm_seed_dicts = seed_papers_from_llm(agent, idea_description, topic)
    validated_seed_papers = validate_llm_seeds_via_openalex(llm_seed_dicts)
    for p in validated_seed_papers:
        # Make sure to insert into state.papers with a stable paper_id
        # Use DOI or OpenAlex ID as key
        key = p.doi if p.doi else p.openalex_id
        if key and key not in state.papers:
            state.papers[key] = p
            logger.info(f"[PriorArt] Added seed paper: {p.title}")

    # 2) Ask the LLM to generate search queries.
    search_queries = generate_search_queries_from_idea(agent, idea_description, topic)

    state = run_exploration_loop(
        agent=agent,
        state=state,
        max_rounds=max_rounds,
        search_queries=search_queries,
    )

    logger.info(
        f"[PriorArtCheck] Exploration finished with "
        f"{len(state.papers)} candidate papers."
    )

    facets = decompose_idea_into_facets(agent, idea_description)
    idea_features = extract_idea_features(agent, idea_description)
    idea_high_level_facets = extract_high_level_facets_from_idea(agent, idea_description)
    
    # Stage 1: abstract-level coarse scoring (cheap, no fulltext)
    matches = score_papers_with_abstract_only(
        agent=agent, 
        state=state, 
        facets=facets, 
        idea_features=idea_features, 
        idea_high_level_facets=idea_high_level_facets,
        idea_description=idea_description,
        cfg=cfg,
        stage="initial"
    )

    # Citation graph expansion (optional)
    if use_citation_graph:
        s2_client = SemanticScholarClient()
        expand_corpus_via_citation_graph(
            s2_client=s2_client,
            state=state,
            matches=matches,
            max_seed_papers=5,
            max_neighbors_per_seed=30,
        )
        
        # Re-score newly added papers at abstract level
        logger.info(f"[PriorArt] Re-scoring {len(state.papers)} total papers after citation expansion...")
        matches = score_papers_with_abstract_only(
            agent=agent,
            state=state,
            facets=facets,
            idea_features=idea_features,
            idea_high_level_facets=idea_high_level_facets,
            idea_description=idea_description,
            cfg=cfg,
            stage="after_citation"
        )

    # Stage 2: fulltext refinement for top-K candidates.
    matches = refine_top_papers_with_fulltext(
        agent=agent,
        state=state,
        matches=matches,
        idea_description=idea_description,
        facets=facets,
        idea_features=idea_features,
        top_k=20,
    )

    report = render_prior_art_report(
        idea_description=idea_description,
        facets=facets,
        scored_papers=matches,
        state=state,
        idea_features=idea_features,
        idea_high_level_facets=idea_high_level_facets,
    )
    return report
