import sys
import os

# Fix Windows encoding issue
sys.stdout.reconfigure(encoding="utf-8")

from loguru import logger
from dotenv import load_dotenv

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pipelines.tasks.slot_filling import run_slot_filling


class MockAgent:
    """Mock agent for testing without requiring real LLM calls."""
    
    def run(self, prompt: str) -> str:
        """
        Simple deterministic mock that returns valid JSON based on prompt content.
        """
        # For slot filling scoring
        if "relevance_level" in prompt.lower():
            # Simulate different relevance levels based on paper context
            if "monoculardepth" in prompt.lower() or "m3d" in prompt.lower():
                return '''{
                    "relevance_level": 3,
                    "should_cite": true,
                    "suggested_slot": "monocular 3D detection baselines",
                    "reason": "Highly relevant baseline method for monocular 3D object detection that should be cited."
                }'''
            elif "slam" in prompt.lower():
                return '''{
                    "relevance_level": 1,
                    "should_cite": true,
                    "suggested_slot": "general 3D vision systems",
                    "reason": "Related to 3D vision but focuses on SLAM rather than detection."
                }'''
            else:
                return '''{
                    "relevance_level": 2,
                    "should_cite": true,
                    "suggested_slot": "data augmentation techniques",
                    "reason": "Relevant augmentation approach that could be discussed in the context of robust 3D detection."
                }'''
        
        # Fallback
        return '{"result": "mock response"}'


def test_slot_filling():
    load_dotenv(override=True)
    
    manuscript_excerpt = """
    Recent advances in monocular 3D object detection have shown promising results.
    Camera-space data augmentation techniques have been explored to improve robustness.
    However, most methods still rely on dense depth supervision or complex LiDAR fusion.
    """
    
    known_refs = [
        "10.1109/CVPR.2019.00123",  # Some existing citation
        "MonoFlex",  # Known by title
        "arXiv:2104.05858",  # Known by arXiv ID
    ]
    
    topic = "monocular 3D object detection"
    
    logger.info(f"🚀 Starting Slot Filling Test for topic '{topic}'...")
    logger.info(f"Known refs: {len(known_refs)} references")
    logger.info(f"Manuscript excerpt length: {len(manuscript_excerpt)} chars")
    
    # Use mock agent for testing
    agent = MockAgent()
    
    # Run task
    report = run_slot_filling(
        agent=agent,
        topic=topic,
        manuscript_excerpt=manuscript_excerpt,
        known_refs=known_refs,
        years=(2018, 2024),
        venues=["CVPR", "ICCV", "ECCV"],
        max_rounds=1
    )
    
    logger.info("✅ Task finished. Generated Report:")
    print("\n" + "="*60)
    print(report)
    print("="*60 + "\n")
    
    # Also save to file for easier viewing
    output_path = "slot_filling_report_test.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"📄 Report also saved to {output_path}")
    
    # Basic assertions
    assert "Suggested Related Work Additions" in report, "Missing report title"
    assert "High-Priority" in report or "Medium-Priority" in report or "Low-Priority" in report, "Missing priority sections"
    logger.info("✅ All basic assertions passed!")


def test_is_known_ref():
    """Unit test for the _is_known_ref helper function."""
    from pipelines.tasks.slot_filling import _is_known_ref
    from pipelines.state import Paper
    
    logger.info("🚀 Testing _is_known_ref function...")
    
    # Test DOI matching
    paper1 = Paper(
        title="Test Paper 1",
        doi="10.1109/CVPR.2020.00123"
    )
    known_refs = ["10.1109/CVPR.2020.00123", "Some other paper"]
    assert _is_known_ref(paper1, known_refs), "Should match by DOI"
    
    # Test arXiv matching
    paper2 = Paper(
        title="Test Paper 2",
        doi=None
    )
    paper2.arxiv_id = "arXiv:2104.05858"
    known_refs = ["2104.05858", "Another paper"]
    assert _is_known_ref(paper2, known_refs), "Should match by arXiv ID"
    
    # Test title matching
    paper3 = Paper(
        title="MonoFlex: Objects Are Different - Flexible Inference for 3D Detection",
        doi=None
    )
    known_refs = ["MonoFlex", "M3D-RPN"]
    assert _is_known_ref(paper3, known_refs), "Should match by title substring"
    
    # Test non-match
    paper4 = Paper(
        title="Completely Different Paper About Cats",
        doi="10.1234/cats.2021"
    )
    known_refs = ["10.5678/dogs.2020", "Bird Watching"]
    assert not _is_known_ref(paper4, known_refs), "Should not match"
    
    logger.info("✅ All _is_known_ref tests passed!")


if __name__ == "__main__":
    # Run unit test first
    test_is_known_ref()
    
    # Run integration test
    test_slot_filling()
