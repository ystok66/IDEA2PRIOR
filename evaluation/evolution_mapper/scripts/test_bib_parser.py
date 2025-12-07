"""
Quick test script for BIB parser
"""

import sys
from pathlib import Path

# Add script directory to path
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from bib_parser import BibParser

def test_all_bib_files():
    """Test parsing all BIB files"""
    ground_truth_dir = script_dir.parent / "ground_truth"
    
    bib_files = [
        "ConnectedPapers-for-Attention-is-All-you-Need.bib",
        "ConnectedPapers-for-Generative-Adversarial-Nets.bib",
        "ConnectedPapers-for-ImageNet-classification-with-deep-convolutional-neural-networks.bib"
    ]
    
    for bib_file in bib_files:
        bib_path = ground_truth_dir / bib_file
        print(f"\n{'='*70}")
        print(f"Testing: {bib_file}")
        print(f"{'='*70}")
        
        parser = BibParser(bib_path)
        papers = parser.parse()
        data = parser.to_dict()
        
        print(f"✅ Parsed {len(papers)} papers")
        print(f"   Year range: {min(data['years'])} - {max(data['years'])}")
        print(f"   Key papers: {len(data['key_papers'])}")
        
        print(f"\n   First 3 papers:")
        for i, paper in enumerate(papers[:3], 1):
            print(f"   {i}. {paper.title} ({paper.year})")

if __name__ == "__main__":
    test_all_bib_files()

