import mimetypes
import os
import pathlib
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import unquote, urljoin, urlparse

import requests
import serpapi

import asyncio
try:
    from crawl4ai import AsyncWebCrawler
except ImportError:
    AsyncWebCrawler = None

from smolagents import Tool, OpenAIServerModel
from rag.embeddings.openai_embedding import OpenAIEmbedding
from rag.embeddings.gemini_embedding import GeminiEmbedding
from rag.retrievers.simple_vector_retriever import SimpleVectorRetriever
from rag.retrievers.simple_bm25_retriever import SimpleBM25Retriever

class SimpleCrawler:
    '''
        @overview: a simple crawler for agent to search contents related to query, crawl pages through url.etc
        @func:

    '''
    def __init__(self,
                 serpapi_key: Optional[str] = None,
                 model: OpenAIServerModel=None,
                 reflection: bool=True,
                 roll_out: int=0,
                 search_limit: int=10,
                 serp_num: int=10,
                 topk: int=1,
                 rerank: bool=False,
                 chunk_size: int=500,
                 chunk_overlap: int=50,
                 use_db: Optional[bool] = False,
                 path: Optional[str] = None,
                 ):
        self.serpapi_key = os.getenv("SERPAPI_API_KEY") if serpapi_key is None else serpapi_key
        self.model = model
        if model is not None and reflection:
            from tools.search_reflector import SearchReflector
            self.reflector = SearchReflector(model=model)
            self.reflection = True
        else:
            self.reflection = False
        self.history = []
        self.roll_out = roll_out
        self.serp_num = serp_num
        self.search_limit = search_limit
        self.topk = topk
        self.rerank = rerank

        self.search_client = serpapi.Client(api_key=self.serpapi_key)
        
        # Initialize Retriever with Fallback Logic
        embedding_model = None
        
        # 1. Try OpenAI
        if os.getenv("OPENAI_API_KEY"):
            try:
                embedding_model = OpenAIEmbedding()
            except Exception as e:
                pass
        
        # 2. Try Gemini if OpenAI failed or key missing
        if embedding_model is None and os.getenv("GOOGLE_API_KEY"):
            try:
                embedding_model = GeminiEmbedding()
            except Exception as e:
                pass

        if embedding_model:
            self.retriever = SimpleVectorRetriever(embedding_model=embedding_model,
                                                   chunk_size=chunk_size,
                                                   chunk_overlap=chunk_overlap,
                                                   path=None if not use_db else path)
        else:
            # Fallback if no embedding model available
            self.retriever = None

        # Initialize BM25 Retriever (always available if dependencies installed)
        try:
            self.bm25_retriever = SimpleBM25Retriever(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        except Exception as e:
            print(f"Warning: Failed to initialize BM25Retriever: {e}")
            self.bm25_retriever = None

    def _search(self, query, filter_year=None):
        self.history.append((query, time.time()))

        params = {
            "engine": "google",
            "q": query,
            "num": self.serp_num
        }
        if filter_year is not None:
            params["tbs"] = f"cdr:1,cd_min:01/01/{filter_year},cd_max:12/31/{filter_year}"

        results = self.search_client.search(params)

        self.page_title = f"{query} - Search"
        if "organic_results" not in results.keys():
            raise Exception(f"No results found for query: '{query}'. Use a less specific query.")
        if len(results["organic_results"]) == 0:
            year_filter_message = f" with filter year={filter_year}" if filter_year is not None else ""
            return f"No results found for '{query}'{year_filter_message}. Try with a more general query, or remove the year filter."

        web_snippets: List[str] = list()
        idx = 0
        if "organic_results" in results:
            for page in results["organic_results"]:
                idx += 1
                date_published = ""
                if "date" in page:
                    date_published = "\nDate published: " + page["date"]

                source = ""
                if "source" in page:
                    source = "\nSource: " + page["source"]

                snippet = ""
                if "snippet" in page:
                    snippet = "\n" + page["snippet"]

                _search_result = {
                    "idx": idx,
                    "title": page["title"],
                    "date": date_published,
                    "snippet": snippet,
                    "source": source,
                    "link": page['link']
                }
                
                web_snippets.append(_search_result)

        return web_snippets

    
    def _pre_visit(self, url):
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i][0] == url:
                return f"You previously visited this page {round(time.time() - self.history[i][1])} seconds ago.\n"
        return ""


    def _to_contents(self, query:str, snippets:List):
        web_snippets = []
        idx=1
        for search_info in snippets:
            redacted_version = f"{idx}. [{search_info['title']}]({search_info['link']})" + \
                            f"{search_info['date']}{search_info['source']}\n{self._pre_visit(search_info['link'])}{search_info['snippet']}"
            redacted_version = redacted_version.replace("Your browser can't play this video.", "")
            web_snippets.append(redacted_version)
            idx+=1
        
        content = (
            f"A Google search for '{query}' found {len(web_snippets)} results:\n\n## Web Results\n"
            + "\n\n".join(web_snippets)
        )
        return content

    def search(self, query, filter_year=None):
        use_rollout = self.roll_out > 0 and self.model is not None
        if use_rollout:
            queries = self._expand_query(query)
        else:
            queries = [query]
        
        search_results = {}
        for q in queries:
            search_results[q] = self._search(q, filter_year)
        
        if use_rollout:
            aggregated_results = self.aggregate(query, search_results, intersect=False, rerank=self.rerank)
            return self._to_contents(query, aggregated_results)
        else:
            return self._to_contents(query, search_results[query])
    
    async def _crawl_page(self, url):
        if AsyncWebCrawler is None:
            return "Error: crawl4ai is not installed. Please install it or use read_type='jina_read'."
        async with AsyncWebCrawler(verbose=True) as crawler:
            result = await crawler.arun(url=url)
            return result.markdown
        
    def _read_page(self, url):
        jina_url = f'https://r.jina.ai/{url}'
        headers = {
            'Authorization': f'Bearer {os.getenv("JINA_API_KEY")}',
            'X-Engine': 'direct',
            'X-Return-Format': 'text',
            'X-Timeout': '10',
            'X-Token-Budget': '50000'
        }
        response = requests.get(jina_url, headers=headers)
        return response.text

    def _query(self, query, contents):
        # 1. Vector Search
        vector_res = ""
        if self.retriever:
            try:
                vector_res = self.retriever.retrieve(query=query, contents=contents, limit=5)
            except Exception as e:
                print(f"Vector retrieval failed: {e}")
        
        # 2. BM25 Search
        bm25_res = ""
        if self.bm25_retriever:
            try:
                bm25_res = self.bm25_retriever.retrieve(query=query, contents=contents, limit=5)
            except Exception as e:
                print(f"BM25 retrieval failed: {e}")
            
        if not vector_res and not bm25_res:
             return "No relevant content found."
             
        return f"## Vector Search Results\n{vector_res}\n\n## BM25 Search Results\n{bm25_res}"
    
    def _check_history(self, url_or_query):
        header=''
        for i in range(len(self.history) - 2, -1, -1):
            if self.history[i][0] == url_or_query:
                header += f"You previously visited this page {round(time.time() - self.history[i][1])} seconds ago.\n"
                return header
        self.history.append((url_or_query, time.time()))
        return header

    def crawl(self, url):
        return asyncio.run(self._crawl_page(url))
    
    def read_page(self, url):
        return self._read_page(url)

class CrawlerSearchTool(Tool):
    name = "web_search"
    description = "Perform a web search query (think a google search) and returns the search results."
    inputs = {"query": {"type": "string", "description": "The web search query to perform."}}
    inputs["filter_year"] = {
        "type": "string",
        "description": "[Optional parameter]: filter the search results to only include pages from a specific year. For example, '2020' will only include pages from 2020. Make sure to use this parameter if you're trying to search for articles from a specific date!",
        "nullable": True,
    }
    output_type = "string"

    def __init__(self, crawler: SimpleCrawler):
        super().__init__()
        self.crawler = crawler

    def forward(self, query: str, filter_year: Optional[int] = None) -> str:
        return self.crawler.search(query, filter_year)


class CrawlerReadTool(Tool):
    name = "read_page"
    description = "Read the content of a webpage from a URL."
    inputs = {"url": {"type": "string", "description": "The URL of the webpage to read."}}
    output_type = "string"

    def __init__(self, crawler: SimpleCrawler, read_type: str = "crawl4ai"):
        super().__init__()
        self.crawler = crawler
        self.read_type = read_type

    def forward(self, url: str) -> str:
        if self.read_type == "crawl4ai":
            return self.crawler.crawl(url)
        elif self.read_type == "jina_read":
            return self.crawler.read_page(url)
        else:
            raise ValueError(f"Invalid read_type: {self.read_type}")


class CrawlerArchiveSearchTool(Tool):
    name = "archive_search"
    description = "Search for archived versions of a URL on the Wayback Machine."
    inputs = {"url": {"type": "string", "description": "The URL to search for in the archive."}}
    output_type = "string"

    def forward(self, url: str) -> str:
        return f"Archive search for {url} is not implemented yet."


class CrawlerRAGTool(Tool):
    name = "web_rag_search"
    description = "Perform a RAG-based search on a webpage content."
    inputs = {
        "url": {"type": "string", "description": "The URL of the webpage to search."},
        "query": {"type": "string", "description": "The query to search for within the page content."}
    }
    output_type = "string"

    def __init__(self, crawler: SimpleCrawler):
        super().__init__()
        self.crawler = crawler

    def forward(self, url: str, query: str) -> str:
        # 1. Read page content
        content = self.crawler.read_page(url)
        
        # 2. Use RAG to query content
        if self.crawler.retriever is None and self.crawler.bm25_retriever is None:
            return "RAG functionality is not available (missing API keys for embeddings and BM25 not initialized)."
            
        return self.crawler._query(query, content)
