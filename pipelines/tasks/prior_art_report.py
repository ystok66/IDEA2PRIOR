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

from pipelines.paper_record import is_in_domain_for_topic


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_papers_by_coverage_bucket(
    coverage_levels: Dict[str, int],
    paper_records: Dict[str, "PaperRecord"],
    topic: str = "monocular_3d_detection",
) -> Dict[str, List["PaperRecord"]]:
    """
    Get papers grouped by coverage bucket using coverage_classifier output.
    
    This is the SINGLE SOURCE OF TRUTH for paper bucketing.
    All bucket-related logic in the report should use this function.
    
    Args:
        coverage_levels: Dict of paper_id -> coverage_level (0-3) from coverage_classifier
        paper_records: Dict of paper_id -> PaperRecord
        topic: Topic for domain filtering (papers must be in-domain for level 1+)
        
    Returns:
        Dict with keys "high", "partial", "adjacent", each containing list of PaperRecords
    """
    buckets: Dict[str, List["PaperRecord"]] = {
        "high": [],
        "partial": [],
        "adjacent": [],
    }
    
    for paper_id, level in coverage_levels.items():
        if paper_id not in paper_records:
            continue
        
        record = paper_records[paper_id]
        
        # In-domain filtering: Adjacent (level=1) must pass domain filter
        # Levels 2-3 have stricter evidence requirements that imply in-domain
        if level == 1:
            if not is_in_domain_for_topic(record, topic):
                continue  # Skip out-of-domain papers for Adjacent
        
        if level == 3:
            buckets["high"].append(record)
        elif level == 2:
            buckets["partial"].append(record)
        elif level == 1:
            buckets["adjacent"].append(record)
    
    return buckets


def sort_bucket_with_scores(
    bucket: List["PaperRecord"],
    scored_papers: List["PriorArtMatch"],
    fallback_sort_key: str = "citation_count",
) -> List["PaperRecord"]:
    """
    Sort papers within a bucket using LLM scores, with fallback to citation count.
    
    scored_papers is used for optional decoration (sorting), NOT for bucket membership.
    
    Args:
        bucket: List of PaperRecords to sort
        scored_papers: Optional list of PriorArtMatch with scores
        fallback_sort_key: Field name for fallback sorting (default: citation_count)
    """
    # Build score map from paper_id -> score
    score_map = {}
    if scored_papers:
        for m in scored_papers:
            # Use task_similarity as the score, or combined score if available
            score = getattr(m, "task_similarity", 0.0) or 0.0
            score_map[m.paper_id] = score
    
    def sort_key(record: "PaperRecord"):
        # Primary: LLM score (if available)
        llm_score = score_map.get(record.paper_id, 0.0)
        # Secondary: citation count
        citations = getattr(record, fallback_sort_key, 0) or 0
        return (-llm_score, -citations)  # Descending order
    
    return sorted(bucket, key=sort_key)

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
    
    # Build buckets using single source of truth: coverage_classifier output
    # scored_papers is ONLY used for sorting and optional notes (not for bucket membership)
    buckets = {"high": [], "partial": [], "adjacent": []}
    if coverage_levels and paper_records:
        buckets = get_papers_by_coverage_bucket(
            coverage_levels=coverage_levels,
            paper_records=paper_records,
            topic=state.intent.topic if hasattr(state.intent, "topic") else "monocular_3d_detection",
        )
        # Sort each bucket by LLM score
        for bucket_name in buckets:
            buckets[bucket_name] = sort_bucket_with_scores(buckets[bucket_name], scored_papers)
    else:
        # Fallback to old logic if coverage_levels not available
        # This maintains backward compatibility
        match_map = {m.paper_id: m for m in scored_papers}
        for m in scored_papers:
            if m.coverage_level == 3:
                if m.paper_id in paper_records:
                    buckets["high"].append(paper_records[m.paper_id])
            elif m.coverage_level == 2:
                if m.paper_id in paper_records:
                    buckets["partial"].append(paper_records[m.paper_id])
            elif m.coverage_level == 1:
                if m.paper_id in paper_records:
                    buckets["adjacent"].append(paper_records[m.paper_id])
    
    # Build match map for notes lookup (scored_papers = optional decoration)
    match_map = {m.paper_id: m for m in scored_papers} if scored_papers else {}
    
    # Generate keywords for evidence search
    evidence_keywords = []
    if make_keywords_fn:
        evidence_keywords = make_keywords_fn(idea_features)
    
    # Render High Overlap section
    high_papers = buckets.get("high", [])
    if high_papers:
        for record in high_papers:
            paper = state.papers.get(record.paper_id)
            if not paper:
                # Fallback: use PaperRecord fields directly
                lines.append(f"### {record.title}")
                lines.append(f"- **Venue**: {record.venue or 'Unknown'}, {record.year or 'Unknown'}")
                lines.append(f"- **Citations**: {record.citation_count or 'N/A'}")
            else:
                lines.append(f"### {paper.title}")
                lines.append(f"- **Venue**: {paper.venue or 'Unknown'}, {paper.year or 'Unknown'}")
                lines.append(f"- **Citations**: {paper.citation_count or 'N/A'}")
                if paper.doi:
                    lines.append(f"- **DOI**: [{paper.doi}](https://doi.org/{paper.doi})")
            
            # Get notes from scored_papers if available
            match = match_map.get(record.paper_id)
            if match:
                if match.covered_facets:
                    lines.append(f"- **Covered Facets**: {', '.join(match.covered_facets)}")
                if match.structural_score and match.structural_score > 0:
                    lines.append(f"- **Structural Overlap**: {', '.join(match.structural_overlap)}")
                if match.notes:
                    lines.append(f"- **Notes**: {_clean_note_for_report(match.notes)}")
            
            # Evidence Snippets
            if find_snippets_fn and evidence_keywords and paper:
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
    
    partial_papers = buckets.get("partial", [])
    if partial_papers:
        for record in partial_papers:
            paper = state.papers.get(record.paper_id)
            if not paper:
                lines.append(f"### {record.title}")
                lines.append(f"- **Venue**: {record.venue or 'Unknown'}, {record.year or 'Unknown'}")
                lines.append(f"- **Citations**: {record.citation_count or 'N/A'}")
            else:
                lines.append(f"### {paper.title}")
                lines.append(f"- **Venue**: {paper.venue or 'Unknown'}, {paper.year or 'Unknown'}")
                lines.append(f"- **Citations**: {paper.citation_count or 'N/A'}")
            
            # Get notes from scored_papers if available
            match = match_map.get(record.paper_id)
            if match:
                if match.covered_facets:
                    lines.append(f"- **Covered Facets**: {', '.join(match.covered_facets)}")
                if match.structural_score and match.structural_score > 0:
                    lines.append(f"- **Structural Overlap**: {', '.join(match.structural_overlap)}")
                if match.notes:
                    lines.append(f"- **Notes**: {_clean_note_for_report(match.notes)}")
            lines.append("")
    else:
        lines.append("- None found.")
        lines.append("")
    
    # 5. Adjacent Work (level 1)
    lines.append("## 4. Adjacent Work (Coverage Level = 1)")
    lines.append("Related work in the same domain but with different approaches:")
    lines.append("")
    
    adjacent_papers = buckets.get("adjacent", [])
    if adjacent_papers:
        # Separate surveys from non-surveys, limit surveys to 3
        survey_keywords = ["survey", "review", "overview", "tutorial"]
        surveys = []
        non_surveys = []
        
        for record in adjacent_papers:
            title_lower = record.title.lower()
            if any(kw in title_lower for kw in survey_keywords):
                surveys.append(record)
            else:
                non_surveys.append(record)
        
        # Show non-surveys first (up to 7), then surveys (up to 3)
        for record in non_surveys[:7]:
            paper = state.papers.get(record.paper_id)
            title = paper.title if paper else record.title
            venue = (paper.venue if paper else record.venue) or "Unknown"
            year = (paper.year if paper else record.year) or "Unknown"
            lines.append(f"- **{title}** ({venue}, {year})")
            
            # Get notes from scored_papers if available
            match = match_map.get(record.paper_id)
            if match and match.notes:
                lines.append(f"  - {_clean_note_for_report(match.notes)}")
        
        for record in surveys[:3]:
            paper = state.papers.get(record.paper_id)
            title = paper.title if paper else record.title
            venue = (paper.venue if paper else record.venue) or "Unknown"
            year = (paper.year if paper else record.year) or "Unknown"
            lines.append(f"- **{title}** ({venue}, {year})")
            
            match = match_map.get(record.paper_id)
            if match and match.notes:
                lines.append(f"  - {_clean_note_for_report(match.notes)}")
        
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
