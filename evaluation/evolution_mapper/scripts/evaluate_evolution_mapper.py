"""
Evaluation Pipeline for Evolution Mapper Tool

Uses Connected Papers as ground truth and LLM (ChatGPT) as judge
"""

import json
import os
import sys
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "paperarena_hub"))

from openai import OpenAI
from loguru import logger

from bib_parser import BibParser
from paperarena_hub.tools.evolution_mapper import EvolutionMapper
from paperarena_hub.tools.semantic_scholar_tool import SemanticScholarTool
from paperarena_hub.tools.database_search import DatabaseSearch


class LLMJudge:
    """LLM Judge for evaluating Evolution Mapper output"""
    
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        # ALWAYS use OpenAI's official endpoint for LLM Judge, never Gemini
        # This ensures we use the correct API for evaluation
        base_url = "https://api.openai.com/v1"
        logger.info(f"🔧 LLM Judge using OpenAI official endpoint: {base_url}")
        
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.evaluation_prompt = self._load_evaluation_prompt()
    
    def _load_evaluation_prompt(self) -> str:
        """Load evaluation prompt template"""
        return """You are an expert in academic research and citation network analysis. 
Please compare two technology evolution maps:

**Ground Truth (Connected Papers)**:
{ground_truth}

**Our Tool Output (Evolution Mapper)**:
{our_output}

**Evaluation Criteria**:
1. **Paper Discovery Coverage** (0-10):
   - How many papers from Connected Papers are also in our output?
   - Are key papers from Connected Papers included?
   - Are there important papers missing?

2. **Citation Relationship Accuracy** (0-10):
   - Do citation relationships match Connected Papers?
   - Are the relationships correctly represented?
   - Are there incorrect relationships?

3. **Network Structure Similarity** (0-10):
   - Does the network structure match Connected Papers?
   - Are key papers (high citations) correctly identified?
   - Is the overall topology similar?

4. **Additional Value** (0-10):
   - Does our tool provide additional insights beyond Connected Papers?
   - Are there unique features (e.g., timeline, annotations)?
   - Is the output format more useful?

**Output Format**: JSON
{{
  "scores": {{
    "paper_coverage": 0-10,
    "citation_accuracy": 0-10,
    "network_similarity": 0-10,
    "additional_value": 0-10
  }},
  "overall_score": 0-10,
  "detailed_comparison": {{
    "papers_in_both": ["paper1", "paper2"],
    "papers_only_in_connected_papers": ["paper3"],
    "papers_only_in_our_tool": ["paper4"],
    "citation_relationships_match": true/false,
    "key_differences": ["difference1", "difference2"]
  }},
  "strengths": ["strength1", "strength2"],
  "weaknesses": ["weakness1", "weakness2"],
  "recommendations": ["recommendation1", "recommendation2"]
}}"""
    
    def evaluate(
        self,
        ground_truth: Dict[str, Any],
        our_output: str,
        topic: str
    ) -> Dict[str, Any]:
        """Evaluate our output against ground truth using LLM"""
        # Format ground truth for LLM
        gt_summary = self._format_ground_truth(ground_truth)
        
        prompt = self.evaluation_prompt.format(
            ground_truth=gt_summary,
            our_output=our_output[:8000]  # Limit output size
        )
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert in academic research evaluation. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            result["topic"] = topic
            result["evaluation_timestamp"] = datetime.now().isoformat()
            return result
        
        except Exception as e:
            logger.error(f"LLM evaluation failed: {e}")
            return {
                "error": str(e),
                "topic": topic,
                "evaluation_timestamp": datetime.now().isoformat()
            }
    
    def _format_ground_truth(self, ground_truth: Dict[str, Any]) -> str:
        """Format ground truth data for LLM"""
        papers = ground_truth.get("papers", [])
        key_papers = ground_truth.get("key_papers", [])
        
        summary = f"**Total Papers**: {ground_truth.get('total_papers', 0)}\n"
        summary += f"**Year Range**: {min(ground_truth.get('years', []))} - {max(ground_truth.get('years', []))}\n"
        summary += f"**Key Papers**: {len(key_papers)}\n\n"
        
        summary += "**Key Papers List**:\n"
        for paper_id in key_papers[:10]:  # Top 10 key papers
            paper = next((p for p in papers if p["paper_id"] == paper_id), None)
            if paper:
                summary += f"- {paper['title']} ({paper.get('year', 'N/A')})\n"
        
        summary += "\n**All Papers** (first 20):\n"
        for i, paper in enumerate(papers[:20], 1):
            summary += f"{i}. {paper['title']} ({paper.get('year', 'N/A')})\n"
            if paper.get('authors'):
                summary += f"   Authors: {', '.join(paper['authors'][:3])}\n"
        
        return summary


class EvolutionMapperEvaluator:
    """Main evaluator class"""
    
    def __init__(
        self,
        ground_truth_dir: Path,
        results_dir: Path,
        openai_api_key: str,
        semantic_scholar_api_key: Optional[str] = None
    ):
        self.ground_truth_dir = ground_truth_dir
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize LLM Judge
        self.judge = LLMJudge(api_key=openai_api_key)
        
        # Initialize Evolution Mapper
        if semantic_scholar_api_key:
            semantic_tool = SemanticScholarTool(api_key=semantic_scholar_api_key)
            semantic_tool.setup()
        else:
            semantic_tool = None
        
        database_search = DatabaseSearch()
        database_search.setup()
        
        self.evolution_mapper = EvolutionMapper(
            semantic_tool=semantic_tool,
            database_search=database_search,
        )
        self.evolution_mapper.setup()
        
        # Topic mapping
        self.topic_mapping = {
            "transformers": {
                "bib_file": "ConnectedPapers-for-Attention-is-All-you-Need.bib",
                "topic": "Attention is All You Need, Transformer, BERT, GPT",
                "seed_paper": "Attention is All You Need"
            },
            "gan": {
                "bib_file": "ConnectedPapers-for-Generative-Adversarial-Nets.bib",
                "topic": "Generative Adversarial Networks, GAN, DCGAN, WGAN",
                "seed_paper": "Generative Adversarial Nets"
            },
            "imagenet": {
                "bib_file": "ConnectedPapers-for-ImageNet-classification-with-deep-convolutional-neural-networks.bib",
                "topic": "ImageNet classification, deep convolutional neural networks, AlexNet, ResNet",
                "seed_paper": "ImageNet classification with deep convolutional neural networks"
            }
        }
    
    def load_ground_truth(self, topic: str) -> Dict[str, Any]:
        """Load ground truth data for a topic"""
        if topic not in self.topic_mapping:
            raise ValueError(f"Unknown topic: {topic}. Available: {list(self.topic_mapping.keys())}")
        
        bib_file = self.ground_truth_dir / self.topic_mapping[topic]["bib_file"]
        if not bib_file.exists():
            raise FileNotFoundError(f"Ground truth file not found: {bib_file}")
        
        parser = BibParser(bib_file)
        papers = parser.parse()
        return parser.to_dict()
    
    def run_evolution_mapper(self, topic: str, max_papers: int = 20) -> str:
        """Run Evolution Mapper for a topic"""
        topic_info = self.topic_mapping[topic]
        search_topic = topic_info["topic"]
        
        logger.info(f"Running Evolution Mapper for topic: {search_topic}")
        
        result = self.evolution_mapper.forward(
            topic=search_topic,
            max_papers=max_papers,
        )
        
        return result
    
    def evaluate_topic(
        self, 
        topic: str, 
        max_papers: int = 20, 
        num_evaluations: int = 3
    ) -> Dict[str, Any]:
        """Evaluate Evolution Mapper for a specific topic with multiple runs
        
        Args:
            topic: Topic to evaluate
            max_papers: Maximum number of papers for Evolution Mapper
            num_evaluations: Number of LLM evaluations to run and average (default: 3)
        """
        logger.info(f"Evaluating topic: {topic}")
        
        # Step 1: Load ground truth
        logger.info("Loading ground truth...")
        ground_truth = self.load_ground_truth(topic)
        
        # Step 2: Run Evolution Mapper (only once, as it's deterministic)
        logger.info("Running Evolution Mapper...")
        our_output = self.run_evolution_mapper(topic, max_papers)
        
        # Step 3: Run LLM Judge evaluation multiple times
        logger.info(f"Running LLM Judge evaluation {num_evaluations} times...")
        evaluations = []
        
        for i in range(num_evaluations):
            logger.info(f"  Evaluation run {i+1}/{num_evaluations}...")
            evaluation = self.judge.evaluate(
                ground_truth=ground_truth,
                our_output=our_output,
                topic=topic
            )
            
            # Only add if evaluation was successful (no error)
            if "error" not in evaluation:
                evaluations.append(evaluation)
            else:
                logger.warning(f"  ⚠️  Evaluation run {i+1} failed: {evaluation.get('error', 'Unknown error')}")
        
        if not evaluations:
            logger.error("❌ All evaluations failed!")
            averaged_evaluation = {
                "error": "All LLM evaluations failed",
                "topic": topic,
                "evaluation_timestamp": datetime.now().isoformat()
            }
        else:
            # Step 4: Average the evaluations
            logger.info(f"✅ Successfully completed {len(evaluations)}/{num_evaluations} evaluations")
            averaged_evaluation = self._average_evaluations(evaluations)
        
        # Step 5: Combine results
        result = {
            "topic": topic,
            "seed_paper": self.topic_mapping[topic]["seed_paper"],
            "ground_truth_summary": {
                "total_papers": ground_truth.get("total_papers", 0),
                "year_range": f"{min(ground_truth.get('years', []))} - {max(ground_truth.get('years', []))}",
                "key_papers_count": len(ground_truth.get("key_papers", []))
            },
            "our_output_length": len(our_output),
            "num_evaluations": num_evaluations,
            "successful_evaluations": len(evaluations),
            "evaluation": averaged_evaluation,
            "individual_evaluations": evaluations,  # Save all individual evaluations
            "timestamp": datetime.now().isoformat()
        }
        
        return result
    
    def _average_evaluations(self, evaluations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Average multiple evaluation results"""
        if not evaluations:
            return {}
        
        # Extract scores from all evaluations
        all_scores = {
            "paper_coverage": [],
            "citation_accuracy": [],
            "network_similarity": [],
            "additional_value": [],
            "overall_score": []
        }
        
        # Collect all scores
        for eval_result in evaluations:
            scores = eval_result.get("scores", {})
            for key in all_scores.keys():
                if key == "overall_score":
                    value = eval_result.get(key)
                else:
                    value = scores.get(key)
                
                if value is not None:
                    all_scores[key].append(value)
        
        # Calculate averages
        averaged_scores = {}
        for key, values in all_scores.items():
            if values:
                averaged_scores[key] = sum(values) / len(values)
            else:
                averaged_scores[key] = None
        
        # Combine strengths, weaknesses, and recommendations from all evaluations
        all_strengths = []
        all_weaknesses = []
        all_recommendations = []
        
        for eval_result in evaluations:
            all_strengths.extend(eval_result.get("strengths", []))
            all_weaknesses.extend(eval_result.get("weaknesses", []))
            all_recommendations.extend(eval_result.get("recommendations", []))
        
        # Get most common items (or all unique items)
        from collections import Counter
        common_strengths = [item for item, count in Counter(all_strengths).most_common(5)]
        common_weaknesses = [item for item, count in Counter(all_weaknesses).most_common(5)]
        common_recommendations = [item for item, count in Counter(all_recommendations).most_common(5)]
        
        # Use the most detailed comparison from the evaluations
        detailed_comparison = evaluations[0].get("detailed_comparison", {})
        
        return {
            "scores": {
                "paper_coverage": round(averaged_scores.get("paper_coverage", 0), 2),
                "citation_accuracy": round(averaged_scores.get("citation_accuracy", 0), 2),
                "network_similarity": round(averaged_scores.get("network_similarity", 0), 2),
                "additional_value": round(averaged_scores.get("additional_value", 0), 2),
            },
            "overall_score": round(averaged_scores.get("overall_score", 0), 2),
            "score_std_dev": {
                "paper_coverage": round(self._std_dev(all_scores.get("paper_coverage", [])), 2),
                "citation_accuracy": round(self._std_dev(all_scores.get("citation_accuracy", [])), 2),
                "network_similarity": round(self._std_dev(all_scores.get("network_similarity", [])), 2),
                "additional_value": round(self._std_dev(all_scores.get("additional_value", [])), 2),
                "overall_score": round(self._std_dev(all_scores.get("overall_score", [])), 2),
            },
            "detailed_comparison": detailed_comparison,
            "strengths": common_strengths,
            "weaknesses": common_weaknesses,
            "recommendations": common_recommendations,
            "topic": evaluations[0].get("topic", ""),
            "evaluation_timestamp": datetime.now().isoformat(),
            "num_runs": len(evaluations)
        }
    
    def _std_dev(self, values: List[float]) -> float:
        """Calculate standard deviation"""
        if not values or len(values) < 2:
            return 0.0
        
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5
    
    def save_results(self, result: Dict[str, Any], topic: str):
        """Save evaluation results"""
        output_file = self.results_dir / f"{topic}_evaluation.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Results saved to: {output_file}")
        
        # Also save a summary
        summary_file = self.results_dir / f"{topic}_evaluation_summary.md"
        self._save_summary(result, summary_file)
    
    def _save_summary(self, result: Dict[str, Any], summary_file: Path):
        """Save evaluation summary as Markdown"""
        eval_data = result.get("evaluation", {})
        scores = eval_data.get("scores", {})
        std_dev = eval_data.get("score_std_dev", {})
        num_eval = result.get("num_evaluations", 1)
        successful = result.get("successful_evaluations", 0)
        
        summary = f"""# Evolution Mapper Evaluation Summary

## Topic: {result['topic']}
**Seed Paper**: {result['seed_paper']}
**Evaluation Time**: {result['timestamp']}
**Number of Evaluations**: {successful}/{num_eval} successful

## Ground Truth Summary
- **Total Papers**: {result['ground_truth_summary']['total_papers']}
- **Year Range**: {result['ground_truth_summary']['year_range']}
- **Key Papers**: {result['ground_truth_summary']['key_papers_count']}

## Evaluation Scores (Averaged over {successful} runs)

### Overall Score: {eval_data.get('overall_score', 'N/A')}/10 (std: {std_dev.get('overall_score', 'N/A')})

### Detailed Scores:
- **Paper Coverage**: {scores.get('paper_coverage', 'N/A')}/10 (std: {std_dev.get('paper_coverage', 'N/A')})
- **Citation Accuracy**: {scores.get('citation_accuracy', 'N/A')}/10 (std: {std_dev.get('citation_accuracy', 'N/A')})
- **Network Similarity**: {scores.get('network_similarity', 'N/A')}/10 (std: {std_dev.get('network_similarity', 'N/A')})
- **Additional Value**: {scores.get('additional_value', 'N/A')}/10 (std: {std_dev.get('additional_value', 'N/A')})

## Strengths
"""
        for strength in eval_data.get("strengths", []):
            summary += f"- {strength}\n"
        
        summary += "\n## Weaknesses\n"
        for weakness in eval_data.get("weaknesses", []):
            summary += f"- {weakness}\n"
        
        summary += "\n## Recommendations\n"
        for rec in eval_data.get("recommendations", []):
            summary += f"- {rec}\n"
        
        summary += "\n## Detailed Comparison\n"
        comp = eval_data.get("detailed_comparison", {})
        summary += f"- **Papers in Both**: {len(comp.get('papers_in_both', []))}\n"
        summary += f"- **Papers Only in Connected Papers**: {len(comp.get('papers_only_in_connected_papers', []))}\n"
        summary += f"- **Papers Only in Our Tool**: {len(comp.get('papers_only_in_our_tool', []))}\n"
        summary += f"- **Citation Relationships Match**: {comp.get('citation_relationships_match', 'N/A')}\n"
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(summary)
        
        logger.info(f"Summary saved to: {summary_file}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate Evolution Mapper Tool")
    parser.add_argument(
        "--topic",
        type=str,
        choices=["transformers", "gan", "imagenet", "all"],
        default="all",
        help="Topic to evaluate (default: all)"
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=20,
        help="Maximum number of papers for Evolution Mapper (default: 20)"
    )
    parser.add_argument(
        "--num-evaluations",
        type=int,
        default=3,
        help="Number of LLM evaluations to run and average (default: 3)"
    )
    parser.add_argument(
        "--openai-api-key",
        type=str,
        help="OpenAI API key for LLM Judge (or set OPENAI_API_KEY env var)"
    )
    parser.add_argument(
        "--semantic-scholar-api-key",
        type=str,
        help="Semantic Scholar API key (or set SEMANTIC_SCHOLAR_API_KEY env var)"
    )
    
    args = parser.parse_args()
    
    # Get API keys
    openai_api_key = args.openai_api_key or os.getenv("OPENAI_API_KEY")
    
    # Check if the API key is actually a Gemini key (starts with AIzaSy)
    if openai_api_key and openai_api_key.startswith("AIzaSy"):
        logger.warning("⚠️  OPENAI_API_KEY appears to be a Gemini key, using provided OpenAI key instead")
        openai_api_key = None
    
    if not openai_api_key:
        # Use the provided OpenAI API key from user (ChatGPT API key)
        openai_api_key = "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"
        logger.info("✅ Using OpenAI API key from code (ChatGPT API)")
    else:
        logger.info("✅ Using OpenAI API key from environment variable")
    
    semantic_scholar_api_key = args.semantic_scholar_api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    
    # Setup paths
    script_dir = Path(__file__).parent
    ground_truth_dir = script_dir.parent / "ground_truth"
    results_dir = script_dir.parent / "results"
    
    # Initialize evaluator
    evaluator = EvolutionMapperEvaluator(
        ground_truth_dir=ground_truth_dir,
        results_dir=results_dir,
        openai_api_key=openai_api_key,
        semantic_scholar_api_key=semantic_scholar_api_key
    )
    
    # Run evaluation
    topics = ["transformers", "gan", "imagenet"] if args.topic == "all" else [args.topic]
    
    for topic in topics:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"Evaluating: {topic}")
            logger.info(f"{'='*70}\n")
            
            result = evaluator.evaluate_topic(
                topic, 
                max_papers=args.max_papers,
                num_evaluations=args.num_evaluations
            )
            evaluator.save_results(result, topic)
            
            logger.info(f"\n✅ Evaluation completed for {topic}")
            logger.info(f"Overall Score: {result['evaluation'].get('overall_score', 'N/A')}/10\n")
        
        except Exception as e:
            logger.error(f"❌ Evaluation failed for {topic}: {e}")
            import traceback
            logger.error(traceback.format_exc())


if __name__ == "__main__":
    main()

