"""
BIB file parser for Connected Papers ground truth data
"""

import re
from typing import Dict, List, Any, Optional
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class BibPaper:
    """BIB paper entry"""
    paper_id: str
    title: str
    year: Optional[int] = None
    authors: List[str] = field(default_factory=list)
    abstract: str = ""
    url: str = ""
    arxivid: str = ""
    doi: str = ""
    journal: str = ""
    venue: str = ""
    volume: str = ""
    pages: str = ""


class BibParser:
    """Parser for BIB files from Connected Papers"""
    
    def __init__(self, bib_file: Path):
        self.bib_file = bib_file
        self.papers: List[BibPaper] = []
    
    def parse(self) -> List[BibPaper]:
        """Parse BIB file and return list of papers"""
        with open(self.bib_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Split by @article entries
        entries = re.split(r'@article\{', content)
        
        for entry in entries[1:]:  # Skip first empty entry
            paper = self._parse_entry(entry)
            if paper:
                self.papers.append(paper)
        
        return self.papers
    
    def _parse_entry(self, entry: str) -> Optional[BibPaper]:
        """Parse a single BIB entry"""
        # Extract paper ID (first line)
        lines = entry.strip().split('\n')
        if not lines:
            return None
        
        # Get paper ID from first line: {paper_id,
        first_line = lines[0].strip()
        paper_id_match = re.match(r'^([^,]+),', first_line)
        if not paper_id_match:
            return None
        
        paper_id = paper_id_match.group(1).strip()
        
        # Parse fields
        title = ""
        year = None
        authors = []
        abstract = ""
        url = ""
        arxivid = ""
        doi = ""
        journal = ""
        venue = ""
        volume = ""
        pages = ""
        
        # Extract fields using regex
        title_match = re.search(r'title\s*=\s*\{([^\}]+)\}', entry, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
        
        year_match = re.search(r'year\s*=\s*\{?(\d{4})\}?', entry, re.IGNORECASE)
        if year_match:
            year = int(year_match.group(1))
        
        author_match = re.search(r'author\s*=\s*\{([^\}]+)\}', entry, re.IGNORECASE)
        if author_match:
            authors_str = author_match.group(1)
            # Split by 'and'
            authors = [a.strip() for a in re.split(r'\s+and\s+', authors_str, flags=re.IGNORECASE)]
        
        abstract_match = re.search(r'abstract\s*=\s*\{([^\}]+)\}', entry, re.IGNORECASE | re.DOTALL)
        if abstract_match:
            abstract = abstract_match.group(1).strip()
        
        url_match = re.search(r'url\s*=\s*\{([^\}]+)\}', entry, re.IGNORECASE)
        if url_match:
            url = url_match.group(1).strip()
        
        arxivid_match = re.search(r'arxivid\s*=\s*\{?([^\},]+)\}?', entry, re.IGNORECASE)
        if arxivid_match:
            arxivid = arxivid_match.group(1).strip()
        
        doi_match = re.search(r'doi\s*=\s*\{?([^\},]+)\}?', entry, re.IGNORECASE)
        if doi_match:
            doi = doi_match.group(1).strip()
        
        journal_match = re.search(r'journal\s*=\s*\{([^\}]+)\}', entry, re.IGNORECASE)
        if journal_match:
            journal = journal_match.group(1).strip()
        
        return BibPaper(
            paper_id=paper_id,
            title=title,
            year=year,
            authors=authors,
            abstract=abstract,
            url=url,
            arxivid=arxivid,
            doi=doi,
            journal=journal,
            venue=venue,
            volume=volume,
            pages=pages
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert parsed papers to dictionary format"""
        return {
            "papers": [
                {
                    "paper_id": p.paper_id,
                    "title": p.title,
                    "year": p.year,
                    "authors": p.authors,
                    "abstract": p.abstract[:500] if p.abstract else "",  # Truncate for LLM
                    "url": p.url,
                    "arxivid": p.arxivid,
                    "doi": p.doi,
                    "journal": p.journal,
                }
                for p in self.papers
            ],
            "total_papers": len(self.papers),
            "years": sorted([p.year for p in self.papers if p.year]),
            "key_papers": self._identify_key_papers()
        }
    
    def _identify_key_papers(self) -> List[str]:
        """Identify key papers (highly cited or seminal works)"""
        # Simple heuristic: papers with specific keywords or early papers
        key_papers = []
        for paper in self.papers:
            title_lower = paper.title.lower()
            # Check for seminal works
            if any(keyword in title_lower for keyword in [
                "attention is all you need",
                "generative adversarial",
                "imagenet classification",
                "deep residual",
                "bert",
                "gpt",
                "transformer"
            ]):
                key_papers.append(paper.paper_id)
            # Also include early papers (first 3 years)
            elif paper.year and len([p for p in self.papers if p.year and p.year <= paper.year + 2]) <= 3:
                key_papers.append(paper.paper_id)
        
        return key_papers


if __name__ == "__main__":
    # Test parser
    import sys
    from pathlib import Path
    
    if len(sys.argv) > 1:
        bib_file = Path(sys.argv[1])
    else:
        bib_file = Path(__file__).parent.parent / "ground_truth" / "ConnectedPapers-for-Attention-is-All-you-Need.bib"
    
    parser = BibParser(bib_file)
    papers = parser.parse()
    
    print(f"Parsed {len(papers)} papers")
    print(f"\nFirst 3 papers:")
    for i, paper in enumerate(papers[:3], 1):
        print(f"\n{i}. {paper.title}")
        print(f"   Year: {paper.year}")
        print(f"   Authors: {', '.join(paper.authors[:3])}...")
        print(f"   ID: {paper.paper_id}")
    
    # Convert to dict
    data = parser.to_dict()
    print(f"\n\nTotal papers: {data['total_papers']}")
    print(f"Year range: {min(data['years'])} - {max(data['years'])}")
    print(f"Key papers: {len(data['key_papers'])}")

