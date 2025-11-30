
import sys
import os
from loguru import logger
from dotenv import load_dotenv

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pipelines.tasks import run_benchmark_landscape

def test_benchmark_task():
    load_dotenv(override=True)
    
    topic = "Mamba State Space Models"
    logger.info(f"🚀 Starting Benchmark Landscape Task Test for '{topic}'...")
    
    # Mock agent
    class MockAgent:
        pass
    agent = MockAgent()
    
    # Run task
    report = run_benchmark_landscape(
        agent=agent,
        topic=topic,
        years=(2023, 2024),
        venues=[], # Simplify for v0 test
        max_rounds=1
    )
    
    logger.info("✅ Task finished. Generated Report:")
    print("\n" + "="*40)
    print(report)
    print("="*40 + "\n")

if __name__ == "__main__":
    test_benchmark_task()
