"""
Prior Art Check - Report Rendering Module

This module contains pure rendering functions for generating the prior-art report.
These functions read data and return Markdown strings - no LLM calls or network requests.

Dependency: May import from prior_art_scoring for evidence snippets, but
            prior_art_scoring MUST NOT import from this module.
"""

from __future__ import annotations

from typing import Dict, Set, List, Iterable, Sequence, Optional, TYPE_CHECKING
from collections import Counter
import re

from pipelines.state import PipelineState
from pipelines.tasks.prior_art_types import (
    PriorArtConfig, PriorArtMatch, HIGH_LEVEL_FACET_TYPES
)
from pipelines.evidence import FacetCoverageStats, FacetReportView, coverage_label

if TYPE_CHECKING:
    from pipelines.paper_record import PaperRecord


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

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


# =============================================================================
# SECTION RENDERERS
# =============================================================================

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
                note = "→ ⚠️ not found in corpus"
            elif bucket == "rare":
                note = "→ rare (1 match)"
            else:
                note = "→ covered"
            lines.append(f"- `{name}` — matched in ~{count} paper(s) {note}")
        lines.append("")
        
    if len(lines) <= 5:
        lines.append("*No high-level facets extracted from idea.*")
        lines.append("")
        
    return "\\n".join(lines)


def render_high_level_coverage_section_v2(
    facet_coverage: Dict[str, FacetCoverageStats],
    facet_views: Dict[str, FacetReportView],
) -> str:
    """
    Render high-level structural coverage using FacetCoverageStats (Block 3).
    
    This replaces the old phrase-counting logic to ensure consistency
    between facet statistics and paper coverage levels.
    """
    lines = []
    lines.append("## High-level Structural Coverage")
    lines.append("Analysis of core facets based on evidence extraction:")
    lines.append("")
    
    for facet_id, view in facet_views.items():
        count_strong = view.stats.count_strong
        count_any = view.stats.count_any
        label = coverage_label(count_strong, count_any)
        
        lines.append(f"- **{view.human_label}**: {count_strong} strong / {count_any} total papers — {label}")
    
    if len(lines) <= 3:  # Only header + description
        lines.append("*No facet evidence collected.*")
    
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


# =============================================================================
# MAIN REPORT RENDERER
# =============================================================================

def render_prior_art_report(
    idea_description: str,
    facets: List[str],
    scored_papers: List[PriorArtMatch],
    state: PipelineState,
    idea_features: Dict[str, Set[str]],
    idea_high_level_facets: Dict[str, Set[str]],
    # Block 3 unified data sources (Issue 1, 3, 4, 6)
    coverage_levels: Dict[str, int] = None,
    paper_records: Dict[str, "PaperRecord"] = None,
    facet_coverage: Dict[str, FacetCoverageStats] = None,
    facet_views: Dict[str, FacetReportView] = None,
    config: PriorArtConfig = None,
    # Evidence functions passed in to avoid circular import
    make_keywords_fn=None,
    find_snippets_fn=None,
) -> str:
    """
    Render a structured Markdown report for the prior-art check.

    This function is pure: it only reads from the inputs and returns a string.
    
    Args:
        make_keywords_fn: Function to generate evidence keywords (from scoring module)
        find_snippets_fn: Function to find evidence snippets (from scoring module)
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
    
    # Issue 6: Use Block 3 coverage_levels when available
    if coverage_levels:
        high_paper_ids = {pid for pid, lvl in coverage_levels.items() if lvl == 3}
        high_matches = [m for m in scored_papers if m.paper_id in high_paper_ids]
    else:
        # Fallback to LLM-assigned levels
        high_matches = [m for m in scored_papers if m.coverage_level == 3]
    
    # Generate keywords for evidence search
    evidence_keywords = []
    if make_keywords_fn:
        evidence_keywords = make_keywords_fn(idea_features)
    
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
            if find_snippets_fn and evidence_keywords:
                snippets = find_snippets_fn(paper, evidence_keywords, max_snippets=3)
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
    
    # Issue 6: Use Block 3 coverage_levels when available
    if coverage_levels:
        partial_paper_ids = {pid for pid, lvl in coverage_levels.items() if lvl == 2}
        partial_matches = [m for m in scored_papers if m.paper_id in partial_paper_ids]
    else:
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
    
    # Issue 1: Use Block 3 coverage_levels (in_domain + level=1)
    if coverage_levels:
        adjacent_paper_ids = {pid for pid, lvl in coverage_levels.items() if lvl == 1}
        adjacent_matches = [m for m in scored_papers if m.paper_id in adjacent_paper_ids]
    else:
        adjacent_matches = [m for m in scored_papers if m.coverage_level == 1]
    if adjacent_matches:
        # Separate surveys from non-surveys, limit surveys to 3
        survey_keywords = ["survey", "review", "overview", "tutorial"]
        surveys = []
        non_surveys = []
        
        for match in adjacent_matches:
            paper = state.papers[match.paper_id]
            title_lower = paper.title.lower()
            if any(kw in title_lower for kw in survey_keywords):
                surveys.append(match)
            else:
                non_surveys.append(match)
        
        # Show non-surveys first (up to 7), then surveys (up to 3)
        shown = 0
        for match in non_surveys[:7]:
            paper = state.papers[match.paper_id]
            lines.append(f"- **{paper.title}** ({paper.venue or 'Unknown'}, {paper.year or 'Unknown'})")
            lines.append(f"  - {_clean_note_for_report(match.notes)}")
            shown += 1
        
        surveys_shown = 0
        for match in surveys[:3]:
            paper = state.papers[match.paper_id]
            lines.append(f"- **{paper.title}** ({paper.venue or 'Unknown'}, {paper.year or 'Unknown'})")
            lines.append(f"  - {_clean_note_for_report(match.notes)}")
            surveys_shown += 1
        
        if len(surveys) > 3:
            lines.append(f"- *(+ {len(surveys) - 3} additional survey/review papers omitted)*")
    else:
        lines.append("- None found.")
    lines.append("")
    
    # 5. High-level Structural Coverage (Issue 3: Use FacetCoverageStats)
    if facet_views:
        lines.append(render_high_level_coverage_section_v2(facet_coverage, facet_views))
    else:
        # Fallback to old phrase-counting logic
        lines.append(render_high_level_coverage_section(idea_high_level_facets, state))
    
    # 6. Structural Coverage Section (Issue 5: Conditionally display)
    cfg = config or PriorArtConfig()
    if cfg.enable_dataset_metric_stats:
        lines.append(render_structural_coverage_section(idea_features, state))
    else:
        lines.append("## 5. Structural Coverage of Your Idea")
        lines.append("_Dataset/metric statistics are temporarily disabled pending refinement._")
        lines.append("")
    
    # 7. Facet-Level Gap Analysis (Issue 4: Use FacetCoverageStats)
    lines.append("## 6. Facet-Level Gap Analysis")
    lines.append("Coverage of each facet in papers with partial or high overlap:")
    lines.append("")
    lines.append("> **Note**: 'Not found' means the facet was not clearly identified in retrieved papers. ")
    lines.append("> This may indicate a gap OR that relevant work exists but was not retrieved.")
    lines.append("")
    
    # Issue 4: Use FacetCoverageStats when available
    if facet_views:
        for facet_id, view in facet_views.items():
            if facet_id.startswith("F"):  # Only core facets F1-F4
                count_any = view.stats.count_any
                count_strong = view.stats.count_strong
                lines.append(f'- **{view.human_label}** — {count_strong} strong, {count_any} total paper(s)')
                if count_any == 0:
                    lines.append(
                        "  - ⚠️ **Not found in retrieved corpus** — may be a gap or uncovered in search"
                    )
    else:
        # Fallback to old LLM-based counting
        relevant_matches = [m for m in scored_papers if m.coverage_level >= 2]
        for i, facet in enumerate(facets, 1):
            label = f"F{i}"
            count = sum(1 for m in relevant_matches if label in m.covered_facet_labels)
            lines.append(f'- **{label}**: "{facet}" — covered in {count} paper(s)')
            if count == 0:
                lines.append(
                    "  - ⚠️ **Not found in retrieved corpus** — may be a gap or uncovered in search"
                )
    
    lines.append("")
    lines.append("---")
    lines.append("**⚠️ Disclaimer**: This is an automated prior-art check based on paper metadata and LLM analysis. ")
    lines.append("Results may contain errors due to: incomplete retrieval, LLM hallucination, or extraction failures. ")
    lines.append("**Always verify key references manually** before making high-stakes decisions.")
    
    return "\\n".join(lines)
