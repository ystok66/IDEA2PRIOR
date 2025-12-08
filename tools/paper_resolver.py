import os
import requests
from typing import Optional, Dict, Any
from smolagents import Tool
from loguru import logger

class PaperResolverTool(Tool):
    name = "paper_resolver"
    description = """
    Resolve a paper's PDF URL and metadata using multiple sources (arXiv, Semantic Scholar, OpenAlex, Unpaywall).
    Input can be DOI, arXiv ID, Semantic Scholar ID, or Title.
    Returns a dictionary with 'pdf_url', 'metadata', and 'source'.
    """
    inputs = {
        "doi": {"type": "string", "description": "Digital Object Identifier (DOI)", "nullable": True},
        "arxiv_id": {"type": "string", "description": "arXiv ID (e.g., '2301.12345')", "nullable": True},
        "s2_id": {"type": "string", "description": "Semantic Scholar ID", "nullable": True},
        "openalex_id": {"type": "string", "description": "OpenAlex ID", "nullable": True},
        "title": {"type": "string", "description": "Paper Title", "nullable": True},
    }
    output_type = "string"

    def __init__(self):
        super().__init__()
        self.s2_api_key = os.getenv("Semantic_Scholar_API_key")
        self.openalex_mailto = os.getenv("OPENALEX_MAILTO")
        self.email = os.getenv("OPENALEX_MAILTO") # Use same email for Unpaywall

    def _normalize_pdf_url(self, url: str) -> str:
        """
        Normalize known HTML URLs to direct PDF URLs.
        
        Handles:
        - CVF (openaccess.thecvf.com): .html -> .pdf
        """
        if not url:
            return url
        
        # CVF HTML pages can be converted to PDF
        if "openaccess.thecvf.com" in url and url.endswith(".html"):
            return url.replace(".html", ".pdf")
        
        return url

    def forward(self, doi: Optional[str] = None, arxiv_id: Optional[str] = None, 
                s2_id: Optional[str] = None, openalex_id: Optional[str] = None, 
                title: Optional[str] = None) -> str:
        
        result = self.resolve_pdf_and_metadata(doi, arxiv_id, s2_id, openalex_id, title)
        import json
        return json.dumps(result)  # Return proper JSON string, not str(dict)

    def resolve_pdf_and_metadata(self, doi=None, arxiv_id=None, s2_id=None, openalex_id=None, title=None):
        # 1. arXiv (Fastest, High Confidence)
        if arxiv_id:
            logger.info(f"Resolving via arXiv ID: {arxiv_id}")
            url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            return {"pdf_url": url, "source": "arxiv", "metadata": {"arxiv_id": arxiv_id}}

        # 2. Semantic Scholar (Rich Metadata + Links)
        logger.info("Checking Semantic Scholar...")
        paper = self._lookup_semantic_scholar(doi, s2_id, arxiv_id, title)
        if paper:
            # Check for OA PDF
            if paper.get("openAccessPdf"):
                pdf_url = self._normalize_pdf_url(paper["openAccessPdf"]["url"])
                logger.info(f"[PaperResolver] Selected PDF from S2 OA: {pdf_url[:80]}...")
                return {
                    "pdf_url": pdf_url,
                    "metadata": self._extract_meta_from_s2(paper),
                    "source": "semanticscholar",
                }
            
            # Update IDs from S2 result for further searches
            external_ids = paper.get("externalIds", {})
            doi = doi or external_ids.get("DOI")
            arxiv_id = arxiv_id or external_ids.get("ArXiv")
            
            # If we found an arXiv ID now, return it
            if arxiv_id:
                return {
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "metadata": self._extract_meta_from_s2(paper),
                    "source": "arxiv_via_s2"
                }

        # 3. OpenAlex (Good for OA)
        if doi or openalex_id or title:
            logger.info("Checking OpenAlex...")
            work = self._lookup_openalex(doi, openalex_id, title)
            if work:
                pdf_url = self._normalize_pdf_url(self._openalex_pdf_url(work))
                if pdf_url:
                    logger.info(f"[PaperResolver] Selected PDF from OpenAlex: {pdf_url[:80]}...")
                    return {
                        "pdf_url": pdf_url,
                        "metadata": self._extract_meta_from_openalex(work),
                        "source": "openalex",
                    }
                # Update DOI if found
                doi = doi or work.get("doi", "").replace("https://doi.org/", "")

        # 4. Unpaywall (Fallback for DOI)
        if doi:
            logger.info("Checking Unpaywall...")
            info = self._lookup_unpaywall(doi)
            if info:
                pdf_url = self._normalize_pdf_url(self._unpaywall_pdf_url(info))
                if pdf_url:
                    logger.info(f"[PaperResolver] Selected PDF from Unpaywall: {pdf_url[:80]}...")
                    return {
                        "pdf_url": pdf_url,
                        "metadata": self._extract_meta_from_unpaywall(info),
                        "source": "unpaywall",
                    }

        # 5. Failed to find PDF
        logger.warning("No PDF found.")
        return {
            "pdf_url": None,
            "metadata": {},
            "source": "html_only",
        }

    def _lookup_semantic_scholar(self, doi, s2_id, arxiv_id, title):
        base_url = "https://api.semanticscholar.org/graph/v1/paper"
        params = {"fields": "title,year,venue,externalIds,openAccessPdf,authors,abstract"}
        headers = {"x-api-key": self.s2_api_key} if self.s2_api_key else {}

        try:
            if s2_id:
                url = f"{base_url}/{s2_id}"
            elif doi:
                url = f"{base_url}/DOI:{doi}"
            elif arxiv_id:
                url = f"{base_url}/ARXIV:{arxiv_id}"
            elif title:
                # Search by title
                search_url = f"{base_url}/search"
                search_params = {**params, "query": title, "limit": 1}
                resp = requests.get(search_url, params=search_params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if data.get("data"):
                    return data["data"][0]
                return None
            else:
                return None

            resp = requests.get(url, params=params, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Semantic Scholar lookup failed: {e}")
            return None

    def _lookup_openalex(self, doi, openalex_id, title):
        base_url = "https://api.openalex.org/works"
        params = {}
        if self.openalex_mailto:
            params["mailto"] = self.openalex_mailto

        try:
            if openalex_id:
                url = f"{base_url}/{openalex_id}"
                resp = requests.get(url, params=params)
            elif doi:
                url = f"{base_url}/doi:{doi}"
                resp = requests.get(url, params=params)
            elif title:
                params["search"] = title
                params["per_page"] = 1
                resp = requests.get(base_url, params=params)
                data = resp.json()
                if data.get("results"):
                    return data["results"][0]
                return None
            else:
                return None

            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"OpenAlex lookup failed: {e}")
            return None

    def _lookup_unpaywall(self, doi):
        if not self.email:
            return None
        url = f"https://api.unpaywall.org/v2/{doi}"
        try:
            resp = requests.get(url, params={"email": self.email})
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Unpaywall lookup failed: {e}")
            return None

    def _openalex_pdf_url(self, work):
        best_oa = work.get("best_oa_location") or {}
        return best_oa.get("pdf_url")

    def _unpaywall_pdf_url(self, info):
        best_oa = info.get("best_oa_location") or {}
        return best_oa.get("url_for_pdf")

    def _extract_meta_from_s2(self, paper):
        return {
            "title": paper.get("title"),
            "year": paper.get("year"),
            "venue": paper.get("venue"),
            "authors": [a["name"] for a in paper.get("authors", [])],
            "abstract": paper.get("abstract")
        }

    def _extract_meta_from_openalex(self, work):
        return {
            "title": work.get("title"),
            "year": work.get("publication_year"),
            "venue": work.get("host_venue", {}).get("display_name"),
            "authors": [a.get("author", {}).get("display_name") for a in work.get("authorships", [])],
        }

    def _extract_meta_from_unpaywall(self, info):
        return {
            "title": info.get("title"),
            "year": info.get("year"),
            "venue": info.get("genre"), # Unpaywall structure varies
            "authors": [a.get("family") for a in info.get("z_authors", [])]
        }
