"""Tiny Okapi BM25 over short text docs — no external dependency.

Why this exists: substring match (`q in haystack`) is the wrong tool for
intent queries. "what aligns RNA-seq reads" should surface STAR/salmon even
though none of those words appear in the query; "enrichment analysis" should
rank gseapy above an incidental mention. BM25 gives term-frequency / inverse-
document-frequency ranking with length normalization — the standard lexical
relevance baseline — in ~40 lines, deterministic, with nothing to install.

Scope: indexes are rebuilt from scratch on demand over ~10^2 docs (skills,
capabilities), so we don't bother with incremental updates. If a catalog ever
reaches 10^4+, swap this impl behind the same `BM25(docs).search(q)` seam for
an embedding index — callers don't change.
"""
from __future__ import annotations
import math
import re
from typing import Iterable

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase + split on non-alphanumeric. Deliberately simple: no
    stemming/stopwords (the vocab is small, domain-specific, and we'd rather
    keep 'de'/'qc'/'rna' than risk a stoplist eating them)."""
    return _TOKEN.findall((text or "").lower())


class BM25:
    """Okapi BM25. Construct with an iterable of (doc_id, text); call
    `search(query, limit=k)` for a ranked [(doc_id, score)] list (scores > 0
    only, descending). Empty query or empty index → []."""

    def __init__(self, docs: Iterable[tuple[str, str]], *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.ids: list[str] = []
        self.doc_len: list[int] = []
        self.tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for doc_id, text in docs:
            counts: dict[str, int] = {}
            for t in tokenize(text):
                counts[t] = counts.get(t, 0) + 1
            self.ids.append(doc_id)
            self.doc_len.append(sum(counts.values()))
            self.tf.append(counts)
            for t in counts:
                df[t] = df.get(t, 0) + 1
        self.N = len(self.ids)
        self.avgdl = (sum(self.doc_len) / self.N) if self.N else 0.0
        # Standard BM25 idf with +0.5 smoothing and a +1 floor so a term in
        # nearly every doc still contributes a hair (never goes negative).
        self.idf = {
            t: math.log(1.0 + (self.N - n + 0.5) / (n + 0.5))
            for t, n in df.items()
        }

    def search(self, query: str, *, limit: int = 10) -> list[tuple[str, float]]:
        q = tokenize(query)
        if not q or self.N == 0:
            return []
        avgdl = self.avgdl or 1.0
        scored: list[tuple[str, float]] = []
        for i, doc_id in enumerate(self.ids):
            counts = self.tf[i]
            dl = self.doc_len[i] or 1
            score = 0.0
            for t in q:
                f = counts.get(t)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                denom = f + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                score += idf * (f * (self.k1 + 1.0)) / denom
            if score > 0.0:
                scored.append((doc_id, score))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[: max(1, limit)]
