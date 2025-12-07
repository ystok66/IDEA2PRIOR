"""
Quick test script to verify ground truth data loading
"""

import json
import sys
from pathlib import Path

# Add script directory to path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

ground_truth_file = script_dir.parent / "ground_truth" / "code_extractor_ground_truth.json"

def test_ground_truth():
    """Test loading ground truth data"""
    print("📊 Testing Ground Truth Data Loading")
    print("=" * 70)
    
    with open(ground_truth_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"✅ Loaded {len(data)} papers\n")
    
    print("📄 Paper List:")
    for i, paper in enumerate(data, 1):
        print(f"\n{i}. {paper.get('title', 'N/A')}")
        print(f"   ID: {paper.get('id', 'N/A')}")
        print(f"   Pseudocode Completeness: {paper.get('pseudocode_completeness', 'N/A')}/2")
        print(f"   Reproducibility: {paper.get('reproducibility', 'N/A')}/3")
        print(f"   Frameworks: {', '.join(paper.get('frameworks', [])) or 'None'}")
        print(f"   Dependencies: {len(paper.get('dependencies', []))} dependencies")
        print(f"   Missing Components: {len(paper.get('missing_components', []))} items")
    
    print("\n" + "=" * 70)
    print("✅ Ground Truth data loaded successfully!")

if __name__ == "__main__":
    test_ground_truth()

