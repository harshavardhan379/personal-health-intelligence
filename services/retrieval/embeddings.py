"""Embedding provider (YGH-73).

A deterministic, offline fake embedding (hashed bag-of-words -> fixed-size vector).
It has no real semantic understanding, but it's stable, needs no API key or GPU,
and is enough to exercise storage, pgvector search and ranking end to end. Swap
`embed()` for a real sentence-transformer or hosted embeddings API later; nothing
downstream (storage shape, search code) needs to change, only EMBEDDING_DIM must
stay in sync with packages/schemas/models.py.
"""

import hashlib
import math
import re

from packages.schemas.models import EMBEDDING_DIM

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def embed(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def chunk_text(text: str, max_chars: int = 500) -> list[str]:
    """Simple fixed-size chunking with no overlap. Good enough for MVP semantic
    search over per-page document text.
    """
    text = text.strip()
    if not text:
        return []
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
