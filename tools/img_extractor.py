"""
Image Extractor Tool for smolagents
Extracts graphs (figures) and tables from PDFs and saves them to ./imgs/<task_name>/
"""

import json
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional

from smolagents import Tool
from loguru import logger


class ImageExtractorTool(Tool):
    """
    Tool for extracting graphs (figures) and tables from parsed PDFs.
    
    This tool reads from mineru's content_list.json to find images and tables,
    then copies them to ./imgs/<task_name>/ directory organized by PDF name.
    """

    name = "img_extractor"
    description = (
        "Extract graphs (figures) and tables from parsed PDFs and save them "
        "to ./imgs/<task_name>/ directory. Takes a PDF name and task name as input. "
        "Returns a summary of extracted images and tables."
    )
    inputs = {
        "pdf_name": {
            "type": "string",
            "description": "The name of the PDF file (without .pdf extension) to extract images from",
        },
        "task_name": {
            "type": "string",
            "description": "Task name for organizing images (e.g., 'benchmark_landscape', 'prior_art_check')",
        },
        "content_types": {
            "type": "string",
            "description": "Comma-separated list of content types to extract: 'graph', 'table', or 'all' (default: 'all')",
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(
        self,
        output_base_dir: Path | str = None,
    ):
        """Initialize the image extractor tool"""
        super().__init__()
        # Default path for mineru output (same as DataAnalyzer)
        if output_base_dir is None:
            self.output_base_dir = Path(
                "/data/wdy/AgenticPaperQA/paper-prepare/sampled_jsons"
            )
        else:
            self.output_base_dir = Path(output_base_dir)
        
        # Base directory for extracted images
        self.imgs_base_dir = Path("./imgs")

    def setup(self):
        """Setup method called before first use"""
        logger.info("🔧 Setting up image extractor tool...")
        self.imgs_base_dir.mkdir(parents=True, exist_ok=True)
        self.is_initialized = True

    def _load_content_list(
        self, pdf_name: str
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Load the content list JSON file for the given PDF

        Args:
            pdf_name: Name of the PDF file (without extension)

        Returns:
            Tuple of (content list, error message or None)
        """
        # Check both auto and vlm directories
        json_path = None
        for dir_name in ["auto", "vlm"]:
            candidate = self.output_base_dir / pdf_name / dir_name / f"{pdf_name}_content_list.json"
            if candidate.exists():
                json_path = candidate
                break

        if not json_path:
            return None, f"Content list file not found in 'auto' or 'vlm' directories for {pdf_name}. PDF may need to be parsed first."

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                content_list = json.load(f)

            logger.info(f"📄 Loaded content list with {len(content_list)} blocks")
            return content_list, None

        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse JSON file: {e}"
            logger.error(f"❌ {error_msg}")
            return None, error_msg
        except Exception as e:
            error_msg = f"Failed to read JSON file: {e}"
            logger.error(f"❌ {error_msg}")
            return None, error_msg

    def _extract_images(
        self,
        content_list: List[Dict[str, Any]],
        pdf_name: str,
        task_name: str,
        extract_graphs: bool = True,
        extract_tables: bool = True,
    ) -> Dict[str, Any]:
        """
        Extract images and tables from content list and copy to imgs directory

        Args:
            content_list: List of content blocks from mineru
            pdf_name: Name of the PDF
            task_name: Task name for organizing images
            extract_graphs: Whether to extract graphs/figures
            extract_tables: Whether to extract tables

        Returns:
            Dictionary with extraction results
        """
        # Create task directory
        task_dir = self.imgs_base_dir / task_name / pdf_name
        task_dir.mkdir(parents=True, exist_ok=True)
        
        graphs_dir = task_dir / "graphs"
        tables_dir = task_dir / "tables"
        
        if extract_graphs:
            graphs_dir.mkdir(parents=True, exist_ok=True)
        if extract_tables:
            tables_dir.mkdir(parents=True, exist_ok=True)

        extracted_graphs = []
        extracted_tables = []
        
        graph_idx = 1
        table_idx = 1

        for block in content_list:
            block_type = block.get("type", "")
            
            # Extract graphs/figures
            if extract_graphs and block_type == "image":
                img_relative_path = block.get("img_path", "")
                if img_relative_path:
                    # Source image path
                    src_img_path = self.output_base_dir / pdf_name / "auto" / img_relative_path
                    
                    if src_img_path.exists():
                        # Get caption for naming
                        captions = block.get("image_caption", [])
                        caption_text = captions[0] if captions else f"Figure_{graph_idx}"
                        
                        # Create safe filename
                        safe_caption = "".join(
                            c if c.isalnum() or c in (' ', '-', '_') else '_' 
                            for c in caption_text
                        )[:50]
                        safe_caption = safe_caption.replace(' ', '_')
                        
                        # Determine file extension
                        ext = src_img_path.suffix or '.png'
                        dst_filename = f"graph_{graph_idx:03d}_{safe_caption}{ext}"
                        dst_path = graphs_dir / dst_filename
                        
                        # Copy image
                        try:
                            shutil.copy2(src_img_path, dst_path)
                            extracted_graphs.append({
                                "index": graph_idx,
                                "caption": caption_text,
                                "source_path": str(src_img_path),
                                "saved_path": str(dst_path),
                                "relative_path": f"{task_name}/{pdf_name}/graphs/{dst_filename}",
                            })
                            logger.info(f"📊 Extracted graph {graph_idx}: {dst_filename}")
                            graph_idx += 1
                        except Exception as e:
                            logger.warning(f"⚠️ Failed to copy image {src_img_path}: {e}")
            
            # Extract tables
            elif extract_tables and block_type == "table":
                # Tables might be stored as images or as structured data
                # Check for table image first
                table_img_path = block.get("table_img_path") or block.get("img_path", "")
                
                if table_img_path:
                    src_table_path = self.output_base_dir / pdf_name / "auto" / table_img_path
                    
                    if src_table_path.exists():
                        captions = block.get("table_caption", [])
                        caption_text = captions[0] if captions else f"Table_{table_idx}"
                        
                        safe_caption = "".join(
                            c if c.isalnum() or c in (' ', '-', '_') else '_' 
                            for c in caption_text
                        )[:50]
                        safe_caption = safe_caption.replace(' ', '_')
                        
                        ext = src_table_path.suffix or '.png'
                        dst_filename = f"table_{table_idx:03d}_{safe_caption}{ext}"
                        dst_path = tables_dir / dst_filename
                        
                        try:
                            shutil.copy2(src_table_path, dst_path)
                            extracted_tables.append({
                                "index": table_idx,
                                "caption": caption_text,
                                "source_path": str(src_table_path),
                                "saved_path": str(dst_path),
                                "relative_path": f"{task_name}/{pdf_name}/tables/{dst_filename}",
                            })
                            logger.info(f"📋 Extracted table {table_idx}: {dst_filename}")
                            table_idx += 1
                        except Exception as e:
                            logger.warning(f"⚠️ Failed to copy table image {src_table_path}: {e}")
                
                # Also save table data as JSON if available
                table_data = block.get("table_data") or block.get("table_content")
                if table_data:
                    json_filename = f"table_{table_idx:03d}_data.json"
                    json_path = tables_dir / json_filename
                    try:
                        with open(json_path, "w", encoding="utf-8") as f:
                            json.dump(table_data, f, indent=2, ensure_ascii=False)
                        logger.info(f"💾 Saved table data: {json_filename}")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to save table data: {e}")

        return {
            "graphs": extracted_graphs,
            "tables": extracted_tables,
            "total_graphs": len(extracted_graphs),
            "total_tables": len(extracted_tables),
            "output_directory": str(task_dir),
        }

    def forward(
        self,
        pdf_name: str,
        task_name: str,
        content_types: Optional[str] = None,
    ) -> str:
        """
        Extract graphs and tables from PDF and save to imgs directory

        Args:
            pdf_name: Name of the PDF file (without .pdf extension)
            task_name: Task name for organizing images
            content_types: Comma-separated list: 'graph', 'table', or 'all' (default: 'all')

        Returns:
            Summary string of extraction results
        """
        try:
            # Parse content_types
            if content_types is None or content_types.lower() == "all":
                extract_graphs = True
                extract_tables = True
            else:
                types = [t.strip().lower() for t in content_types.split(",")]
                extract_graphs = "graph" in types or "figure" in types or "image" in types
                extract_tables = "table" in types

            logger.info(f"🖼️ Extracting images from PDF: {pdf_name}")
            logger.info(f"📁 Task name: {task_name}")
            logger.info(f"📊 Extract graphs: {extract_graphs}, Extract tables: {extract_tables}")

            # Load content list
            content_list, error = self._load_content_list(pdf_name)
            if error:
                return f"Error: {error}"

            # Extract images and tables
            results = self._extract_images(
                content_list,
                pdf_name,
                task_name,
                extract_graphs=extract_graphs,
                extract_tables=extract_tables,
            )

            # Format summary
            summary_lines = [
                f"✅ Image extraction completed for {pdf_name}",
                f"📁 Output directory: {results['output_directory']}",
                f"📊 Graphs extracted: {results['total_graphs']}",
                f"📋 Tables extracted: {results['total_tables']}",
            ]

            if results['graphs']:
                summary_lines.append("\n📊 Extracted Graphs:")
                for graph in results['graphs']:
                    summary_lines.append(
                        f"  - Graph {graph['index']}: {graph['caption'][:50]}"
                    )
                    summary_lines.append(f"    Saved to: {graph['relative_path']}")

            if results['tables']:
                summary_lines.append("\n📋 Extracted Tables:")
                for table in results['tables']:
                    summary_lines.append(
                        f"  - Table {table['index']}: {table['caption'][:50]}"
                    )
                    summary_lines.append(f"    Saved to: {table['relative_path']}")

            summary = "\n".join(summary_lines)
            logger.info(f"✅ Extraction summary:\n{summary}")
            return summary

        except Exception as e:
            error_msg = f"❌ Image extraction failed: {str(e)}"
            logger.error(error_msg)
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return f"Error: {str(e)}"


if __name__ == "__main__":
    # Test the image extractor tool
    tool = ImageExtractorTool()
    tool.setup()

    # Example usage
    test_pdf_name = "example_paper"  # Replace with actual PDF name
    test_task_name = "benchmark_landscape"

    result = tool.forward(
        pdf_name=test_pdf_name,
        task_name=test_task_name,
        content_types="all",
    )

    print("🔍 Image Extraction Result:")
    print("=" * 60)
    print(result)
    print("=" * 60)

