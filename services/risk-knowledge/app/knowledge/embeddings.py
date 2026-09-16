"""Embedding providers. Runtime = multilingual Sentence Transformers (e5). Tests = deterministic hashing embedder,
which the service refuses to use outside APP_ENV=test so it can never masquerade as the real model."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    name: str
    dim: int

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name, device="cpu")
        self.dim = int(self._model.get_sentence_embedding_dimension() or 384)
        self._e5 = "e5" in model_name.lower()

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        prefixed = [("passage: " + t) if self._e5 else t for t in texts]
        return np.asarray(self._model.encode(prefixed, normalize_embeddings=True, batch_size=16), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        q = ("query: " + text) if self._e5 else text
        return np.asarray(self._model.encode([q], normalize_embeddings=True)[0], dtype=np.float32)


class HashingEmbedder:
    """Deterministic bag-of-words hashing embedding (TEST ONLY). Semantic quality is intentionally low."""

    name = "hashing-test-embedder"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in text.lower().split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)  # noqa: S324 - not security
            v[h % self.dim] += 1.0
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


def make_embedder(provider: str, model_name: str, app_env: str) -> Embedder:
    if provider == "hashing":
        if app_env != "test":
            raise RuntimeError("hashing embedder is test-only; set EMBEDDING_PROVIDER=sentence_transformers")
        return HashingEmbedder()
    return SentenceTransformerEmbedder(model_name)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0 or math.isnan(na) or math.isnan(nb):
        return 0.0
    return float(np.dot(a, b) / (na * nb))
