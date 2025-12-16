#!/usr/bin/env python3
"""
Graph Comparison Feature - Experimental Evaluation Script

This script:
1. Loads PDFs from ./pdf_cache
2. Parses them with mineru (if needed) to extract graphs
3. Runs image extraction
4. Runs graph comparison queries
5. Records comprehensive metrics
"""

import json
import time
import traceback
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import statistics

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger

# Import tools
from tools.img_extractor import ImageExtractorTool
from tools.graph_comparison import GraphComparisonTool
from tools.pdf_extractor import PDFExtractorTool
from tools.pdf_utils import parse_doc, MINERU_AVAILABLE

# Verify mineru import directly
try:
    from mineru.cli.common import convert_pdf_bytes_to_bytes_by_pypdfium2, prepare_env, read_fn
    MINERU_DIRECT_AVAILABLE = True
    logger.info("✅ Direct mineru import: SUCCESS")
except ImportError as e:
    MINERU_DIRECT_AVAILABLE = False
    logger.warning("⚠️ Direct mineru import failed: {}".format(e))
    logger.info("📊 pdf_utils MINERU_AVAILABLE = {}".format(MINERU_AVAILABLE))


class GraphComparisonExperiment:
    """Experiment runner for graph comparison feature evaluation"""
    
    def __init__(
        self,
        pdf_cache_dir: Path = Path("./pdf_cache"),
        output_base_dir: Path = Path("./experiments/parsed_pdfs"),
        results_dir: Path = Path("./experiments/results"),
        task_name: str = "graph_comparison_experiment",
    ):
        self.pdf_cache_dir = Path(pdf_cache_dir)
        self.output_base_dir = Path(output_base_dir)
        self.results_dir = Path(results_dir)
        self.task_name = task_name
        
        # Create directories
        self.output_base_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize tools
        self.img_extractor = ImageExtractorTool(output_base_dir=self.output_base_dir)
        self.img_extractor.setup()
        
        self.graph_comparison = GraphComparisonTool(output_base_dir=self.output_base_dir)
        
        # PDFExtractorTool expects pdf_base_dir, but we'll handle pdf_cache separately
        # We'll use pdf_extractor for parsing, but need to ensure PDFs are accessible
        self.pdf_extractor = PDFExtractorTool(
            pdf_base_dir=self.pdf_cache_dir,  # Point to pdf_cache
            output_base_dir=self.output_base_dir,
        )
        self.pdf_extractor.setup()
        
        # Experiment results storage
        self.results: Dict[str, Any] = {
            "experiment_metadata": {
                "start_time": datetime.now().isoformat(),
                "task_name": task_name,
                "pdf_cache_dir": str(pdf_cache_dir),
                "output_base_dir": str(output_base_dir),
            },
            "extraction_results": [],
            "comparison_results": [],
            "summary_metrics": {},
        }
        
        # Test queries for graph comparison
        self.test_queries = [
            "accuracy comparison curves",
            "performance graphs",
            "architecture diagrams",
            "ablation study results",
            "experimental results",
            "method comparison",
        ]
    
    def find_pdfs(self) -> List[Path]:
        """Find all PDF files in pdf_cache directory"""
        pdf_files = list(self.pdf_cache_dir.glob("*.pdf"))
        logger.info(f"Found {len(pdf_files)} PDF files in {self.pdf_cache_dir}")
        return pdf_files
    
    def get_pdf_name(self, pdf_path: Path) -> str:
        """Extract PDF name without extension"""
        return pdf_path.stem
    
    def parse_pdf_if_needed(self, pdf_path: Path, pdf_name: str) -> Tuple[bool, Optional[str]]:
        """
        Parse PDF with mineru if not already parsed.
        Returns (success, error_message)
        """
        # Check if already parsed (check both auto and vlm directories)
        content_list_path = None
        for dir_name in ["auto", "vlm"]:
            candidate = self.output_base_dir / pdf_name / dir_name / f"{pdf_name}_content_list.json"
            if candidate.exists():
                content_list_path = candidate
                break
        
        if content_list_path:
            logger.info(f"✅ PDF {pdf_name} already parsed, skipping")
            return True, None
        
        # Check both MINERU_AVAILABLE from pdf_utils and direct import
        mineru_available = MINERU_AVAILABLE or MINERU_DIRECT_AVAILABLE
        if not mineru_available:
            logger.warning("⚠️ mineru not available (pdf_utils: {}, direct: {}), cannot parse PDFs".format(
                MINERU_AVAILABLE, MINERU_DIRECT_AVAILABLE))
            return False, "mineru not available"
        
        # Ensure PDF exists in pdf_cache (it should, but verify)
        if not pdf_path.exists():
            return False, f"PDF file not found: {pdf_path}"
        
        try:
            logger.info(f"📄 Parsing PDF: {pdf_name}")
            # PDFExtractorTool expects pdf_name without extension and looks in pdf_base_dir
            result = self.pdf_extractor.forward(pdf_name=pdf_name)
            
            if result.startswith("Error:"):
                logger.warning(f"⚠️ Parsing failed: {result}")
                return False, result
            else:
                # Verify content_list was created (check both directories)
                content_list_path = None
                for dir_name in ["auto", "vlm"]:
                    candidate = self.output_base_dir / pdf_name / dir_name / f"{pdf_name}_content_list.json"
                    if candidate.exists():
                        content_list_path = candidate
                        break
                
                if content_list_path:
                    logger.info(f"✅ Successfully parsed {pdf_name}")
                    return True, None
                else:
                    logger.warning(f"⚠️ Content list not created for {pdf_name}")
                    return False, "Content list not created after parsing"
        except Exception as e:
            logger.error(f"❌ Parsing exception for {pdf_name}: {e}")
            traceback.print_exc()
            return False, f"Parsing exception: {str(e)}"
    
    def extract_graphs(self, pdf_name: str) -> Dict[str, Any]:
        """Extract graphs from a parsed PDF"""
        extraction_metrics = {
            "pdf_name": pdf_name,
            "start_time": time.time(),
            "success": False,
            "graphs_extracted": 0,
            "tables_extracted": 0,
            "graphs_with_captions": 0,
            "graphs_without_captions": 0,
            "error": None,
            "extraction_time": 0,
        }
        
        try:
            logger.info(f"🖼️ Extracting graphs from {pdf_name}")
            result = self.img_extractor.forward(
                pdf_name=pdf_name,
                task_name=self.task_name,
                content_types="graph",  # Only graphs for now
            )
            
            extraction_metrics["extraction_time"] = time.time() - extraction_metrics["start_time"]
            
            if result.startswith("Error:"):
                extraction_metrics["error"] = result
                return extraction_metrics
            
            # Parse the result to get counts
            # The result string contains information like "Graphs extracted: X"
            if "Graphs extracted:" in result:
                try:
                    # Try to extract numbers from the result
                    lines = result.split("\n")
                    for line in lines:
                        if "Graphs extracted:" in line:
                            extraction_metrics["graphs_extracted"] = int(
                                line.split("Graphs extracted:")[1].strip().split()[0]
                            )
                        if "Tables extracted:" in line:
                            extraction_metrics["tables_extracted"] = int(
                                line.split("Tables extracted:")[1].strip().split()[0]
                            )
                except:
                    pass
            
            # Load content list to get detailed metrics
            content_list, error = self.img_extractor._load_content_list(pdf_name)
            if not error and content_list:
                graph_count = sum(1 for block in content_list if block.get("type") == "image")
                extraction_metrics["graphs_extracted"] = graph_count
                
                # Count graphs with/without captions
                for block in content_list:
                    if block.get("type") == "image":
                        captions = block.get("image_caption", [])
                        if captions and len(captions) > 0:
                            extraction_metrics["graphs_with_captions"] += 1
                        else:
                            extraction_metrics["graphs_without_captions"] += 1
            
            extraction_metrics["success"] = True
            logger.info(
                f"✅ Extracted {extraction_metrics['graphs_extracted']} graphs from {pdf_name}"
            )
            
        except Exception as e:
            extraction_metrics["error"] = str(e)
            extraction_metrics["extraction_time"] = time.time() - extraction_metrics["start_time"]
            logger.error(f"❌ Graph extraction failed for {pdf_name}: {e}")
            traceback.print_exc()
        
        return extraction_metrics
    
    def run_comparison_query(
        self, pdf_names: List[str], query: str, comparison_type: str = "all", top_k: int = 5
    ) -> Dict[str, Any]:
        """Run a single graph comparison query"""
        comparison_metrics = {
            "query": query,
            "pdf_names": pdf_names,
            "comparison_type": comparison_type,
            "top_k": top_k,
            "start_time": time.time(),
            "success": False,
            "graphs_found": 0,
            "similarity_scores": [],
            "comparison_time": 0,
            "error": None,
        }
        
        try:
            logger.info(f"🔍 Running comparison query: '{query}' on {len(pdf_names)} PDFs")
            
            pdf_names_str = ",".join(pdf_names)
            result = self.graph_comparison.forward(
                pdf_names=pdf_names_str,
                task_name=self.task_name,
                query=query,
                comparison_type=comparison_type,
                top_k=top_k,
            )
            
            comparison_metrics["comparison_time"] = time.time() - comparison_metrics["start_time"]
            
            if result.startswith("Error:") or result.startswith("❌"):
                comparison_metrics["error"] = result
                return comparison_metrics
            
            # Parse result to extract metrics
            if "Found" in result and "similar graphs" in result:
                try:
                    # Extract number of graphs found
                    for line in result.split("\n"):
                        if "Found" in line and "similar graphs" in line:
                            parts = line.split("Found")[1].split("similar graphs")
                            if parts:
                                comparison_metrics["graphs_found"] = int(parts[0].strip())
                except:
                    pass
            
            # Try to extract similarity scores from the report
            # The report contains lines like "Similarity Score: 0.xxx"
            lines = result.split("\n")
            for i, line in enumerate(lines):
                if "Similarity Score:" in line:
                    try:
                        score_str = line.split("Similarity Score:")[1].strip().split()[0]
                        score = float(score_str)
                        comparison_metrics["similarity_scores"].append(score)
                    except:
                        pass
            
            comparison_metrics["success"] = True
            logger.info(
                f"✅ Comparison query completed: found {comparison_metrics['graphs_found']} graphs"
            )
            
        except Exception as e:
            comparison_metrics["error"] = str(e)
            comparison_metrics["comparison_time"] = time.time() - comparison_metrics["start_time"]
            logger.error(f"❌ Comparison query failed: {e}")
            traceback.print_exc()
        
        return comparison_metrics
    
    def compute_summary_metrics(self) -> Dict[str, Any]:
        """Compute summary metrics from all results"""
        extraction_results = self.results["extraction_results"]
        comparison_results = self.results["comparison_results"]
        
        summary = {
            "total_pdfs": len(extraction_results),
            "successful_extractions": sum(1 for r in extraction_results if r["success"]),
            "failed_extractions": sum(1 for r in extraction_results if not r["success"]),
            "total_graphs_extracted": sum(r["graphs_extracted"] for r in extraction_results),
            "total_tables_extracted": sum(r["tables_extracted"] for r in extraction_results),
            "total_graphs_with_captions": sum(r["graphs_with_captions"] for r in extraction_results),
            "total_graphs_without_captions": sum(
                r["graphs_without_captions"] for r in extraction_results
            ),
            "avg_graphs_per_pdf": statistics.mean([r["graphs_extracted"] for r in extraction_results])
            if extraction_results
            else 0,
            "avg_extraction_time": statistics.mean([r["extraction_time"] for r in extraction_results])
            if extraction_results
            else 0,
            "extraction_success_rate": (
                sum(1 for r in extraction_results if r["success"]) / len(extraction_results)
                if extraction_results
                else 0
            ),
            "caption_coverage_rate": (
                sum(r["graphs_with_captions"] for r in extraction_results)
                / (
                    sum(r["graphs_extracted"] for r in extraction_results)
                    if sum(r["graphs_extracted"] for r in extraction_results) > 0
                    else 1
                )
            ),
            "total_comparison_queries": len(comparison_results),
            "successful_comparisons": sum(1 for r in comparison_results if r["success"]),
            "failed_comparisons": sum(1 for r in comparison_results if not r["success"]),
            "avg_comparison_time": statistics.mean([r["comparison_time"] for r in comparison_results])
            if comparison_results
            else 0,
            "avg_graphs_found_per_query": (
                statistics.mean([r["graphs_found"] for r in comparison_results if r["success"]])
                if [r for r in comparison_results if r["success"]]
                else 0
            ),
            "avg_similarity_score": (
                statistics.mean(
                    [
                        score
                        for r in comparison_results
                        for score in r.get("similarity_scores", [])
                    ]
                )
                if [
                    score
                    for r in comparison_results
                    for score in r.get("similarity_scores", [])
                ]
                else 0
            ),
        }
        
        return summary
    
    def run_experiment(self):
        """Run the complete experiment"""
        logger.info("🚀 Starting Graph Comparison Experiment")
        logger.info(f"📁 PDF cache: {self.pdf_cache_dir}")
        logger.info(f"📁 Output base: {self.output_base_dir}")
        
        # Step 1: Find all PDFs
        pdf_files = self.find_pdfs()
        if not pdf_files:
            logger.warning("⚠️ No PDF files found in pdf_cache directory")
            return
        
        # Step 2: Parse PDFs and extract graphs
        pdf_names = []
        for pdf_path in pdf_files:
            pdf_name = self.get_pdf_name(pdf_path)
            pdf_names.append(pdf_name)
            
            # Parse PDF if needed
            success, error = self.parse_pdf_if_needed(pdf_path, pdf_name)
            if not success:
                logger.warning(f"⚠️ Failed to parse {pdf_name}: {error}")
                # Still try extraction in case it was parsed before
                pass
            
            # Extract graphs
            extraction_metrics = self.extract_graphs(pdf_name)
            self.results["extraction_results"].append(extraction_metrics)
        
        # Step 3: Run comparison queries
        if len(pdf_names) >= 2:  # Need at least 2 PDFs for comparison
            for query in self.test_queries:
                # Test different comparison types
                for comparison_type in ["visual", "semantic", "all"]:
                    comparison_metrics = self.run_comparison_query(
                        pdf_names=pdf_names,
                        query=query,
                        comparison_type=comparison_type,
                        top_k=5,
                    )
                    self.results["comparison_results"].append(comparison_metrics)
        else:
            logger.warning("⚠️ Need at least 2 PDFs for comparison queries")
        
        # Step 4: Compute summary metrics
        self.results["summary_metrics"] = self.compute_summary_metrics()
        self.results["experiment_metadata"]["end_time"] = datetime.now().isoformat()
        
        # Step 5: Save results
        self.save_results()
        
        logger.info("✅ Experiment completed")
        self.print_summary()
    
    def save_results(self):
        """Save experiment results to JSON file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_file = self.results_dir / f"graph_comparison_experiment_{timestamp}.json"
        
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        
        logger.info(f"💾 Results saved to {results_file}")
    
    def print_summary(self):
        """Print experiment summary"""
        summary = self.results["summary_metrics"]
        
        print("\n" + "=" * 60)
        print("EXPERIMENT SUMMARY")
        print("=" * 60)
        print(f"Total PDFs processed: {summary['total_pdfs']}")
        print(f"Successful extractions: {summary['successful_extractions']}")
        print(f"Failed extractions: {summary['failed_extractions']}")
        print(f"Extraction success rate: {summary['extraction_success_rate']:.2%}")
        print(f"\nTotal graphs extracted: {summary['total_graphs_extracted']}")
        print(f"Total tables extracted: {summary['total_tables_extracted']}")
        print(f"Graphs with captions: {summary['total_graphs_with_captions']}")
        print(f"Graphs without captions: {summary['total_graphs_without_captions']}")
        print(f"Caption coverage rate: {summary['caption_coverage_rate']:.2%}")
        print(f"Average graphs per PDF: {summary['avg_graphs_per_pdf']:.2f}")
        print(f"Average extraction time: {summary['avg_extraction_time']:.2f}s")
        print(f"\nTotal comparison queries: {summary['total_comparison_queries']}")
        print(f"Successful comparisons: {summary['successful_comparisons']}")
        print(f"Failed comparisons: {summary['failed_comparisons']}")
        print(f"Average comparison time: {summary['avg_comparison_time']:.2f}s")
        print(f"Average graphs found per query: {summary['avg_graphs_found_per_query']:.2f}")
        if summary['avg_similarity_score'] > 0:
            print(f"Average similarity score: {summary['avg_similarity_score']:.3f}")
        print("=" * 60 + "\n")


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Graph Comparison Experiment")
    parser.add_argument(
        "--pdf-cache",
        type=str,
        default="./pdf_cache",
        help="Directory containing PDF files",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./experiments/parsed_pdfs",
        help="Directory for parsed PDF outputs",
    )
    parser.add_argument(
        "--results-dir",
        type=str,
        default="./experiments/results",
        help="Directory for experiment results",
    )
    parser.add_argument(
        "--task-name",
        type=str,
        default="graph_comparison_experiment",
        help="Task name for organizing images",
    )
    
    args = parser.parse_args()
    
    # Run experiment
    experiment = GraphComparisonExperiment(
        pdf_cache_dir=Path(args.pdf_cache),
        output_base_dir=Path(args.output_dir),
        results_dir=Path(args.results_dir),
        task_name=args.task_name,
    )
    
    experiment.run_experiment()


if __name__ == "__main__":
    main()

