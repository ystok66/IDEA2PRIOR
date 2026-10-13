# pipelines/evidence.py

"""
EvidenceSpan and unified facet coverage computation.

This module provides:
- EvidenceSpan: A structured representation of evidence linking papers to facets
- Facet pattern matching for monocular 3D detection ideas
- Unified coverage computation that both structural and facet-level analysis use
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Set, Optional

from loguru import logger

from pipelines.paper_record import PaperRecord, SectionRecord
from pipelines.runtime import invoke_model


# ========== Sentence Role Classification ==========

OWN_METHOD_PATTERNS = [
    "we propose", "we present", "we introduce", "our method", "our approach",
    "in this paper, we", "in this work, we", "we develop", "we design",
    "our contribution", "we contribute", "our novel", "we offer",
]

PRIOR_WORK_PATTERNS = [
    "previous work", "prior work", "existing methods", "earlier approaches",
    "has been widely studied", "existing work", "related work",
    "previous methods", "prior methods", "state-of-the-art",
]

BACKGROUND_PATTERNS = [
    "in general", "it is well known", "it is widely known",
    "background", "in this section, we review", "typically",
    "commonly used", "standard approach", "conventional",
]


def infer_sentence_role(sentence: str, section_role: str = "other") -> str:
    """
    Infer whether a sentence describes own method, prior work, or background.
    
    Args:
        sentence: The sentence text
        section_role: The role of the containing section (for fallback)
        
    Returns:
        One of: "own_method", "prior_work", "background"
    """
    s = sentence.lower()
    
    if any(p in s for p in OWN_METHOD_PATTERNS):
        return "own_method"
    if any(p in s for p in PRIOR_WORK_PATTERNS):
        return "prior_work"
    if any(p in s for p in BACKGROUND_PATTERNS):
        return "background"
    
    # Fallback based on section role
    if section_role in {"method", "experiments"}:
        return "own_method"
    if section_role == "related_work":
        return "prior_work"
    
    return "background"


# ========== Facet Patterns ==========

# Facet patterns for monocular 3D detection + rotation augmentation idea
FACET_PATTERNS = {
    # Core idea facets (F1-F4)
    "F1_camera_space_rotation": [
        "camera-space rotation", "camera space rotation", "viewpoint rotation",
        "camera-centric rotation", "camera coordinate rotation", "view-space rotation",
        "rotation in camera space", "camera-based rotation",
    ],
    "F2_geometry_consistency": [
        "2d-3d consistency", "geometric consistency", "projective consistency",
        "geometry-consistent", "2d 3d consistency", "geometric constraint",
        "projection consistency", "3d-2d consistency",
    ],
    "F3_depth_free": [
        "without depth", "no depth", "depth-free", "no depth supervision",
        "depth independent", "without depth estimation", "no explicit depth",
    ],
    "F4_multimodal_rgb_lidar": [
        "rgb and lidar", "lidar+rgb", "multi-modal fusion", "sensor fusion",
        "camera-lidar fusion", "rgb-lidar", "multimodal 3d", "fusion of camera and lidar",
    ],
    
    # Structural facets (problem/setting/mechanism)
    "problem_monocular3d": [
        "monocular 3d detection", "monocular 3-d detection", "mono 3d detection",
        "single-image 3d detection", "image-based 3d detection",
    ],
    "setting_camera_space": [
        "camera-space coordinate", "camera frame", "camera coordinate system",
        "camera-centric coordinate", "ego-vehicle coordinate",
    ],
    "mechanism_rotation_aug": [
        "rotation augmentation", "rotational augmentation", "view augmentation",
        "geometric augmentation", "3d rotation", "viewpoint augmentation",
    ],
    "mechanism_data_aug": [
        "data augmentation", "training augmentation", "augmentation strategy",
        "augmentation technique", "image augmentation",
    ],
}


# ========== Dynamic Facet Pattern Generation (LLM-based) ==========

def generate_facet_patterns_from_idea(
    idea_description: str,
    max_facets: int = 6,
    max_patterns_per_facet: int = 10,
    facets: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, List[str]]:
    """
    Use expert LLM to dynamically generate facet patterns from the idea.
    
    This replaces hardcoded FACET_PATTERNS with idea-specific patterns.
    
    Args:
        idea_description: The research idea
        max_facets: Maximum number of facets to generate
        max_patterns_per_facet: Maximum patterns per facet
        
    Returns:
        Dict mapping facet_id to list of lexical patterns
    """
    import json
    from smolagents import LiteLLMModel
    from pipelines.llm_config import get_litellm_config
    from pipelines.tasks.prior_art_llm_utils import (
        extract_text_from_llm_response, 
        repair_json_output,
    )
    
    model_id, api_base, api_key = get_litellm_config(role="expert")
    expert_model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
    
    # Step 1: Generate facets from idea
    if facets is None:
        facets = _generate_facets_from_idea(
            idea_description=idea_description,
            expert_model=expert_model,
            max_facets=max_facets,
        )
    
    if not facets:
        logger.warning("[Evidence] Failed to generate facets, using empty patterns")
        return {}
    
    logger.info(f"[Evidence] Generated {len(facets)} facets from idea:")
    for f in facets:
        logger.info(f"  - {f['id']}: {f['name']}")
    
    # Step 2: Generate patterns for each facet
    facet_patterns = _generate_patterns_for_facets(
        idea_description=idea_description,
        facets=facets,
        expert_model=expert_model,
        max_patterns_per_facet=max_patterns_per_facet,
    )
    
    allowed = {f['id'] for f in facets}
    if set(facet_patterns) - allowed:
        raise ValueError('Pattern response references undefined technical facets')
    return facet_patterns


def _generate_facets_from_idea(
    idea_description: str,
    expert_model,
    max_facets: int = 6,
) -> List[Dict[str, str]]:
    """
    Step 1: Generate facet definitions from the idea.
    """
    import json
    from pipelines.tasks.prior_art_llm_utils import (
        extract_text_from_llm_response, 
        repair_json_output,
    )
    
    prompt = f"""You are an expert researcher.

Given the following research idea:

\"\"\"{idea_description}\"\"\"

1. Identify the main technical facets (sub-aspects) needed to understand
   and evaluate this idea. For example, if the idea describes a new training
   method, facets might include "base model architecture", "training objective",
   "data augmentation", "optimization algorithm", etc.
2. Each facet should be short (a few words) and technical.
3. Facets should be distinct from each other.

Output format:
Return ONLY a JSON object:

{{
  "facets": [
    {{
      "id": "F1",
      "name": "...",
      "description": "1-2 sentence explanation"
    }},
    ...
  ]
}}

Constraints:
- At most {max_facets} facets.
- Do NOT include any extra commentary outside the JSON.
"""
    
    messages = [{"role": "user", "content": prompt}]
    resp = invoke_model(expert_model, messages, stage="evidence_patterns")
    text = extract_text_from_llm_response(resp)
    
    try:
        data = json.loads(text)
        return data.get("facets", [])
    except Exception:
        try:
            repaired = repair_json_output(text)
            data = json.loads(repaired)
            return data.get("facets", [])
        except Exception:
            logger.warning("[Evidence] Failed to parse facet generation JSON")
            return []


def _generate_patterns_for_facets(
    idea_description: str,
    facets: List[Dict[str, str]],
    expert_model,
    max_patterns_per_facet: int = 10,
) -> Dict[str, List[str]]:
    """
    Step 2: Generate lexical patterns for each facet.
    """
    import json
    from pipelines.tasks.prior_art_llm_utils import (
        extract_text_from_llm_response, 
        repair_json_output,
    )
    
    facets_str = json.dumps(facets, ensure_ascii=False, indent=2)
    
    prompt = f"""You are helping to design lexical patterns for detecting evidence sentences
in research papers related to the following idea:

\"\"\"{idea_description}\"\"\"

You are given a list of facets in JSON:

```json
{facets_str}
```

For each facet, you must propose a list of lexical patterns (short phrases)
that are likely to appear in sentences that provide evidence for that facet.

Guidelines:
- Patterns should be in English.
- Patterns should be short phrases that can be matched by substring search.
- Include both:
  - specific phrases directly from the facet name/description,
  - and a few variants or synonyms.
- Avoid extremely generic words alone (e.g., just "model" or "data").
- Patterns should be directly grounded in the facet name and description.

Output format:
Return ONLY a JSON object:

{{
  "facet_patterns": [
    {{
      "facet_id": "F1",
      "patterns": ["...", "...", ...]
    }},
    ...
  ]
}}

Constraints:
- For each facet, at most {max_patterns_per_facet} patterns.
- Do NOT include any extra commentary outside the JSON.
"""
    
    messages = [{"role": "user", "content": prompt}]
    resp = invoke_model(expert_model, messages, stage="evidence_patterns")
    text = extract_text_from_llm_response(resp)
    
    try:
        data = json.loads(text)
        facet_patterns_list = data.get("facet_patterns", [])
    except Exception:
        try:
            repaired = repair_json_output(text)
            data = json.loads(repaired)
            facet_patterns_list = data.get("facet_patterns", [])
        except Exception:
            logger.warning("[Evidence] Failed to parse pattern generation JSON")
            return {}
    
    # Convert to dict format
    result: Dict[str, List[str]] = {}
    for fp in facet_patterns_list:
        fid = fp.get("facet_id", "")
        patterns = [p.strip().lower() for p in fp.get("patterns", []) if p.strip()]
        if fid and patterns:
            result[fid] = patterns
    
    logger.info(f"[Evidence] Generated patterns for {len(result)} facets")
    for fid, plist in result.items():
        logger.info(f"  - {fid}: {len(plist)} patterns")
    
    return result

@dataclass
class EvidenceSpan:
    """
    A structured piece of evidence linking a paper to a facet.
    
    This is the atomic unit for coverage computation - both structural
    coverage and facet-level gap analysis read from EvidenceSpans.
    """
    paper_id: str
    facet_label: str            # e.g., "F1_camera_space_rotation", "problem_monocular3d"
    section_role: str           # intro / related_work / method / experiments / other
    sentence: str               # The actual sentence containing evidence
    
    role: str                   # own_method / prior_work / background
    strength: float             # ∈ [0, 1], strength of evidence
    source: str                 # title / abstract / section
    section_id: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    document_version: Optional[str] = None


def collect_facet_evidence(
    paper_records: List[PaperRecord],
    facet_patterns: Dict[str, List[str]] = None,
    idea_keywords: Optional[List[str]] = None,
    use_bm25: bool = False,
    bm25_top_k: int = 3,
    min_evidence_for_bm25: int = 2,
) -> List[EvidenceSpan]:
    """
    Collect all facet evidence from a list of PaperRecords.
    
    Iterates over title, abstract, and section sentences, matching
    against facet patterns to produce EvidenceSpans.
    
    Args:
        paper_records: List of PaperRecords to analyze
        facet_patterns: Optional custom facet patterns (defaults to FACET_PATTERNS)
        idea_keywords: Optional list of dynamic keywords from LLM (used for __idea_global__ evidence)
        use_bm25: If True, use BM25 to backfill papers with insufficient evidence
        bm25_top_k: Number of top sentences to extract via BM25 per paper
        min_evidence_for_bm25: Minimum evidence threshold; BM25 only applied if below this
        
    Returns:
        List of EvidenceSpan objects
    """
    if facet_patterns is None:
        facet_patterns = FACET_PATTERNS
    
    evidence: List[EvidenceSpan] = []
    
    # Track evidence per paper for BM25 backfill decision
    evidence_by_paper: Dict[str, List[EvidenceSpan]] = {}
    
    for record in paper_records:
        paper_id = record.paper_id
        evidence_by_paper[paper_id] = []
        
        # =========================================================
        # Step 1: Facet-specific pattern matching (original logic)
        # =========================================================
        
        # Check title (high strength)
        title_lower = record.title.lower()
        for facet_label, patterns in facet_patterns.items():
            if any(p in title_lower for p in patterns):
                span = EvidenceSpan(
                    paper_id=paper_id,
                    facet_label=facet_label,
                    section_role="other",
                    sentence=record.title,
                    role="own_method",  # Title usually describes own contribution
                    strength=0.9,
                    source="title",
                )
                evidence.append(span)
                evidence_by_paper[paper_id].append(span)
        
        # Check abstract
        if record.abstract:
            abstract_sentences = [
                s.strip() for s in record.abstract.split(". ")
                if s.strip()
            ]
            for sent in abstract_sentences:
                sent_lower = sent.lower()
                sent_role = infer_sentence_role(sent, "intro")
                
                for facet_label, patterns in facet_patterns.items():
                    if any(p in sent_lower for p in patterns):
                        # Strength based on sentence role
                        strength = 0.8 if sent_role == "own_method" else 0.5
                        span = EvidenceSpan(
                            paper_id=paper_id,
                            facet_label=facet_label,
                            section_role="abstract",
                            sentence=sent,
                            role=sent_role,
                            strength=strength,
                            source="abstract",
                        )
                        evidence.append(span)
                        evidence_by_paper[paper_id].append(span)
        
        # Check sections
        for section in record.sections:
            for sent in section.sentences:
                sent_lower = sent.lower()
                sent_role = infer_sentence_role(sent, section.role)
                
                for facet_label, patterns in facet_patterns.items():
                    if any(p in sent_lower for p in patterns):
                        # Strength based on section role and sentence role
                        if section.role in {"intro", "method"} and sent_role == "own_method":
                            strength = 1.0
                        elif section.role == "related_work":
                            strength = 0.6
                        elif section.role in {"experiments", "conclusion"}:
                            strength = 0.4
                        else:
                            strength = 0.5
                        
                        span = EvidenceSpan(
                            paper_id=paper_id,
                            facet_label=facet_label,
                            section_role=section.role,
                            sentence=sent,
                            role=sent_role,
                            strength=strength,
                            source="section",
                            section_id=section.section_id, page_start=section.page_start, page_end=section.page_end, document_version=section.document_version,
                        )
                        evidence.append(span)
                        evidence_by_paper[paper_id].append(span)
        
        # =========================================================
        # Step 2: idea_keywords matching (__idea_global__ facet)
        # =========================================================
        if idea_keywords:
            idea_kw_lower = [kw.lower() for kw in idea_keywords]
            
            # Check title against idea_keywords
            for kw in idea_kw_lower:
                if kw in title_lower and len(kw) >= 4:  # Skip very short keywords
                    span = EvidenceSpan(
                        paper_id=paper_id,
                        facet_label="__idea_global__",
                        section_role="other",
                        sentence=record.title,
                        role="own_method",
                        strength=0.7,
                        source="title",
                    )
                    evidence.append(span)
                    evidence_by_paper[paper_id].append(span)
                    break  # One match per title is enough
            
            # Check abstract against idea_keywords
            if record.abstract:
                for sent in abstract_sentences:
                    sent_lower = sent.lower()
                    matched_kw_count = sum(1 for kw in idea_kw_lower if kw in sent_lower and len(kw) >= 4)
                    if matched_kw_count >= 2:  # Need at least 2 keyword matches
                        sent_role = infer_sentence_role(sent, "intro")
                        span = EvidenceSpan(
                            paper_id=paper_id,
                            facet_label="__idea_global__",
                            section_role="abstract",
                            sentence=sent,
                            role=sent_role,
                            strength=0.5 + 0.1 * min(matched_kw_count, 5),  # Scale by match count
                            source="abstract",
                        )
                        evidence.append(span)
                        evidence_by_paper[paper_id].append(span)
            
            # Check sections against idea_keywords (intro/method only for efficiency)
            for section in record.sections:
                if section.role not in {"intro", "method", "related_work"}:
                    continue
                for sent in section.sentences:
                    sent_lower = sent.lower()
                    matched_kw_count = sum(1 for kw in idea_kw_lower if kw in sent_lower and len(kw) >= 4)
                    if matched_kw_count >= 2:
                        sent_role = infer_sentence_role(sent, section.role)
                        strength = 0.5 + 0.1 * min(matched_kw_count, 5)
                        if section.role == "method" and sent_role == "own_method":
                            strength = min(1.0, strength + 0.2)
                        
                        span = EvidenceSpan(
                            paper_id=paper_id,
                            facet_label="__idea_global__",
                            section_role=section.role,
                            sentence=sent,
                            role=sent_role,
                            strength=strength,
                            source="section",
                            section_id=section.section_id, page_start=section.page_start, page_end=section.page_end, document_version=section.document_version,
                        )
                        evidence.append(span)
                        evidence_by_paper[paper_id].append(span)
    
    # =========================================================
    # Step 3: BM25 backfill for papers with insufficient evidence
    # =========================================================
    if use_bm25 and idea_keywords:
        bm25_evidence = _collect_bm25_backfill(
            paper_records=paper_records,
            evidence_by_paper=evidence_by_paper,
            idea_keywords=idea_keywords,
            bm25_top_k=bm25_top_k,
            min_evidence_threshold=min_evidence_for_bm25,
        )
        evidence.extend(bm25_evidence)
    
    logger.debug(f"[Evidence] Collected {len(evidence)} evidence spans from {len(paper_records)} papers")
    return evidence


def _collect_bm25_backfill(
    paper_records: List[PaperRecord],
    evidence_by_paper: Dict[str, List[EvidenceSpan]],
    idea_keywords: List[str],
    bm25_top_k: int = 3,
    min_evidence_threshold: int = 2,
) -> List[EvidenceSpan]:
    """
    Use BM25 to find additional evidence for papers with insufficient evidence.
    
    Only papers with fewer than `min_evidence_threshold` evidence spans will be processed.
    This is a "backfill" strategy - BM25 supplements pattern matching, not replaces it.
    """
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning("[Evidence] rank_bm25 not installed, skipping BM25 backfill")
        return []
    
    bm25_evidence: List[EvidenceSpan] = []
    query_tokens = " ".join(idea_keywords).lower().split()
    
    for record in paper_records:
        paper_id = record.paper_id
        existing_count = len(evidence_by_paper.get(paper_id, []))
        
        # Only backfill if evidence is below threshold
        if existing_count >= min_evidence_threshold:
            continue
        
        # Collect all sentences from this paper
        all_sentences = []  # (text, section_role, source)
        
        if record.abstract:
            for sent in record.abstract.split(". "):
                sent = sent.strip()
                if len(sent) > 30:
                    all_sentences.append((sent, "abstract", "abstract", None))
        
        for section in record.sections:
            if section.role in {"intro", "method", "related_work", "experiment", "experiments"}:
                for sent in section.sentences:
                    if len(sent) > 30:
                        all_sentences.append((sent, section.role, "section", section))
        
        if not all_sentences:
            continue
        
        # Build BM25 index for this paper's sentences
        tokenized = [s[0].lower().split() for s in all_sentences]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(query_tokens)
        
        # Get top-K sentences
        scored = sorted(zip(all_sentences, scores), key=lambda x: x[1], reverse=True)
        
        for (sent_text, section_role, source, section), score in scored[:bm25_top_k]:
            if score <= 0:
                break
            
            sent_role = infer_sentence_role(sent_text, section_role)
            bm25_evidence.append(EvidenceSpan(
                paper_id=paper_id,
                facet_label="__bm25_backfill__",
                section_role=section_role,
                sentence=sent_text[:500],  # Truncate long sentences
                role=sent_role,
                strength=min(0.7, 0.3 + score * 0.1),  # Scale BM25 score to strength
                source=source,
                section_id=section.section_id if section else "",
                page_start=section.page_start if section else None,
                page_end=section.page_end if section else None,
                document_version=section.document_version if section else None,
            ))
    
    logger.debug(f"[Evidence] BM25 backfill added {len(bm25_evidence)} spans for papers with low evidence")
    return bm25_evidence




# ========== Unified Facet Coverage ==========

@dataclass
class FacetCoverageStats:
    """
    Aggregated coverage statistics for a single facet.
    
    Both structural coverage and facet-level gap analysis should
    read from the same FacetCoverageStats to ensure consistency.
    
    This is the ONLY source of truth for coverage counts - no legacy
    phrase counters should be used in report generation.
    """
    facet_label: str
    paper_ids_any: Set[str] = field(default_factory=set)      # Any mention
    paper_ids_strong: Set[str] = field(default_factory=set)   # Strong evidence
    evidence_by_paper: Dict[str, List[EvidenceSpan]] = field(default_factory=dict)
    
    # Breakdowns by role and section
    by_role: Dict[str, int] = field(default_factory=dict)  # own_method/prior_work/background
    by_section_role: Dict[str, int] = field(default_factory=dict)  # intro/related_work/method/etc.
    
    @property
    def count_any(self) -> int:
        return len(self.paper_ids_any)
    
    @property
    def count_strong(self) -> int:
        return len(self.paper_ids_strong)


# Human-readable facet labels
FACET_HUMAN_LABELS = {
    "F1_camera_space_rotation": "Camera-space rotation augmentation",
    "F2_geometry_consistency": "2D-3D geometric consistency",
    "F3_depth_free": "Depth-free augmentation",
    "F4_multimodal_rgb_lidar": "Multi-modal RGB+LiDAR fusion",
    "problem_monocular3d": "Monocular 3D detection",
    "setting_camera_space": "Camera-space coordinates",
    "mechanism_rotation_aug": "Rotation augmentation",
    "mechanism_data_aug": "Data augmentation",
}


@dataclass
class FacetReportView:
    """
    A view object for report generation, built ONLY from FacetCoverageStats.
    
    This class does NOT perform any additional text scanning or phrase counting.
    It is a pure view on already-computed statistics.
    """
    facet_id: str
    human_label: str
    coverage_level: str  # "none", "sparse", "moderate", "heavy"
    stats: FacetCoverageStats
    example_paper_ids_strong: List[str] = field(default_factory=list)
    example_paper_ids_any: List[str] = field(default_factory=list)
    
    @classmethod
    def from_stats(cls, stats: FacetCoverageStats, max_examples: int = 3) -> "FacetReportView":
        """Build a FacetReportView from FacetCoverageStats."""
        human_label = FACET_HUMAN_LABELS.get(stats.facet_label, stats.facet_label)
        
        # Determine coverage level
        if stats.count_strong >= 5:
            coverage_level = "heavy"
        elif stats.count_strong >= 2:
            coverage_level = "moderate"
        elif stats.count_any >= 1:
            coverage_level = "sparse"
        else:
            coverage_level = "none"
        
        return cls(
            facet_id=stats.facet_label,
            human_label=human_label,
            coverage_level=coverage_level,
            stats=stats,
            example_paper_ids_strong=list(stats.paper_ids_strong)[:max_examples],
            example_paper_ids_any=list(stats.paper_ids_any)[:max_examples],
        )


def compute_facet_coverage(
    evidence_spans: List[EvidenceSpan],
) -> Dict[str, FacetCoverageStats]:
    """
    Compute unified facet coverage statistics from evidence spans.
    
    Strong evidence is defined as:
    - strength >= 0.7 AND
    - section_role in {"intro", "related_work", "method", "abstract"}
    
    Args:
        evidence_spans: List of EvidenceSpan objects
        
    Returns:
        Dict mapping facet_label to FacetCoverageStats
    """
    stats: Dict[str, FacetCoverageStats] = {}
    
    for ev in evidence_spans:
        if ev.facet_label not in stats:
            stats[ev.facet_label] = FacetCoverageStats(facet_label=ev.facet_label)
        
        st = stats[ev.facet_label]
        st.paper_ids_any.add(ev.paper_id)
        st.evidence_by_paper.setdefault(ev.paper_id, []).append(ev)
        
        # Update role breakdown
        st.by_role[ev.role] = st.by_role.get(ev.role, 0) + 1
        st.by_section_role[ev.section_role] = st.by_section_role.get(ev.section_role, 0) + 1
        
        # Strong evidence criteria
        is_strong = (
            ev.strength >= 0.7 and 
            ev.section_role in {"intro", "related_work", "method", "abstract", "other"}
        )
        if is_strong:
            st.paper_ids_strong.add(ev.paper_id)
    
    return stats


def coverage_label(num_strong: int, num_any: int) -> str:
    """
    Generate a human-readable coverage label.
    
    Args:
        num_strong: Number of papers with strong evidence
        num_any: Number of papers with any evidence
        
    Returns:
        One of: "well covered", "lightly covered", "not found"
    """
    if num_strong >= 5:
        return "well covered"
    if num_strong >= 2:
        return "moderately covered"
    if num_any >= 1:
        return "lightly covered"
    return "not found"


def summarize_facet_coverage(
    stats: Dict[str, FacetCoverageStats],
) -> Dict[str, Dict]:
    """
    Summarize facet coverage for logging/debugging.
    
    Returns:
        Dict with facet_label -> {count_strong, count_any, label}
    """
    summary = {}
    for facet_label, st in stats.items():
        summary[facet_label] = {
            "count_strong": st.count_strong,
            "count_any": st.count_any,
            "label": coverage_label(st.count_strong, st.count_any),
        }
    return summary


def summarize_facet_for_report(view: FacetReportView) -> str:
    """
    Generate a human-readable summary for a facet in the report.
    
    Uses only FacetReportView data - NO additional text scanning.
    """
    stats = view.stats
    
    if stats.count_any == 0:
        return (
            f"For **{view.human_label}**, we did not find any in-domain papers "
            f"explicitly matching this facet in introductions, related work, or method sections."
        )
    
    if stats.count_strong == 0:
        # Only weak/background mentions
        own_method_count = stats.by_role.get("own_method", 0)
        prior_work_count = stats.by_role.get("prior_work", 0)
        return (
            f"For **{view.human_label}**, we found brief mentions in {stats.count_any} paper(s), "
            f"but without clear methodology or evaluation. "
            f"({own_method_count} own-method, {prior_work_count} prior-work references)"
        )
    
    # Has strong evidence
    intro_count = stats.by_section_role.get("intro", 0)
    method_count = stats.by_section_role.get("method", 0)
    related_count = stats.by_section_role.get("related_work", 0)
    
    section_breakdown = []
    if method_count > 0:
        section_breakdown.append(f"{method_count} in method sections")
    if intro_count > 0:
        section_breakdown.append(f"{intro_count} in introductions")
    if related_count > 0:
        section_breakdown.append(f"{related_count} in related work")
    
    breakdown_str = ", ".join(section_breakdown) if section_breakdown else "various sections"
    
    return (
        f"For **{view.human_label}**, we found {stats.count_strong} in-domain paper(s) "
        f"with strong evidence ({breakdown_str}). "
        f"Total mentions: {stats.count_any} paper(s)."
    )


def build_facet_report_views(
    stats: Dict[str, FacetCoverageStats],
) -> Dict[str, FacetReportView]:
    """Build FacetReportViews for all facets."""
    return {
        facet_label: FacetReportView.from_stats(st)
        for facet_label, st in stats.items()
    }

