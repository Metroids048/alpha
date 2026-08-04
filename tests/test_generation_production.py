from __future__ import annotations

import json
import re
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
    assert llm.knowledge_ref.startswith("worldquant:")
    assert llm.knowledge_ref in llm.calls[0]
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
                return {"candidates": [{
                    "expression": "group_neutralize(ts_rank(fund_b,63),market)", "settings": {},
                    "economic_rationale": "Slow-moving fundamental quality information persists over a medium horizon.",
                    "novelty_reason": "The repaired plan uses only the grounded field scope.",
                    "anti_corr_design": "Uses a separate field and a medium horizon to reduce crowding.",
                    "parent_seed": "group_neutralize(rank(fund_b),market)",
                    "knowledge_refs": [self.knowledge_ref], "feedback_patterns_used": [],
                    "likely_failure_modes": ["LOW_SHARPE"],
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
    assert len(rows) == 2
    assert rows[0]["queue_status"] == "SIMULATED"
    assert {row["queue_status"] for row in rows} == {"SIMULATED", "PENDING_SIMULATION"}
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
