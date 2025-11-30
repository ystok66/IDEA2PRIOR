import os
import google.generativeai as genai
from smolagents import Tool
from typing import Optional, List, Dict
from loguru import logger

class GeminiFileManagerTool(Tool):
    name = "gemini_file_manager"
    description = """
    Manage files with Google Gemini for RAG.
    Supports uploading files and querying them using Gemini's File API.
    """
    inputs = {
        "action": {
            "type": "string",
            "description": "Action to perform: 'upload' or 'query'."
        },
        "file_path": {
            "type": "string",
            "description": "Path to local file (for upload).",
            "nullable": True
        },
        "file_uri": {
            "type": "string",
            "description": "URI of uploaded file (for query).",
            "nullable": True
        },
        "query": {
            "type": "string",
            "description": "Question to ask about the file (for query).",
            "nullable": True
        }
    }
    output_type = "string"

    def __init__(self):
        super().__init__()
        api_key = os.getenv("GOOGLE_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        self.model_name = "gemini-1.5-flash"

    def forward(self, action: str, file_path: Optional[str] = None, 
                file_uri: Optional[str] = None, query: Optional[str] = None) -> str:
        
        if action == "upload":
            return self.upload_file(file_path)
        elif action == "query":
            return self.query_file(file_uri, query)
        else:
            return "Error: Invalid action. Use 'upload' or 'query'."

    def upload_file(self, file_path: str) -> str:
        if not file_path or not os.path.exists(file_path):
            return f"Error: File not found at {file_path}"
        
        try:
            logger.info(f"Uploading file to Gemini: {file_path}")
            mime_type = "application/pdf" if file_path.lower().endswith(".pdf") else "text/plain"
            uploaded_file = genai.upload_file(file_path, mime_type=mime_type)
            logger.info(f"File uploaded successfully. URI: {uploaded_file.uri}")
            return f"File uploaded. URI: {uploaded_file.uri}"
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return f"Error uploading file: {str(e)}"

    def query_file(self, file_uri: str, query: str) -> str:
        if not file_uri or not query:
            return "Error: file_uri and query are required for query action."
            
        try:
            # Note: In a real scenario, we'd need to retrieve the file object or pass the URI correctly.
            # The GenerativeModel.generate_content accepts parts which can be file URIs or objects.
            # However, genai.upload_file returns a File object. If we only have URI, we might need to get the file.
            # For simplicity, we assume the user passes the URI string, but the API might need the object or specific format.
            # Let's try fetching the file object first if possible, or pass URI if supported.
            
            # Actually, generate_content supports file API integration.
            # We need to pass the file object. If we only have URI, we can try `genai.get_file(name)`.
            # The URI usually looks like 'https://generativelanguage.googleapis.com/v1beta/files/...'
            # The 'name' is 'files/...'
            
            # Extract file name from URI or assume URI is the name if it starts with 'files/'
            file_name = file_uri
            if "googleapis.com" in file_uri:
                 # This is a full URI, we might need to parse it, but usually the SDK returns a File object with a .name property
                 # which is what we need.
                 pass

            # If the user provides the raw URI string returned by upload, we might need to handle it.
            # But let's assume for this tool, the user (Agent) passes what upload returned.
            
            # Let's try to get the file metadata to ensure it exists and is processed.
            # The 'name' attribute of the uploaded file object is what we need (e.g., 'files/abc12345')
            
            # If input is a full URI, try to extract the name.
            if "/files/" in file_uri:
                file_name = "files/" + file_uri.split("/files/")[-1]
            else:
                file_name = file_uri

            file_obj = genai.get_file(file_name)
            
            model = genai.GenerativeModel(self.model_name)
            response = model.generate_content([query, file_obj])
            return response.text
            
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return f"Error querying file: {str(e)}"
