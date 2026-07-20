from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import alpha_mining.validation.phase2_llm_acceptance as acceptance_module
from alpha_mining.llm import RuntimeProviders
from alpha_mining.storage.sqlite_store import SqliteRunLog
from alpha_mining.validation.phase2_llm_acceptance import run_acceptance


class SequentialLLM:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls = 0
        self.fail_at = fail_at

    def generate_json(
        self, *, system_prompt: str, user_prompt: str, json_schema: dict
    ) -> dict:
        del system_prompt, user_prompt, json_schema
        self.calls += 1
        if self.fail_at == self.calls:
            raise RuntimeError("synthetic provider failure")
        return {
            "hypothesis_statement": f"可证伪假设 {self.calls}",
            "mechanism": f"经济机制 {self.calls}",
            "horizon": ("short", "medium", "long")[(self.calls - 1) % 3],
            "expected_direction": "正向",
            "candidate_data_concepts": [f"field_{self.calls}"],
        }


class OrthogonalEmbedder:
    def __init__(self, dimensions: int = 64) -> None:
        self.calls = 0
        self.dimensions = dimensions

    def embed(self, text: str) -> tuple[float, ...]:
        del text
        vector = [0.0] * self.dimensions
        vector[self.calls] = 1.0
        self.calls += 1
        return tuple(vector)


def _database(tmp_path: Path) -> Path:
    database = tmp_path / "research_memory.sqlite"
    SqliteRunLog(database).initialize_schema()
    topics = [
        ("fund_a", "fundamental"),
        ("fund_b", "fundamental"),
        ("price_a", "price"),
        ("options_a", "options"),
        ("sentiment_a", "sentiment"),
    ]
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO research_topics (
                topic_id, topic_name_cn, topic_name_en, category, data_category,
                description, source, created_at, active
            ) VALUES (?, ?, ?, 'test', ?, ?, 'fixture', '2026-07-17T00:00:00Z', 1)
            """,
            [
                (topic_id, topic_id, topic_id, category, f"description {topic_id}")
                for topic_id, category in topics
            ],
        )
    return database


def test_acceptance_persists_exact_count_across_three_categories_and_writes_report(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    report = tmp_path / "work" / "phase2-report.json"

    result = run_acceptance(
        database,
        count=20,
        report=report,
        llm=SequentialLLM(),
        embedder=OrthogonalEmbedder(),
        model_id="fake-model",
    )

    assert result.generated_count == 20
    assert len(result.data_categories) >= 3
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT h.statement_cn, h.mechanism, h.horizon, h.topic_id, t.data_category
            FROM hypotheses h
            JOIN research_topics t ON t.topic_id = h.topic_id
            ORDER BY h.created_at, h.hypothesis_id
            """
        ).fetchall()
    assert len(rows) == 20
    assert len({row[4] for row in rows}) >= 3

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["generated_count"] == 20
    assert len(payload["hypotheses"]) == 20
    assert set(payload["hypotheses"][0]) == {
        "statement",
        "mechanism",
        "horizon",
        "expected_direction",
        "candidate_data_concepts",
        "topic",
        "category",
    }
    assert payload["hypotheses"][0]["expected_direction"] == "正向"
    assert payload["hypotheses"][0]["candidate_data_concepts"] == ["field_1"]
    assert "仅用于人工抽查" in payload["persistence_note"]
    assert "api_key" not in report.read_text(encoding="utf-8").lower()


def test_acceptance_failure_leaves_target_database_and_report_unchanged(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    report = tmp_path / "work" / "phase2-report.json"

    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        run_acceptance(
            database,
            count=20,
            report=report,
            llm=SequentialLLM(fail_at=7),
            embedder=OrthogonalEmbedder(),
            model_id="fake-model",
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 0
    assert not report.exists()


def test_acceptance_rejects_insufficient_active_category_coverage(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE research_topics SET active=0 WHERE data_category IN ('options', 'sentiment')"
        )

    with pytest.raises(ValueError, match="at least 3 active data categories"):
        run_acceptance(
            database,
            count=20,
            report=tmp_path / "work" / "phase2-report.json",
            llm=SequentialLLM(),
            embedder=OrthogonalEmbedder(),
            model_id="fake-model",
        )


def test_report_publish_failure_keeps_committed_rows_and_previous_report(
    tmp_path: Path, monkeypatch
) -> None:
    database = _database(tmp_path)
    report = tmp_path / "work" / "phase2-report.json"
    report.parent.mkdir()
    report.write_text("previous report", encoding="utf-8")
    real_replace = acceptance_module.os.replace

    def fail_new_report(source, destination):
        if Path(source).suffix == ".tmp":
            raise OSError("synthetic report publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(acceptance_module.os, "replace", fail_new_report)
    with pytest.raises(OSError, match="synthetic report publish failure"):
        run_acceptance(
            database,
            count=20,
            report=report,
            llm=SequentialLLM(),
            embedder=OrthogonalEmbedder(),
            model_id="fake-model",
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 20
    assert report.read_text(encoding="utf-8") == "previous report"
    assert not list(report.parent.glob(".*.tmp"))


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_acceptance_rejects_report_path_that_is_database_or_sqlite_sidecar(
    tmp_path: Path, suffix: str
) -> None:
    database = _database(tmp_path)
    report = Path(f"{database}{suffix}")

    with pytest.raises(ValueError, match="report path must not target database"):
        run_acceptance(
            database,
            count=20,
            report=report,
            llm=SequentialLLM(),
            embedder=OrthogonalEmbedder(),
            model_id="fake-model",
        )


def test_acceptance_cleanup_does_not_mask_original_provider_error(
    tmp_path: Path, monkeypatch
) -> None:
    database = _database(tmp_path)
    monkeypatch.setattr(
        acceptance_module,
        "_remove_temporary_database",
        lambda path: (_ for _ in ()).throw(OSError("cleanup failed")),
    )

    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        run_acceptance(
            database,
            count=20,
            report=tmp_path / "work" / "phase2-report.json",
            llm=SequentialLLM(fail_at=2),
            embedder=OrthogonalEmbedder(),
            model_id="fake-model",
        )


def test_acceptance_closes_factory_providers_when_only_llm_is_injected(
    tmp_path: Path, monkeypatch
) -> None:
    class ClosableProvider:
        def __init__(self, delegate) -> None:
            self.delegate = delegate
            self.closed = False

        def generate_json(self, **kwargs):
            return self.delegate.generate_json(**kwargs)

        def embed(self, text: str):
            return self.delegate.embed(text)

        def close(self) -> None:
            self.closed = True

    unused_llm = ClosableProvider(SequentialLLM())
    used_embedder = ClosableProvider(OrthogonalEmbedder())
    monkeypatch.setattr(
        acceptance_module,
        "create_runtime_providers",
        lambda: RuntimeProviders(llm=unused_llm, embedder=used_embedder),
    )

    run_acceptance(
        _database(tmp_path),
        count=20,
        report=tmp_path / "work" / "phase2-report.json",
        llm=SequentialLLM(),
        model_id="fake-model",
    )
    assert unused_llm.closed
    assert used_embedder.closed
