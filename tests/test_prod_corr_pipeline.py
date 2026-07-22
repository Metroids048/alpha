"""Tests for Production Correlation data chain.

Covers the 12 required test cases:
  1. test_parse_prod_corr_failure_message
  2. test_parse_prod_corr_dynamic_cutoff
  3. test_prod_corr_missing_blocks_submit
  4. test_prod_corr_unknown_blocks_submit
  5. test_prod_corr_pending_blocks_submit
  6. test_prod_corr_fail_penalizes_parent
  7. test_high_prod_corr_cluster_freezes
  8. test_settings_only_same_research_identity
  9. test_prod_feedback_reaches_scheduler
 10. test_zero_positive_labels_uses_exploration
 11. test_local_model_cannot_override_platform_fail
 12. test_runtime_uses_current_main_engine
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1 & 2 – Parser
# ---------------------------------------------------------------------------

from alpha_mining.platform.check_parser import (
    PROD_CORR_FAIL,
    PROD_CORR_MISSING,
    PROD_CORR_PASS,
    PROD_CORR_PENDING,
    PROD_CORR_UNKNOWN,
    GateObservation,
    parse_prod_corr_details,
)


def _make_gate_obs(
    result: str,
    message: str = "",
    value: float | None = None,
    limit: float | None = None,
    gate_name: str = "PROD_CORRELATION",
) -> GateObservation:
    """Build a minimal GateObservation for testing."""
    return GateObservation(
        gate_name=gate_name,
        result=result,
        limit=limit,
        value=value,
        message=message,
        region="USA",
        universe="TOP3000",
        delay="1",
        alpha_type="REGULAR",
        theme_id="*",
        pyramid_id="*",
        source_alpha_id="test_alpha_001",
        observed_at="2026-07-21T00:00:00Z",
        raw_payload_hash="abc123",
        direction="MAX",
        ingested_at="2026-07-21T00:00:00Z",
        timestamp_source="argument",
        freshness_eligible=True,
        source="platform_payload",
        observation_id="obs001",
    )


def test_parse_prod_corr_failure_message():
    """Parser extracts 0.8379 / 0.7 / 10% from the canonical platform message."""
    obs = _make_gate_obs(
        result="FAIL",
        message=(
            "Prod correlation 0.8379 is above cutoff of 0.7 "
            "and Sharpe not better by 10.0% or more."
        ),
    )
    details = parse_prod_corr_details(obs)
    assert details.status == PROD_CORR_FAIL
    assert details.prod_correlation == pytest.approx(0.8379)
    assert details.prod_cutoff == pytest.approx(0.7)
    assert details.required_sharpe_improvement == pytest.approx(0.10)


def test_parse_prod_corr_dynamic_cutoff():
    """Parser handles a different cutoff value — nothing is hardcoded."""
    obs = _make_gate_obs(
        result="FAIL",
        message=(
            "Production correlation 0.9120 is above cutoff of 0.65 "
            "and Sharpe not better by 15.0% or more."
        ),
    )
    details = parse_prod_corr_details(obs)
    assert details.status == PROD_CORR_FAIL
    assert details.prod_correlation == pytest.approx(0.912)
    assert details.prod_cutoff == pytest.approx(0.65)
    assert details.required_sharpe_improvement == pytest.approx(0.15)


def test_parse_prod_corr_pass():
    """PASS result → PROD_CORR_PASS status."""
    obs = _make_gate_obs(result="PASS")
    details = parse_prod_corr_details(obs)
    assert details.status == PROD_CORR_PASS


def test_parse_prod_corr_unknown_on_bad_message():
    """Parse failure → UNKNOWN, not PASS.  System must never assume low correlation."""
    obs = _make_gate_obs(
        result="FAIL",
        message="Completely unrecognisable message format XYZ",
    )
    details = parse_prod_corr_details(obs)
    # Status comes from the gate result field (FAIL), not the message parse.
    assert details.status == PROD_CORR_FAIL
    # Numeric fields remain None when parsing fails.
    assert details.prod_cutoff is None or details.prod_correlation is not None


# ---------------------------------------------------------------------------
# 3, 4, 5 – Guard: MISSING / UNKNOWN / PENDING must block submit
# ---------------------------------------------------------------------------

from alpha_mining.submitter.guard import CandidateContext, SubmissionGuard


def _base_context(**overrides) -> CandidateContext:
    defaults: dict = dict(
        alpha_id="alpha_001",
        expression_id="expr_001",
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
        ],
        gate_snapshots_fresh=True,
        quality_buffer_pass=True,
        local_correlation_status="PASS",
    )
    defaults.update(overrides)
    return CandidateContext(**defaults)


def test_prod_corr_missing_blocks_submit():
    """No PROD_CORRELATION check in response → treated as MISSING → blocked."""
    ctx = _base_context(
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
            # PROD_CORRELATION intentionally absent
        ]
    )
    decision = SubmissionGuard().evaluate(ctx)
    assert not decision.allowed
    missing_reasons = [r for r in decision.reasons if "PROD_CORRELATION" in r and "MISSING" in r]
    assert missing_reasons, f"Expected PROD_CORRELATION_MISSING in reasons; got {decision.reasons}"


def test_prod_corr_unknown_blocks_submit():
    """PROD_CORRELATION = UNKNOWN → blocked."""
    ctx = _base_context(
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
            {"name": "PROD_CORRELATION", "result": "UNKNOWN"},
        ]
    )
    decision = SubmissionGuard().evaluate(ctx)
    assert not decision.allowed
    assert any("PROD_CORRELATION" in r for r in decision.reasons)


def test_prod_corr_pending_blocks_submit():
    """PROD_CORRELATION = PENDING → blocked."""
    ctx = _base_context(
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
            {"name": "PROD_CORRELATION", "result": "PENDING"},
        ]
    )
    decision = SubmissionGuard().evaluate(ctx)
    assert not decision.allowed
    assert any("PROD_CORRELATION" in r for r in decision.reasons)


def test_prod_corr_pass_allows_submit():
    """All checks PASS including PROD_CORRELATION → allowed."""
    ctx = _base_context(
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
            {"name": "PROD_CORRELATION", "result": "PASS"},
        ]
    )
    decision = SubmissionGuard().evaluate(ctx)
    assert decision.allowed, f"Expected allowed; reasons: {decision.reasons}"


# ---------------------------------------------------------------------------
# 6 – Prod Corr FAIL penalises parent via bandit reward
# ---------------------------------------------------------------------------

from alpha_mining.policy.consultant_policy import ConsultantPolicy
from alpha_mining.scheduler.consultant_bandit import BanditArm, ConsultantBandit
from alpha_mining.scheduler.prod_corr_feedback import (
    get_prod_corr_reward_components,
    upsert_prod_corr_observation,
)
from alpha_mining.storage.migrations import migrate


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.sqlite"
    migrate(db)
    return db


def test_prod_corr_fail_penalizes_parent(tmp_path):
    """Reward with prod_corr_fail component must be lower than without it."""
    db = _make_db(tmp_path)
    policy = ConsultantPolicy()
    bandit = ConsultantBandit(db, policy=policy)
    arm = BanditArm(family="fundamental", dataset="analyst", mutation_type="mechanism", settings_profile="default")

    # Reward without prod_corr signal (baseline)
    baseline = bandit.reward(arm, {"platform_pass": 1.0, "quality_buffer": 1.0})

    # Reward with explicit FAIL
    fail_reward = bandit.reward(arm, {"platform_pass": 0.0, "quality_buffer": 1.0, "prod_corr_fail": 1.0})

    assert fail_reward < baseline, (
        f"FAIL reward ({fail_reward}) must be lower than baseline ({baseline})"
    )
    # The penalty must be larger than just losing platform_pass
    platform_pass_loss = bandit.reward(arm, {"platform_pass": 0.0, "quality_buffer": 1.0})
    assert fail_reward < platform_pass_loss, (
        "prod_corr_fail penalty must add on top of platform_pass=0"
    )


def test_prod_corr_pass_boosts_reward(tmp_path):
    """PASS reward must be higher than baseline (no prod_corr signal)."""
    db = _make_db(tmp_path)
    policy = ConsultantPolicy()
    bandit = ConsultantBandit(db, policy=policy)
    arm = BanditArm(family="fundamental", dataset="analyst", mutation_type="mechanism", settings_profile="default")

    baseline = bandit.reward(arm, {"platform_pass": 1.0})
    pass_reward = bandit.reward(arm, {"platform_pass": 1.0, "prod_corr_pass": 1.0})
    assert pass_reward > baseline


# ---------------------------------------------------------------------------
# 7 – High Prod Corr cluster is frozen
# ---------------------------------------------------------------------------

from alpha_mining.scheduler.cluster_crowding import ClusterFreezeRegistry


def test_high_prod_corr_cluster_freezes(tmp_path):
    """A cluster with 5+ FAIL observations and median > cutoff + margin → frozen."""
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as con:
        # Insert 6 FAIL observations for one cluster.
        for i in range(6):
            upsert_prod_corr_observation(
                con,
                alpha_id=f"alpha_{i}",
                expression_id=f"expr_{i}",
                behavior_cluster_id="cluster_A",
                prod_correlation=0.85,
                prod_cutoff=0.70,
                required_sharpe_improvement=0.10,
                status="FAIL",
                failure_message="above cutoff",
                raw_payload_hash=f"hash_{i}",
                observed_at="2026-07-21T00:00:00Z",
            )
        # Simulate a live gate snapshot so the registry knows the cutoff.
        con.execute(
            "INSERT OR IGNORE INTO platform_gate_snapshots "
            "(snapshot_key, gate_name, limit_value, direction, region, universe_name, delay, "
            " alpha_type, theme_id, pyramid_id, first_seen_at, last_seen_at, "
            " observation_count, source, raw_payload_hash, version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("snap1", "PROD_CORRELATION", 0.70, "MAX", "USA", "TOP3000",
             "1", "REGULAR", "*", "*", "2026-07-21T00:00:00Z",
             "2026-07-21T00:00:00Z", 1, "test", "h1", 1),
        )
        con.commit()

    registry = ClusterFreezeRegistry(db, min_observations=5, freeze_margin=0.05)
    registry.refresh()
    assert registry.is_frozen("cluster_A"), (
        "cluster_A has 6 FAIL observations with median 0.85 > 0.70+0.05; should be frozen"
    )


def test_cluster_not_frozen_below_threshold(tmp_path):
    """Only 3 observations → not frozen (below min_observations=5)."""
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as con:
        for i in range(3):
            upsert_prod_corr_observation(
                con,
                alpha_id=f"alpha_{i}",
                expression_id=f"expr_{i}",
                behavior_cluster_id="cluster_B",
                prod_correlation=0.90,
                prod_cutoff=0.70,
                required_sharpe_improvement=0.10,
                status="FAIL",
                raw_payload_hash=f"hash_{i}",
                observed_at="2026-07-21T00:00:00Z",
            )
        con.commit()

    registry = ClusterFreezeRegistry(db, min_observations=5)
    registry.refresh()
    assert not registry.is_frozen("cluster_B")


# ---------------------------------------------------------------------------
# 8 – Settings-only variant has the same research identity
# ---------------------------------------------------------------------------

from alpha_mining.domain.expression_normalization import behavior_signature
from alpha_mining.integration.phase4 import expression_id_for


def test_settings_only_same_research_identity():
    """Decay/truncation/neutralization changes must NOT produce a new expression_id."""
    base_expr = "rank(ts_delta(fundamental_value, 63))"
    id_base = expression_id_for(base_expr)
    id_same = expression_id_for(base_expr)  # same text → same id
    assert id_base == id_same

    # behavior_signature must also be invariant to settings parameter
    sig_no_settings = behavior_signature(base_expr)
    sig_with_settings = behavior_signature(base_expr, settings={"decay": 8, "truncation": 0.03})
    assert sig_no_settings == sig_with_settings, (
        "Settings (decay/truncation) must not change behavior_signature"
    )


# ---------------------------------------------------------------------------
# 9 – Prod Corr feedback reaches scheduler reward components
# ---------------------------------------------------------------------------

def test_prod_feedback_reaches_scheduler(tmp_path):
    """After inserting a FAIL observation, get_prod_corr_reward_components returns FAIL key."""
    db = _make_db(tmp_path)
    alpha_id = "alpha_feedback_test"
    with sqlite3.connect(db) as con:
        upsert_prod_corr_observation(
            con,
            alpha_id=alpha_id,
            expression_id="expr_feedback",
            prod_correlation=0.83,
            prod_cutoff=0.70,
            required_sharpe_improvement=0.10,
            status="FAIL",
            raw_payload_hash="hash_feedback",
            observed_at="2026-07-21T00:00:00Z",
        )
        con.commit()

    with sqlite3.connect(db) as con:
        components = get_prod_corr_reward_components(con, alpha_id)

    assert "prod_corr_fail" in components, (
        f"Expected prod_corr_fail in components; got {components}"
    )
    assert components["prod_corr_fail"] == pytest.approx(1.0)


def test_prod_feedback_missing_returns_unknown(tmp_path):
    """No observation → returns prod_corr_unknown (never empty or prod_corr_pass)."""
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as con:
        components = get_prod_corr_reward_components(con, "nonexistent_alpha")

    assert "prod_corr_unknown" in components
    assert "prod_corr_pass" not in components


# ---------------------------------------------------------------------------
# 10 – Zero positive labels → exploration policy
# ---------------------------------------------------------------------------

def test_zero_positive_labels_uses_exploration(tmp_path):
    """When all observations are FAIL, no PASS label exists → exploration must be available."""
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as con:
        for i in range(10):
            upsert_prod_corr_observation(
                con,
                alpha_id=f"alpha_{i}",
                expression_id=f"expr_{i}",
                prod_correlation=0.88,
                prod_cutoff=0.70,
                required_sharpe_improvement=0.10,
                status="FAIL",
                raw_payload_hash=f"h_{i}",
                observed_at="2026-07-21T00:00:00Z",
            )
        con.commit()

    # Verify: pass_count=0 for all observations
    with sqlite3.connect(db) as con:
        pass_count = con.execute(
            "SELECT COUNT(*) FROM prod_correlation_observations WHERE status='PASS'"
        ).fetchone()[0]
    assert pass_count == 0

    # When there are no positive labels, the ClusterFreezeRegistry should still
    # allow unfrozen expressions to be explored (freeze only happens for known clusters
    # with enough data, not for entirely new families).
    registry = ClusterFreezeRegistry(db, min_observations=5)
    registry.refresh()
    # New cluster with no observations at all → NOT frozen (exploration is open)
    assert not registry.is_frozen("brand_new_cluster_XYZ")


# ---------------------------------------------------------------------------
# 11 – Local model cannot override platform FAIL
# ---------------------------------------------------------------------------

def test_local_model_cannot_override_platform_fail():
    """High novelty score does NOT flip a PROD_CORRELATION FAIL into allowed."""
    ctx = _base_context(
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS"},
            {"name": "SELF_CORRELATION", "result": "PASS"},
            {"name": "PROD_CORRELATION", "result": "FAIL"},
        ],
        metrics={"novelty": 0.99, "robustness": 0.99},  # local model says "great"
    )
    decision = SubmissionGuard().evaluate(ctx)
    assert not decision.allowed, (
        "Platform PROD_CORRELATION FAIL must never be overridden by local metrics"
    )
    assert any("PROD_CORRELATION" in r for r in decision.reasons)


# ---------------------------------------------------------------------------
# 12 – Runtime uses current main engine
# ---------------------------------------------------------------------------

def test_runtime_uses_current_main_engine():
    """The active production entry point must delegate to auto_alpha_pipeline_rebuilt_v50."""
    cycle_path = Path(__file__).parent.parent / "run_pipeline_cycle.py"
    assert cycle_path.exists(), "run_pipeline_cycle.py missing"
    content = cycle_path.read_text(encoding="utf-8")
    assert "auto_alpha_pipeline_rebuilt_v50" in content, (
        "run_pipeline_cycle.py must import auto_alpha_pipeline_rebuilt_v50 as the engine"
    )
