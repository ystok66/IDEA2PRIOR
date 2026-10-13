"""
Code Extractor Tool for smolagents
Extracts code and pseudocode from research papers, evaluates completeness and reproducibility,
parses framework and dependencies, runs tests, and classifies papers.
"""

from __future__ import annotations

import re
import ast
import json
import hashlib
import textwrap
import uuid
from pipelines.runtime import atomic_write_json, atomic_write_bytes
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict

from smolagents import Tool
from smolagents.local_python_executor import LocalPythonExecutor
from loguru import logger

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .pdf_extractor import PDFExtractorRAGTool

# Try to import LLM chat function
try:
    import importlib.util
    import sys
    from pathlib import Path
    
    # Try to import from llm_chat(1).py
    llm_chat_path = Path(__file__).parent / "llm_chat(1).py"
    if llm_chat_path.exists():
        spec = importlib.util.spec_from_file_location("llm_chat", llm_chat_path)
        llm_chat_module = importlib.util.module_from_spec(spec)
        sys.modules["llm_chat"] = llm_chat_module
        spec.loader.exec_module(llm_chat_module)
        chat_with_llm = llm_chat_module.chat_with_llm
        LLM_AVAILABLE = True
    else:
        # Try normal import
        from .llm_chat import chat_with_llm
        LLM_AVAILABLE = True
except (ImportError, FileNotFoundError, AttributeError) as e:
    LLM_AVAILABLE = False
    LLM_IMPORT_ERROR = e
    logger.warning(f"⚠️  LLM chat function not available: {e}, pseudocode evaluation will be limited")


@dataclass
class CodeBlock:
    """Code block data structure"""
    code: str
    language: str
    line_start: int
    line_end: int
    context: str = ""
    section: str = ""
    algorithm_name: str = ""
    provenance: str = "paper_extracted_text"


@dataclass
class PseudocodeBlock:
    """Pseudocode block data structure"""
    algorithm_name: str
    steps: List[str]
    input: str = ""
    output: str = ""
    context: str = ""
    line_start: int = 0
    line_end: int = 0
    raw_text: str = ""
    provenance: str = "paper_extracted_text"


@dataclass
class CodeExistenceResult:
    """Code existence detection result"""
    has_code: bool
    has_pseudocode: bool
    code_blocks_count: int
    pseudocode_blocks_count: int
    total_code_elements: int


@dataclass
class CompletenessResult:
    """Completeness check result"""
    code_completeness: float
    pseudocode_completeness: float
    missing_parts: List[str] = field(default_factory=list)
    completeness_score: float = 0.0


@dataclass
class LLMEvaluationResult:
    """LLM evaluation result"""
    reproducibility_score: float
    completeness_score: float
    identified_framework: str = ""
    identified_dependencies: List[str] = field(default_factory=list)
    model_structure: Dict[str, Any] = field(default_factory=dict)
    conversion_difficulty: str = "unknown"
    missing_components: List[str] = field(default_factory=list)


@dataclass
class FrameworkAnalysisResult:
    """Framework and dependency analysis result"""
    framework: str = ""
    dependencies: List[str] = field(default_factory=list)
    model_structure: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestResult:
    """Test result"""
    syntax_check: Dict[str, Any] = field(default_factory=dict)
    execution_test: Dict[str, Any] = field(default_factory=dict)
    logic_check: Dict[str, Any] = field(default_factory=dict)
    accuracy_test: Dict[str, Any] = field(default_factory=dict)
    block_results: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PaperClassification:
    """Paper classification result"""
    category: str  # "complete", "partial", "none"
    emoji: str  # "📗", "📙", "📕"
    reason: str = ""
    score: float = 0.0


class CodeExtractorTool(Tool):
    """
    Complete code extraction, evaluation, verification and classification tool.
    
    This tool:
    1. Detects if code/pseudocode exists in the paper
    2. Checks completeness of code/pseudocode
    3. Uses LLM to evaluate pseudocode for reproducibility
    4. Parses framework, dependencies, and model structure
    5. Runs tests on extracted code
    6. Classifies papers into three categories:
       - 📗 Complete code (ready to use)
       - 📙 Partial code (needs repair)
       - 📕 No code (not reproducible)
    """
    
    name = "code_extractor"
    description = (
        "Extract code and pseudocode from research papers, evaluate their "
        "completeness and reproducibility, parse framework and dependencies, "
        "run tests, and classify papers into three categories: "
        "📗 complete code (ready to use), "
        "📙 partial code (needs repair), "
        "📕 no code (not reproducible)."
    )
    inputs = {
        "pdf_name": {
            "type": "string",
            "description": "Name of the PDF file (without .pdf extension) to extract code from",
        },
        "query": {
            "type": "string",
            "description": "Optional query to search for specific algorithms or code sections",
            "required": False,
            "nullable": True,
        },
    }
    output_type = "string"
    
    # LLM evaluation prompt template
    PSEUDOCODE_EVALUATION_PROMPT = """You are a code evaluation expert. Please evaluate the pseudocode in the following paper:

{pseudocode}

Please analyze:
1. Completeness of pseudocode (whether it has input/output, complete steps)
2. Reproducibility (whether it contains enough information to reproduce)
3. Framework used (PyTorch, TensorFlow, JAX, etc.)
4. Dependencies (numpy, torch, sklearn, etc.)
5. Model structure (number of layers, parameters, etc.)
6. Missing components (training loop, data preprocessing, etc.)

Please return in JSON format:
{{
    "reproducibility_score": 0.0-1.0,
    "completeness_score": 0.0-1.0,
    "identified_framework": "framework name or unknown",
    "identified_dependencies": ["dependency1", "dependency2"],
    "model_structure": {{
        "layers": ["layer1", "layer2"],
        "parameters": "parameter description"
    }},
    "conversion_difficulty": "easy/medium/hard/unknown",
    "missing_components": ["missing component1", "missing component2"]
}}
"""
    
    def __init__(
        self,
        pdf_extractor: Optional[PDFExtractorRAGTool] = None,
        pdf_base_dir: Optional[Path | str] = None,
        output_base_dir: Optional[Path | str] = None,
        authorized_imports: Optional[List[str]] = None,
        use_mock: bool = False,
        llm_model_name: str = "gemini-2.0-flash-exp",  # Use Gemini model name
        web_search_tool: Optional[Any] = None,  # Optional web search tool (e.g., DuckDuckGoSearchTool)
        enable_web_search: bool = True,
        execute_code: bool = False,
        llm_enabled: bool = True,  # Enable web search enhancement
    ):
        """Initialize the code extractor tool"""
        super().__init__()
        self.pdf_extractor = pdf_extractor
        self.is_initialized = False
        self.use_mock = use_mock
        self.execute_code = execute_code
        self.llm_enabled = llm_enabled
        self.analysis_errors = []
        self.llm_model_name = llm_model_name
        self.web_search_tool = web_search_tool
        self.enable_web_search = enable_web_search and (web_search_tool is not None)
        
        # Set up paths
        if pdf_base_dir is None:
            self.pdf_base_dir = Path(
                "inputs/papers"
            )
        else:
            self.pdf_base_dir = Path(pdf_base_dir)
            
        if output_base_dir is None:
            self.output_base_dir = Path(
                "outputs/extension_cache"
            )
        else:
            self.output_base_dir = Path(output_base_dir)
        
        # Patterns for code extraction
        self.code_block_pattern = re.compile(
            r'```(\w+)?\n(.*?)```',
            re.DOTALL
        )
        # Improved algorithm pattern: more flexible matching with multiple strategies
        # Strategy 1: Algorithm X: or Algorithm X (with optional colon)
        # Strategy 2: Algorithm X followed by content (more flexible)
        self.algorithm_pattern = re.compile(
            r'(?:Algorithm|算法)\s+(\d+)[:：]?\s*[^\n]*(?:\n|$)((?:[^\n]+\n)+?)(?=\n(?:Algorithm|算法|\d+\.|#|##|\Z))',  # Support both English and Chinese
            re.MULTILINE | re.IGNORECASE
        )
        
        # Additional pattern for algorithms without explicit "Algorithm" keyword
        # Look for numbered lists that look like algorithm steps
        self.algorithm_alt_pattern = re.compile(
            r'(?:^|\n)(?:Algorithm|算法|Procedure|过程|伪代码|Pseudocode)[:\s]*[^\n]*(?:\n|$)((?:[^\n]*\n){3,})',  # At least 3 lines
            re.MULTILINE | re.IGNORECASE
        )
        
        # IMPROVED: Additional patterns for better algorithm detection
        # Pattern 1: Input/Output format (common in papers)
        self.input_output_pattern = re.compile(
            r'(?:Input|输入)[:\s]+(.+?)(?:\n|$).*?(?:Output|输出)[:\s]+(.+?)(?:\n|$)', 
            re.DOTALL | re.IGNORECASE
        )
        
        # Pattern 2: Mathematical formula algorithms
        self.math_algorithm_pattern = re.compile(
            r'\$\$.*?(?:for|while|if|repeat|until|procedure|algorithm).*?\$\$',
            re.DOTALL | re.IGNORECASE
        )
        
        # Pattern 3: Paragraph-form algorithm descriptions
        self.paragraph_algorithm_pattern = re.compile(
            r'(?:The algorithm|The procedure|We propose|We present).*?(?:Input|Output|Step|步骤)',
            re.DOTALL | re.IGNORECASE
        )
        
        # Pattern 4: Flowchart/step descriptions
        self.flowchart_pattern = re.compile(
            r'(?:flowchart|flow chart|步骤|step)[:\s]+(.*?)(?=\n\n|\n(?:flowchart|flow chart|步骤|step)|\Z)',
            re.DOTALL | re.IGNORECASE
        )
        
        # Pattern 5: Numbered step lists (without explicit Algorithm label)
        self.numbered_steps_pattern = re.compile(
            r'(?:^|\n)\s*(\d+)[\.\)]\s+(.+?)(?=\n\s*\d+[\.\)]|\n\n|\Z)',
            re.MULTILINE
        )
        
        self.section_pattern = re.compile(r'^#+\s+(.+)$', re.MULTILINE)
        
        # Set up code executor
        default_imports = [
            "math", "random", "collections", "itertools", "functools",
            "datetime", "json", "re",
        ]
        
        optional_imports = [
            ("numpy", "numpy.*"),
            ("torch", "torch.*"),
            ("scipy", "scipy.*"),
            ("pandas", "pandas.*"),
            ("matplotlib", "matplotlib.*"),
        ]
        
        available_imports = default_imports.copy()
        for lib, lib_wildcard in (optional_imports if execute_code else []):
            try:
                __import__(lib)
                available_imports.extend([lib, lib_wildcard])
            except ImportError:
                pass
        
        authorized_imports = authorized_imports or available_imports
        
        try:
            self.code_executor = LocalPythonExecutor(authorized_imports) if execute_code else None
            if self.code_executor is not None:
                self.code_executor.send_tools({})
            logger.info(f"✅ Code executor initialized with {len(authorized_imports)} authorized imports")
        except Exception as e:
            logger.warning(f"⚠️  Failed to initialize LocalPythonExecutor: {e}")
            self.code_executor = None
    
    def setup(self):
        """Setup method called before first use"""
        logger.info("🔧 Setting up code extractor tool...")
        self.is_initialized = True
    
    def _get_markdown_content(self, pdf_name: str, query: Optional[str] = None) -> str:
        """Get markdown content from PDF - IMPROVED with validation and multiple paths"""
        if self.pdf_extractor:
            if query:
                result = self.pdf_extractor.forward(pdf_name, query)
                if result.startswith(f"Query: '{query}'"):
                    content = result.split("\n\nRelevant content:\n\n", 1)
                    if len(content) > 1:
                        return content[1]
                return result
            else:
                # IMPORTANT: Read full markdown file directly instead of using forward()
                # because forward() only returns first 2000 characters, which is not enough
                # for code extraction. We need the full content.
                from .pdf_extractor import PDFExtractorTool
                extractor = PDFExtractorTool(
                    pdf_base_dir=self.pdf_base_dir,
                    output_base_dir=self.output_base_dir
                )
                extractor.setup()
                
                # First, ensure PDF is parsed (this will create the markdown file)
                extractor.forward(pdf_name)
                
                # Try multiple possible paths for markdown file
                possible_paths = [
                    self.output_base_dir / pdf_name / "auto" / f"{pdf_name}.md",
                    self.output_base_dir / pdf_name / f"{pdf_name}.md",
                    self.output_base_dir / f"{pdf_name}.md",
                ]
                
                md_path = None
                for path in possible_paths:
                    if path.exists():
                        md_path = path
                        break
                
                if md_path and md_path.exists():
                    with open(md_path, "r", encoding="utf-8") as f:
                        full_content = f.read()
                    
                    # IMPROVED: Validate content completeness
                    if len(full_content) < 500:
                        logger.warning(f"⚠️  Markdown content too short ({len(full_content)} chars) for {pdf_name}")
                    else:
                        logger.info(f"📖 Read full markdown content: {len(full_content)} characters")
                    
                    return full_content
                else:
                    # Fallback: Try to parse PDF and read full content even if forward() fails
                    logger.warning(f"⚠️  Markdown file not found, attempting to parse PDF...")
                    try:
                        # Try to parse PDF directly
                        pdf_path = self.pdf_base_dir / f"{pdf_name}.pdf"
                        if pdf_path.exists():
                            from .pdf_utils import parse_doc
                            parse_doc(
                                path_list=[str(pdf_path)],
                                output_dir=str(self.output_base_dir),
                                lang="en",
                                backend="pipeline",
                                method="auto",
                            )
                            # Check all possible paths again
                            for path in possible_paths:
                                if path.exists():
                                    with open(path, "r", encoding="utf-8") as f:
                                        full_content = f.read()
                                    logger.info(f"📖 Read full markdown content after parsing: {len(full_content)} characters")
                                    return full_content
                    except Exception as e:
                        logger.warning(f"⚠️  PDF parsing failed: {e}, using truncated content as fallback")
                    
                    # Final fallback: use truncated content but log warning
                    truncated = extractor.forward(pdf_name)
                    logger.warning(f"⚠️  Using truncated content ({len(truncated)} chars). Code extraction may be limited.")
                    return truncated
        else:
            # Try multiple possible paths
            possible_paths = [
                self.output_base_dir / pdf_name / "auto" / f"{pdf_name}.md",
                self.output_base_dir / pdf_name / f"{pdf_name}.md",
                self.output_base_dir / f"{pdf_name}.md",
            ]
            
            for path in possible_paths:
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if len(content) < 500:
                        logger.warning(f"⚠️  Markdown content too short ({len(content)} chars)")
                    return content
            
            raise FileNotFoundError(f"Markdown file not found for {pdf_name}")
    
    def _extract_paper_title(self, markdown_content: str, pdf_name: str) -> str:
        """
        Extract paper title from markdown content
        
        Args:
            markdown_content: Markdown content of the paper
            pdf_name: Fallback PDF name if title not found
            
        Returns:
            Paper title string
        """
        # Try to find title in markdown (usually first # heading)
        title_pattern = re.compile(r'^#\s+(.+)$', re.MULTILINE)
        match = title_pattern.search(markdown_content)
        if match:
            title = match.group(1).strip()
            # Clean up title (remove extra formatting)
            title = re.sub(r'[*_`]', '', title)  # Remove markdown formatting
            if len(title) > 10:  # Reasonable title length
                logger.info(f"📄 Extracted paper title: {title}")
                return title
        
        # Fallback to PDF name
        logger.info(f"📄 Using PDF name as title: {pdf_name}")
        return pdf_name
    
    def _search_web_for_paper(self, paper_title: str) -> Dict[str, Any]:
        """
        Search the web for additional information about the paper
        
        Args:
            paper_title: Title of the paper to search for
            
        Returns:
            Dictionary containing web search results with keys:
            - github_repos: List of GitHub repository URLs
            - implementations: List of implementation-related results
            - frameworks: List of framework mentions
            - dependencies: List of dependency mentions
            - code_snippets: List of code snippets found
            - raw_results: Raw search results
        """
        if not self.enable_web_search or not self.web_search_tool:
            logger.info("⚠️  Web search is disabled or web_search_tool is not provided")
            return {}
        
        logger.info(f"🌐 Starting web search for paper: {paper_title}")
        web_results = {
            "github_repos": [],
            "implementations": [],
            "frameworks": [],
            "dependencies": [],
            "code_snippets": [],
            "raw_results": []
        }
        
        try:
            # Search strategy 1: GitHub repositories
            github_query = f"{paper_title} github code implementation"
            logger.info(f"🔍 Searching for GitHub repositories: {github_query}")
            try:
                github_results = self.web_search_tool.forward(github_query)
                web_results["raw_results"].append({
                    "query": github_query,
                    "results": github_results
                })
                # Extract GitHub URLs from results
                github_urls = re.findall(r'https?://github\.com/[\w\-\.]+/[\w\-\.]+', github_results)
                web_results["github_repos"] = list(set(github_urls))  # Remove duplicates
                logger.info(f"📦 Found {len(web_results['github_repos'])} GitHub repositories")
            except Exception as e:
                logger.warning(f"⚠️  GitHub search failed: {e}")
            
            # Search strategy 2: Implementation and code
            implementation_query = f"{paper_title} implementation code example"
            logger.info(f"🔍 Searching for implementations: {implementation_query}")
            try:
                impl_results = self.web_search_tool.forward(implementation_query)
                web_results["raw_results"].append({
                    "query": implementation_query,
                    "results": impl_results
                })
                # Extract code-related content
                if "code" in impl_results.lower() or "implementation" in impl_results.lower():
                    web_results["implementations"].append(impl_results)
                logger.info(f"📝 Found implementation-related results")
            except Exception as e:
                logger.warning(f"⚠️  Implementation search failed: {e}")
            
            # Search strategy 3: Framework and dependencies
            framework_query = f"{paper_title} framework library dependencies requirements"
            logger.info(f"🔍 Searching for frameworks and dependencies: {framework_query}")
            try:
                framework_results = self.web_search_tool.forward(framework_query)
                web_results["raw_results"].append({
                    "query": framework_query,
                    "results": framework_results
                })
                # Extract framework mentions (PyTorch, TensorFlow, etc.)
                framework_keywords = ["pytorch", "tensorflow", "keras", "jax", "mxnet", "caffe", "theano"]
                for keyword in framework_keywords:
                    if keyword.lower() in framework_results.lower():
                        web_results["frameworks"].append(keyword)
                # Extract dependency mentions (numpy, scipy, etc.)
                dependency_keywords = ["numpy", "scipy", "pandas", "matplotlib", "scikit-learn", "sklearn"]
                for keyword in dependency_keywords:
                    if keyword.lower() in framework_results.lower():
                        web_results["dependencies"].append(keyword)
                web_results["frameworks"] = list(set(web_results["frameworks"]))
                web_results["dependencies"] = list(set(web_results["dependencies"]))
                logger.info(f"🔧 Found {len(web_results['frameworks'])} frameworks, {len(web_results['dependencies'])} dependencies")
            except Exception as e:
                logger.warning(f"⚠️  Framework search failed: {e}")
            
            logger.info(f"✅ Web search completed. Found {len(web_results['github_repos'])} repos, {len(web_results['implementations'])} implementations")
            return web_results
            
        except Exception as e:
            logger.error(f"❌ Web search failed: {e}")
            return web_results
    
    def _integrate_web_results_with_llm(
        self,
        pdf_extracted: Dict[str, Any],
        web_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Use LLM to integrate PDF extraction results with web search results
        
        Args:
            pdf_extracted: Dictionary containing PDF extraction results
            web_results: Dictionary containing web search results
            
        Returns:
            Integrated results dictionary
        """
        if not (LLM_AVAILABLE and self.llm_enabled):
            logger.warning("⚠️  LLM not available, skipping web result integration")
            return pdf_extracted
        
        if not web_results or not web_results.get("raw_results"):
            logger.info("ℹ️  No web results to integrate")
            return pdf_extracted
        
        logger.info("🤖 Using LLM to integrate web search results with PDF extraction...")
        
        # Prepare context for LLM
        pdf_summary = f"""
PDF Extraction Results:
- Code blocks found: {len(pdf_extracted.get('code_blocks', []))}
- Pseudocode blocks found: {len(pdf_extracted.get('pseudocode_blocks', []))}
- Framework: {pdf_extracted.get('framework', 'Not found')}
- Dependencies: {', '.join(pdf_extracted.get('dependencies', [])) or 'Not found'}
"""
        
        web_summary = f"""
Web Search Results:
- GitHub repositories: {', '.join(web_results.get('github_repos', [])) or 'None'}
- Frameworks mentioned: {', '.join(web_results.get('frameworks', [])) or 'None'}
- Dependencies mentioned: {', '.join(web_results.get('dependencies', [])) or 'None'}
- Implementation results: {len(web_results.get('implementations', []))} found
"""
        
        prompt = f"""You are an expert in code analysis and research paper reproducibility evaluation.

I have extracted code and pseudocode from a research paper PDF, and also searched the web for additional information.

**PDF Extraction Results:**
{pdf_summary}

**Web Search Results:**
{web_summary}

**Task**: Analyze the web search results and integrate them with the PDF extraction results to provide:
1. **Enhanced Framework Identification**: If web search found frameworks not in PDF, add them
2. **Enhanced Dependency List**: If web search found dependencies not in PDF, add them
3. **GitHub Repository Links**: Include any GitHub repositories found
4. **Additional Code Information**: If web search found code examples, note them
5. **Confidence Assessment**: Assess whether web results confirm or contradict PDF extraction

**Output Format**: JSON
{{
  "enhanced_framework": "framework name or 'None'",
  "enhanced_dependencies": ["dep1", "dep2"],
  "github_repos": ["url1", "url2"],
  "additional_notes": "any additional information from web search",
  "confidence": "high/medium/low",
  "conflicts": ["any conflicts between PDF and web results"]
}}
"""
        
        try:
            response = chat_with_llm(
                prompt,
                model_name=self.llm_model_name,
                max_tokens=2000,
                temperature=0.0
            )
            
            # Parse JSON response
            import json
            # Try to extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                integrated = json.loads(json_match.group(0))
                
                # Merge results
                if integrated.get("enhanced_framework") and integrated["enhanced_framework"] != "None":
                    pdf_extracted["framework"] = integrated["enhanced_framework"]
                if integrated.get("enhanced_dependencies"):
                    pdf_extracted["dependencies"].extend(integrated["enhanced_dependencies"])
                    pdf_extracted["dependencies"] = list(set(pdf_extracted["dependencies"]))
                if integrated.get("github_repos"):
                    pdf_extracted["github_repos"] = integrated["github_repos"]
                if integrated.get("additional_notes"):
                    pdf_extracted["web_search_notes"] = integrated["additional_notes"]
                if integrated.get("confidence"):
                    pdf_extracted["web_search_confidence"] = integrated["confidence"]
                
                logger.info(f"✅ LLM integration completed. Enhanced framework: {integrated.get('enhanced_framework')}, Added {len(integrated.get('enhanced_dependencies', []))} dependencies")
                return pdf_extracted
            else:
                logger.warning("⚠️  Could not parse LLM response as JSON")
                return pdf_extracted
                
        except Exception as e:
            logger.error(f"❌ LLM integration failed: {e}")
            return pdf_extracted
    
    def _preprocess_content(self, content: str) -> str:
        """Preprocess markdown content for better extraction - IMPROVED"""
        # 1. Clean format issues
        content = content.replace('\r\n', '\n')  # Normalize line endings
        content = re.sub(r'\n{3,}', '\n\n', content)  # Normalize multiple blank lines
        
        # 2. Fix broken code blocks
        content = re.sub(r'```\s*\n\s*```', '```\n```', content)
        
        # 3. Normalize whitespace (but preserve indentation for code)
        # Only normalize multiple spaces/tabs in non-code contexts
        lines = content.split('\n')
        processed_lines = []
        in_code_block = False
        for line in lines:
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
            if not in_code_block and not (line.startswith('    ') or line.startswith('\t')):
                # Normalize multiple spaces (but not in code blocks or indented lines)
                line = re.sub(r'[ \t]+', ' ', line)
            processed_lines.append(line)
        content = '\n'.join(processed_lines)
        
        return content
    
    def _extract_code_blocks(self, markdown_content: str) -> List[CodeBlock]:
        """Extract code blocks from markdown content - IMPROVED with more patterns"""
        # IMPROVED: Preprocess content first
        markdown_content = self._preprocess_content(markdown_content)
        
        code_blocks = []
        lines = markdown_content.split('\n')
        
        # 1. Extract standard Markdown code blocks (```python ... ```)
        for match in self.code_block_pattern.finditer(markdown_content):
            language = match.group(1) or "text"
            code = match.group(2).strip()
            
            start_pos = match.start()
            line_start = markdown_content[:start_pos].count('\n') + 1
            line_end = line_start + code.count('\n')
            
            context_start = max(0, line_start - 6)
            context_end = min(len(lines), line_end + 3)
            context = '\n'.join(lines[context_start:context_end])
            
            section = ""
            for i in range(line_start - 1, -1, -1):
                if i < len(lines):
                    section_match = self.section_pattern.match(lines[i])
                    if section_match:
                        section = section_match.group(1)
                        break
            
            code_blocks.append(CodeBlock(
                code=code,
                language=language.lower(),
                line_start=line_start,
                line_end=line_end,
                context=context,
                section=section
            ))
        
        # 2. Extract indented code blocks (4 spaces or tab at start)
        # Look for consecutive lines with significant indentation that contain code-like patterns
        in_code_block = False
        code_lines = []
        code_start_line = 0
        min_indent = None
        
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if not stripped:  # Empty line
                if in_code_block and len(code_lines) > 0:
                    # End of code block
                    code_text = '\n'.join(code_lines)
                    if self._looks_like_code(code_text):
                        code_blocks.append(CodeBlock(
                            code=code_text,
                            language="python",  # Default to python
                            line_start=code_start_line + 1,
                            line_end=code_start_line + len(code_lines),
                            context='\n'.join(lines[max(0, code_start_line-3):min(len(lines), code_start_line+len(code_lines)+3)]),
                            section=self._find_section(lines, code_start_line)
                        ))
                    code_lines = []
                    in_code_block = False
                continue
            
            # Check if line looks like code (has indentation and code-like patterns)
            indent = len(line) - len(stripped)
            if indent >= 4 or (indent > 0 and self._line_looks_like_code(stripped)):
                if not in_code_block:
                    in_code_block = True
                    code_start_line = i
                    min_indent = indent
                code_lines.append(stripped)
            else:
                if in_code_block and len(code_lines) > 0:
                    # End of code block
                    code_text = '\n'.join(code_lines)
                    if self._looks_like_code(code_text):
                        code_blocks.append(CodeBlock(
                            code=code_text,
                            language="python",
                            line_start=code_start_line + 1,
                            line_end=code_start_line + len(code_lines),
                            context='\n'.join(lines[max(0, code_start_line-3):min(len(lines), code_start_line+len(code_lines)+3)]),
                            section=self._find_section(lines, code_start_line)
                        ))
                    code_lines = []
                    in_code_block = False
        
        # Handle code block at end of file
        if in_code_block and len(code_lines) > 0:
            code_text = '\n'.join(code_lines)
            if self._looks_like_code(code_text):
                code_blocks.append(CodeBlock(
                    code=code_text,
                    language="python",
                    line_start=code_start_line + 1,
                    line_end=code_start_line + len(code_lines),
                    context='\n'.join(lines[max(0, code_start_line-3):min(len(lines), code_start_line+len(code_lines)+3)]),
                    section=self._find_section(lines, code_start_line)
                ))
        
        # 3. Extract inline code snippets (IMPROVED: More patterns, better matching)
        # Look for patterns like: def function_name, class ClassName, import module, @decorator, async def
        inline_code_pattern = re.compile(
            r'(?:(?:^|\n)(?:(?:def|class|import|from|@|async\s+def)\s+\w+.*?(?=\n\n|\n(?:def|class|import|from|@)|\Z)))',
            re.MULTILINE | re.DOTALL
        )
        
        for match in inline_code_pattern.finditer(markdown_content):
            code = match.group(0).strip()
            # IMPROVED: Reduced minimum length from 20 to 15 to catch shorter code snippets
            if len(code) > 15 and self._looks_like_code(code):
                start_pos = match.start()
                line_start = markdown_content[:start_pos].count('\n') + 1
                line_end = line_start + code.count('\n')
                
                # Check if this code is already in a code block
                is_duplicate = False
                for existing_block in code_blocks:
                    if abs(existing_block.line_start - line_start) < 5:
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    context_start = max(0, line_start - 6)
                    context_end = min(len(lines), line_end + 3)
                    context = '\n'.join(lines[context_start:context_end])
                    
                    code_blocks.append(CodeBlock(
                        code=code,
                        language="python",
                        line_start=line_start,
                        line_end=line_end,
                        context=context,
                        section=self._find_section(lines, line_start - 1)
                    ))
        
        logger.info(f"📦 Extracted {len(code_blocks)} code blocks")
        return code_blocks
    
    def _line_looks_like_code(self, line: str) -> bool:
        """Check if a line looks like code"""
        code_keywords = ['def ', 'class ', 'import ', 'from ', 'return ', 'if ', 'for ', 'while ', 
                        '=', '(', ')', '[', ']', '{', '}', ':', '->', 'self.', 'torch.', 'np.']
        return any(keyword in line for keyword in code_keywords)
    
    def _looks_like_code(self, text: str) -> bool:
        """Check if text looks like code (IMPROVED: More patterns)"""
        if len(text.strip()) < 10:  # Too short
            return False
        
        # Check for code-like patterns (IMPROVED: More comprehensive patterns)
        code_patterns = [
            r'\bdef\s+\w+\s*\(',  # Function definition
            r'\bclass\s+\w+',     # Class definition
            r'\bimport\s+\w+',    # Import statement
            r'\bfrom\s+\w+\s+import',  # From import
            r'\breturn\s+',       # Return statement
            r'\bif\s+.*:',        # If statement
            r'\bfor\s+\w+\s+in', # For loop
            r'\bwhile\s+.*:',     # While loop
            r'\btry\s*:',        # Try block
            r'\bexcept\s+',      # Except block
            r'=\s*\[',           # List assignment
            r'=\s*\{',           # Dict assignment
            r'\.append\(',       # List append
            r'\.extend\(',       # List extend
            r'np\.',             # NumPy usage
            r'torch\.',          # PyTorch usage
            r'tf\.',             # TensorFlow usage
            r'self\.',           # Class method
            r'@\w+',             # Decorator
        ]
        
        # Count how many patterns match
        pattern_matches = sum(1 for pattern in code_patterns if re.search(pattern, text, re.IGNORECASE))
        
        # IMPROVED: Require at least 1 pattern to match (relaxed to catch more code)
        if pattern_matches >= 1:
            return True
        
        # Check for common code structures (IMPROVED: More flexible)
        has_indentation = any(line.startswith('    ') or line.startswith('\t') for line in text.split('\n') if line.strip())
        has_brackets = '(' in text and ')' in text
        has_operators = any(op in text for op in ['=', '==', '!=', '<=', '>=', '+', '-', '*', '/', '%', '//'])
        has_colon = ':' in text  # Common in Python
        
        # More flexible: accept if has indentation OR (brackets AND operators) OR (colon AND operators)
        return has_indentation or (has_brackets and has_operators) or (has_colon and has_operators)
    
    def _find_section(self, lines: List[str], line_num: int) -> str:
        """Find section name for a given line number"""
        for i in range(line_num, -1, -1):
            if i < len(lines):
                section_match = self.section_pattern.match(lines[i])
                if section_match:
                    return section_match.group(1)
        return ""
    
    def _postprocess_code_blocks(self, code_blocks: List[CodeBlock]) -> List[CodeBlock]:
        """Postprocess code blocks: deduplicate, validate, and sort - IMPROVED"""
        if not code_blocks:
            return []
        
        # Remove duplicates (same code or overlapping lines)
        unique_blocks = []
        seen_codes = set()
        for block in code_blocks:
            code_hash = hash(block.code.strip())
            if code_hash not in seen_codes:
                # Check for overlapping line ranges
                is_duplicate = False
                for existing in unique_blocks:
                    if (abs(existing.line_start - block.line_start) < 3 and 
                        abs(existing.line_end - block.line_end) < 3):
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    seen_codes.add(code_hash)
                    unique_blocks.append(block)
        
        # Sort by line_start
        unique_blocks.sort(key=lambda x: x.line_start)
        
        return unique_blocks
    
    def _extract_pseudocode(self, markdown_content: str, query: Optional[str] = None) -> List[PseudocodeBlock]:
        """Extract pseudocode from markdown content - IMPROVED with more patterns"""
        # IMPROVED: Preprocess content first
        markdown_content = self._preprocess_content(markdown_content)
        
        pseudocode_blocks = []
        lines = markdown_content.split('\n')
        
        # Strategy 1: Use main algorithm pattern
        for match in self.algorithm_pattern.finditer(markdown_content):
            algorithm_num = match.group(1)
            algorithm_text = match.group(2).strip()
            
            if query and query.lower() not in algorithm_text.lower():
                continue
            
            start_pos = match.start()
            line_start = markdown_content[:start_pos].count('\n') + 1
            line_end = line_start + algorithm_text.count('\n')
            
            algorithm_name = f"Algorithm {algorithm_num}"
            first_line = algorithm_text.split('\n')[0] if algorithm_text else ""
            if first_line and len(first_line) < 100:
                algorithm_name = f"Algorithm {algorithm_num}: {first_line}"
            
            steps = []
            for line in algorithm_text.split('\n'):
                line = line.strip()
                if line and (line[0].isdigit() or line.startswith('-') or line.startswith('•')):
                    steps.append(line)
            
            input_desc = ""
            output_desc = ""
            if "Input:" in algorithm_text or "输入:" in algorithm_text:  # Support both English and Chinese
                input_match = re.search(r'(?:Input|输入):\s*(.+?)(?:\n|$)', algorithm_text, re.IGNORECASE)
                if input_match:
                    input_desc = input_match.group(1).strip()
            if "Output:" in algorithm_text or "输出:" in algorithm_text:  # Support both English and Chinese
                output_match = re.search(r'(?:Output|输出):\s*(.+?)(?:\n|$)', algorithm_text, re.IGNORECASE)
                if output_match:
                    output_desc = output_match.group(1).strip()
            
            context_start = max(0, line_start - 6)
            context_end = min(len(lines), line_end + 3)
            context = '\n'.join(lines[context_start:context_end])
            
            pseudocode_blocks.append(PseudocodeBlock(
                algorithm_name=algorithm_name,
                steps=steps if steps else [algorithm_text[:200]],
                input=input_desc,
                output=output_desc,
                context=context,
                line_start=line_start,
                line_end=line_end,
                raw_text=algorithm_text
            ))
        
        # Strategy 2: Use alternative pattern for algorithms without explicit numbering
        for match in self.algorithm_alt_pattern.finditer(markdown_content):
            algorithm_text = match.group(1).strip()
            
            if query and query.lower() not in algorithm_text.lower():
                continue
            
            # Check if this looks like pseudocode (IMPROVED: More flexible detection)
            has_steps = any(re.search(r'^\s*\d+[\.\)]\s+', line, re.MULTILINE) for line in algorithm_text.split('\n'))
            has_keywords = any(keyword in algorithm_text.lower() for keyword in [
                'input', 'output', 'step', 'compute', 'return', 'for', 'while', 'if',
                'algorithm', 'procedure', 'function', 'method', 'pseudocode',
                'initialize', 'iterate', 'update', 'calculate', 'evaluate'
            ])
            has_algorithm_structure = any(
                phrase in algorithm_text.lower() 
                for phrase in ['for each', 'for all', 'while', 'until', 'repeat', 'end for', 'end while']
            )
            
            if has_steps or has_keywords or has_algorithm_structure:
                start_pos = match.start()
                line_start = markdown_content[:start_pos].count('\n') + 1
                line_end = line_start + algorithm_text.count('\n')
                
                # Extract steps
                steps = []
                for line in algorithm_text.split('\n'):
                    line = line.strip()
                    if line and (re.match(r'^\d+[\.\)]\s+', line) or line.startswith('-') or line.startswith('•')):
                        steps.append(line)
                
                # Extract input/output
                input_desc = ""
                output_desc = ""
                if "Input:" in algorithm_text or "输入:" in algorithm_text:
                    input_match = re.search(r'(?:Input|输入):\s*(.+?)(?:\n|$)', algorithm_text, re.IGNORECASE)
                    if input_match:
                        input_desc = input_match.group(1).strip()
                if "Output:" in algorithm_text or "输出:" in algorithm_text:
                    output_match = re.search(r'(?:Output|输出):\s*(.+?)(?:\n|$)', algorithm_text, re.IGNORECASE)
                    if output_match:
                        output_desc = output_match.group(1).strip()
                
                context_start = max(0, line_start - 6)
                context_end = min(len(lines), line_end + 3)
                context = '\n'.join(lines[context_start:context_end])
                
                algorithm_name = "Algorithm (unnumbered)"
                first_line = algorithm_text.split('\n')[0] if algorithm_text else ""
                if first_line and len(first_line) < 100:
                    algorithm_name = f"Algorithm: {first_line}"
                
                pseudocode_blocks.append(PseudocodeBlock(
                    algorithm_name=algorithm_name,
                    steps=steps if steps else [algorithm_text[:200]],
                    input=input_desc,
                    output=output_desc,
                    context=context,
                    line_start=line_start,
                    line_end=line_end,
                    raw_text=algorithm_text
                ))
        
        # IMPROVED: Strategy 3: Extract Input/Output format algorithms
        for match in self.input_output_pattern.finditer(markdown_content):
            input_desc = match.group(1).strip()
            output_desc = match.group(2).strip()
            
            # Extract surrounding text as algorithm steps
            start_pos = match.start()
            end_pos = match.end()
            context_start = max(0, start_pos - 200)
            context_end = min(len(markdown_content), end_pos + 200)
            algorithm_text = markdown_content[context_start:context_end]
            
            # Extract steps from surrounding context
            steps = []
            for line in algorithm_text.split('\n'):
                line = line.strip()
                if line and (re.match(r'^\d+[\.\)]\s+', line) or line.startswith('-') or line.startswith('•')):
                    steps.append(line)
            
            if input_desc or output_desc or steps:
                line_start = markdown_content[:start_pos].count('\n') + 1
                line_end = markdown_content[:end_pos].count('\n') + 1
                
                pseudocode_blocks.append(PseudocodeBlock(
                    algorithm_name="Algorithm (Input/Output format)",
                    steps=steps if steps else [algorithm_text[:200]],
                    input=input_desc,
                    output=output_desc,
                    context=algorithm_text,
                    line_start=line_start,
                    line_end=line_end,
                    raw_text=algorithm_text
                ))
        
        # IMPROVED: Strategy 4: Extract numbered step lists
        for match in self.numbered_steps_pattern.finditer(markdown_content):
            step_num = match.group(1)
            step_text = match.group(2).strip()
            
            # Collect consecutive numbered steps
            start_pos = match.start()
            line_start = markdown_content[:start_pos].count('\n') + 1
            
            # Find all consecutive numbered steps
            steps = []
            current_pos = start_pos
            while True:
                step_match = self.numbered_steps_pattern.search(markdown_content, current_pos)
                if not step_match:
                    break
                next_num = int(step_match.group(1))
                if next_num == int(step_num) + len(steps):
                    steps.append(step_match.group(2).strip())
                    current_pos = step_match.end()
                else:
                    break
            
            if len(steps) >= 2:  # At least 2 steps to be considered an algorithm
                line_end = markdown_content[:current_pos].count('\n') + 1
                context_start = max(0, line_start - 6)
                context_end = min(len(lines), line_end + 3)
                context = '\n'.join(lines[context_start:context_end])
                
                pseudocode_blocks.append(PseudocodeBlock(
                    algorithm_name=f"Algorithm (Steps {step_num}-{int(step_num)+len(steps)-1})",
                    steps=steps,
                    input="",
                    output="",
                    context=context,
                    line_start=line_start,
                    line_end=line_end,
                    raw_text="\n".join(steps)
                ))
        
        # IMPROVED: Postprocess to remove duplicates
        pseudocode_blocks = self._postprocess_pseudocode_blocks(pseudocode_blocks)
        
        logger.info(f"📝 Extracted {len(pseudocode_blocks)} pseudocode blocks")
        return pseudocode_blocks
    
    def _postprocess_pseudocode_blocks(self, pseudocode_blocks: List[PseudocodeBlock]) -> List[PseudocodeBlock]:
        """Postprocess pseudocode blocks: deduplicate and validate - IMPROVED"""
        if not pseudocode_blocks:
            return []
        
        # Remove duplicates (same algorithm name or overlapping lines)
        unique_blocks = []
        seen_names = set()
        for block in pseudocode_blocks:
            if block.algorithm_name not in seen_names:
                # Check for overlapping line ranges
                is_duplicate = False
                for existing in unique_blocks:
                    if (abs(existing.line_start - block.line_start) < 5 and 
                        abs(existing.line_end - block.line_end) < 5):
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    seen_names.add(block.algorithm_name)
                    unique_blocks.append(block)
        
        # Sort by line_start
        unique_blocks.sort(key=lambda x: x.line_start)
        
        return unique_blocks
    
    def _detect_code_existence(self, code_blocks: List[CodeBlock], pseudocode_blocks: List[PseudocodeBlock]) -> CodeExistenceResult:
        """Detect if code/pseudocode exists in the paper"""
        has_code = len(code_blocks) > 0
        has_pseudocode = len(pseudocode_blocks) > 0
        
        return CodeExistenceResult(
            has_code=has_code,
            has_pseudocode=has_pseudocode,
            code_blocks_count=len(code_blocks),
            pseudocode_blocks_count=len(pseudocode_blocks),
            total_code_elements=len(code_blocks) + len(pseudocode_blocks)
        )
    
    def _check_completeness(self, code_blocks: List[CodeBlock], pseudocode_blocks: List[PseudocodeBlock]) -> CompletenessResult:
        """Check completeness of code and pseudocode"""
        missing_parts = []
        
        # Check code completeness
        code_completeness = 0.0
        if code_blocks:
            total_score = 0.0
            for block in code_blocks:
                score = 1.0
                # Check if has function/class definition
                if block.language == "python":
                    if "def " not in block.code and "class " not in block.code:
                        score -= 0.3
                        missing_parts.append(f"Code block missing function/class definition")
                    # Check if has return statement (for functions)
                    if "def " in block.code and "return " not in block.code:
                        score -= 0.2
                        missing_parts.append(f"Function missing return statement")
                    # Check if has import statements
                    if "import " not in block.code and "from " not in block.code:
                        score -= 0.1
                total_score += score
            code_completeness = total_score / len(code_blocks)
        else:
            code_completeness = 0.0
        
        # Check pseudocode completeness (IMPROVED: More lenient scoring)
        pseudocode_completeness = 0.0
        if pseudocode_blocks:
            total_score = 0.0
            for block in pseudocode_blocks:
                score = 1.0
                # Input/output are nice to have but not critical
                if not block.input:
                    score -= 0.1  # Reduced penalty (was 0.2)
                    missing_parts.append(f"{block.algorithm_name} missing input description")
                if not block.output:
                    score -= 0.1  # Reduced penalty (was 0.2)
                    missing_parts.append(f"{block.algorithm_name} missing output description")
                # Steps completeness: more lenient
                if len(block.steps) < 2:  # Changed from 3 to 2
                    score -= 0.2  # Reduced penalty (was 0.3)
                    missing_parts.append(f"{block.algorithm_name} steps incomplete")
                elif len(block.steps) < 5:
                    score -= 0.1  # Minor penalty for short but valid pseudocode
                total_score += score
            pseudocode_completeness = total_score / len(pseudocode_blocks)
        else:
            pseudocode_completeness = 0.0
        
        # Overall completeness score
        if code_blocks or pseudocode_blocks:
            completeness_score = (code_completeness * len(code_blocks) + 
                                pseudocode_completeness * len(pseudocode_blocks)) / \
                               (len(code_blocks) + len(pseudocode_blocks))
        else:
            completeness_score = 0.0
        
        return CompletenessResult(
            code_completeness=code_completeness,
            pseudocode_completeness=pseudocode_completeness,
            missing_parts=list(set(missing_parts)),  # Remove duplicates
            completeness_score=completeness_score
        )
    
    def _extract_with_llm(self, markdown_content: str) -> Optional[Dict[str, Any]]:
        """Use LLM to extract code/pseudocode information - IMPROVED with chunking and multi-round extraction"""
        if not (LLM_AVAILABLE and self.llm_enabled):
            return None
        
        try:
            # IMPROVED: Chunk processing for long documents
            max_chunk_size = 20000  # Increased from 12000
            chunks = []
            for i in range(0, len(markdown_content), max_chunk_size):
                chunk = markdown_content[i:i+max_chunk_size]
                chunks.append(chunk)
            
            all_pseudocode_blocks = []
            all_frameworks = []
            all_dependencies = []
            all_model_structures = []
            
            # IMPROVED: Multi-round extraction
            for chunk_idx, chunk in enumerate(chunks):
                # Round 1: Extract pseudocode and algorithms
                prompt = f"""You are an expert code extractor. Extract ALL code and pseudocode from this paper section.

CRITICAL INSTRUCTIONS:
1. Look in ALL sections: abstract, introduction, methods, experiments, appendix, supplementary
2. Extract even incomplete code/pseudocode
3. Look for:
   - Code blocks with ``` markers
   - Function/class definitions
   - Algorithm descriptions (even without "Algorithm X" label)
   - Procedure definitions
   - Step-by-step instructions
   - Mathematical algorithms in formulas
   - Input/Output format descriptions
4. If you find ANY code or pseudocode, extract it (even if incomplete)

Paper content (section {chunk_idx + 1}/{len(chunks)}):
{chunk[:10000]}

Return JSON format:
{{
  "pseudocode_blocks": [
    {{
      "algorithm_name": "Algorithm name or description",
      "steps": ["step1", "step2", "step3"],
      "input": "input description if mentioned",
      "output": "output description if mentioned"
    }}
  ],
  "framework": "PyTorch or TensorFlow or JAX or Keras or empty string if not found",
  "dependencies": ["numpy", "torch", "tensorflow", "scipy", etc.],
  "model_structure": {{
    "description": "model description",
    "layers": "layer information",
    "parameters": "parameter information"
  }}
}}

**IMPORTANT**: You MUST return valid JSON. If you find no information, return empty arrays/strings, but still return valid JSON."""
                
                response = chat_with_llm(prompt, model_name=self.llm_model_name)
                
                # Try to extract JSON from response
                import json
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    try:
                        result = json.loads(json_match.group(0))
                        
                        # Convert to PseudocodeBlock objects
                        for block_data in result.get("pseudocode_blocks", []):
                            all_pseudocode_blocks.append(PseudocodeBlock(
                                algorithm_name=block_data.get("algorithm_name", "Unknown"),
                                steps=block_data.get("steps", []),
                                input=block_data.get("input", ""),
                                output=block_data.get("output", ""),
                                raw_text="\n".join(block_data.get("steps", [])),
                                provenance="llm_reconstructed_pseudocode",
                            ))
                        
                        framework = result.get("framework", "").strip()
                        if framework:
                            all_frameworks.append(framework)
                        
                        dependencies = result.get("dependencies", [])
                        if isinstance(dependencies, str):
                            dependencies = [d.strip() for d in dependencies.split(',') if d.strip()]
                        all_dependencies.extend(dependencies)
                        
                        model_structure = result.get("model_structure", {})
                        if model_structure:
                            all_model_structures.append(model_structure)
                            
                    except json.JSONDecodeError as e:
                        self.analysis_errors.append({"type": type(e).__name__, "phase": "extract_with_llm"})
                        logger.warning(f"⚠️  Failed to parse LLM JSON response for chunk {chunk_idx + 1}: {e}")
                        logger.debug(f"Response: {response[:500]}")
            
            # Merge results from all chunks
            final_framework = all_frameworks[0] if all_frameworks else ""
            final_dependencies = list(set(all_dependencies))  # Remove duplicates
            final_model_structure = all_model_structures[0] if all_model_structures else {}
            
            logger.info(f"📦 LLM extracted: {len(all_pseudocode_blocks)} pseudocode blocks, framework='{final_framework}', dependencies={final_dependencies}")
            
            return {
                "pseudocode_blocks": all_pseudocode_blocks,
                "framework": final_framework,
                "dependencies": final_dependencies,
                "model_structure": final_model_structure
            }
            
        except Exception as e:
            self.analysis_errors.append({"type": type(e).__name__, "phase": "extract_with_llm"})
            logger.warning(f"⚠️  LLM-assisted extraction failed: {e}")
            return None
    
    def _evaluate_pseudocode_with_llm(self, pseudocode_blocks: List[PseudocodeBlock]) -> LLMEvaluationResult:
        """Use LLM to evaluate pseudocode for reproducibility"""
        if not pseudocode_blocks:
            return LLMEvaluationResult(
                reproducibility_score=0.0,
                completeness_score=0.0
            )
        
        if not (LLM_AVAILABLE and self.llm_enabled):
            logger.warning("⚠️  LLM not available, using basic evaluation")
            # Basic evaluation without LLM
            total_score = 0.0
            for block in pseudocode_blocks:
                score = 0.5  # Base score
                if block.input and block.output:
                    score += 0.2
                if len(block.steps) >= 5:
                    score += 0.3
                total_score += min(score, 1.0)
            
            avg_score = total_score / len(pseudocode_blocks) if pseudocode_blocks else 0.0
            return LLMEvaluationResult(
                reproducibility_score=avg_score,
                completeness_score=avg_score
            )
        
        try:
            # Prepare pseudocode text
            pseudocode_text = "\n\n".join([
                f"### {block.algorithm_name}\n{block.raw_text}"
                for block in pseudocode_blocks
            ])
            
            # Build prompt
            prompt = self.PSEUDOCODE_EVALUATION_PROMPT.format(
                pseudocode=pseudocode_text
            )
            
            # Call LLM
            logger.info("🤖 Calling LLM to evaluate pseudocode...")
            response = chat_with_llm(
                text_prompt=prompt,
                model_name=self.llm_model_name,
                system_prompt="You are a code evaluation expert. Always return valid JSON.",
                temperature=0.0,
                max_tokens=2000
            )
            
            # Parse JSON response
            # Try to extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result_dict = json.loads(json_match.group(0))
            else:
                # Try to parse entire response as JSON
                result_dict = json.loads(response)
            
            # IMPROVED: Handle "unknown" and empty strings properly
            identified_framework = result_dict.get("identified_framework", "")
            if identified_framework and identified_framework.lower() in ["unknown", "none", "n/a", ""]:
                identified_framework = ""
            
            identified_dependencies = result_dict.get("identified_dependencies", [])
            if isinstance(identified_dependencies, str):
                # Handle case where dependencies is a string
                identified_dependencies = [d.strip() for d in identified_dependencies.split(',') if d.strip() and d.strip().lower() not in ["none", "n/a"]]
            
            return LLMEvaluationResult(
                reproducibility_score=float(result_dict.get("reproducibility_score", 0.0)),
                completeness_score=float(result_dict.get("completeness_score", 0.0)),
                identified_framework=identified_framework,
                identified_dependencies=identified_dependencies,
                model_structure=result_dict.get("model_structure", {}),
                conversion_difficulty=result_dict.get("conversion_difficulty", "unknown"),
                missing_components=result_dict.get("missing_components", [])
            )
        except Exception as e:
            self.analysis_errors.append({"type": type(e).__name__, "phase": "evaluate_pseudocode_with_llm"})
            logger.error(f"❌ LLM evaluation failed: {e}")
            # Fallback to basic evaluation
            return self._evaluate_pseudocode_with_llm([])  # Will use basic evaluation
    
    def _extract_framework_from_text(self, markdown_content: str) -> Optional[Dict[str, Any]]:
        """Extract framework and dependencies from text using LLM"""
        if not (LLM_AVAILABLE and self.llm_enabled):
            return None
        
        try:
            # IMPROVED: Increased context window for better framework/dependency extraction
            content_sample = markdown_content[:10000] if len(markdown_content) > 10000 else markdown_content
            
            prompt = f"""You are an expert in analyzing research papers. Analyze this paper text and extract:

1. **Framework name**: Look for mentions of PyTorch, TensorFlow, JAX, Keras, or other deep learning frameworks. Check for phrases like "implemented in", "using", "based on", etc. Return the framework name or empty string if not found.

2. **Dependencies/libraries**: Extract ALL mentioned Python libraries, packages, or dependencies. Look for:
   - Import statements mentioned in text
   - Library names in methodology sections
   - Dependencies in implementation details
   Examples: numpy, scipy, torch, tensorflow, sklearn, pandas, matplotlib, etc.

3. **Model structure information**: Extract any information about:
   - Model architecture description
   - Layer types and configurations
   - Parameter counts or model size

**Text**:
{content_sample}

**IMPORTANT**: You MUST return valid JSON. Be thorough in extracting dependencies - even common ones like numpy, scipy should be included if the paper uses them.

Return JSON:
{{
  "framework": "PyTorch or TensorFlow or JAX or Keras or empty string",
  "dependencies": ["numpy", "scipy", "torch", "tensorflow", etc.],
  "model_structure": {{
    "description": "model description",
    "layers": "layer information",
    "parameters": "parameter information"
  }}
}}"""
            
            response = chat_with_llm(prompt, model_name=self.llm_model_name)
            
            import json
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group(0))
                    framework = result.get("framework", "").strip()
                    dependencies = result.get("dependencies", [])
                    if isinstance(dependencies, str):
                        dependencies = [d.strip() for d in dependencies.split(',') if d.strip()]
                    
                    logger.info(f"📦 LLM framework extraction: framework='{framework}', dependencies={dependencies}")
                    return {
                        "framework": framework,
                        "dependencies": dependencies,
                        "model_structure": result.get("model_structure", {})
                    }
                except json.JSONDecodeError as e:
                    self.analysis_errors.append({"type": type(e).__name__, "phase": "extract_framework_from_text"})
                    logger.warning(f"⚠️  Failed to parse LLM JSON response: {e}")
                    logger.debug(f"Response: {response[:500]}")
            
            return None
        except Exception as e:
            self.analysis_errors.append({"type": type(e).__name__, "phase": "extract_framework_from_text"})
            logger.warning(f"⚠️  LLM framework extraction failed: {e}")
            return None
    
    def _parse_framework_and_dependencies(self, code_blocks: List[CodeBlock]) -> FrameworkAnalysisResult:
        """Parse framework, dependencies and model structure from code"""
        framework = ""
        dependencies = []
        model_structure = {
            "layers": [],
            "parameters": {},
            "architecture": ""
        }
        
        for block in code_blocks:
            if block.language == "python":
                code = block.code
                
                # Extract imports
                import_pattern = re.compile(
                    r'^(?:import|from)\s+(\w+)(?:\s+import|\s*$|\s+as)',
                    re.MULTILINE
                )
                imports = import_pattern.findall(code)
                dependencies.extend(imports)
                
                # Detect framework (also check comments)
                code_lower = code.lower()
                
                # Also extract from comments like "# Dependencies: numpy, torch"
                if "dependencies" in code_lower:
                    deps_match = re.search(r'dependencies[:\s]+([^\n]+)', code_lower)
                    if deps_match:
                        deps_str = deps_match.group(1).strip()
                        deps_list = [d.strip() for d in deps_str.split(',') if d.strip()]
                        dependencies.extend(deps_list)
                if "torch" in code_lower or "pytorch" in code_lower:
                    framework = "PyTorch"
                elif "tensorflow" in code_lower or "tf." in code_lower:
                    framework = "TensorFlow"
                elif "jax" in code_lower:
                    framework = "JAX"
                elif "keras" in code_lower:
                    framework = "Keras"
                elif "framework" in code_lower:
                    # Try to extract framework name from comments like "# Framework: PyTorch"
                    framework_match = re.search(r'framework[:\s]+(\w+)', code_lower)
                    if framework_match:
                        framework = framework_match.group(1).capitalize()
                
                # Extract model structure
                if "class " in code and "nn.Module" in code:
                    # PyTorch model
                    class_match = re.search(r'class\s+(\w+).*?nn\.Module', code)
                    if class_match:
                        model_structure["architecture"] = class_match.group(1)
                    
                    # Extract layers
                    layer_patterns = [
                        (r'nn\.Linear\([^)]+\)', "Linear"),
                        (r'nn\.Conv2d\([^)]+\)', "Conv2d"),
                        (r'nn\.TransformerEncoder', "TransformerEncoder"),
                        (r'nn\.LSTM\([^)]+\)', "LSTM"),
                        (r'nn\.GRU\([^)]+\)', "GRU"),
                    ]
                    for pattern, layer_name in layer_patterns:
                        if re.search(pattern, code):
                            model_structure["layers"].append(layer_name)
        
        dependencies = list(set(dependencies))  # Remove duplicates
        
        return FrameworkAnalysisResult(
            framework=framework,
            dependencies=dependencies,
            model_structure=model_structure
        )
    
    def _run_tests(self, code_blocks: List[CodeBlock]) -> TestResult:
        """Record each block separately; loading definitions is not exercising them.

        This validates the supplied snippets only. It makes no assertion about
        paper-level algorithm correctness, external dependencies, or test data.
        """
        records = []
        artifact_root = Path(self.output_base_dir) / "code_validation" / uuid.uuid4().hex
        artifact_root.mkdir(parents=True, exist_ok=False)
        for index, block in enumerate(code_blocks):
            entry = {"block_index": index, "section": block.section,
                     "line_start": block.line_start, "line_end": block.line_end,
                     "provenance": block.provenance,
                     "source_sha256": hashlib.sha256(block.code.encode("utf-8")).hexdigest(),
                     "syntax": "NOT_RUN", "wrapper_syntax": "NOT_RUN", "import": "NOT_RUN",
                     "load": "NOT_RUN", "execute": "NOT_RUN", "assertion": "NOT_APPLICABLE",
                     "status": "NOT_RUN", "error": None, "stdout": None, "stderr": None}
            records.append(entry)
            original_path = artifact_root / f"block_{index:04d}_original.txt"
            atomic_write_bytes(original_path, block.code.encode("utf-8"))
            entry["source_path"] = str(original_path)
            if block.language.lower() != "python":
                entry.update(status="NOT_APPLICABLE", syntax="NOT_APPLICABLE")
                continue
            try:
                tree = ast.parse(block.code)
                entry["syntax"] = "SUCCEEDED"
            except SyntaxError as error:
                entry.update(syntax="FAILED", status="FAILED", error=f"SyntaxError at line {error.lineno}: {error.msg}")
                continue
            definition_only = bool(tree.body) and all(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))
                or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))
                for node in tree.body)
            has_assert = any(isinstance(node, ast.Assert) for node in tree.body)
            has_import = any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body)
            entry["definition_only"] = definition_only
            entry["import"] = "NOT_RUN" if has_import else "NOT_APPLICABLE"
            entry["assertion"] = "NOT_RUN" if has_assert else "NOT_APPLICABLE"
            body = textwrap.indent(block.code or "pass", "    ")
            wrapped = ("try:\n" + body + "\n    _paperarena_validation = {'status': 'SUCCEEDED'}\n"
                       "except Exception as _paperarena_error:\n"
                       "    _paperarena_validation = {'status': 'FAILED', 'error_type': type(_paperarena_error).__name__, 'error': str(_paperarena_error)}\n"
                       "_paperarena_validation\n")
            wrapped_path = artifact_root / f"block_{index:04d}_wrapped.py"
            atomic_write_bytes(wrapped_path, wrapped.encode("utf-8"))
            entry.update(wrapper_path=str(wrapped_path), wrapper_sha256=hashlib.sha256(wrapped.encode("utf-8")).hexdigest())
            try:
                ast.parse(wrapped)
                entry["wrapper_syntax"] = "SUCCEEDED"
            except SyntaxError as error:
                entry.update(wrapper_syntax="FAILED", status="FAILED", error=f"Wrapper SyntaxError: {error}")
                continue
            if not self.code_executor:
                continue
            try:
                returned = self.code_executor(wrapped)
                if hasattr(returned, "output"):
                    outcome = returned.output
                    logs = getattr(returned, "logs", None)
                elif isinstance(returned, tuple) and len(returned) == 3:
                    outcome, logs, _ = returned
                else:
                    outcome, logs = returned, None
                entry["executor_logs"] = logs
                if not isinstance(outcome, dict) or outcome.get("status") not in ("SUCCEEDED", "FAILED"):
                    entry.update(status="INVALID_OUTPUT", error="Executor did not return the declared validation schema")
                    continue
                entry["status"] = outcome["status"]
                if outcome["status"] == "FAILED":
                    entry["error"] = outcome.get("error")
                    entry["error_type"] = outcome.get("error_type")
                    entry["load"] = "FAILED"
                    entry["execute"] = "FAILED"
                    if outcome.get("error_type") in ("ImportError", "ModuleNotFoundError"):
                        entry["import"] = "FAILED"
                        entry["execute"] = "NOT_RUN"
                    if outcome.get("error_type") == "AssertionError":
                        entry["assertion"] = "FAILED"
                else:
                    entry["load"] = "SUCCEEDED"
                    entry["execute"] = "NOT_RUN" if definition_only else "SUCCEEDED"
                    if has_import:
                        entry["import"] = "SUCCEEDED"
                    if has_assert:
                        entry["assertion"] = "SUCCEEDED"
            except Exception as error:
                entry.update(status="UNKNOWN", error_type=type(error).__name__, error=str(error))
            finally:
                logs_path = artifact_root / f"block_{index:04d}_execution.json"
                atomic_write_json(logs_path, entry)
                entry["execution_path"] = str(logs_path)
        applicable = [row for row in records if row["syntax"] != "NOT_APPLICABLE"]
        syntax_passed = bool(applicable) and all(row["syntax"] == "SUCCEEDED" for row in applicable)
        execution_passed = bool(applicable) and all(row["execute"] == "SUCCEEDED" for row in applicable)
        statuses = [row["status"] for row in applicable]
        aggregate = ("NOT_APPLICABLE" if not applicable else
                     "SUCCEEDED" if execution_passed else
                     "PARTIAL" if any(row["execute"] == "SUCCEEDED" for row in applicable) else
                     "FAILED" if any(state in ("FAILED", "INVALID_OUTPUT", "UNKNOWN") for state in statuses) else "NOT_RUN")
        atomic_write_json(artifact_root / "blocks.json", {"status": aggregate, "blocks": records,
            "claim": "snippet loading/execution only; paper algorithm accuracy NOT_RUN"})
        return TestResult(
            syntax_check={"passed": syntax_passed, "status": "NOT_APPLICABLE" if not applicable else "SUCCEEDED" if syntax_passed else "FAILED",
                          "errors": [row["error"] for row in applicable if row["syntax"] == "FAILED"]},
            execution_test={"passed": execution_passed, "status": aggregate, "output": "", "artifact_dir": str(artifact_root),
                            "errors": [row["error"] for row in applicable if row["error"]], "planned_blocks": len(code_blocks),
                            "applicable_blocks": len(applicable), "executed_blocks": sum(row["execute"] == "SUCCEEDED" for row in applicable)},
            logic_check={"passed": False, "status": "NOT_RUN", "warnings": ["No algorithm correctness oracle was provided"]},
            accuracy_test={"available": False, "status": "NOT_RUN", "result": ""}, block_results=records,
        )
    
    def _classify_paper(
        self,
        code_existence: CodeExistenceResult,
        completeness: CompletenessResult,
        llm_evaluation: LLMEvaluationResult,
        test_results: TestResult
    ) -> PaperClassification:
        """Classify paper into three categories"""
        # Check if code exists
        if not code_existence.has_code:
            if code_existence.has_pseudocode:
                # Check pseudocode reproducibility
                if llm_evaluation.reproducibility_score > 0.7:
                    return PaperClassification(
                        category="partial",
                        emoji="📙",
                        reason="Only pseudocode, but high reproducibility",
                        score=llm_evaluation.reproducibility_score
                    )
                else:
                    return PaperClassification(
                        category="none",
                        emoji="📕",
                        reason="Only pseudocode, and low reproducibility",
                        score=llm_evaluation.reproducibility_score
                    )
            else:
                return PaperClassification(
                    category="none",
                    emoji="📕",
                    reason="No code or pseudocode at all",
                    score=0.0
                )
        
        # Check code completeness and tests
        if completeness.completeness_score > 0.8 and test_results.execution_test.get("passed", False):
            return PaperClassification(
                category="complete",
                emoji="📗",
                reason="Code complete and tests passed",
                score=completeness.completeness_score
            )
        elif completeness.completeness_score > 0.5:
            return PaperClassification(
                category="partial",
                emoji="📙",
                reason=f"Code incomplete (completeness: {completeness.completeness_score:.2f})",
                score=completeness.completeness_score
            )
        else:
            return PaperClassification(
                category="none",
                emoji="📕",
                reason=f"Code incomplete and unusable (completeness: {completeness.completeness_score:.2f})",
                score=completeness.completeness_score
            )
    
    def _generate_report(
        self,
        pdf_name: str,
        code_existence: CodeExistenceResult,
        completeness: CompletenessResult,
        llm_evaluation: LLMEvaluationResult,
        framework_analysis: FrameworkAnalysisResult,
        test_results: TestResult,
        classification: PaperClassification,
        code_blocks: List[CodeBlock],
        pseudocode_blocks: List[PseudocodeBlock]
    ) -> str:
        """Generate comprehensive Markdown report"""
        report = f"# Code Extraction and Reproducibility Evaluation Report\n\n"
        report += f"## Paper Information\n\n"
        report += f"- **Paper name**: {pdf_name}\n"
        report += f"- **Evaluation time (UTC)**: {datetime.now(timezone.utc).isoformat()}\n\n"
        
        # Code existence
        report += f"## 📊 Code Detection Results\n\n"
        report += f"- **Code blocks count**: {code_existence.code_blocks_count}\n"
        report += f"- **Pseudocode blocks count**: {code_existence.pseudocode_blocks_count}\n"
        report += f"- **Code exists**: {'✅ Yes' if code_existence.has_code else '❌ No'}\n"
        report += f"- **Pseudocode exists**: {'✅ Yes' if code_existence.has_pseudocode else '❌ No'}\n\n"
        
        # Completeness
        report += f"## ✅ Completeness Check\n\n"
        report += f"- **Code completeness**: {completeness.code_completeness:.2f}/1.0\n"
        report += f"- **Pseudocode completeness**: {completeness.pseudocode_completeness:.2f}/1.0\n"
        report += f"- **Overall completeness**: {completeness.completeness_score:.2f}/1.0\n"
        report += f"- **Completeness score**: {completeness.completeness_score:.2f}\n"
        if completeness.missing_parts:
            report += f"- **Missing components**: {', '.join(completeness.missing_parts)}\n"
        report += "\n"
        
        # LLM evaluation
        report += "## 🤖 LLM Evaluation Results\n\n" if self.llm_enabled else "## Heuristic Evaluation (LLM disabled)\n\n"
        report += f"- **Reproducibility score**: {llm_evaluation.reproducibility_score:.2f}/1.0\n"
        report += f"- **Completeness score**: {llm_evaluation.completeness_score:.2f}/1.0\n"
        # Always show framework/dependencies (even if empty) for parser compatibility
        report += f"- **Identified framework**: {llm_evaluation.identified_framework or 'None'}\n"
        if llm_evaluation.identified_dependencies:
            report += f"- **Identified dependencies**: {', '.join(llm_evaluation.identified_dependencies)}\n"
        else:
            report += f"- **Identified dependencies**: None\n"
        if llm_evaluation.model_structure:
            report += f"- **Model structure**: {json.dumps(llm_evaluation.model_structure, ensure_ascii=False, indent=2)}\n"
        report += f"- **Conversion difficulty**: {llm_evaluation.conversion_difficulty}\n"
        if llm_evaluation.missing_components:
            report += f"- **Missing components**: {', '.join(llm_evaluation.missing_components)}\n"
        else:
            report += f"- **Missing components**: None\n"
        report += "\n"
        
        # Framework analysis (IMPROVED: Always show framework/dependencies, use fallback from llm_evaluation)
        report += f"## 🔧 Framework and Dependency Analysis\n\n"
        # Use framework_analysis first, fallback to llm_evaluation if empty
        final_framework = framework_analysis.framework or llm_evaluation.identified_framework or "None"
        final_dependencies = framework_analysis.dependencies or llm_evaluation.identified_dependencies or []
        report += f"- **Identified framework**: {final_framework}\n"
        if final_dependencies:
            report += f"- **Identified dependencies**: {', '.join(final_dependencies)}\n"
        else:
            report += f"- **Identified dependencies**: None\n"
        if framework_analysis.model_structure:
            report += f"- **Model structure**:\n"
            report += f"  ```python\n"
            report += f"  {json.dumps(framework_analysis.model_structure, ensure_ascii=False, indent=2)}\n"
            report += f"  ```\n"
        
        # Web search enhancement results (if available)
        if hasattr(framework_analysis, 'web_search_metadata') and framework_analysis.web_search_metadata:
            web_meta = framework_analysis.web_search_metadata
            report += f"\n### 🌐 Web Search Enhancement\n\n"
            if web_meta.get("github_repos"):
                report += f"- **GitHub Repositories Found**:\n"
                for repo in web_meta["github_repos"]:
                    report += f"  - [{repo}]({repo})\n"
            if web_meta.get("web_search_notes"):
                report += f"- **Additional Notes from Web Search**: {web_meta['web_search_notes']}\n"
            if web_meta.get("web_search_confidence"):
                report += f"- **Web Search Confidence**: {web_meta['web_search_confidence']}\n"
        
        report += "\n"
        
        # Test results
        report += f"## 🧪 Test Results\n\n"
        report += f"- **Syntax check**: {test_results.syntax_check.get('status', 'UNKNOWN')}\n"
        if test_results.syntax_check.get('errors'):
            for error in test_results.syntax_check['errors']:
                report += f"  - {error}\n"
        report += f"- **Execution test**: {test_results.execution_test.get('status', 'UNKNOWN')}\n"
        if test_results.execution_test.get('errors'):
            for error in test_results.execution_test['errors']:
                report += f"  - {error}\n"
        report += f"- **Logic check**: {test_results.logic_check.get('status', 'UNKNOWN')}\n"
        if test_results.logic_check.get('warnings'):
            for warning in test_results.logic_check['warnings']:
                report += f"  - {warning}\n"
        report += f"- **Accuracy test**: {test_results.accuracy_test.get('status', 'UNKNOWN')}\n"
        report += "\n"
        
        # Classification
        report += f"## 📋 Paper Classification\n\n"
        report += f"### {classification.emoji} {classification.category}\n\n"
        report += f"**Classification reason**: {classification.reason}\n"
        report += f"**Classification confidence**: {classification.score:.2f}\n\n"
        
        # Code blocks
        if code_blocks:
            report += f"## Extracted Code Blocks\n\n"
            for i, block in enumerate(code_blocks):
                report += f"### Code Block {i + 1}\n\n"
                report += f"- **Location**: {block.section or 'Unknown section'}, Lines {block.line_start}-{block.line_end}\n"
                report += f"- **Language**: {block.language}\n"
                report += f"- **Provenance**: {block.provenance}\n"
                report += f"\n**Code**:\n\n```{block.language}\n{block.code}\n```\n\n"
        
        # Pseudocode blocks
        if pseudocode_blocks:
            report += f"## Extracted Pseudocode\n\n"
            for i, block in enumerate(pseudocode_blocks):
                report += f"### {block.algorithm_name}\n\n"
                report += f"- **Provenance**: {block.provenance}\n"
                if block.input:
                    report += f"- **Input**: {block.input}\n"
                if block.output:
                    report += f"- **Output**: {block.output}\n"
                report += f"- **Location**: Lines {block.line_start}-{block.line_end}\n"
                report += f"\n**Steps**:\n\n"
                for j, step in enumerate(block.steps, 1):
                    report += f"{j}. {step}\n"
                report += "\n"
        
        return report
    
    def forward(
        self,
        pdf_name: str,
        query: Optional[str] = None,
    ) -> str:
        """
        Extract code, evaluate completeness and reproducibility, and classify paper
        
        Args:
            pdf_name: Name of the PDF file (without .pdf extension)
            query: Optional query to search for specific algorithms or code
            
        Returns:
            Markdown report with comprehensive evaluation
        """
        self.last_tool_status = "UNKNOWN"
        try:
            logger.info(f"🔍 Extracting and evaluating code from PDF: {pdf_name}")
            
            # Get markdown content
            markdown_content = self._get_markdown_content(pdf_name, query)
            
            # Extract paper title from markdown (for web search)
            paper_title = self._extract_paper_title(markdown_content, pdf_name)
            
            # Web search enhancement (if enabled)
            web_results = {}
            if self.enable_web_search:
                web_results = self._search_web_for_paper(paper_title)
            
            # Extract code and pseudocode
            code_blocks = self._extract_code_blocks(markdown_content)
            pseudocode_blocks = self._extract_pseudocode(markdown_content, query)
            
            # IMPROVED: More aggressive LLM extraction strategy
            # Try LLM extraction for better coverage (even if regex found something)
            llm_extracted_framework = None
            llm_extracted_dependencies = []
            if LLM_AVAILABLE and self.llm_enabled:
                # IMPROVED: More aggressive trigger conditions
                # Try LLM extraction if:
                # 1. No code/pseudocode found with regex, OR
                # 2. Only code found but no pseudocode, OR
                # 3. Very few code blocks found (less than 2) - might be incomplete, OR
                # 4. Document is long (might have more content) - always try for long docs
                should_try_llm = (
                    (len(code_blocks) == 0 and len(pseudocode_blocks) == 0) or
                    (len(code_blocks) > 0 and len(pseudocode_blocks) == 0) or
                    (len(code_blocks) < 2 and len(pseudocode_blocks) == 0) or
                    len(markdown_content) > 10000  # Always try for long documents
                )
                
                if should_try_llm:
                    logger.info("🔍 Trying LLM-assisted extraction for better coverage...")
                    llm_extracted = self._extract_with_llm(markdown_content)
                    if llm_extracted:
                        # Add LLM-extracted pseudocode blocks
                        pseudocode_blocks.extend(llm_extracted.get("pseudocode_blocks", []))
                        # Store framework and dependencies for later use
                        llm_extracted_framework = llm_extracted.get("framework", "")
                        llm_extracted_dependencies = llm_extracted.get("dependencies", [])
                        logger.info(f"📦 LLM extracted: {len(llm_extracted.get('pseudocode_blocks', []))} pseudocode blocks, framework={llm_extracted_framework}, dependencies={llm_extracted_dependencies}")
            
            # 1. Detect code existence
            code_existence = self._detect_code_existence(code_blocks, pseudocode_blocks)
            
            # 2. Check completeness
            completeness = self._check_completeness(code_blocks, pseudocode_blocks)
            
            # 3. LLM evaluation of pseudocode
            llm_evaluation = self._evaluate_pseudocode_with_llm(pseudocode_blocks)
            
            # 4. Parse framework and dependencies (also from text if no code blocks)
            framework_analysis = self._parse_framework_and_dependencies(code_blocks)
            
            # If no framework found in code, try to extract from text
            if not framework_analysis.framework and LLM_AVAILABLE:
                # First, use framework/dependencies from LLM-assisted code/pseudocode extraction if available
                if llm_extracted_framework:
                    framework_analysis.framework = llm_extracted_framework
                    framework_analysis.dependencies.extend(llm_extracted_dependencies)
                    logger.info(f"✅ Using framework/dependencies from LLM-assisted extraction: {llm_extracted_framework}")
                else:
                    # Fallback to dedicated framework extraction
                    logger.info("🔍 No framework found in code, trying LLM-assisted extraction from text...")
                    text_analysis = self._extract_framework_from_text(markdown_content)
                    if text_analysis:
                        framework_analysis.framework = text_analysis.get("framework", framework_analysis.framework)
                        framework_analysis.dependencies.extend(text_analysis.get("dependencies", []))
                        if text_analysis.get("model_structure"):
                            framework_analysis.model_structure.update(text_analysis["model_structure"])
                        logger.info(f"✅ Using framework/dependencies from dedicated LLM extraction: {framework_analysis.framework}")
            
            # Merge LLM evaluation results into framework_analysis (CRITICAL FIX)
            # This ensures that LLM-extracted framework/dependencies are included in the report
            if llm_evaluation.identified_framework and not framework_analysis.framework:
                framework_analysis.framework = llm_evaluation.identified_framework
                logger.info(f"✅ Merged framework from LLM evaluation: {framework_analysis.framework}")
            if llm_evaluation.identified_dependencies:
                framework_analysis.dependencies.extend(llm_evaluation.identified_dependencies)
                logger.info(f"✅ Merged dependencies from LLM evaluation: {llm_evaluation.identified_dependencies}")
            
            # Remove duplicate dependencies
            framework_analysis.dependencies = list(set(framework_analysis.dependencies))
            
            # Integrate web search results (if available)
            if web_results and self.enable_web_search:
                logger.info("🔄 Integrating web search results...")
                pdf_extracted_data = {
                    "code_blocks": code_blocks,
                    "pseudocode_blocks": pseudocode_blocks,
                    "framework": framework_analysis.framework,
                    "dependencies": framework_analysis.dependencies
                }
                integrated_data = self._integrate_web_results_with_llm(pdf_extracted_data, web_results)
                
                # Update framework and dependencies from integrated results
                if integrated_data.get("framework") and not framework_analysis.framework:
                    framework_analysis.framework = integrated_data["framework"]
                    logger.info(f"✅ Updated framework from web search: {framework_analysis.framework}")
                if integrated_data.get("dependencies"):
                    framework_analysis.dependencies.extend(integrated_data["dependencies"])
                    framework_analysis.dependencies = list(set(framework_analysis.dependencies))
                    logger.info(f"✅ Updated dependencies from web search: {len(integrated_data['dependencies'])} added")
                
                # Store web search metadata (dynamically add to dataclass instance)
                if not hasattr(framework_analysis, 'web_search_metadata'):
                    framework_analysis.web_search_metadata = {}
                framework_analysis.web_search_metadata.update({
                    "github_repos": integrated_data.get("github_repos", []),
                    "web_search_notes": integrated_data.get("web_search_notes", ""),
                    "web_search_confidence": integrated_data.get("web_search_confidence", "unknown")
                })
            
            # 5. Run tests
            test_results = self._run_tests(code_blocks)
            
            # 6. Classify paper
            classification = self._classify_paper(
                code_existence,
                completeness,
                llm_evaluation,
                test_results
            )
            
            # Generate report
            self.last_analysis = {
                "code_existence": asdict(code_existence), "completeness": asdict(completeness),
                "llm_evaluation": asdict(llm_evaluation), "framework": asdict(framework_analysis),
                "tests": asdict(test_results), "classification": asdict(classification),
                "code_blocks": [asdict(block) for block in code_blocks],
                "pseudocode_blocks": [asdict(block) for block in pseudocode_blocks],
            }
            report = self._generate_report(
                pdf_name,
                code_existence,
                completeness,
                llm_evaluation,
                framework_analysis,
                test_results,
                classification,
                code_blocks,
                pseudocode_blocks
            )
            
            logger.info(f"✅ Code extraction and evaluation completed. Classification: {classification.emoji} {classification.category}")
            if not self.llm_enabled:
                report = "> LLM analysis was disabled. Evaluation scores below are heuristic fallbacks, not model assessments.\n\n" + report
            if not self.execute_code:
                report = "> Extracted Python was not executed. Syntax checks and code presence do not establish algorithm correctness.\n\n" + report
            self.last_tool_status = "SUCCEEDED"
            self.last_test_results = test_results
            return report
            
        except Exception as e:
            error_msg = f"❌ Code extraction and evaluation failed: {str(e)}"
            logger.error(error_msg)
            self.last_tool_status = "FAILED"
            return f"Error: {str(e)}"

    def interpret_execution_result(self, result) -> str:
        """Declared operation status; code-block validity stays in its own ledger."""
        return getattr(self, "last_tool_status", "UNKNOWN")


if __name__ == "__main__":
    # Test the code extractor tool
    tool = CodeExtractorTool()
    tool.setup()
    
    result = tool.forward(
        pdf_name="test_paper",
    )
    print(result)
