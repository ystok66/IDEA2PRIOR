# pipelines/coverage_classifier.py

"""
Coverage level assignment based on EvidenceSpans and domain filtering.

This module provides:
- assign_coverage_level: Classify a paper into High(3)/Partial(2)/Adjacent(1)/Ignore(0)
- classify_all_papers: Classify all papers in a batch

Coverage levels are computed from the SAME evidence_spans used for FacetCoverageStats,
ensuring consistency between paper lists and facet statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set

from loguru import logger

from pipelines.paper_record import PaperRecord, is_in_domain_for_topic
from pipelines.evidence import EvidenceSpan


# Key facets that determine coverage level
KEY_FACETS = {
    "F1_camera_space_rotation",
    "F2_geometry_consistency", 
    "F3_depth_free",
    "F4_multimodal_rgb_lidar",
}


def assign_coverage_level(
    paper_id: str,
    evidence_for_paper: List[EvidenceSpan],
    is_in_domain: bool,
) -> int:
    """
    Assign a coverage level to a paper based on its evidence.
    
    Level 3 (High overlap):
        - In-domain
        - >= 2 key facets with strong evidence (role="own_method" in method/intro)
        
    Level 2 (Partial overlap):
        - In-domain
        - >= 1 key facet with strong evidence OR >= 2 facets with any evidence
        
    Level 1 (Adjacent):
        - In-domain
        - Has some evidence but doesn't meet Level 2/3 criteria
        
    Level 0 (Ignore):
        - Out-of-domain OR no evidence
    
    Args:
        paper_id: Paper identifier
        evidence_for_paper: List of EvidenceSpans for this paper
        is_in_domain: Whether paper passes domain filter
        
    Returns:
        Coverage level 0, 1, 2, or 3
    """
    # Domain filter is a hard gate
    if not is_in_domain:
        return 0
    
    # No evidence at all
    if not evidence_for_paper:
        return 0
    
    # Count key facets with strong evidence
    key_facets_strong: Set[str] = set()
    key_facets_any: Set[str] = set()
    all_facets_any: Set[str] = set()
    
    for ev in evidence_for_paper:
        all_facets_any.add(ev.facet_label)
        
        if ev.facet_label in KEY_FACETS:
            key_facets_any.add(ev.facet_label)
            
            # Strong evidence: own_method or strong prior_work in core sections
            is_strong = (
                ev.strength >= 0.7 and
                ev.role == "own_method" and
                ev.section_role in {"intro", "method", "abstract", "other"}
            )
            if is_strong:
                key_facets_strong.add(ev.facet_label)
    
    # Level 3: >= 2 key facets with strong evidence
    if len(key_facets_strong) >= 2:
        return 3
    
    # Level 2: >= 1 key facet strong OR >= 2 key facets any
    if len(key_facets_strong) >= 1 or len(key_facets_any) >= 2:
        return 2
    
    # Level 1: In-domain with some evidence
    if len(all_facets_any) >= 1:
        return 1
    
    # No meaningful evidence
    return 0


def classify_all_papers(
    paper_records: Dict[str, PaperRecord],
    evidence_spans: List[EvidenceSpan],
    topic: str = "monocular_3d_detection",
) -> Dict[str, int]:
    """
    Classify all papers by coverage level.
    
    Uses the SAME evidence_spans as FacetCoverageStats to ensure consistency.
    
    Args:
        paper_records: Dict of paper_id -> PaperRecord
        evidence_spans: All evidence spans (same used for FacetCoverageStats)
        topic: Topic for domain filtering
        
    Returns:
        Dict of paper_id -> coverage_level (0, 1, 2, or 3)
    """
    # Group evidence by paper
    evidence_by_paper: Dict[str, List[EvidenceSpan]] = {}
    for ev in evidence_spans:
        evidence_by_paper.setdefault(ev.paper_id, []).append(ev)
    
    # Classify each paper
    coverage_levels: Dict[str, int] = {}
    level_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    
    for paper_id, record in paper_records.items():
        is_in_domain = is_in_domain_for_topic(record, topic)
        evidence_for_paper = evidence_by_paper.get(paper_id, [])
        
        level = assign_coverage_level(paper_id, evidence_for_paper, is_in_domain)
        coverage_levels[paper_id] = level
        level_counts[level] += 1
    
    logger.info(
        f"[CoverageClassifier] Classified {len(paper_records)} papers: "
        f"High(3)={level_counts[3]}, Partial(2)={level_counts[2]}, "
        f"Adjacent(1)={level_counts[1]}, Ignored(0)={level_counts[0]}"
    )
    
    return coverage_levels


def get_papers_by_level(
    coverage_levels: Dict[str, int],
    paper_records: Dict[str, PaperRecord],
) -> Dict[int, List[PaperRecord]]:
    """
    Group papers by their coverage level.
    
    Returns:
        Dict of level -> List[PaperRecord]
    """
    by_level: Dict[int, List[PaperRecord]] = {0: [], 1: [], 2: [], 3: []}
    
    for paper_id, level in coverage_levels.items():
        if paper_id in paper_records:
            by_level[level].append(paper_records[paper_id])
    
    return by_level


def validate_coverage_consistency(
    coverage_levels: Dict[str, int],
    evidence_spans: List[EvidenceSpan],
) -> List[str]:
    """
    Validate that coverage levels are consistent with evidence.
    
    Assertion: If a paper appears in High(3) or Partial(2), it must have
    at least one strong EvidenceSpan for at least one key facet.
    
    Returns:
        List of warning messages for any inconsistencies
    """
    warnings = []
    
    # Group evidence by paper
    evidence_by_paper: Dict[str, List[EvidenceSpan]] = {}
    for ev in evidence_spans:
        evidence_by_paper.setdefault(ev.paper_id, []).append(ev)
    
    for paper_id, level in coverage_levels.items():
        if level >= 2:
            # Should have at least one key facet with strong evidence
            paper_evidence = evidence_by_paper.get(paper_id, [])
            has_key_facet = any(
                ev.facet_label in KEY_FACETS and ev.strength >= 0.7
                for ev in paper_evidence
            )
            if not has_key_facet:
                warnings.append(
                    f"Paper {paper_id[:30]}... has level={level} but no strong key facet evidence"
                )
    
    if warnings:
        logger.warning(f"[CoverageClassifier] {len(warnings)} consistency warnings")
    
    return warnings
