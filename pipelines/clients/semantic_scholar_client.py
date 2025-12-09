"""
Thin wrapper around the Semantic Scholar Academic Graph API.

Responsible for:
- Fetching paper metadata (title, year, abstract, venue, citationCount, etc.)
- Fetching references and citations for a given paperId.
"""

import os
from typing import Dict, List, Any, Optional
import requests
from loguru import logger

from pipelines.rate_limiter import s2_rate_limiter


class SemanticScholarClient:
    """
    Thin wrapper around the Semantic Scholar Academic Graph API.
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("Semantic_Scholar_API_key")

    def _headers(self) -> Dict[str, str]:
        if self.api_key:
            return {"x-api-key": self.api_key}
        return {}

    def get_paper(self, paper_id: str, fields: str = "title,year,abstract,venue,citationCount,externalIds") -> Optional[Dict[str, Any]]:
        """
        Fetch metadata for a single paper.
        
        Args:
            paper_id: Paper ID (S2 ID, DOI, arXiv ID, etc.)
            fields: Comma-separated list of fields to return
            
        Returns:
            Paper metadata dict, or None if failed
        """
        url = f"{self.BASE_URL}/paper/{paper_id}"
        params = {"fields": fields}
        
        try:
            # Per-request rate limiting (sliding window, not batch + sleep)
            s2_rate_limiter.wait_for_slot()
            
            response = requests.get(url, params=params, headers=self._headers(), timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to fetch paper {paper_id}: {e}")
            return None

    def get_references(
        self, 
        paper_id: str, 
        fields: str = "paperId,title,year,abstract,venue,citationCount,externalIds",
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Call /paper/{paper_id}/references and return a list of referenced papers
        with the requested fields.
        
        Args:
            paper_id: Paper ID
            fields: Comma-separated list of fields to return for each reference
            limit: Maximum number of references to return
            
        Returns:
            List of reference paper dicts
        """
        url = f"{self.BASE_URL}/paper/{paper_id}/references"
        params = {"fields": fields, "limit": limit}
        
        try:
            # Per-request rate limiting (sliding window, not batch + sleep)
            s2_rate_limiter.wait_for_slot()
            
            response = requests.get(url, params=params, headers=self._headers(), timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # The response structure is: {"data": [{"citedPaper": {...}}, ...]}
            references = []
            for item in data.get("data", []):
                cited_paper = item.get("citedPaper")
                if cited_paper:
                    references.append(cited_paper)
                    
            logger.info(f"Fetched {len(references)} references for paper {paper_id}")
            return references
        except Exception as e:
            logger.warning(f"Failed to fetch references for {paper_id}: {e}")
            return []

    def get_citations(
        self, 
        paper_id: str, 
        fields: str = "paperId,title,year,abstract,venue,citationCount,externalIds",
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Call /paper/{paper_id}/citations and return a list of citing papers.
        
        Args:
            paper_id: Paper ID
            fields: Comma-separated list of fields to return for each citation
            limit: Maximum number of citations to return
            
        Returns:
            List of citing paper dicts
        """
        url = f"{self.BASE_URL}/paper/{paper_id}/citations"
        params = {"fields": fields, "limit": limit}
        
        try:
            # Per-request rate limiting (sliding window, not batch + sleep)
            s2_rate_limiter.wait_for_slot()
            
            response = requests.get(url, params=params, headers=self._headers(), timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # The response structure is: {"data": [{"citingPaper": {...}}, ...]}
            citations = []
            for item in data.get("data", []):
                citing_paper = item.get("citingPaper")
                if citing_paper:
                    citations.append(citing_paper)
                    
            logger.info(f"Fetched {len(citations)} citations for paper {paper_id}")
            return citations
        except Exception as e:
            logger.warning(f"Failed to fetch citations for {paper_id}: {e}")
            return []
