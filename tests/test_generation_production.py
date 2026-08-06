from __future__ import annotations

import json
import re
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace


def _mechanism_contract(expression: str) -> dict[str, object]:
    from alpha_mining.domain.expression_normalization import extract_fields, extract_functions

    fields = extract_fields(expression)
    functions = extract_functions(expression)
    turnover = next((item for item in functions if item.startswith("ts_") or item == "rank"), functions[0])
    diversifiers = [fields[0]]
    if "group_neutralize" in functions:
        diversifiers.append("group_neutralize")
    return {
        "field_roles": [{"field_id": field, "role": "economic input"} for field in fields],
        "operator_roles": [{"operator": operator, "role": "signal transformation"} for operator in functions],
        "turnover_controls": [turnover],
        "correlation_diversifiers": diversifiers,
    }


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
            ExpressionCandidate("group_neutralize(rank(fund_b),market)", "fundamental", "v50", 1.9),
        ]


class _LLM:
    model_id = "fake-deepseek"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.cycle = 0
        self.knowledge_ref = ""

    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        del system_prompt, json_schema
        self.calls.append(user_prompt)
        phase = len(self.calls) % 3
        if phase == 1:
            refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
            self.knowledge_ref = refs[0]
            return {
                "research_direction": "fundamental quality",
                "hypothesis": "quality persists",
                "economic_mechanism": "slow fundamental information diffusion",
                "expected_horizon": "medium",
                "fields_to_use": ["fund_a", "fund_b"],
                "operators_to_use": ["rank", "ts_rank", "group_neutralize"],
                "anti_correlation_plan": "use a distinct field and slow window",
                "expected_turnover_behavior": "medium-low",
                "historical_failures_to_avoid": ["SELF_CORRELATION"],
                "knowledge_refs": [self.knowledge_ref],
            }
        if phase == 2:
            self.cycle += 1
            field = "fund_a" if self.cycle == 1 else "fund_b"
            expression = f"group_neutralize(ts_rank({field},63),market)" if field == "fund_a" else "group_neutralize(rank(fund_b),market)"
            return {"candidates": [{
                "expression": expression,
                "settings": {"region": "USA", "universe": "TOP3000", "delay": 1, "decay": 4, "neutralization": "MARKET", "truncation": 0.08},
                "economic_rationale": "fundamental quality persists over a medium horizon",
                "novelty_reason": "field-specific slow signal",
                "anti_corr_design": "slow window and field diversification",
                "parent_seed": expression,
                "knowledge_refs": [self.knowledge_ref],
                "feedback_patterns_used": [],
                "likely_failure_modes": ["LOW_SHARPE"],
                **_mechanism_contract(expression),
            }]}
        return {"approved": [{"approved": True, "expression": "approved", "critique": "mechanism and catalog checked"}]}


def test_production_defaults_to_enforce_and_cli_allows_shadow(monkeypatch) -> None:
    from alpha_mining.generation import production

    captured = []

    def fake_run_cycle(config, **_kwargs):
        captured.append(config)
        return production.CycleSummary("test-cycle", "COMPLETE")

    monkeypatch.setattr(production, "run_cycle", fake_run_cycle)
    monkeypatch.setattr(production.time, "sleep", lambda _seconds: None)

    assert production.ProductionConfig().portfolio_mode == "enforce"
    assert production.main(["--once"]) == 0
    assert captured[-1].portfolio_mode == "enforce"
    assert production.main(["--once", "--portfolio-mode", "shadow"]) == 0
    assert captured[-1].portfolio_mode == "shadow"
    assert captured[-1].allow_degraded is False
    assert production.main(["--once", "--allow-degraded"]) == 0
    assert captured[-1].allow_degraded is True


def test_rejection_digest_exposes_primary_reasons() -> None:
    from alpha_mining.generation.production import _rejection_digest

    assert _rejection_digest({"LOW_LOCAL_QUALITY": 6, "UNKNOWN_OPERATOR": 3}) == (
        "LOW_LOCAL_QUALITY:6,UNKNOWN_OPERATOR:3"
    )


def test_partial_offline_catalog_uses_explicit_longer_age_window(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    from alpha_mining.generation.snapshots import load_local_snapshots

    cached_at = (datetime.now(timezone.utc) - timedelta(hours=200)).timestamp()
    context = {"cached_at": cached_at, "region": "USA", "universe": "TOP3000", "delay": 1}
    (tmp_path / ".alpha_datasets_cache.json").write_text(
        json.dumps({**context, "dataset_ids": ["fund"], "records": [{"id": "fund"}]}),
        encoding="utf-8",
    )
    (tmp_path / ".alpha_datafields_cache.json").write_text(
        json.dumps({**context, "rows": [{"id": "fund_a", "_ds": "fund", "type": "MATRIX"}]}),
        encoding="utf-8",
    )

    snapshots = load_local_snapshots(root=tmp_path, allow_partial_offline=True)

    assert snapshots.catalog_source == "root-dot-cache-partial-offline"
    assert 199 < snapshots.catalog_age_hours < 201
    assert len(snapshots.catalog.operators) == 15
    assert snapshots.catalog.info["source"] == "local_offline_field_snapshot"


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
    assert llm.knowledge_ref.startswith("worldquant:")
    assert llm.knowledge_ref in llm.calls[0]
    rows = summary.queue_rows
    assert len(rows) == 1
    assert rows[0]["queue_status"] == "PENDING_SIMULATION"
    assert rows[0]["alpha_id"] == ""
    assert rows[0]["degraded"] == "false"
    assert rows[0]["knowledge_usage_mode"] == "LIVE_LLM_KNOWLEDGE"
    assert rows[0]["knowledge_refs_json"]


def test_production_cycle_uses_partial_offline_catalog_without_platform_io(tmp_path, monkeypatch) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    now = time.time()
    (tmp_path / ".alpha_datasets_cache.json").write_text(
        json.dumps({"cached_at": now, "dataset_ids": ["fund"], "records": [{"id": "fund"}]}),
        encoding="utf-8",
    )
    (tmp_path / ".alpha_datafields_cache.json").write_text(
        json.dumps(
            {"cached_at": now, "rows": [{"id": "fund_a", "_ds": "fund", "type": "MATRIX", "description": "quality"}]}
        ),
        encoding="utf-8",
    )

    class OfflineKernel:
        def generate(self, *_args, **_kwargs):
            return [SimpleNamespace(expression="ts_rank(fund_a,63)", score=1.0)]

    class OfflineLLM:
        model_id = "offline-test"

        def __init__(self) -> None:
            self.calls = 0
            self.knowledge_ref = ""

        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            del system_prompt, json_schema
            self.calls += 1
            if self.calls == 1:
                self.knowledge_ref = re.search(r'"ref_id":\s*"([^"]+)"', user_prompt).group(1)
                return {
                    "research_direction": "fundamental quality",
                    "hypothesis": "quality persists",
                    "economic_mechanism": "slow fundamental information diffusion",
                    "expected_horizon": "medium",
                    "fields_to_use": ["fund_a"],
                    "operators_to_use": ["ts_rank", "ts_mean"],
                    "anti_correlation_plan": "use a distinct slow transform",
                    "expected_turnover_behavior": "medium-low",
                    "historical_failures_to_avoid": ["SELF_CORRELATION"],
                    "knowledge_refs": [self.knowledge_ref],
                }
            if self.calls == 2:
                expression = "ts_mean(fund_a,63)"
                return {"candidates": [{
                    "expression": expression,
                    "settings": {},
                    "economic_rationale": "Slow-moving fundamental quality information persists over a medium horizon.",
                    "novelty_reason": "Uses a different local time-series transform than the seed.",
                    "anti_corr_design": "A medium window reduces short-horizon crowding.",
                    "parent_seed": "ts_rank(fund_a,63)",
                    "knowledge_refs": [self.knowledge_ref],
                    "feedback_patterns_used": [],
                    "likely_failure_modes": ["LOW_SHARPE"],
                    **_mechanism_contract(expression),
                }]}
            return {"approved": [{"approved": True, "expression": "ts_mean(fund_a,63)", "critique": "local catalog checked"}]}

    def offline_only(*_args, **_kwargs):
        raise AssertionError("partial offline production generation attempted network access")

    platform_modules_before = {
        name for name in sys.modules if name == "alpha_mining.platform" or name.startswith("alpha_mining.platform.")
    }
    monkeypatch.setattr(socket, "getaddrinfo", offline_only)
    monkeypatch.setattr(socket, "create_connection", offline_only)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", candidates_per_cycle=1),
        llm=OfflineLLM(),
        kernel=OfflineKernel(),
    )

    assert summary.state == "COMPLETE"
    assert summary.catalog_operators == 15
    assert summary.state != "CATALOG_UNAVAILABLE"
    assert {
        name for name in sys.modules if name == "alpha_mining.platform" or name.startswith("alpha_mining.platform.")
    } == platform_modules_before


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
    assert summary.queue_rows == ()


def test_invalid_research_plan_is_repaired_before_candidate_generation(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    class PlanRepairLLM(_LLM):
        def __init__(self) -> None:
            super().__init__()
            self.prompts: list[str] = []

        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            del system_prompt, json_schema
            self.prompts.append(user_prompt)
            self.calls.append("call")
            if len(self.calls) == 1:
                refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
                self.knowledge_ref = refs[0]
                return {
                    "research_direction": "fundamental quality", "hypothesis": "quality persists",
                    "economic_mechanism": "slow fundamental information diffusion", "expected_horizon": "medium",
                    "fields_to_use": ["cap"], "operators_to_use": ["rank", "group_neutralize"],
                    "anti_correlation_plan": "use a distinct fundamental field", "expected_turnover_behavior": "medium-low",
                    "historical_failures_to_avoid": ["SELF_CORRELATION"], "knowledge_refs": [self.knowledge_ref],
                }
            if len(self.calls) == 2:
                return {
                    "research_direction": "fundamental quality", "hypothesis": "quality persists",
                    "economic_mechanism": "slow fundamental information diffusion", "expected_horizon": "medium",
                    "fields_to_use": ["fund_b"],
                    "operators_to_use": ["rank", "ts_rank", "group_neutralize"],
                    "anti_correlation_plan": "use a distinct fundamental field", "expected_turnover_behavior": "medium-low",
                    "historical_failures_to_avoid": ["SELF_CORRELATION"], "knowledge_refs": [self.knowledge_ref],
                }
            if len(self.calls) == 3:
                expression = "group_neutralize(ts_rank(fund_b,63),market)"
                return {"candidates": [{
                    "expression": expression, "settings": {},
                    "economic_rationale": "Slow-moving fundamental quality information persists over a medium horizon.",
                    "novelty_reason": "The repaired plan uses only the grounded field scope.",
                    "anti_corr_design": "Uses a separate field and a medium horizon to reduce crowding.",
                    "parent_seed": "group_neutralize(rank(fund_b),market)",
                    "knowledge_refs": [self.knowledge_ref], "feedback_patterns_used": [],
                    "likely_failure_modes": ["LOW_SHARPE"],
                    **_mechanism_contract(expression),
                }]}
            if len(self.calls) == 4:
                return {"approved": [{"approved": True, "critique": "grounded plan and candidate"}]}
            raise AssertionError("unexpected extra LLM call")

    _write_catalog(tmp_path)
    llm = PlanRepairLLM()
    result = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"), llm=llm, kernel=_Kernel(),
    )

    assert result.enqueued == 1
    assert len(llm.calls) == 4
    assert "PLAN_UNKNOWN_FIELD" in (result.rejections or {})
    assert '"cap"' in llm.prompts[1]


def test_unresolved_plan_scope_is_grounded_locally_before_candidate_generation(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    now = time.time()
    (tmp_path / ".alpha_datasets_cache.json").write_text(
        json.dumps({"cached_at": now, "dataset_ids": ["fund", "other"], "records": [{"id": "fund"}, {"id": "other"}]}),
        encoding="utf-8",
    )
    (tmp_path / ".alpha_datafields_cache.json").write_text(
        json.dumps({"cached_at": now, "rows": [
            {"id": "fund_a", "_ds": "fund", "type": "MATRIX", "description": "quality"},
            {"id": "fund_b", "_ds": "fund", "type": "MATRIX", "description": "value"},
            {"id": "other_a", "_ds": "other", "type": "MATRIX", "description": "unrelated"},
        ]}),
        encoding="utf-8",
    )

    class ScopeFailureLLM(_LLM):
        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            del system_prompt, json_schema
            self.calls.append(user_prompt)
            if len(self.calls) in {1, 2}:
                refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
                if refs:
                    self.knowledge_ref = refs[0]
                return {
                    "research_direction": "fundamental quality", "hypothesis": "quality persists",
                    "economic_mechanism": "slow fundamental information diffusion", "expected_horizon": "medium",
                    "fields_to_use": ["fund_a", "other_a"], "operators_to_use": ["rank", "ts_rank", "group_neutralize", "sub"],
                    "anti_correlation_plan": "use a distinct fundamental field", "expected_turnover_behavior": "medium-low",
                    "historical_failures_to_avoid": ["SELF_CORRELATION"], "knowledge_refs": [self.knowledge_ref],
                }
            if len(self.calls) == 3:
                expression = "group_neutralize(ts_rank(fund_a,63),market)"
                return {"candidates": [{
                    "expression": expression, "settings": {},
                    "economic_rationale": "Slow-moving fundamental quality information persists over a medium horizon.",
                    "novelty_reason": "Uses a grounded local field and a slow transform.",
                    "anti_corr_design": "Uses a medium window and sector-neutralized field-specific signal.",
                    "parent_seed": expression, "knowledge_refs": [self.knowledge_ref], "feedback_patterns_used": [],
                    "likely_failure_modes": ["LOW_SHARPE"], **_mechanism_contract(expression),
                }]}
            if len(self.calls) == 4:
                return {"approved": [{"approved": True, "critique": "grounded plan and candidate"}]}
            raise AssertionError("unexpected extra LLM call")

    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"),
        llm=ScopeFailureLLM(), kernel=_Kernel(),
    )

    assert summary.enqueued == 1
    assert len(summary.queue_rows) == 1
    assert len(summary.rejections or {}) >= 1
    assert summary.queue_rows[0]["generator_source"] == "LLM_LOCALLY_GROUNDED_PLAN"
    evidence = json.loads(summary.queue_rows[0]["quality_evidence_json"])
    assert evidence["plan_locally_grounded"] is True
    assert evidence["catalog_source"] == "root-dot-cache"
    assert evidence["catalog_age_hours"] >= 0
    assert evidence["portfolio_selection"]["mode"] == "enforce"
    assert evidence["portfolio_selection"]["decision"] == "ACCEPT"


def test_non_llm_generation_failure_is_not_reported_as_llm_unavailable(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    class BrokenKernel:
        def generate(self, *_args, **_kwargs):
            raise RuntimeError("local kernel failure")

    _write_catalog(tmp_path)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"),
        llm=_LLM(), kernel=BrokenKernel(),
    )

    assert summary.state == "GENERATION_FAILED"


def test_value_error_from_local_kernel_is_not_reported_as_llm_unavailable(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    class BrokenKernel:
        def generate(self, *_args, **_kwargs):
            raise ValueError("invalid local metadata")

    _write_catalog(tmp_path)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"),
        llm=_LLM(), kernel=BrokenKernel(),
    )

    assert summary.state == "GENERATION_FAILED"
    assert summary.enqueued == 0


def test_windows_sigbreak_uses_the_graceful_keyboard_interrupt_path(monkeypatch) -> None:
    import signal

    from alpha_mining.generation import production

    registered = {}
    monkeypatch.setattr(signal, "signal", lambda event, handler: registered.setdefault(event, handler))
    production._install_console_interrupt_handler()

    if hasattr(signal, "SIGBREAK"):
        assert registered[signal.SIGBREAK] is production._raise_keyboard_interrupt
        try:
            registered[signal.SIGBREAK](signal.SIGBREAK, None)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("SIGBREAK handler must enter the graceful KeyboardInterrupt path")


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
    assert len(rows) == 1
    assert rows[0]["queue_status"] == "SIMULATED"
    assert second.enqueued == 0
    assert (second.rejections or {}).get("LOW_LOCAL_QUALITY", 0) == 1
    assert not list(tmp_path.glob("*.tmp"))
    assert not config.queue_path.with_suffix(config.queue_path.suffix + ".lock").exists()


def test_hallucinated_llm_fields_operators_and_refs_are_hard_rejected(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    class BadLLM(_LLM):
        def __init__(self, kind: str) -> None:
            super().__init__()
            self.kind = kind

        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            del system_prompt, json_schema
            self.calls.append(user_prompt)
            phase = len(self.calls) % 3
            if phase == 1:
                refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
                self.knowledge_ref = refs[0]
                return {
                    "research_direction": "fundamental", "hypothesis": "quality", "economic_mechanism": "slow information diffusion",
                    "expected_horizon": "medium", "fields_to_use": ["fund_a", "fund_b"],
                    "operators_to_use": ["rank", "ts_rank", "group_neutralize"], "anti_correlation_plan": "slow field diversification",
                    "expected_turnover_behavior": "low", "historical_failures_to_avoid": ["SELF_CORRELATION"], "knowledge_refs": [self.knowledge_ref],
                }
            if phase == 2:
                expression = "group_neutralize(rank(fund_a),market)"
                refs = [self.knowledge_ref]
                if self.kind == "field":
                    expression = "group_neutralize(rank(fake_field),market)"
                elif self.kind == "operator":
                    expression = "group_neutralize(fake_operator(fund_a),market)"
                elif self.kind == "ref":
                    refs = ["worldquant:invented#1"]
                return {"candidates": [{
                    "expression": expression, "settings": {}, "economic_rationale": "a sufficiently explicit economic mechanism for testing",
                    "novelty_reason": "test", "anti_corr_design": "slow window and independent field selection",
                    "parent_seed": "group_neutralize(ts_rank(fund_a,63),market)", "knowledge_refs": refs,
                    "feedback_patterns_used": [], "likely_failure_modes": [],
                    **_mechanism_contract(expression),
                }]}
            return {"approved": [{"approved": True, "critique": "passes model critique"}]}

    _write_catalog(tmp_path)
    for kind, expected in (("field", "UNKNOWN_FIELD"), ("operator", "UNKNOWN_OPERATOR"), ("ref", "HALLUCINATED_KNOWLEDGE_REF")):
        root = tmp_path / kind
        root.mkdir()
        _write_catalog(root)
        result = run_cycle(ProductionConfig(root=root, database=root / "history.sqlite"), llm=BadLLM(kind), kernel=_Kernel())
        assert result.enqueued == 0
        assert expected in (result.rejections or {})


def test_scope_repair_reasks_llm_but_keeps_all_hard_gates(tmp_path) -> None:
    """A malformed first LLM draft gets one constrained repair, never a bypass."""

    from alpha_mining.generation.production import ProductionConfig, run_cycle

    class RecordingScopeRepairLLM(_LLM):
        def __init__(self) -> None:
            super().__init__()
            self.prompts: list[str] = []

        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            del system_prompt, json_schema
            self.prompts.append(user_prompt)
            if len(self.calls) == 0:
                refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
                self.knowledge_ref = refs[0]
                self.calls.append("plan")
                return {
                    "research_direction": "fundamental quality", "hypothesis": "quality persists",
                    "economic_mechanism": "slow fundamental information diffusion", "expected_horizon": "medium",
                    "fields_to_use": ["fund_a"],
                    "operators_to_use": ["rank", "ts_rank", "group_neutralize"],
                    "anti_correlation_plan": "slow field diversification", "expected_turnover_behavior": "medium-low",
                    "historical_failures_to_avoid": ["SELF_CORRELATION"], "knowledge_refs": [self.knowledge_ref],
                }
            self.calls.append("next")
            if len(self.calls) == 2:
                expression = "group_neutralize(rank(fund_b),market)"
            elif len(self.calls) == 3:
                return {"approved": [{"approved": True, "critique": "initial draft reviewed"}]}
            elif len(self.calls) == 4:
                expression = "group_neutralize(rank(fund_a),market)"
            elif len(self.calls) == 5:
                return {"approved": [{"approved": True, "critique": "repaired draft reviewed"}]}
            else:
                raise AssertionError("unexpected extra LLM call")
            return {"candidates": [{
                "expression": expression, "settings": {},
                "economic_rationale": "A slow, cross-sectional fundamental quality signal captures persistent information diffusion.",
                "novelty_reason": "Uses a distinct allowed fundamental field after the invalid draft was rejected.",
                "anti_corr_design": "Uses a separate field and avoids the rejected invalid identifier.",
                "parent_seed": "group_neutralize(ts_rank(fund_a,63),market)", "knowledge_refs": [self.knowledge_ref],
                "feedback_patterns_used": [], "likely_failure_modes": ["LOW_SHARPE"],
                    **_mechanism_contract(expression),
            }]}

    _write_catalog(tmp_path)
    llm = RecordingScopeRepairLLM()
    result = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"), llm=llm, kernel=_Kernel(),
    )

    assert result.enqueued == 1
    assert len(llm.calls) == 5
    assert "PLAN_SCOPE_VIOLATION" in (result.rejections or {})
    assert result.queue_rows[0]["expression"] == "group_neutralize(rank(fund_a),market)"
    assert '"forbidden_identifiers"' in llm.prompts[3]
    assert '"cap"' in llm.prompts[3]


def test_history_self_correlation_and_low_sharpe_change_next_seed_selection(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    (tmp_path / "alpha_submission_feedback.csv").write_text(
        "expression,status,self_correlation_status,Failure Reasons\n"
        '"group_neutralize(ts_rank(fund_a,126),market)",ok,FAIL,"SELF_CORRELATION;LOW_SHARPE"\n',
        encoding="utf-8-sig",
    )

    result = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"),
        llm=_LLM(), kernel=_Kernel(),
    )

    assert result.self_corr_risk == 1
    assert result.v50_seeds == 1, result.detail  # the f_a topology was excluded before LLM selection
    assert result.enqueued == 0
    assert "UNKNOWN_PARENT_SEED" in (result.rejections or {})


def test_unchanged_zero_output_cycle_skips_llm_and_increases_backoff(tmp_path) -> None:
    from alpha_mining.generation.production import GenerationLoopState, ProductionConfig, run_cycle

    class EmptyLLM(_LLM):
        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            del system_prompt, json_schema
            self.calls.append(user_prompt)
            phase = len(self.calls) % 3
            if phase == 1:
                refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
                self.knowledge_ref = refs[0]
                return {
                    "research_direction": "fundamental quality", "hypothesis": "quality persists",
                    "economic_mechanism": "slow information diffusion", "expected_horizon": "medium",
                    "fields_to_use": ["fund_a"], "operators_to_use": ["rank", "ts_rank", "group_neutralize"],
                    "anti_correlation_plan": "distinct field", "expected_turnover_behavior": "low",
                    "historical_failures_to_avoid": [], "knowledge_refs": [self.knowledge_ref],
                }
            if phase == 2:
                return {"candidates": []}
            return {"approved": []}

    _write_catalog(tmp_path)
    llm = EmptyLLM()
    state = GenerationLoopState()
    config = ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", interval_seconds=300)

    first = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    calls_after_first = len(llm.calls)
    second = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    third = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    fourth = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    fifth = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)

    assert first.enqueued == 0
    assert calls_after_first == 3
    assert second.state == "NO_NEW_EVIDENCE"
    assert third.state == "NO_NEW_EVIDENCE"
    assert fourth.state == "NO_NEW_EVIDENCE"
    assert second.next_wait_seconds == 900
    assert third.next_wait_seconds == 1800
    assert fourth.next_wait_seconds == 3600
    assert fifth.state == "COMPLETE"
    assert len(llm.calls) == calls_after_first + 3


def test_forced_refresh_failure_is_retried_instead_of_skipped(tmp_path) -> None:
    from alpha_mining.generation.production import GenerationLoopState, ProductionConfig, run_cycle

    class FailingRefreshLLM(_LLM):
        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            if len(self.calls) == 0:
                return super().generate_json(
                    system_prompt=system_prompt, user_prompt=user_prompt, json_schema=json_schema
                )
            if len(self.calls) == 1:
                self.calls.append(user_prompt)
                return {"candidates": []}
            if len(self.calls) == 2:
                self.calls.append(user_prompt)
                return {"approved": []}
            if len(self.calls) >= 3:
                raise RuntimeError("temporary transport failure")
            raise AssertionError("unexpected call sequence")

    _write_catalog(tmp_path)
    llm = FailingRefreshLLM()
    state = GenerationLoopState()
    config = ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", interval_seconds=300)

    first = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    assert first.enqueued == 0
    run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    failed = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    retry = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)

    assert failed.state == "LLM_UNAVAILABLE"
    assert retry.state == "LLM_UNAVAILABLE"
    assert len(llm.calls) == 3


def test_pending_limit_waits_for_consumer_without_calling_llm(tmp_path) -> None:
    from alpha_mining.generation.production import GenerationLoopState, ProductionConfig, run_cycle
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    class ForbiddenLLM:
        model_id = "must-not-run"

        def generate_json(self, **_kwargs):
            raise AssertionError("LLM must not run when pending queue is full")

    _write_catalog(tmp_path)
    config = ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", pending_limit=20)
    queue = CandidateCsvQueue(config.queue_path, config.events_path)
    with queue.writer():
        for index in range(20):
            row = queue.empty_candidate()
            row.update(
                candidate_id=f"candidate-{index}", request_hash=f"request-{index}",
                expression=f"rank(fund_a)+{index}", queue_status="PENDING_SIMULATION",
                quality_evidence_json=json.dumps({"generator_contract_version": "generation-hq-v2"}),
            )
            queue.upsert(row)

    summary = run_cycle(config, llm=ForbiddenLLM(), kernel=_Kernel(), runtime_state=GenerationLoopState())

    assert summary.state == "WAITING_FOR_CONSUMER"
    assert summary.pending_total == 20


def test_legacy_pending_candidate_without_v2_evidence_is_quarantined_not_deleted(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    class EmptyKernel:
        def generate(self, *_args, **_kwargs):
            return []

    class UnusedLLM:
        model_id = "unused"

        def generate_json(self, **_kwargs):
            raise AssertionError("empty kernel should not call the LLM")

    _write_catalog(tmp_path)
    config = ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite")
    queue = CandidateCsvQueue(config.queue_path, config.events_path)
    row = queue.empty_candidate()
    row.update(
        candidate_id="legacy-1", request_hash="legacy-request", expression="group_neutralize(ts_rank(fund_a,63),market)",
        queue_status="PENDING_SIMULATION", quality_evidence_json="{}",
    )
    with queue.writer():
        queue.upsert(row)

    summary = run_cycle(config, llm=UnusedLLM(), kernel=EmptyKernel())
    rows = CandidateCsvQueue(config.queue_path, config.events_path).read()

    assert summary.state == "COMPLETE"
    assert len(rows) == 1
    assert rows[0]["queue_status"] == "REJECTED_LOCAL_REVALIDATION"
    assert rows[0]["last_error_category"] == "LEGACY_CONTRACT_MISSING_EVIDENCE"


def test_worldquant_knowledge_change_resets_no_new_evidence_skip(tmp_path) -> None:
    from alpha_mining.generation.production import GenerationLoopState, ProductionConfig, run_cycle

    class EmptyLLM(_LLM):
        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            del system_prompt, json_schema
            self.calls.append(user_prompt)
            phase = len(self.calls) % 3
            if phase == 1:
                refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
                self.knowledge_ref = refs[0]
                return {
                    "research_direction": "fundamental quality",
                    "hypothesis": "quality persists",
                    "economic_mechanism": "slow information diffusion",
                    "expected_horizon": "medium",
                    "fields_to_use": ["fund_a"],
                    "operators_to_use": ["rank", "ts_rank", "group_neutralize"],
                    "anti_correlation_plan": "distinct field",
                    "expected_turnover_behavior": "low",
                    "historical_failures_to_avoid": [],
                    "knowledge_refs": [self.knowledge_ref],
                }
            if phase == 2:
                return {"candidates": []}
            return {"approved": []}

    _write_catalog(tmp_path)
    knowledge_root = tmp_path / "World quant"
    knowledge_root.mkdir()
    knowledge_file = knowledge_root / "优质Alpha挖掘.md"
    knowledge_file.write_text("基本面 Alpha 应坚持假说优先、算子多样性并降低自相关。", encoding="utf-8")
    config = ProductionConfig(
        root=tmp_path,
        database=tmp_path / "history.sqlite",
        knowledge_root=knowledge_root,
        interval_seconds=300,
    )
    llm = EmptyLLM()
    state = GenerationLoopState()

    first = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    second = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    calls_before_change = len(llm.calls)
    knowledge_file.write_text("基本面 Alpha 应坚持假说优先、算子多样性、降低自相关并避免拥挤字段。", encoding="utf-8")
    third = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)

    assert first.enqueued == 0
    assert second.state == "NO_NEW_EVIDENCE"
    assert third.state == "COMPLETE"
    assert len(llm.calls) > calls_before_change


def test_research_prompt_contains_used_queue_directions_fields_topologies_and_rejections(tmp_path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    _write_catalog(tmp_path)
    config = ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", candidates_per_cycle=1)
    queue = CandidateCsvQueue(config.queue_path, config.events_path)
    row = queue.empty_candidate()
    row.update(
        candidate_id="old-candidate",
        request_hash="old-request",
        expression="rank(fund_a + fund_b)",
        data_fields='["fund_a","fund_b"]',
        datasets='["fund"]',
        research_direction="already-used-value-quality",
        operator_family="rank",
        queue_status="REJECTED_LOCAL_REVALIDATION",
        last_error_category="CYCLE_SIMILARITY",
    )
    with queue.writer():
        queue.upsert(row)
        queue.record_event("old-cycle", "LOCAL_REJECTED", "CYCLE_SIMILARITY:3")

    llm = _LLM()
    run_cycle(config, llm=llm, kernel=_Kernel())

    research_payload = json.loads(llm.calls[0])
    inventory = research_payload["candidate_inventory"]
    assert "already-used-value-quality" in inventory["used_research_directions"]
    assert ["fund_a", "fund_b"] in inventory["used_field_sets"]
    assert inventory["used_operator_topologies"]
    assert inventory["recent_rejection_counts"]["CYCLE_SIMILARITY"] == 4


def test_new_local_rejection_evidence_resets_no_new_evidence_skip(tmp_path) -> None:
    from alpha_mining.generation.production import GenerationLoopState, ProductionConfig, run_cycle
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    class EmptyLLM(_LLM):
        def generate_json(self, *, system_prompt, user_prompt, json_schema):
            del system_prompt, json_schema
            self.calls.append(user_prompt)
            phase = len(self.calls) % 3
            if phase == 1:
                refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
                self.knowledge_ref = refs[0]
                return {
                    "research_direction": "fundamental quality",
                    "hypothesis": "quality persists",
                    "economic_mechanism": "slow information diffusion",
                    "expected_horizon": "medium",
                    "fields_to_use": ["fund_a"],
                    "operators_to_use": ["rank", "ts_rank", "group_neutralize"],
                    "anti_correlation_plan": "distinct field",
                    "expected_turnover_behavior": "low",
                    "historical_failures_to_avoid": [],
                    "knowledge_refs": [self.knowledge_ref],
                }
            if phase == 2:
                return {"candidates": []}
            return {"approved": []}

    _write_catalog(tmp_path)
    config = ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", interval_seconds=300)
    llm = EmptyLLM()
    state = GenerationLoopState()

    first = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    second = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)
    calls_before_event = len(llm.calls)
    queue = CandidateCsvQueue(config.queue_path, config.events_path)
    with queue.writer():
        queue.record_event("external-review", "LOCAL_REJECTED", "MECHANISM_FIELD_MISMATCH:2")
    third = run_cycle(config, llm=llm, kernel=_Kernel(), runtime_state=state)

    assert first.enqueued == 0
    assert second.state == "NO_NEW_EVIDENCE"
    assert third.state == "COMPLETE"
    assert len(llm.calls) > calls_before_event
