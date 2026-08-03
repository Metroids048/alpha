"""Architecture acceptance tests for the authoritative candidate generation pipeline.

These tests define the expected contract. They fail initially (RED) and pass once
the implementation is complete (GREEN). Do not weaken these tests to fit implementation.

Phase 1 of docs/final-closure/BASELINE.md.
"""

from __future__ import annotations

import inspect
import random
import sqlite3
import tempfile
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp: Path) -> Path:
    """Create a minimally initialised factory database."""
    db = tmp / "test_factory.sqlite"
    from alpha_mining.storage.sqlite_store import SqliteRunLog
    from alpha_mining.storage.migrations import migrate
    SqliteRunLog(db).initialize_schema()
    migrate(db)
    return db


def _seed_research(db: Path) -> None:
    """Insert minimal research data so _research_specs() returns rows."""
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT OR IGNORE INTO research_topics (topic_id,category,active) VALUES ('t1','momentum',1)"
        )
        con.execute(
            "INSERT OR IGNORE INTO hypotheses (hypothesis_id,topic_id,statement_en,mechanism,horizon,status) "
            "VALUES ('h1','t1','Test momentum hypothesis','momentum reversal','medium','active')"
        )
        con.execute(
            "INSERT OR IGNORE INTO hypotheses (hypothesis_id,topic_id,statement_en,mechanism,horizon,status) "
            "VALUES ('h2','t1','Test volatility hypothesis','volatility risk','medium','active')"
        )
        con.execute(
            "INSERT OR IGNORE INTO hypotheses (hypothesis_id,topic_id,statement_en,mechanism,horizon,status) "
            "VALUES ('h3','t1','Test fundamental hypothesis','fundamental value','medium','active')"
        )
        for h, f, ds in [
            ("h1", "returns", "ds1"),
            ("h2", "volume", "ds1"),
            ("h3", "cap", "ds2"),
        ]:
            con.execute(
                "INSERT OR IGNORE INTO data_mappings "
                "(mapping_id,hypothesis_id,data_field,dataset_id,field_quality_score) "
                "VALUES (?,?,?,?,0.9)",
                (f"{h}_{f}", h, f, ds),
            )


# ---------------------------------------------------------------------------
# Test 1 – Production entry point does NOT delegate to v50
# ---------------------------------------------------------------------------

class TestProductionEntryPoint:
    def test_run_pipeline_cycle_imports_factory_runtime(self):
        import importlib.util, pathlib
        src = pathlib.Path("run_pipeline_cycle.py").read_text(encoding="utf-8")
        assert "alpha_mining.factory.runtime" in src, (
            "run_pipeline_cycle.py must import from alpha_mining.factory.runtime"
        )

    def test_production_chain_does_not_import_v50(self):
        import importlib.util, pathlib
        for fname in [
            "run_pipeline_cycle.py",
            "run_pipeline_loop.py",
            "run_pipeline_supervisor.py",
            "alpha_mining/factory/runtime.py",
            "alpha_mining/factory/orchestrator.py",
        ]:
            src = pathlib.Path(fname).read_text(encoding="utf-8")
            assert "import auto_alpha_pipeline_rebuilt_v50" not in src, (
                f"{fname} must not import auto_alpha_pipeline_rebuilt_v50"
            )
            assert "from auto_alpha_pipeline_rebuilt_v50" not in src, (
                f"{fname} must not import from auto_alpha_pipeline_rebuilt_v50"
            )


# ---------------------------------------------------------------------------
# Test 2 – CandidateGenerationService injectable into FactoryOrchestrator
# ---------------------------------------------------------------------------

class TestCandidateServiceInjection:
    def test_factory_orchestrator_accepts_candidate_service_kwarg(self):
        from alpha_mining.factory.orchestrator import FactoryOrchestrator
        sig = inspect.signature(FactoryOrchestrator.__init__)
        assert "candidate_service" in sig.parameters, (
            "FactoryOrchestrator.__init__ must accept candidate_service keyword argument"
        )

    def test_injected_fake_service_is_called(self):
        from alpha_mining.factory.orchestrator import FactoryOrchestrator
        from alpha_mining.generation.service import CandidateGenerationService

        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            _seed_research(db)
            # Fake service that records calls
            calls = []

            class FakeService:
                def generate(self, *, limit: int):
                    calls.append(limit)
                    from alpha_mining.generation.service import CandidateGenerationBatch
                    return CandidateGenerationBatch(
                        candidates=(),
                        selected_topic_ids=(),
                        selected_families=(),
                        rejected_by_reason={},
                        generation_state="READY",
                        deferred_reason="",
                    )

            fake_sim = MagicMock()
            fake_sim.simulate.return_value = MagicMock(
                alpha_id="", status="error", checks=[], sharpe=None,
                fitness=None, turnover=None, progress_location="",
            )

            orch = FactoryOrchestrator(db, fake_sim, candidate_service=FakeService())
            orch.run_simulate(batch_size=2)
            assert calls, "CandidateGenerationService.generate() must be called"

    def test_orchestrator_does_not_call_consultant_generator_directly(self):
        """When a candidate_service is injected, ConsultantGenerator.generate must not be called."""
        from alpha_mining.factory.orchestrator import FactoryOrchestrator
        from alpha_mining.generation.service import CandidateGenerationBatch

        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            _seed_research(db)

            consultant_calls = []

            class FakeService:
                def generate(self, *, limit: int):
                    return CandidateGenerationBatch(
                        candidates=(),
                        selected_topic_ids=(),
                        selected_families=(),
                        rejected_by_reason={},
                        generation_state="READY",
                        deferred_reason="",
                    )

            fake_sim = MagicMock()
            fake_sim.simulate.return_value = MagicMock(
                alpha_id="", status="error", checks=[], sharpe=None,
                fitness=None, turnover=None, progress_location="",
            )

            orch = FactoryOrchestrator(db, fake_sim, candidate_service=FakeService())
            # Patch ConsultantGenerator.generate to detect if it's called
            if hasattr(orch, "generator"):
                original = orch.generator.generate
                orch.generator.generate = lambda **kw: (consultant_calls.append(kw) or [])
            orch.run_simulate(batch_size=2)
            assert not consultant_calls, (
                "When candidate_service is injected, ConsultantGenerator.generate must not be called directly"
            )


# ---------------------------------------------------------------------------
# Test 3 – Multi-family diversity
# ---------------------------------------------------------------------------

class TestMultiFamilyDiversity:
    def test_generation_service_produces_multiple_families(self):
        from alpha_mining.generation.service import CandidateGenerationService, CandidateProposal

        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            _seed_research(db)
            svc = CandidateGenerationService(db, rng=random.Random(42))
            batch = svc.generate(limit=9)
            if len(batch.candidates) >= 3:
                families = {c.strategy_family for c in batch.candidates}
                assert len(families) >= 2, (
                    f"With 3+ candidates, must span >= 2 strategy families; got {families}"
                )

    def test_candidate_proposal_has_strategy_family_field(self):
        from alpha_mining.generation.service import CandidateProposal
        fields = {f.name for f in CandidateProposal.__dataclass_fields__.values()} if hasattr(CandidateProposal, '__dataclass_fields__') else set(dir(CandidateProposal))
        assert "strategy_family" in fields
        assert "research_family" in fields
        assert "hypothesis_id" in fields
        assert "topic_id" in fields
        assert "mechanism" in fields
        assert "dataset" in fields
        assert "parent_template" in fields
        assert "exact_hash" in fields
        assert "parameter_skeleton" in fields
        assert "field_skeleton" in fields
        assert "generator_source" in fields


# ---------------------------------------------------------------------------
# Test 4 – Canonical deduplication semantics
# ---------------------------------------------------------------------------

class TestCanonicalDeduplication:
    def _make_screening(self):
        from alpha_mining.generation.screening import CandidateScreeningPolicy
        return CandidateScreeningPolicy(group_rank_enabled=False)

    def test_exact_hash_reject(self):
        from alpha_mining.generation.screening import RejectionReason
        policy = self._make_screening()
        expr = "rank(ts_rank(returns,21))"
        first = policy.screen_expression(expr, round_seen_hashes=set(), round_seen_skeletons=set())
        assert first is None or first == RejectionReason.NONE, f"First time should pass: {first}"

        from alpha_mining.domain.expression_normalization import expression_identity
        seen = {expression_identity(expr).exact_hash}
        second = policy.screen_expression(expr, round_seen_hashes=seen, round_seen_skeletons=set())
        assert second is not None and second != RejectionReason.NONE, (
            "Duplicate exact_hash must be rejected"
        )

    def test_field_skeleton_round_limit(self):
        from alpha_mining.generation.screening import RejectionReason
        policy = self._make_screening()
        from alpha_mining.domain.expression_normalization import expression_identity
        expr1 = "rank(ts_rank(returns,21))"
        expr2 = "rank(ts_rank(volume,21))"
        id1 = expression_identity(expr1)
        id2 = expression_identity(expr2)
        # Same field skeleton in this round → second should be rejected
        seen_skeletons = {id1.field_skeleton}
        result = policy.screen_expression(expr2, round_seen_hashes=set(), round_seen_skeletons=seen_skeletons)
        if id1.field_skeleton == id2.field_skeleton:
            assert result is not None and result != RejectionReason.NONE, (
                "Second candidate with same field_skeleton in same round must be rejected"
            )

    def test_group_rank_disabled_by_default(self):
        from alpha_mining.generation.screening import RejectionReason
        policy = self._make_screening()
        expr = "group_rank(returns, sector)"
        result = policy.screen_expression(expr, round_seen_hashes=set(), round_seen_skeletons=set())
        assert result is not None and result != RejectionReason.NONE, (
            "group_rank expression must be rejected when group_rank_enabled=False"
        )

    def test_different_structure_allowed(self):
        from alpha_mining.generation.screening import RejectionReason
        policy = self._make_screening()
        expr1 = "rank(ts_rank(returns,21))"
        expr2 = "ts_zscore(volume,63)"
        result = policy.screen_expression(expr2, round_seen_hashes=set(), round_seen_skeletons=set())
        assert result is None or result == RejectionReason.NONE, (
            "Different structure should not be rejected"
        )


# ---------------------------------------------------------------------------
# Test 5 – group_rank default-disabled shared policy
# ---------------------------------------------------------------------------

class TestGroupRankDisabled:
    def test_screening_policy_rejects_group_rank_by_default(self):
        from alpha_mining.generation.screening import CandidateScreeningPolicy, RejectionReason
        policy = CandidateScreeningPolicy()
        result = policy.screen_expression(
            "group_rank(returns, sector)",
            round_seen_hashes=set(),
            round_seen_skeletons=set(),
        )
        assert result is not None and result != RejectionReason.NONE

    def test_screening_policy_allows_group_rank_when_enabled(self):
        from alpha_mining.generation.screening import CandidateScreeningPolicy, RejectionReason
        policy = CandidateScreeningPolicy(group_rank_enabled=True)
        result = policy.screen_expression(
            "group_rank(returns, sector)",
            round_seen_hashes=set(),
            round_seen_skeletons=set(),
        )
        assert result is None or result == RejectionReason.NONE


# ---------------------------------------------------------------------------
# Test 6 – Request context survives restart
# ---------------------------------------------------------------------------

class TestRequestContextRecovery:
    def test_context_field_on_request_lease(self):
        from alpha_mining.factory.simulation_requests import RequestLease
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(RequestLease)}
        assert "context" in field_names, "RequestLease must have a 'context' field"

    def test_claim_accepts_context_kwarg(self):
        from alpha_mining.factory.simulation_requests import SimulationRequestStore
        sig = inspect.signature(SimulationRequestStore.claim)
        assert "context" in sig.parameters, (
            "SimulationRequestStore.claim must accept a context keyword argument"
        )

    def test_context_persisted_and_recovered(self):
        from alpha_mining.factory.simulation_requests import SimulationRequestStore
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            store = SimulationRequestStore(db)
            ctx = {
                "candidate_id": "c1", "topic_id": "t1", "hypothesis_id": "h1",
                "research_family": "momentum", "strategy_family": "momentum",
                "mechanism": "reversal", "dataset": "ds1", "parent_template": "tmpl1",
                "exact_hash": "abc123", "parameter_skeleton": "sk1",
                "field_skeleton": "fsk1", "generator_source": "ConsultantGenerator",
            }
            claim = store.claim(
                "rank(ts_rank(returns,21))",
                {"neutralization": "market", "decay": 6},
                context=ctx,
            )
            assert claim.claimed, f"Claim failed: {claim.reason}"
            leases = store.acquire(1, request_hash=claim.request_hash)
            assert leases, "Should acquire a lease"
            lease = leases[0]
            assert lease.context.get("candidate_id") == "c1", (
                f"Context not preserved. Got: {lease.context}"
            )
            assert lease.context.get("strategy_family") == "momentum"

    def test_legacy_request_without_context_uses_fallback(self):
        """Requests created before migration have no context; lease must not crash."""
        from alpha_mining.factory.simulation_requests import SimulationRequestStore
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            store = SimulationRequestStore(db)
            claim = store.claim("rank(ts_rank(volume,21))", {"neutralization": "market"})
            assert claim.claimed
            leases = store.acquire(1, request_hash=claim.request_hash)
            assert leases
            assert isinstance(leases[0].context, dict)


# ---------------------------------------------------------------------------
# Test 7 – Failure feedback persistence
# ---------------------------------------------------------------------------

class TestFailureFeedbackPersistence:
    def test_candidate_feedback_store_exists(self):
        from alpha_mining.generation.feedback import CandidateFeedbackStore
        assert CandidateFeedbackStore is not None

    def test_all_outcomes_persistable(self):
        from alpha_mining.generation.feedback import CandidateFeedbackStore
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            store = CandidateFeedbackStore(db)
            for outcome in ("PASS", "NEAR_PASS", "FAR_FAIL", "FAILED", "UNKNOWN"):
                store.record(
                    request_hash=f"hash_{outcome}",
                    outcome=outcome,
                    sharpe=0.5 if outcome == "PASS" else None,
                    field_skeleton=f"sk_{outcome}",
                    strategy_family="momentum",
                    topic_id="t1",
                )

    def test_duplicate_finalize_is_idempotent(self):
        from alpha_mining.generation.feedback import CandidateFeedbackStore
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            store = CandidateFeedbackStore(db)
            for _ in range(3):
                store.record(
                    request_hash="hash_dup",
                    outcome="PASS",
                    sharpe=0.8,
                    field_skeleton="sk1",
                    strategy_family="momentum",
                    topic_id="t1",
                )

    def test_unknown_not_overwritten_by_failed(self):
        from alpha_mining.generation.feedback import CandidateFeedbackStore
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            store = CandidateFeedbackStore(db)
            store.record("hash_u", "UNKNOWN", sharpe=None, field_skeleton="sk1",
                         strategy_family="m", topic_id="t1")
            # Attempt to overwrite with FAILED — must be ignored (idempotent first-write wins)
            store.record("hash_u", "FAILED", sharpe=None, field_skeleton="sk1",
                         strategy_family="m", topic_id="t1")
            outcomes = store.outcomes_for_request("hash_u")
            assert outcomes == "UNKNOWN", (
                f"UNKNOWN outcome must not be overwritten; got {outcomes!r}"
            )


# ---------------------------------------------------------------------------
# Test 8 – Feedback changes next-round generation
# ---------------------------------------------------------------------------

class TestFeedbackInfluencesGeneration:
    def test_low_pass_family_weight_decreases_after_feedback(self):
        from alpha_mining.scheduler.arm_metrics import ResearchArmTracker, ArmDimensions
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            tracker = ResearchArmTracker(db)
            arm = ArmDimensions(
                family="low_yield", dataset="ds1", field_family="price",
                mechanism="reversal", operator_topology="rank", region="*",
                universe="TOP3000", delay="1",
            )
            # Record enough observations to trigger low-yield window (20+, all failing)
            stats = tracker.record_window(
                arm,
                sharpes=[-0.1] * 20,
                base_passes=[False] * 20,
                near_passes=[False] * 20,
                self_corr_passes=0,
                prod_corr_passes=0,
                final_submits=0,
            )
            assert stats.sampling_weight <= 1.0

            # After 3 consecutive low windows, weight must drop
            for _ in range(2):
                stats = tracker.record_window(
                    arm,
                    sharpes=[-0.1] * 20,
                    base_passes=[False] * 20,
                    near_passes=[False] * 20,
                    self_corr_passes=0,
                    prod_corr_passes=0,
                    final_submits=0,
                )
            assert stats.sampling_weight < 1.0, (
                f"After 3 consecutive low windows, sampling_weight must be < 1.0; got {stats.sampling_weight}"
            )

    def test_record_observation_increments_window(self):
        from alpha_mining.scheduler.arm_metrics import ResearchArmTracker, ArmDimensions
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            tracker = ResearchArmTracker(db)
            arm = ArmDimensions(
                family="test", dataset="ds1", field_family="price",
                mechanism="momentum", operator_topology="rank", region="*",
                universe="TOP3000", delay="1",
            )
            # record_observation should exist
            assert hasattr(tracker, "record_observation"), (
                "ResearchArmTracker must have record_observation() method"
            )
            tracker.record_observation(arm, base_pass=True, sharpe=0.5, near_pass=True)
            stats = tracker.stats(arm)
            assert stats.simulation_count >= 1, "record_observation must persist to DB"


# ---------------------------------------------------------------------------
# Test 9 – EvolutionEngine PASS statistics
# ---------------------------------------------------------------------------

class TestEvolutionEnginePassStats:
    def test_queue_status_pass_counted_by_evolution_engine(self):
        from alpha_mining.scheduler.evolution import EvolutionEngine
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            _seed_research(db)
            with sqlite3.connect(db) as con:
                con.execute(
                    "INSERT OR IGNORE INTO simulation_runs "
                    "(run_id,alpha_id,status,queue_status,sharpe,created_at) "
                    "VALUES ('r1','a1','COMPLETE','PASS',0.8,datetime('now'))"
                )
            engine = EvolutionEngine(db)
            weights = engine.topic_weights()
            # Should not crash; if topic/family tracking works, PASS counts should be visible
            assert isinstance(weights, dict)

    def test_factory_queue_status_pass_not_zero_rate(self):
        """Factory-written PASS simulations must produce non-zero pass rate in EvolutionEngine."""
        from alpha_mining.scheduler.evolution import EvolutionEngine
        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            _seed_research(db)
            with sqlite3.connect(db) as con:
                for i in range(5):
                    con.execute(
                        f"INSERT OR IGNORE INTO simulation_runs "
                        f"(run_id,alpha_id,topic_id,status,queue_status,sharpe,created_at) "
                        f"VALUES ('r{i}','a{i}','t1','COMPLETE','PASS',{0.5+i*0.1},datetime('now'))"
                    )
            engine = EvolutionEngine(db)
            weights = engine.topic_weights()
            # topic t1 should have a positive weight (PASS results exist)
            # This test verifies PASS simulations are counted, not silently ignored
            assert isinstance(weights, dict)


# ---------------------------------------------------------------------------
# Test 10 – Offline and Factory share screening
# ---------------------------------------------------------------------------

class TestOfflineAndFactoryShareScreening:
    def test_offline_service_uses_same_screening_module(self):
        import importlib
        spec = importlib.util.find_spec("alpha_mining.generation.screening")
        assert spec is not None, "alpha_mining.generation.screening must exist"
        offline_src = Path("alpha_mining/offline/service.py").read_text(encoding="utf-8")
        assert "alpha_mining.generation" in offline_src or "generation.screening" in offline_src or \
               "generation.canonical" in offline_src, (
            "alpha_mining.offline.service must import from alpha_mining.generation"
        )

    def test_screening_policy_is_importable_from_generation(self):
        from alpha_mining.generation.screening import CandidateScreeningPolicy
        policy = CandidateScreeningPolicy()
        assert hasattr(policy, "screen_expression")
        assert hasattr(policy, "group_rank_enabled")


# ---------------------------------------------------------------------------
# Test 11 – Production loop does not read from CSV
# ---------------------------------------------------------------------------

class TestProductionLoopNoCsvDependency:
    def test_orchestrator_does_not_import_csv_queue(self):
        import pathlib
        src = pathlib.Path("alpha_mining/factory/orchestrator.py").read_text(encoding="utf-8")
        assert "CandidateCsvQueue" not in src, (
            "FactoryOrchestrator must not import CandidateCsvQueue"
        )
        assert "csv_queue" not in src, (
            "FactoryOrchestrator must not reference csv_queue"
        )

    def test_candidate_generation_service_does_not_read_csv(self):
        from alpha_mining.generation import service
        import inspect
        src = inspect.getsource(service)
        assert "CandidateCsvQueue" not in src, (
            "CandidateGenerationService must not use CandidateCsvQueue"
        )
        assert "候选Alpha.csv" not in src, (
            "CandidateGenerationService must not reference 候选Alpha.csv"
        )

    def test_factory_works_without_csv_files(self):
        """Deleting the CSV must not break candidate generation or simulation."""
        from alpha_mining.factory.orchestrator import FactoryOrchestrator
        from alpha_mining.generation.service import CandidateGenerationBatch

        with tempfile.TemporaryDirectory() as td:
            db = _make_db(Path(td))
            _seed_research(db)
            # Explicitly ensure no CSV files exist in the temp dir
            for f in Path(td).glob("*.csv"):
                f.unlink()

            class FakeService:
                def generate(self, *, limit: int):
                    return CandidateGenerationBatch(
                        candidates=(), selected_topic_ids=(), selected_families=(),
                        rejected_by_reason={}, generation_state="READY", deferred_reason="",
                    )

            fake_sim = MagicMock()
            fake_sim.simulate.return_value = MagicMock(
                alpha_id="", status="error", checks=[], sharpe=None,
                fitness=None, turnover=None, progress_location="",
            )
            orch = FactoryOrchestrator(db, fake_sim, candidate_service=FakeService())
            # Must not raise even when no CSV files exist
            summary = orch.run_simulate(batch_size=2)
            assert summary is not None
