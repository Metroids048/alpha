from __future__ import annotations

from types import SimpleNamespace

from alpha_mining.generation.portfolio import (
    PortfolioLimits,
    feedback_penalty,
    select_candidates,
)
from alpha_mining.generation.high_quality import AcceptedCandidate, HighQualityGenerator
from alpha_mining.generation.snapshots import FeedbackRecord, FeedbackSummary


def _candidate(
    expression: str,
    *,
    parent: str = "parent-a",
    quality: float = 90.0,
    novelty: float = 0.8,
) -> SimpleNamespace:
    return SimpleNamespace(
        expression=expression,
        datasets=("fund",),
        parent_seed=parent,
        local_quality_score=quality,
        novelty_score=novelty,
    )


def _feedback(expression: str, failures: tuple[str, ...]) -> FeedbackRecord:
    return FeedbackRecord(
        ref_id=f"ref-{expression}-{len(failures)}",
        request_hash=f"request-{expression}-{len(failures)}",
        expression=expression,
        outcome="FAILED",
        family="",
        dataset="fund",
        failure_types=failures,
        self_corr_risk="SELF_CORRELATION" in failures,
        grounded=True,
    )


def test_shadow_preserves_legacy_order_but_records_enforce_decision() -> None:
    candidates = [
        _candidate("ts_rank(fund_a,63)", parent="p1", quality=99),
        _candidate("ts_rank(fund_b,63)", parent="p2", quality=80),
    ]
    result = select_candidates(
        candidates,
        inventory=(),
        feedback=FeedbackSummary((), (), (), (), (), {}),
        limit=1,
        mode="shadow",
    )

    assert result.accepted == (candidates[0],)
    decisions = {item["expression"]: item for item in result.decisions}
    assert decisions[candidates[1].expression]["reason"] == "PORTFOLIO_CYCLE_FIELD_SKELETON_LIMIT"
    assert result.rejection_counts == {"PORTFOLIO_SHADOW_WOULD_REJECT": 1}


def test_enforce_selects_diverse_candidate_and_respects_active_limits() -> None:
    candidates = [
        _candidate("ts_rank(fund_a,63)", parent="p1", quality=99),
        _candidate("ts_rank(fund_b,63)", parent="p2", quality=98),
        _candidate("ts_mean(fund_c,63)", parent="p3", quality=80),
    ]
    active = SimpleNamespace(
        request_hash="old",
        candidate_id="old",
        expression="ts_mean(fund_d,63)",
        queue_status="PENDING_SIMULATION",
        family="ts_mean",
        dataset="fund",
        field_skeleton="",
        research_direction="old-parent",
        exact_hash="",
        structure_signature="",
        behavior_signature="",
    )
    result = select_candidates(
        candidates,
        inventory=(active,),
        feedback=FeedbackSummary((), (), (), (), (), {}),
        limit=2,
        pending_limit=8,
        mode="enforce",
    )

    assert len(result.accepted) == 2
    assert candidates[1] not in result.accepted
    assert result.rejection_counts
    assert all(item["decision"] in {"ACCEPT", "REJECT"} for item in result.decisions)


def test_feedback_penalty_requires_two_matching_grounded_records() -> None:
    candidate = _candidate("ts_rank(fund_a,63)")
    one = FeedbackSummary(
        records=(_feedback("ts_rank(fund_a,63)", ("SELF_CORRELATION",)),),
        positive=(), near_pass=(), failures=(), self_corr_risk=(), failure_counts={"SELF_CORRELATION": 1},
    )
    two_records = (
        _feedback("ts_rank(fund_a,63)", ("SELF_CORRELATION",)),
        _feedback("ts_rank(fund_a,126)", ("LOW_SHARPE",)),
    )
    two = FeedbackSummary(
        records=two_records,
        positive=(), near_pass=(), failures=two_records, self_corr_risk=two_records,
        failure_counts={"SELF_CORRELATION": 1, "LOW_SHARPE": 1},
    )

    from alpha_mining.generation.portfolio import DiversityVector

    vector = DiversityVector.from_candidate(candidate)
    assert feedback_penalty(vector, one).score == 0
    penalty = feedback_penalty(vector, two)
    assert penalty.sample_count == 2
    assert penalty.score > 0
    assert dict(penalty.failure_counts)["SELF_CORRELATION"] == 1


def test_selection_is_deterministic_for_same_inputs() -> None:
    candidates = [
        _candidate("ts_mean(fund_b,63)", parent="p2", quality=90),
        _candidate("ts_rank(fund_a,63)", parent="p1", quality=90),
    ]
    kwargs = {
        "inventory": (),
        "feedback": FeedbackSummary((), (), (), (), (), {}),
        "limit": 1,
        "limits": PortfolioLimits(),
        "mode": "enforce",
    }
    first = select_candidates(candidates, **kwargs)
    second = select_candidates(candidates, **kwargs)
    assert [item.expression for item in first.accepted] == [item.expression for item in second.accepted]
    assert first.decisions == second.decisions
    assert first.inventory_hash == second.inventory_hash


def test_enforce_selection_attaches_auditable_evidence() -> None:
    candidate = AcceptedCandidate(
        expression="ts_rank(fund_a,63)",
        settings={},
        datasets=("fund",),
        parent_seed="parent-a",
        research_direction="quality",
        hypothesis="quality persists",
        economic_rationale="A slow fundamental signal captures persistent information diffusion.",
        knowledge_refs=("worldquant:test#1",),
        feedback_refs=(),
        anti_corr_design="A single grounded field avoids unsupported cross-dataset mixing.",
        expected_turnover_behavior="medium-low",
        local_quality_score=90.0,
        novelty_score=0.9,
        self_corr_risk_score=0.0,
        quality_evidence={"generator_contract_version": "generation-hq-v2"},
        generator_source="LLM_REFINED_V50",
    )
    snapshots = SimpleNamespace(
        inventory=SimpleNamespace(records=()),
        feedback=FeedbackSummary((), (), (), (), (), {}),
    )
    generator = HighQualityGenerator(llm=None, kernel=None, portfolio_mode="enforce")
    rejections: dict[str, int] = {}
    selected = generator._select_portfolio(
        [candidate], snapshots, candidates_per_cycle=1, rejections=rejections,
    )

    assert len(selected) == 1
    evidence = selected[0].quality_evidence["portfolio_selection"]
    assert evidence["mode"] == "enforce"
    assert evidence["policy_version"] == "portfolio-diversity-v1"
    assert evidence["inventory_hash"]
    assert evidence["decision"] == "ACCEPT"


def test_default_quality_threshold_allows_diverse_candidate_below_hard_similarity_gate() -> None:
    from alpha_mining.generation.high_quality import HighQualityGenerator

    snapshots = SimpleNamespace(
        catalog=SimpleNamespace(
            fields={
                "fund_a": SimpleNamespace(
                    coverage=1.0,
                    date_coverage=1.0,
                    user_count=0,
                )
            },
            info={"source": "local_offline_field_snapshot"},
        ),
        feedback=FeedbackSummary((), (), (), (), (), {}),
        catalog_source="test",
        catalog_age_hours=0.0,
    )
    generator = HighQualityGenerator(llm=None, kernel=None)

    score, _evidence = generator._quality_score(
        "ts_rank(fund_a,126)",
        ("fund_a",),
        {"worldquant:test#1"},
        set(),
        snapshots,
        max_similarity=0.30,
        mechanism_complete=True,
    )

    assert score >= generator._quality_threshold(snapshots)
