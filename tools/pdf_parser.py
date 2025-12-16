import fitz  # PyMuPDF
from typing import List, Dict, Any, Optional
import re
from smolagents import Tool
from loguru import logger

# Import section data structures
from pipelines.sections import Section, PaperSections


# Regex pattern to detect section headings
# Matches: optional number prefix (1, 2.1, 3.2.4) + capitalized title words
HEADING_PATTERN = re.compile(
    r"^\s*(\d+(\.\d+)*\.?\s+)?([A-Z][A-Za-z0-9 ,\-&:]+)\s*$"
)

# Minimum length for a valid heading (avoid short false positives)
MIN_HEADING_LENGTH = 5
MAX_HEADING_LENGTH = 100


def infer_section_role(heading: str) -> str:
    """
    Infer the semantic role of a section based on its heading text.
    
    Role mapping:
    - "intro": Introduction
    - "related_work": Related Work / Background / Literature Review / Previous Work
    - "method": Method / Approach / Model / Architecture / Proposed / Framework
    - "experiment": Experiments / Results / Evaluation / Analysis
    - "conclusion": Conclusion / Discussion / Summary
    - "other": Everything else (Appendix, Acknowledgments, etc.)
    
    Args:
        heading: Section heading text
        
    Returns:
        Role string
    """
    h = heading.lower()
    
    if "introduction" in h:
        return "intro"
    
    if any(k in h for k in [
        "related work", "related works", "background", 
        "literature review", "previous work", "prior work"
    ]):
        return "related_work"
    
    if any(k in h for k in [
        "method", "approach", "model", "architecture", 
        "proposed", "framework", "technique", "algorithm"
    ]):
        return "method"
    
    if any(k in h for k in [
        "experiment", "experiments", "results", 
        "evaluation", "analysis", "ablation"
    ]):
        return "experiment"
    
    if any(k in h for k in [
        "conclusion", "conclusions", "discussion", 
        "summary", "future work"
    ]):
        return "conclusion"
    
    return "other"


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
        doc.close()
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

    def parse_sections(
        self,
        pdf_path: str,
        paper_id: str,
        title: str,
        year: int,
    ) -> PaperSections:
        """
        Parse a PDF into structured sections with semantic role labels.
        
        This method:
        1. Extracts text from each page
        2. Detects section headings using regex patterns
        3. Splits content into sections by heading positions
        4. Assigns semantic roles (intro, related_work, method, etc.)
        
        Args:
            pdf_path: Path to the PDF file
            paper_id: Unique identifier for the paper
            title: Paper title
            year: Publication year
            
        Returns:
            PaperSections object containing all parsed sections.
            The caller is responsible for saving to JSON if needed.
        """
        try:
            # 1. Extract pages
            pages = self._extract_text_pymupdf(pdf_path)
            
            if not pages:
                logger.warning(f"[PdfParser] No pages extracted from {pdf_path}")
                return PaperSections(
                    paper_id=paper_id,
                    title=title,
                    year=year,
                    sections=[]
                )
            
            # 2. Detect heading positions
            # Each entry: (page_idx, line_idx, heading_text, global_char_offset)
            heading_positions = []
            global_offset = 0
            
            for page_idx, page in enumerate(pages):
                page_text = page["text"]
                lines = page_text.split("\n")
                
                for line_idx, line in enumerate(lines):
                    line_stripped = line.strip()
                    
                    # Skip empty or too short/long lines
                    if len(line_stripped) < MIN_HEADING_LENGTH:
                        continue
                    if len(line_stripped) > MAX_HEADING_LENGTH:
                        continue
                    
                    # Check if it matches heading pattern
                    if HEADING_PATTERN.match(line_stripped):
                        heading_positions.append({
                            "page_idx": page_idx,
                            "line_idx": line_idx,
                            "heading": line_stripped,
                            "page_num": page["page"],
                        })
                
                global_offset += len(page_text)
            
            logger.debug(f"[PdfParser] Found {len(heading_positions)} potential headings")
            
            # 3. Build sections from heading positions
            sections: List[Section] = []
            
            for i, pos in enumerate(heading_positions):
                heading = pos["heading"]
                start_page = pos["page_num"]
                
                # Determine end position (start of next section or end of document)
                if i + 1 < len(heading_positions):
                    end_page = heading_positions[i + 1]["page_num"]
                else:
                    end_page = pages[-1]["page"]
                
                # Extract text between this heading and the next
                section_text = self._extract_section_text(
                    pages=pages,
                    start_page_idx=pos["page_idx"],
                    start_line_idx=pos["line_idx"] + 1,  # Skip heading line
                    end_page_idx=heading_positions[i + 1]["page_idx"] if i + 1 < len(heading_positions) else len(pages) - 1,
                    end_line_idx=heading_positions[i + 1]["line_idx"] if i + 1 < len(heading_positions) else None,
                )
                
                # Infer role
                role = infer_section_role(heading)
                
                section = Section(
                    role=role,
                    heading=heading,
                    text=section_text.strip(),
                    start_page=start_page,
                    end_page=end_page,
                )
                sections.append(section)
            
            # Handle case where no headings were detected
            if not sections:
                logger.warning(f"[PdfParser] No sections detected for {paper_id}, treating entire document as 'other'")
                full_text = "\n\n".join(p["text"] for p in pages)
                sections.append(Section(
                    role="other",
                    heading="(Full Document)",
                    text=full_text,
                    start_page=1,
                    end_page=pages[-1]["page"]
                ))
            
            logger.info(f"[PdfParser] Parsed {len(sections)} sections for paper {paper_id}")
            
            return PaperSections(
                paper_id=paper_id,
                title=title,
                year=year,
                sections=sections
            )
            
        except Exception as e:
            logger.error(f"[PdfParser] Failed to parse sections for {pdf_path}: {e}")
            return PaperSections(
                paper_id=paper_id,
                title=title,
                year=year,
                sections=[]
            )
    
    def _extract_section_text(
        self,
        pages: List[Dict],
        start_page_idx: int,
        start_line_idx: int,
        end_page_idx: int,
        end_line_idx: Optional[int],
    ) -> str:
        """
        Extract text between two positions in the document.
        
        Args:
            pages: List of page dicts with 'text' key
            start_page_idx: Starting page index
            start_line_idx: Starting line index within the page
            end_page_idx: Ending page index
            end_line_idx: Ending line index (None = end of page)
            
        Returns:
            Extracted text as a single string
        """
        text_parts = []
        
        for page_idx in range(start_page_idx, end_page_idx + 1):
            if page_idx >= len(pages):
                break
                
            page_text = pages[page_idx]["text"]
            lines = page_text.split("\n")
            
            # Determine line range for this page
            if page_idx == start_page_idx:
                line_start = start_line_idx
            else:
                line_start = 0
            
            if page_idx == end_page_idx and end_line_idx is not None:
                line_end = end_line_idx
            else:
                line_end = len(lines)
            
            # Extract lines
            for line_idx in range(line_start, min(line_end, len(lines))):
                text_parts.append(lines[line_idx])
        
        return "\n".join(text_parts)

