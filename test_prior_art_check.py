import sys
import os

# Fix Windows encoding issue
sys.stdout.reconfigure(encoding="utf-8")

from loguru import logger
from dotenv import load_dotenv

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pipelines.tasks import run_prior_art_check


class MockAgent:
    """Mock agent for testing without requiring real LLM calls."""
    
    def __call__(self, messages):
        """
        Make MockAgent callable for call_llm_json compatibility.
        Extracts prompt from messages and delegates to run().
        """
        if isinstance(messages, list) and len(messages) > 0:
            prompt = messages[0].get("content", "") if isinstance(messages[0], dict) else str(messages[0])
        else:
            prompt = str(messages)
        return self.run(prompt)
    
    def run(self, prompt: str) -> str:
        """
        Simple deterministic mock that returns valid JSON based on prompt content.
        """
        # For seed papers from LLM
        if "list up to" in prompt.lower() and "real papers" in prompt.lower():
            return '''[
                {"title": "MonoGRNet: A Geometric Reasoning Network for Monocular 3D Object Detection", "venue": "CVPR", "year": 2019, "doi": null, "arxiv_id": null, "url": null}
            ]'''
        
        # For search queries
        if "search queries" in prompt.lower():
            return '["monocular 3D detection", "3D object detection augmentation"]'
        
        # For facet decomposition
        if "key technical facets" in prompt.lower():
            return '''[
                "Camera-space rotation augmentation",
                "2D-3D geometric consistency preservation",
                "Depth-free augmentation approach",
                "Multi-modal applicability (RGB and LiDAR+RGB fusion)"
            ]'''
        
        # For paper scoring
        if "coverage_level" in prompt.lower():
            # Return a simple scoring result
            return '''{
                "coverage_level": 1,
                "covered_facets": ["F1"],
                "notes": "Adjacent work in the same domain but different technical approach."
            }'''
            
        # For structural features extraction
        if "extract the key structural features" in prompt.lower():
            return '''{
                "datasets": ["KITTI", "nuScenes"],
                "metrics": ["mAP", "rotation error"],
                "method_families": ["Data Augmentation", "Geometric Transformation"]
            }'''
        
        # For high-level structural facets extraction
        if "high-level structural facets" in prompt.lower():
            return '''{
                "problem_facets": ["monocular 3D detection", "viewpoint-invariant representation"],
                "setting_facets": ["camera-space coordinate system", "single RGB image input"],
                "mechanism_facets": ["camera-space rotation augmentation", "2D-3D geometric consistency"],
                "assumption_facets": ["calibrated pinhole camera", "no depth supervision"]
            }'''
        
        # For relevance keywords
        if "relevance keywords" in prompt.lower():
            return '["3d detection", "monocular", "augmentation"]'
        
        # Fallback
        return '{"result": "mock response"}'


def test_prior_art_check():
    load_dotenv(override=True)
    
    idea = ("A novel data augmentation scheme for monocular 3D object detection that applies "
            "rotation augmentation in camera space while preserving 2D-3D geometric consistency, "
            "without requiring depth information, and is applicable to both RGB and LiDAR+RGB fusion pipelines.")
    
    topic = "monocular 3D object detection"
    
    logger.info(f"🚀 Starting Prior Art Check Test for idea on '{topic}'...")
    
    # Use mock agent for testing
    agent = MockAgent()
    
    # Run task
    report = run_prior_art_check(
        agent=agent,
        idea_description=idea,
        topic=topic,
        years=(2019, 2024),
        venues=[],
        max_rounds=1,
        debug_mode=True,
    )
    
    logger.info("✅ Task finished. Generated Report:")
    print("\n" + "="*60)
    print(report)
    print("="*60 + "\n")
    
    # Also save to file for easier viewing
    output_path = "prior_art_report_test.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info(f"📄 Report also saved to {output_path}")


def test_render_report_with_chunks():
    """Unit test for report rendering with structural features, evidence snippets, and high-level facets."""
    logger.info("🚀 Starting Unit Test for Report Rendering...")
    
    from pipelines.state import PipelineState, Intent, Paper, Chunk
    from pipelines.tasks.prior_art_check import render_prior_art_report, PriorArtMatch
    
    # 1. Setup State & Paper
    intent = Intent(task_type="prior_art_check", topic="test topic")
    state = PipelineState(intent=intent)
    
    paper = Paper(
        title="Test Paper with Evidence",
        year=2024,
        venue="CVPR",
        citation_count=100,
        doi="10.1234/test",
        datasets=["KITTI"],
        metrics=["mAP"],
        method_families=["Data Augmentation"]
    )
    
    # Add chunks
    paper.chunks = [
        Chunk(text="We propose a novel camera-space rotation augmentation method.", page_start=1),
        Chunk(text="Our method achieves state-of-the-art mAP on KITTI dataset.", page_start=5),
        Chunk(text="Irrelevant text about something else.", page_start=2)
    ]
    
    # Add high-level facets to paper
    paper.high_level_facets = {
        "problem_facets": ["monocular 3D detection"],
        "mechanism_facets": ["camera-space rotation augmentation"],
        "setting_facets": [],
        "assumption_facets": []
    }
    
    state.papers["test_id"] = paper
    
    # 2. Setup Matches
    matches = [
        PriorArtMatch(
            paper_id="test_id",
            coverage_level=3,
            covered_facets=["F1"],
            notes="High overlap test.",
            structural_score=2,
            structural_overlap=["Datasets: kitti", "Metrics: map"],
            high_level_facet_score=2,
            high_level_facet_matches={
                "problem_facets": ["monocular 3d detection"],
                "mechanism_facets": ["camera-space rotation augmentation"]
            }
        )
    ]
    
    # 3. Setup Idea Features
    idea_features = {
        "datasets": {"kitti", "nuscenes"},
        "metrics": {"map", "rotation error"},
        "method_families": {"data augmentation"}
    }
    
    # 4. Setup High-level Facets
    idea_high_level_facets = {
        "problem_facets": {"monocular 3d detection"},
        "setting_facets": {"camera-space coordinate system"},
        "mechanism_facets": {"camera-space rotation augmentation"},
        "assumption_facets": {"calibrated pinhole camera"}
    }
    
    # 5. Render
    report = render_prior_art_report(
        idea_description="Test idea about camera-space rotation augmentation",
        facets=["F1: Camera-space rotation"],
        scored_papers=matches,
        state=state,
        idea_features=idea_features,
        idea_high_level_facets=idea_high_level_facets,
    )
    
    print("\n" + "="*60)
    print("UNIT TEST REPORT OUTPUT:")
    print(report)
    print("="*60 + "\n")
    
    # Verify evidence snippets
    assert "**Evidence snippets**" in report, "Missing evidence snippets section"
    assert "camera-space rotation augmentation" in report, "Missing expected snippet content"
    assert "mAP on KITTI" in report, "Missing expected dataset/metric snippet"
    
    # Verify structural coverage (low-level)
    assert "Structural Coverage of Your Idea" in report, "Missing structural coverage section"
    assert "kitti" in report.lower(), "Missing kitti in structural coverage"
    
    # Verify high-level structural coverage
    assert "High-level Structural Coverage" in report, "Missing high-level coverage section"
    assert "monocular 3d detection" in report.lower(), "Missing problem facet"
    
    # Verify high-level facets displayed in paper details
    assert "problem_facets" in report or "Problem Facets" in report, "Missing high-level facet display"
    
    logger.info("✅ All unit test assertions passed!")


if __name__ == "__main__":
    # Run integration test
    test_prior_art_check()
    
    # Run unit test
    test_render_report_with_chunks()
