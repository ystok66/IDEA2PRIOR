"""
Evolution Mapper Tool for smolagents
Generates technology evolution maps based on citation networks
"""

import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from smolagents import Tool
from loguru import logger

from .semantic_scholar_tool import SemanticScholarTool
from .database_search import DatabaseSearch


class OrderedMembershipList(list):
    """List order/serialization with constant-time membership for graph IDs.

    Mutating the public list through normal list methods keeps the index coherent.
    The scientific traversal still iterates the original ordered list.
    """
    def __init__(self, values=()):
        super().__init__(values)
        self._members = set(self)

    def __contains__(self, value):
        try:
            return value in self._members
        except TypeError:
            return super().__contains__(value)

    def append(self, value):
        self._members.add(value)
        super().append(value)

    def extend(self, values):
        for value in list(values):
            self.append(value)

    def insert(self, index, value):
        super().insert(index, value)
        self._members.add(value)

    def __setitem__(self, index, value):
        super().__setitem__(index, value)
        self._members = set(self)

    def __delitem__(self, index):
        super().__delitem__(index)
        self._members = set(self)

    def pop(self, index=-1):
        value = super().pop(index)
        self._members = set(self)
        return value

    def remove(self, value):
        super().remove(value)
        self._members = set(self)

    def clear(self):
        super().clear()
        self._members.clear()

    def __iadd__(self, values):
        self.extend(values)
        return self

    def __imul__(self, count):
        super().__imul__(count)
        self._members = set(self)
        return self


@dataclass
class PaperNode:
    """Paper node data structure"""
    paper_id: str
    title: str
    year: Optional[int]
    citation_count: int = 0
    references: List[str] = field(default_factory=OrderedMembershipList)  # IDs of papers referenced by this paper
    citations: List[str] = field(default_factory=OrderedMembershipList)  # IDs of papers that cite this paper
    authors: List[str] = field(default_factory=list)
    venue: str = ""
    abstract: str = ""


class CitationNetwork:
    """Citation network data structure"""
    
    def __init__(self):
        self.papers: Dict[str, PaperNode] = {}
        self.edges: List[Tuple[str, str]] = OrderedMembershipList()  # ordered directed edges
    
    def add_paper(self, paper_data: Dict[str, Any]) -> None:
        """Add paper node"""
        paper_id = paper_data.get("paperId") or paper_data.get("id", "")
        if not paper_id:
            return
        
        if paper_id not in self.papers:
            self.papers[paper_id] = PaperNode(
                paper_id=paper_id,
                title=paper_data.get("title", "Unknown"),
                year=paper_data.get("year"),
                citation_count=paper_data.get("citationCount", 0),
                authors=[a.get("name", "") for a in paper_data.get("authors", [])],
                venue=paper_data.get("venue", ""),
                abstract=paper_data.get("abstract", "")[:200] if paper_data.get("abstract") else ""
            )
    
    def add_citation(self, from_paper_id: str, to_paper_id: str) -> None:
        """Add citation edge: from_paper_id cites to_paper_id"""
        if from_paper_id in self.papers and to_paper_id in self.papers:
            if to_paper_id not in self.papers[from_paper_id].references:
                self.papers[from_paper_id].references.append(to_paper_id)
            if from_paper_id not in self.papers[to_paper_id].citations:
                self.papers[to_paper_id].citations.append(from_paper_id)
            if (from_paper_id, to_paper_id) not in self.edges:
                self.edges.append((from_paper_id, to_paper_id))
    
    def get_key_papers(self, min_citations: int = 10) -> List[PaperNode]:
        """Get key papers (high citation count)"""
        key_papers = [
            paper for paper in self.papers.values()
            if paper.citation_count >= min_citations
        ]
        key_papers.sort(key=lambda p: p.citation_count, reverse=True)
        return key_papers
    
    def get_timeline(self, start_year: Optional[int] = None, end_year: Optional[int] = None) -> Dict[int, List[PaperNode]]:
        """Organize papers by timeline"""
        timeline = defaultdict(list)
        for paper in self.papers.values():
            if paper.year is None:
                continue
            if start_year and paper.year < start_year:
                continue
            if end_year and paper.year > end_year:
                continue
            timeline[paper.year].append(paper)
        
        # Sort by year and citation count
        for year in timeline:
            timeline[year].sort(key=lambda p: p.citation_count, reverse=True)
        
        return dict(timeline)
    
    def get_evolution_paths(self, start_paper_id: str, max_depth: int = 5) -> List[List[str]]:
        """Find evolution paths starting from the initial paper"""
        paths = []
        visited = set()
        
        def dfs(current_id: str, path: List[str], depth: int):
            if depth > max_depth or current_id in visited or current_id not in self.papers:
                if len(path) > 1:
                    paths.append(path.copy())
                return
            
            visited.add(current_id)
            path.append(current_id)
            
            # Find subsequent papers that cite this paper (sorted by year and citation count)
            current_paper = self.papers[current_id]
            next_papers = []
            for cit_id in current_paper.citations:
                if cit_id in self.papers:
                    next_paper = self.papers[cit_id]
                    if next_paper.year and current_paper.year:
                        if next_paper.year > current_paper.year:  # Only consider subsequent papers
                            next_papers.append(next_paper)
            
            next_papers.sort(key=lambda p: (p.year or 0, -p.citation_count))
            
            if not next_papers:
                # Reached path endpoint
                if len(path) > 1:
                    paths.append(path.copy())
            else:
                for next_paper in next_papers[:3]:  # Maximum 3 branches
                    dfs(next_paper.paper_id, path, depth + 1)
            
            path.pop()
            visited.remove(current_id)
        
        dfs(start_paper_id, [], 0)
        return paths


class EvolutionMapper(Tool):
    """
    Tool for generating technology evolution maps based on citation networks.
    
    This tool discovers papers related to a topic, builds citation networks,
    analyzes evolution paths, and generates Mermaid/Graphviz diagrams.
    """
    
    name = "evolution_mapper"
    description = (
        "Generate technology evolution maps for a research topic. "
        "Takes a topic/keywords as input, discovers related papers, builds citation networks, "
        "analyzes evolution paths, and generates Mermaid/Graphviz visualization diagrams. "
        "Useful for understanding how a technology or research area has evolved over time."
    )
    inputs = {
        "topic": {
            "type": "string",
            "description": "Research topic or keywords (e.g., 'Vision Transformer', 'BERT variants')",
        },
        "max_papers": {
            "type": "integer",
            "description": "Maximum number of papers to include (default: 20)",
            "nullable": True,
        },
        "start_year": {
            "type": "integer",
            "description": "Start year for filtering papers (optional)",
            "nullable": True,
        },
        "end_year": {
            "type": "integer",
            "description": "End year for filtering papers (optional)",
            "nullable": True,
        },
        "format": {
            "type": "string",
            "description": "Output format: 'mermaid' (default) or 'graphviz'",
            "nullable": True,
        },
    }
    output_type = "string"
    
    def __init__(
        self,
        semantic_tool: Optional[SemanticScholarTool] = None,
        database_search: Optional[DatabaseSearch] = None,
        output_dir: Optional[Path] = None,
    ):
        """
        Initialize Evolution Mapper tool
        
        Args:
            semantic_tool: SemanticScholarTool instance for API access
            database_search: DatabaseSearch instance for local database search
            output_dir: Directory for caching results
        """
        super().__init__()
        self.semantic_tool = semantic_tool
        self.database_search = database_search
        self.output_dir = Path(output_dir) if output_dir else None
        self.network_cache: Dict[str, Dict[str, Any]] = {}
        self.errors = []
        self.last_tool_status = "NOT_RUN"
    
    def setup(self):
        """Setup method called before first use"""
        logger.info("🔧 Setting up Evolution Mapper tool...")
        
        # Initialize tools if not provided
        if self.semantic_tool is None:
            import os
            api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
            self.semantic_tool = SemanticScholarTool(api_key=api_key)
            self.semantic_tool.setup()
        
        if self.database_search is None:
            self.database_search = DatabaseSearch()
            self.database_search.setup()
        
        self.is_initialized = True
    
    def _discover_papers(
        self,
        topic: str,
        max_papers: int = 20,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Discover papers related to the topic
        
        Args:
            topic: Research topic or keywords
            max_papers: Maximum number of papers
            start_year: Start year filter
            end_year: End year filter
        
        Returns:
            List of paper dictionaries with paperId, title, year, etc.
        """
        logger.info(f"🔍 Discovering papers for topic: '{topic}'")
        
        all_papers = []
        paper_ids_seen = set()
        
        # 1. Search in local database
        if self.database_search:
            try:
                # Try to search in common conferences
                for conference in ["cvpr", "icml", "iclr", "nips"]:
                    for year in range(2020, 2025):
                        try:
                            result = self.database_search.forward(
                                conference=conference,
                                year=year,
                                status="all",
                                keywords=topic
                            )
                            # Parse result to extract paper info (simplified)
                            # In practice, DatabaseSearch returns statistics, not paper list
                            # We'll rely more on Semantic Scholar
                        except Exception as e:
                            from paperarena.diagnostics import safe_error
                            self.errors.append(safe_error(e, "evolutionmapper"))
                            logger.debug(f"Database search failed for {conference} {year}: {e}")
                            continue
            except Exception as e:
                from paperarena.diagnostics import safe_error
                self.errors.append(safe_error(e, "evolutionmapper"))
                logger.warning(f"⚠️ Database search failed: {e}")
        
        # 2. Search Semantic Scholar
        if self.semantic_tool:
            try:
                papers = self.semantic_tool.search_papers(
                    query=topic,
                    limit=min(max_papers, 50),
                    fields="paperId,title,abstract,year,authors,venue,citationCount,referenceCount"
                )
                
                for paper in papers:
                    paper_id = paper.get("paperId")
                    if not paper_id or paper_id in paper_ids_seen:
                        continue
                    
                    # Apply year filter
                    year = paper.get("year")
                    if start_year and year and year < start_year:
                        continue
                    if end_year and year and year > end_year:
                        continue
                    
                    paper_ids_seen.add(paper_id)
                    all_papers.append(paper)
                    
                    if len(all_papers) >= max_papers:
                        break
                
                logger.info(f"✅ Discovered {len(all_papers)} papers from Semantic Scholar")
            except Exception as e:
                from paperarena.diagnostics import safe_error
                self.errors.append(safe_error(e, "evolutionmapper"))
                logger.error(f"❌ Semantic Scholar search failed: {e}")
        
        # Sort by citation count and year
        all_papers.sort(key=lambda p: (
            p.get("year") or 0,
            -p.get("citationCount", 0)
        ))
        
        return all_papers[:max_papers]
    
    def _build_citation_network(self, papers: List[Dict[str, Any]]) -> CitationNetwork:
        """
        Build citation network from paper list
        
        Args:
            papers: List of paper dictionaries
        
        Returns:
            CitationNetwork object
        """
        logger.info(f"🔗 Building citation network for {len(papers)} papers...")
        
        network = CitationNetwork()
        
        # Add all papers to network
        for paper in papers:
            network.add_paper(paper)
        
        # Build citation relationships
        if self.semantic_tool:
            for i, paper in enumerate(papers, 1):
                paper_id = paper.get("paperId")
                if not paper_id:
                    continue
                
                logger.info(f"[{i}/{len(papers)}] Building network for: {paper.get('title', 'Unknown')[:50]}...")
                
                try:
                    # Get citation network
                    citation_data = self.semantic_tool.get_citation_network(paper_id)
                    
                    if "error" in citation_data:
                        logger.warning(f"⚠️ Failed to get citation network for {paper_id}")
                        continue
                    
                    # Add references (papers this paper cites)
                    for ref in citation_data.get("references", []):
                        ref_id = ref.get("paperId")
                        if ref_id:
                            # Add reference paper to network if not already there
                            ref_paper = {
                                "paperId": ref_id,
                                "title": ref.get("title", "Unknown"),
                                "year": ref.get("year"),
                                "citationCount": 0,
                            }
                            network.add_paper(ref_paper)
                            network.add_citation(paper_id, ref_id)
                    
                    # Add citations (papers that cite this paper)
                    for cit in citation_data.get("citations", []):
                        cit_id = cit.get("paperId")
                        if cit_id:
                            # Add citing paper to network if not already there
                            cit_paper = {
                                "paperId": cit_id,
                                "title": cit.get("title", "Unknown"),
                                "year": cit.get("year"),
                                "citationCount": 0,
                            }
                            network.add_paper(cit_paper)
                            network.add_citation(cit_id, paper_id)
                    
                    # Rate limiting
                    time.sleep(self.semantic_tool.rate_limit_delay)
                    
                except Exception as e:
                    from paperarena.diagnostics import safe_error
                    self.errors.append(safe_error(e, "evolutionmapper"))
                    logger.warning(f"⚠️ Failed to build network for {paper_id}: {e}")
                    continue
        
        logger.info(f"✅ Citation network built: {len(network.papers)} papers, {len(network.edges)} edges")
        return network
    
    def _analyze_evolution_paths(self, network: CitationNetwork) -> Dict[str, Any]:
        """
        Analyze evolution paths in the citation network
        
        Args:
            network: CitationNetwork object
        
        Returns:
            Dictionary with key papers, paths, and branches
        """
        logger.info("📊 Analyzing evolution paths...")
        
        # Find key papers (high citation count)
        key_papers = network.get_key_papers(min_citations=10)
        
        # Find evolution paths starting from earliest papers
        timeline = network.get_timeline()
        if not timeline:
            return {
                "key_papers": [],
                "paths": [],
                "branches": [],
            }
        
        earliest_year = min(timeline.keys())
        earliest_papers = timeline[earliest_year]
        
        # Sort by citation count to find most influential starting papers
        earliest_papers.sort(key=lambda p: p.citation_count, reverse=True)
        
        all_paths = []
        for start_paper in earliest_papers[:3]:  # Start from top 3 earliest papers
            paths = network.get_evolution_paths(start_paper.paper_id, max_depth=5)
            all_paths.extend(paths)
        
        # Identify branches (papers with multiple citations)
        branches = []
        for paper_id, paper in network.papers.items():
            if len(paper.citations) > 2:  # Cited by multiple papers
                branches.append({
                    "paper_id": paper_id,
                    "title": paper.title,
                    "year": paper.year,
                    "cited_by": len(paper.citations),
                })
        
        branches.sort(key=lambda b: b["cited_by"], reverse=True)
        
        return {
            "key_papers": [
                {
                    "paper_id": p.paper_id,
                    "title": p.title,
                    "year": p.year,
                    "citations": p.citation_count,
                }
                for p in key_papers[:10]
            ],
            "paths": all_paths[:10],  # Limit to top 10 paths
            "branches": branches[:10],
        }
    
    def _generate_mermaid_diagram(
        self,
        network: CitationNetwork,
        analysis: Dict[str, Any],
        topic: str,
    ) -> Tuple[str, Dict[str, Dict[str, Any]]]:
        """
        Generate Mermaid diagram from citation network with detailed annotations
        
        Args:
            network: CitationNetwork object
            analysis: Evolution analysis results
            topic: Research topic
        
        Returns:
            Tuple of (Mermaid diagram code, node annotations dict)
        """
        logger.info("📊 Generating Mermaid diagram with annotations...")
        
        # Flowchart (showing citation relationships)
        mermaid = f"graph TD\n"
        mermaid += f"    %% {topic} Evolution Map\n"
        mermaid += f"    %% Each node represents a research paper with detailed information\n\n"
        
        # Create node IDs (shortened for readability)
        node_map = {}
        node_annotations = {}  # Store detailed info for each node
        node_counter = 0
        
        # Get papers sorted by year
        timeline = network.get_timeline()
        all_papers_sorted = []
        for year in sorted(timeline.keys()):
            for paper in timeline[year]:
                all_papers_sorted.append(paper)
        
        # Limit to top papers for readability
        top_papers = sorted(
            all_papers_sorted,
            key=lambda p: p.citation_count,
            reverse=True
        )[:20]  # Increased to 20 for better coverage
        
        # Create nodes with detailed information
        for paper in top_papers:
            node_id = f"P{node_counter}"
            node_map[paper.paper_id] = node_id
            node_counter += 1
            
            # Create detailed annotation
            authors_str = ", ".join(paper.authors[:3]) if paper.authors else "Unknown"
            if paper.authors and len(paper.authors) > 3:
                authors_str += f" et al."
            
            venue_str = paper.venue if paper.venue else "Unknown Venue"
            year_str = str(paper.year) if paper.year else "?"
            cites_str = f"{paper.citation_count:,}" if paper.citation_count > 0 else "0"
            
            # Store full annotation
            node_annotations[node_id] = {
                "paper_id": paper.paper_id,
                "title": paper.title,
                "authors": paper.authors or [],
                "year": paper.year,
                "venue": paper.venue,
                "citation_count": paper.citation_count,
                "abstract": paper.abstract[:200] + "..." if paper.abstract and len(paper.abstract) > 200 else (paper.abstract or "No abstract available"),
            }
            
            # Create node label with key information
            # Use HTML-style formatting in Mermaid for better display
            title_short = paper.title[:50] + "..." if len(paper.title) > 50 else paper.title
            node_label = f"{title_short}<br/>({year_str})<br/>📊 {cites_str} citations<br/>📍 {venue_str}"
            
            mermaid += f'    {node_id}["{node_label}"]\n'
        
        # Add click events for tooltips (Mermaid supports this)
        mermaid += "\n    %% Node annotations (detailed information)\n"
        for node_id, annotation in node_annotations.items():
            # Add comment with full paper info
            mermaid += f"    %% {node_id}: {annotation['title']}\n"
        
        # Create edges (only between top papers)
        mermaid += "\n    %% Citation relationships\n"
        edge_count = 0
        for from_id, to_id in network.edges:
            if from_id in node_map and to_id in node_map:
                from_node = network.papers[from_id]
                to_node = network.papers[to_id]
                
                # Only show forward citations (later papers citing earlier ones)
                if from_node.year and to_node.year and from_node.year > to_node.year:
                    mermaid += f"    {node_map[from_id]} -->|cites| {node_map[to_id]}\n"
                    edge_count += 1
                    if edge_count >= 30:  # Increased edge limit
                        break
        
        return mermaid, node_annotations
    
    def _generate_report(
        self,
        topic: str,
        papers: List[Dict[str, Any]],
        network: CitationNetwork,
        analysis: Dict[str, Any],
        mermaid_diagram: str,
        node_annotations: Dict[str, Dict[str, Any]],
    ) -> str:
        """
        Generate comprehensive evolution report in Markdown format
        
        Args:
            topic: Research topic
            papers: Discovered papers
            network: Citation network
            analysis: Evolution analysis
            mermaid_diagram: Mermaid diagram code
            node_annotations: Detailed annotations for each node
        
        Returns:
            Formatted Markdown report string
        """
        report = f"# {topic} Technology Evolution Map\n\n"
        
        # Statistics section
        report += f"## 📊 Statistics\n\n"
        report += f"- **Total papers**: {len(network.papers)}\n"
        report += f"- **Citation relationships**: {len(network.edges)}\n"
        
        timeline = network.get_timeline()
        if timeline:
            report += f"- **Time span**: {min(timeline.keys())} - {max(timeline.keys())}\n"
        
        # Key papers section
        report += f"\n## 🔑 Key Papers\n\n"
        
        for i, key_paper in enumerate(analysis.get("key_papers", [])[:10], 1):
            paper_obj = network.papers.get(key_paper['paper_id'])
            if paper_obj:
                authors_str = ", ".join(paper_obj.authors[:5]) if paper_obj.authors else "Unknown"
                if paper_obj.authors and len(paper_obj.authors) > 5:
                    authors_str += f" et al. ({len(paper_obj.authors)} authors)"
                
                report += f"### {i}. {key_paper['title']}\n\n"
                report += f"- **Publication year**: {key_paper['year']}\n"
                report += f"- **Citation count**: {key_paper['citations']:,}\n"
                report += f"- **Authors**: {authors_str}\n"
                if paper_obj.venue:
                    report += f"- **Venue**: {paper_obj.venue}\n"
                if paper_obj.abstract:
                    abstract_short = paper_obj.abstract[:300] + "..." if len(paper_obj.abstract) > 300 else paper_obj.abstract
                    report += f"- **Abstract**: {abstract_short}\n"
                report += f"- **Paper ID**: `{key_paper['paper_id']}`\n\n"
        
        # Evolution paths section
        report += f"## 📈 Evolution Paths\n\n"
        
        paths = analysis.get("paths", [])
        if paths:
            for i, path in enumerate(paths[:5], 1):
                report += f"### Path {i}\n\n"
                for j, paper_id in enumerate(path, 1):
                    if paper_id in network.papers:
                        paper = network.papers[paper_id]
                        report += f"{j}. **{paper.title}** ({paper.year})\n"
                        if paper.venue:
                            report += f"   - Published: {paper.venue}\n"
                        report += f"   - Citation count: {paper.citation_count:,}\n\n"
                report += "\n"
        else:
            report += "No clear evolution paths found (may need more paper data)\n\n"
        
        # Technical branches section
        report += f"## 🌳 Technology Branches\n\n"
        
        branches = analysis.get("branches", [])
        if branches:
            for i, branch in enumerate(branches[:5], 1):
                paper_obj = network.papers.get(branch['paper_id'])
                report += f"### {i}. {branch['title']}\n\n"
                report += f"- **Publication year**: {branch['year']}\n"
                report += f"- **Cited by**: {branch['cited_by']} papers\n"
                if paper_obj and paper_obj.venue:
                    report += f"- **Venue**: {paper_obj.venue}\n"
                report += "\n"
        else:
            report += "No clear technology branches found\n\n"
        
        # Visualization section with node annotations
        report += f"## 📊 Visualization\n\n"
        report += f"### Evolution Relationship Diagram\n\n"
        report += f"```mermaid\n{mermaid_diagram}\n```\n\n"
        
        # Detailed node annotations
        report += f"### 📝 Node Details\n\n"
        report += f"The following is a detailed description of each node in the diagram:\n\n"
        
        # Sort nodes by year for better readability
        sorted_nodes = sorted(
            node_annotations.items(),
            key=lambda x: (x[1].get('year', 0), -x[1].get('citation_count', 0))
        )
        
        for node_id, annotation in sorted_nodes:
            report += f"#### {node_id}: {annotation['title']}\n\n"
            report += f"- **Node ID**: `{node_id}`\n"
            report += f"- **Paper title**: {annotation['title']}\n"
            
            if annotation.get('authors'):
                authors_str = ", ".join(annotation['authors'][:5])
                if len(annotation['authors']) > 5:
                    authors_str += f" et al. ({len(annotation['authors'])} authors total)"
                report += f"- **Authors**: {authors_str}\n"
            
            if annotation.get('year'):
                report += f"- **Publication year**: {annotation['year']}\n"
            
            if annotation.get('venue'):
                report += f"- **Venue**: {annotation['venue']}\n"
            
            report += f"- **Citation count**: {annotation.get('citation_count', 0):,}\n"
            
            if annotation.get('abstract'):
                report += f"- **Abstract**: {annotation['abstract']}\n"
            
            report += f"- **Paper ID**: `{annotation['paper_id']}`\n\n"
        
        return report
    
    def forward(
        self,
        topic: str,
        max_papers: Optional[int] = None,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        format: Optional[str] = None,
    ) -> str:
        """
        Generate evolution map for a research topic
        
        Args:
            topic: Research topic or keywords
            max_papers: Maximum number of papers (default: 20)
            start_year: Start year filter
            end_year: End year filter
            format: Output format ('mermaid' or 'graphviz', default: 'mermaid')
        
        Returns:
            Formatted evolution report with Mermaid diagram
        """
        self.last_tool_status = "RUNNING"
        try:
            max_papers = max_papers or 10  # Default to 10 to speed up testing
            format = format or "mermaid"
            
            logger.info(f"🗺️ Generating evolution map for: '{topic}'")
            
            # Step 1: Discover papers
            papers = self._discover_papers(topic, max_papers, start_year, end_year)
            
            if not papers:
                self.last_tool_status = "FAILED" if self.errors or getattr(self.semantic_tool, "errors", []) else "EMPTY"
                return f"❌ No papers found for topic: '{topic}'"
            
            # Step 2: Build citation network
            network = self._build_citation_network(papers)
            
            if len(network.papers) < 2:
                self.last_tool_status = "PARTIAL"
                return f"⚠️ Insufficient papers to build evolution map. Found {len(network.papers)} papers."
            
            # Step 3: Analyze evolution paths
            analysis = self._analyze_evolution_paths(network)
            
            # Step 4: Generate visualization with annotations
            if format == "mermaid":
                mermaid_diagram, node_annotations = self._generate_mermaid_diagram(network, analysis, topic)
            else:
                mermaid_diagram, node_annotations = self._generate_mermaid_diagram(network, analysis, topic)
            
            # Step 5: Generate report
            report = self._generate_report(topic, papers, network, analysis, mermaid_diagram, node_annotations)
            self.last_network = {"papers": {key: vars(value) for key, value in network.papers.items()},
                                 "citations": list(network.edges), "analysis": analysis}
            self.last_tool_status = "PARTIAL" if self.errors or getattr(self.semantic_tool, "errors", []) else "SUCCEEDED"
            
            logger.info("✅ Evolution map generated successfully")
            return report
        
        except Exception as e:
            from paperarena.diagnostics import safe_error
            self.errors.append(safe_error(e, "evolution_mapper"))
            self.last_tool_status = "FAILED"
            error_msg = f"❌ Evolution map generation failed: {str(e)}"
            logger.error(error_msg)
            import traceback
            logger.error(traceback.format_exc())
            return f"Error: {str(e)}"

    def interpret_execution_result(self, result):
        # Planner–Worker must distinguish empty/error returns from valid maps.
        return "SUCCEEDED" if self.last_tool_status == "SUCCEEDED" else "FAILED"


if __name__ == "__main__":
    # Test the Evolution Mapper tool
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    # Initialize tools
    semantic_tool = SemanticScholarTool(api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY"))
    semantic_tool.setup()
    
    database_search = DatabaseSearch()
    database_search.setup()
    
    # Create Evolution Mapper
    mapper = EvolutionMapper(
        semantic_tool=semantic_tool,
        database_search=database_search,
    )
    mapper.setup()
    
    # Test evolution map generation
    print("=" * 70)
    print("Testing Evolution Mapper")
    print("=" * 70)
    
    result = mapper.forward(
        topic="Vision Transformer",
        max_papers=10,
        start_year=2020,
        end_year=2023,
    )
    
    print(result)

