from typing import List, Any, Dict, Optional, Union
from rank_bm25 import BM25Okapi
from langchain.text_splitter import RecursiveCharacterTextSplitter

class SimpleBM25Retriever:
    def __init__(
        self,
        text_splitter: Optional[RecursiveCharacterTextSplitter] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> None:
        self.text_splitter = text_splitter or RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", " ", ""]
        )
        self.bm25 = None
        self.chunks = []

    def _split_text(self, contents: Union[str, List[str]]) -> List[str]:
        if isinstance(contents, str):
            chunks = self.text_splitter.split_text(contents)
        elif isinstance(contents, list):
            nested_chunks = [self.text_splitter.split_text(content) for content in contents]
            # Flatten list
            chunks = [item for sublist in nested_chunks for item in sublist]
        else:
             print(f"[Warning] from text_splitter: Input type {type(contents)} error!")
             chunks = []
        return chunks

    def retrieve(
        self,
        query: str,
        contents: Union[str, List[str]],
        limit: int = 10,
    ) -> str:
        # 1. Chunk contents
        self.chunks = self._split_text(contents)
        
        if not self.chunks:
            return "No content to retrieve from."

        # 2. Tokenize corpus (simple whitespace split for now)
        tokenized_corpus = [chunk.lower().split(" ") for chunk in self.chunks]
        
        # 3. Build BM25 index
        self.bm25 = BM25Okapi(tokenized_corpus)
        
        # 4. Tokenize query
        tokenized_query = query.lower().split(" ")
        
        # 5. Get top N chunks
        top_n = self.bm25.get_top_n(tokenized_query, self.chunks, n=limit)
        
        # Format output
        output_content = ''
        for i, chunk in enumerate(top_n):
            output_content += f"Relative content {i+1}:\n{chunk}\n"
            
        return output_content
