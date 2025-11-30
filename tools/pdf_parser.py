import fitz  # PyMuPDF
from typing import List, Dict, Any, Optional
from smolagents import Tool
from loguru import logger

class PdfParserTool(Tool):
    name = "pdf_parser"
    description = """
    Parse a local PDF file into structured text chunks using PyMuPDF.
    Returns a dictionary with 'sections' and 'chunks' suitable for RAG.
    """
    inputs = {
        "pdf_path": {
            "type": "string",
            "description": "Absolute path to the local PDF file."
        },
        "chunk_size": {
            "type": "integer",
            "description": "Target token/character size for chunks (default 500).",
            "nullable": True
        }
    }
    output_type = "string"

    def forward(self, pdf_path: str, chunk_size: int = 500) -> str:
        try:
            if not pdf_path.endswith(".pdf"):
                return "Error: File must be a PDF."
            
            # 1. Extract text pages
            pages = self._extract_text_pymupdf(pdf_path)
            
            # 2. Create chunks
            chunks = self._simple_chunk_from_pages(pages, chunk_size)
            
            # 3. Format output
            import json
            return json.dumps(chunks)
            
        except Exception as e:
            logger.error(f"PDF parsing failed: {e}")
            return f"Error parsing PDF: {str(e)}"

    def _extract_text_pymupdf(self, pdf_path: str) -> List[Dict]:
        """Extract text from PDF using PyMuPDF."""
        doc = fitz.open(pdf_path)
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text("text")
            pages.append({"page": i + 1, "text": text})
        return pages

    def _simple_chunk_from_pages(self, pages: List[Dict], chunk_size: int) -> List[Dict]:
        """Simple chunking strategy."""
        chunks = []
        current_chunk_text = ""
        current_chunk_start_page = -1
        
        for page in pages:
            page_num = page["page"]
            text = page["text"]
            
            # Split by paragraphs (simple heuristic)
            paragraphs = text.split('\n\n')
            
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                
                if not current_chunk_text:
                    current_chunk_start_page = page_num
                
                # Check if adding this paragraph exceeds chunk size
                if len(current_chunk_text) + len(para) > chunk_size:
                    # Save current chunk
                    chunks.append({
                        "text": current_chunk_text,
                        "page_start": current_chunk_start_page,
                        "page_end": page_num
                    })
                    # Start new chunk
                    current_chunk_text = para
                    current_chunk_start_page = page_num
                else:
                    current_chunk_text += "\n\n" + para if current_chunk_text else para
        
        # Add last chunk
        if current_chunk_text:
            chunks.append({
                "text": current_chunk_text,
                "page_start": current_chunk_start_page,
                "page_end": pages[-1]["page"]
            })
            
        return chunks
