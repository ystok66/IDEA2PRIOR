import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

# Fix Windows encoding
sys.stdout.reconfigure(encoding="utf-8")

def test_list_models():
    """Test listing available models from custom endpoint."""
    load_dotenv(override=True)
    
    api_base = os.getenv("CUSTOM_LLM_API_BASE")
    api_key = os.getenv("CUSTOM_LLM_API_KEY")
    
    if not api_base or not api_key:
        print("❌ Error: CUSTOM_LLM_API_BASE or CUSTOM_LLM_API_KEY not set in .env")
        return False
    
    print(f"🔗 Connecting to: {api_base}")
    print(f"🔑 Using API key: {api_key[:10]}...")
    print()
    
    try:
        client = OpenAI(
            base_url=api_base,
            api_key=api_key,
        )
        
        print("📋 Fetching available models...")
        models = client.models.list()
        
        print(f"✅ Found {len(models.data)} models:")
        print()
        
        # Group models by category
        gpt_models = []
        claude_models = []
        gemini_models = []
        deepseek_models = []
        other_models = []
        
        for m in models.data:
            model_id = m.id
            if "gpt" in model_id.lower():
                gpt_models.append(model_id)
            elif "claude" in model_id.lower():
                claude_models.append(model_id)
            elif "gemini" in model_id.lower():
                gemini_models.append(model_id)
            elif "deepseek" in model_id.lower():
                deepseek_models.append(model_id)
            else:
                other_models.append(model_id)
        
        if gpt_models:
            print("🤖 GPT Models:")
            for m in sorted(gpt_models)[:10]:  # Show first 10
                print(f"  - {m}")
            if len(gpt_models) > 10:
                print(f"  ... and {len(gpt_models) - 10} more")
            print()
        
        if claude_models:
            print("🧠 Claude Models:")
            for m in sorted(claude_models)[:10]:
                print(f"  - {m}")
            if len(claude_models) > 10:
                print(f"  ... and {len(claude_models) - 10} more")
            print()
        
        if gemini_models:
            print("💎 Gemini Models:")
            for m in sorted(gemini_models)[:10]:
                print(f"  - {m}")
            if len(gemini_models) > 10:
                print(f"  ... and {len(gemini_models) - 10} more")
            print()
        
        if deepseek_models:
            print("🔍 DeepSeek Models:")
            for m in sorted(deepseek_models)[:10]:
                print(f"  - {m}")
            if len(deepseek_models) > 10:
                print(f"  ... and {len(deepseek_models) - 10} more")
            print()
        
        print("💡 Recommended models for your use case:")
        print("  - Cost-effective: gpt-4o-mini, claude-3-5-haiku-20241022")
        print("  - Balanced: gpt-4o, claude-3-5-sonnet-20241022")
        print("  - Reasoning: deepseek-reasoner, o1-mini")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error listing models: {e}")
        print()
        print("🔧 Troubleshooting:")
        print("  1. Check your API key is correct")
        print("  2. Check your network connection")
        print("  3. Verify the base URL is correct")
        return False


def test_chat_completion():
    """Test a simple chat completion."""
    load_dotenv(override=True)
    
    api_base = os.getenv("CUSTOM_LLM_API_BASE")
    api_key = os.getenv("CUSTOM_LLM_API_KEY")
    model_name = os.getenv("CUSTOM_LLM_MODEL_NAME", "gpt-4o-mini")
    
    if not api_base or not api_key:
        print("❌ Error: CUSTOM_LLM_API_BASE or CUSTOM_LLM_API_KEY not set in .env")
        return False
    
    print(f"🧪 Testing chat completion with model: {model_name}")
    print()
    
    try:
        client = OpenAI(
            base_url=api_base,
            api_key=api_key,
        )
        
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'Hello from custom endpoint!' and nothing else."}
            ],
            max_tokens=50,
        )
        
        reply = response.choices[0].message.content
        print(f"✅ Model response: {reply}")
        print()
        print(f"📊 Usage:")
        print(f"  - Prompt tokens: {response.usage.prompt_tokens}")
        print(f"  - Completion tokens: {response.usage.completion_tokens}")
        print(f"  - Total tokens: {response.usage.total_tokens}")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error during chat completion: {e}")
        print()
        print("🔧 Troubleshooting:")
        print("  1. Make sure the model name is correct")
        print("  2. Check if your account has access to this model")
        print("  3. Verify your API key has sufficient credits/quota")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Custom LLM Endpoint Test")
    print("=" * 60)
    print()
    
    # Test 1: List models
    models_ok = test_list_models()
    
    if not models_ok:
        print("⚠️  Skipping chat completion test due to previous error")
        sys.exit(1)
    
    print()
    print("=" * 60)
    print()
    
    # Test 2: Chat completion
    chat_ok = test_chat_completion()
    
    print()
    print("=" * 60)
    if models_ok and chat_ok:
        print("✅ All tests passed! Your custom endpoint is working.")
    else:
        print("❌ Some tests failed. Please check the errors above.")
    print("=" * 60)
