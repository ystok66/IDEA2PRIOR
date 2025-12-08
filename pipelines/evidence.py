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


def collect_facet_evidence(
    paper_records: List[PaperRecord],
    facet_patterns: Dict[str, List[str]] = None,
) -> List[EvidenceSpan]:
    """
    Collect all facet evidence from a list of PaperRecords.
    
    Iterates over title, abstract, and section sentences, matching
    against facet patterns to produce EvidenceSpans.
    
    Args:
        paper_records: List of PaperRecords to analyze
        facet_patterns: Optional custom facet patterns (defaults to FACET_PATTERNS)
        
    Returns:
        List of EvidenceSpan objects
    """
    if facet_patterns is None:
        facet_patterns = FACET_PATTERNS
    
    evidence: List[EvidenceSpan] = []
    
    for record in paper_records:
        paper_id = record.paper_id
        
        # Check title (high strength)
        title_lower = record.title.lower()
        for facet_label, patterns in facet_patterns.items():
            if any(p in title_lower for p in patterns):
                evidence.append(EvidenceSpan(
                    paper_id=paper_id,
                    facet_label=facet_label,
                    section_role="other",
                    sentence=record.title,
                    role="own_method",  # Title usually describes own contribution
                    strength=0.9,
                    source="title",
                ))
        
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
                        evidence.append(EvidenceSpan(
                            paper_id=paper_id,
                            facet_label=facet_label,
                            section_role="abstract",
                            sentence=sent,
                            role=sent_role,
                            strength=strength,
                            source="abstract",
                        ))
        
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
                        
                        evidence.append(EvidenceSpan(
                            paper_id=paper_id,
                            facet_label=facet_label,
                            section_role=section.role,
                            sentence=sent,
                            role=sent_role,
                            strength=strength,
                            source="section",
                        ))
    
    logger.debug(f"[Evidence] Collected {len(evidence)} evidence spans from {len(paper_records)} papers")
    return evidence


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

