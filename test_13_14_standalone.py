"""
Standalone test script for 1.3 (Method/Experiment Snippets) and 1.4 (Key Papers).

This script runs 1.3 and 1.4 against an existing run directory that already has
pdf_sections/*.sections.json files, without re-running the full search pipeline.

Usage:
    python test_13_14_standalone.py --run_dir runs/20251208_155800_monocular_3d_object_detection

Requirements:
    - The run directory must exist and contain pdf_sections/*.sections.json files
    - The script will create/overwrite synthesis/method_exp_snippets.jsonl and 
      synthesis/key_papers_selection.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not required

from loguru import logger

from pipelines.state import PipelineState, Paper, Intent
from pipelines.run_manager import RunManager
from pipelines.tasks.prior_art_types import PriorArtConfig

from pipelines.tasks.prior_art_fulltext import (
    build_method_experiment_snippets,
    select_key_papers_and_dump_intro_rw_full,
)
from pipelines.tasks.prior_art_synthesis import build_synthesis_input


def load_state_from_run(run_dir: Path) -> PipelineState:
    """Load a minimal PipelineState from an existing run directory."""
    
    # Create minimal intent
    intent = Intent(
        task_type="prior_art_check",
        topic="test topic",
        years=(2020, 2024),
        venues=[],
    )
    
    state = PipelineState(intent=intent)
    
    # Load papers from pdf_sections
    sections_dir = run_dir / "pdf_sections"
    if not sections_dir.exists():
        logger.error(f"No pdf_sections directory found in {run_dir}")
        return state
    
    for sections_file in sections_dir.glob("*.sections.json"):
        try:
            with open(sections_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            paper_id = data.get("paper_id", sections_file.stem.replace(".sections", ""))
            title = data.get("title", paper_id)
            year = data.get("year", 2023)
            
            # Create Paper with basic info
            paper = Paper(
                title=title,
                year=year,
                venue=data.get("venue", ""),
                abstract="",
                doi=paper_id if paper_id.startswith("10.") else None,
                citation_count=data.get("citation_count", 0),
            )
            
            # Add a placeholder chunk to indicate fulltext is available
            from pipelines.state import Chunk
            paper.chunks = [Chunk(text="fulltext available", page_start=1)]
            
            state.papers[paper_id] = paper
            logger.info(f"Loaded paper: {title[:50]}... (id={paper_id[:30]})")
            
        except Exception as e:
            logger.warning(f"Failed to load {sections_file}: {e}")
    
    logger.info(f"Loaded {len(state.papers)} papers from {sections_dir}")
    return state


def main():
    parser = argparse.ArgumentParser(description="Test 1.3 and 1.4 features standalone")
    parser.add_argument(
        "--run_dir",
        type=str,
        required=True,
        help="Path to existing run directory (e.g., runs/20251208_155800_...)",
    )
    parser.add_argument(
        "--facets",
        type=str,
        nargs="+",
        default=[
            "Monocular 3D object detection",
            "Camera-space data augmentation",
            "Depth estimation improvement",
        ],
        help="Facets to use for snippet extraction",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (fewer LLM calls)",
    )
    args = parser.parse_args()
    
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        logger.error(f"Run directory does not exist: {run_dir}")
        sys.exit(1)
    
    # Load state
    state = load_state_from_run(run_dir)
    if not state.papers:
        logger.error("No papers loaded from run directory")
        sys.exit(1)
    
    # Create config
    config = PriorArtConfig(
        debug_mode=args.debug,
        enable_method_exp_snippets=True,
        enable_intro_rw_full=True,
        max_paragraphs_per_facet=30,
        max_snippets_per_paper_facet=3,
        debug_max_paragraphs_per_facet=15,
        key_paper_top_cited_k=3,
        key_paper_latest_k=3,
        key_paper_survey_k=2,
    )
    
    # Create minimal run_mgr for output paths
    class DummyRunMgr:
        def __init__(self, run_dir: Path):
            self.run_dir = str(run_dir)
    
    run_mgr = DummyRunMgr(run_dir)
    
    # Facets and idea features
    facets = args.facets
    idea_features = {
        "datasets": {"kitti", "nuscenes"},
        "metrics": {"ap", "map"},
        "method_families": {"cnn", "transformer"},
    }
    
    # Run 1.3
    logger.info("=" * 60)
    logger.info("Running 1.3: Method/Experiment Snippets")
    logger.info("=" * 60)
    
    try:
        build_method_experiment_snippets(
            state=state,
            facets=facets,
            idea_features=idea_features,
            run_mgr=run_mgr,
            config=config,
        )
    except Exception as e:
        logger.error(f"1.3 failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Run 1.4
    logger.info("=" * 60)
    logger.info("Running 1.4: Key Papers & Intro+RW Full")
    logger.info("=" * 60)
    
    try:
        # 1.4 doesn't need agent in debug mode
        select_key_papers_and_dump_intro_rw_full(
            agent=None,  # Not used in debug mode
            state=state,
            task_root=run_dir,
            config=config,
        )
    except Exception as e:
        logger.error(f"1.4 failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Run 2.1 Synthesis JSON Builder
    logger.info("=" * 60)
    logger.info("Running 2.1: Synthesis JSON Builder")
    logger.info("=" * 60)
    
    try:
        synthesis_input = build_synthesis_input(
            state=state,
            config=config,
            run_dir=run_dir,
            facets=[{"id": f"F{i+1}", "label": f, "category": "general"} for i, f in enumerate(facets)],
        )
    except Exception as e:
        logger.error(f"2.1 failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Check outputs
    logger.info("=" * 60)
    logger.info("Checking outputs")
    logger.info("=" * 60)
    
    snippets_path = run_dir / "synthesis" / "method_exp_snippets.jsonl"
    if snippets_path.exists():
        with open(snippets_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        logger.info(f"[1.3] method_exp_snippets.jsonl: {len(lines)} snippets")
        for line in lines[:3]:
            obj = json.loads(line)
            logger.info(f"  - [{obj.get('facet_label', 'N/A')[:20]}] {obj.get('bullet', 'N/A')[:60]}...")
    else:
        logger.warning(f"[1.3] {snippets_path} not found")
    
    key_papers_path = run_dir / "synthesis" / "key_papers_selection.json"
    if key_papers_path.exists():
        with open(key_papers_path, "r", encoding="utf-8") as f:
            selection = json.load(f)
        buckets = selection.get("buckets", {})
        logger.info(f"[1.4] key_papers_selection.json: {len(selection.get('per_paper_roles', {}))} key papers")
        for bucket_name, bucket_data in buckets.items():
            primary = bucket_data.get("primary", "None")
            logger.info(f"  - {bucket_name}: primary={primary[:30] if primary else 'None'}")
    else:
        logger.warning(f"[1.4] {key_papers_path} not found")
    
    intro_rw_dir = run_dir / "synthesis" / "intro_rw_full"
    if intro_rw_dir.exists():
        json_files = list(intro_rw_dir.glob("*.json"))
        logger.info(f"[1.4] intro_rw_full/: {len(json_files)} files")
    else:
        logger.warning(f"[1.4] {intro_rw_dir} not found")
    
    # Check synthesis_input.json
    synthesis_path = run_dir / "synthesis" / "synthesis_input.json"
    if synthesis_path.exists():
        with open(synthesis_path, "r", encoding="utf-8") as f:
            synthesis = json.load(f)
        num_papers = len(synthesis.get("papers", []))
        buckets = synthesis.get("buckets", {})
        has_prompt = "prompt_templates" in synthesis
        logger.info(f"[2.1] synthesis_input.json: {num_papers} papers, has_prompt_templates={has_prompt}")
        logger.info(f"  - high_overlap: {len(buckets.get('high_overlap', []))}")
        logger.info(f"  - partial_overlap: {len(buckets.get('partial_overlap', []))}")
        logger.info(f"  - adjacent: {len(buckets.get('adjacent', []))}")
    else:
        logger.warning(f"[2.1] {synthesis_path} not found")
    
    logger.info("Done!")


if __name__ == "__main__":
    main()
