import os
import sys
from loguru import logger
from dotenv import load_dotenv

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tools.paper_resolver import PaperResolverTool
from tools.pdf_parser import PdfParserTool
from tools.gemini_file_manager import GeminiFileManagerTool

def test_tools():
    load_dotenv(override=True)
    
    logger.info("🧪 Testing PaperResolverTool...")
    resolver = PaperResolverTool()
    # Test with a known arXiv ID
    res = resolver.forward(arxiv_id="2312.00752") # Mamba paper
    logger.info(f"Resolver Result (arXiv): {res}")
    
    logger.info("\n🧪 Testing PdfParserTool...")
    parser = PdfParserTool()
    # Create a dummy PDF for testing if needed, or use an existing one.
    # For now, we'll skip if no PDF is found, or user can provide one.
    # We will just instantiate it to check imports.
    logger.info("PdfParserTool instantiated successfully.")
    
    logger.info("\n🧪 Testing GeminiFileManagerTool...")
    gemini_tool = GeminiFileManagerTool()
    logger.info("GeminiFileManagerTool instantiated successfully.")
    
    logger.info("\n✅ All tools instantiated. Run full test with actual files if needed.")

if __name__ == "__main__":
    test_tools()
