"""
Test script to verify LLM priority logic in demo_research_agent.py
"""
import os
import sys
from dotenv import load_dotenv

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(override=True)

from demo_research_agent import build_llm_from_env

def test_llm_priority():
    """Test that build_llm_from_env() returns correct priority."""
    
    print("="*60)
    print("Testing LLM Priority Logic")
    print("="*60)
    
    # Show current environment
    print("\nCurrent Environment Variables:")
    print(f"  CUSTOM_LLM_API_BASE: {os.getenv('CUSTOM_LLM_API_BASE', '(not set)')}")
    print(f"  CUSTOM_LLM_API_KEY: {'***' if os.getenv('CUSTOM_LLM_API_KEY') else '(not set)'}")
    print(f"  CUSTOM_LLM_MODEL_NAME: {os.getenv('CUSTOM_LLM_MODEL_NAME', '(not set)')}")
    print(f"  GOOGLE_API_KEY: {'***' if os.getenv('GOOGLE_API_KEY') else '(not set)'}")
    print(f"  OPENAI_API_KEY: {'***' if os.getenv('OPENAI_API_KEY') else '(not set)'}")
    
    print("\n" + "-"*60)
    print("Running build_llm_from_env()...")
    print("-"*60 + "\n")
    
    model_id, api_base, api_key = build_llm_from_env()
    
    print("\nResult:")
    print(f"  model_id: {model_id}")
    print(f"  api_base: {api_base}")
    print(f"  api_key: {'***' if api_key else None}")
    
    # Determine which config was used
    print("\n" + "="*60)
    if api_base and api_key:
        print("✅ CUSTOM_LLM configuration selected (HIGHEST PRIORITY)")
        print(f"   Endpoint: {api_base}")
        print(f"   Model: {model_id}")
    elif "gemini" in model_id:
        print("✅ Google Gemini configuration selected (MEDIUM PRIORITY)")
        print(f"   Model: {model_id}")
        print("   Will use GOOGLE_API_KEY from environment")
    elif "gpt" in model_id:
        print("✅ OpenAI configuration selected (LOWEST PRIORITY)")
        print(f"   Model: {model_id}")
        print("   Will use OPENAI_API_KEY from environment")
    print("="*60)
    
    # Recommendations
    print("\n💡 Recommendations:")
    has_custom = (os.getenv("CUSTOM_LLM_API_BASE") and 
                  os.getenv("CUSTOM_LLM_API_KEY") and 
                  os.getenv("CUSTOM_LLM_MODEL_NAME"))
    
    if has_custom:
        print("   - You have CUSTOM_LLM configured. All LLM calls will use your custom endpoint.")
        print("   - GOOGLE_API_KEY (if set) will be ignored for general chat but may be used by specific tools.")
    elif os.getenv("GOOGLE_API_KEY"):
        print("   - Using Google Gemini. Watch out for quota limits on free tier.")
        print("   - To use a custom endpoint instead, set CUSTOM_LLM_* variables in .env")
    elif os.getenv("OPENAI_API_KEY"):
        print("   - Using OpenAI. This will consume your OpenAI credits.")
    else:
        print("   - ⚠️ No LLM configured! Please set one of:")
        print("     * CUSTOM_LLM_API_BASE + CUSTOM_LLM_API_KEY + CUSTOM_LLM_MODEL_NAME")
        print("     * GOOGLE_API_KEY")
        print("     * OPENAI_API_KEY")

if __name__ == "__main__":
    test_llm_priority()
