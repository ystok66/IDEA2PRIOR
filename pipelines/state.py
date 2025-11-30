from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel


class Intent(BaseModel):
    """
    Parsed intent of a research task.

    The agent should convert a natural-language user query into
    this structured form once at the beginning of a pipeline.
    """
    task_type: str  # e.g. "benchmark_landscape", "prior_art_check"
    topic: str      # e.g. "3D object detection", "monocular depth estimation"
    years: Optional[Tuple[int, int]] = None  # (start_year, end_year)
    venues: List[str] = []  # e.g. ["CVPR", "ICLR", "NeurIPS"]
    extra_constraints: Dict[str, str] = {}  # e.g. {"modality": "RGB+LiDAR"}


class Chunk(BaseModel):
    """A text chunk from a parsed PDF."""
    text: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None


class Paper(BaseModel):
    """
    Canonical representation of a paper collected during exploration.

    IDs from different providers (OpenAlex, Semantic Scholar, arXiv, DOI)
    are stored together so that we can de-duplicate and enrich metadata.
    """
    # Basic bibliographic info
    title: str
    year: Optional[int] = None
    venue: Optional[str] = None
    abstract: Optional[str] = None

    # IDs from different APIs
    openalex_id: Optional[str] = None
    s2_id: Optional[str] = None      # Semantic Scholar ID
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None

    # Enriched metadata
    citation_count: Optional[int] = None
    fields_of_study: List[str] = []
    tags: List[str] = []  # method family / task / modality tags
    method_families: List[str] = []  # detected method families (Transformer, CNN, YOLO, etc.)
    datasets: List[str] = []  # detected datasets (KITTI, nuScenes, etc.)
    metrics: List[str] = []  # detected metrics (mAP, F1, etc.)
    high_level_facets: Dict[str, List[str]] = {}  # problem, setting, mechanism, assumption

    # Pointers to local / remote resources (optional)
    pdf_url: Optional[str] = None
    local_pdf_path: Optional[str] = None
    gemini_file_uri: Optional[str] = None  # for Gemini File API
    
    # Content
    chunks: List[Chunk] = []


class CitationEdge(BaseModel):
    """
    Directed edge in a local citation graph.

    source_id and target_id should typically refer to s2_id or openalex_id.
    """
    source_id: str  # citing paper
    target_id: str  # cited paper
    edge_type: str = "cites"  # "cites" or "references"


class PipelineState(BaseModel):
    """
    Shared state for a flow-driven research pipeline.

    The exploration loop and task-specific heads will read/write this object.
    """
    intent: Intent

    # Papers currently known to the system (keyed by a canonical ID)
    papers: Dict[str, Paper] = {}

    # Local citation graph over the current paper set
    citation_edges: List[CitationEdge] = []

    # Optional: dataset and repository info extracted from papers
    datasets: Dict[str, dict] = {}
    repos: Dict[str, dict] = {}

    # Bookkeeping for iterative exploration
    discovery_round: int = 0
    metadata_round: int = 0

    # Flags for downstream stages (e.g. RAG indexing status)
    chunks_indexed: bool = False
