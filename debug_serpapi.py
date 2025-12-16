"""
Debug script to test SerpAPI seed paper discovery.

This script tests the SerpAPI integration without running the full pipeline.
It will:
1. Generate search queries using expert LLM
2. Run 4-mode SerpAPI searches
3. Parse results with light LLM
4. Output discovered paper candidates

Usage:
    python debug_serpapi.py --topic "monocular 3D object detection" \
        --idea "camera-space rotation augmentation" \
        --year_start 2020 --year_end 2024
"""

import os
import sys
import json
import argparse
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from pipelines.tasks.prior_art_types import PriorArtConfig
from pipelines.tasks.prior_art_retrieval import (
    generate_serpapi_queries,
    seed_papers_from_serpapi,
)
from pipelines.clients.serpapi_client import (
    run_all_serpapi_modes,
    combine_all_mode_results,
)


def main():
    parser = argparse.ArgumentParser(description="Test SerpAPI seed paper discovery")
    parser.add_argument("--topic", type=str, required=True, help="Research topic")
    parser.add_argument("--idea", type=str, required=True, help="Research idea")
    parser.add_argument("--year_start", type=int, default=2020, help="Start year")
    parser.add_argument("--year_end", type=int, default=2024, help="End year")
    parser.add_argument("--save_raw", action="store_true", help="Save raw SerpAPI JSON")
    args = parser.parse_args()
    
    # Check for API key
    if not os.environ.get("SERPAPI_API_KEY"):
        logger.error("SERPAPI_API_KEY not set in environment!")
        logger.info("Set it in .env file or as environment variable")
        sys.exit(1)
    
    logger.info(f"Topic: {args.topic}")
    logger.info(f"Idea: {args.idea}")
    logger.info(f"Year range: {args.year_start} - {args.year_end}")
    logger.info("=" * 60)
    
    # Create config
    cfg = PriorArtConfig(
        serpapi_max_queries=3,
        serpapi_num_results_per_call=10,
        serpapi_use_duckduckgo=False,
        serpapi_timeout_sec=15,
    )
    
    # Step 1: Generate search queries
    logger.info("\n[Step 1] Generating search queries...")
    queries = generate_serpapi_queries(
        topic=args.topic,
        idea_description=args.idea,
        max_queries=cfg.serpapi_max_queries,
    )
    
    if not queries:
        logger.error("Failed to generate queries")
        sys.exit(1)
    
    # Step 2: Run SerpAPI searches and save raw results
    if args.save_raw:
        logger.info("\n[Step 2] Running SerpAPI searches (saving raw JSON)...")
        for qi, query in enumerate(queries, 1):
            raw_results = run_all_serpapi_modes(
                query=query,
                year_start=args.year_start,
                year_end=args.year_end,
                num_results=cfg.serpapi_num_results_per_call,
                use_duckduckgo=cfg.serpapi_use_duckduckgo,
                timeout=cfg.serpapi_timeout_sec,
            )
            
            # Save raw JSON for debugging
            debug_path = f"debug_serpapi_query_{qi}.json"
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump(raw_results, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved raw results to: {debug_path}")
            
            # Check if we found relevant results
            for mode, results in raw_results.items():
                organic = results.get("organic_results", [])
                logger.info(f"  {mode}: {len(organic)} results")
                for r in organic[:3]:
                    logger.info(f"    - {r.get('title', 'Unknown')[:60]}...")
    
    # Step 3: Run full SerpAPI seed discovery
    logger.info("\n[Step 3] Running full SerpAPI seed discovery...")
    seeds = seed_papers_from_serpapi(
        topic=args.topic,
        idea_description=args.idea,
        year_start=args.year_start,
        year_end=args.year_end,
        cfg=cfg,
    )
    
    # Output results
    logger.info("\n" + "=" * 60)
    logger.info(f"[RESULT] Found {len(seeds)} seed papers from web search:")
    logger.info("=" * 60)
    
    for i, seed in enumerate(seeds, 1):
        title = seed.get("title", "Unknown")
        year = seed.get("year", "?")
        venue = seed.get("venue", "?")
        url = seed.get("url", "")[:50] if seed.get("url") else ""
        logger.info(f"[{i}] {title}")
        logger.info(f"    Year: {year}, Venue: {venue}")
        if url:
            logger.info(f"    URL: {url}...")
        logger.info("")
    
    # Save to JSON
    output_path = "serpapi_seeds.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(seeds, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved seeds to: {output_path}")
    
    # Check for key papers
    key_papers = [
        "exploring geometric consistency",
        "pseudo-lidar",
        "fcos3d",
        "smoke",
        "monoflex",
    ]
    
    found_key = []
    for seed in seeds:
        title_lower = seed.get("title", "").lower()
        for key in key_papers:
            if key in title_lower:
                found_key.append(seed.get("title"))
                break
    
    if found_key:
        logger.info("\n[KEY PAPERS FOUND]")
        for title in found_key:
            logger.info(f"  ✓ {title}")
    else:
        logger.warning("\n[WARNING] None of the expected key papers were found")
        logger.info("Expected to find papers containing: " + ", ".join(key_papers))


if __name__ == "__main__":
    main()
