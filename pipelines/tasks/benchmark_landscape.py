# paperarena_hub/pipelines/tasks/benchmark_landscape.py

"""
Task-level orchestrator for "benchmark landscape" analysis.

This task takes a high-level topic (e.g., "3D object detection"),
optionally a year range and venues, runs the shared exploration loop,
and then summarizes:
- commonly used datasets,
- common evaluation metrics, and
- representative baselines / SOTA methods.

This file only defines the high-level flow.
Heavier logic (e.g., extracting datasets/metrics from papers) can be
implemented later, or delegated to separate helper modules.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from loguru import logger

from pipelines.state import Intent, PipelineState
from pipelines.exploration_loop import run_exploration_loop


def build_benchmark_intent(
    topic: str,
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
) -> Intent:
    """
    Build a simple Intent for the benchmark-landscape task.

    In a later version, this can be replaced by an LLM-powered
    intent parser that reads a free-form user query. For now, we
    assume the caller already has structured inputs.
    """
    return Intent(
        task_type="benchmark_landscape",
        topic=topic,
        years=years,
        venues=venues or [],
        extra_constraints={},
    )


def run_benchmark_landscape(
    agent,
    topic: str,
    years: Optional[Tuple[int, int]] = None,
    venues: Optional[List[str]] = None,
    max_rounds: int = 2,
) -> str:
    """
    High-level entry point for the "benchmark landscape" task.

    Parameters
    ----------
    agent:
        The CodeAgent instance configured with OpenAlex / Semantic Scholar
        tools and any other required tools.
    topic:
        Research topic, e.g. "3D object detection", "monocular depth estimation".
    years:
        Optional (start_year, end_year) filter, e.g. (2020, 2025).
    venues:
        Optional list of target venues, e.g. ["CVPR", "ICLR", "NeurIPS"].
    max_rounds:
        How many exploration rounds to run in the shared exploration loop.

    Returns
    -------
    str
        A human-readable report (Markdown) that summarizes benchmarks for
        this topic: datasets, metrics, and representative baselines.
    """
    # 1) Build the initial Intent and PipelineState.
    intent = build_benchmark_intent(topic=topic, years=years, venues=venues)
    state = PipelineState(intent=intent)

    logger.info(f"[BenchmarkLandscape] Starting for topic='{topic}'.")

    # 2) Run the shared exploration loop to populate state.papers, etc.
    state = run_exploration_loop(
        agent=agent,
        state=state,
        max_rounds=max_rounds,
    )

    logger.info(
        f"[BenchmarkLandscape] Exploration finished with "
        f"{len(state.papers)} papers."
    )

    # 3) Analyze the final state to extract benchmark information.
    #    This step should:
    #      - Infer commonly used datasets from paper titles/abstracts/metadata.
    #      - Infer common evaluation metrics used for this topic.
    #      - Identify representative baselines / SOTA models.
    #
    # For v0, we can implement a very lightweight heuristic version:
    #    - Just list paper titles, venues, and years.
    #    - Optionally highlight the most cited papers.
    #
    # In later versions, we can:
    #    - Use a RAG-style QA over parsed PDFs.
    #    - Add structured dataset/metric extraction.
    report = render_benchmark_report(state)

    return report


def render_benchmark_report(state: PipelineState) -> str:
    """
    Render a human-readable benchmark landscape report from the final state.

    This function should be pure: it only reads from `state` and returns
    a Markdown string.
    """
    lines = []
    lines.append(f"# Benchmark Landscape: {state.intent.topic}")
    lines.append("")
    
    # Summary section
    lines.append("## Summary")
    lines.append(f"- **Total Papers Discovered**: {len(state.papers)}")
    
    years_str = f"{state.intent.years[0]}-{state.intent.years[1]}" if state.intent.years else "All years"
    lines.append(f"- **Year Range**: {years_str}")
    
    venues_str = ", ".join(state.intent.venues) if state.intent.venues else "All venues"
    lines.append(f"- **Target Venues**: {venues_str}")
    lines.append("")
    
    if not state.papers:
        lines.append("> [!WARNING]")
        lines.append("> No papers found for this topic with the given filters.")
        return "\n".join(lines)

    # Top Cited Papers section
    lines.append("## Top Cited Papers")
    lines.append("Here are the most cited papers discovered in this exploration:")
    lines.append("")
    
    # Sort papers by citation count (descending), treating None as 0
    sorted_papers = sorted(
        state.papers.values(),
        key=lambda p: p.citation_count if p.citation_count is not None else 0,
        reverse=True
    )
    
    # List top 10
    top_k = 10
    for i, paper in enumerate(sorted_papers[:top_k], 1):
        citation_count = paper.citation_count if paper.citation_count is not None else "N/A"
        venue = paper.venue or "Unknown Venue"
        year = paper.year or "Unknown Year"
        
        lines.append(f"{i}. **{paper.title}**")
        lines.append(f"   - *{venue}, {year}* | Citations: {citation_count}")
        if paper.doi:
            lines.append(f"   - DOI: [{paper.doi}](https://doi.org/{paper.doi})")
        elif paper.openalex_id:
            lines.append(f"   - OpenAlex ID: {paper.openalex_id}")
        lines.append("")

    lines.append("")
    # Datasets Section (Heuristic / Extracted)
    lines.append("## Common Datasets (Detected)")
    datasets_found = {k: v for k, v in state.datasets.items() if not k.startswith("metric_")}
    if datasets_found:
        for ds, info in datasets_found.items():
            lines.append(f"- **{ds.upper()}**: Found in {info['count']} papers")
    else:
        lines.append("*No datasets detected in analyzed papers.*")
    lines.append("")
    
    # Metrics Section (Heuristic / Extracted)
    lines.append("## Common Metrics (Detected)")
    metrics_found = {k.replace("metric_", ""): v for k, v in state.datasets.items() if k.startswith("metric_")}
    if metrics_found:
        # Sort by count descending
        sorted_metrics = sorted(metrics_found.items(), key=lambda x: x[1]['count'], reverse=True)
        for metric, info in sorted_metrics:
            lines.append(f"- **{metric.upper()}**: Used in {info['count']} papers")
    else:
        lines.append("*No metrics detected in analyzed papers.*")
    lines.append("")
    
    # Method Families Section (Heuristic / Extracted)
    lines.append("## Common Method Families (Detected)")
    # Aggregate method families from all papers
    family_counts = {}
    for paper in state.papers.values():
        for family in paper.method_families:
            if family not in family_counts:
                family_counts[family] = 0
            family_counts[family] += 1
    
    if family_counts:
        # Sort by count descending
        sorted_families = sorted(family_counts.items(), key=lambda x: x[1], reverse=True)
        for family, count in sorted_families:
            lines.append(f"- **{family}**: Used in {count} paper(s)")
    else:
        lines.append("*No method families detected (PDF parsing or method detection may not have been triggered in this run).*")
    lines.append("")

    lines.append("---")
    lines.append("*Note: This is a preliminary report based on metadata and heuristic PDF analysis. Dataset, metric, and method family extraction will be improved in future versions.*")

    return "\n".join(lines)
