"""Quick integration test for CUSTOM_LLM with minimal pipeline"""
import sys
import os
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(override=True)

from demo_research_agent import create_minimal_agent, build_llm_from_env
from loguru import logger

def test_minimal_llm_call():
    """Test that we can make a simple LLM call with the configured endpoint."""
    
    logger.info("="*60)
    logger.info("Integration Test: Minimal LLM Call")
    logger.info("="*60)
    
    # Build LLM config
    model_id, api_base, api_key = build_llm_from_env()
    
    # Create agent
    logger.info("Creating minimal agent...")
    agent = create_minimal_agent(
        model_id=model_id,
        api_base=api_base,
        api_key=api_key,
    )
    
    # Simple test prompt
    test_prompt = 'Please respond with only this JSON: {"test": "success", "msg": "Hello from LLM"}'
    
    logger.info(f"Sending test prompt: {test_prompt}")
    
    try:
        result = agent.run(test_prompt)
        logger.info(f"✅ SUCCESS! Agent responded:")
        logger.info(f"   {result}")
        return True
    except Exception as e:
        logger.error(f"❌ FAILED! Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_minimal_llm_call()
    sys.exit(0 if success else 1)
