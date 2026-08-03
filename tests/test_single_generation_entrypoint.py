from __future__ import annotations

import importlib.util
from pathlib import Path


def test_ready_csv_store_is_atomic_and_idempotent_by_alpha_and_hash(tmp_path) -> None:
    from alpha_mining.storage.ready_alpha_csv import ReadyAlphaCsvStore

    store = ReadyAlphaCsvStore(tmp_path / "待提交Alpha列表.csv")
    row = {"alpha_id": "a1", "exact_hash": "h1", "expression": "rank(close)", "quality_status": "READY_TO_SUBMIT"}
    assert store.upsert(row) is True
    assert store.upsert(row) is False
    assert store.read_ready() == [row]
    assert not list(tmp_path.glob("*.tmp"))


def test_generation_entrypoint_is_the_only_new_public_flow() -> None:
    entry = Path("生成Alpha.py")
    assert entry.is_file()
    text = entry.read_text(encoding="utf-8")
    assert "QualityAlphaWorkflow" in text
    assert "ReadOnlyExpressionCatalog" in text
    assert "WorldQuantKnowledgeRepository" in text
    assert "run_pipeline_" not in text


def test_production_candidate_service_rejects_unreferenced_candidates(tmp_path) -> None:
    import sqlite3

    from alpha_mining.generation.service import CandidateGenerationService
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "candidates.sqlite"
    SqliteRunLog(database).initialize_schema()
    migrate(database)
    with sqlite3.connect(database) as con:
        con.execute("INSERT INTO research_topics(topic_id,topic_name_cn,topic_name_en,created_at,active) VALUES ('t','t','t','now',1)")
        con.execute("INSERT INTO hypotheses(hypothesis_id,topic_id,statement_cn,created_at,status) VALUES ('h','t','h','now','active')")
        con.execute("INSERT INTO data_mappings(mapping_id,hypothesis_id,data_field,dataset_id,created_at) VALUES ('m','h','close','ds','now')")

    class Generator:
        def generate(self, **_kwargs):
            from alpha_mining.generator.consultant_generator import ConsultantCandidate
            return [ConsultantCandidate("c", "h", "f", "test", "rank(close)")]

    batch = CandidateGenerationService(database, generator=Generator()).generate(limit=1)

    assert not batch.candidates
    assert batch.rejected_by_reason["KNOWLEDGE_MISSING"] >= 1
