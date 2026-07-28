"""Pending-request drain, exit-code precedence, and catalog autosync gating."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class _RecordingSimulation:
    """Return a distinct COMPLETE result per call, optionally failing some."""

    def __init__(
        self,
        *,
        sharpe: float = 1.30,
        fail_on: set[str] | None = None,
        reject_on: set[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.sharpe = sharpe
        self.fail_on = fail_on or set()
        self.reject_on = reject_on or set()

    def simulate(self, *, expression: str, settings: dict, alpha_type: str = "REGULAR"):
        from alpha_mining.factory.orchestrator import SimulationResult

        self.calls.append((expression, dict(settings)))
        if expression in self.fail_on:
            raise RuntimeError("simulation submit failed with HTTP 400")
        if expression in self.reject_on:
            # PlatformGateway returns (never raises) this shape when the platform
            # ends a simulation in FAILED/ERROR/REJECTED: no alpha id, no metrics.
            return SimulationResult(
                alpha_id="", status="ERROR", metrics={}, checks=[], raw={"status": "ERROR"}
            )
        return SimulationResult(
            alpha_id=f"alpha-{len(self.calls)}",
            status="COMPLETE",
            metrics={"sharpe": self.sharpe, "fitness": 1.10},
            checks=[{"name": "LOW_SHARPE", "result": "PASS", "mandatory": True}],
            raw={"id": f"alpha-{len(self.calls)}"},
        )


def _empty_research_database(tmp_path: Path) -> Path:
    """Schema-complete database with no hypotheses, so generation yields nothing."""
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "drain.sqlite"
    SqliteRunLog(database).initialize_schema()
    migrate(database)
    return database


def _insert_pending(database: Path, expression: str, *, created_at: str) -> str:
    import hashlib

    settings = {
        "region": "USA",
        "universe": "TOP3000",
        "delay": 1,
        "instrumentType": "EQUITY",
        "language": "FASTEXPR",
        "unitHandling": "VERIFY",
        "visualization": False,
    }
    payload = {"type": "REGULAR", "regular": expression, "settings": settings}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with sqlite3.connect(database) as con:
        con.execute(
            """INSERT INTO simulation_requests
            (request_hash,payload_json,status,created_at,updated_at)
            VALUES (?,?,'PENDING',?,?)""",
            (request_hash, encoded, created_at, created_at),
        )
    return request_hash


def test_drain_simulates_pending_requests_and_persists_each_run(tmp_path: Path) -> None:
    """A request left PENDING by an earlier cycle is re-run and recorded."""
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    database = _empty_research_database(tmp_path)
    _insert_pending(database, "-rank(ts_delta(close,5))", created_at="2026-01-01T00:00:00Z")
    _insert_pending(database, "rank(ts_mean(revenue,21))", created_at="2026-01-02T00:00:00Z")

    simulation = _RecordingSimulation()
    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=20)

    assert summary.simulated == 2
    assert summary.failed == 0
    # No hypotheses exist, so nothing was newly generated: every sim came from the drain.
    assert summary.generated == 0
    assert [expression for expression, _ in simulation.calls] == [
        "-rank(ts_delta(close,5))",
        "rank(ts_mean(revenue,21))",
    ]

    with sqlite3.connect(database) as con:
        statuses = dict(
            con.execute(
                "SELECT status,COUNT(*) FROM simulation_requests GROUP BY status"
            ).fetchall()
        )
        runs = con.execute(
            """SELECT expression,alpha_id,sharpe,status FROM simulation_runs
               ORDER BY expression"""
        ).fetchall()
        expressions = con.execute("SELECT COUNT(*) FROM expressions").fetchone()[0]

    assert statuses == {"COMPLETE": 2}
    assert len(runs) == 2, "each drained request must persist a simulation_runs row"
    assert {row[0] for row in runs} == {
        "-rank(ts_delta(close,5))",
        "rank(ts_mean(revenue,21))",
    }
    assert all(row[1] for row in runs), "alpha_id must be persisted, not discarded"
    assert all(row[2] == 1.30 for row in runs), "sharpe must be persisted"
    assert expressions == 2


def test_drain_honours_the_batch_size_and_leaves_the_remainder_pending(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    database = _empty_research_database(tmp_path)
    for index in range(5):
        _insert_pending(
            database,
            f"rank(ts_mean(close,{index + 2}))",
            created_at=f"2026-01-0{index + 1}T00:00:00Z",
        )

    simulation = _RecordingSimulation()
    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=2)

    assert summary.simulated == 2
    assert len(simulation.calls) == 2
    with sqlite3.connect(database) as con:
        statuses = dict(
            con.execute(
                "SELECT status,COUNT(*) FROM simulation_requests GROUP BY status"
            ).fetchall()
        )
    assert statuses == {"COMPLETE": 2, "PENDING": 3}


def test_drain_marks_a_failing_request_failed_without_aborting_the_batch(tmp_path: Path) -> None:
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    database = _empty_research_database(tmp_path)
    _insert_pending(database, "boom(close)", created_at="2026-01-01T00:00:00Z")
    _insert_pending(database, "rank(ts_mean(close,21))", created_at="2026-01-02T00:00:00Z")

    simulation = _RecordingSimulation(fail_on={"boom(close)"})
    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=20)

    assert summary.failed == 1
    assert summary.simulated == 1, "a single failure must not abort the remaining requests"
    with sqlite3.connect(database) as con:
        statuses = dict(
            con.execute(
                "SELECT status,COUNT(*) FROM simulation_requests GROUP BY status"
            ).fetchall()
        )
    assert statuses == {"FAILED": 1, "COMPLETE": 1}


def test_drain_counts_a_platform_rejection_as_failed_not_simulated(tmp_path: Path) -> None:
    """A rejected simulation returns a result instead of raising; it is not success.

    PlatformGateway.simulate returns SimulationResult("", "ERROR", ...) when the
    platform ends the run in FAILED/ERROR/REJECTED. Counting that as simulated
    would mark the request COMPLETE and let the loop report a clean cycle for
    work the platform actually threw away.
    """
    from alpha_mining.factory.orchestrator import FactoryOrchestrator

    database = _empty_research_database(tmp_path)
    _insert_pending(database, "rejected(close)", created_at="2026-01-01T00:00:00Z")
    _insert_pending(database, "rank(ts_mean(close,21))", created_at="2026-01-02T00:00:00Z")

    simulation = _RecordingSimulation(reject_on={"rejected(close)"})
    summary = FactoryOrchestrator(database, simulation).run_simulate(batch_size=20)

    assert summary.failed == 1
    assert summary.simulated == 1
    with sqlite3.connect(database) as con:
        statuses = dict(
            con.execute(
                "SELECT status,COUNT(*) FROM simulation_requests GROUP BY status"
            ).fetchall()
        )
        rejected_rows = con.execute(
            "SELECT COUNT(*) FROM simulation_runs WHERE expression='rejected(close)'"
        ).fetchone()[0]

    assert statuses == {"FAILED": 1, "COMPLETE": 1}
    assert rejected_rows == 0, "a rejected run must not be persisted as a completed run"


def test_drained_work_outranks_a_generation_deferral_in_the_exit_code() -> None:
    """A cycle that simulated must not trigger the loop's catalog backoff."""
    from alpha_mining.factory.orchestrator import FactoryCycleSummary
    from alpha_mining.factory.runtime import cycle_exit_code

    drained_but_catalog_stale = FactoryCycleSummary(
        0, 3, 0, 0, 3, 0, deferred_reason="data-field cache is stale"
    )
    nothing_drained_catalog_stale = FactoryCycleSummary(
        0, 0, 0, 0, 0, 0, deferred_reason="data-field cache is stale"
    )

    assert cycle_exit_code(drained_but_catalog_stale) == 0
    assert cycle_exit_code(nothing_drained_catalog_stale) == 8


def test_stage1_default_carries_every_platform_required_setting() -> None:
    """The platform rejects a simulation whose settings omit these fields."""
    from alpha_mining.simulate.settings_optimizer import SettingsOptimizer

    settings = SettingsOptimizer(max_local_trials=4).stage1_default("profitability")

    for required in ("instrumentType", "unitHandling", "language", "visualization"):
        assert required in settings, f"platform requires settings.{required}"
    assert settings["instrumentType"] == "EQUITY"
    assert settings["language"] == "FASTEXPR"
    assert settings["unitHandling"] == "VERIFY"


def test_catalog_cache_staleness_detects_missing_invalid_and_expired(tmp_path: Path) -> None:
    import time

    from run_pipeline_loop import CATALOG_CACHE_FILENAMES, _catalog_cache_stale

    assert _catalog_cache_stale(tmp_path), "absent caches are stale"

    now = time.time()
    for filename in CATALOG_CACHE_FILENAMES:
        (tmp_path / filename).write_text(json.dumps({"cached_at": now}), encoding="utf-8")
    assert not _catalog_cache_stale(tmp_path), "freshly written caches are usable"

    (tmp_path / CATALOG_CACHE_FILENAMES[0]).write_text(
        json.dumps({"cached_at": now - 48 * 3600}), encoding="utf-8"
    )
    assert _catalog_cache_stale(tmp_path), "an expired cache is stale"

    (tmp_path / CATALOG_CACHE_FILENAMES[0]).write_text("not json", encoding="utf-8")
    assert _catalog_cache_stale(tmp_path), "an unparseable cache is stale"
