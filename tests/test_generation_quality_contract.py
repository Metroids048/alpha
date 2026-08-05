from __future__ import annotations

import json
import re
import time
from pathlib import Path


def _write_catalog(root: Path) -> None:
    context = {"cached_at": time.time(), "region": "USA", "universe": "TOP3000", "delay": 1, "source": "test"}
    (root / ".alpha_datasets_cache.json").write_text(
        json.dumps({**context, "dataset_ids": ["fund"], "records": [{"id": "fund"}]}), encoding="utf-8"
    )
    (root / ".alpha_datafields_cache.json").write_text(
        json.dumps({
            **context,
            "rows": [
                {"id": "fund_a", "_ds": "fund", "type": "MATRIX", "description": "quality", "coverage": 0.91, "dateCoverage": 0.88, "userCount": 24},
                {"id": "fund_b", "_ds": "fund", "type": "MATRIX", "description": "value", "coverage": 0.72, "dateCoverage": 0.70, "userCount": 118},
            ],
        }), encoding="utf-8"
    )
    (root / ".alpha_operators_cache.json").write_text(
        json.dumps({
            **context,
            "records": [
                {"name": "rank", "signature": "rank(x)", "arity": 1},
                {"name": "ts_rank", "signature": "ts_rank(x,d)", "arity": 2},
                {"name": "group_neutralize", "signature": "group_neutralize(x,g)", "arity": 2},
            ],
        }), encoding="utf-8"
    )


class _Kernel:
    def generate(self, *_args, **_kwargs):
        from auto_alpha_pipeline_rebuilt_v50 import ExpressionCandidate

        return [
            ExpressionCandidate("group_neutralize(ts_rank(fund_a,63),market)", "fundamental", "v50", 2.0),
            ExpressionCandidate("group_neutralize(ts_rank(fund_b,63),market)", "fundamental", "v50", 1.9),
        ]


class _TwoCandidateLLM:
    model_id = "fake-deepseek"

    def __init__(self, *, mismatch: bool = False, extra_operator: bool = False) -> None:
        self.calls = 0
        self.knowledge_ref = ""
        self.mismatch = mismatch
        self.extra_operator = extra_operator

    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        del system_prompt, json_schema
        self.calls += 1
        if self.calls == 1:
            refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
            self.knowledge_ref = refs[0]
            return {
                "research_direction": "fundamental quality",
                "hypothesis": "slow information diffusion",
                "economic_mechanism": "fundamental information is incorporated gradually",
                "expected_horizon": "medium",
                "fields_to_use": ["fund_a", "fund_b"],
                "operators_to_use": ["rank", "ts_rank", "group_neutralize"],
                "anti_correlation_plan": "use distinct fields and slow transforms",
                "expected_turnover_behavior": "medium-low",
                "historical_failures_to_avoid": ["SELF_CORRELATION"],
                "knowledge_refs": [self.knowledge_ref],
            }
        if self.calls == 2:
            rows = []
            for field in ("fund_a", "fund_b"):
                expression = f"group_neutralize(ts_rank({field},63),market)"
                claimed = "fund_b" if self.mismatch and field == "fund_a" else field
                rows.append({
                    "expression": expression,
                    "settings": {},
                    "economic_rationale": f"{claimed} captures slowly diffusing fundamental quality information",
                    "novelty_reason": "distinct field mechanism",
                    "anti_corr_design": f"{field} and ts_rank diversify the signal",
                    "parent_seed": "group_neutralize(ts_rank(fund_a,63),market)",
                    "knowledge_refs": [self.knowledge_ref],
                    "feedback_patterns_used": [],
                    "likely_failure_modes": ["LOW_SHARPE"],
                    "field_roles": [{"field_id": claimed, "role": "fundamental quality input"}],
                    "operator_roles": [
                        {"operator": "ts_rank", "role": "slow persistence"},
                        {"operator": "group_neutralize", "role": "market diversification"},
                    ] + ([{"operator": "rank", "role": "claimed but unused"}] if self.extra_operator else []),
                    "turnover_controls": ["ts_rank"],
                    "correlation_diversifiers": [field, "group_neutralize"],
                })
            return {"candidates": rows}
        return {"approved": [{"approved": True}, {"approved": True}]}


class _CritiqueOnlyRejectingLLM(_TwoCandidateLLM):
    """Models a narrative-only critic failure on both draft and repair passes."""

    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        if self.calls < 2:
            return super().generate_json(
                system_prompt=system_prompt, user_prompt=user_prompt, json_schema=json_schema
            )
        self.calls += 1
        if self.calls in {3, 5}:
            return {"approved": [{"approved": False, "critique": "narrative mismatch"}]}
        expression = "group_neutralize(ts_rank(fund_a,63),market)"
        return {
            "candidates": [{
                "expression": expression,
                "settings": {},
                "economic_rationale": "fundamental information diffuses slowly into prices",
                "novelty_reason": "slow persistence",
                "anti_corr_design": "long window and group neutralization",
                "parent_seed": "group_neutralize(ts_rank(fund_a,63),market)",
                "knowledge_refs": [self.knowledge_ref],
                "feedback_patterns_used": [],
                "likely_failure_modes": ["LOW_SHARPE"],
                "field_roles": [{"field_id": "fund_a", "role": "economic input"}],
                "operator_roles": [
                    {"operator": "ts_rank", "role": "slow persistence"},
                    {"operator": "group_neutralize", "role": "group diversification"},
                ],
                "turnover_controls": ["ts_rank"],
                "correlation_diversifiers": ["fund_a", "group_neutralize"],
            }],
        }


class _RepairThenRejectingLLM:
    """Models the production path where repair rows still fail local gates."""

    model_id = "fake-deepseek"

    def __init__(self) -> None:
        self.calls = 0
        self.knowledge_ref = ""

    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        del system_prompt, json_schema
        self.calls += 1
        if self.calls == 1:
            refs = re.findall(r'"ref_id":\s*"([^"]+)"', user_prompt)
            self.knowledge_ref = refs[0]
            return {
                "research_direction": "fundamental quality",
                "hypothesis": "slow information diffusion",
                "economic_mechanism": "fundamental information is incorporated gradually",
                "expected_horizon": "medium",
                "fields_to_use": ["fund_a", "fund_b"],
                "operators_to_use": ["rank", "ts_rank", "group_neutralize"],
                "anti_correlation_plan": "use distinct fields and slow transforms",
                "expected_turnover_behavior": "medium-low",
                "historical_failures_to_avoid": ["SELF_CORRELATION"],
                "knowledge_refs": [self.knowledge_ref],
            }
        if self.calls == 2:
            rows = []
            for index in range(10):
                expression = f"unknown_operator_{index}(fund_a)"
                rows.append({
                    "expression": expression,
                    "settings": {},
                    "economic_rationale": "A slow fundamental signal captures persistent information diffusion.",
                    "novelty_reason": "The draft explores a distinct transformation.",
                    "anti_corr_design": "A field-specific signal avoids broad market exposure.",
                    "parent_seed": "group_neutralize(ts_rank(fund_a,63),market)",
                    "knowledge_refs": [self.knowledge_ref],
                    "feedback_patterns_used": [],
                    "likely_failure_modes": ["LOW_SHARPE"],
                    "field_roles": [{"field_id": "fund_a", "role": "economic input"}],
                    "operator_roles": [{"operator": f"unknown_operator_{index}", "role": "invalid draft transform"}],
                    "turnover_controls": [],
                    "correlation_diversifiers": ["fund_a"],
                })
            return {"candidates": rows}
        if self.calls == 3:
            return {"approved": [{"approved": True} for _ in range(10)]}
        if self.calls == 4:
            expression = "ts_rank(fund_a,126)"
            return {"candidates": [{
                "expression": expression,
                "settings": {},
                "economic_rationale": "A slow fundamental signal captures persistent information diffusion.",
                "novelty_reason": "The repair uses a long time-series window.",
                "anti_corr_design": "A single grounded field avoids unsupported cross-dataset exposure.",
                "parent_seed": "group_neutralize(ts_rank(fund_a,63),market)",
                "knowledge_refs": [self.knowledge_ref],
                "feedback_patterns_used": [],
                "likely_failure_modes": ["LOW_SHARPE"],
                "field_roles": [{"field_id": "fund_a", "role": "economic input"}],
                "operator_roles": [
                    {"operator": "ts_rank", "role": "slow persistence"},
                    {"operator": "rank", "role": "unused extra operator"},
                ],
                "turnover_controls": ["ts_rank"],
                "correlation_diversifiers": ["fund_a"],
            }]}
        if self.calls == 5:
            return {"approved": [{"approved": True}]}
        raise AssertionError("unexpected extra LLM call")


class _RepairCritiqueRejectingLLM(_RepairThenRejectingLLM):
    def generate_json(self, *, system_prompt, user_prompt, json_schema):
        if self.calls == 4:
            self.calls += 1
            return {"approved": [{"approved": False, "critique": "explicit correlation risk"}]}
        return super().generate_json(
            system_prompt=system_prompt, user_prompt=user_prompt, json_schema=json_schema
        )


def test_same_cycle_proxy_similarity_gate_keeps_only_one_highly_similar_candidate(tmp_path: Path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", candidates_per_cycle=2),
        llm=_TwoCandidateLLM(), kernel=_Kernel(),
    )

    assert summary.enqueued == 1
    assert (summary.rejections or {}).get("CYCLE_SIMILARITY", 0) == 1
    assert float(summary.queue_rows[0]["local_quality_score"]) <= 85.0


def test_narrative_only_critic_rejection_recovers_through_deterministic_gates(tmp_path: Path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"),
        llm=_CritiqueOnlyRejectingLLM(), kernel=_Kernel(),
    )

    assert summary.enqueued == 1
    assert (summary.rejections or {}).get("LLM_CRITIQUE_RECOVERED_BY_DETERMINISTIC_GATES") == 1
    assert summary.queue_rows[0]["quality_evidence_json"]


def test_repair_rows_rejected_by_local_gates_recover_without_bypassing_validation(tmp_path: Path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    llm = _RepairThenRejectingLLM()
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"),
        llm=llm, kernel=_Kernel(),
    )

    assert summary.enqueued == 1
    assert llm.calls == 5
    assert (summary.rejections or {}).get("UNKNOWN_OPERATOR", 0) == 10
    assert (summary.rejections or {}).get("MECHANISM_OPERATOR_MISMATCH", 0) >= 1
    assert (summary.rejections or {}).get("DETERMINISTIC_LOCAL_FALLBACK_USED") == 1
    evidence = json.loads(summary.queue_rows[0]["quality_evidence_json"])
    assert evidence["generator_contract_version"] == "generation-hq-v2"


def test_repair_critic_rejection_does_not_trigger_deterministic_fallback(tmp_path: Path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite"),
        llm=_RepairCritiqueRejectingLLM(), kernel=_Kernel(),
    )

    assert summary.enqueued == 0
    assert (summary.rejections or {}).get("LLM_CRITIQUE_REJECTED", 0) >= 1
    assert "DETERMINISTIC_LOCAL_FALLBACK_USED" not in (summary.rejections or {})


def test_existing_pending_inventory_is_included_in_similarity_gate(tmp_path: Path) -> None:
    from alpha_mining.generation.production import ProductionConfig, _queue_row, run_cycle
    from alpha_mining.generation.high_quality import AcceptedCandidate
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    _write_catalog(tmp_path)
    config = ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", candidates_per_cycle=1)
    queue = CandidateCsvQueue(config.queue_path, config.events_path)
    existing = AcceptedCandidate(
        expression="rank(group_neutralize(ts_rank(fund_a,126),market))", settings={"alpha_type": "REGULAR", "region": "USA", "universe": "TOP3000", "delay": 1, "decay": 4, "neutralization": "MARKET", "truncation": 0.08, "language": "FASTEXPR"},
        datasets=("fund",), parent_seed="seed", research_direction="old", hypothesis="old", economic_rationale="old",
        knowledge_refs=(), feedback_refs=(), anti_corr_design="old", expected_turnover_behavior="low",
        local_quality_score=80, novelty_score=1, self_corr_risk_score=0, quality_evidence={}, generator_source="fixture",
    )
    with queue.writer():
        queue.upsert(_queue_row(existing, model_id="fixture"))

    summary = run_cycle(config, llm=_TwoCandidateLLM(), kernel=_Kernel())

    assert summary.enqueued == 0
    assert (summary.rejections or {}).get("INVENTORY_SIMILARITY", 0) >= 1


def test_claimed_field_not_present_in_expression_is_rejected(tmp_path: Path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", candidates_per_cycle=1),
        llm=_TwoCandidateLLM(mismatch=True), kernel=_Kernel(),
    )

    assert summary.enqueued == 0
    assert (summary.rejections or {}).get("MECHANISM_FIELD_MISMATCH", 0) >= 1


def test_claimed_operator_not_present_in_expression_is_rejected(tmp_path: Path) -> None:
    from alpha_mining.generation.production import ProductionConfig, run_cycle

    _write_catalog(tmp_path)
    summary = run_cycle(
        ProductionConfig(root=tmp_path, database=tmp_path / "history.sqlite", candidates_per_cycle=1),
        llm=_TwoCandidateLLM(extra_operator=True), kernel=_Kernel(),
    )

    assert summary.enqueued == 0
    assert (summary.rejections or {}).get("MECHANISM_OPERATOR_MISMATCH", 0) >= 1
