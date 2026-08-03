from __future__ import annotations

import json
import time
from types import SimpleNamespace

from alpha_mining.factory.orchestrator import SimulationResult


def _write_catalog(root) -> None:
    context = {
        "cached_at": time.time(),
        "region": "USA",
        "universe": "TOP3000",
        "delay": 1,
        "source": "test",
    }
    (root / ".alpha_datasets_cache.json").write_text(
        json.dumps({**context, "dataset_ids": ["pv1"]}), encoding="utf-8"
    )
    (root / ".alpha_datafields_cache.json").write_text(
        json.dumps({**context, "rows": [{"id": "price_close", "_ds": "pv1", "type": "MATRIX"}]}),
        encoding="utf-8",
    )
    (root / ".alpha_operators_cache.json").write_text(
        json.dumps({**context, "operators": ["rank"], "records": [{"name": "rank", "signature": "rank(x)", "arity": 1}]}),
        encoding="utf-8",
    )


def test_adapter_has_stable_identity_and_single_dataset() -> None:
    from alpha_mining.factory.v50_adapter import adapt_v50_candidate

    candidate = SimpleNamespace(expression="rank(price_close)", family="Momentum", source="v50")
    catalog = SimpleNamespace(field_dataset={"price_close": "pv1"})

    first = adapt_v50_candidate(candidate, catalog)
    second = adapt_v50_candidate(candidate, catalog)

    assert first.candidate_id == second.candidate_id
    assert first.exact_hash == second.exact_hash
    assert first.dataset == "pv1"


def test_active_cycle_blocks_mandatory_metric_failure_without_submit(tmp_path) -> None:
    from alpha_mining.factory.runtime import GenerationCycleConfig, run_generation_cycle
    from alpha_mining.storage.migrations import migrate

    class Gateway:
        def __init__(self) -> None:
            self.simulate_calls = 0
            self.submit_calls = 0

        def simulate(self, **_kwargs):
            self.simulate_calls += 1
            return SimulationResult(
                alpha_id="alpha-1",
                status="COMPLETE",
                metrics={"sharpe": 1.8, "fitness": 1.1, "turnover": 0.2},
                checks=[
                    {"name": "LOW_SHARPE", "result": "FAIL", "mandatory": True},
                    {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
                    {"name": "PROD_CORRELATION", "result": "PASS", "mandatory": True},
                ],
                raw={},
            )

        def submit(self, **_kwargs):
            self.submit_calls += 1
            raise AssertionError("generation must not submit")

    database = tmp_path / "factory.sqlite"
    output = tmp_path / "待提交Alpha列表.csv"
    migrate(database)
    _write_catalog(tmp_path)
    candidate = SimpleNamespace(expression="rank(price_close)", family="momentum", source="v50", score=1.0)
    source_catalog = SimpleNamespace(field_dataset={"price_close": "pv1"})
    gateway = Gateway()

    summary = run_generation_cycle(
        GenerationCycleConfig(database, output, tmp_path, tmp_path / "auth.json", tmp_path / "lock"),
        candidate_source=lambda: ([candidate], source_catalog),
        gateway=gateway,
    )

    assert summary.simulated == 1
    assert summary.ready == 0
    assert gateway.simulate_calls == 1
    assert gateway.submit_calls == 0
    assert not output.exists()
