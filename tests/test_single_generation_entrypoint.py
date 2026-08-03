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
    assert "run_pipeline_" not in text
