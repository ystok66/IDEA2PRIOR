
from pipelines.clients.http_transport import get as http_get
from uuid import uuid4
"""
Semantic Scholar API Tool for PaperArena
Provides access to Semantic Scholar's academic graph API for:
- Paper search and metadata retrieval
- Citation network analysis
- Related paper recommendations
- Author information
"""

import json
import time
from typing import Dict, Any, Optional, List
from pathlib import Path
import requests
from loguru import logger

from smolagents import Tool


class SemanticScholarTool(Tool):
    """
    Tool for accessing Semantic Scholar API
    
    Features:
    - Search papers by query
    - Get paper details by ID or title
    - Retrieve citation network
    - Get related papers
    - Author information
    """

    name = "semantic_scholar_search"
    description = (
        "Search and retrieve academic papers from Semantic Scholar. "
        "Can search by query, get paper details, citation networks, and related papers. "
        "Useful for finding relevant papers, understanding citation relationships, "
        "and building evolution maps for research topics."
    )
    inputs = {
        "query": {
            "type": "string",
            "description": "Search query (paper title, keywords, or topic)",
        },
        "search_type": {
            "type": "string",
            "description": "Type of search: 'paper' (default), 'author', or 'paper_id'",
            "nullable": True,
        },
        "paper_id": {
            "type": "string",
            "description": "Semantic Scholar paper ID (e.g., '1706.03762' for arXiv ID or S2 ID)",
            "nullable": True,
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of results (default: 10, max: 100)",
            "nullable": True,
        },
        "fields": {
            "type": "string",
            "description": "Comma-separated fields to return (e.g., 'title,abstract,citations,references')",
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(
        self,
        api_key: Optional[str] = None,
        rate_limit_delay: Optional[float] = None,
        output_dir: Optional[Path] = None,
    ):
        """
        Initialize Semantic Scholar tool

        Args:
            api_key: Semantic Scholar API key (optional, but recommended for higher rate limits)
            rate_limit_delay: Delay between requests to respect rate limits (seconds)
                            If None, auto-detects based on API key presence
            output_dir: Directory to cache results
        """
        super().__init__()
        self.api_key = api_key
        self.errors = []
        # Auto-detect rate limit delay if not specified
        if rate_limit_delay is None:
            if api_key:
                # API key: 1 request per second
                self.rate_limit_delay = 1.0
            else:
                # Public API: 100 req/min (shared), use 0.6s to be safe
                self.rate_limit_delay = 0.6
        else:
            self.rate_limit_delay = rate_limit_delay
        self.base_url = "https://api.semanticscholar.org/graph/v1"
        self.output_dir = Path(output_dir) if output_dir else None
        
        # Headers for API requests
        self.headers = {
            "Accept": "application/json",
        }
        if self.api_key:
            self.headers["x-api-key"] = self.api_key

    def setup(self):
        """Setup method called before first use"""
        logger.info("🔧 Setting up Semantic Scholar tool...")
        if self.api_key:
            logger.info(f"✅ Using Semantic Scholar API key (rate limit: 1 req/sec)")
            logger.info(f"   Delay between requests: {self.rate_limit_delay:.1f}s")
        else:
            logger.warning("⚠️ No API key provided (using public rate limits: 100 req/min shared)")
            logger.info(f"   Delay between requests: {self.rate_limit_delay:.1f}s")
        self.is_initialized = True

    def _make_request(self, endpoint: str, params: Optional[Dict] = None, _remaining_retries: int = 1, _request_id=None, _attempt_ordinal=1) -> Dict[str, Any]:
        """
        Make API request with rate limiting

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            JSON response as dictionary
        """
        _request_id = _request_id or uuid4().hex
        url = f"{self.base_url}/{endpoint}"
        
        try:
            # Rate limiting
            # API key: 1 request per second (strict)
            # Public API: 100 req/min shared (more conservative)
            # Shared HTTP transport performs provider admission before each attempt.
            
            response = http_get(url, headers=self.headers, params=params, timeout=30, logical_request_id=_request_id, attempt_ordinal=_attempt_ordinal)
            response.raise_for_status()
            
            return response.json()
        
        except requests.exceptions.HTTPError as e:
            from paperarena.diagnostics import safe_error
            self.errors.append(safe_error(e, "semanticscholartool"))
            if response.status_code == 429:
                if _remaining_retries <= 0:
                    raise
                wait_time = 60 if not self.api_key else 30
                logger.warning(f"⚠️ Rate limit exceeded, waiting {wait_time} seconds...")
                time.sleep(wait_time)
                # Retry once
                return self._make_request(endpoint, params, _remaining_retries=_remaining_retries - 1, _request_id=_request_id, _attempt_ordinal=_attempt_ordinal + 1)
            elif response.status_code == 404:
                logger.warning(f"⚠️ Resource not found: {endpoint}")
                return {}
            else:
                logger.error(f"❌ HTTP error {response.status_code}: {e}")
                raise
        except Exception as e:
            from paperarena.diagnostics import safe_error
            self.errors.append(safe_error(e, "semanticscholartool"))
            logger.error(f"❌ Request failed: {e}")
            raise

    def search_papers(
        self,
        query: str,
        limit: int = 10,
        fields: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for papers

        Args:
            query: Search query
            limit: Maximum number of results
            fields: Comma-separated fields to return

        Returns:
            List of paper dictionaries
        """
        default_fields = "paperId,title,abstract,authors,year,venue,citationCount,referenceCount"
        fields = fields or default_fields
        
        params = {
            "query": query,
            "limit": min(limit, 100),  # API limit is 100
            "fields": fields,
        }
        
        logger.info(f"🔍 Searching Semantic Scholar: '{query}'")
        response = self._make_request("paper/search", params)
        
        papers = response.get("data", [])
        logger.info(f"✅ Found {len(papers)} papers")
        
        return papers

    def get_paper_details(
        self,
        paper_id: str,
        fields: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a paper

        Args:
            paper_id: Semantic Scholar paper ID (can be arXiv ID like '1706.03762' or S2 ID)
            fields: Comma-separated fields to return

        Returns:
            Paper details dictionary or None
        """
        default_fields = (
            "paperId,title,abstract,authors,year,venue,citationCount,referenceCount,"
            "citations,citations.paperId,citations.title,citations.year,"
            "references,references.paperId,references.title,references.year,"
            "isOpenAccess,openAccessPdf"
        )
        fields = fields or default_fields
        
        # Handle arXiv IDs
        if paper_id.startswith("arXiv:") or not paper_id.startswith("CorpusId:"):
            # Try to find paper by arXiv ID
            search_params = {"query": paper_id, "limit": 1}
            search_response = self._make_request("paper/search", search_params)
            if search_response.get("data"):
                paper_id = search_response["data"][0]["paperId"]
        
        logger.info(f"📄 Fetching paper details: {paper_id}")
        paper = self._make_request(f"paper/{paper_id}", {"fields": fields})
        
        if paper:
            logger.info(f"✅ Retrieved paper: {paper.get('title', 'Unknown')}")
        
        return paper

    def get_citation_network(
        self,
        paper_id: str,
        depth: int = 1,
    ) -> Dict[str, Any]:
        """
        Get citation network for a paper

        Args:
            paper_id: Paper ID
            depth: Depth of citation network (1 = direct citations/references)

        Returns:
            Citation network dictionary
        """
        paper = self.get_paper_details(
            paper_id,
            fields="paperId,title,year,citations.paperId,citations.title,citations.year,"
                   "references.paperId,references.title,references.year"
        )
        
        if not paper:
            return {"error": "Paper not found"}
        
        network = {
            "paper": {
                "id": paper.get("paperId"),
                "title": paper.get("title"),
                "year": paper.get("year"),
            },
            "citations": paper.get("citations", []),  # Papers that cite this
            "references": paper.get("references", []),  # Papers this cites
        }
        
        logger.info(
            f"📊 Citation network: {len(network['citations'])} citations, "
            f"{len(network['references'])} references"
        )
        
        return network

    def get_related_papers(
        self,
        paper_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get papers related to a given paper

        Args:
            paper_id: Paper ID
            limit: Maximum number of related papers

        Returns:
            List of related papers
        """
        params = {
            "limit": min(limit, 100),
            "fields": "paperId,title,abstract,year,citationCount",
        }
        
        logger.info(f"🔗 Finding papers related to: {paper_id}")
        response = self._make_request(f"paper/{paper_id}/recommendations", params)
        
        papers = response.get("data", [])
        logger.info(f"✅ Found {len(papers)} related papers")
        
        return papers

    def format_paper_list(self, papers: List[Dict[str, Any]]) -> str:
        """Format list of papers as readable string"""
        if not papers:
            return "No papers found."
        
        formatted = []
        for i, paper in enumerate(papers, 1):
            title = paper.get("title", "Unknown Title")
            year = paper.get("year", "Unknown Year")
            authors = ", ".join([a.get("name", "") for a in paper.get("authors", [])[:3]])
            if len(paper.get("authors", [])) > 3:
                authors += " et al."
            citations = paper.get("citationCount", 0)
            paper_id = paper.get("paperId", "N/A")
            
            formatted.append(
                f"{i}. {title}\n"
                f"   Authors: {authors}\n"
                f"   Year: {year} | Citations: {citations} | ID: {paper_id}"
            )
        
        return "\n\n".join(formatted)

    def forward(
        self,
        query: str,
        search_type: str = "paper",
        paper_id: Optional[str] = None,
        limit: int = 10,
        fields: Optional[str] = None,
    ) -> str:
        """
        Main forward method for tool execution

        Args:
            query: Search query or paper identifier
            search_type: Type of search ('paper', 'author', 'paper_id')
            paper_id: Direct paper ID (if provided, overrides query)
            limit: Maximum results
            fields: Fields to return

        Returns:
            Formatted search results
        """
        try:
            if paper_id or search_type == "paper_id":
                # Get paper details
                paper_id = paper_id or query
                paper = self.get_paper_details(paper_id, fields)
                
                if not paper:
                    return f"❌ Paper not found: {paper_id}"
                
                # Format paper details
                result = f"📄 Paper Details:\n"
                result += f"Title: {paper.get('title', 'N/A')}\n"
                result += f"Year: {paper.get('year', 'N/A')}\n"
                result += f"Authors: {', '.join([a.get('name', '') for a in paper.get('authors', [])])}\n"
                result += f"Venue: {paper.get('venue', 'N/A')}\n"
                result += f"Citations: {paper.get('citationCount', 0)}\n"
                result += f"References: {paper.get('referenceCount', 0)}\n"
                
                if paper.get('abstract'):
                    result += f"\nAbstract:\n{paper['abstract'][:500]}...\n"
                
                # Include citation network if available
                if paper.get('citations') or paper.get('references'):
                    result += f"\n📊 Citation Network:\n"
                    if paper.get('references'):
                        result += f"  References ({len(paper['references'])}): "
                        ref_titles = [r.get('title', 'N/A')[:50] for r in paper['references'][:5]]
                        result += ", ".join(ref_titles)
                        if len(paper['references']) > 5:
                            result += f" ... and {len(paper['references']) - 5} more\n"
                    if paper.get('citations'):
                        result += f"  Cited by ({len(paper['citations'])}): "
                        cit_titles = [c.get('title', 'N/A')[:50] for c in paper['citations'][:5]]
                        result += ", ".join(cit_titles)
                        if len(paper['citations']) > 5:
                            result += f" ... and {len(paper['citations']) - 5} more\n"
                
                return result
            
            else:
                # Search papers
                papers = self.search_papers(query, limit, fields)
                return self.format_paper_list(papers)
        
        except Exception as e:
            from paperarena.diagnostics import safe_error
            self.errors.append(safe_error(e, "semanticscholartool"))
            error_msg = f"❌ Semantic Scholar search failed: {str(e)}"
            logger.error(error_msg)
            return f"Error: {str(e)}"


if __name__ == "__main__":
    # Test the Semantic Scholar tool
    import os
    
    # Get API key from environment (optional)
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    
    tool = SemanticScholarTool(api_key=api_key)
    tool.setup()
    
    # Test search
    print("=" * 60)
    print("Test 1: Search papers")
    print("=" * 60)
    result = tool.forward("transformer attention mechanism", limit=5)
    print(result)
    
    # Test paper details (using arXiv ID)
    print("\n" + "=" * 60)
    print("Test 2: Get paper details")
    print("=" * 60)
    result = tool.forward("1706.03762", search_type="paper_id")
    print(result)

