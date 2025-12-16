"""
PDF Extractor Tool for smolagents
Extracts and parses PDF files to markdown format using mineru backend
"""

import os
from pathlib import Path
from typing import Optional

from smolagents import Tool
from loguru import logger

from .pdf_utils import parse_doc, MINERU_AVAILABLE, PIPELINE_BACKEND_AVAILABLE, VLM_BACKEND_AVAILABLE

# Make RAG imports optional (only needed for PDFExtractorRAGTool)
try:
    from rag.embeddings import OpenAIEmbedding
    from rag.retrievers import SimpleVectorRetriever
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False


class PDFExtractorTool(Tool):
    """
    Tool for extracting and parsing PDF files to markdown format.

    This tool takes a PDF file path as input and returns the first 2000 characters
    of the parsed markdown content. It uses the mineru backend for PDF parsing
    with support for formulas, tables, and OCR.
    """

    name = "pdf_extractor"
    description = (
        "Extract and parse PDF files to markdown format. Takes a PDF name as input "
        "and returns the first 2000 characters of the parsed markdown content. "
        "Supports complex PDFs with formulas, tables, and images. Uses cached results if available."
    )
    inputs = {
        "pdf_name": {
            "type": "string",
            "description": "The name of the PDF file (without .pdf extension) to be parsed",
        },
    }
    output_type = "string"

    def __init__(
        self,
        pdf_base_dir: Path | str = None,
        output_base_dir: Path | str = None,
    ):
        """Initialize the PDF extractor tool"""
        super().__init__()
        # Fixed paths for PDF processing
        if pdf_base_dir is None:
            self.pdf_base_dir = Path(
                "/data/wdy/AgenticPaperQA/paper-prepare/sampled_papers"
            )
        else:
            self.pdf_base_dir = Path(pdf_base_dir)
        if output_base_dir is None:
            self.output_base_dir = Path(
                "/data/wdy/AgenticPaperQA/paper-prepare/sampled_jsons"
            )
        else:
            self.output_base_dir = Path(output_base_dir)

    def setup(self):
        """Setup method called before first use"""
        logger.info("🔧 Setting up PDF extractor tool...")
        self.is_initialized = True

    def _find_markdown_file(self, output_dir: Path, pdf_name: str) -> Optional[Path]:
        """
        Find markdown file in either 'auto' or 'vlm' directory
        
        Args:
            output_dir: Base output directory
            pdf_name: PDF name without extension
            
        Returns:
            Path to markdown file if found, None otherwise
        """
        for dir_name in ["auto", "vlm"]:
            candidate_path = output_dir / pdf_name / dir_name / f"{pdf_name}.md"
            if candidate_path.exists():
                return candidate_path
        return None
    
    def _validate_pdf_path(self, pdf_path: str) -> tuple[Path, Optional[str]]:
        """
        Validate PDF file path and return Path object

        Args:
            pdf_path: Path to PDF file

        Returns:
            Tuple of (Path object, error message or None)
        """
        path = Path(pdf_path)

        if not path.exists():
            return None, f"PDF file not found: {pdf_path}"

        if not path.is_file():
            return None, f"Path is not a file: {pdf_path}"

        if path.suffix.lower() != ".pdf":
            return None, f"File is not a PDF: {pdf_path}"

        return path, None

    def _extract_markdown_content(
        self, output_dir: Path, pdf_name: str
    ) -> tuple[str, Optional[str]]:
        """
        Extract markdown content from parsed PDF output

        Args:
            output_dir: Directory containing parsed output
            pdf_name: Name of the PDF file (without extension)

        Returns:
            Tuple of (markdown content, error message or None)
        """
        # Check both possible directories: "auto" (pipeline backend) and "vlm" (VLM backend)
        possible_dirs = ["auto", "vlm"]
        md_file_path = None
        
        for dir_name in possible_dirs:
            candidate_path = output_dir / pdf_name / dir_name / f"{pdf_name}.md"
            if candidate_path.exists():
                md_file_path = candidate_path
                break
        
        if md_file_path is None:
            return None, f"Markdown file not found in any expected location (checked: {possible_dirs})"

        logger.info(f"📄 Reading markdown file: {md_file_path}")

        # Read the markdown content
        try:
            with open(md_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            return content, None
        except Exception as e:
            error_msg = f"Failed to read markdown file: {e}"
            logger.error(f"❌ {error_msg}")
            return None, error_msg

    def forward(self, pdf_name: str) -> str:
        """
        Extract and parse PDF file to markdown format

        Args:
            pdf_name: Name of the PDF file (without .pdf extension)

        Returns:
            First 2000 characters of the parsed markdown content
        """
        # Fixed values for language and backend
        language = "en"
        # Auto-select backend: prefer pipeline, fallback to VLM if pipeline not available
        if PIPELINE_BACKEND_AVAILABLE:
            backend = "pipeline"
        elif VLM_BACKEND_AVAILABLE:
            backend = "vlm-transformers"
            logger.info("⚠️ Pipeline backend not available, using VLM backend instead")
        else:
            return f"Error: No mineru backend available. Pipeline backend requires 'doclayout-yolo', VLM backend is also unavailable."

        try:
            logger.info(f"🚀 Starting PDF extraction for: {pdf_name}")

            # Build paths
            pdf_file_path = self.pdf_base_dir / f"{pdf_name}.pdf"
            output_dir = self.output_base_dir

            # Find markdown file (check both auto and vlm directories)
            expected_md_path = self._find_markdown_file(output_dir, pdf_name)

            # Check if already parsed (cache check)
            if expected_md_path:
                logger.info(f"✅ Found cached markdown file: {expected_md_path}")
                try:
                    with open(expected_md_path, "r", encoding="utf-8") as f:
                        markdown_content = f.read()
                except Exception as e:
                    return f"Error: Failed to read cached markdown file: {e}"
            else:
                # Validate input PDF exists
                validated_pdf_path, validation_error = self._validate_pdf_path(
                    str(pdf_file_path)
                )
                if validation_error:
                    return f"Error: {validation_error}"

                logger.info(f"📁 Using output directory: {output_dir}")
                logger.info(f"⚙️ Using language: {language}, backend: {backend}")

                # Parse PDF using pdf_utils
                try:
                    if not MINERU_AVAILABLE:
                        return f"Error: mineru module is not available. Please install mineru to use PDF extraction."
                    parse_doc(
                        path_list=[validated_pdf_path],
                        output_dir=str(output_dir),
                        lang=language,
                        backend=backend,
                        method="auto",
                    )
                except Exception as e:
                    return f"Error: PDF parsing failed: {e}"

                # Find and read markdown file
                expected_md_path = self._find_markdown_file(output_dir, pdf_name)
                if not expected_md_path:
                    return f"Error: Markdown file not found after parsing. Checked both 'auto' and 'vlm' directories."
                
                try:
                    with open(expected_md_path, "r", encoding="utf-8") as f:
                        markdown_content = f.read()
                except Exception as e:
                    return f"Error: Failed to read markdown file: {e}"

            # Return first 2000 characters
            if len(markdown_content) > 2000:
                result = (
                    markdown_content[:2000]
                    + "\n\n... (content truncated to 2000 characters)"
                )
                logger.info(f"✂️ Content truncated to 2000 characters")
            else:
                result = markdown_content
                logger.info(f"📏 Content length: {len(markdown_content)} characters")

            logger.info("✅ PDF extraction completed successfully")
            return result

        except Exception as e:
            error_msg = f"❌ PDF extraction failed: {str(e)}"
            logger.error(error_msg)
            return f"Error: {str(e)}"

        finally:
            # No cleanup needed as we use persistent output directory
            pass


class PDFExtractorRAGTool(Tool):
    """
    Tool for extracting PDF content with query-based retrieval.

    This tool parses a PDF and then searches for content relevant to a specific query,
    returning the most relevant sections up to 2000 characters.
    """

    name = "pdf_extractor_with_query"
    description = (
        "Extract and parse PDF files, then retrieve content relevant to a specific query. "
        "Takes a PDF name and a search query as input, returns the most relevant "
        "sections of the parsed content up to 2000 characters."
    )
    inputs = {
        "pdf_name": {
            "type": "string",
            "description": "The name of the PDF file (without .pdf extension) to be parsed",
        },
        "query": {
            "type": "string",
            "description": "Search query to find relevant content in the PDF",
        },
    }
    output_type = "string"

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        pdf_base_dir: Path | str = None,
        output_base_dir: Path | str = None,
        embedding_api_key: str | None = None,
    ):
        """Initialize the PDF extractor RAG tool
        
        Args:
            chunk_size: Size of text chunks for vector search
            chunk_overlap: Overlap between chunks
            pdf_base_dir: Directory containing PDF files
            output_base_dir: Directory for parsed markdown output
            embedding_api_key: OpenAI API key specifically for embeddings (optional)
        """
        super().__init__()
        # Fixed paths for PDF processing (same as PDFExtractorTool)
        if pdf_base_dir is None:
            self.pdf_base_dir = Path(
                "/data/wdy/AgenticPaperQA/paper-prepare/sampled_papers"
            )
        else:
            self.pdf_base_dir = Path(pdf_base_dir)
        if output_base_dir is None:
            self.output_base_dir = Path(
                "/data/wdy/AgenticPaperQA/paper-prepare/sampled_jsons"
            )
        else:
            self.output_base_dir = Path(output_base_dir)

        # Initialize vector retriever for content search
        if not RAG_AVAILABLE:
            raise ImportError("RAG modules (OpenAIEmbedding, SimpleVectorRetriever) are not available. Please install langchain and related dependencies.")
        
        # Initialize vector retriever with dedicated OpenAI API key for embeddings
        # Use provided key or fall back to environment variable
        embedding_key = embedding_api_key or os.getenv("OPENAI_EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
        
        # Initialize embedding model with dedicated API key
        # OpenAIEmbedding will use OpenAI's official endpoint (https://api.openai.com/v1)
        # even if OPENAI_BASE_URL points to Gemini, because we set OPENAI_EMBEDDING_BASE_URL
        # or it defaults to OpenAI's endpoint
        embedding_model = OpenAIEmbedding(api_key=embedding_key)
        
        self.retriever = SimpleVectorRetriever(
            embedding_model=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            path=None,  # Don't use persistent storage for PDF content
        )

    def setup(self):
        """Setup method called before first use"""
        logger.info("🔧 Setting up PDF extractor RAG tool...")
        self.is_initialized = True

    def _search_content(self, content: str, query: str) -> str:
        """
        Vector-based content search using SimpleVectorRetriever

        Args:
            content: Full markdown content
            query: Search query

        Returns:
            Most relevant content sections up to 2000 characters
        """
        try:
            # Use vector retriever to find most relevant content
            result = self.retriever.retrieve(query=query, contents=[content])

            # Handle different return types for backward compatibility
            # New version returns a formatted string, old version may return a list
            if isinstance(result, str):
                # New format: formatted string with "Relative content X:" headers
                if "Not enough relative contents" in result:
                    # No relevant content found, return beginning of content
                    logger.info("ℹ️ No relevant chunks found, using beginning of content")
                    return content[:2000] + "\n\n... (no specific matches found for query)"
                
                # Result is a formatted string with relevant content
                # Format: "Relative content 1:\n{chunk}\nRelative content 2:\n{chunk}\n..."
                # Extract just the content parts (remove "Relative content X:" headers)
                import re
                # Extract content after "Relative content X:" headers
                chunks = re.split(r'Relative content \d+:\n', result)
                chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
                
                if not chunks:
                    return content[:2000] + "\n\n... (no specific matches found for query)"
                
                # Combine chunks up to 2000 characters
                combined_result = "\n\n---\n\n".join(chunks)
                if len(combined_result) > 2000:
                    combined_result = combined_result[:2000] + "\n\n... (content truncated)"
                
                return combined_result
            elif isinstance(result, list):
                # Old format: list of chunks (for backward compatibility)
                if not result:
                    # If no relevant chunks found, return beginning of content
                    logger.info("ℹ️ No relevant chunks found, using beginning of content")
                    return content[:2000] + "\n\n... (no specific matches found for query)"

                # Combine the most relevant chunks
                result_parts = []
                total_length = 0

                for chunk in result:
                    chunk_text = (
                        chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
                    )
                    if (
                        total_length + len(chunk_text) > 1800
                    ):  # Leave room for truncation message
                        break
                    result_parts.append(chunk_text)
                    total_length += len(chunk_text)

                combined_result = "\n\n---\n\n".join(result_parts)
                if len(combined_result) > 2000:
                    combined_result = combined_result[:2000] + "\n\n... (content truncated)"

                return combined_result
            else:
                # Unexpected return type
                logger.warning(f"⚠️ Unexpected return type from retriever: {type(result)}")
                return content[:2000] + "\n\n... (unexpected result format)"

        except Exception as e:
            logger.warning(
                f"⚠️ Vector search failed, falling back to simple search: {e}"
            )
            # Fallback to returning beginning of content
            return (
                content[:2000]
                + "\n\n... (vector search failed, showing beginning of content)"
            )

    def forward(self, pdf_name: str, query: str) -> str:
        """
        Extract PDF and retrieve query-relevant content

        Args:
            pdf_name: Name of the PDF file (without .pdf extension)
            query: Search query for relevant content

        Returns:
            Query-relevant content up to 2000 characters
        """
        try:
            logger.info(f"🔍 Extracting PDF with query: '{query}'")

            # Build paths
            pdf_file_path = self.pdf_base_dir / f"{pdf_name}.pdf"
            output_dir = self.output_base_dir

            # Find markdown file (check both auto and vlm directories)
            expected_md_path = self._find_markdown_file(output_dir, pdf_name)

            # Check if already parsed (cache check)
            if expected_md_path:
                logger.info(f"✅ Found cached markdown file: {expected_md_path}")
                try:
                    with open(expected_md_path, "r", encoding="utf-8") as f:
                        full_content = f.read()
                except Exception as e:
                    return f"Error: Failed to read cached markdown file: {e}"
            else:
                # Validate input PDF exists
                if not pdf_file_path.exists():
                    return f"Error: PDF file not found: {pdf_file_path}"

                if not pdf_file_path.is_file():
                    return f"Error: Path is not a file: {pdf_file_path}"

                if pdf_file_path.suffix.lower() != ".pdf":
                    return f"Error: File is not a PDF: {pdf_file_path}"

                logger.info(f"📁 Using output directory: {output_dir}")
                logger.info(f"⚙️ Using language: en, backend: {backend}")

                # Parse PDF using pdf_utils
                try:
                    parse_doc(
                        path_list=[pdf_file_path],
                        output_dir=str(output_dir),
                        lang="en",
                        backend="pipeline",
                        method="auto",
                    )
                except Exception as e:
                    return f"Error: PDF parsing failed: {e}"

                # Find and read markdown file
                expected_md_path = self._find_markdown_file(output_dir, pdf_name)
                if not expected_md_path:
                    return f"Error: Markdown file not found after parsing. Checked both 'auto' and 'vlm' directories."

                try:
                    with open(expected_md_path, "r", encoding="utf-8") as f:
                        full_content = f.read()
                except Exception as e:
                    return f"Error: Failed to read markdown file: {e}"

            # Search for query-relevant content
            relevant_content = self._search_content(full_content, query)

            logger.info("✅ PDF extraction with query completed successfully")
            return f"Query: '{query}'\n\nRelevant content:\n\n{relevant_content}"

        except Exception as e:
            error_msg = f"❌ PDF extraction with query failed: {str(e)}"
            logger.error(error_msg)
            return f"Error: {str(e)}"


if __name__ == "__main__":
    # Test the PDF extractor tool
    tool = PDFExtractorTool()
    tool.setup()

    # Example usage - just provide the PDF name without extension
    test_pdf_name = "example_paper"  # Replace with actual PDF name

    # Check if the PDF exists in the expected location
    expected_pdf_path = tool.pdf_base_dir / f"{test_pdf_name}.pdf"

    if expected_pdf_path.exists():
        result = tool.forward(test_pdf_name)
        print("🔍 PDF Extraction Result:")
        print("=" * 50)
        print(result)
    else:
        print(f"❌ Test PDF not found: {expected_pdf_path}")
        print(
            "💡 Please place your PDF in the expected directory and provide the correct name."
        )
