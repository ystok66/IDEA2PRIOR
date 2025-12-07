"""
Evaluation Pipeline for Code Extractor Tool

Uses manually annotated ground truth and LLM (ChatGPT) as judge
"""

import json
import os
import sys
import argparse
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "paperarena_hub"))

from openai import OpenAI
from loguru import logger

from paperarena_hub.tools.code_extractor import CodeExtractorTool
from paperarena_hub.tools.pdf_extractor import PDFExtractorRAGTool
from paperarena_hub.tools.semantic_scholar_tool import SemanticScholarTool


class LLMJudge:
    """LLM Judge for evaluating Code Extractor output against ground truth"""
    
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        # ALWAYS use OpenAI's official endpoint for LLM Judge, never Gemini
        base_url = "https://api.openai.com/v1"
        logger.info(f"🔧 LLM Judge using OpenAI official endpoint: {base_url}")
        
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.evaluation_prompt = self._load_evaluation_prompt()
    
    def evaluate_standalone(self, our_output: str, paper_title: str) -> Dict[str, Any]:
        """Standalone evaluation of Code Extractor output (without ground truth)"""
        prompt = self._load_standalone_evaluation_prompt()
        formatted_prompt = prompt.format(
            paper_title=paper_title,
            our_output=our_output[:8000]
        )
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert in code analysis and research paper reproducibility evaluation. Always respond with valid JSON."},
                    {"role": "user", "content": formatted_prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            result["paper_title"] = paper_title
            result["evaluation_timestamp"] = datetime.now().isoformat()
            result["evaluation_type"] = "standalone"
            return result
        
        except Exception as e:
            logger.error(f"Standalone LLM evaluation failed: {e}")
            return {
                "error": str(e),
                "paper_title": paper_title,
                "evaluation_timestamp": datetime.now().isoformat(),
                "evaluation_type": "standalone"
            }
    
    def _load_standalone_evaluation_prompt(self) -> str:
        """Load standalone evaluation prompt (without ground truth)"""
        return """You are an expert in code analysis and research paper reproducibility evaluation.
Please evaluate the Code Extractor tool output for the following paper:

**Paper Title**: {paper_title}

**Code Extractor Output**:
{our_output}

**Evaluation Criteria** (0-10 scale for each):

1. **Code/Pseudocode Extraction Quality** (0-10):
   - Are code blocks OR pseudocode blocks correctly identified and extracted?
   - Is the code syntax correct (if executable code exists)?
   - Are code/pseudocode blocks complete and usable?
   - **IMPORTANT**: If pseudocode is extracted (even if incomplete), give at least 3 points. Only give 0-2 points if NO code or pseudocode is found at all.
   - Score guidelines:
     * 0-2: No code or pseudocode extracted, or extraction completely failed
     * 3-4: Pseudocode extracted but very incomplete (missing key steps) - MINIMUM score if any pseudocode found
     * 5-6: Pseudocode extracted and partially complete (has main steps but missing details)
     * 7-8: Pseudocode extracted and mostly complete, OR incomplete code blocks extracted
     * 9-10: Complete pseudocode with all details, OR complete executable code blocks

2. **Pseudocode Completeness** (0-10):
   - Are pseudocode blocks identified?
   - Do they have clear input/output?
   - Are the steps complete and logical?

3. **Reproducibility Assessment** (0-10):
   - Can the code/pseudocode be used to reproduce the paper?
   - Are there enough details for implementation?
   - What is the estimated reproducibility score (1-3 scale)?

4. **Dependency Identification** (0-10):
   - Are dependencies accurately listed?
   - Are the identifications comprehensive?

5. **Model Structure Analysis** (0-10):
   - Is the model structure correctly analyzed?
   - Are key details (layers, parameters) captured?
   - Is the analysis detailed and accurate?

6. **Missing Components Detection** (0-10):
   - Are missing components correctly identified?
   - Is the list comprehensive?
   - Are the identifications accurate?

7. **Overall Quality** (0-10):
   - Overall quality of the extraction and analysis
   - How useful is the output for researchers?

**Output Format**: JSON
{{
  "scores": {{
    "code_pseudocode_extraction_quality": 0-10,
    "pseudocode_completeness": 0-10,
    "reproducibility_assessment": 0-10,
    "dependency_identification": 0-10,
    "model_structure_analysis": 0-10,
    "missing_components_detection": 0-10,
    "overall_quality": 0-10
  }},
  "overall_score": 0-10,
  "estimated_pseudocode_completeness": 0-2,
  "estimated_reproducibility": 1-3,
  "identified_dependencies": ["dep1", "dep2"],
  "identified_missing_components": ["component1", "component2"],
  "model_structure_summary": {{
    "description": "description",
    "layers": "value",
    "parameters": "value"
  }},
  "strengths": ["strength1", "strength2"],
  "weaknesses": ["weakness1", "weakness2"],
  "recommendations": ["recommendation1", "recommendation2"]
}}"""
    
    def _load_evaluation_prompt(self) -> str:
        """Load evaluation prompt template"""
        return """You are an expert in code analysis and research paper reproducibility evaluation.
Please compare the Code Extractor tool output with the ground truth annotation:

**Ground Truth**:
{ground_truth}

**Our Tool Output**:
{our_output}

**IMPORTANT - Value Mapping Rules**:
- Tool outputs completeness as 0-1.0, but ground truth uses 0-2. Map tool value: multiply by 2.
- Tool outputs reproducibility as 0-1.0, but ground truth uses 1-3. Map tool value: multiply by 2, then add 1.
- Example: Tool completeness 0.8 → mapped to 1.6 (round to 2). Tool reproducibility 0.0 → mapped to 1.0 (round to 1), but if tool estimates 1.0 in standalone evaluation, use 3.

**Evaluation Criteria**:
1. **Pseudocode Completeness Match** (0-10):
   - Extract tool's completeness value from output (look for "Completeness score" or "Overall completeness").
   - Map tool value (0-1.0) to ground truth range (0-2): multiply by 2.
   - Compare mapped value with ground truth pseudocode_completeness (0-2).
   - Score: 10 if perfect match (within 0.2), 7-8 if close match (within 0.5), 5 if partial match (within 1.0), 0 if no match

2. **Reproducibility Score Match** (0-10):
   - Extract tool's reproducibility value from output (look for "Reproducibility score").
   - Map tool value (0-1.0) to ground truth range (1-3): multiply by 2, then add 1.
   - Compare mapped value with ground truth reproducibility (1-3).
   - Score: 10 if perfect match (exact or within 0.2), 7-8 if close match (within 0.5), 5 if partial match (within 1.0), 0 if no match

3. **Dependency Identification** (0-10):
   - Extract tool's identified dependencies from output.
   - Compare with ground truth dependencies list.
   - If both are empty, give 10/10 (perfect match).
   - Score: 10 if perfect match (both empty or identical), 7-8 if high precision/recall (>0.8), 5 if moderate (>0.5), 0 if no match

5. **Model Structure Analysis** (0-10):
   - Does the model structure analysis match ground truth?
   - Are key details (description, layers, parameters) captured?
   - Score based on how well it matches

6. **Missing Components Detection** (0-10):
   - Are missing components correctly identified?
   - Do they match ground truth missing_components?
   - Score based on precision and recall

**Output Format**: JSON
{{
  "scores": {{
    "pseudocode_completeness_match": 0-10,
    "reproducibility_score_match": 0-10,
    "dependency_identification": 0-10,
    "model_structure_analysis": 0-10,
    "missing_components_detection": 0-10
  }},
  "overall_score": 0-10,
  "detailed_comparison": {{
    "pseudocode_completeness": {{
      "ground_truth": 0-2,
      "our_tool": "value or N/A",
      "match": true/false
    }},
    "reproducibility": {{
      "ground_truth": 1-3,
      "our_tool": "value or N/A",
      "match": true/false
    }},
    "dependencies": {{
      "ground_truth": ["dep1", "dep2"],
      "our_tool": ["dep1"],
      "precision": 0.0-1.0,
      "recall": 0.0-1.0
    }},
    "missing_components": {{
      "ground_truth": ["component1"],
      "our_tool": ["component1"],
      "precision": 0.0-1.0,
      "recall": 0.0-1.0
    }}
  }},
  "strengths": ["strength1", "strength2"],
  "weaknesses": ["weakness1", "weakness2"],
  "recommendations": ["recommendation1", "recommendation2"]
}}"""
    
    def evaluate(
        self,
        ground_truth: Dict[str, Any],
        our_output: str,
        paper_title: str
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
                    {"role": "system", "content": "You are an expert in code analysis evaluation. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            result["paper_title"] = paper_title
            result["evaluation_timestamp"] = datetime.now().isoformat()
            return result
        
        except Exception as e:
            logger.error(f"LLM evaluation failed: {e}")
            return {
                "error": str(e),
                "paper_title": paper_title,
                "evaluation_timestamp": datetime.now().isoformat()
            }
    
    def _format_ground_truth(self, ground_truth: Dict[str, Any]) -> str:
        """Format ground truth data for LLM"""
        summary = f"""**Paper Title**: {ground_truth.get('title', 'N/A')}
**ID**: {ground_truth.get('id', 'N/A')}

**Pseudocode Completeness**: {ground_truth.get('pseudocode_completeness', 'N/A')}/2
**Reproducibility**: {ground_truth.get('reproducibility', 'N/A')}/3

**Frameworks**: {', '.join(ground_truth.get('frameworks', [])) or 'None'}
**Dependencies**: {', '.join(ground_truth.get('dependencies', [])) or 'None'}

**Model Structure**:
{json.dumps(ground_truth.get('model_structure', {}), indent=2, ensure_ascii=False)}

**Missing Components**:
{', '.join(ground_truth.get('missing_components', [])) or 'None'}

**Notes**: {ground_truth.get('notes', 'N/A')}
"""
        return summary


class CodeExtractorOutputParser:
    """Parser for Code Extractor tool output"""
    
    @staticmethod
    def parse_output(output: str) -> Dict[str, Any]:
        """Parse Code Extractor tool output to extract key information"""
        result = {
            "pseudocode_completeness": None,
            "reproducibility_score": None,
            "dependencies": [],
            "model_structure": {},
            "missing_components": [],
            "classification": None
        }
        
        # Extract pseudocode completeness (try multiple patterns)
        # Tool outputs 0-1.0, but ground truth uses 0-2, so we need to map
        completeness_match = re.search(r'(?:Pseudocode completeness|Completeness score|Overall completeness)[:\s]+([0-9.]+)', output, re.IGNORECASE)
        if completeness_match:
            completeness_value = float(completeness_match.group(1))
            # Map 0-1.0 to 0-2 (multiply by 2)
            # If value is already in 0-2 range (e.g., from a different source), keep it
            if completeness_value <= 1.0:
                result["pseudocode_completeness"] = completeness_value * 2
            else:
                result["pseudocode_completeness"] = completeness_value
        
        # Extract reproducibility score (try multiple patterns)
        # Tool outputs 0-1.0, but ground truth uses 1-3, so we need to map
        repro_match = re.search(r'(?:Reproducibility score|Reproducibility)[:\s]+([0-9.]+)', output, re.IGNORECASE)
        if repro_match:
            repro_value = float(repro_match.group(1))
            # Map 0-1.0 to 1-3: value * 2 + 1
            # If value is already in 1-3 range (e.g., from a different source), keep it
            if repro_value <= 1.0:
                result["reproducibility_score"] = repro_value * 2 + 1
            else:
                result["reproducibility_score"] = repro_value
        
        # Extract dependencies (IMPROVED: Multiple patterns, handle "None", avoid matching section titles)
        deps_patterns = [
            r'\*\*Identified dependencies\*\*[:\s]+([^\n]+?)(?:\n|$)',
            r'Identified dependencies[:\s]+([^\n]+?)(?:\n|$)',
            r'Dependencies[:\s]+([^\n]+?)(?:\n|$)',
        ]
        for pattern in deps_patterns:
            deps_match = re.search(pattern, output, re.IGNORECASE)
            if deps_match:
                deps_str = deps_match.group(1).strip()
                # Handle "None" case and avoid matching section titles
                if deps_str.lower() not in ['none', 'n/a', '', 'and dependency analysis', 'framework and dependency analysis']:
                    # Split by comma and filter
                    dependencies = [d.strip() for d in deps_str.split(',') if d.strip() and d.strip().lower() not in ['none', 'n/a', 'and dependency analysis']]
                    if dependencies:
                        result["dependencies"] = dependencies
                        break
        
        # Extract missing components (IMPROVED: Prioritize LLM Evaluation Results section)
        # First try to extract from LLM Evaluation Results section
        llm_section_match = re.search(
            r'## 🤖 LLM Evaluation Results.*?Missing components[:\s]+([^\n]+)',
            output,
            re.DOTALL | re.IGNORECASE
        )
        if llm_section_match:
            missing_str = llm_section_match.group(1).strip()
            if missing_str.lower() not in ['none', 'n/a', '']:
                result["missing_components"] = [m.strip() for m in missing_str.split(',') if m.strip()]
        else:
            # Fallback to Completeness Check section
            completeness_section_match = re.search(
                r'## ✅ Completeness Check.*?Missing components[:\s]+([^\n]+)',
                output,
                re.DOTALL | re.IGNORECASE
            )
            if completeness_section_match:
                missing_str = completeness_section_match.group(1).strip()
                if missing_str.lower() not in ['none', 'n/a', '']:
                    result["missing_components"] = [m.strip() for m in missing_str.split(',') if m.strip()]
            else:
                # Last resort: any "Missing components" match
                missing_match = re.search(r'Missing components[:\s]+([^\n]+)', output, re.IGNORECASE)
                if missing_match:
                    missing_str = missing_match.group(1).strip()
                    if missing_str.lower() not in ['none', 'n/a', '']:
                        result["missing_components"] = [m.strip() for m in missing_str.split(',') if m.strip()]
        
        # Extract model structure (IMPROVED: Extract from code block)
        # Model structure is in a code block under "Framework and Dependency Analysis"
        model_structure_patterns = [
            r'## 🔧 Framework and Dependency Analysis.*?Model structure[:\s]*\n\s*```python\s*\n\s*(\{.*?\})\s*\n\s*```',
            r'Model structure[:\s]*\n\s*```python\s*\n\s*(\{.*?\})\s*\n\s*```',
            r'Model structure[:\s]*\n\s*```json\s*\n\s*(\{.*?\})\s*\n\s*```',
            r'\*\*Model structure\*\*[:\s]*\n\s*```python\s*\n\s*(\{.*?\})\s*\n\s*```',
        ]
        for pattern in model_structure_patterns:
            model_match = re.search(pattern, output, re.DOTALL | re.IGNORECASE)
            if model_match:
                try:
                    model_json_str = model_match.group(1).strip()
                    result["model_structure"] = json.loads(model_json_str)
                    break
                except (json.JSONDecodeError, AttributeError):
                    continue
        
        # Extract classification
        if "📗" in output or "Complete code" in output:
            result["classification"] = "📗 Complete"
        elif "📙" in output or "Partial code" in output:
            result["classification"] = "📙 Partial"
        elif "📕" in output or "No code" in output:
            result["classification"] = "📕 None"
        
        return result


class CodeExtractorEvaluator:
    """Main evaluator class"""
    
    def __init__(
        self,
        ground_truth_file: Path,
        results_dir: Path,
        openai_api_key: str,
        pdf_dir: Optional[Path] = None,
        semantic_scholar_api_key: Optional[str] = None
    ):
        self.ground_truth_file = ground_truth_file
        self.results_dir = results_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir = pdf_dir
        
        # Initialize LLM Judge
        self.judge = LLMJudge(api_key=openai_api_key)
        
        # Initialize Semantic Scholar (for paper search)
        if semantic_scholar_api_key:
            self.semantic_tool = SemanticScholarTool(api_key=semantic_scholar_api_key)
            self.semantic_tool.setup()
        else:
            self.semantic_tool = None
        
        # Initialize Code Extractor with correct paths
        # Set output_base_dir for markdown files (where PDFs are parsed to)
        output_base_dir = project_root / "evaluation" / "code_extractor" / "temp_output"
        output_base_dir.mkdir(parents=True, exist_ok=True)
        
        pdf_extractor = PDFExtractorRAGTool(
            pdf_base_dir=pdf_dir if pdf_dir else (project_root / "evaluation" / "code_extractor" / "pdfs"),
            output_base_dir=output_base_dir
        )
        pdf_extractor.setup()
        
        self.code_extractor = CodeExtractorTool(
            pdf_extractor=pdf_extractor,
            pdf_base_dir=pdf_dir if pdf_dir else (project_root / "evaluation" / "code_extractor" / "pdfs"),
            output_base_dir=output_base_dir
        )
        self.code_extractor.setup()
        
        # Initialize parser
        self.parser = CodeExtractorOutputParser()
        
        # Load ground truth
        with open(ground_truth_file, 'r', encoding='utf-8') as f:
            self.ground_truth_data = json.load(f)
    
    def find_pdf_for_paper(self, paper_title: str) -> Optional[str]:
        """Find PDF file for a paper title"""
        if not self.pdf_dir or not self.pdf_dir.exists():
            return None
        
        # Normalize title for matching
        def normalize_text(text: str) -> str:
            """Normalize text for comparison"""
            # Remove special characters, convert to lowercase, normalize spaces
            text = text.lower()
            # Replace various separators with spaces
            for char in ['-', '_', ':', ',', '.', '‑']:  # Note: includes non-breaking hyphen
                text = text.replace(char, ' ')
            # Normalize multiple spaces
            text = ' '.join(text.split())
            return text
        
        title_normalized = normalize_text(paper_title)
        title_words = [w for w in title_normalized.split() if len(w) > 2]  # Get words longer than 2 chars
        
        best_match = None
        best_score = 0
        
        for pdf_file in self.pdf_dir.glob("*.pdf"):
            pdf_name_normalized = normalize_text(pdf_file.stem)
            
            # Strategy 1: Exact or near-exact match (high priority)
            if title_normalized == pdf_name_normalized:
                logger.info(f"✅ Exact match found: {pdf_file.name}")
                return pdf_file.stem
            
            # Strategy 2: Substring match (one contains the other)
            if title_normalized in pdf_name_normalized or pdf_name_normalized in title_normalized:
                # Calculate match score based on length ratio
                match_ratio = min(len(title_normalized), len(pdf_name_normalized)) / max(len(title_normalized), len(pdf_name_normalized))
                if match_ratio > 0.8:  # At least 80% match
                    logger.info(f"✅ Substring match found (ratio: {match_ratio:.2f}): {pdf_file.name}")
                    return pdf_file.stem
            
            # Strategy 3: Word-based matching with scoring
            if len(title_words) > 0:
                matched_words = []
                for word in title_words:
                    if word in pdf_name_normalized:
                        matched_words.append(word)
                
                if len(matched_words) > 0:
                    # Calculate match score: percentage of words matched, weighted by word importance
                    # Important words (longer, more unique) get higher weight
                    word_weights = {word: len(word) for word in title_words}
                    total_weight = sum(word_weights.values())
                    matched_weight = sum(word_weights[word] for word in matched_words)
                    match_score = matched_weight / total_weight if total_weight > 0 else 0
                    
                    # Require at least 60% of words matched, and at least 3 words for short titles
                    min_words_required = max(3, len(title_words) * 0.6)
                    if len(matched_words) >= min_words_required and match_score > best_score:
                        best_match = pdf_file.stem
                        best_score = match_score
        
        if best_match and best_score > 0.6:  # At least 60% match score
            logger.info(f"✅ Best word match found (score: {best_score:.2f}): {best_match}")
            return best_match
        
        return None
    
    def run_code_extractor(self, paper_title: str, pdf_name: Optional[str] = None) -> str:
        """Run Code Extractor for a paper"""
        if not pdf_name:
            pdf_name = self.find_pdf_for_paper(paper_title)
        
        if not pdf_name:
            # Try to find paper via Semantic Scholar and get PDF URL
            if self.semantic_tool:
                logger.info(f"🔍 Searching Semantic Scholar for: {paper_title}")
                try:
                    papers = self.semantic_tool.search_papers(
                        query=paper_title,
                        limit=1,
                        fields="paperId,title,abstract,year,authors,openAccessPdf"
                    )
                    if papers:
                        paper = papers[0]
                        pdf_url = paper.get("openAccessPdf", {}).get("url") if isinstance(paper.get("openAccessPdf"), dict) else None
                        if pdf_url:
                            logger.info(f"✅ Found open access PDF: {pdf_url}")
                            # Note: We would need to download the PDF here
                            # For now, we'll proceed with what we have
                        else:
                            logger.warning(f"⚠️  No open access PDF found for: {paper_title}")
                except Exception as e:
                    logger.warning(f"⚠️  Semantic Scholar search failed: {e}")
            
            logger.warning(f"⚠️  PDF not found for paper: {paper_title}")
            logger.info("💡 Note: Code Extractor requires PDF files. Without PDF, evaluation will be limited.")
            return f"PDF not found for paper: {paper_title}. Code Extractor requires PDF files to extract code."
        
        logger.info(f"Running Code Extractor for: {paper_title} (PDF: {pdf_name})")
        
        try:
            result = self.code_extractor.forward(
                pdf_name=pdf_name,
                query=""
            )
            return result
        except Exception as e:
            logger.error(f"Code Extractor failed: {e}")
            return f"Error: {str(e)}"
    
    def evaluate_paper(
        self, 
        paper_id: int,
        num_evaluations: int = 3
    ) -> Dict[str, Any]:
        """Evaluate Code Extractor for a specific paper"""
        # Find ground truth for this paper
        ground_truth = next((gt for gt in self.ground_truth_data if gt.get("id") == paper_id), None)
        if not ground_truth:
            raise ValueError(f"Paper ID {paper_id} not found in ground truth")
        
        paper_title = ground_truth.get("title", "")
        logger.info(f"Evaluating paper: {paper_title}")
        
        # Step 1: Run Code Extractor
        logger.info("Running Code Extractor...")
        our_output = self.run_code_extractor(paper_title)
        
        # Step 2: Parse output
        parsed_output = self.parser.parse_output(our_output)
        
        # Step 3: Run Standalone LLM Judge evaluation (without ground truth)
        logger.info("Running Standalone LLM Judge evaluation...")
        standalone_evaluations = []
        
        for i in range(num_evaluations):
            logger.info(f"  Standalone evaluation run {i+1}/{num_evaluations}...")
            standalone_eval = self.judge.evaluate_standalone(
                our_output=our_output,
                paper_title=paper_title
            )
            
            if "error" not in standalone_eval:
                standalone_evaluations.append(standalone_eval)
            else:
                logger.warning(f"  ⚠️  Standalone evaluation run {i+1} failed: {standalone_eval.get('error', 'Unknown error')}")
        
        # Step 4: Run Comparison LLM Judge evaluation (with ground truth)
        logger.info(f"Running Comparison LLM Judge evaluation {num_evaluations} times...")
        comparison_evaluations = []
        
        for i in range(num_evaluations):
            logger.info(f"  Comparison evaluation run {i+1}/{num_evaluations}...")
            comparison_eval = self.judge.evaluate(
                ground_truth=ground_truth,
                our_output=our_output,
                paper_title=paper_title
            )
            
            if "error" not in comparison_eval:
                comparison_evaluations.append(comparison_eval)
            else:
                logger.warning(f"  ⚠️  Comparison evaluation run {i+1} failed: {comparison_eval.get('error', 'Unknown error')}")
        
        # Step 5: Average evaluations
        if not standalone_evaluations and not comparison_evaluations:
            logger.error("❌ All evaluations failed!")
            averaged_standalone = {
                "error": "All standalone LLM evaluations failed",
                "paper_title": paper_title,
                "evaluation_timestamp": datetime.now().isoformat()
            }
            averaged_comparison = {
                "error": "All comparison LLM evaluations failed",
                "paper_title": paper_title,
                "evaluation_timestamp": datetime.now().isoformat()
            }
        else:
            if standalone_evaluations:
                logger.info(f"✅ Successfully completed {len(standalone_evaluations)}/{num_evaluations} standalone evaluations")
                averaged_standalone = self._average_standalone_evaluations(standalone_evaluations)
            else:
                averaged_standalone = {}
            
            if comparison_evaluations:
                logger.info(f"✅ Successfully completed {len(comparison_evaluations)}/{num_evaluations} comparison evaluations")
                averaged_comparison = self._average_evaluations(comparison_evaluations)
            else:
                averaged_comparison = {}
        
        # Step 6: Combine results
        result = {
            "paper_id": paper_id,
            "paper_title": paper_title,
            "ground_truth": ground_truth,
            "our_output_length": len(our_output),
            "parsed_output": parsed_output,
            "num_evaluations": num_evaluations,
            "standalone_evaluation": {
                "successful_evaluations": len(standalone_evaluations),
                "averaged_result": averaged_standalone,
                "individual_evaluations": standalone_evaluations
            },
            "comparison_evaluation": {
                "successful_evaluations": len(comparison_evaluations),
                "averaged_result": averaged_comparison,
                "individual_evaluations": comparison_evaluations
            },
            "timestamp": datetime.now().isoformat()
        }
        
        return result
    
    def _average_standalone_evaluations(self, evaluations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Average multiple standalone evaluation results"""
        if not evaluations:
            return {}
        
        # Extract scores from all evaluations
        # Support both old and new field names for backward compatibility
        all_scores = {
            "code_pseudocode_extraction_quality": [],
            "code_extraction_quality": [],  # Keep for backward compatibility
            "pseudocode_completeness": [],
            "reproducibility_assessment": [],
            "dependency_identification": [],
            "model_structure_analysis": [],
            "missing_components_detection": [],
            "overall_quality": [],
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
        
        # Aggregate other fields
        all_dependencies = []
        all_missing = []
        all_pseudocode_completeness = []
        all_reproducibility = []
        
        for eval_result in evaluations:
            all_dependencies.extend(eval_result.get("identified_dependencies", []))
            all_missing.extend(eval_result.get("identified_missing_components", []))
            if eval_result.get("estimated_pseudocode_completeness") is not None:
                all_pseudocode_completeness.append(eval_result.get("estimated_pseudocode_completeness"))
            if eval_result.get("estimated_reproducibility") is not None:
                all_reproducibility.append(eval_result.get("estimated_reproducibility"))
        
        from collections import Counter
        common_dependencies = [item for item, count in Counter(all_dependencies).most_common(10)]
        common_missing = [item for item, count in Counter(all_missing).most_common(10)]
        
        avg_pseudocode = sum(all_pseudocode_completeness) / len(all_pseudocode_completeness) if all_pseudocode_completeness else None
        avg_reproducibility = sum(all_reproducibility) / len(all_reproducibility) if all_reproducibility else None
        
        # Get most common strengths, weaknesses, recommendations
        all_strengths = []
        all_weaknesses = []
        all_recommendations = []
        
        for eval_result in evaluations:
            all_strengths.extend(eval_result.get("strengths", []))
            all_weaknesses.extend(eval_result.get("weaknesses", []))
            all_recommendations.extend(eval_result.get("recommendations", []))
        
        common_strengths = [item for item, count in Counter(all_strengths).most_common(5)]
        common_weaknesses = [item for item, count in Counter(all_weaknesses).most_common(5)]
        common_recommendations = [item for item, count in Counter(all_recommendations).most_common(5)]
        
        # Use the most detailed model structure from evaluations
        model_structure = evaluations[0].get("model_structure_summary", {})
        
        # Use new field name, fallback to old if new doesn't exist
        code_quality_score = (
            averaged_scores.get("code_pseudocode_extraction_quality") or 
            averaged_scores.get("code_extraction_quality") or 
            0
        )
        
        return {
            "scores": {
                "code_pseudocode_extraction_quality": round(code_quality_score, 2),
                "code_extraction_quality": round(code_quality_score, 2),  # Keep for backward compatibility
                "pseudocode_completeness": round(averaged_scores.get("pseudocode_completeness", 0), 2),
                "reproducibility_assessment": round(averaged_scores.get("reproducibility_assessment", 0), 2),
                "dependency_identification": round(averaged_scores.get("dependency_identification", 0), 2),
                "model_structure_analysis": round(averaged_scores.get("model_structure_analysis", 0), 2),
                "missing_components_detection": round(averaged_scores.get("missing_components_detection", 0), 2),
                "overall_quality": round(averaged_scores.get("overall_quality", 0), 2),
            },
            "overall_score": round(averaged_scores.get("overall_score", 0), 2),
            "estimated_pseudocode_completeness": round(avg_pseudocode, 2) if avg_pseudocode else None,
            "estimated_reproducibility": round(avg_reproducibility, 2) if avg_reproducibility else None,
            "identified_dependencies": common_dependencies,
            "identified_missing_components": common_missing,
            "model_structure_summary": model_structure,
            "strengths": common_strengths,
            "weaknesses": common_weaknesses,
            "recommendations": common_recommendations,
            "paper_title": evaluations[0].get("paper_title", ""),
            "evaluation_timestamp": datetime.now().isoformat(),
            "num_runs": len(evaluations)
        }
    
    def _average_evaluations(self, evaluations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Average multiple evaluation results"""
        if not evaluations:
            return {}
        
        # Extract scores from all evaluations
        all_scores = {
            "pseudocode_completeness_match": [],
            "reproducibility_score_match": [],
            "dependency_identification": [],
            "model_structure_analysis": [],
            "missing_components_detection": [],
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
        
        # Combine strengths, weaknesses, and recommendations
        all_strengths = []
        all_weaknesses = []
        all_recommendations = []
        
        for eval_result in evaluations:
            all_strengths.extend(eval_result.get("strengths", []))
            all_weaknesses.extend(eval_result.get("weaknesses", []))
            all_recommendations.extend(eval_result.get("recommendations", []))
        
        # Get most common items
        from collections import Counter
        common_strengths = [item for item, count in Counter(all_strengths).most_common(5)]
        common_weaknesses = [item for item, count in Counter(all_weaknesses).most_common(5)]
        common_recommendations = [item for item, count in Counter(all_recommendations).most_common(5)]
        
        # Use the most detailed comparison from the evaluations
        detailed_comparison = evaluations[0].get("detailed_comparison", {})
        
        return {
            "scores": {
                "pseudocode_completeness_match": round(averaged_scores.get("pseudocode_completeness_match", 0), 2),
                "reproducibility_score_match": round(averaged_scores.get("reproducibility_score_match", 0), 2),
                "dependency_identification": round(averaged_scores.get("dependency_identification", 0), 2),
                "model_structure_analysis": round(averaged_scores.get("model_structure_analysis", 0), 2),
                "missing_components_detection": round(averaged_scores.get("missing_components_detection", 0), 2),
            },
            "overall_score": round(averaged_scores.get("overall_score", 0), 2),
            "score_std_dev": {
                "pseudocode_completeness_match": round(self._std_dev(all_scores.get("pseudocode_completeness_match", [])), 2),
                "reproducibility_score_match": round(self._std_dev(all_scores.get("reproducibility_score_match", [])), 2),
                "dependency_identification": round(self._std_dev(all_scores.get("dependency_identification", [])), 2),
                "model_structure_analysis": round(self._std_dev(all_scores.get("model_structure_analysis", [])), 2),
                "missing_components_detection": round(self._std_dev(all_scores.get("missing_components_detection", [])), 2),
                "overall_score": round(self._std_dev(all_scores.get("overall_score", [])), 2),
            },
            "detailed_comparison": detailed_comparison,
            "strengths": common_strengths,
            "weaknesses": common_weaknesses,
            "recommendations": common_recommendations,
            "paper_title": evaluations[0].get("paper_title", ""),
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
    
    def save_results(self, result: Dict[str, Any], paper_id: int):
        """Save evaluation results"""
        output_file = self.results_dir / f"paper_{paper_id}_evaluation.json"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Results saved to: {output_file}")
        
        # Also save a summary
        summary_file = self.results_dir / f"paper_{paper_id}_evaluation_summary.md"
        self._save_summary(result, summary_file)
    
    def _save_summary(self, result: Dict[str, Any], summary_file: Path):
        """Save evaluation summary as Markdown"""
        standalone_eval = result.get("standalone_evaluation", {}).get("averaged_result", {})
        comparison_eval = result.get("comparison_evaluation", {}).get("averaged_result", {})
        standalone_scores = standalone_eval.get("scores", {})
        comparison_scores = comparison_eval.get("scores", {})
        num_eval = result.get("num_evaluations", 1)
        standalone_successful = result.get("standalone_evaluation", {}).get("successful_evaluations", 0)
        comparison_successful = result.get("comparison_evaluation", {}).get("successful_evaluations", 0)
        ground_truth = result.get("ground_truth", {})
        
        summary = f"""# Code Extractor Evaluation Summary

## Paper: {result['paper_title']}
**Paper ID**: {result['paper_id']}
**Evaluation Time**: {result['timestamp']}

## Ground Truth Summary
- **Pseudocode Completeness**: {ground_truth.get('pseudocode_completeness', 'N/A')}/2
- **Reproducibility**: {ground_truth.get('reproducibility', 'N/A')}/3
- **Dependencies**: {len(ground_truth.get('dependencies', []))} dependencies

---

## 📊 Standalone Evaluation (Independent Assessment)

**Number of Evaluations**: {standalone_successful}/{num_eval} successful

### Overall Score: {standalone_eval.get('overall_score', 'N/A')}/10

### Detailed Scores:
- **Code/Pseudocode Extraction Quality**: {standalone_scores.get('code_pseudocode_extraction_quality') or standalone_scores.get('code_extraction_quality', 'N/A')}/10
- **Pseudocode Completeness**: {standalone_scores.get('pseudocode_completeness', 'N/A')}/10
- **Reproducibility Assessment**: {standalone_scores.get('reproducibility_assessment', 'N/A')}/10
- **Dependency Identification**: {standalone_scores.get('dependency_identification', 'N/A')}/10
- **Model Structure Analysis**: {standalone_scores.get('model_structure_analysis', 'N/A')}/10
- **Missing Components Detection**: {standalone_scores.get('missing_components_detection', 'N/A')}/10
- **Overall Quality**: {standalone_scores.get('overall_quality', 'N/A')}/10

### LLM Estimates:
- **Estimated Pseudocode Completeness**: {standalone_eval.get('estimated_pseudocode_completeness', 'N/A')}/2
- **Estimated Reproducibility**: {standalone_eval.get('estimated_reproducibility', 'N/A')}/3
- **Identified Dependencies**: {len(standalone_eval.get('identified_dependencies', []))} dependencies

### Standalone Strengths
"""
        for strength in standalone_eval.get("strengths", []):
            summary += f"- {strength}\n"
        
        summary += "\n### Standalone Weaknesses\n"
        for weakness in standalone_eval.get("weaknesses", []):
            summary += f"- {weakness}\n"
        
        summary += "\n### Standalone Recommendations\n"
        for rec in standalone_eval.get("recommendations", []):
            summary += f"- {rec}\n"
        
        summary += f"""

---

## 🔍 Comparison Evaluation (vs Ground Truth)

**Number of Evaluations**: {comparison_successful}/{num_eval} successful

### Overall Score: {comparison_eval.get('overall_score', 'N/A')}/10

### Detailed Scores:
- **Pseudocode Completeness Match**: {comparison_scores.get('pseudocode_completeness_match', 'N/A')}/10
- **Reproducibility Score Match**: {comparison_scores.get('reproducibility_score_match', 'N/A')}/10
- **Dependency Identification**: {comparison_scores.get('dependency_identification', 'N/A')}/10
- **Model Structure Analysis**: {comparison_scores.get('model_structure_analysis', 'N/A')}/10
- **Missing Components Detection**: {comparison_scores.get('missing_components_detection', 'N/A')}/10

### Comparison Strengths
"""
        for strength in comparison_eval.get("strengths", []):
            summary += f"- {strength}\n"
        
        summary += "\n### Comparison Weaknesses\n"
        for weakness in comparison_eval.get("weaknesses", []):
            summary += f"- {weakness}\n"
        
        summary += "\n### Comparison Recommendations\n"
        for rec in comparison_eval.get("recommendations", []):
            summary += f"- {rec}\n"
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(summary)
        
        logger.info(f"Summary saved to: {summary_file}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate Code Extractor Tool")
    parser.add_argument(
        "--paper-id",
        type=int,
        help="Specific paper ID to evaluate (1-10), or omit to evaluate all"
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
        "--pdf-dir",
        type=str,
        default="evaluation/code_extractor/pdfs",
        help="Directory containing PDF files (default: evaluation/code_extractor/pdfs)"
    )
    parser.add_argument(
        "--semantic-scholar-api-key",
        type=str,
        help="Semantic Scholar API key (optional, for paper search)"
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
    
    # Setup paths
    script_dir = Path(__file__).parent
    ground_truth_file = script_dir.parent / "ground_truth" / "code_extractor_ground_truth.json"
    results_dir = script_dir.parent / "results"
    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else None
    
    semantic_scholar_api_key = args.semantic_scholar_api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    
    # Initialize evaluator
    evaluator = CodeExtractorEvaluator(
        ground_truth_file=ground_truth_file,
        results_dir=results_dir,
        openai_api_key=openai_api_key,
        pdf_dir=pdf_dir,
        semantic_scholar_api_key=semantic_scholar_api_key
    )
    
    # Determine which papers to evaluate
    if args.paper_id:
        paper_ids = [args.paper_id]
    else:
        paper_ids = [gt.get("id") for gt in evaluator.ground_truth_data if gt.get("id")]
    
    # Run evaluation
    for paper_id in paper_ids:
        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"Evaluating Paper ID: {paper_id}")
            logger.info(f"{'='*70}\n")
            
            result = evaluator.evaluate_paper(paper_id, num_evaluations=args.num_evaluations)
            evaluator.save_results(result, paper_id)
            
            logger.info(f"\n✅ Evaluation completed for Paper ID {paper_id}")
            
            # Log standalone evaluation score
            standalone_eval = result.get('standalone_evaluation', {}).get('averaged_result', {})
            if standalone_eval:
                logger.info(f"Standalone Overall Score: {standalone_eval.get('overall_score', 'N/A')}/10")
            
            # Log comparison evaluation score
            comparison_eval = result.get('comparison_evaluation', {}).get('averaged_result', {})
            if comparison_eval:
                logger.info(f"Comparison Overall Score: {comparison_eval.get('overall_score', 'N/A')}/10\n")
        
        except Exception as e:
            logger.error(f"❌ Evaluation failed for Paper ID {paper_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())


if __name__ == "__main__":
    main()

