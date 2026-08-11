from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from alpha_mining.factory.orchestrator import SimulationResult
from alpha_mining.recovery import (
    ARMS,
    RecoveryCandidate,
    RecoveryCandidateGenerator,
    RecoveryRunner,
    classify_platform_result,
)


def _write_catalog(root: Path) -> None:
    now = time.time()
    context = {"cached_at": now, "region": "USA", "universe": "TOP3000", "delay": 1}
    fields = [{"id": f"field_{index}", "_ds": "ds", "type": "MATRIX"} for index in range(32)]
    (root / ".alpha_datasets_cache.json").write_text(
        json.dumps({**context, "dataset_ids": ["ds"], "records": [{"id": "ds"}]}),
        encoding="utf-8",
    )
    (root / ".alpha_datafields_cache.json").write_text(
        json.dumps({**context, "rows": fields}), encoding="utf-8"
    )
    (root / ".alpha_operators_cache.json").write_text(
        json.dumps({**context, "operators": ["rank"], "records": [{"name": "rank", "signature": "rank(x)", "arity": 1}]}),
        encoding="utf-8",
    )
    (root / ".alpha_simulation_settings_cache.json").write_text(
        json.dumps(
            {
                "schema_version": "simulation-settings-v1",
                "fetched_at": now,
                "context": {"region": "USA", "universe": "TOP3000", "delay": 1},
                "defaults": {
                    "alpha_type": "REGULAR",
                    "region": "USA",
                    "universe": "TOP3000",
                    "delay": 1,
                    "decay": 0,
                    "neutralization": "NONE",
                    "truncation": 0.08,
                    "language": "FASTEXPR",
                },
                "allowed_values": {
                    "alpha_type": ["REGULAR"],
                    "region": ["USA"],
                    "universe": ["TOP3000"],
                    "delay": [1],
                    "decay": [0],
                    "neutralization": ["NONE"],
                    "truncation": [0.08],
                    "language": ["FASTEXPR"],
                },
            }
        ),
        encoding="utf-8",
    )


def _passing_checks(*, self_status: str = "PASS", value: float | None = None) -> list[dict[str, object]]:
    self_check: dict[str, object] = {"name": "SELF_CORRELATION", "result": self_status, "mandatory": True}
    if value is not None:
        self_check["value"] = value
    return [
        {"name": "LOW_SHARPE", "result": "PASS", "mandatory": True},
        {"name": "LOW_FITNESS", "result": "PASS", "mandatory": True},
        {"name": "CONCENTRATED_WEIGHT", "result": "PASS", "mandatory": True},
        self_check,
    ]


class _Gateway:
    def __init__(self, *, mode: str = "pass", self_status: str = "PASS") -> None:
        self.mode = mode
        self.self_status = self_status
        self.simulate_calls = 0
        self.refresh_calls = 0
        self.submit_calls = 0

    def simulate(self, **_kwargs):
        self.simulate_calls += 1
        if self.mode == "limit":
            raise RuntimeError("HTTP 429 Retry-After: 60")
        if self.mode == "auth":
            raise RuntimeError("HTTP 401 authentication required")
        status = "PENDING" if self.mode == "pending" else self.self_status
        return SimulationResult(
            alpha_id=f"fresh-{self.simulate_calls}",
            status="COMPLETE",
            metrics={"sharpe": 1.8, "fitness": 1.2, "turnover": 0.2},
            checks=_passing_checks(self_status=status),
            raw={},
        )

    def refresh_alpha_checks(self, _alpha_id: str):
        self.refresh_calls += 1
        return {"metrics": {"sharpe": 1.8, "fitness": 1.2, "turnover": 0.2}, "checks": _passing_checks()}

    def submit(self, **_kwargs):
        self.submit_calls += 1
        raise AssertionError("recovery must never submit")


@pytest.fixture
def runner_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    root.mkdir()
    catalog = root / ".validation_workspace"
    catalog.mkdir()
    _write_catalog(catalog)
    return root, catalog


def _runner(root: Path, catalog: Path, gateway: _Gateway) -> RecoveryRunner:
    return RecoveryRunner(
        database=root / "effective.sqlite",
        root=root,
        catalog_dir=catalog,
        gateway=gateway,
        sleeper=lambda _seconds: None,
    )


def _synthetic_pool(self, arm: str, count: int):
    return [
        RecoveryCandidate(
            candidate_id=f"{self.run_id}-{arm}-{index}",
            expression=f"rank(field_{index})",
            search_arm=arm,
            dataset="ds",
            field_family="MATRIX",
        )
        for index in range(count)
    ]


def test_pending_self_correlation_is_not_historical_final_pass(runner_root: tuple[Path, Path]) -> None:
    root, catalog = runner_root
    (root / "hopeful_alphas.jsonl").write_text(
        json.dumps(
            {
                "alpha_id": "historic-alpha",
                "expression": "rank(field_0)",
                "metrics": {"sharpe": 1.5, "fitness": 1.1},
                "checks": _passing_checks(self_status="PENDING"),
            }
        ) + "\n",
        encoding="utf-8",
    )
    result = _runner(root, catalog, _Gateway()).analyze()
    assert result["evidence_classes"] == {"PERFORMANCE_PASS": 1}
    state, _reasons, self_status, _value = classify_platform_result(
        status="COMPLETE", metrics={"sharpe": 1.5, "fitness": 1.1}, checks=_passing_checks(self_status="PENDING")
    )
    assert state == "WAITING_CHECKS"
    assert self_status == "PENDING"


def test_numeric_self_correlation_qualified_survivors_sort_lowest_first(runner_root: tuple[Path, Path]) -> None:
    root, catalog = runner_root
    runner = _runner(root, catalog, _Gateway())
    runner.analyze()
    run_id = runner.store.create_run(history_fingerprint="test", resume=False)
    for index, correlation in enumerate((0.3, 0.1)):
        candidate = RecoveryCandidate(f"candidate-{index}", f"rank(field_{index})", "broad_exploration", "ds", "MATRIX")
        assert runner.store.insert_candidate(run_id, candidate, {})
        runner.store.update_candidate(
            candidate.candidate_id,
            state="QUALIFIED",
            alpha_id=f"fresh-{index}",
            metrics={"sharpe": 1.5, "fitness": 1.0},
            checks=_passing_checks(value=correlation),
            self_status="PASS",
            self_value=correlation,
        )
    report = runner.status(run_id)
    assert [item["alpha_id"] for item in report["QUALIFIED_ALPHAS"]] == ["fresh-1", "fresh-0"]


def test_three_fresh_platform_qualified_alphas_stop_without_submit(monkeypatch, runner_root: tuple[Path, Path]) -> None:
    root, catalog = runner_root
    gateway = _Gateway()
    monkeypatch.setattr(RecoveryCandidateGenerator, "generate", _synthetic_pool)
    report = _runner(root, catalog, gateway).run()
    assert report["STATUS"] == "SUCCESS_ALPHA_FACTORY_RECOVERED"
    assert len(report["QUALIFIED_ALPHAS"]) == 3
    assert gateway.simulate_calls == 3
    assert gateway.submit_calls == 0
    assert not (root / "待提交Alpha列表.csv").exists()


def test_waiting_checks_refreshes_before_a_new_simulation(monkeypatch, runner_root: tuple[Path, Path]) -> None:
    root, catalog = runner_root
    gateway = _Gateway(mode="pending")
    monkeypatch.setattr(RecoveryCandidateGenerator, "generate", _synthetic_pool)
    runner = _runner(root, catalog, gateway)
    runner.run(max_batches=1)
    assert gateway.simulate_calls > 0
    first_calls = gateway.simulate_calls
    report = runner.run(resume=True, max_batches=1)
    assert gateway.refresh_calls > 0
    assert gateway.simulate_calls == first_calls
    assert report["STATUS"] == "SUCCESS_ALPHA_FACTORY_RECOVERED"
    assert gateway.submit_calls == 0


def test_auth_pause_keeps_generation_independent_and_resume_consumes_pending(monkeypatch, runner_root: tuple[Path, Path]) -> None:
    root, catalog = runner_root
    gateway = _Gateway(mode="auth")
    monkeypatch.setattr(RecoveryCandidateGenerator, "generate", _synthetic_pool)
    runner = _runner(root, catalog, gateway)
    paused = runner.run(max_batches=1)
    assert paused["STATUS"] == "AUTH_PAUSED"
    assert gateway.simulate_calls == 1
    pending = runner.store.candidate_rows(paused["RUN_ID"], states=("PENDING_SIMULATION",))
    assert pending
    gateway.mode = "pass"
    resumed = runner.run(resume=True, max_batches=1)
    assert gateway.simulate_calls > 1
    assert resumed["STATUS"] == "SUCCESS_ALPHA_FACTORY_RECOVERED"
    assert gateway.submit_calls == 0


def test_generation_queues_when_settings_snapshot_is_unavailable(monkeypatch, runner_root: tuple[Path, Path]) -> None:
    root, catalog = runner_root
    (catalog / ".alpha_simulation_settings_cache.json").unlink()
    gateway = _Gateway(mode="auth")
    monkeypatch.setattr(RecoveryCandidateGenerator, "generate", _synthetic_pool)
    report = _runner(root, catalog, gateway).run(max_batches=1)
    assert report["STATUS"] == "AUTH_PAUSED"
    assert gateway.simulate_calls == 0
    assert report["SEARCH_STATISTICS"]["total_real_simulations"] == 0


@pytest.mark.parametrize(
    ("mode", "expected"),
    (("limit", "PLATFORM_LIMIT_REACHED_WITHOUT_SUCCESS"), ("auth", "AUTH_PAUSED")),
)
def test_platform_blockers_are_persisted_without_submit(monkeypatch, runner_root: tuple[Path, Path], mode: str, expected: str) -> None:
    root, catalog = runner_root
    gateway = _Gateway(mode=mode)
    monkeypatch.setattr(RecoveryCandidateGenerator, "generate", _synthetic_pool)
    report = _runner(root, catalog, gateway).run()
    assert report["STATUS"] == expected
    assert report["REMAINING_LIMITATION"]["kind"] == ("AUTH" if mode == "auth" else "PLATFORM_LIMIT")
    assert gateway.submit_calls == 0


def test_recovery_module_has_no_submission_or_ready_csv_dependency() -> None:
    source = Path("alpha_mining/recovery.py").read_text(encoding="utf-8")
    assert "submission_client" not in source
    assert "待提交Alpha列表" not in source
