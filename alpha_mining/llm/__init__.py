"""Runtime factories for production LLM and embedding providers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from alpha_mining.llm.deepseek import DeepSeekStructuredLLM
from alpha_mining.llm.local_embedding import LocalSentenceTransformerEmbedder


@dataclass(frozen=True)
class RuntimeProviders:
    llm: DeepSeekStructuredLLM
    embedder: LocalSentenceTransformerEmbedder

    def close(self) -> None:
        self.llm.close()
        self.embedder.close()

    def __enter__(self) -> RuntimeProviders:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def load_dotenv(
    dotenv_path: str | Path | None = None,
    *,
    override: bool = False,
) -> bool:
    """Load runtime environment values only when explicitly called."""
    try:
        from dotenv import load_dotenv as python_dotenv_load
    except ImportError as exc:
        raise RuntimeError(
            "python-dotenv is required; install requirements-llm.txt"
        ) from exc
    return bool(python_dotenv_load(dotenv_path=dotenv_path, override=override))


def create_runtime_providers(
    *,
    dotenv_path: str | Path | None = None,
    load_environment: bool = True,
    llm_client: httpx.Client | None = None,
    llm_transport: httpx.BaseTransport | None = None,
    embedding_model: Any | None = None,
) -> RuntimeProviders:
    """Create reusable L2/L3/L4 providers without making calls or loading a model."""
    if load_environment:
        load_dotenv(dotenv_path)
    return RuntimeProviders(
        llm=DeepSeekStructuredLLM(client=llm_client, transport=llm_transport),
        embedder=LocalSentenceTransformerEmbedder(model=embedding_model),
    )


__all__ = [
    "DeepSeekStructuredLLM",
    "LocalSentenceTransformerEmbedder",
    "RuntimeProviders",
    "create_runtime_providers",
    "load_dotenv",
]
