"""
Test script to verify BM25 integration in SimpleCrawler
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Test BM25Retriever directly
print("=" * 60)
print("Testing SimpleBM25Retriever...")
print("=" * 60)

from rag.retrievers.simple_bm25_retriever import SimpleBM25Retriever

# Create retriever
bm25_retriever = SimpleBM25Retriever(chunk_size=500, chunk_overlap=50)

# Test content
test_content = """
Machine learning is a subset of artificial intelligence that focuses on building systems 
that can learn from data. Deep learning is a type of machine learning that uses neural networks 
with multiple layers. Natural language processing (NLP) is another important area of AI that 
deals with understanding and generating human language.
"""

# Test query
query = "What is deep learning?"

print(f"\nQuery: {query}")
print(f"\nTest Content:\n{test_content}")
print("\n" + "=" * 60)
print("BM25 Retrieval Results:")
print("=" * 60)

result = bm25_retriever.retrieve(query=query, contents=test_content, limit=3)
print(result)

# Test SimpleCrawler with BM25
print("\n" + "=" * 60)
print("Testing SimpleCrawler with BM25 integration...")
print("=" * 60)

from tools.async_web_crawler import SimpleCrawler

# Create crawler (without API keys to avoid actual searches)
crawler = SimpleCrawler(
    serpapi_key=None,
    model=None,
    reflection=False,
    chunk_size=500,
    chunk_overlap=50
)

print(f"\nCrawler initialized:")
print(f"  - Vector retriever: {crawler.retriever is not None}")
print(f"  - BM25 retriever: {crawler.bm25_retriever is not None}")

# Test _query method
if crawler.bm25_retriever:
    print("\n" + "=" * 60)
    print("Testing hybrid _query method:")
    print("=" * 60)
    
    query_result = crawler._query(query, test_content)
    print(query_result)
    
    print("\n" + "=" * 60)
    print("SUCCESS: BM25 integration is working!")
    print("=" * 60)
else:
    print("\nWARNING: BM25 retriever not initialized")
