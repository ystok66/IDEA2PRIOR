#!/usr/bin/env python3
"""
Demo Research Agent for PaperArena
This script demonstrates how to use the agent to research specific topics (e.g., 2025 3D detection papers)
using web search and content reading tools, bypassing the local database.
"""

import os
import sys
import argparse
import warnings
from typing import Optional
from dotenv import load_dotenv
from loguru import logger

# Suppress Pydantic serialization warnings from smolagents/litellm
warnings.filterwarnings("ignore", message=".*PydanticSerializationUnexpectedValue.*")

# Fix Windows encoding issue for Chinese characters
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add the current directory to sys.path to ensure imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from smolagents import CodeAgent, LiteLLMModel

from pipelines.llm_config import get_litellm_config

def setup_environment() -> None:
    """Load environment variables from .env file."""
    load_dotenv(override=True)
    # Logging is handled by llm_config now

def create_minimal_agent(
    model_id: str = "gpt-4o",
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
) -> CodeAgent:
    """Create a minimal CodeAgent for pipeline-driven tasks (no heavy web tools)."""
    from tools.openalex_search import OpenAlexWorksSearchTool
    from tools.semantic_scholar_search import SemanticScholarPaperDetailTool, SemanticScholarCitationTool
    from tools.code_executor import AUTHORIZED_IMPORTS
    
    model = LiteLLMModel(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
    )
    
    # Minimal toolset for research pipelines
    openalex_tool = OpenAlexWorksSearchTool()
    s2_detail_tool = SemanticScholarPaperDetailTool()
    s2_citation_tool = SemanticScholarCitationTool()
    
    
    agent = CodeAgent(
        tools=[openalex_tool, s2_detail_tool, s2_citation_tool],
        model=model,
        additional_authorized_imports=AUTHORIZED_IMPORTS
    )
    
    return agent

def create_research_agent(
    model_id: str = "gpt-4o",
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    use_free_search: bool = False,
) -> CodeAgent:
    """Create a full-featured CodeAgent with web crawler (requires all dependencies)."""
    from tools.async_web_crawler import SimpleCrawler, CrawlerSearchTool, CrawlerReadTool
    from tools.duckduckgo_search import DuckDuckGoSearchTool
    from tools.openalex_search import OpenAlexWorksSearchTool
    from tools.semantic_scholar_search import SemanticScholarPaperDetailTool, SemanticScholarCitationTool
    from tools.google_file_search import GoogleFileSearchTool
    from tools.code_executor import AUTHORIZED_IMPORTS
    
    model = LiteLLMModel(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
    )
    
    # Initialize Tools
    crawler = SimpleCrawler(
        serpapi_key=os.getenv("SERPAPI_API_KEY"),
        model=model, 
        search_limit=5,
    )
    
    if use_free_search:
        logger.info("🦆 Using DuckDuckGo (Free) for search...")
        search_tool = DuckDuckGoSearchTool(max_results=5)
    else:
        if not os.getenv("SERPAPI_API_KEY"):
            logger.warning("⚠️ SERPAPI_API_KEY not found. Switching to DuckDuckGo...")
            search_tool = DuckDuckGoSearchTool(max_results=5)
        else:
            logger.info("🔍 Using Google Search (SerpAPI)...")
            search_tool = CrawlerSearchTool(crawler=crawler)
            
    read_tool = CrawlerReadTool(crawler=crawler, read_type="jina_read")
    
    openalex_tool = OpenAlexWorksSearchTool()
    s2_detail_tool = SemanticScholarPaperDetailTool()
    s2_citation_tool = SemanticScholarCitationTool()
    google_file_tool = GoogleFileSearchTool()
    
    
    agent = CodeAgent(
        tools=[
            search_tool, 
            read_tool, 
            openalex_tool, 
            s2_detail_tool, 
            s2_citation_tool,
            google_file_tool
        ],
        model=model,
        additional_authorized_imports=AUTHORIZED_IMPORTS
    )
    
    return agent

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task_type",
        type=str,
        default="benchmark_landscape",
        help="Type of research task to run.",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default="3D Object Detection",
        help="Research topic for the task.",
    )
    parser.add_argument(
        "--year_start",
        type=int,
        default=None,
        help="Start year for filtering papers.",
    )
    parser.add_argument(
        "--year_end",
        type=int,
        default=None,
        help="End year for filtering papers.",
    )
    parser.add_argument(
        "--use_full_agent",
        action="store_true",
        help="Use full agent with web crawler (requires all dependencies).",
    )
    parser.add_argument(
        "--idea",
        type=str,
        default=None,
        help="Idea description for prior-art check.",
    )
    parser.add_argument(
        "--max_rounds",
        type=int,
        default=1,
        help="Maximum number of exploration rounds for pipelines (default: 1).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode (limits LLM calls for prior_art_check).",
    )
    parser.add_argument(
        "--excluded_paper_title",
        type=str,
        default=None,
        help="Title of paper to exclude for blind evaluation.",
    )
    parser.add_argument(
        "--excluded_paper_arxiv",
        type=str,
        default=None,
        help="arXiv ID of paper to exclude (e.g., 2304.07647).",
    )
    parser.add_argument(
        "--excluded_paper_doi",
        type=str,
        default=None,
        help="DOI of paper to exclude.",
    )
    return parser.parse_args()

def main():
    setup_environment()
    
    args = parse_args()
    
    # Build LLM configuration using new dual-role system
    # For the main research agent, we typically use "heavy" role
    model_id, api_base, api_key = get_litellm_config(role="heavy")
    
    use_free_search = os.getenv("USE_FREE_SEARCH", "false").lower() == "true"
    
    try:
        # For pipeline-driven tasks, use minimal agent to avoid heavy dependencies
        if args.task_type == "benchmark_landscape" and not args.use_full_agent:
            from pipelines.tasks import run_benchmark_landscape
            
            logger.info("Using minimal agent (pipeline-driven mode)")
            agent = create_minimal_agent(
                model_id=model_id,
                api_base=api_base,
                api_key=api_key,
            )
            
            years = None
            if args.year_start and args.year_end:
                years = (args.year_start, args.year_end)
                
            logger.info(f"Running Benchmark Landscape for topic: {args.topic}")
            report = run_benchmark_landscape(
                agent=agent,
                topic=args.topic,
                years=years,
                venues=None,
                max_rounds=args.max_rounds,
            )
            print("\n" + "="*60)
            print(report)
            print("="*60 + "\n")
        elif args.task_type == "prior_art_check" and not args.use_full_agent:
            from pipelines.tasks import run_prior_art_check
            
            if not args.idea:
                raise ValueError("prior_art_check requires --idea argument.")
            
            logger.info("Using minimal agent (pipeline-driven mode)")
            agent = create_minimal_agent(
                model_id=model_id,
                api_base=api_base,
                api_key=api_key,
            )
            
            years = None
            if args.year_start and args.year_end:
                years = (args.year_start, args.year_end)
            
            logger.info(f"Running Prior Art Check for idea on topic: {args.topic}")
            
            # Build excluded papers list if any excluded paper args provided
            excluded_papers = None
            if args.excluded_paper_title or args.excluded_paper_arxiv or args.excluded_paper_doi:
                excluded_papers = [{
                    "title": args.excluded_paper_title or "",
                    "arxiv_id": args.excluded_paper_arxiv,
                    "doi": args.excluded_paper_doi,
                }]
                logger.info(f"🔒 Blind Evaluation Mode: Excluding paper '{args.excluded_paper_title or args.excluded_paper_arxiv}'")
            
            report = run_prior_art_check(
                agent=agent,
                idea_description=args.idea,
                topic=args.topic,
                years=years,
                venues=None,
                max_rounds=args.max_rounds,
                debug_mode=args.debug,
                excluded_papers=excluded_papers,
            )
            print("\n" + "="*60)
            print(report)
            print("="*60 + "\n")
        else:
            # Use full agent with all tools
            logger.info("Using full agent with web crawler")
            agent = create_research_agent(
                model_id=model_id,
                api_base=api_base,
                api_key=api_key,
                use_free_search=use_free_search,
            )
            
            user_query = (
                f"Please research '{args.topic}' papers from {args.year_start or 'recent'}-{args.year_end or 'present'} "
                "and summarize key trends."
            )
            result = agent.run(user_query)
            print(result)
            
    except Exception as e:
        logger.error(f"❌ An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
