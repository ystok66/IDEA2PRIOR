"""
Intro + Related Work Summarization Data Structures.

This module defines data structures for storing structured summaries
of paper introductions and related work sections.
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
import json
from pathlib import Path


@dataclass
class IntroRwSummary:
    """
    Structured summary of a paper's Introduction and Related Work sections.
    
    Attributes:
        paper_id: Unique identifier for the paper
        title: Paper title
        year: Publication year
        is_survey: Whether this paper is a survey/review paper
        domain_overview_bullets: 2-4 bullets describing the problem area and motivation
        contribution_bullets: 2-4 bullets describing the paper's main contributions
        positioning_bullets: 1-3 bullets describing how the paper positions itself
        bullets_with_snippets: List of dicts, each containing:
            - "type": "domain" | "contribution" | "positioning"
            - "bullet": The bullet text
            - "snippet": 1-3 sentence quote from original text
            - "section_role": "intro" or "related_work"
            - "loc": Location indicator like "intro_para_5" (optional, approximate)
    """
    paper_id: str
    title: str
    year: int
    is_survey: bool
    domain_overview_bullets: List[str]
    contribution_bullets: List[str]
    positioning_bullets: List[str]
    bullets_with_snippets: List[Dict[str, str]]
    status: str = "success"
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntroRwSummary":
        """Create from dictionary (loaded from JSON)."""
        return cls(
            paper_id=data["paper_id"],
            title=data["title"],
            year=data["year"],
            is_survey=data.get("is_survey", False),
            domain_overview_bullets=data.get("domain_overview_bullets", []),
            contribution_bullets=data.get("contribution_bullets", []),
            positioning_bullets=data.get("positioning_bullets", []),
            bullets_with_snippets=data.get("bullets_with_snippets", []),
            status=data.get("status", "success"), error=data.get("error"),
        )
    
    @classmethod
    def empty(cls, paper_id: str, title: str, year: int, status="empty", error=None) -> "IntroRwSummary":
        """Create an empty summary (fallback for parse failures)."""
        return cls(
            paper_id=paper_id,
            title=title,
            year=year,
            is_survey=False,
            domain_overview_bullets=[],
            contribution_bullets=[],
            positioning_bullets=[],
            bullets_with_snippets=[],
            status=status, error=error,
        )


def save_intro_rw_summaries_jsonl(
    task_root: str,
    summaries: List[IntroRwSummary],
) -> Path:
    """
    Save all IntroRwSummary objects to a JSONL file.
    
    Args:
        task_root: Root directory for the task/run
        summaries: List of IntroRwSummary objects
        
    Returns:
        Path to the saved JSONL file
    """
    out_dir = Path(task_root) / "synthesis"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    out_path = out_dir / "intro_rw_summaries.jsonl"
    
    with out_path.open("w", encoding="utf-8") as f:
        for summary in summaries:
            json_line = json.dumps(summary.to_dict(), ensure_ascii=False)
            f.write(json_line + "\n")
    
    return out_path


def load_intro_rw_summaries_jsonl(task_root: str) -> List[IntroRwSummary]:
    """
    Load all IntroRwSummary objects from JSONL file.
    
    Args:
        task_root: Root directory for the task/run
        
    Returns:
        List of IntroRwSummary objects (empty list if file doesn't exist)
    """
    file_path = Path(task_root) / "synthesis" / "intro_rw_summaries.jsonl"
    
    if not file_path.exists():
        return []
    
    summaries = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            summaries.append(IntroRwSummary.from_dict(data))
    
    return summaries
