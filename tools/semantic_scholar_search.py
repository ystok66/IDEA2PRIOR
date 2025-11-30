import os
import requests
from typing import Optional, List, Dict, Any
from smolagents import Tool

class SemanticScholarPaperDetailTool(Tool):
    name = "semantic_scholar_detail"
    description = """
    Get detailed information about a paper from Semantic Scholar using its ID (S2ID, DOI, ArXiv ID).
    Returns abstract, TLDR (summary), citation count, and other metadata.
    """
    inputs = {
        "paper_id": {
            "type": "string",
            "description": "The paper ID. Can be S2ID, DOI (e.g., '10.1109/CVPR...'), or ArXiv ID (e.g., 'ARXIV:2301...')."
        }
    }
    output_type = "string"

    def __init__(self):
        super().__init__()
        self.base_url = os.getenv("Semantic_Scholar_URL", "https://api.semanticscholar.org/graph/v1")
        self.api_key = os.getenv("Semantic_Scholar_API_key")

    def forward(self, paper_id: str) -> str:
        url = f"{self.base_url}/paper/{paper_id}"
        params = {
            "fields": "title,year,venue,abstract,tldr,citationCount,referenceCount,authors,url,openAccessPdf"
        }
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
            
        try:
            response = requests.get(url, params=params, headers=headers)
            if response.status_code == 404:
                return f"Paper not found with ID: {paper_id}"
            response.raise_for_status()
            data = response.json()
            
            title = data.get("title")
            abstract = data.get("abstract")
            tldr = data.get("tldr", {}).get("text") if data.get("tldr") else None
            year = data.get("year")
            venue = data.get("venue")
            citations = data.get("citationCount")
            url_link = data.get("url")
            pdf_link = data.get("openAccessPdf", {}).get("url") if data.get("openAccessPdf") else None
            
            authors = [a.get("name", "") for a in data.get("authors", [])]
            external_ids = data.get("externalIds", {})

            result_dict = {
                "title": title,
                "year": year,
                "venue": venue,
                "authors": authors,
                "citation_count": citations,
                "url": url_link,
                "pdf_url": pdf_link,
                "tldr": tldr,
                "abstract": abstract,
                "external_ids": external_ids,
                "s2_id": data.get("paperId")
            }
            
            import json
            return json.dumps(result_dict, indent=2)
            
        except Exception as e:
            return f"Error querying Semantic Scholar: {str(e)}"

class SemanticScholarCitationTool(Tool):
    name = "semantic_scholar_citations"
    description = """
    Get papers that cite the given paper (forward citations) or references (backward citations).
    """
    inputs = {
        "paper_id": {
            "type": "string",
            "description": "The paper ID (S2ID, DOI, ArXiv ID)."
        },
        "type": {
            "type": "string",
            "description": "Either 'citations' (papers citing this one) or 'references' (papers cited by this one). Default 'citations'.",
            "nullable": True
        },
        "limit": {
            "type": "integer",
            "description": "Max results to return (default 10).",
            "nullable": True
        }
    }
    output_type = "string"

    def __init__(self):
        super().__init__()
        self.base_url = os.getenv("Semantic_Scholar_URL", "https://api.semanticscholar.org/graph/v1")
        self.api_key = os.getenv("Semantic_Scholar_API_key")

    def forward(self, paper_id: str, type: str = "citations", limit: int = 10) -> str:
        if type not in ["citations", "references"]:
            return "Error: type must be 'citations' or 'references'"
            
        url = f"{self.base_url}/paper/{paper_id}/{type}"
        params = {
            "limit": limit,
            "fields": "title,year,venue,citationCount"
        }
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
            
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            results = []
            key = "citingPaper" if type == "citations" else "citedPaper"
            
            for item in data.get("data", []):
                paper = item.get(key, {})
                if not paper: continue
                
                results.append(
                    f"- {paper.get('title')} ({paper.get('year')}, {paper.get('venue')}) - Citations: {paper.get('citationCount')}"
                )
                
            if not results:
                return f"No {type} found."
                
            return "\n".join(results)
            
        except Exception as e:
            return f"Error querying Semantic Scholar: {str(e)}"
