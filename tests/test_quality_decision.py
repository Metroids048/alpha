from __future__ import annotations

import sqlite3

from alpha_mining.generation.feedback import CandidateFeedbackStore
from alpha_mining.storage.migrations import migrate


def _checks(*, prod: str = "PASS") -> list[dict[str, object]]:
    return [
        {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
        {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
        {"name": "PROD_CORRELATION", "result": prod, "mandatory": True},
    ]


def test_ready_requires_every_hard_gate_and_uses_stricter_live_threshold() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    decision = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.70, "fitness": 1.05, "turnover": 0.25},
        checks=_checks(),
        live_thresholds={"sharpe": 1.65, "fitness": 1.02, "turnover_max": 0.65},
    )

    assert decision.status is QualityStatus.READY_TO_SUBMIT
    assert decision.repairable is False


def test_missing_check_is_waiting_not_ready() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    decision = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.70, "fitness": 1.05, "turnover": 0.25},
        checks=[{"name": "LOW_SHARPE", "result": "PASS", "mandatory": True}],
    )

    assert decision.status is QualityStatus.WAITING_CHECKS
    assert "SELF_CORRELATION_MISSING" in decision.reasons


def test_mandatory_metric_check_failure_blocks_ready() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    decision = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.70, "fitness": 1.05, "turnover": 0.25},
        checks=[
            {"name": "LOW_SHARPE", "result": "FAIL", "mandatory": True},
            {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
            {"name": "PROD_CORRELATION", "result": "PASS", "mandatory": True},
        ],
    )

    assert decision.status is QualityStatus.FAR_FAIL
    assert "LOW_SHARPE_FAIL" in decision.reasons


def test_non_mandatory_platform_failure_blocks_ready() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    decision = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.8, "fitness": 1.1, "turnover": 0.2},
        checks=[
            {"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "FAIL"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
            {"name": "PROD_CORRELATION", "result": "PASS"},
        ],
    )

    assert decision.status is QualityStatus.FAR_FAIL
    assert "LOW_SUB_UNIVERSE_SHARPE_FAIL" in decision.reasons


def test_duplicate_platform_checks_use_worst_status() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    decision = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.8, "fitness": 1.1, "turnover": 0.2},
        checks=[
            {"name": "SELF_CORRELATION", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "FAIL"},
            {"name": "PROD_CORRELATION", "result": "PASS"},
        ],
    )

    assert decision.status is QualityStatus.FAR_FAIL
    assert "SELF_CORRELATION_FAIL" in decision.reasons


def test_confirmed_prod_corr_exception_does_not_waive_other_failures() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    decision = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.8, "fitness": 1.1, "turnover": 0.2},
        checks=[
            {"name": "PROD_CORRELATION", "result": "FAIL"},
            {"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "FAIL"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
        ],
        prod_corr_exception_confirmed=True,
    )

    assert decision.status is QualityStatus.FAR_FAIL
    assert "PROD_CORRELATION_FAIL" not in decision.reasons
    assert "LOW_SUB_UNIVERSE_SHARPE_FAIL" in decision.reasons


def test_production_correlation_alias_satisfies_required_gate() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    decision = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.70, "fitness": 1.05, "turnover": 0.25},
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
            {"name": "SELF_CORRELATION", "result": "PASS", "mandatory": True},
            {"name": "PRODUCTION_CORRELATION", "result": "PASS", "mandatory": True},
        ],
    )

    assert decision.status is QualityStatus.READY_TO_SUBMIT


def test_platform_checks_are_final_truth_while_explicit_local_thresholds_remain_tunable() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    near = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.35, "fitness": 1.05, "turnover": 0.25},
        checks=_checks(),
        live_thresholds={"sharpe": 1.57},
    )
    far = evaluate_quality(
        alpha_id="alpha-1",
        status="COMPLETE",
        metrics={"sharpe": 1.10, "fitness": 0.60, "turnover": 0.90},
        checks=[
            {"name": "LOW_SHARPE", "result": "FAIL"},
            {"name": "LOW_FITNESS", "result": "FAIL"},
            {"name": "HIGH_TURNOVER", "result": "FAIL"},
            *(_checks()),
        ],
    )

    assert near.status is QualityStatus.NEAR_PASS
    assert near.repairable is True
    assert far.status is QualityStatus.FAR_FAIL


def test_platform_pass_is_not_overridden_by_legacy_numeric_floor() -> None:
    from alpha_mining.quality.decision import QualityStatus, evaluate_quality

    decision = evaluate_quality(
        alpha_id="alpha-1", status="COMPLETE",
        metrics={"sharpe": 0.3, "fitness": 0.2, "turnover": 0.95},
        checks=_checks(),
    )

    assert decision.status is QualityStatus.READY_TO_SUBMIT


def test_migration_18_persists_quality_and_lineage_first_write_wins(tmp_path) -> None:
    database = tmp_path / "quality.sqlite"
    migrate(database)
    store = CandidateFeedbackStore(database)
    store.record(
        "request-1",
        "READY_TO_SUBMIT",
        quality_status="READY_TO_SUBMIT",
        quality_reasons=["all hard gates passed"],
        self_correlation="PASS",
        prod_correlation="PASS",
        knowledge_refs=["worldquant:operators#rank"],
        knowledge_usage_mode="LIVE_LLM_KNOWLEDGE",
        context_refs=["worldquant:operators#rank", "worldquant:operators#decay"],
        knowledge_context_hash="ctx-1",
        parent_candidate_id="parent-1",
        repair_action="decay_only",
        operator_topology="rank(ts_rank)",
        region="USA",
        universe_name="TOP3000",
        delay="1",
    )
    store.record("request-1", "FAR_FAIL", quality_status="FAR_FAIL")

    with sqlite3.connect(database) as con:
        row = con.execute(
            """SELECT outcome,quality_status,quality_reasons_json,self_correlation,
                      prod_correlation,knowledge_refs_json,parent_candidate_id,repair_action,
                      operator_topology,region,universe_name,delay,knowledge_usage_mode,
                      context_refs_json,knowledge_context_hash,degraded
               FROM candidate_outcomes WHERE request_hash='request-1'"""
        ).fetchone()

    assert row[0] == "READY_TO_SUBMIT"
    assert row[1] == "READY_TO_SUBMIT"
    assert "all hard gates passed" in row[2]
    assert row[3:] == (
        "PASS", "PASS", '["worldquant:operators#rank"]', "parent-1", "decay_only",
        "rank(ts_rank)", "USA", "TOP3000", "1", "LIVE_LLM_KNOWLEDGE",
        '["worldquant:operators#rank", "worldquant:operators#decay"]', "ctx-1", 0,
    )
