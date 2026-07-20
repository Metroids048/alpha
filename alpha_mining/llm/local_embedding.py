"""Lazy local sentence-transformer implementation of EmbeddingClient."""

from __future__ import annotations

import math
import os
from numbers import Real
from typing import Any


DEFAULT_LOCAL_EMBEDDING_MODEL = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)


class LocalSentenceTransformerEmbedder:
    """Generate local multilingual embeddings without loading a model at import time."""

    def __init__(
        self, *, model_name: str | None = None, model: Any | None = None
    ) -> None:
        self.model_name = (
            model_name
            or os.getenv("LOCAL_EMBEDDING_MODEL")
            or DEFAULT_LOCAL_EMBEDDING_MODEL
        )
        self._model = model

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is required for local embeddings; "
                    "install requirements-llm.txt"
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> tuple[float, ...]:
        encoded = self._load_model().encode([text], convert_to_numpy=True)
        if encoded is None or isinstance(encoded, Real):
            raise ValueError("embedding must be a non-empty finite float vector")
        try:
            rows = list(encoded)
        except TypeError:
            raise ValueError(
                "embedding must be a non-empty finite float vector"
            ) from None
        if not rows:
            raise ValueError("embedding must be a non-empty finite float vector")
        if isinstance(rows[0], Real):
            vector = rows
        else:
            if len(rows) != 1 or rows[0] is None or isinstance(rows[0], Real):
                raise ValueError("embedding must be a non-empty finite float vector")
            try:
                vector = list(rows[0])
            except TypeError:
                raise ValueError(
                    "embedding must be a non-empty finite float vector"
                ) from None
        if not vector or any(not isinstance(value, Real) for value in vector):
            raise ValueError("embedding must be a non-empty finite float vector")
        try:
            values = tuple(float(value) for value in vector)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(
                "embedding must be a non-empty finite float vector"
            ) from None
        if not values or any(not math.isfinite(value) for value in values):
            raise ValueError("embedding must be a non-empty finite float vector")
        return values

    def close(self) -> None:
        model = self._model
        close = getattr(model, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> LocalSentenceTransformerEmbedder:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
