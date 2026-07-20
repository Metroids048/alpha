from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from alpha_mining.generator.hypothesis import (
    DuplicateHypothesisError,
    HypothesisGenerator,
    InvalidHypothesisOutput,
    TopicNotFoundError,
    decode_embedding,
    encode_embedding,
)
from alpha_mining.storage.sqlite_store import SqliteRunLog


VALID_DRAFT = {
    "hypothesis_statement": "资本效率持续改善的公司未来收益更高",
    "mechanism": "管理层把投入资本转化为经营利润的能力具有持续性。",
    "horizon": "medium",
    "expected_direction": "资本效率改善 -> 未来收益上升",
    "candidate_data_concepts": ["roic", "operating_income", "invested_capital"],
}


class FakeLLM:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    def generate_json(
        self, *, system_prompt: str, user_prompt: str, json_schema: dict
    ) -> dict:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "json_schema": json_schema,
            }
        )
        return self.outputs.pop(0)


class FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return self.vectors[text]


def _database(tmp_path: Path, *, active: int = 1) -> Path:
    database = tmp_path / "hypotheses.sqlite3"
    SqliteRunLog(database).initialize_schema()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO research_topics (
                topic_id, topic_name_cn, topic_name_en, category, data_category,
                description, source, created_at, active
            ) VALUES (
                'capital_efficiency', '资本效率', 'Capital Efficiency',
                'profitability', 'fundamental',
                '研究投入资本转化为经营利润的持续改善。',
                'seed', '2026-07-17T00:00:00Z', ?
            )
            """,
            (active,),
        )
    return database


def _insert_hypothesis(
    database: Path,
    *,
    hypothesis_id: str,
    topic_id: str,
    statement: str,
    embedding: list[float],
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO hypotheses (
                hypothesis_id, topic_id, statement_cn, statement_en, mechanism,
                horizon, embedding, created_at, llm_model, status
            ) VALUES (?, ?, ?, NULL, 'existing mechanism', 'medium', ?,
                      '2026-07-17T00:00:00Z', 'fixture', 'active')
            """,
            (hypothesis_id, topic_id, statement, encode_embedding(embedding)),
        )


def test_prompt_contains_topic_description_existing_statements_and_json_schema(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    _insert_hypothesis(
        database,
        hypothesis_id="existing",
        topic_id="capital_efficiency",
        statement="现有资本效率假设",
        embedding=[1.0, 0.0],
    )
    llm = FakeLLM([VALID_DRAFT])
    embedder = FakeEmbedder({VALID_DRAFT["hypothesis_statement"]: [0.0, 1.0]})

    generated = HypothesisGenerator(
        database,
        llm=llm,
        embedder=embedder,
        model_id="mock-structured-model",
    ).generate("capital_efficiency")

    call = llm.calls[0]
    assert "研究投入资本转化为经营利润的持续改善" in call["user_prompt"]
    assert "现有资本效率假设" in call["user_prompt"]
    schema = call["json_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(VALID_DRAFT)
    assert generated.draft.candidate_data_concepts == (
        "roic",
        "operating_income",
        "invested_capital",
    )
    assert generated.max_similarity == 0.0

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT topic_id, statement_cn, mechanism, horizon, embedding, llm_model, status
            FROM hypotheses WHERE hypothesis_id = ?
            """,
            (generated.hypothesis_id,),
        ).fetchone()
    assert row[0:4] == (
        "capital_efficiency",
        VALID_DRAFT["hypothesis_statement"],
        VALID_DRAFT["mechanism"],
        "medium",
    )
    assert decode_embedding(row[4]) == pytest.approx((0.0, 1.0))
    assert row[5:] == ("mock-structured-model", "active")


def test_semantic_duplicate_is_discarded_and_regenerated(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _insert_hypothesis(
        database,
        hypothesis_id="existing",
        topic_id="capital_efficiency",
        statement="existing",
        embedding=[1.0, 0.0],
    )
    duplicate = dict(VALID_DRAFT, hypothesis_statement="重复假设")
    unique = dict(VALID_DRAFT, hypothesis_statement="独立假设")
    llm = FakeLLM([duplicate, unique])
    embedder = FakeEmbedder({"重复假设": [0.99, 0.01], "独立假设": [0.0, 1.0]})

    generated = HypothesisGenerator(
        database,
        llm=llm,
        embedder=embedder,
        model_id="mock",
        similarity_threshold=0.90,
        max_attempts=2,
    ).generate("capital_efficiency")

    assert generated.draft.hypothesis_statement == "独立假设"
    assert len(llm.calls) == 2
    with sqlite3.connect(database) as connection:
        statements = {
            row[0] for row in connection.execute("SELECT statement_cn FROM hypotheses")
        }
    assert statements == {"existing", "独立假设"}


def test_similarity_check_uses_embeddings_from_other_topics(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO research_topics VALUES (
                'other', '其他', 'Other', 'value', 'fundamental', 'other topic',
                'seed', '2026-07-17T00:00:00Z', 1
            )
            """
        )
    _insert_hypothesis(
        database,
        hypothesis_id="other-existing",
        topic_id="other",
        statement="other topic duplicate",
        embedding=[1.0, 0.0],
    )
    llm = FakeLLM([VALID_DRAFT, VALID_DRAFT])
    embedder = FakeEmbedder({VALID_DRAFT["hypothesis_statement"]: [1.0, 0.0]})

    with pytest.raises(DuplicateHypothesisError, match="2 attempts"):
        HypothesisGenerator(
            database,
            llm=llm,
            embedder=embedder,
            model_id="mock",
            max_attempts=2,
        ).generate("capital_efficiency")

    assert len(llm.calls) == 2
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM hypotheses WHERE topic_id='capital_efficiency'"
            ).fetchone()[0]
            == 0
        )


def test_invalid_structured_output_is_rejected_without_database_write(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    invalid = dict(VALID_DRAFT, horizon="overnight", candidate_data_concepts=[])

    with pytest.raises(InvalidHypothesisOutput):
        HypothesisGenerator(
            database,
            llm=FakeLLM([invalid]),
            embedder=FakeEmbedder({}),
            model_id="mock",
            max_attempts=1,
        ).generate("capital_efficiency")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 0


def test_missing_or_inactive_topic_is_rejected_before_llm_call(tmp_path: Path) -> None:
    database = _database(tmp_path, active=0)
    llm = FakeLLM([VALID_DRAFT])

    with pytest.raises(TopicNotFoundError):
        HypothesisGenerator(
            database,
            llm=llm,
            embedder=FakeEmbedder({}),
            model_id="mock",
        ).generate("capital_efficiency")

    assert llm.calls == []


def test_embedding_blob_round_trip_and_validation() -> None:
    vector = (0.25, -0.5, 1.0)
    assert decode_embedding(encode_embedding(vector)) == pytest.approx(vector)
    with pytest.raises(ValueError, match="non-empty"):
        encode_embedding([])
    with pytest.raises(ValueError, match="multiple of 4"):
        decode_embedding(b"bad")
