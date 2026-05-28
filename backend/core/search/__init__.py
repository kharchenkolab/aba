"""Small, dependency-free retrieval primitives.

`BM25` ranks short text docs (skills, capabilities) by free-text intent so
the catalog can grow to hundreds of entries while the agent still finds the
right one — and the in-prompt index stays bounded (retrieval-gated to top-K).
Scale here is ~10^2 docs; a pure-python scan is plenty and avoids a runtime
dep (no rank_bm25 / embeddings to install or pin).
"""
from .bm25 import BM25, tokenize

__all__ = ["BM25", "tokenize"]
