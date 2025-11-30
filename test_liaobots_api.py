import os
import json
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

base = os.environ.get("CUSTOM_LLM_API_BASE", "https://ai.liaobots.work/v1")
key = os.environ["CUSTOM_LLM_API_KEY"]
model = os.environ.get("CUSTOM_LLM_MODEL_NAME", "gpt-4o")  # 你现在是 gemini-3-pro-preview

url = f"{base}/chat/completions"
payload = {
    "model": model,
    "messages": [
        {
            "role": "user",
            "content": "请只返回一个 JSON：{\"ok\": true, \"msg\": \"hello from liaobots\"}",
        }
    ],
    "stream": False,
}

resp = requests.post(
    url,
    headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    },
    json=payload,
    timeout=60,
)

print("HTTP status:", resp.status_code)
print("Raw response:")
print(resp.text)

resp.raise_for_status()
data = resp.json()
print("\n--- model reply ---\n")
print(data["choices"][0]["message"]["content"])
