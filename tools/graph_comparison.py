"""
Graph Comparison Tool for smolagents
Gathers and compares graphs/figures from multiple PDFs
"""

import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from PIL import Image

from smolagents import Tool
from loguru import logger

from .img_extractor import ImageExtractorTool
from .llm_chat import chat_with_llm
from rag.embeddings import VisionLanguageEmbedding, OpenAIEmbedding


class GraphComparisonTool(Tool):
    """
    Tool for gathering and comparing graphs/figures from multiple PDFs.
    
    Features:
    - Gathers graphs from multiple PDFs using ImageExtractorTool
    - Visual similarity search using vision embeddings
    - Semantic similarity search using caption embeddings
    - Detailed comparison using multimodal LLM
    - Generates comparison reports
    """

    name = "graph_comparison"
    description = (
        "Gather and compare graphs/figures from multiple PDFs. "
        "Takes a list of PDF names and a query description, then finds similar graphs "
        "and generates comparison reports. Supports visual and semantic similarity search."
    )
    inputs = {
        "pdf_names": {
            "type": "string",
            "description": "Comma-separated list of PDF names (without .pdf extension) to compare graphs from",
        },
        "task_name": {
            "type": "string",
            "description": "Task name for organizing images (e.g., 'benchmark_landscape', 'prior_art_check')",
        },
        "query": {
            "type": "string",
            "description": "Description of what to compare (e.g., 'accuracy curves', 'performance comparison', 'architecture diagrams')",
        },
        "comparison_type": {
            "type": "string",
            "description": "Type of comparison: 'visual' (image similarity), 'semantic' (caption similarity), 'data' (extract and compare data), or 'all' (default: 'all')",
            "nullable": True,
        },
        "top_k": {
            "type": "integer",
            "description": "Number of similar graphs to return (default: 5)",
            "nullable": True,
        },
    }
    output_type = "string"

    def __init__(
        self,
        output_base_dir: Path | str = None,
        vision_model: str = "openai/clip-vit-base-patch32",
    ):
        """Initialize the graph comparison tool"""
        super().__init__()
        
        # Initialize image extractor
        self.img_extractor = ImageExtractorTool(output_base_dir=output_base_dir)
        self.img_extractor.setup()
        
        # Base directory for extracted images
        self.imgs_base_dir = Path("./imgs")
        
        # Initialize embedding models
        try:
            self.vision_embedding = VisionLanguageEmbedding(model_name=vision_model)
            logger.info(f"✅ Vision embedding model loaded: {vision_model}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load vision embedding model: {e}")
            self.vision_embedding = None
        
        try:
            self.text_embedding = OpenAIEmbedding()
            logger.info("✅ Text embedding model loaded")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load text embedding model: {e}")
            self.text_embedding = None
        
        # Cache for graph metadata and embeddings
        self.graph_cache: Dict[str, Dict[str, Any]] = {}

    def _gather_graphs(
        self, pdf_names: List[str], task_name: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Gather graphs from multiple PDFs using ImageExtractorTool
        
        Args:
            pdf_names: List of PDF names
            task_name: Task name for organization
            
        Returns:
            Dictionary mapping PDF names to lists of graph metadata
        """
        all_graphs = {}
        
        for pdf_name in pdf_names:
            logger.info(f"📊 Gathering graphs from {pdf_name}...")
            
            # Extract images using ImageExtractorTool
            result = self.img_extractor.forward(
                pdf_name=pdf_name,
                task_name=task_name,
                content_types="graph",
            )
            
            # Load content list to get graph metadata
            content_list, error = self.img_extractor._load_content_list(pdf_name)
            if error:
                logger.warning(f"⚠️ Failed to load content list for {pdf_name}: {error}")
                continue
            
            # Extract graph metadata
            graphs = []
            graph_idx = 1
            for block in content_list:
                if block.get("type") == "image":
                    img_relative_path = block.get("img_path", "")
                    if img_relative_path:
                        # Try to find the saved image path in imgs directory
                        graphs_dir = self.imgs_base_dir / task_name / pdf_name / "graphs"
                        saved_img_path = None
                        
                        # Look for graph files in the directory
                        if graphs_dir.exists():
                            graph_files = sorted(graphs_dir.glob(f"graph_{graph_idx:03d}_*"))
                            if graph_files:
                                saved_img_path = graph_files[0]
                        
                        # Fallback to source path if not found in imgs directory
                        if not saved_img_path or not saved_img_path.exists():
                            source_path = (
                                self.img_extractor.output_base_dir / pdf_name / "auto" / img_relative_path
                            )
                            if source_path.exists():
                                saved_img_path = source_path
                            else:
                                logger.warning(f"⚠️ Image not found: {img_relative_path}")
                                continue
                        
                        captions = block.get("image_caption", [])
                        caption_text = captions[0] if captions else f"Figure {graph_idx}"
                        
                        graph_meta = {
                            "pdf_name": pdf_name,
                            "graph_id": f"{pdf_name}_graph_{graph_idx}",
                            "index": graph_idx,
                            "caption": caption_text,
                            "all_captions": captions,
                            "image_path": str(saved_img_path),
                            "img_relative_path": img_relative_path,
                            "page": block.get("page", None),
                            "block_data": block,
                        }
                        graphs.append(graph_meta)
                        graph_idx += 1
            
            all_graphs[pdf_name] = graphs
            logger.info(f"✅ Found {len(graphs)} graphs in {pdf_name}")
        
        return all_graphs

    def _compute_visual_embeddings(
        self, graphs: List[Dict[str, Any]]
    ) -> Dict[str, List[float]]:
        """
        Compute visual embeddings for graphs
        
        Args:
            graphs: List of graph metadata dictionaries
            
        Returns:
            Dictionary mapping graph_id to embedding vector
        """
        if not self.vision_embedding:
            return {}
        
        embeddings = {}
        
        for graph in graphs:
            graph_id = graph["graph_id"]
            img_path = Path(graph["image_path"])
            
            if not img_path.exists():
                logger.warning(f"⚠️ Image not found: {img_path}")
                continue
            
            try:
                # Load and embed image
                img = Image.open(img_path)
                embedding = self.vision_embedding.embed(obj=img)
                embeddings[graph_id] = embedding
                logger.debug(f"✅ Computed visual embedding for {graph_id}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to compute visual embedding for {graph_id}: {e}")
        
        return embeddings

    def _compute_semantic_embeddings(
        self, graphs: List[Dict[str, Any]]
    ) -> Dict[str, List[float]]:
        """
        Compute semantic embeddings for graph captions
        
        Args:
            graphs: List of graph metadata dictionaries
            
        Returns:
            Dictionary mapping graph_id to embedding vector
        """
        if not self.text_embedding:
            return {}
        
        embeddings = {}
        
        for graph in graphs:
            graph_id = graph["graph_id"]
            caption = graph["caption"]
            
            try:
                # Embed caption text
                embedding = self.text_embedding.embed(obj=caption)
                embeddings[graph_id] = embedding
                logger.debug(f"✅ Computed semantic embedding for {graph_id}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to compute semantic embedding for {graph_id}: {e}")
        
        return embeddings

    def _find_similar_graphs(
        self,
        query: str,
        all_graphs: Dict[str, List[Dict[str, Any]]],
        comparison_type: str = "all",
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Find graphs similar to the query
        
        Args:
            query: Description of what to find
            all_graphs: Dictionary of graphs by PDF name
            comparison_type: Type of comparison ('visual', 'semantic', 'all')
            top_k: Number of results to return
            
        Returns:
            List of similar graphs with similarity scores
        """
        # Flatten all graphs into a single list
        all_graphs_list = []
        for pdf_name, graphs in all_graphs.items():
            all_graphs_list.extend(graphs)
        
        if not all_graphs_list:
            return []
        
        results = []
        
        # Compute query embedding
        query_visual_emb = None
        query_semantic_emb = None
        
        if comparison_type in ["visual", "all"] and self.vision_embedding:
            try:
                # For visual search, we can use text query with CLIP
                query_visual_emb = self.vision_embedding.embed(obj=query)
            except Exception as e:
                logger.warning(f"⚠️ Failed to compute visual embedding for query: {e}")
        
        if comparison_type in ["semantic", "all"] and self.text_embedding:
            try:
                query_semantic_emb = self.text_embedding.embed(obj=query)
            except Exception as e:
                logger.warning(f"⚠️ Failed to compute semantic embedding for query: {e}")
        
        # Compute embeddings for all graphs
        visual_embeddings = {}
        semantic_embeddings = {}
        
        if comparison_type in ["visual", "all"]:
            visual_embeddings = self._compute_visual_embeddings(all_graphs_list)
        
        if comparison_type in ["semantic", "all"]:
            semantic_embeddings = self._compute_semantic_embeddings(all_graphs_list)
        
        # Compute similarity scores
        for graph in all_graphs_list:
            graph_id = graph["graph_id"]
            scores = {}
            
            # Visual similarity
            if query_visual_emb and graph_id in visual_embeddings:
                try:
                    visual_sim = np.dot(
                        np.array(query_visual_emb),
                        np.array(visual_embeddings[graph_id])
                    )
                    scores["visual"] = float(visual_sim)
                except Exception as e:
                    logger.debug(f"Visual similarity computation failed: {e}")
            
            # Semantic similarity
            if query_semantic_emb and graph_id in semantic_embeddings:
                try:
                    semantic_sim = np.dot(
                        np.array(query_semantic_emb),
                        np.array(semantic_embeddings[graph_id])
                    )
                    scores["semantic"] = float(semantic_sim)
                except Exception as e:
                    logger.debug(f"Semantic similarity computation failed: {e}")
            
            # Combined score (weighted average)
            if scores:
                if "visual" in scores and "semantic" in scores:
                    combined_score = 0.6 * scores["visual"] + 0.4 * scores["semantic"]
                elif "visual" in scores:
                    combined_score = scores["visual"]
                elif "semantic" in scores:
                    combined_score = scores["semantic"]
                else:
                    combined_score = 0.0
                
                graph["similarity_scores"] = scores
                graph["combined_score"] = combined_score
                results.append(graph)
        
        # Sort by combined score
        results.sort(key=lambda x: x.get("combined_score", 0.0), reverse=True)
        
        return results[:top_k]

    def _compare_graphs_pair(
        self, graph1: Dict[str, Any], graph2: Dict[str, Any]
    ) -> str:
        """
        Compare two graphs using multimodal LLM
        
        Args:
            graph1: First graph metadata
            graph2: Second graph metadata
            
        Returns:
            Comparison text
        """
        try:
            # Build comparison prompt
            prompt = f"""Please compare these two graphs and identify:
1. Similarities (what they have in common)
2. Differences (key distinctions)
3. Which graph shows better performance/results (if applicable)
4. Any notable patterns or trends

Graph 1:
- Source: {graph1['pdf_name']}
- Caption: {graph1['caption']}

Graph 2:
- Source: {graph2['pdf_name']}
- Caption: {graph2['caption']}

Provide a detailed comparison."""
            
            # Use multimodal LLM if both images exist
            img1_path = Path(graph1["image_path"])
            img2_path = Path(graph2["image_path"])
            
            if img1_path.exists() and img2_path.exists():
                # For now, compare using text only (can be enhanced to use both images)
                # Note: Most LLMs support multiple images, but we'll use a simpler approach
                result = chat_with_llm(
                    text_prompt=prompt,
                    image_path=img1_path,  # Use first image
                    system_prompt="You are an expert at comparing scientific graphs and figures.",
                )
                return result
            else:
                # Fallback to text-only comparison
                result = chat_with_llm(
                    text_prompt=prompt,
                    system_prompt="You are an expert at comparing scientific graphs and figures.",
                )
                return result
                
        except Exception as e:
            logger.warning(f"⚠️ Failed to compare graphs: {e}")
            return f"Comparison failed: {str(e)}"

    def _generate_comparison_report(
        self,
        similar_graphs: List[Dict[str, Any]],
        query: str,
        comparison_type: str,
    ) -> str:
        """
        Generate a comparison report
        
        Args:
            similar_graphs: List of similar graphs with scores
            query: Original query
            comparison_type: Type of comparison used
            
        Returns:
            Formatted comparison report
        """
        lines = []
        lines.append(f"# Graph Comparison Report")
        lines.append("")
        lines.append(f"**Query:** {query}")
        lines.append(f"**Comparison Type:** {comparison_type}")
        lines.append(f"**Found {len(similar_graphs)} similar graphs**")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # Group by PDF
        graphs_by_pdf = {}
        for graph in similar_graphs:
            pdf_name = graph["pdf_name"]
            if pdf_name not in graphs_by_pdf:
                graphs_by_pdf[pdf_name] = []
            graphs_by_pdf[pdf_name].append(graph)
        
        # List all similar graphs
        lines.append("## Similar Graphs Found")
        lines.append("")
        
        for idx, graph in enumerate(similar_graphs, 1):
            scores = graph.get("similarity_scores", {})
            combined_score = graph.get("combined_score", 0.0)
            
            lines.append(f"### {idx}. Graph from {graph['pdf_name']}")
            lines.append(f"- **Caption:** {graph['caption']}")
            lines.append(f"- **Similarity Score:** {combined_score:.3f}")
            if "visual" in scores:
                lines.append(f"  - Visual: {scores['visual']:.3f}")
            if "semantic" in scores:
                lines.append(f"  - Semantic: {scores['semantic']:.3f}")
            lines.append(f"- **Image Path:** {graph['image_path']}")
            if graph.get("page"):
                lines.append(f"- **Page:** {graph['page']}")
            lines.append("")
        
        # Pairwise comparisons (top 3 pairs)
        if len(similar_graphs) >= 2:
            lines.append("## Pairwise Comparisons")
            lines.append("")
            
            # Compare top graphs with each other
            num_comparisons = min(3, len(similar_graphs) - 1)
            for i in range(num_comparisons):
                graph1 = similar_graphs[0]
                graph2 = similar_graphs[i + 1]
                
                lines.append(f"### Comparison: {graph1['pdf_name']} vs {graph2['pdf_name']}")
                lines.append("")
                
                try:
                    comparison_text = self._compare_graphs_pair(graph1, graph2)
                    lines.append(comparison_text)
                except Exception as e:
                    lines.append(f"*Comparison failed: {str(e)}*")
                
                lines.append("")
        
        # Summary by PDF
        lines.append("## Summary by Paper")
        lines.append("")
        for pdf_name, graphs in graphs_by_pdf.items():
            lines.append(f"### {pdf_name}")
            lines.append(f"- **Number of similar graphs:** {len(graphs)}")
            for graph in graphs:
                lines.append(f"  - {graph['caption'][:60]}... (score: {graph.get('combined_score', 0.0):.3f})")
            lines.append("")
        
        return "\n".join(lines)

    def forward(
        self,
        pdf_names: str,
        task_name: str,
        query: str,
        comparison_type: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> str:
        """
        Gather and compare graphs from multiple PDFs
        
        Args:
            pdf_names: Comma-separated list of PDF names
            task_name: Task name for organizing images
            query: Description of what to compare
            comparison_type: Type of comparison ('visual', 'semantic', 'data', 'all')
            top_k: Number of similar graphs to return
            
        Returns:
            Comparison report as formatted string
        """
        try:
            # Parse inputs
            pdf_list = [name.strip() for name in pdf_names.split(",")]
            comparison_type = comparison_type or "all"
            top_k = top_k or 5
            
            logger.info(f"🔍 Starting graph comparison for {len(pdf_list)} PDFs")
            logger.info(f"📝 Query: {query}")
            logger.info(f"🔧 Comparison type: {comparison_type}")
            
            # Step 1: Gather graphs from all PDFs
            logger.info("📊 Step 1: Gathering graphs from PDFs...")
            all_graphs = self._gather_graphs(pdf_list, task_name)
            
            total_graphs = sum(len(graphs) for graphs in all_graphs.values())
            if total_graphs == 0:
                return f"❌ No graphs found in the specified PDFs: {', '.join(pdf_list)}"
            
            logger.info(f"✅ Found {total_graphs} total graphs across {len(pdf_list)} PDFs")
            
            # Step 2: Find similar graphs
            logger.info("🔍 Step 2: Finding similar graphs...")
            similar_graphs = self._find_similar_graphs(
                query=query,
                all_graphs=all_graphs,
                comparison_type=comparison_type,
                top_k=top_k,
            )
            
            if not similar_graphs:
                return f"❌ No similar graphs found for query: '{query}'"
            
            logger.info(f"✅ Found {len(similar_graphs)} similar graphs")
            
            # Step 3: Generate comparison report
            logger.info("📝 Step 3: Generating comparison report...")
            report = self._generate_comparison_report(
                similar_graphs=similar_graphs,
                query=query,
                comparison_type=comparison_type,
            )
            
            logger.info("✅ Graph comparison completed successfully")
            return report
            
        except Exception as e:
            error_msg = f"❌ Graph comparison failed: {str(e)}"
            logger.error(error_msg)
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return f"Error: {str(e)}"


if __name__ == "__main__":
    # Test the graph comparison tool
    tool = GraphComparisonTool()
    
    # Example usage
    pdf_names = "paper1,paper2,paper3"
    task_name = "benchmark_landscape"
    query = "accuracy comparison curves"
    
    result = tool.forward(
        pdf_names=pdf_names,
        task_name=task_name,
        query=query,
        comparison_type="all",
        top_k=5,
    )
    
    print("🔍 Graph Comparison Result:")
    print("=" * 60)
    print(result)
    print("=" * 60)

