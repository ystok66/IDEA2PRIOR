
from pipelines.clients.http_transport import get as http_get
import os
import requests
from typing import Optional, List, Dict, Any
from smolagents import Tool

from pipelines.rate_limiter import openalex_rate_limiter

class OpenAlexWorksSearchTool(Tool):
    name = "openalex_works_search"
    description = """
    Search for academic works (papers) using OpenAlex API. 
    Useful for finding papers by topic, venue, year, or keywords.
    Returns a list of papers with metadata (title, authors, venue, year, cited_by_count, ids).
    """
    inputs = {
        "query": {
            "type": "string",
            "description": "Keywords to search for in the full text (title, abstract, etc.).",
            "nullable": True
        },
        "from_year": {
            "type": "integer",
            "description": "Filter for works published since this year (inclusive).",
            "nullable": True
        },
        "to_year": {
            "type": "integer",
            "description": "Filter for works published up to this year (inclusive).",
            "nullable": True
        },
        "venue": {
            "type": "string",
            "description": "Filter by venue name (e.g., 'CVPR', 'NeurIPS').",
            "nullable": True
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of results to return (default 10).",
            "nullable": True
        }
    }
    output_type = "string"

    def __init__(self):
        super().__init__()
        self.base_url = os.getenv("OPENALEX_BASE_URL", "https://api.openalex.org")
        self.mailto = os.getenv("OPENALEX_MAILTO")

    def forward(self, query: Optional[str] = None, from_year: Optional[int] = None, 
                to_year: Optional[int] = None, venue: Optional[str] = None, 
                limit: int = 10) -> str:
        
        url = f"{self.base_url}/works"
        params = {
            "per_page": limit,
            "sort": "cited_by_count:desc" # Default sort by citation
        }
        
        if self.mailto:
            params["mailto"] = self.mailto

        # Construct filters
        filters = []
        
        if from_year:
            filters.append(f"from_publication_date:{from_year}-01-01")
        if to_year:
            filters.append(f"to_publication_date:{to_year}-12-31")
            
        if filters:
            params["filter"] = ",".join(filters)
            
        # NOTE: Venue filtering via `search` parameter is INCORRECT and kills recall.
        # OpenAlex does NOT support filtering works by source/venue *name* via `search`.
        # Papers rarely mention conference names (e.g., "CVPR") in title/abstract.
        # 
        # TODO: Implement proper venue filtering:
        # 1. Call /sources API to resolve venue names → source IDs (e.g., CVPR → S114869939)
        # 2. Use filter=primary_location.source.id:S114869939|S... 
        # See: https://docs.openalex.org/api-entities/works/filter-works
        #
        # For now, we DO NOT filter by venue to preserve recall.
        
        if query:
            params["search"] = query
            
        try:
            # Per-request rate limiting (sliding window, not batch + sleep)
            
            response = http_get(url, params=params, timeout=(10, 60))
            response.raise_for_status()
            data = response.json()
            
            results = []
            for work in data.get("results", []):
                title = work.get("title")
                pub_year = work.get("publication_year")
                venue_name = ((work.get("primary_location") or {}).get("source") or {}).get("display_name") or (work.get("host_venue") or {}).get("display_name") or "Unknown Venue"
                cited_by = work.get("cited_by_count")
                work_id = work.get("id")
                doi = work.get("doi")
                
                # Extract abstract from inverted index format
                abstract = None
                abstract_index = work.get("abstract_inverted_index")
                if abstract_index:
                    try:
                        # Reconstruct abstract from inverted index
                        word_positions = []
                        for word, positions in abstract_index.items():
                            for pos in positions:
                                word_positions.append((pos, word))
                        word_positions.sort(key=lambda x: x[0])
                        abstract = " ".join(w for _, w in word_positions)
                    except Exception:
                        abstract = None
                
                authors = [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])]
                
                results.append({
                    "title": title,
                    "year": pub_year,
                    "venue": venue_name,
                    "authors": authors,
                    "cited_by_count": cited_by,
                    "openalex_id": work_id,
                    "doi": doi,
                    "pdf_url": (work.get("primary_location") or {}).get("pdf_url"),
                    "abstract": abstract,  # Now includes abstract
                })
                
            if not results:
                return "[]"
                
            import json
            return json.dumps(results, indent=2)
            
        except Exception as e:
            return f"Error querying OpenAlex: {str(e)}"
