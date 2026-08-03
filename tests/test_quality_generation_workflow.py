from __future__ import annotations

from types import SimpleNamespace

from alpha_mining.factory.orchestrator import SimulationResult


def _write_catalog(root) -> None:
    import json
    import time

    now = time.time()
    context = {"cached_at": now, "region": "USA", "universe": "TOP3000", "delay": 1, "source": "platform_catalog"}
    (root / ".alpha_datasets_cache.json").write_text(json.dumps({**context, "dataset_ids": ["pv1"], "records": [{"id": "pv1", "name": "pv1"}]}), encoding="utf-8")
    (root / ".alpha_datafields_cache.json").write_text(json.dumps({**context, "rows": [{"id": "price_close", "_ds": "pv1", "type": "MATRIX"}]}), encoding="utf-8")
    (root / ".alpha_operators_cache.json").write_text(json.dumps({**context, "operators": ["rank"], "records": [{"name": "rank", "signature": "rank(x)", "arity": 1}]}), encoding="utf-8")


class _Gateway:
    calls = 0

    def simulate(self, *, expression, settings, alpha_type="REGULAR", checkpoint=None, checkpoint_sink=None):
        self.calls += 1
        return SimulationResult(
            alpha_id="alpha-1", status="COMPLETE",
            metrics={"sharpe": 1.8, "fitness": 1.1, "turnover": 0.2},
            checks=[
                {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
                {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
                {"name": "PROD_CORRELATION", "result": "PASS", "mandatory": True},
            ], raw={},
        )


def test_active_generation_cycle_writes_only_ready_csv(tmp_path) -> None:
    from alpha_mining.factory.runtime import GenerationCycleConfig, run_generation_cycle
    from alpha_mining.storage.ready_alpha_csv import ReadyAlphaCsvStore
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "factory.sqlite"
    migrate(database)
    _write_catalog(tmp_path)
    candidate = SimpleNamespace(expression="rank(price_close)", family="momentum", source="v50", score=1.0)
    source_catalog = SimpleNamespace(field_dataset={"price_close": "pv1"})
    gateway = _Gateway()
    summary = run_generation_cycle(
        GenerationCycleConfig(database, tmp_path / "待提交Alpha列表.csv", tmp_path, tmp_path / "auth.json", tmp_path / "lock"),
        candidate_source=lambda: ([candidate], source_catalog), gateway=gateway,
    )

    assert summary.ready == 1
    assert gateway.calls == 1
    row = ReadyAlphaCsvStore(tmp_path / "待提交Alpha列表.csv").read_ready()[0]
    assert row["alpha_id"] == "alpha-1"
    import csv
    with (tmp_path / "待提交Alpha列表.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.reader(handle)) == list(ReadyAlphaCsvStore.FIELDS)


def test_quality_workflow_module_is_not_active() -> None:
    from pathlib import Path

    entry = Path("生成Alpha.py").read_text(encoding="utf-8")
    runtime = Path("alpha_mining/factory/runtime.py").read_text(encoding="utf-8")
    assert "QualityAlphaWorkflow" not in entry + runtime
    assert "CandidateGenerationService" not in entry + runtime
