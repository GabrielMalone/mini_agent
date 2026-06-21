#!/usr/bin/env python3
"""
semantic_memory.py -- Embedding-based semantic injection for project_knowledge.

Provides a lightweight vector search over project_knowledge entries using
the same sentence-transformer model already loaded by search_ops.py.
No external vector DB required -- embeddings are stored as BLOBs in SQLite
and cosine similarity is computed in-process.

Call flow:
  1. ``embed_and_store_new(store)`` -- called periodically to embed any
     knowledge entries that lack embeddings (batch backfill).
  2. ``inject_semantic_memories(messages, store, query_text)`` -- called
     before each turn to inject top-k relevant memories into context.
"""

from __future__ import annotations

import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.memory import MemoryStore

# Reuse the same model name as search_ops for consistency
_MODEL_NAME = "isuruwijesiri/all-MiniLM-L6-v2-code-search-512"
_model = None


def _get_model():
    """Lazy-load the sentence transformer (shared with search_ops)."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(_MODEL_NAME)
        except Exception:
            return None
    return _model


def _embed_text(text: str) -> bytes | None:
    """Embed a single text string, return float32 bytes or None on failure."""
    model = _get_model()
    if model is None:
        return None
    try:
        vec = model.encode(text, show_progress_bar=False)
        return np.asarray(vec, dtype=np.float32).tobytes()
    except Exception:
        return None


def embed_and_store_new(store: MemoryStore, batch_size: int = 20) -> int:
    """Embed any un-embedded knowledge entries and persist them.

    Returns the number of entries successfully embedded.
    """
    model = _get_model()
    if model is None:
        return 0

    entries = store.get_knowledge_without_embeddings(limit=batch_size)
    if not entries:
        return 0

    # Combine summary + detail for richer embedding
    texts = [
        f"{e['summary']}\n{e['detail']}" if e.get('detail') else e['summary']
        for e in entries
    ]

    try:
        vectors = model.encode(texts, show_progress_bar=False)
    except Exception:
        return 0

    count = 0
    for entry, vec in zip(entries, vectors):
        emb_bytes = np.asarray(vec, dtype=np.float32).tobytes()
        store.set_knowledge_embedding(entry["id"], emb_bytes)
        count += 1

    return count


def query_and_format(
    store: MemoryStore | None,
    query_text: str,
    k: int = 3,
    min_similarity: float = 0.3,
) -> str | None:
    """Query semantic knowledge and format results for context injection.

    Returns a formatted string ready to inject into the conversation,
    or None if no relevant memories were found.
    """
    if store is None:
        return None

    query_emb = _embed_text(query_text)
    if query_emb is None:
        return None

    results = store.query_semantic_knowledge(query_emb, k=k)
    if not results:
        return None

    # Filter by minimum similarity
    relevant = [r for r in results if r.get("similarity", 0) >= min_similarity]
    if not relevant:
        return None

    lines = ["## SEMANTIC MEMORY (from project_knowledge)\n"]
    lines.append("The following learnings are semantically relevant to the current task:\n")
    for r in relevant:
        sim = r.get("similarity", 0)
        cat = r.get("category", "general")
        summary = r.get("summary", "")
        detail = r.get("detail", "")
        lines.append(f"- [{cat}] (relevance: {sim:.2f}) {summary}")
        if detail and detail != summary:
            lines.append(f"  {detail}")
    lines.append("")

    return "\n".join(lines)
