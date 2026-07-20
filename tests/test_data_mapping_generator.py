from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from alpha_mining.generator.data_mapping import (
    DataMappingGenerator,
    HypothesisNotFoundError,
    InvalidDataMappingOutput,
)
from alpha_mining.storage.sqlite_store import SqliteRunLog
from auto_alpha_pipeline_rebuilt_v50 import FieldCatalog, field_quality_score


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


def _database(tmp_path: Path, *, status: str = "active") -> Path:
    database = tmp_path / "mappings.sqlite3"
    SqliteRunLog(database).initialize_schema()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO research_topics (
                topic_id, topic_name_cn, topic_name_en, category, data_category,
                description, source, created_at, active
            ) VALUES (
                'capital_efficiency', '资本效率', 'Capital Efficiency',
                'profitability', 'fundamental', 'capital efficiency topic',
                'seed', '2026-07-17T00:00:00Z', 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO hypotheses (
                hypothesis_id, topic_id, statement_cn, statement_en, mechanism,
                horizon, embedding, created_at, llm_model, status
            ) VALUES (
                'hypothesis-1', 'capital_efficiency',
                '资本效率改善预测未来收益', NULL,
                '经营效率具有持续性', 'medium', NULL,
                '2026-07-17T00:00:00Z', 'fixture', ?
            )
            """,
            (status,),
        )
    return database


def _catalog() -> FieldCatalog:
    return FieldCatalog.from_df(
        pd.DataFrame(
            [
                {"id": "operating_income", "_ds": "fundamental6", "userCount": 200},
                {"id": "revenue_growth", "_ds": "fundamental6", "userCount": 180},
                {"id": "free_cash_flow", "_ds": "fundamental2", "userCount": 160},
                {"id": "company_name", "_ds": "fundamental6", "userCount": 999},
                {"id": "inventory", "_ds": "fundamental6", "userCount": 500},
                {"id": "analyst_revision", "_ds": "analyst4", "userCount": 100},
                {"id": "close", "_ds": "pv1", "userCount": 1000},
            ]
        )
    )


VALID_OUTPUT = {
    "mappings": [
        {
            "data_field": "operating_income",
            "rationale": "Direct operating profit numerator.",
        },
        {
            "data_field": "revenue_growth",
            "rationale": "Captures improvement in operating scale.",
        },
        {
            "data_field": "free_cash_flow",
            "rationale": "Tests whether accounting profit is cash backed.",
        },
    ]
}


def test_prefilters_catalog_and_persists_three_to_eight_explainable_mappings(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    llm = FakeLLM([VALID_OUTPUT])

    mappings = DataMappingGenerator(database, llm=llm).generate(
        "hypothesis-1", _catalog()
    )

    assert len(mappings) == 3
    assert {mapping.data_field for mapping in mappings} == {
        "operating_income",
        "revenue_growth",
        "free_cash_flow",
    }
    call = llm.calls[0]
    assert "资本效率改善预测未来收益" in call["user_prompt"]
    assert "经营效率具有持续性" in call["user_prompt"]
    field_enum = call["json_schema"]["properties"]["mappings"]["items"]["properties"][
        "data_field"
    ]["enum"]
    assert "company_name" not in field_enum
    assert "inventory" not in field_enum
    assert "analyst_revision" not in field_enum
    assert "close" not in field_enum
    assert set(field_enum) == {"operating_income", "revenue_growth", "free_cash_flow"}

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT data_field, dataset_id, rationale, field_quality_score, selected_by
            FROM data_mappings ORDER BY data_field
            """
        ).fetchall()
    assert len(rows) == 3
    assert {row[1] for row in rows} == {"fundamental2", "fundamental6"}
    assert all(row[2] for row in rows)
    assert all(row[3] == field_quality_score(row[0]) for row in rows)
    assert {row[4] for row in rows} == {"llm"}


def test_hallucinated_or_duplicate_field_is_rejected_without_write(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    invalid = {
        "mappings": [
            {"data_field": "operating_income", "rationale": "one"},
            {"data_field": "operating_income", "rationale": "duplicate"},
            {"data_field": "invented_field", "rationale": "hallucinated"},
        ]
    }

    with pytest.raises(InvalidDataMappingOutput):
        DataMappingGenerator(database, llm=FakeLLM([invalid])).generate(
            "hypothesis-1", _catalog()
        )

    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM data_mappings").fetchone()[0] == 0
        )


@pytest.mark.parametrize("count", [2, 9])
def test_mapping_count_must_be_between_three_and_eight(
    tmp_path: Path, count: int
) -> None:
    database = _database(tmp_path)
    catalog = _catalog()
    fields = ["operating_income", "revenue_growth", "free_cash_flow"]
    output = {
        "mappings": [
            {"data_field": fields[index % len(fields)], "rationale": f"reason {index}"}
            for index in range(count)
        ]
    }

    with pytest.raises(InvalidDataMappingOutput, match="3 to 8"):
        DataMappingGenerator(database, llm=FakeLLM([output])).generate(
            "hypothesis-1", catalog
        )


def test_repeated_generation_upserts_deterministic_mapping_ids(tmp_path: Path) -> None:
    database = _database(tmp_path)
    llm = FakeLLM([VALID_OUTPUT, VALID_OUTPUT])
    generator = DataMappingGenerator(database, llm=llm)

    first = generator.generate("hypothesis-1", _catalog())
    second = generator.generate("hypothesis-1", _catalog())

    assert {mapping.mapping_id for mapping in first} == {
        mapping.mapping_id for mapping in second
    }
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM data_mappings").fetchone()[0] == 3
        )


def test_retired_or_missing_hypothesis_is_rejected_before_llm(tmp_path: Path) -> None:
    database = _database(tmp_path, status="retired")
    llm = FakeLLM([VALID_OUTPUT])

    with pytest.raises(HypothesisNotFoundError):
        DataMappingGenerator(database, llm=llm).generate("hypothesis-1", _catalog())

    assert llm.calls == []


def test_module_imports_existing_field_quality_helpers() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "alpha_mining"
        / "generator"
        / "data_mapping.py"
    ).read_text(encoding="utf-8")
    for helper in (
        "FieldCatalog",
        "is_bad_field_name",
        "is_weak_fundamental_field",
        "field_quality_score",
    ):
        assert helper in source
    assert "def is_bad_field_name" not in source
    assert "def is_weak_fundamental_field" not in source
    assert "def field_quality_score" not in source
