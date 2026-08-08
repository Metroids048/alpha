"""Synthetic priors must never steer generation; only platform evidence may.

These tests lock the B-1 contract: a feedback row that never reached a real
simulation carries no evidence about alpha quality, so it must stay out of the
positive / near-pass tiers that the seed amplifier and research prompt read.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from alpha_mining.generation.feedback import (
    PLATFORM_ERROR,
    PLATFORM_VERIFIED,
    SYNTHETIC_PRIOR,
    UNVERIFIED,
    CandidateFeedbackStore,
    derive_provenance,
)


def _write_dot_catalog(root: Path) -> None:
    context = {"cached_at": time.time(), "region": "USA", "universe": "TOP3000", "delay": 1}
    (root / ".alpha_datasets_cache.json").write_text(
        json.dumps({**context, "dataset_ids": ["analyst10"], "records": [{"id": "analyst10"}]}),
        encoding="utf-8",
    )
    (root / ".alpha_datafields_cache.json").write_text(
        json.dumps(
            {
                **context,
                "rows": [
                    {
                        "id": "anl10_surprise",
                        "_ds": "analyst10",
                        "type": "MATRIX",
                        "description": "analyst earnings surprise",
                        "coverage": 0.9,
                        "userCount": 10,
                    }
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
                    {"name": "ts_rank", "signature": "ts_rank(x,d)", "arity": 2},
                    {"name": "ts_zscore", "signature": "ts_zscore(x,d)", "arity": 2},
                    {"name": "group_neutralize", "signature": "group_neutralize(x,g)", "arity": 2},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_derive_provenance_classifies_by_evidence_not_by_claim() -> None:
    assert derive_provenance([{"name": "LOW_SHARPE", "result": "FAIL"}]) == PLATFORM_VERIFIED
    assert derive_provenance(None, "SIMULATION_FAILED") == PLATFORM_ERROR
    assert derive_provenance([], "") == UNVERIFIED


def test_synthetic_pass_without_checks_is_not_platform_verified(tmp_path: Path) -> None:
    store = CandidateFeedbackStore(tmp_path / "history.sqlite")
    store.record(
        "req-synthetic",
        "PASS",
        expression="ts_rank(anl10_surprise,126)",
        sharpe=1.8,
        fitness=0.65,
        provenance=SYNTHETIC_PRIOR,
    )
    with sqlite3.connect(tmp_path / "history.sqlite") as con:
        row = con.execute(
            "SELECT provenance FROM candidate_outcomes WHERE request_hash='req-synthetic'"
        ).fetchone()
    assert row[0] == SYNTHETIC_PRIOR


def test_hand_written_pass_never_reaches_positive_tier(tmp_path: Path) -> None:
    """A fabricated PASS must not become a positive example for the generator."""

    from alpha_mining.generation.snapshots import load_local_snapshots

    _write_dot_catalog(tmp_path)
    database = tmp_path / "history.sqlite"
    store = CandidateFeedbackStore(database)
    # A hand-injected "PASS" with an invented Sharpe and no platform checks.
    store.record(
        "req-fake-pass",
        "PASS",
        expression="ts_rank(anl10_surprise,126)",
        sharpe=1.25,
        fitness=0.58,
        provenance=SYNTHETIC_PRIOR,
    )
    # A row whose write was triggered by a transport failure, not a verdict.
    store.record(
        "req-circuit",
        "FAILED",
        expression="ts_zscore(anl10_surprise,126)",
        error_category="SIMULATION_FAILED",
        error_message="CircuitOpen: a single recovery probe is already in flight",
    )

    snapshots = load_local_snapshots(
        root=tmp_path, database=database, queue_path=tmp_path / "missing-queue.csv"
    )

    assert snapshots.feedback.positive == ()
    assert snapshots.feedback.near_pass == ()
    by_hash = {item.request_hash: item for item in snapshots.feedback.records}
    assert by_hash["req-fake-pass"].provenance == SYNTHETIC_PRIOR
    assert by_hash["req-fake-pass"].platform_verified is False
    assert by_hash["req-circuit"].provenance == PLATFORM_ERROR
    assert by_hash["req-circuit"].platform_verified is False
    # Both rows remain visible so de-duplication still sees their expressions.
    assert "ts_rank(anl10_surprise,126)" in snapshots.feedback.expressions
    assert "ts_zscore(anl10_surprise,126)" in snapshots.feedback.expressions


def test_platform_returned_checks_do_reach_the_steering_tier(tmp_path: Path) -> None:
    from alpha_mining.generation.snapshots import load_local_snapshots

    _write_dot_catalog(tmp_path)
    database = tmp_path / "history.sqlite"
    CandidateFeedbackStore(database).record(
        "req-real-pass",
        "PASS",
        expression="group_neutralize(ts_zscore(anl10_surprise,126),sector)",
        sharpe=1.7,
        fitness=0.9,
        checks=[
            {"name": "LOW_SHARPE", "result": "PASS", "value": 1.7, "limit": 1.58},
            {"name": "LOW_FITNESS", "result": "PASS", "value": 0.9, "limit": 1.0},
        ],
    )

    snapshots = load_local_snapshots(
        root=tmp_path, database=database, queue_path=tmp_path / "missing-queue.csv"
    )

    assert len(snapshots.feedback.positive) == 1
    record = snapshots.feedback.positive[0]
    assert record.provenance == PLATFORM_VERIFIED
    assert record.platform_verified is True
    assert record.sharpe == 1.7


def test_amplifier_only_consumes_platform_verified_records(tmp_path: Path) -> None:
    """The v50 amplifier must not be fed a fabricated Sharpe."""

    from alpha_mining.generation.snapshots import load_local_snapshots

    _write_dot_catalog(tmp_path)
    database = tmp_path / "history.sqlite"
    store = CandidateFeedbackStore(database)
    store.record(
        "req-fake-near",
        "NEAR_PASS",
        expression="ts_rank(anl10_surprise,126)",
        sharpe=1.25,
        provenance=SYNTHETIC_PRIOR,
    )
    snapshots = load_local_snapshots(
        root=tmp_path, database=database, queue_path=tmp_path / "missing-queue.csv"
    )

    amplify_records = [
        {"expression": item.expression, "sharpe": float(item.sharpe)}
        for item in snapshots.feedback.positive + snapshots.feedback.near_pass
        if item.expression and item.sharpe is not None
    ]
    assert amplify_records == []


def test_migration_backfills_provenance_from_stored_evidence(tmp_path: Path) -> None:
    """An older database must be reclassified by evidence, not defaulted in."""

    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "legacy.sqlite"
    # Build the real schema, then roll back to the pre-provenance state so the
    # backfill runs against a database shaped like the one already on disk.
    CandidateFeedbackStore(database)
    with sqlite3.connect(database) as con:
        # The index covers the column, so it has to go first.
        con.execute("DROP INDEX IF EXISTS idx_co_provenance")
        con.execute("ALTER TABLE candidate_outcomes DROP COLUMN provenance")
        con.execute("DELETE FROM schema_migrations WHERE version=23")
        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(candidate_outcomes)")}
        assert "provenance" not in columns
        con.executemany(
            "INSERT INTO candidate_outcomes"
            "(request_hash,candidate_id,expression,outcome,sharpe,checks_json,"
            "error_category,observed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                ("h-real", "c1", "expr1", "WAITING_CHECKS", 0.27,
                 json.dumps([{"name": "LOW_SHARPE", "result": "FAIL"}]), "", "2026-08-06T00:00:00Z"),
                ("h-circuit", "c2", "expr2", "FAILED", None, "[]",
                 "SIMULATION_FAILED", "2026-08-06T00:00:00Z"),
                ("h-fake", "bootstrap_seed_001", "expr3", "PASS", 1.8, "[]",
                 "", "2026-08-06T00:00:00Z"),
            ],
        )

    migrate(database)

    with sqlite3.connect(database) as con:
        rows = dict(
            con.execute("SELECT request_hash, provenance FROM candidate_outcomes").fetchall()
        )
    assert rows["h-real"] == PLATFORM_VERIFIED
    assert rows["h-circuit"] == PLATFORM_ERROR
    assert rows["h-fake"] == SYNTHETIC_PRIOR
