"""L2 structured hypothesis generation with embedding-based semantic deduplication."""

from __future__ import annotations

import json
import math
import sqlite3
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

if TYPE_CHECKING:
    from alpha_mining.filter.insights import RepairInsights


class TopicNotFoundError(LookupError):
    """The requested active research topic does not exist."""


class InvalidHypothesisOutput(ValueError):
    """The LLM response did not match the required structured contract."""


class DuplicateHypothesisError(RuntimeError):
    """Every bounded generation attempt was semantically duplicate."""


class EmbeddingDimensionError(ValueError):
    """Stored and generated embeddings use incompatible dimensions."""


class StructuredLLM(Protocol):
    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
    ) -> Mapping[str, Any]: ...


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> Sequence[float]: ...


HYPOTHESIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hypothesis_statement": {"type": "string", "minLength": 1},
        "mechanism": {"type": "string", "minLength": 1},
        "horizon": {"type": "string", "enum": ["short", "medium", "long"]},
        "expected_direction": {"type": "string", "minLength": 1},
        "candidate_data_concepts": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": [
        "hypothesis_statement",
        "mechanism",
        "horizon",
        "expected_direction",
        "candidate_data_concepts",
    ],
}


@dataclass(frozen=True)
class HypothesisDraft:
    hypothesis_statement: str
    mechanism: str
    horizon: str
    expected_direction: str
    candidate_data_concepts: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedHypothesis:
    hypothesis_id: str
    topic_id: str
    draft: HypothesisDraft
    embedding: tuple[float, ...]
    max_similarity: float


def encode_embedding(vector: Sequence[float]) -> bytes:
    values = tuple(float(value) for value in vector)
    if not values:
        raise ValueError("embedding must be non-empty")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("embedding values must be finite")
    return struct.pack(f"<{len(values)}f", *values)


def decode_embedding(blob: bytes) -> tuple[float, ...]:
    if not blob or len(blob) % 4:
        raise ValueError("embedding BLOB length must be a non-zero multiple of 4")
    count = len(blob) // 4
    return tuple(struct.unpack(f"<{count}f", blob))


def _validated_draft(raw: Mapping[str, Any]) -> HypothesisDraft:
    expected = set(HYPOTHESIS_JSON_SCHEMA["required"])
    if set(raw) != expected:
        raise InvalidHypothesisOutput(
            "structured hypothesis fields do not match the JSON schema"
        )
    text_fields = (
        "hypothesis_statement",
        "mechanism",
        "horizon",
        "expected_direction",
    )
    cleaned: dict[str, str] = {}
    for field in text_fields:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise InvalidHypothesisOutput(f"{field} must be a non-empty string")
        cleaned[field] = value.strip()
    if cleaned["horizon"] not in {"short", "medium", "long"}:
        raise InvalidHypothesisOutput("horizon must be short, medium, or long")
    concepts = raw.get("candidate_data_concepts")
    if not isinstance(concepts, list) or not concepts:
        raise InvalidHypothesisOutput(
            "candidate_data_concepts must be a non-empty list"
        )
    cleaned_concepts: list[str] = []
    for concept in concepts:
        if not isinstance(concept, str) or not concept.strip():
            raise InvalidHypothesisOutput(
                "candidate_data_concepts entries must be non-empty strings"
            )
        if concept.strip() not in cleaned_concepts:
            cleaned_concepts.append(concept.strip())
    return HypothesisDraft(
        hypothesis_statement=cleaned["hypothesis_statement"],
        mechanism=cleaned["mechanism"],
        horizon=cleaned["horizon"],
        expected_direction=cleaned["expected_direction"],
        candidate_data_concepts=tuple(cleaned_concepts),
    )


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise EmbeddingDimensionError(
            f"embedding dimensions differ: generated={len(left)} stored={len(right)}"
        )
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise EmbeddingDimensionError(
            "zero-length embedding vectors cannot be compared"
        )
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


class HypothesisGenerator:
    def __init__(
        self,
        database: str | Path,
        *,
        llm: StructuredLLM,
        embedder: EmbeddingClient,
        model_id: str,
        similarity_threshold: float = 0.90,
        max_attempts: int = 3,
        use_repair_insights: bool = False,
    ) -> None:
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0 and 1")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not model_id.strip():
            raise ValueError("model_id must not be empty")
        self.database = Path(database).expanduser().resolve()
        self.llm = llm
        self.embedder = embedder
        self.model_id = model_id.strip()
        self.similarity_threshold = float(similarity_threshold)
        self.max_attempts = int(max_attempts)
        self.use_repair_insights = bool(use_repair_insights)

    def _context(self, topic_id: str) -> tuple[str, list[str], list[tuple[float, ...]]]:
        with sqlite3.connect(self.database) as connection:
            topic = connection.execute(
                "SELECT description FROM research_topics WHERE topic_id=? AND active=1",
                (topic_id,),
            ).fetchone()
            if topic is None:
                raise TopicNotFoundError(f"active research topic not found: {topic_id}")
            statements = [
                str(row[0])
                for row in connection.execute(
                    "SELECT statement_cn FROM hypotheses WHERE topic_id=? ORDER BY created_at",
                    (topic_id,),
                )
            ]
            embeddings = [
                decode_embedding(bytes(row[0]))
                for row in connection.execute(
                    "SELECT embedding FROM hypotheses WHERE embedding IS NOT NULL"
                )
            ]
        return str(topic[0]), statements, embeddings

    @staticmethod
    def _prompt(
        topic_id: str,
        description: str,
        statements: Sequence[str],
        *,
        insights: RepairInsights | None = None,
    ) -> str:
        base = (
            f"Research topic id: {topic_id}\n"
            f"Topic description: {description}\n"
            "Existing hypotheses for this topic:\n"
            f"{json.dumps(list(statements), ensure_ascii=False)}\n"
            "Generate exactly one economically falsifiable hypothesis that is semantically different "
            "from every existing hypothesis. Return only data matching the supplied JSON schema."
        )
        if insights is not None:
            if insights.avoided_data_concepts:
                base += (
                    "\nConstraint: avoid proposing hypotheses that rely primarily on these data fields"
                    " (recent platform over-correlation failures indicate market saturation for them): "
                    + ", ".join(insights.avoided_data_concepts)
                    + "."
                )
            if insights.preferred_horizon:
                base += (
                    f"\nConstraint: prefer {insights.preferred_horizon} horizon hypotheses"
                    " (recent short-horizon alphas failed in-sample stability ladder checks;"
                    " longer horizons improve regime consistency)."
                )
        return base

    def generate(self, topic_id: str) -> GeneratedHypothesis:
        description, statements, historical_embeddings = self._context(topic_id)
        insights: RepairInsights | None = None
        if self.use_repair_insights:
            from alpha_mining.filter.insights import load_repair_insights

            insights = load_repair_insights(self.database)
        rejected_statements: list[str] = []
        last_invalid: InvalidHypothesisOutput | None = None
        duplicate_attempts = 0
        for attempt in range(1, self.max_attempts + 1):
            prompt_statements = [*statements, *rejected_statements]
            raw = self.llm.generate_json(
                system_prompt=(
                    "You are a quantitative research hypothesis generator. Produce structured, "
                    "testable claims; do not produce alpha expressions or free-form commentary."
                ),
                user_prompt=self._prompt(
                    topic_id, description, prompt_statements, insights=insights
                ),
                json_schema=HYPOTHESIS_JSON_SCHEMA,
            )
            try:
                draft = _validated_draft(raw)
            except InvalidHypothesisOutput as exc:
                last_invalid = exc
                if attempt == self.max_attempts:
                    raise
                continue
            embedding = tuple(
                float(value)
                for value in self.embedder.embed(draft.hypothesis_statement)
            )
            encode_embedding(embedding)
            similarities = [
                _cosine_similarity(embedding, historical)
                for historical in historical_embeddings
            ]
            max_similarity = max(similarities, default=0.0)
            if max_similarity >= self.similarity_threshold:
                duplicate_attempts += 1
                rejected_statements.append(draft.hypothesis_statement)
                continue

            hypothesis_id = f"hyp_{uuid.uuid4().hex}"
            created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            with sqlite3.connect(self.database) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute(
                    """
                    INSERT INTO hypotheses (
                        hypothesis_id, topic_id, statement_cn, statement_en, mechanism,
                        horizon, embedding, created_at, llm_model, status
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, 'active')
                    """,
                    (
                        hypothesis_id,
                        topic_id,
                        draft.hypothesis_statement,
                        draft.mechanism,
                        draft.horizon,
                        encode_embedding(embedding),
                        created_at,
                        self.model_id,
                    ),
                )
            return GeneratedHypothesis(
                hypothesis_id=hypothesis_id,
                topic_id=topic_id,
                draft=draft,
                embedding=embedding,
                max_similarity=max_similarity,
            )
        if duplicate_attempts:
            raise DuplicateHypothesisError(
                f"all {self.max_attempts} attempts exceeded semantic similarity threshold "
                f"{self.similarity_threshold:.2f}"
            )
        if last_invalid is not None:
            raise last_invalid
        raise RuntimeError("hypothesis generation exhausted without a result")
