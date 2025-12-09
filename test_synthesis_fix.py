"""
Standalone test script to regenerate synthesis_input.json from existing run data.
This avoids re-running the entire search pipeline.
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
from pipelines.state import PipelineState, Paper, Intent
from pipelines.tasks.prior_art_types import PriorArtConfig, PriorArtMatch
from pipelines.tasks.prior_art_synthesis import build_synthesis_input

def load_state_from_run(run_dir: Path) -> tuple:
    """Load state, coverage_levels, and matches from run artifacts."""
    
    # Load config
    config_path = run_dir / "config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            topic = config_data.get("topic", "unknown")
            idea = config_data.get("idea", "")
    else:
        topic = "monocular 3D object detection"
        idea = "rotation data augmentation"
    
    # Create minimal state
    state = PipelineState(
        intent=Intent(
            topic=topic,
            task_type="prior_art_check",
            extra_constraints={"idea": idea}
        )
    )
    
    # Load papers from pdf_sections
    pdf_sections_dir = run_dir / "pdf_sections"
    papers_loaded = 0
    if pdf_sections_dir.exists():
        for section_file in pdf_sections_dir.glob("*.sections.json"):
            with open(section_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("paper_id", section_file.stem.replace(".sections", ""))
            paper = Paper(
                title=data.get("title", "Unknown"),
                year=data.get("year"),
                doi=pid if pid.startswith("10.") else None,
            )
            state.papers[pid] = paper
            papers_loaded += 1
    
    # Also scan synthesis dir for paper IDs
    synthesis_dir = run_dir / "synthesis"
    intro_rw_full_dir = synthesis_dir / "intro_rw_full"
    if intro_rw_full_dir.exists():
        for json_file in intro_rw_full_dir.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("paper_id", json_file.stem)
            if pid not in state.papers:
                paper = Paper(
                    title=data.get("title", "Unknown"),
                    year=data.get("year"),
                    doi=pid if pid.startswith("10.") else None,
                )
                state.papers[pid] = paper
                papers_loaded += 1
    
    logger.info(f"Loaded {papers_loaded} papers from run artifacts")
    
    # Simulate coverage_levels (based on key_papers_selection if available)
    coverage_levels = {}
    key_papers_path = synthesis_dir / "key_papers_selection.json"
    key_paper_ids = set()
    if key_papers_path.exists():
        with open(key_papers_path, "r", encoding="utf-8") as f:
            key_data = json.load(f)
        for role_list in key_data.get("per_paper_roles", {}).values():
            for role in role_list:
                key_paper_ids.add(role)
        # Key papers get level 2 (partial overlap)
        for cat_data in key_data.get("per_category_selection", {}).values():
            for pid in cat_data.get("selected_paper_ids", []):
                key_paper_ids.add(pid)
    
    # Assign coverage levels
    for pid in state.papers.keys():
        if pid in key_paper_ids:
            coverage_levels[pid] = 2  # Partial overlap
        else:
            coverage_levels[pid] = 1  # Adjacent
    
    logger.info(f"Coverage levels: {sum(1 for v in coverage_levels.values() if v >= 2)} partial, {sum(1 for v in coverage_levels.values() if v == 1)} adjacent")
    
    # Create mock matches (with facet_labels from snippets)
    matches = []
    snippets_path = synthesis_dir / "method_exp_snippets.jsonl"
    facets_by_pid = {}
    if snippets_path.exists():
        with open(snippets_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    snippet = json.loads(line)
                    pid = snippet.get("paper_id", "")
                    facet = snippet.get("facet_label", "")
                    if pid and facet:
                        if pid not in facets_by_pid:
                            facets_by_pid[pid] = set()
                        facets_by_pid[pid].add(facet)
    
    for pid in state.papers.keys():
        match = PriorArtMatch(
            paper_id=pid,
            coverage_level=coverage_levels.get(pid, 0),
            covered_facet_labels=list(facets_by_pid.get(pid, [])),
            task_similarity=0.8 if coverage_levels.get(pid, 0) >= 2 else 0.5,
            method_similarity=0.7 if coverage_levels.get(pid, 0) >= 2 else 0.4,
        )
        matches.append(match)
    
    logger.info(f"Created {len(matches)} matches, {sum(1 for m in matches if m.covered_facet_labels)} with facet_labels")
    
    return state, coverage_levels, matches, topic, idea


def main():
    # Use the latest run
    run_dir = Path("F:/PaperArena/paperarena_hub/runs/20251209_143654_monocular_3d_object_detection")
    
    logger.info(f"Testing synthesis on run: {run_dir}")
    
    # Load data
    state, coverage_levels, matches, topic, idea = load_state_from_run(run_dir)
    
    # Create config
    config = PriorArtConfig()
    
    # Define facets
    facets = [
        {"id": "F1", "label": "Monocular 3D object detection task", "category": "core"},
        {"id": "F2", "label": "Rotation-based data augmentation method", "category": "core"},
        {"id": "F3", "label": "Camera coordinate space transformation", "category": "core"},
        {"id": "F4", "label": "2D-3D label synchronization", "category": "core"},
        {"id": "F5", "label": "Viewpoint variation robustness", "category": "core"},
    ]
    
    # Call build_synthesis_input with the new parameters
    result = build_synthesis_input(
        state=state,
        config=config,
        run_dir=run_dir,
        facets=facets,
        user_query=idea,
        retrieval_queries=["monocular 3D detection", "data augmentation"],
        coverage_levels=coverage_levels,
        scored_matches=matches,
    )
    
    # Print summary
    print("\n" + "="*60)
    print("SYNTHESIS JSON SUMMARY")
    print("="*60)
    print(f"coverage_by_level: {result['global_stats']['coverage_by_level']}")
    print(f"buckets: {{{', '.join(f'{k}: {len(v)}' for k, v in result['buckets'].items())}}}")
    print(f"papers included: {len(result['papers'])}")
    
    # Check facet_labels
    with_facets = sum(1 for p in result['papers'] if p.get('facet_labels'))
    print(f"papers with facet_labels: {with_facets}")
    
    # Show first paper with facet_labels
    for p in result['papers']:
        if p.get('facet_labels'):
            print(f"\nExample paper with facets:")
            print(f"  title: {p['title'][:50]}...")
            print(f"  overlap_level: {p['overlap_level']}")
            print(f"  facet_labels: {p['facet_labels']}")
            break
    
    print("\n" + "="*60)
    print(f"Output: {run_dir / 'synthesis' / 'synthesis_input.json'}")
    print("="*60)


if __name__ == "__main__":
    main()
