#!/usr/bin/env python3
"""
Demo Research Agent for PaperArena
This script demonstrates how to use the agent to research specific topics (e.g., 2025 3D detection papers)
using web search and content reading tools, bypassing the local database.
"""

import os
import sys
import argparse
from typing import Optional
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

# Fix Windows encoding issue for Chinese characters
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add the current directory to sys.path to ensure imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from smolagents import CodeAgent, LiteLLMModel

from pipelines.llm_config import get_litellm_config

def check_requirements() -> bool:
    """
    Check if all required imports and dependencies are working correctly.
    
    Returns:
        bool: True if all checks pass, False otherwise
    """
    print('=== Checking Opposition Generator Requirements ===\n')
    
    all_ok = True
    
    # 1. Check imports
    try:
        from tools.opposition_generator import OppositionGeneratorTool, generate_opposition_standalone
        print('✅ Tool imports: OK')
    except Exception as e:
        print(f'❌ Tool imports: FAILED - {e}')
        all_ok = False
    
    # 2. Check LLM config
    try:
        model_id, api_base, api_key = get_litellm_config('heavy')
        print(f'✅ LLM config: OK (model={model_id})')
    except Exception as e:
        print(f'❌ LLM config: FAILED - {e}')
        all_ok = False
    
    # 3. Check tool instantiation
    tool = None
    try:
        tool = OppositionGeneratorTool()
        print(f'✅ Tool instantiation: OK (name={tool.name})')
    except Exception as e:
        print(f'❌ Tool instantiation: FAILED - {e}')
        all_ok = False
    
    # 4. Check tool inputs (only if tool was successfully instantiated)
    if tool is not None:
        try:
            required_inputs = ['content', 'context', 'focus_areas']
            for inp in required_inputs:
                if inp in tool.inputs:
                    print(f'✅ Input "{inp}": OK')
                else:
                    print(f'❌ Input "{inp}": MISSING')
                    all_ok = False
        except Exception as e:
            print(f'❌ Input check: FAILED - {e}')
            all_ok = False
    
    # 5. Check history directory exists
    if os.path.exists('history'):
        history_files = [f for f in os.listdir('history') if os.path.isdir(os.path.join('history', f))]
        print(f'✅ History directory: OK ({len(history_files)} timestamp directories)')
    else:
        print('⚠️  History directory: MISSING (will be created automatically)')
    
    print('\n=== Requirements Check Complete ===\n')
    
    return all_ok

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
    from tools.opposition_generator import OppositionGeneratorTool
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
    opposition_tool = OppositionGeneratorTool()
    
    
    agent = CodeAgent(
        tools=[openalex_tool, s2_detail_tool, s2_citation_tool, opposition_tool],
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
    from tools.opposition_generator import OppositionGeneratorTool
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
    opposition_tool = OppositionGeneratorTool()
    
    
    agent = CodeAgent(
        tools=[
            search_tool, 
            read_tool, 
            openalex_tool, 
            s2_detail_tool, 
            s2_citation_tool,
            google_file_tool,
            opposition_tool
        ],
        model=model,
        additional_authorized_imports=AUTHORIZED_IMPORTS
    )
    
    return agent

def save_report_to_history(task_type: str, topic: str, report: str, args, idea: Optional[str] = None):
    """
    Save generated report to history directory with new filename format
    
    Args:
        task_type: Type of task (benchmark_landscape, prior_art_check, research_agent)
        topic: Research topic (search topic)
        report: Generated report content
        args: Command line arguments
        idea: Optional idea description (for prior_art_check)
    """
    try:
        # Create history directory if it doesn't exist
        history_dir = Path("history")
        history_dir.mkdir(parents=True, exist_ok=True)
        
        # Create safe topic name for filename (sanitize for filesystem)
        safe_topic = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in topic)
        safe_topic = safe_topic.replace(' ', '_')
        # Remove multiple consecutive underscores
        while '__' in safe_topic:
            safe_topic = safe_topic.replace('__', '_')
        # Remove leading/trailing underscores
        safe_topic = safe_topic.strip('_')
        
        # Create timestamp in format: yyyy-mm-dd-hh
        timestamp = datetime.now().strftime("%Y-%m-%d-%H")
        
        # Create filename: <search topic>_<yyyy-mm-dd-hh>.md
        filename = f"{safe_topic}_{timestamp}.md"
        
        file_path = history_dir / filename
        
        # Write report to file (as markdown)
        with open(file_path, "w", encoding="utf-8") as f:
            # Write metadata header as markdown frontmatter or header
            f.write(f"# {task_type.replace('_', ' ').title()}\n\n")
            f.write(f"**Topic:** {topic}\n")
            if idea:
                f.write(f"**Idea:** {idea}\n")
            if args.year_start and args.year_end:
                f.write(f"**Year Range:** {args.year_start}-{args.year_end}\n")
            f.write(f"**Max Rounds:** {args.max_rounds}\n")
            f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\n---\n\n")
            
            # Write report content
            f.write(report)
        
        logger.info(f"📁 Report saved to: {file_path}")
        print(f"\n💾 Report saved to: {file_path}\n")
        
    except Exception as e:
        logger.error(f"❌ Failed to save report to history: {e}")


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
        "--check",
        action="store_true",
        help="Check if all required imports and dependencies are working correctly.",
    )
    return parser.parse_args()

def main():
    setup_environment()
    
    args = parse_args()
    
    # If --check flag is set, run requirements check and exit
    if args.check:
        check_requirements()
        return
    
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
                max_papers=20,
            )
            
            if report:
                logger.info(f"Report generated successfully, length: {len(report)} characters")
                # Save report to history
                save_report_to_history("benchmark_landscape", args.topic, report, args)
            else:
                logger.error("Report is None or empty, not saving")
            
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
            report = run_prior_art_check(
                agent=agent,
                idea_description=args.idea,
                topic=args.topic,
                years=years,
                venues=None,
                max_rounds=args.max_rounds,
                debug_mode=args.debug,
            )
            
            # Save report to history
            save_report_to_history("prior_art_check", args.topic, report, args, idea=args.idea)
            
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
            
            # Save report to history
            save_report_to_history("research_agent", args.topic, result, args)
            
            print(result)
            
    except Exception as e:
        logger.error(f"❌ An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
