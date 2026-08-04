from __future__ import annotations

import json
import time
from pathlib import Path


def _write_dot_catalog(root: Path) -> None:
    context = {"cached_at": time.time(), "region": "USA", "universe": "TOP3000", "delay": 1}
    (root / ".alpha_datasets_cache.json").write_text(json.dumps({**context, "dataset_ids": ["ds"], "records": [{"id": "ds"}]}), encoding="utf-8")
    (root / ".alpha_datafields_cache.json").write_text(json.dumps({**context, "rows": [{"id": "field", "_ds": "ds", "type": "MATRIX"}]}), encoding="utf-8")
    (root / ".alpha_operators_cache.json").write_text(json.dumps({**context, "records": [{"name": "rank", "signature": "rank(x)", "arity": 1}]}), encoding="utf-8")


def test_snapshot_loader_requires_complete_local_catalog_and_summarises_feedback(tmp_path: Path) -> None:
    from alpha_mining.generation.feedback import CandidateFeedbackStore
    from alpha_mining.generation.snapshots import CatalogUnavailable, load_local_snapshots

    try:
        load_local_snapshots(root=tmp_path, database=tmp_path / "history.sqlite")
        raise AssertionError("missing catalog must fail closed")
    except CatalogUnavailable:
        pass
    _write_dot_catalog(tmp_path)
    feedback = CandidateFeedbackStore(tmp_path / "history.sqlite")
    feedback.record(
        "request-1", "FAILED", strategy_family="fundamental", dataset="ds",
        checks=[{"name": "SELF_CORRELATION", "result": "FAIL"}],
    )

    snapshots = load_local_snapshots(root=tmp_path, database=tmp_path / "history.sqlite")

    assert snapshots.catalog_source == "root-dot-cache"
    assert len(snapshots.catalog.fields) == 1
    assert snapshots.feedback.failure_counts["SELF_CORRELATION"] == 1
    assert len(snapshots.feedback.self_corr_risk) == 1


def test_v50_kernel_uses_pure_primitives_without_worldquant_pipeline(tmp_path: Path) -> None:
    from alpha_mining.generation.snapshots import load_local_snapshots
    from alpha_mining.generation.v50_kernel import V50Kernel

    _write_dot_catalog(tmp_path)
    snapshots = load_local_snapshots(root=tmp_path, database=tmp_path / "history.sqlite")
    batch = V50Kernel(seed_pool_size=12).generate_batch(snapshots)

    source = Path("alpha_mining/generation/v50_kernel.py").read_text(encoding="utf-8")
    assert batch.candidates
    assert "WorldQuantAlphaPipeline(" not in source
    assert "fetch_datafields" not in source
    assert "submit_simulation" not in source
