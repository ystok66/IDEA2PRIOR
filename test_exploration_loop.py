
import sys
import os
from loguru import logger
from dotenv import load_dotenv

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pipelines.state import PipelineState, Intent
from pipelines.exploration_loop import run_exploration_loop

def test_loop():
    load_dotenv(override=True)
    
    # 1. Create a dummy intent
    intent = Intent(
        task_type="benchmark_landscape",
        topic="Mamba State Space Models",
        years=(2023, 2024),
        venues=[] # Simplify for v0 test to avoid filter issues
    )
    
    # 2. Initialize state
    state = PipelineState(intent=intent)
    
    # 3. Mock agent (not used in v0 logic but required by signature)
    class MockAgent:
        pass
    agent = MockAgent()
    
    # 4. Run loop
    logger.info("🚀 Starting Exploration Loop Test...")
    final_state = run_exploration_loop(
        agent=agent,
        state=state,
        max_rounds=1, # Run 1 round for testing
        min_new_per_round=1
    )
    
    # 5. Verify results
    logger.info(f"✅ Loop finished. Total papers: {len(final_state.papers)}")
    
    if len(final_state.papers) > 0:
        first_paper = list(final_state.papers.values())[0]
        logger.info(f"📄 Sample Paper: {first_paper.title}")
        logger.info(f"   Year: {first_paper.year}")
        logger.info(f"   Citations: {first_paper.citation_count}")
        logger.info(f"   DOI: {first_paper.doi}")
        logger.info(f"   S2 ID: {first_paper.s2_id}")
    else:
        logger.warning("⚠️ No papers found!")

if __name__ == "__main__":
    test_loop()
