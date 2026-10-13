from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Literal
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
    authors: List[str] = []
    paper_id: Optional[str] = None
    entity_id: Optional[str] = None
    document_version: Optional[str] = None
    parser_version: Optional[str] = None
    content_hash: Optional[str] = None
    aliases: List[str] = []
    source_records: List[Dict[str, Any]] = []
    field_conflicts: Dict[str, List[Any]] = {}

    # IDs from different APIs
    openalex_id: Optional[str] = None
    s2_id: Optional[str] = None      # Semantic Scholar ID
    doi: Optional[str] = None
    preprint_doi: Optional[str] = None
    external_doi: Optional[str] = None  # May be preprint; type not yet verified.
    publication_doi: Optional[str] = None
    arxiv_id: Optional[str] = None

    # Enriched metadata
    citation_count: Optional[int] = None
    citation_count_source: Optional[str] = None
    publication_date: Optional[str] = None
    publication_year_source: Optional[str] = None
    publication_dates: Dict[str, Any] = {}
    preprint_published: Optional[str] = None
    preprint_updated: Optional[str] = None
    journal_reference: Optional[str] = None
    identity_status: str = 'unverified'
    publication_status: str = 'unverified'
    metadata_agency: Optional[str] = None
    publication_type: Optional[str] = None
    preprint_metadata_dates: Dict[str, Any] = {}
    fields_of_study: List[str] = []
    tags: List[str] = []  # method family / task / modality tags
    method_families: List[str] = []  # detected method families (Transformer, CNN, YOLO, etc.)
    datasets: List[str] = []  # detected datasets (KITTI, nuScenes, etc.)
    metrics: List[str] = []  # detected metrics (mAP, F1, etc.)
    high_level_facets: Dict[str, List[str]] = {}  # problem, setting, mechanism, assumption

    # Pointers to local / remote resources (optional)
    pdf_url: Optional[str] = None
    local_pdf_path: Optional[str] = None
    pdf_origin: Optional[str] = None  # explicit_local | remote_cache
    pdf_source_binding: Dict[str, Any] = {}
    gemini_file_uri: Optional[str] = None  # for Gemini File API
    pdf_resolve_failed: bool = False  # True if PDF URL resolution already attempted and failed
    has_fulltext: bool = False  # True if fulltext was successfully parsed (PDF sections available)
    
    # Granular status tracking for PDF pipeline (Phase 2 addition)
    # - "not_attempted": Default, PDF download not yet tried
    # - "downloaded": PDF successfully downloaded  
    # - "not_found": PDF URL resolution returned no URL
    # - "error": Download or resolution failed due to network/other error
    pdf_status: Literal["not_attempted", "resolved", "downloaded", "not_found", "error"] = "not_attempted"
    # - "not_attempted": Default, sections parsing not yet tried
    # - "parsed": Sections successfully extracted from PDF
    # - "parse_error": PDF downloaded but section parsing failed
    section_status: Literal["not_attempted", "parsed", "parse_error"] = "not_attempted"
    pages_status: str = "not_attempted"
    chunks_status: str = "not_attempted"
    sections_status: str = "not_attempted"
    parsed_sections: List[Dict[str, Any]] = []
    is_survey: Optional[bool] = None
    
    # Retrieval metadata
    retrieval_source: Optional[str] = None  # e.g., "block1_main_q0", "citation_graph"
    
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
    paper_aliases: Dict[str, List[str]] = {}
    identity_conflicts: List[Dict[str, Any]] = []
    idea_spec: Dict[str, Any] = {}

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
