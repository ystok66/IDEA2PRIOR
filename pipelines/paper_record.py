# pipelines/paper_record.py

"""
PaperRecord: Central intermediate representation for papers.

All downstream logic (coverage counts, facet gaps, adjacency classification, 
report generation) should read from PaperRecord instead of ad-hoc dicts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pipelines.state import Paper


@dataclass
class SectionRecord:
    """A section of a paper (e.g., Introduction, Related Work, Method)."""
    name: str                  # e.g., "Introduction", "Related Work", "Method"
    text: str                  # Raw section text
    sentences: List[str] = field(default_factory=list)  # Simple sentence split
    role: str = "other"        # intro/related_work/method/experiments/conclusion/background/other
    
    def __post_init__(self):
        """Split text into sentences if not provided, and infer role."""
        if not self.sentences and self.text:
            # Simple sentence splitting (can be improved later)
            self.sentences = [
                s.strip() for s in re.split(r'(?<=[.!?])\s+', self.text)
                if s.strip()
            ]
        # Infer role from section name if not set
        if self.role == "other" and self.name:
            self.role = infer_section_role(self.name)


# Section role patterns
INTRO_PATTERNS = ["introduction", "intro"]
RELATED_PATTERNS = ["related work", "related works", "literature review", "previous work", "prior work"]
METHOD_PATTERNS = ["method", "approach", "proposed method", "our method", "methodology", "framework"]
EXPERIMENTS_PATTERNS = ["experiment", "experimental", "results", "evaluation", "ablation"]
CONCLUSION_PATTERNS = ["conclusion", "conclusions", "discussion", "summary", "future work"]
BACKGROUND_PATTERNS = ["background", "preliminaries", "preliminary", "problem formulation"]


def infer_section_role(section_title: str) -> str:
    """
    Infer the role of a section from its title.
    
    Returns one of: intro, related_work, method, experiments, conclusion, background, other
    """
    t = section_title.lower().strip()
    
    if any(p in t for p in INTRO_PATTERNS):
        return "intro"
    if any(p in t for p in RELATED_PATTERNS):
        return "related_work"
    if any(p in t for p in METHOD_PATTERNS):
        return "method"
    if any(p in t for p in EXPERIMENTS_PATTERNS):
        return "experiments"
    if any(p in t for p in CONCLUSION_PATTERNS):
        return "conclusion"
    if any(p in t for p in BACKGROUND_PATTERNS):
        return "background"
    return "other"


@dataclass
class PaperRecord:
    """
    Central structured representation for a paper.
    
    All downstream logic should use PaperRecord as the single source of truth.
    """
    # Core identity
    paper_id: str              # e.g., "openalex:W123...", "arxiv:2301.12345"
    title: str
    
    # Metadata
    authors: List[str] = field(default_factory=list)
    venue: Optional[str] = None
    year: Optional[int] = None
    citation_count: Optional[int] = None
    
    # Identifiers
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    openalex_id: Optional[str] = None
    s2_id: Optional[str] = None
    
    # URLs
    url_pdf: Optional[str] = None
    url_landing: Optional[str] = None
    
    # Content
    abstract: Optional[str] = None
    sections: List[SectionRecord] = field(default_factory=list)
    
    # Derived tags (filled by tagging functions)
    datasets: List[str] = field(default_factory=list)   # e.g., ["kitti", "nuscenes"]
    metrics: List[str] = field(default_factory=list)    # e.g., ["map", "rotation_error"]
    domains: List[str] = field(default_factory=list)    # e.g., ["monocular_3d_detection"]
    
    # Facet coverage flags (filled during scoring)
    facet_flags: Dict[str, bool] = field(default_factory=dict)
    # e.g., {"F1": True, "F2": False, ...}
    
    # Coverage level from scoring (0-3)
    coverage_level: int = 0
    
    # Role classification
    role: str = "unknown"  # main_contribution, key_technique, background, irrelevant
    
    # Retrieval metadata
    retrieval_source: str = ""  # e.g., "block1_main_q0", "exploration_loop"
    
    # Raw metadata for debugging
    raw_meta: Dict[str, Any] = field(default_factory=dict)
    
    def get_full_text(self) -> str:
        """Get concatenated text from title, abstract, and all sections."""
        parts = [self.title or ""]
        if self.abstract:
            parts.append(self.abstract)
        for sec in self.sections:
            parts.append(sec.text)
        return " ".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        # Convert SectionRecords to dicts
        d["sections"] = [asdict(s) for s in self.sections]
        return d
    
    def to_json(self, path: Path) -> None:
        """Save to JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaperRecord":
        """Create from dictionary."""
        # Convert section dicts back to SectionRecords
        sections = [
            SectionRecord(**s) if isinstance(s, dict) else s
            for s in d.get("sections", [])
        ]
        d["sections"] = sections
        return cls(**d)
    
    @classmethod
    def from_json(cls, path: Path) -> "PaperRecord":
        """Load from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ========== Tagging Functions ==========

# Dataset keywords
DATASET_KEYWORDS = {
    "kitti": ["kitti", "kitti-3d", "kitti dataset"],
    "nuscenes": ["nuscenes", "nu-scenes", "nuscene"],
    "waymo": ["waymo", "waymo open dataset"],
    "argoverse": ["argoverse", "argo verse"],
    "lyft": ["lyft level 5", "lyft l5"],
    "cityscapes": ["cityscapes", "city scapes"],
    "sun_rgbd": ["sun rgb-d", "sun-rgbd", "sunrgbd"],
    "scannet": ["scannet", "scan net"],
}

# Metric keywords
METRIC_KEYWORDS = {
    "map": ["map", "mean average precision", "average precision"],
    "iou": ["iou", "intersection over union"],
    "rotation_error": ["rotation error", "orientation error", "heading error", "yaw error"],
    "translation_error": ["translation error", "localization error", "position error"],
    "depth_error": ["depth error", "depth rmse", "abs rel"],
    "aos": ["aos", "average orientation similarity"],
    "nds": ["nds", "nuScenes detection score"],
}

# Domain keywords
DOMAIN_KEYWORDS = {
    "monocular_3d_detection": [
        "monocular 3d", "monocular 3-d", "mono3d", "mono 3d",
        "single image 3d", "rgb-based 3d detection",
    ],
    "autonomous_driving": [
        "autonomous driving", "self-driving", "self driving",
        "automated driving", "vehicle detection", "traffic scene",
    ],
    "lidar_detection": [
        "lidar", "point cloud detection", "3d point cloud",
    ],
    "stereo_3d": [
        "stereo 3d", "stereo vision", "disparity",
    ],
    "depth_estimation": [
        "depth estimation", "monocular depth", "depth prediction",
    ],
    "medical_imaging": [
        "medical image", "clinical", "diagnosis", "pathology",
        "radiology", "mri", "ct scan", "x-ray",
        "glaucoma", "retinal", "fundus", "ophthalmology",
    ],
    "wildlife_conservation": [
        "wildlife", "conservation", "animal detection", "ecology",
        "biodiversity", "species detection",
    ],
    "remote_sensing": [
        "remote sensing", "satellite", "aerial imagery", "geospatial",
    ],
}


def tag_datasets_and_metrics(record: PaperRecord) -> None:
    """
    Tag paper with detected datasets and metrics.
    
    Reads only from PaperRecord fields, modifies record.datasets and record.metrics.
    """
    text = record.get_full_text().lower()
    
    # Detect datasets
    datasets = set()
    for dataset_key, keywords in DATASET_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                datasets.add(dataset_key)
                break
    
    # Detect metrics
    metrics = set()
    for metric_key, keywords in METRIC_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                metrics.add(metric_key)
                break
    
    record.datasets = sorted(datasets)
    record.metrics = sorted(metrics)


def tag_domains(record: PaperRecord) -> None:
    """
    Tag paper with detected research domains.
    
    Reads only from PaperRecord fields, modifies record.domains.
    """
    text = record.get_full_text().lower()
    
    domains = set()
    for domain_key, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                domains.add(domain_key)
                break
    
    # Also check for additional out-of-domain markers
    out_of_domain_markers = [
        ("nanomedicine", ["nanoparticle", "drug delivery", "nanomedicine", "nano-particle"]),
        ("bmi", ["brain-machine interface", "brain machine interface", "neuralink", "neural interface", "eeg-based"]),
        ("agriculture", ["crop detection", "agricultural", "plant disease", "weed detection"]),
        ("text_nlp", ["natural language", "text classification", "sentiment analysis", "language model"]),
    ]
    for domain_key, keywords in out_of_domain_markers:
        for kw in keywords:
            if kw in text:
                domains.add(domain_key)
                break
    
    record.domains = sorted(domains)


# Out-of-domain markers - papers with these domains are likely irrelevant for 3D detection
OUT_OF_DOMAIN_MARKERS = {
    "medical_imaging", "wildlife_conservation", "remote_sensing", 
    "nanomedicine", "bmi", "agriculture", "text_nlp"
}

# In-domain markers for monocular 3D detection
IN_DOMAIN_MARKERS = {
    "monocular_3d_detection", "autonomous_driving", "lidar_detection",
    "stereo_3d", "depth_estimation"
}


def is_in_domain_for_topic(record: PaperRecord, topic: str = "monocular_3d_detection") -> bool:
    """
    Check if a paper is in-domain for the given topic.
    
    For monocular 3D detection:
    - Paper is in-domain if it has any of the in-domain markers
    - Paper is out-of-domain if it ONLY has out-of-domain markers
    - Edge case: papers with both are considered in-domain
    
    Args:
        record: PaperRecord with domains already tagged
        topic: Topic identifier (default: monocular_3d_detection)
        
    Returns:
        True if paper is relevant to the topic
    """
    domains = set(record.domains)
    
    # Quick check: has any in-domain marker?
    has_in_domain = bool(domains & IN_DOMAIN_MARKERS)
    
    # Quick check: has only out-of-domain markers?
    has_out_domain = bool(domains & OUT_OF_DOMAIN_MARKERS)
    has_only_out_domain = has_out_domain and not has_in_domain
    
    if has_only_out_domain:
        return False
    
    if has_in_domain:
        return True
    
    # Fallback: check title/abstract for topic-specific keywords
    text = (record.title + " " + (record.abstract or "")).lower()
    
    if topic == "monocular_3d_detection":
        # Check for 3D detection / object detection / augmentation keywords
        if "3d" in text and ("detection" in text or "object" in text):
            return True
        if "monocular" in text and "3d" in text:
            return True
        if "data augmentation" in text and "detection" in text:
            return True
        if "camera" in text and "3d" in text:
            return True
    
    # If no strong signal, assume out-of-domain if domains is empty
    return len(domains) == 0  # Empty domains = generic, might be relevant


def paper_to_paper_record(
    paper: "Paper",
    sections: Optional[List[SectionRecord]] = None,
) -> PaperRecord:
    """
    Convert a Paper object (from pipelines.state) to PaperRecord.
    
    Args:
        paper: Paper object from the existing pipeline
        sections: Optional list of SectionRecords (from PDF parsing)
        
    Returns:
        PaperRecord with fields filled from Paper
    """
    # Build paper_id from available identifiers
    paper_id = (
        paper.openalex_id or 
        paper.doi or 
        paper.arxiv_id or 
        paper.s2_id or 
        paper.title or
        "unknown"
    )
    
    record = PaperRecord(
        paper_id=paper_id,
        title=paper.title or "Unknown Title",
        authors=getattr(paper, "authors", []) or [],
        venue=paper.venue,
        year=paper.year,
        citation_count=paper.citation_count,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        openalex_id=paper.openalex_id,
        s2_id=paper.s2_id,
        url_pdf=getattr(paper, "pdf_url", None),
        abstract=paper.abstract,
        sections=sections or [],
        retrieval_source=getattr(paper, "retrieval_source", ""),
        raw_meta={
            "fulltext_path": getattr(paper, "fulltext_path", None),
        },
    )
    
    # Apply tagging
    tag_datasets_and_metrics(record)
    tag_domains(record)
    
    return record


def tag_all_papers(records: List[PaperRecord]) -> None:
    """Apply all tagging functions to a list of PaperRecords."""
    for record in records:
        tag_datasets_and_metrics(record)
        tag_domains(record)
