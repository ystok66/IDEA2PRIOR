import os
import sys
from dotenv import load_dotenv
from smolagents import LiteLLMModel
from pipelines.llm_config import get_litellm_config

# Fix encoding for Windows
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

# Load .env
load_dotenv(override=True)

def test_role(role):
    print(f"\nTesting role: {role.upper()}")
    try:
        model_id, api_base, api_key = get_litellm_config(role=role)
        print(f"  Config: model={model_id}, base={api_base}")
        
        model = LiteLLMModel(model_id=model_id, api_base=api_base, api_key=api_key)
        
        # Simple prompt
        messages = [{"role": "user", "content": "Hello, return 'OK' if you can hear me."}]
        response = model(messages)
        print(f"  Response: {response.content}")
        print(f"  ✅ {role.upper()} is working.")
        return True
    except Exception as e:
        print(f"  ❌ {role.upper()} failed: {e}")
        return False

def main():
    print("Starting LLM Connectivity Test...")
    roles = ["light", "heavy", "expert"]
    results = {}
    for r in roles:
        results[r] = test_role(r)
    
    print("\nSummary:")
    for r, success in results.items():
        status = "PASSED" if success else "FAILED"
        print(f"  {r.upper()}: {status}")

if __name__ == "__main__":
    main()
