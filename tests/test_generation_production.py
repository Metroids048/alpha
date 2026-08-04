from __future__ import annotations

import json
import socket
import time
from pathlib import Path


def _write_catalog(root: Path) -> None:
    now = time.time()
    context = {
        "cached_at": now,
        "region": "USA",
        "universe": "TOP3000",
        "delay": 1,
        "source": "test",
    }
    (root / ".alpha_datasets_cache.json").write_text(
        json.dumps({**context, "dataset_ids": ["fund"], "records": [{"id": "fund"}]}),
        encoding="utf-8",
    )
    (root / ".alpha_datafields_cache.json").write_text(
        json.dumps(
            {
                **context,
                "rows": [
                    {"id": "fund_a", "_ds": "fund", "type": "MATRIX", "description": "quality"},
                    {"id": "fund_b", "_ds": "fund", "type": "MATRIX", "description": "value"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / ".alpha_operators_cache.json").write_text(
        json.dumps(
            {
                **context,
                "records": [
                    {"name": "rank", "signature": "rank(x)", "arity": 1},
                    {"name": "ts_rank", "signature": "ts_rank(x,d)", "arity": 2},
                    {"name": "group_neutralize", "signature": "group_neutralize(x,g)", "arity": 2},
                ],
            }
        ),
        encoding="utf-8",
    )


class _Kernel:
    def generate(self, *_args, **_kwargs):
        from auto_alpha_pipeline_rebuilt_v50 import ExpressionCandidate

        return [
            ExpressionCandidate("group_neutralize(ts_rank(fund_a,63),market)", "fundamental", "v50", 2.0),
            ExpressionCandidate("group_neutralize(ts_rank(fund_b,126),market)", "fundamental", "v50", 1.9),
        ]


class _LLM:
    model_id = "fake-deepseek"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cycle = 0

    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        del system_prompt, json_schema
        self.calls.append(user_prompt)
        phase = len(self.calls) % 3
        if phase == 1:
            return {
                "research_direction": "fundamental quality",
                "hypothesis": "quality persists",
                "economic_mechanism": "slow fundamental information diffusion",
                "expected_horizon": "medium",
                "fields_to_use": ["fund_a", "fund_b"],
                "operators_to_use": ["ts_rank", "group_neutralize"],
                "anti_correlation_plan": "use a distinct field and slow window",
                "expected_turnover_behavior": "medium-low",
                "historical_failures_to_avoid": ["SELF_CORRELATION"],
                "knowledge_refs": ["worldquant:优质Alpha挖掘：AI工作流优化方法.md#1"],
            }
        if phase == 2:
            self.cycle += 1
            field = "fund_a" if self.cycle == 1 else "fund_b"
            return {"candidates": [{
                "expression": f"group_neutralize(ts_rank({field},63),market)",
                "settings": {"region": "USA", "universe": "TOP3000", "delay": 1, "decay": 4, "neutralization": "MARKET", "truncation": 0.08},
                "economic_rationale": "fundamental quality persists over a medium horizon",
                "novelty_reason": "field-specific slow signal",
                "anti_corr_design": "slow window and field diversification",
                "parent_seed": "group_neutralize(ts_rank(fund_a,63),market)",
                "knowledge_refs": ["worldquant:优质Alpha挖掘：AI工作流优化方法.md#1"],
                "feedback_patterns_used": [],
                "likely_failure_modes": ["LOW_SHARPE"],
            }]}
        return {"approved": [{"approved": True, "expression": "approved", "critique": "mechanism and catalog checked"}]}


def test_production_cycle_uses_llm_knowledge_and_never_platform_io(tmp_path, monkeypatch) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    llm = _LLM()
    original_connect = socket.socket.connect

    def blocked_connect(self, address):
        if "worldquantbrain.com" in str(address):
            raise AssertionError("production generation must not contact World Quant")
        return original_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    config = ProductionConfig(root=tmp_path, candidates_per_cycle=1, database=tmp_path / "history.sqlite")
    summary = run_cycle(config, llm=llm, kernel=_Kernel())

    assert summary.state == "COMPLETE"
    assert summary.enqueued == 1
    assert len(llm.calls) == 3
    assert "worldquant:优质Alpha挖掘：AI工作流优化方法.md#1" in llm.calls[0]
    rows = summary.queue_rows
    assert len(rows) == 1
    assert rows[0]["queue_status"] == "PENDING_SIMULATION"
    assert rows[0]["alpha_id"] == ""
    assert rows[0]["degraded"] == "false"
    assert rows[0]["knowledge_usage_mode"] == "LIVE_LLM_KNOWLEDGE"
    assert rows[0]["knowledge_refs_json"]


def test_llm_unavailable_never_writes_degraded_candidate(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    class BrokenLLM:
        model_id = "broken"

        def generate_json(self, **_kwargs):
            raise RuntimeError("transport unavailable")

    _write_catalog(tmp_path)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"),
        llm=BrokenLLM(), kernel=_Kernel(),
    )

    assert summary.state == "LLM_UNAVAILABLE"
    assert summary.enqueued == 0
    assert summary.queue_rows == []


def test_two_cycles_are_idempotent_and_preserve_consumer_state(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    _write_catalog(tmp_path)
    config = ProductionConfig(root=tmp_path, candidates_per_cycle=1, database=tmp_path / "history.sqlite")
    llm = _LLM()
    first = run_cycle(config, llm=llm, kernel=_Kernel())
    queue = CandidateCsvQueue(config.queue_path, config.events_path)
    with queue.writer():
        queue.transition(first.queue_rows[0]["candidate_id"], "SIMULATED", "consumer owns state")
    second = run_cycle(config, llm=llm, kernel=_Kernel())

    rows = CandidateCsvQueue(config.queue_path, config.events_path).read()
    assert first.cycle_id != second.cycle_id
    assert len(rows) == 2
    assert rows[0]["queue_status"] == "SIMULATED"
    assert {row["queue_status"] for row in rows} == {"SIMULATED", "PENDING_SIMULATION"}
    assert not list(tmp_path.glob("*.tmp"))
    assert not config.queue_path.with_suffix(config.queue_path.suffix + ".lock").exists()
