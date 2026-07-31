from __future__ import annotations

import sqlite3
from pathlib import Path


def _store(tmp_path: Path, *, now=None, lease_timeout_seconds: float = 900):
    from alpha_mining.factory.simulation_requests import SimulationRequestStore
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "requests.sqlite"
    SqliteRunLog(database).initialize_schema()
    migrate(database)
    kwargs = {"lease_timeout_seconds": lease_timeout_seconds}
    if now is not None:
        kwargs["now"] = now
    return database, SimulationRequestStore(database, **kwargs)


def _claim(store):
    return store.claim(
        "rank(ts_delta(close,21))",
        {"region": "USA", "universe": "TOP3000", "delay": 1},
    )


def _statuses(database: Path, request_hash: str) -> tuple[str, str]:
    with sqlite3.connect(database) as con:
        request = con.execute(
            "SELECT status FROM simulation_requests WHERE request_hash=?", (request_hash,)
        ).fetchone()[0]
        claim = con.execute(
            "SELECT status FROM factory_candidate_claims WHERE request_hash=?", (request_hash,)
        ).fetchone()[0]
    return str(request), str(claim)


def test_claim_creates_pending_request(tmp_path: Path) -> None:
    database, store = _store(tmp_path)

    claim = _claim(store)

    assert claim.claimed
    assert _statuses(database, claim.request_hash) == ("PENDING", "CLAIMED")


def test_pending_request_moves_to_in_progress_atomically(tmp_path: Path) -> None:
    database, store = _store(tmp_path)
    claim = _claim(store)

    lease = store.acquire(1)[0]

    assert lease.request_hash == claim.request_hash
    with sqlite3.connect(database) as con:
        status, attempts, started = con.execute(
            "SELECT status,attempt_count,lease_started_at FROM simulation_requests"
        ).fetchone()
    assert (status, attempts) == ("IN_PROGRESS", 1)
    assert started


def test_crash_before_submit_is_recoverable(tmp_path: Path) -> None:
    database, first_process = _store(tmp_path)
    claim = _claim(first_process)

    _, restarted_process = _store(tmp_path)
    leases = restarted_process.acquire(1)

    assert [lease.request_hash for lease in leases] == [claim.request_hash]
    assert _statuses(database, claim.request_hash) == ("IN_PROGRESS", "CLAIMED")


def test_progress_location_resume_does_not_post_twice(tmp_path: Path) -> None:
    from alpha_mining.factory.contracts import SimulationCheckpoint
    from alpha_mining.platform.gateway import PlatformGateway

    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self):
            return {"status": "COMPLETE", "alpha": "alpha-resumed"}

    class Client:
        def __init__(self) -> None:
            self.methods: list[str] = []

        def authenticate(self) -> None:
            pass

        def request(self, method: str, *_args, **_kwargs):
            self.methods.append(method)
            return Response()

        def fetch_alpha(self, alpha_id: str):
            assert alpha_id == "alpha-resumed"
            return {"id": alpha_id, "is": {"sharpe": 1.2, "checks": []}}

    gateway = PlatformGateway(database=tmp_path / "gateway.sqlite", poll_interval=0.01)
    gateway.client = Client()

    result = gateway.simulate(
        expression="rank(close)",
        settings={"delay": 1},
        checkpoint=SimulationCheckpoint(progress_location="/simulations/progress/1"),
    )

    assert result.alpha_id == "alpha-resumed"
    assert gateway.client.methods == ["GET"]


def test_stale_in_progress_without_checkpoint_becomes_unknown(tmp_path: Path) -> None:
    clock = ["2026-07-31T00:00:00Z"]
    database, store = _store(
        tmp_path, now=lambda: clock[0], lease_timeout_seconds=900
    )
    claim = _claim(store)
    assert store.acquire(1)

    clock[0] = "2026-07-31T00:16:00Z"
    assert store.acquire(1) == []

    assert _statuses(database, claim.request_hash) == ("UNKNOWN", "UNKNOWN")


def test_success_marks_request_complete_and_claim_simulated(tmp_path: Path) -> None:
    database, store = _store(tmp_path)
    claim = _claim(store)
    lease = store.acquire(1)[0]

    store.finalize_success(
        claim.request_hash,
        alpha_id="alpha-ok",
        lease_started_at=lease.lease_started_at,
        write_success=lambda _con: None,
    )

    assert _statuses(database, claim.request_hash) == ("COMPLETE", "SIMULATED")
    with sqlite3.connect(database) as con:
        assert con.execute("SELECT alpha_id FROM simulation_requests").fetchone()[0] == "alpha-ok"


def test_complete_request_is_not_replayed(tmp_path: Path) -> None:
    _database, store = _store(tmp_path)
    claim = _claim(store)
    lease = store.acquire(1)[0]
    store.finalize_success(
        claim.request_hash,
        alpha_id="alpha-ok",
        lease_started_at=lease.lease_started_at,
        write_success=lambda _con: None,
    )

    assert store.acquire(10) == []


def test_failed_request_is_terminal(tmp_path: Path) -> None:
    database, store = _store(tmp_path)
    claim = _claim(store)
    lease = store.acquire(1)[0]
    store.finalize_failure(
        claim.request_hash,
        lease_started_at=lease.lease_started_at,
        error="platform rejected",
    )

    assert _statuses(database, claim.request_hash) == ("FAILED", "FAILED")
    assert store.acquire(10) == []


def test_new_claim_acquires_its_exact_request(tmp_path: Path) -> None:
    _database, store = _store(tmp_path)
    first = _claim(store)
    second = store.claim(
        "rank(ts_delta(revenue,21))",
        {"region": "USA", "universe": "TOP3000", "delay": 1},
    )

    lease = store.acquire(1, request_hash=second.request_hash)[0]

    assert lease.request_hash == second.request_hash
    assert lease.request_hash != first.request_hash


def test_expired_lease_cannot_finalize_new_owner(tmp_path: Path) -> None:
    clock = ["2026-07-31T00:00:00Z"]
    database, store = _store(tmp_path, now=lambda: clock[0], lease_timeout_seconds=900)
    claim = _claim(store)
    old_lease = store.acquire(1)[0]
    store.checkpoint(
        claim.request_hash,
        lease_started_at=old_lease.lease_started_at,
        progress_location="/simulations/progress/1",
    )
    clock[0] = "2026-07-31T00:16:00Z"
    new_lease = store.acquire(1)[0]

    assert not store.finalize_failure(
        claim.request_hash,
        lease_started_at=old_lease.lease_started_at,
        error="late old worker",
    )
    assert _statuses(database, claim.request_hash) == ("IN_PROGRESS", "CLAIMED")
    assert store.finalize_success(
        claim.request_hash,
        alpha_id="alpha-new-owner",
        lease_started_at=new_lease.lease_started_at,
        write_success=lambda _con: None,
    )
    assert _statuses(database, claim.request_hash) == ("COMPLETE", "SIMULATED")


def test_legacy_unknown_is_completed_only_with_success_evidence(tmp_path: Path) -> None:
    from alpha_mining.storage.migrations import migrate

    database, store = _store(tmp_path)
    proven = _claim(store)
    unproven = store.claim(
        "rank(ts_delta(revenue,63))",
        {"region": "USA", "universe": "TOP3000", "delay": 1},
    )
    with sqlite3.connect(database) as con:
        con.execute(
            """UPDATE simulation_requests
               SET status='UNKNOWN',last_error='legacy CLAIMED request has no verifiable external checkpoint'"""
        )
        con.execute(
            """INSERT INTO simulation_runs
               (utc_iso,alpha_id,expression,status,queue_status)
               VALUES ('2026-01-01','alpha-proven','rank(ts_delta(close,21))','COMPLETE','PASS')"""
        )

    migrate(database)

    assert _statuses(database, proven.request_hash) == ("COMPLETE", "SIMULATED")
    assert _statuses(database, unproven.request_hash) == ("UNKNOWN", "CLAIMED")
