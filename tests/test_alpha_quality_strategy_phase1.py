from __future__ import annotations

from pathlib import Path


def test_baseline_first_emits_one_candidate_per_hypothesis() -> None:
    from alpha_mining.generator.baseline_first import BaselineFirstGenerator

    candidates = BaselineFirstGenerator().generate(
        hypothesis_id="h1", family="fundamental", fields=["revenue", "close"]
    )

    assert len(candidates) == 1
    assert candidates[0].stage == "baseline"
    assert candidates[0].hypothesis_id == "h1"


def test_baseline_outcome_uses_dynamic_near_pass_ratio() -> None:
    from alpha_mining.generator.baseline_first import BaselineOutcome, classify_baseline

    assert classify_baseline(sharpe=1.0, live_threshold=1.25) is BaselineOutcome.FAR_FAIL
    assert classify_baseline(sharpe=1.20, live_threshold=1.25) is BaselineOutcome.NEAR_PASS
    assert classify_baseline(sharpe=1.25, live_threshold=1.25) is BaselineOutcome.PASS


def test_near_pass_allows_only_four_ofat_settings() -> None:
    from alpha_mining.generator.baseline_first import BaselineFirstGenerator, BaselineOutcome

    base = {
        "delay": 1,
        "neutralization": "SUBINDUSTRY",
        "decay": 0,
        "truncation": 0.08,
        "nanHandling": "ON",
        "pasteurization": "ON",
    }
    trials = BaselineFirstGenerator().settings_trials(
        base, outcome=BaselineOutcome.NEAR_PASS, candidate_id="candidate-1"
    )

    assert len(trials) == 4
    assert {next(iter(trial.parameter_delta)) for trial in trials} == {
        "neutralization",
        "decay",
        "truncation",
        "nanHandling",
    }
    assert all(len(trial.parameter_delta) == 1 for trial in trials)


def test_research_identity_uses_six_semantic_dimensions_and_horizon_buckets() -> None:
    from alpha_mining.domain.research_identity import ResearchIdentity, normalize_holding_horizon

    assert normalize_holding_horizon(20) == normalize_holding_horizon(21)
    assert normalize_holding_horizon(21) != normalize_holding_horizon(63)
    left = ResearchIdentity(
        "earnings surprise", "fundamental", "filing", "peer-relative", normalize_holding_horizon(20), "industry"
    )
    right = ResearchIdentity(
        "earnings surprise", "fundamental", "filing", "peer-relative", normalize_holding_horizon(21), "industry"
    )

    assert left.identity_id({"decay": 0}) == right.identity_id({"decay": 8})


def test_cluster_requires_three_explicit_self_corr_failures_to_freeze() -> None:
    from alpha_mining.legacy.self_corr import cluster_disposition

    assert cluster_disposition(["FAIL"]) == "OBSERVE_ONLY"
    assert cluster_disposition(["FAIL", "FAIL"]) == "OBSERVE_ONLY"
    assert cluster_disposition(["FAIL", "FAIL", "FAIL"]) == "FROZEN"
    assert cluster_disposition(["FAIL", "FAIL", "FAIL", "PASS"]) == "SALVAGEABLE"


def test_round_quota_allows_one_full_check_per_behavior_cluster() -> None:
    from alpha_mining.scheduler.behavior_quota import BehaviorRoundQuota

    quota = BehaviorRoundQuota()
    assert quota.admit_full_check("cluster-1")
    assert not quota.admit_full_check("cluster-1")
    assert quota.admit_full_check("cluster-2")


def test_parent_priority_includes_prod_corr_before_quality() -> None:
    from alpha_mining.scheduler.parent_priority import rank_parents

    rows = [
        {"id": "self-only", "self_corr_status": "PASS", "prod_corr_status": "FAIL", "quality": 99},
        {"id": "both-pass", "self_corr_status": "PASS", "prod_corr_status": "PASS", "quality": 1},
        {"id": "self-fail", "self_corr_status": "FAIL", "prod_corr_status": "PASS", "quality": 999},
    ]

    assert [row["id"] for row in rank_parents(rows)] == ["both-pass", "self-only", "self-fail"]


def test_arm_is_downweighted_after_three_evidence_windows(tmp_path: Path) -> None:
    from alpha_mining.scheduler.arm_metrics import ArmDimensions, ResearchArmTracker
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "arms.sqlite"
    migrate(database)
    tracker = ResearchArmTracker(database)
    arm = ArmDimensions(
        family="fundamental",
        dataset="fundamental6",
        field_family="profitability",
        mechanism="surprise",
        operator_topology="group_rank>ts_delta",
        region="USA",
        universe="TOP3000",
        delay="1",
    )
    for _ in range(3):
        tracker.record_window(
            arm,
            sharpes=[0.1] * 20,
            base_passes=[False] * 20,
            near_passes=[False] * 20,
            self_corr_passes=0,
            prod_corr_passes=0,
            final_submits=0,
        )

    stats = tracker.stats(arm)
    assert stats.simulation_count == 60
    assert stats.consecutive_low_windows == 3
    assert stats.sampling_weight == 0.1
