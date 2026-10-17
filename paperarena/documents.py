"""Document chunks and explicitly configured, observed embedding requests."""
from __future__ import annotations

import math
import re
from urllib.parse import quote

from .diagnostics import safe_error


def document_chunks(content, size=1000, overlap=200):
    if type(size) is not int or not 100 <= size <= 10000:
        raise ValueError("Chunk size must be 100–10,000 characters")
    if type(overlap) is not int or not 0 <= overlap < size:
        raise ValueError("Chunk overlap must be smaller than chunk size")
    markers = list(re.finditer(r"<!-- Page (\d+) -->", content))
    segments = [(m.end(), markers[i+1].start() if i+1 < len(markers) else len(content),
                 int(m.group(1))) for i, m in enumerate(markers)]
    if not segments:
        segments = [(0, len(content), None)]
    chunks = []
    for begin, end, page in segments:
        for start in range(begin, end, size-overlap):
            stop = min(start+size, end)
            text = content[start:stop]
            if text.strip():
                chunks.append({"chunk_id": len(chunks)+1, "page": page,
                               "start": start, "end": stop, "text": text})
            if stop == end:
                break
    return chunks


class EmbeddingClient:
    def __init__(self, profile, timeout, emit):
        self.profile = profile.resolved()
        self.timeout, self.emit = timeout, emit
        self.attempts = self.responses = self.failed = self.known = self.tokens = 0
        self.errors = []

    def usage(self):
        missing = self.attempts-self.known
        return {"model_attempts": self.attempts, "responses_observed": self.responses,
                "failed_model_attempts": self.failed, "recent_request_errors": list(self.errors),
                "reported_token_subtotal": {"prompt_tokens": self.tokens if self.known else None,
                    "completion_tokens": 0, "total_tokens": self.tokens if self.known else None},
                "missing_token_attempt_counts": {"prompt_tokens": missing, "completion_tokens": 0, "total_tokens": missing},
                "tokens": {"prompt_tokens": self.tokens if self.known and not missing else None,
                    "completion_tokens": 0, "total_tokens": self.tokens if self.known and not missing else None},
                "token_usage_complete": bool(self.attempts) and not missing,
                "cost": None, "billable_calls": None, "scope": "Embedding requests; no generation or automatic retries"}

    def embed(self, texts):
        import requests
        p = self.profile
        self.attempts += 1
        self.emit({"phase": "extension_embeddings", "usage": self.usage()})
        try:
            if p.provider == "gemini":
                model = p.model.removeprefix("models/").removeprefix("gemini/")
                url = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(model, safe="") + ":batchEmbedContents"
                headers = {"x-goog-api-key": p.api_key}
                payload = {"requests": [{"model": "models/"+model,
                    "content": {"parts": [{"text": text}]}} for text in texts]}
            else:
                url = (p.base_url or "https://api.openai.com/v1").rstrip("/") + "/embeddings"
                headers = {"Authorization": "Bearer "+p.api_key}
                payload = {"model": p.model.removeprefix("openai/"), "input": texts}
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(url, headers=headers, json=payload,
                                        timeout=self.timeout, allow_redirects=False)
                if 300 <= response.status_code < 400:
                    raise ValueError("Embedding redirects are not accepted")
                response.raise_for_status()
                body = response.json()
            if p.provider == "gemini":
                vectors = [row["values"] for row in body["embeddings"]]
            else:
                rows = sorted(body["data"], key=lambda r: r["index"])
                if [r["index"] for r in rows] != list(range(len(texts))):
                    raise ValueError("Invalid embedding indexes")
                vectors = [row["embedding"] for row in rows]
            if len(vectors) != len(texts) or not vectors:
                raise ValueError("Invalid embedding count")
            dims = len(vectors[0])
            if not dims or any(len(v) != dims or any(type(x) not in (int, float) or not math.isfinite(x) for x in v) for v in vectors):
                raise ValueError("Invalid embedding dimensions or values")
            usage = body.get("usage", {})
            count = usage.get("total_tokens", usage.get("prompt_tokens"))
            if type(count) is int and count >= 0:
                self.known += 1
                self.tokens += count
            self.responses += 1
            return vectors
        except Exception as error:
            self.failed += 1
            self.errors.append(safe_error(error, "extension_embeddings"))
            raise
        finally:
            self.emit({"usage": self.usage()})


def search_document(content, parameters, config, emit):
    chunks = document_chunks(content, parameters["chunk_size"], parameters["chunk_overlap"])
    if not chunks:
        raise ValueError("The document has no searchable text")
    client = EmbeddingClient(config.embedding, config.request_timeout_seconds, emit)
    query = client.embed([parameters.get("query") or parameters["topic"]])[0]
    query_norm = math.sqrt(sum(v*v for v in query))
    if not query_norm:
        raise ValueError("Embedding query has zero norm")
    vectors = []
    for start in range(0, len(chunks), 32):
        vectors.extend(client.embed([row["text"] for row in chunks[start:start+32]]))
    results = []
    for chunk, vector in zip(chunks, vectors):
        if len(vector) != len(query):
            raise ValueError("Embedding dimensions changed between requests")
        norm = math.sqrt(sum(v*v for v in vector))
        if not norm:
            raise ValueError("Embedding chunk has zero norm")
        score = sum(a*b for a, b in zip(query, vector)) / (query_norm*norm)
        results.append({**chunk, "score": score})
    results.sort(key=lambda r: (-r["score"], r["chunk_id"]))
    return {"matches": results[:parameters["top_k"]], "chunk_count": len(chunks),
            "embedding_usage": client.usage(), "evidence_scope": "ranked document passages, not generated answers"}
