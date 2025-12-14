"""
Database Search Tool for smolagents
Searches conference papers database for keyword statistics
"""

import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from json_repair import repair_json

from smolagents import Tool
from loguru import logger

from .llm_chat import chat_with_llm


class DatabaseSearch(Tool):
    """
    Tool for searching conference papers database and generating statistics.

    This tool searches through conference paper databases (ICML, ICLR, NeurIPS, CVPR, WWW)
    to count papers matching specific keywords and status criteria.
    """

    name = "database_search"
    description = (
        "Search conference papers database for keyword statistics. Takes conference name, "
        "year, status level, and keywords as input. Returns count of matching papers "
        "with detailed breakdown by status."
    )
    inputs = {
        "conference": {
            "type": "string",
            "description": "Conference name: 'icml', 'iclr', 'nips', 'cvpr', or 'www'",
        },
        "year": {
            "type": "integer",
            "description": "Year of the conference (e.g., 2024, 2025)",
        },
        "status": {
            "type": "string",
            "description": "Paper status filter: 'top', 'medium', 'standard', or 'all'",
        },
        "keywords": {
            "type": "string",
            "description": "Keywords to search for in paper titles and abstracts",
        },
    }
    output_type = "string"

    def __init__(self, database_dir: Path | str = None):
        """Initialize the database search tool"""
        super().__init__()
        # Fixed path for database
        if database_dir is None:
            self.database_dir = Path(
                "/data/wdy/AgenticPaperQA/paper-prepare/selected_papers"
            )
        else:
            self.database_dir = Path(database_dir)

        # Status mapping for different conferences
        self.status_mapping = {
            "icml": {
                "top": ["Oral"],
                "medium": ["Spotlight"],
                "standard": ["Poster"],
                "all": ["Oral", "Spotlight", "Poster"],
            },
            "iclr": {
                "top": ["Oral"],
                "medium": ["Spotlight"],
                "standard": ["Poster"],
                "all": ["Oral", "Spotlight", "Poster"],
            },
            "nips": {
                "top": ["Oral"],
                "medium": ["Spotlight"],
                "standard": ["Poster"],
                "all": ["Oral", "Spotlight", "Poster"],
            },
            "cvpr": {
                "top": ["Award Candidate"],
                "medium": ["Highlight"],
                "standard": ["Poster"],
                "all": ["Award Candidate", "Highlight", "Poster"],
            },
            "www": {
                "top": ["Oral"],
                "medium": [],  # WWW doesn't have medium category
                "standard": ["Poster"],
                "all": ["Oral", "Poster"],
            },
        }

    def setup(self):
        """Setup method called before first use"""
        logger.info("🔧 Setting up database search tool...")
        self.is_initialized = True

    def _load_conference_data(
        self, conference: str, year: int
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Load conference data from JSON files.
        
        Supports two formats for backward compatibility:
        1. Old format: Single file per conference (e.g., "cvpr2025.json")
        2. New format: Individual JSON files per paper, filtered by conference field

        Args:
            conference: Conference name
            year: Conference year

        Returns:
            Tuple of (paper list, error message or None)
        """
        # Try old format first: single file per conference (backward compatibility)
        filename = f"{conference}{year}.json"
        filepath = self.database_dir / filename

        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    papers = json.load(f)

                if not isinstance(papers, list):
                    return (
                        None,
                        f"Invalid database format: expected list, got {type(papers)}",
                    )

                logger.info(f"📄 Loaded {len(papers)} papers from {filename} (old format)")
                return papers, None

            except json.JSONDecodeError as e:
                return None, f"Failed to parse JSON file: {e}"
            except Exception as e:
                return None, f"Failed to read database file: {e}"
        
        # New format: individual JSON files per paper
        # Load all JSON files and filter by conference
        papers = []
        json_files = list(self.database_dir.glob("*.json"))
        
        if not json_files:
            return None, f"Database file not found: {filepath} and no JSON files found in: {self.database_dir}"
        
        # Normalize conference name for matching
        conference_normalized = conference.lower()
        year_str = str(year)
        
        # Helper function to parse conference and year from string like "cvpr2025"
        def parse_conference_year(conf_str: str) -> tuple[str, int | None]:
            """Parse conference and year from string like 'cvpr2025'"""
            import re
            conf_str = conf_str.lower()
            # Try to match pattern like "cvpr2025" or "icml2024"
            match = re.match(r'([a-z]+)(\d{4})', conf_str)
            if match:
                return match.group(1), int(match.group(2))
            # If no year found, return conference name and None
            return conf_str, None
        
        for json_file in json_files:
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    paper_data = json.load(f)
                
                # Get conference field (format: "cvpr2025" or similar)
                paper_conference_str = paper_data.get("conference", "").lower()
                if not paper_conference_str:
                    continue
                
                # Parse conference and year from paper data
                paper_conf, paper_year = parse_conference_year(paper_conference_str)
                
                # Check if conference matches
                # Support both exact match and partial match (e.g., "cvpr" matches "cvpr2025")
                conference_matches = (
                    paper_conf == conference_normalized or
                    conference_normalized in paper_conf or
                    paper_conf.startswith(conference_normalized)
                )
                
                # Check if year matches
                # If paper has year, use it; otherwise check if year is in conference string
                if paper_year is not None:
                    year_matches = paper_year == year
                else:
                    # Fallback: check if year string is in conference string
                    year_matches = year_str in paper_conference_str
                
                if conference_matches and year_matches:
                    papers.append(paper_data)
                    
            except Exception as e:
                logger.warning(f"⚠️ Failed to load {json_file.name}: {e}")
                continue
        
        if not papers:
            return None, f"No papers found for {conference} {year} in {self.database_dir}"
        
        logger.info(f"📄 Loaded {len(papers)} papers for {conference} {year} from individual files")
        return papers, None

    def _expand_keywords(self, keywords: str) -> tuple[List[str], Optional[str]]:
        """
        Use LLM to expand keywords with similar terms

        Args:
            keywords: Original keywords

        Returns:
            Tuple of (expanded keyword list, error message or None)
        """
        prompt = f"""You are an expert in academic paper terminology. Given keywords, generate a STRICT and CONSERVATIVE expansion that includes only:

1. The original terms (exact match)
2. Common abbreviations/acronyms of the SAME concept
3. Singular/plural forms
4. Minor spelling variations (e.g., hyphenated vs non-hyphenated)

DO NOT include:
- Related but different concepts (e.g., "AI" is NOT the same as "machine learning")
- Broader or narrower terms
- Synonyms that change the meaning
- Sub-fields or applications

Original keywords: "{keywords}"

Please provide a JSON response:
{{
    "original_keywords": [list of original keywords split by common delimiters],
    "expanded_keywords": [CONSERVATIVE list with only abbreviations, singular/plural, exact variations],
    "reasoning": "explanation of conservative expansion"
}}

Examples:
- "machine learning" → ["machine learning", "machine-learning", "ML"] (NOT "AI", "deep learning", etc.)
- "computer vision" → ["computer vision", "computer-vision", "CV"]
- "natural language processing" → ["natural language processing", "NLP", "nlp"]

Be very conservative - if unsure whether to include a term, DON'T include it."""

        try:
            response = chat_with_llm(
                text_prompt=prompt,
                system_prompt="You are an expert at academic terminology and keyword expansion. Always respond with valid JSON.",
            )

            # Parse and repair JSON response
            result = json.loads(repair_json(response.strip()))

            if "expanded_keywords" not in result:
                return None, "LLM response missing 'expanded_keywords' field"

            expanded_keywords = result["expanded_keywords"]
            if not isinstance(expanded_keywords, list):
                return (
                    None,
                    "Invalid LLM response format: expanded_keywords must be a list",
                )

            # Clean and deduplicate keywords (case-insensitive)
            cleaned_keywords = []
            seen = set()
            for keyword in expanded_keywords:
                if isinstance(keyword, str):
                    cleaned = keyword.strip().lower()
                    if cleaned and cleaned not in seen:
                        cleaned_keywords.append(cleaned)
                        seen.add(cleaned)

            logger.info(f"🔍 Expanded {keywords} to {len(cleaned_keywords)} keywords")
            logger.debug(f"Expanded keywords: {cleaned_keywords}")

            return cleaned_keywords, None

        except json.JSONDecodeError as e:
            return None, f"Failed to parse LLM JSON response: {e}"
        except Exception as e:
            return None, f"Failed to expand keywords: {e}"

    def _matches_keywords(self, text: str, keywords: List[str]) -> bool:
        """
        Check if text matches any of the keywords (allows partial matches)

        Args:
            text: Text to search in (title + abstract)
            keywords: List of keywords to match

        Returns:
            True if any keyword matches, False otherwise
        """
        text_lower = text.lower()

        for keyword in keywords:
            # Allow partial matches - simply check if keyword is contained in text
            if keyword.lower() in text_lower:
                return True

        return False

    def _filter_by_status(
        self, papers: List[Dict[str, Any]], conference: str, status: str
    ) -> List[Dict[str, Any]]:
        """
        Filter papers by status criteria

        Args:
            papers: List of papers
            conference: Conference name
            status: Status filter (top/medium/standard/all)

        Returns:
            Filtered list of papers
        """
        if status == "all":
            return papers

        if conference not in self.status_mapping:
            logger.warning(f"⚠️ Unknown conference: {conference}")
            return papers

        if status not in self.status_mapping[conference]:
            logger.warning(f"⚠️ Unknown status: {status} for conference: {conference}")
            return papers

        allowed_statuses = self.status_mapping[conference][status]

        # Handle WWW medium case (no medium category)
        if conference == "www" and status == "medium":
            logger.info("ℹ️ WWW conference has no 'medium' status category")
            return []

        filtered_papers = []
        for paper in papers:
            paper_status = paper.get("status", "")
            if paper_status in allowed_statuses:
                filtered_papers.append(paper)

        logger.info(
            f"📊 Filtered {len(papers)} papers to {len(filtered_papers)} with status: {status}"
        )
        return filtered_papers

    def _generate_statistics(
        self,
        papers: List[Dict[str, Any]],
        matching_papers: List[Dict[str, Any]],
        conference: str,
        year: int,
        status: str,
        keywords: str,
        expanded_keywords: List[str],
    ) -> str:
        """
        Generate detailed statistics report

        Args:
            papers: All papers in database
            matching_papers: Papers matching keywords
            conference: Conference name
            year: Conference year
            status: Status filter
            keywords: Original keywords
            expanded_keywords: Expanded keyword list

        Returns:
            Formatted statistics report
        """
        total_papers = len(papers)
        matching_count = len(matching_papers)

        # Count by status
        status_counts = {}
        for paper in matching_papers:
            paper_status = paper.get("status", "Unknown")
            status_counts[paper_status] = status_counts.get(paper_status, 0) + 1

        # Generate report
        report = f"Database Search Results\n"
        report += "=" * 50 + "\n\n"

        report += f"Conference: {conference.upper()}\n"
        report += f"Year: {year}\n"
        report += f"Status Filter: {status}\n"
        report += f"Keywords: {keywords}\n\n"

        report += f"Search Summary:\n"
        report += f"- Total papers in database: {total_papers}\n"
        report += f"- Papers matching keywords: {matching_count}\n"
        report += f"- Match percentage: {(matching_count/total_papers*100):.2f}%\n\n"

        if status_counts:
            report += f"Breakdown by Status:\n"
            for status_type, count in sorted(status_counts.items()):
                report += f"- {status_type}: {count}\n"
            report += "\n"

        report += f"Keyword Expansion:\n"
        report += f"- Original: {keywords}\n"
        report += f"- Expanded to {len(expanded_keywords)} terms: {', '.join(expanded_keywords[:10])}"
        if len(expanded_keywords) > 10:
            report += f" ... (+{len(expanded_keywords)-10} more)"
        report += "\n\n"

        # Sample matching papers (first 3)
        if matching_papers:
            report += f"Sample Matching Papers:\n"
            for i, paper in enumerate(matching_papers[:3]):
                title = paper.get("title", "Unknown Title")
                paper_status = paper.get("status", "Unknown")
                report += f"{i+1}. [{paper_status}] {title}\n"

            if len(matching_papers) > 3:
                report += f"... and {len(matching_papers)-3} more papers\n"

        return report

    def forward(self, conference: str, year: int, status: str, keywords: str) -> str:
        """
        Search database for papers matching criteria

        Args:
            conference: Conference name (icml/iclr/nips/cvpr/www)
            year: Conference year
            status: Status filter (top/medium/standard/all)
            keywords: Keywords to search for

        Returns:
            Formatted search results and statistics
        """
        conference, year, status, keywords = (
            conference.lower(),
            int(year),
            status.lower(),
            keywords.lower(),
        )
        try:
            logger.info(
                f"🔍 Searching {conference.upper()} {year} for '{keywords}' with status '{status}'"
            )

            # Validate inputs
            if conference not in ["icml", "iclr", "nips", "cvpr", "www"]:
                return f"Error: Invalid conference '{conference}'. Must be one of: icml, iclr, nips, cvpr, www"

            if status not in ["top", "medium", "standard", "all"]:
                return f"Error: Invalid status '{status}'. Must be one of: top, medium, standard, all"

            # Load conference data
            papers, load_error = self._load_conference_data(conference, year)
            if load_error:
                return f"Error: {load_error}"

            # Expand keywords using LLM
            expanded_keywords, expand_error = self._expand_keywords(keywords)
            if expand_error:
                return f"Error: {expand_error}"

            # Filter papers by status
            filtered_papers = self._filter_by_status(papers, conference, status)

            # Find matching papers
            matching_papers = []
            for paper in filtered_papers:
                title = paper.get("title", "")
                abstract = paper.get("abstract", "")
                combined_text = f"{title} {abstract}"

                if self._matches_keywords(combined_text, expanded_keywords):
                    matching_papers.append(paper)

            # Generate statistics report
            result = self._generate_statistics(
                papers,
                matching_papers,
                conference,
                year,
                status,
                keywords,
                expanded_keywords,
            )

            logger.info("✅ Database search completed successfully")
            return result

        except Exception as e:
            error_msg = f"❌ Database search failed: {str(e)}"
            logger.error(error_msg)
            return f"Error: {str(e)}"


if __name__ == "__main__":
    # Test the database search tool
    search = DatabaseSearch()
    search.setup()

    from dotenv import load_dotenv

    load_dotenv("/data/wdy/AgenticPaperQA/smolagents/paper_qa/.env", verbose=True)

    # Example usage
    try:
        result = search.forward(
            conference="nips", year=2024, status="top", keywords="machine learning"
        )
        print("🔍 Database Search Result:")
        print("=" * 50)
        print(result)
    except Exception as e:
        print(f"❌ Database search test failed: {e}")

    # Test with different parameters
    try:
        result = search.forward(
            conference="cvpr",
            year=2025,
            status="all",
            keywords="computer vision deep learning",
        )
        print("\n🔍 Database Search Result 2:")
        print("=" * 50)
        print(result)
    except Exception as e:
        print(f"❌ Database search test 2 failed: {e}")
