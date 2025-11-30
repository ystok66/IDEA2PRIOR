import os
import google.generativeai as genai
from smolagents import Tool
from typing import Optional

class GoogleFileSearchTool(Tool):
    name = "google_file_search"
    description = """
    Upload a file (PDF, text, etc.) to Google Gemini and perform RAG (Retrieval Augmented Generation) 
    to answer questions based on its content.
    Useful for analyzing specific papers or documents found during research.
    """
    inputs = {
        "file_path": {
            "type": "string",
            "description": "Absolute path to the file to upload and analyze."
        },
        "query": {
            "type": "string",
            "description": "The question or prompt to ask about the file content."
        }
    }
    output_type = "string"

    def __init__(self):
        super().__init__()
        api_key = os.getenv("GOOGLE_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        self.model_name = "gemini-1.5-flash" # Use a cost-effective model for RAG

    def forward(self, file_path: str, query: str) -> str:
        if not os.path.exists(file_path):
            return f"Error: File not found at {file_path}"
            
        try:
            # 1. Upload file
            # Note: In a real agent, we might want to cache uploads or check if already uploaded.
            # For simplicity, we upload each time or rely on Gemini's handling.
            # Ideally, we should manage the lifecycle of these files (delete after use).
            
            # Check if it's a supported type
            mime_type = None
            if file_path.lower().endswith(".pdf"):
                mime_type = "application/pdf"
            elif file_path.lower().endswith(".txt"):
                mime_type = "text/plain"
            
            # Upload
            uploaded_file = genai.upload_file(file_path, mime_type=mime_type)
            
            # 2. Generate content
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content(
                [query, uploaded_file]
            )
            
            # 3. Cleanup (optional but recommended to avoid clutter)
            # genai.delete_file(uploaded_file.name) 
            # We might want to keep it if we plan to ask follow-ups, but this tool is single-turn.
            
            return response.text
            
        except Exception as e:
            return f"Error using Google File Search: {str(e)}"
