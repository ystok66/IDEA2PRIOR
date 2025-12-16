from pipelines.llm_config import get_litellm_config
import os
from dotenv import load_dotenv
from loguru import logger
import sys

# Configure logger to stdout
logger.remove()
logger.add(sys.stdout, level="INFO")

def test_llm_config():
    load_dotenv(override=True)
    
    print("\n--- Testing Heavy Role ---")
    try:
        model, base, key = get_litellm_config("heavy")
        print(f"Heavy Config: Model={model}, Base={base}, Key={'*' * 5 if key else 'None'}")
    except Exception as e:
        print(f"Heavy Config Failed: {e}")

    print("\n--- Testing Light Role ---")
    try:
        model, base, key = get_litellm_config("light")
        print(f"Light Config: Model={model}, Base={base}, Key={'*' * 5 if key else 'None'}")
    except Exception as e:
        print(f"Light Config Failed: {e}")

if __name__ == "__main__":
    test_llm_config()
