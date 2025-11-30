# pipelines/tasks/slot_filling.py

"""
Task-level orchestrator for slot filling / related work gap detection.

Given a manuscript excerpt (typically Related Work section) and a list of
already-cited references, this task:
1. Runs exploration loop to find candidate papers
2. Filters out papers that are already cited
3. Scores remaining papers for citation relevance
4. Generates a report suggesting missing citations by priority level
"""

from __future__ import annotations

from typing import List, Optional, Tuple, Set
from dataclasses import dataclass
import json
import ast

from loguru import logger

from pipelines.state import Intent, PipelineState, Paper
from pipelines.exploration_loop import run_exploration_loop


@dataclass
class SlotFillingMatch:
    """Scoring result for whether a paper should be cited in the manuscript."""
    paper_id: str
    relevance_level: int      # 0 = not relevant, 1 = low, 2 = medium, 3 = high
    should_cite: bool
    suggested_slot: str       # e.g. "geometry-consistent data augmentation"
    reason: str               # 1-2 sentence justification


def _is_known_ref(paper: Paper, known_refs: List[str]) -> bool:
    """
    Heuristic: check if paper is already in known_refs list.
    
    Matches based on:
    - DOI (if present in known_refs)
    - arXiv ID (if present in known_refs)
    - Title similarity (case-insensitive substring match)
    
    Args:
        paper: Paper object to check
        known_refs: List of known references (DOI, arXiv ID, or title)
        
    Returns:
        True if paper appears to be already cited
    """
    # Normalize known refs to lowercase
    known_refs_lower = [ref.lower().strip() for ref in known_refs]
    
    # Check DOI
    if paper.doi:
        doi_lower = paper.doi.lower()
        for ref in known_refs_lower:
            if doi_lower in ref or ref in doi_lower:
                return True
    
    # Check arXiv ID
    if hasattr(paper, 'arxiv_id') and paper.arxiv_id:
        arxiv_lower = paper.arxiv_id.lower()
        # Handle both "arXiv:2104.05858" and "2104.05858" formats
        arxiv_bare = arxiv_lower.replace('arxiv:', '')
        for ref in known_refs_lower:
            if arxiv_bare in ref or arxiv_lower in ref:
                return True
    
    # Check title similarity (substring match)
    if paper.title:
        title_lower = paper.title.lower()
        for ref in known_refs_lower:
            # Simple bidirectional substring match
            # If ref is a substring of title (e.g., "MonoFlex" in "MonoFlex: Objects Are...")
            # or title is a substring of ref
            if len(ref) >= 3:  # Only match non-trivial strings
                if ref in title_lower or title_lower in ref:
                    return True
    
    return False


def score_papers_for_slot_filling(
    agent,
    state: PipelineState,
    manuscript_excerpt: str,
    known_refs: List[str],
    max_papers: int = 40,
) -> List[SlotFillingMatch]:
    """
    Score papers for citation relevance to the manuscript.
    
    Args:
        agent: Agent with LLM capabilities
        state: PipelineState with explored papers
        manuscript_excerpt: Current related work text from manuscript
        known_refs: List of already-cited references
        max_papers: Maximum number of papers to score
        
    Returns:
        List of SlotFillingMatch objects sorted by relevance
    """
    logger.info(f"[SlotFilling] Scoring up to {max_papers} papers for citation gaps...")
    
    # Filter out known refs and sort by citation count
    candidate_papers = [
        (paper_id, paper)
        for paper_id, paper in state.papers.items()
        if not _is_known_ref(paper, known_refs)
    ]
    
    logger.info(f"[SlotFilling] Filtered to {len(candidate_papers)} candidate papers (excluding known refs)")
    
    # Sort by citation count
    candidate_papers.sort(key=lambda x: x[1].citation_count or 0, reverse=True)
    
    # Limit to top max_papers
    papers_to_score = candidate_papers[:max_papers]
    
    matches = []
    
    for paper_id, paper in papers_to_score:
        logger.info(f"[SlotFilling] Scoring paper: {paper.title} (id={paper_id})")
        
        # Build paper context
        paper_context = f"""Title: {paper.title}
Venue: {paper.venue or 'Unknown'}
Year: {paper.year or 'Unknown'}
Citations: {paper.citation_count or 'N/A'}"""
        
        # Add abstract if available
        if hasattr(paper, 'abstract') and paper.abstract:
            paper_context += f"\n\nAbstract:\n{paper.abstract[:800]}"
        
        prompt = f"""You are a JSON generator. **Do NOT call any tools or write/run any code.**
Think silently and then output the final answer as JSON only.

We are editing the related-work section of a paper.

Manuscript excerpt (current text):
<<<
{manuscript_excerpt[:2000]}
>>>

Candidate paper:
{paper_context}

Task: Decide whether this paper should be cited in the above manuscript excerpt.

Please output a JSON object with:
- "relevance_level": integer 0-3, where
  0 = not relevant
  1 = weakly related / optional citation
  2 = clearly related, should be cited if space allows
  3 = strongly related and important; missing this citation is a serious gap
- "should_cite": boolean, true if you recommend citing it
- "suggested_slot": a short phrase (<= 10 words) describing where to cite it,
  e.g., "geometry-consistent data augmentation", "monocular depth benchmarks",
  "LiDAR+RGB fusion baselines", etc.
- "reason": 1-2 sentences explaining your decision.

JSON output:"""

        try:
            result = agent.run(prompt)
            result_str = str(result)
            logger.debug(f"[SlotFilling] Raw scoring output for {paper.title[:30]}...: {result_str[:500]!r}")
            
            # Parse JSON with fallback to ast.literal_eval
            start_idx = result_str.find('{')
            end_idx = result_str.rfind('}')
            
            score_data = None
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = result_str[start_idx:end_idx+1]
                # 1. Try JSON
                try:
                    score_data = json.loads(json_str)
                except json.JSONDecodeError:
                    # 2. Try Python dict literal
                    try:
                        score_data = ast.literal_eval(json_str)
                    except Exception:
                        score_data = None
            
            if isinstance(score_data, dict):
                relevance_level = int(score_data.get('relevance_level', 0))
                should_cite = bool(score_data.get('should_cite', False))
                suggested_slot = str(score_data.get('suggested_slot', 'general related work'))
                reason = str(score_data.get('reason', ''))
                
                matches.append(SlotFillingMatch(
                    paper_id=paper_id,
                    relevance_level=relevance_level,
                    should_cite=should_cite,
                    suggested_slot=suggested_slot,
                    reason=reason
                ))
                
                logger.info(f"[SlotFilling] Finished scoring {paper.title}: level={relevance_level}, cite={should_cite}")
            else:
                # Fallback
                logger.warning(f"[SlotFilling] Failed to parse scoring JSON for {paper.title}, using defaults.")
                matches.append(SlotFillingMatch(
                    paper_id=paper_id,
                    relevance_level=0,
                    should_cite=False,
                    suggested_slot="",
                    reason="Failed to parse LLM output."
                ))
                
        except Exception as e:
            logger.warning(f"Failed to score paper {paper.title}: {e}")
            matches.append(SlotFillingMatch(
                paper_id=paper_id,
                relevance_level=0,
                should_cite=False,
                suggested_slot="",
                reason=f"Scoring error: {str(e)[:50]}"
            ))
    
    # Sort by relevance_level (desc), then citation_count (desc)
    matches.sort(
        key=lambda m: (
            m.relevance_level,
            state.papers[m.paper_id].citation_count or 0
        ),
        reverse=True
    )
    
    logger.info(f"[SlotFilling] Scored {len(matches)} papers")
    return matches


def render_slot_filling_report(
    state: PipelineState,
    matches: List[SlotFillingMatch],
) -> str:
    """
    Render a structured Markdown report for slot filling suggestions.
    
    Args:
        state: PipelineState with paper metadata
        matches: List of scored papers
        
    Returns:
        Markdown formatted report string
    """
    lines = []
    
    # Title
    lines.append(f"# Suggested Related Work Additions (Slot Filling)")
    lines.append("")
    lines.append(f"**Topic**: {state.intent.topic}")
    lines.append("")
    
    # Separate into priority buckets
    high_priority = [m for m in matches if m.relevance_level == 3]
    medium_priority = [m for m in matches if m.relevance_level == 2]
    low_priority = [m for m in matches if m.relevance_level == 1 and m.should_cite]
    
    # 1. High-priority missing citations
    lines.append("## 1. High-Priority Missing Citations (Level 3)")
    lines.append("⚠️ These papers are strongly related and important; missing them is a serious gap:")
    lines.append("")
    
    if high_priority:
        for match in high_priority:
            paper = state.papers[match.paper_id]
            lines.append(f"### {paper.title}")
            lines.append(f"- **Venue**: {paper.venue or 'Unknown'}, {paper.year or 'Unknown'}")
            lines.append(f"- **Citations**: {paper.citation_count or 'N/A'}")
            lines.append(f"- **Where to cite**: {match.suggested_slot}")
            lines.append(f"- **Why**: {match.reason}")
            if paper.doi:
                lines.append(f"- **DOI**: [{paper.doi}](https://doi.org/{paper.doi})")
            lines.append("")
    else:
        lines.append("- None found.")
        lines.append("")
    
    # 2. Medium-priority
    lines.append("## 2. Medium-Priority Citations (Level 2)")
    lines.append("These papers are clearly related and should be cited if space allows:")
    lines.append("")
    
    if medium_priority:
        for match in medium_priority:
            paper = state.papers[match.paper_id]
            lines.append(f"### {paper.title}")
            lines.append(f"- **Venue**: {paper.venue or 'Unknown'}, {paper.year or 'Unknown'}")
            lines.append(f"- **Citations**: {paper.citation_count or 'N/A'}")
            lines.append(f"- **Where to cite**: {match.suggested_slot}")
            lines.append(f"- **Why**: {match.reason}")
            if paper.doi:
                lines.append(f"- **DOI**: [{paper.doi}](https://doi.org/{paper.doi})")
            lines.append("")
    else:
        lines.append("- None found.")
        lines.append("")
    
    # 3. Low-priority / optional
    lines.append("## 3. Low-Priority / Optional Citations (Level 1)")
    lines.append("Weakly related papers that could be considered:")
    lines.append("")
    
    if low_priority:
        for match in low_priority[:5]:  # Limit to top 5 for brevity
            paper = state.papers[match.paper_id]
            lines.append(f"- **{paper.title}** ({paper.venue or 'Unknown'}, {paper.year or 'Unknown'})")
            lines.append(f"  - **Where**: {match.suggested_slot}")
            lines.append(f"  - **Why**: {match.reason}")
        if len(low_priority) > 5:
            lines.append(f"  - *(and {len(low_priority) - 5} more...)*")
        lines.append("")
    else:
        lines.append("- None found.")
        lines.append("")
    
    # Check if all buckets are empty
    if not high_priority and not medium_priority and not low_priority:
        lines.append("---")
        lines.append("")
        lines.append("*No strong missing citations were detected based on the current corpus. "
                    "You may still want to double-check manually.*")
        lines.append("")
    
    # Disclaimer
    lines.append("---")
    lines.append("**Note**: This is an automated suggestion based on paper metadata and LLM analysis. "
                "Please double-check relevance and read the papers before finalizing citations.")
    
    return "\n".join(lines)


def run_slot_filling(
    agent,
    topic: str,
    manuscript_excerpt: str,
    known_refs: List[str],
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
    max_rounds: int = 2,
) -> str:
    """
    High-level entry point for the slot filling task.
    
    Flow:
      1) Build Intent and initial PipelineState
      2) Run exploration loop to find candidate papers
      3) Filter out papers already cited (known_refs)
      4) Score remaining papers for citation relevance
      5) Render a structured report with priority levels
    
    Args:
        agent: Agent with LLM and tool capabilities
        topic: Research topic (e.g., "monocular 3D detection")
        manuscript_excerpt: Current related work text from manuscript
        known_refs: List of already-cited references (DOI, arXiv ID, or title)
        years: Optional year range filter for exploration
        venues: Optional venue filter for exploration
        max_rounds: Number of exploration rounds (default: 2)
        
    Returns:
        Markdown formatted report string
        
    Example:
        >>> report = run_slot_filling(
        ...     agent=agent,
        ...     topic="monocular 3D object detection",
        ...     manuscript_excerpt="Recent work on 3D detection...",
        ...     known_refs=["10.1109/cvpr.2020.123", "M3D-RPN"],
        ...     years=(2018, 2024),
        ...     venues=["CVPR", "ICCV", "ECCV"],
        ... )
    """
    intent = Intent(
        task_type="slot_filling",
        topic=topic,
        years=years,
        venues=venues or [],
        extra_constraints={
            "manuscript_excerpt": manuscript_excerpt,
            "known_refs": json.dumps(known_refs),  # Serialize list as JSON string
        },
    )
    state = PipelineState(intent=intent)

    logger.info(f"[SlotFilling] Starting for topic='{topic}'")
    logger.info(f"[SlotFilling] Known refs: {len(known_refs)} references")

    # 1) Run exploration loop
    state = run_exploration_loop(
        agent=agent,
        state=state,
        max_rounds=max_rounds,
    )

    logger.info(
        f"[SlotFilling] Exploration finished with "
        f"{len(state.papers)} candidate papers"
    )

    # 2) Score papers for slot filling
    matches = score_papers_for_slot_filling(
        agent=agent,
        state=state,
        manuscript_excerpt=manuscript_excerpt,
        known_refs=known_refs,
        max_papers=40,
    )

    # 3) Render report
    report = render_slot_filling_report(state, matches)
    return report
