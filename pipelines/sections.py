"""
PDF Section Parsing Data Structures.

This module defines the core data structures for representing
parsed PDF sections with semantic role labels.
"""

from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
import json
from pathlib import Path
from pipelines.runtime import atomic_write_json


# Valid section roles
SECTION_ROLES = frozenset([
    "intro",
    "related_work", 
    "method",
    "experiment",
    "conclusion",
    "other"
])


@dataclass
class Section:
    """
    Represents a single section of a paper.
    
    Attributes:
        role: Semantic role of the section. One of:
            - "intro": Introduction
            - "related_work": Related Work / Background / Literature Review
            - "method": Method / Approach / Model / Architecture
            - "experiment": Experiments / Results / Evaluation
            - "conclusion": Conclusion / Discussion
            - "other": Anything else (e.g., Appendix, Acknowledgments)
        heading: Original section heading text (e.g., "1 Introduction")
        text: Section body text (excluding the heading line)
        start_page: Starting page number (1-indexed), None if unknown
        end_page: Ending page number (1-indexed), None if unknown
    """
    role: str
    heading: str
    text: str
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    
    def __post_init__(self):
        if self.role not in SECTION_ROLES:
            self.role = "other"


@dataclass  
class PaperSections:
    """
    Represents all parsed sections for a single paper.
    
    Attributes:
        paper_id: Unique identifier for the paper
        title: Paper title
        year: Publication year
        sections: List of Section objects in document order
    """
    paper_id: str
    title: str
    year: int
    sections: List[Section]
    
    def get_sections_by_role(self, role: str) -> List[Section]:
        """Get all sections with a specific role."""
        return [s for s in self.sections if s.role == role]
    
    def get_intro_text(self) -> str:
        """Get combined text from all 'intro' sections."""
        intro_sections = self.get_sections_by_role("intro")
        return "\n\n".join(s.text for s in intro_sections).strip()
    
    def get_related_work_text(self) -> str:
        """Get combined text from all 'related_work' sections."""
        rw_sections = self.get_sections_by_role("related_work")
        return "\n\n".join(s.text for s in rw_sections).strip()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "year": self.year,
            "sections": [asdict(s) for s in self.sections]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperSections":
        """Create from dictionary (loaded from JSON)."""
        sections = [Section(**s) for s in data.get("sections", [])]
        return cls(
            paper_id=data["paper_id"],
            title=data["title"],
            year=data["year"],
            sections=sections
        )


def save_paper_sections(task_root: str, paper_sections: PaperSections) -> Path:
    """
    Save PaperSections to JSON file.
    
    Args:
        task_root: Root directory for the task/run
        paper_sections: PaperSections object to save
        
    Returns:
        Path to the saved JSON file
    """
    out_dir = Path(task_root) / "pdf_sections"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize paper_id for use as filename (DOIs contain slashes)
    safe_id = _sanitize_paper_id(paper_sections.paper_id)
    out_path = out_dir / f"{safe_id}.sections.json"
    
    atomic_write_json(out_path, paper_sections.to_dict())
    
    return out_path


def load_paper_sections(task_root: str, paper_id: str) -> Optional[PaperSections]:
    """
    Load PaperSections from JSON file.
    
    Args:
        task_root: Root directory for the task/run
        paper_id: Paper identifier
        
    Returns:
        PaperSections object if file exists, None otherwise
    """
    # Sanitize paper_id for use as filename (DOIs contain slashes)
    safe_id = _sanitize_paper_id(paper_id)
    sections_path = Path(task_root) / "pdf_sections" / f"{safe_id}.sections.json"
    
    if not sections_path.exists():
        return None
    
    with sections_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    
    return PaperSections.from_dict(data)


def _sanitize_paper_id(paper_id: str) -> str:
    """
    Sanitize paper_id for use as a filename.
    
    DOIs like '10.1109/ICCV.2019.00695' contain slashes that are invalid
    in file paths. This replaces / and \\ with underscores.
    
    Args:
        paper_id: Original paper identifier
        
    Returns:
        Filesystem-safe version of the paper_id
    """
    return paper_id.replace("/", "_").replace("\\", "_").replace(":", "_")

