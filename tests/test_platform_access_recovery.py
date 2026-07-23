from __future__ import annotations

import sqlite3
import json
import multiprocessing
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def _hold_platform_lock(path: str, ready) -> None:
    from alpha_mining.platform.access import GlobalPlatformLock

    with GlobalPlatformLock(path):
        ready.set()
        time.sleep(2)


def test_global_platform_lock_is_nonblocking_across_owners(tmp_path: Path) -> None:
    from alpha_mining.platform.access import GlobalPlatformLock, PlatformAccessBusy

    lock_path = tmp_path / "worldquant_api.lock"
    with GlobalPlatformLock(lock_path):
        with pytest.raises(PlatformAccessBusy):
            with GlobalPlatformLock(lock_path):
                pass


def test_global_platform_lock_blocks_a_second_process(tmp_path: Path) -> None:
    from alpha_mining.platform.access import GlobalPlatformLock, PlatformAccessBusy

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_hold_platform_lock, args=(str(tmp_path / "worldquant_api.lock"), ready))
    process.start()
    try:
        assert ready.wait(timeout=10)
        with pytest.raises(PlatformAccessBusy):
            with GlobalPlatformLock(tmp_path / "worldquant_api.lock"):
                pass
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


def test_429_opens_global_circuit_and_blocks_other_endpoints(tmp_path: Path) -> None:
    from alpha_mining.platform.access import CircuitOpen, PlatformAccessController

    clock = MutableClock()
    controller = PlatformAccessController(
        tmp_path / "state.sqlite", tmp_path / "worldquant_api.lock", clock=clock, jitter=lambda _a, _b: 0
    )
    permit = controller.before_request("alpha_list", "GET")
    controller.record_response(permit, status_code=429, retry_after="120", request_id="req-1")
    state = controller.status()
    assert state.state == "RATE_LIMITED"
    assert state.last_429 == clock.value.isoformat().replace("+00:00", "Z")
    with pytest.raises(CircuitOpen):
        controller.before_request("identity", "GET")


def test_only_one_probe_is_allowed_after_retry_after(tmp_path: Path) -> None:
    from alpha_mining.platform.access import CircuitOpen, PlatformAccessController

    clock = MutableClock()
    controller = PlatformAccessController(
        tmp_path / "state.sqlite", tmp_path / "worldquant_api.lock", clock=clock, jitter=lambda _a, _b: 0
    )
    first = controller.before_request("alpha_count", "GET")
    controller.record_response(first, status_code=429, retry_after="10")
    clock.value += timedelta(seconds=11)
    probe = controller.before_request("identity", "GET", recovery_probe=True)
    with pytest.raises(CircuitOpen):
        controller.before_request("alpha_list", "GET", recovery_probe=True)
    controller.record_response(probe, status_code=200)
    assert controller.status().state == "CLOSED"


def test_failed_recovery_probes_remain_rate_limited_and_auto_recoverable(tmp_path: Path) -> None:
    from alpha_mining.platform.access import CircuitOpen, PlatformAccessController

    clock = MutableClock()
    controller = PlatformAccessController(
        tmp_path / "state.sqlite",
        tmp_path / "worldquant_api.lock",
        clock=clock,
        jitter=lambda _a, _b: 0,
        max_auto_recoveries=2,
        fallback_backoff_seconds=(1, 2),
    )
    permit = controller.before_request("alpha_count", "GET")
    controller.record_response(permit, status_code=429, retry_after=None)
    for _ in range(2):
        clock.value += timedelta(seconds=3)
        probe = controller.before_request("identity", "GET", recovery_probe=True)
        controller.record_response(probe, status_code=429, retry_after=None)
    state = controller.status()
    assert state.state == "RATE_LIMITED"
    assert state.reason == "http_429"
    clock.value += timedelta(seconds=3)
    controller.before_request("identity", "GET", recovery_probe=True)


def test_platform_request_events_never_store_credentials_or_headers(tmp_path: Path) -> None:
    from alpha_mining.platform.access import PlatformAccessController

    controller = PlatformAccessController(tmp_path / "state.sqlite", tmp_path / "lock")
    permit = controller.before_request("identity", "GET")
    controller.record_response(permit, status_code=200, request_id="safe-id", response_body=b"{}")
    with sqlite3.connect(tmp_path / "state.sqlite") as con:
        columns = {row[1].lower() for row in con.execute("PRAGMA table_info(platform_request_events)")}
        row = con.execute(
            "SELECT endpoint_class,method,status_code,request_id,response_hash FROM platform_request_events"
        ).fetchone()
    assert not ({"cookie", "authorization", "username", "password", "headers"} & columns)
    assert row[:4] == ("identity", "GET", 200, "safe-id")
    assert len(row[4]) == 64


def test_readiness_requires_all_three_read_probes_and_no_access_errors() -> None:
    from alpha_mining.platform.readiness import evaluate_readiness

    ready = evaluate_readiness(
        auth_status="FRESH",
        identity_status="PASS",
        count_status="PASS",
        list_status="PASS",
        status_counts={200: 3},
    )
    assert ready.ready_for_ledger_sync is True
    blocked = evaluate_readiness(
        auth_status="FRESH",
        identity_status="PASS",
        count_status="PASS",
        list_status="PASS",
        status_counts={200: 3, 429: 1},
    )
    assert blocked.ready_for_ledger_sync is False


def test_connectivity_probe_is_bounded_and_writes_sanitized_readiness(tmp_path: Path) -> None:
    from alpha_mining.platform.readiness import run_connectivity_probe

    class Client:
        state_path = tmp_path / "auth.json"

        def __init__(self) -> None:
            self.calls: list[tuple[str, dict | None]] = []

        def authenticate(self) -> None:
            self.calls.append(("authenticate", None))

        def fetch_identity(self, *, recovery_probe: bool = False) -> dict:
            self.calls.append(("identity", {"recovery_probe": recovery_probe}))
            return {"id": "must-not-be-written", "email": "secret@example.test"}

        def list_alphas(self, params: dict) -> dict:
            self.calls.append(("alphas", dict(params)))
            return {"count": 3, "results": [{"id": "must-not-be-written"}]}

        def count_alphas(self, params: dict) -> int:
            self.calls.append(("count", dict(params)))
            return 3

    output = tmp_path / "platform_readiness.json"
    client = Client()
    result = run_connectivity_probe(
        client,
        database=tmp_path / "events.sqlite",
        output_path=output,
        auth_status_resolver=lambda _path: "fresh",
    )
    assert result.ready_for_ledger_sync is True
    assert [name for name, _ in client.calls] == ["authenticate", "identity", "count", "alphas"]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["ready_for_ledger_sync"] is True
    assert payload["platform_count"] == 3
    assert "secret@example.test" not in output.read_text(encoding="utf-8")


def test_connectivity_probe_stops_after_first_failure(tmp_path: Path) -> None:
    from alpha_mining.platform.readiness import run_connectivity_probe

    class Client:
        state_path = tmp_path / "auth.json"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def authenticate(self) -> None:
            self.calls.append("authenticate")

        def fetch_identity(self, *, recovery_probe: bool = False) -> dict:
            self.calls.append("identity")
            raise RuntimeError("HTTP 429")

        def list_alphas(self, _params: dict) -> dict:
            self.calls.append("alphas")
            raise AssertionError("probe must stop")

    result = run_connectivity_probe(
        Client(),
        database=tmp_path / "events.sqlite",
        output_path=tmp_path / "platform_readiness.json",
        auth_status_resolver=lambda _path: "fresh",
    )
    assert result.ready_for_ledger_sync is False


def test_shared_client_never_replays_429_or_write_requests(tmp_path: Path) -> None:
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    class Response:
        def __init__(self, status: int, retry_after: str = "") -> None:
            self.status_code = status
            self.headers = {"Retry-After": retry_after} if retry_after else {}
            self.content = b"{}"

    class Session:
        def __init__(self, responses: list[Response]) -> None:
            self.responses = responses
            self.calls = 0

        def request(self, *_args, **_kwargs):
            response = self.responses[self.calls]
            self.calls += 1
            return response

    client = ReadOnlyPlatformClient(
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
        max_attempts=3,
        sleeper=lambda _seconds: None,
    )
    session = Session([Response(429, "7"), Response(200)])
    client.session = session
    assert client.request("GET", "https://example.test/read", endpoint_class="alpha_list").status_code == 429
    assert session.calls == 1

    second = ReadOnlyPlatformClient(
        database=tmp_path / "write.sqlite",
        lock_path=tmp_path / "write.lock",
        min_interval=0,
        max_attempts=3,
        sleeper=lambda _seconds: None,
    )
    write_session = Session([Response(500), Response(200)])
    second.session = write_session
    assert second.request("PATCH", "https://example.test/write", endpoint_class="description_patch").status_code == 500
    assert write_session.calls == 1


def test_shared_client_keeps_get_server_retries_within_configured_limit(tmp_path: Path) -> None:
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    class Response:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.headers: dict[str, str] = {}
            self.content = b"{}"
            self.url = "https://example.test/read"
            self.history: list[object] = []

    class Session:
        def __init__(self) -> None:
            self.calls = 0
            self.responses = [Response(500), Response(500), Response(500), Response(200)]

        def request(self, *_args, **_kwargs):
            response = self.responses[self.calls]
            self.calls += 1
            return response

    client = ReadOnlyPlatformClient(
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
        max_attempts=3,
        sleeper=lambda _seconds: None,
    )
    session = Session()
    client.session = session

    response = client.request("GET", "https://example.test/read", endpoint_class="alpha_list")

    assert response.status_code == 500
    assert session.calls == 3


def test_shared_client_reauthenticates_once_and_replays_after_401(tmp_path: Path) -> None:
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.headers: dict[str, str] = {}
            self.content = b"{}"

    class Session:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            return Response(401 if self.calls == 1 else 200)

    client = ReadOnlyPlatformClient(
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
    )
    session = Session()
    client.session = session
    auth_calls: list[bool] = []
    client.authenticate = lambda *, force=False: auth_calls.append(force)  # type: ignore[method-assign]
    assert client.request("GET", "https://example.test/read", endpoint_class="identity").status_code == 200
    assert session.calls == 2
    assert auth_calls == [True]


def test_shared_client_does_not_reauthenticate_for_non_auth_403(tmp_path: Path) -> None:
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    class Response:
        status_code = 403
        headers: dict[str, str] = {}
        content = b'{"detail":"forbidden"}'
        url = "https://api.worldquantbrain.com/alphas/forbidden"
        history: list[object] = []

        def json(self):
            return {"detail": "forbidden"}

    class Session:
        calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    client = ReadOnlyPlatformClient(
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
    )
    session = Session()
    client.session = session
    auth_calls: list[bool] = []
    client.authenticate = lambda *, force=False: auth_calls.append(force)  # type: ignore[method-assign]

    response = client.request("GET", "https://example.test/read", endpoint_class="identity")

    assert response.status_code == 403
    assert session.calls == 1
    assert auth_calls == []


def test_shared_client_does_not_reauthenticate_for_unrelated_403_redirect(tmp_path: Path) -> None:
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    class Redirect:
        status_code = 302
        headers = {"Location": "/other"}
        url = "https://example.test/other"

    class Response:
        status_code = 403
        headers: dict[str, str] = {}
        content = b"{}"
        url = "https://example.test/read"
        history = [Redirect()]

    class Session:
        calls = 0

        def request(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    client = ReadOnlyPlatformClient(
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
    )
    session = Session()
    client.session = session
    auth_calls: list[bool] = []
    client.authenticate = lambda *, force=False: auth_calls.append(force)  # type: ignore[method-assign]

    assert client.request("GET", "https://example.test/read", endpoint_class="identity").status_code == 403
    assert session.calls == 1
    assert auth_calls == []


def test_password_auth_uses_basic_auth_post_without_body(tmp_path: Path, monkeypatch) -> None:
    from requests.auth import HTTPBasicAuth
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    client = ReadOnlyPlatformClient(
        state_path=tmp_path / "state.json",
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
    )
    monkeypatch.setenv("WQ_USERNAME", "user@example.test")
    monkeypatch.setenv("WQ_PASSWORD", "test-password")
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, kwargs=kwargs)
        return type(
            "Response",
            (),
            {"status_code": 201, "headers": {}, "content": b"{}"},
        )()

    client.request = fake_request  # type: ignore[method-assign]
    monkeypatch.setattr(
        "alpha_mining.platform.client.ensure_authenticated",
        lambda session, login, username, settings, force=False: login(),
    )

    client.authenticate(force=True)

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/authentication")
    assert isinstance(captured["kwargs"]["auth"], HTTPBasicAuth)
    assert "json" not in captured["kwargs"] and "data" not in captured["kwargs"]
    assert client.session.headers["Origin"] == "https://platform.worldquantbrain.com"
    assert "application/json" in client.session.headers["Accept"]


def test_no_cookie_cache_performs_password_login_and_persists_internal_session(
    tmp_path: Path, monkeypatch
) -> None:
    from alpha_mining.platform.client import BASE_URL, ReadOnlyPlatformClient

    monkeypatch.setenv("WQ_USERNAME", "user@example.test")
    monkeypatch.setenv("WQ_PASSWORD", "test-password")
    client = ReadOnlyPlatformClient(
        state_path=tmp_path / "missing-auth-state.json",
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
    )
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, **_kwargs):
        calls.append((method, url))
        client.session.cookies.set("session", "internal-test-cookie")
        return type(
            "Response",
            (),
            {"status_code": 201, "headers": {}, "content": b"{}", "url": url, "history": []},
        )()

    monkeypatch.setattr(client.session, "request", fake_request)

    client.authenticate()

    assert calls == [("POST", f"{BASE_URL}/authentication")]
    assert (tmp_path / "missing-auth-state.json").is_file()
    assert "internal-test-cookie" not in (tmp_path / "missing-auth-state.json").read_text(
        encoding="utf-8"
    )


def test_expired_cookie_401_relogs_with_password_and_recovers_original_request(
    tmp_path: Path, monkeypatch
) -> None:
    from alpha_mining.platform.client import BASE_URL, ReadOnlyPlatformClient

    monkeypatch.setenv("WQ_USERNAME", "user@example.test")
    monkeypatch.setenv("WQ_PASSWORD", "test-password")
    client = ReadOnlyPlatformClient(
        state_path=tmp_path / "auth-state.json",
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
    )
    calls: list[tuple[str, str]] = []
    get_count = 0

    def fake_request(method: str, url: str, **_kwargs):
        nonlocal get_count
        calls.append((method, url))
        if method == "POST":
            client.session.cookies.set("session", f"generation-{len(calls)}")
            status = 201
        else:
            get_count += 1
            status = 401 if get_count == 1 else 200
        return type(
            "Response",
            (),
            {
                "status_code": status,
                "headers": {},
                "content": b"{}",
                "url": url,
                "history": [],
                "json": lambda self: {},
            },
        )()

    monkeypatch.setattr(client.session, "request", fake_request)
    client.authenticate(force=True)

    response = client.request("GET", f"{BASE_URL}/users/self/alphas", endpoint_class="alpha_list")

    assert response.status_code == 200
    assert calls == [
        ("POST", f"{BASE_URL}/authentication"),
        ("GET", f"{BASE_URL}/users/self/alphas"),
        ("POST", f"{BASE_URL}/authentication"),
        ("GET", f"{BASE_URL}/users/self/alphas"),
    ]


def test_clear_local_auth_artifacts_deletes_only_explicit_targets(tmp_path: Path) -> None:
    from alpha_mining.auth.session_manager import clear_local_auth_artifacts

    stale = tmp_path / ".wq_auth_state.json"
    cookie = tmp_path / ".wq_browser_cookie.json"
    unrelated = tmp_path / "keep.txt"
    for path in (stale, cookie, unrelated):
        path.write_text("x", encoding="utf-8")
    removed = clear_local_auth_artifacts([stale, cookie])
    assert {Path(path).name for path in removed} == {stale.name, cookie.name}
    assert not stale.exists() and not cookie.exists()
    assert unrelated.exists()


def test_auth_state_metadata_reports_age_without_cookie_material(tmp_path: Path) -> None:
    from alpha_mining.auth.session_manager import auth_state_metadata

    path = tmp_path / ".wq_auth_state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "account_fingerprint": "hash",
                "utc_date": "2026-07-22",
                "auth_attempts": 1,
                "last_auth_utc": "2026-07-22T03:59:30Z",
                "generation": 2,
                "cookie_blob_dpapi_b64": "must-not-leak",
            }
        ),
        encoding="utf-8",
    )
    meta = auth_state_metadata(path, now=datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc))
    assert meta["auth_age_seconds"] == 30.0
    assert meta["last_successful_auth"] == "2026-07-22T03:59:30Z"
    assert "cookie" not in " ".join(meta).lower()


def test_old_pilot_selection_is_stratified_and_one_per_cluster() -> None:
    from alpha_mining.pilot.minimal import select_old_alpha_pilot

    rows = [
        {
            "cluster_id": f"c{i // 2}",
            "alpha_id": f"a{i}",
            "quality": float(100 - i),
            "structural_distance": float(i),
            "near_pass": i % 3 == 0,
            "data_category": f"d{i % 4}",
        }
        for i in range(240)
    ]
    selected = select_old_alpha_pilot(rows, limit=100, random_seed=7)
    assert len(selected) <= 100
    assert len({row["cluster_id"] for row in selected}) == len(selected)
    assert {row["stratum"] for row in selected} >= {"quality", "structural", "near_pass", "data_category", "random_control"}


def test_new_baseline_budget_stops_failed_hypothesis_before_offspring() -> None:
    from alpha_mining.pilot.minimal import plan_new_alpha_pilot

    hypotheses = [
        {"hypothesis_id": "bad", "baseline": "rank(close)", "baseline_status": "FAIL"},
        {
            "hypothesis_id": "near",
            "baseline": "rank(ts_delta(close, 20))",
            "baseline_status": "NEAR_PASS",
            "mechanism_variant": "rank(ts_delta(close, 60))",
        },
    ]
    planned = plan_new_alpha_pilot(hypotheses, limit=40)
    assert [row["hypothesis_id"] for row in planned].count("bad") == 1
    assert [row["hypothesis_id"] for row in planned].count("near") == 2
    assert len(planned) <= 40


def test_description_patch_pilot_caps_budget_and_requires_complete_checks() -> None:
    from alpha_mining.pilot.minimal import select_description_patch_pilot

    rows = [
        {"alpha_id": f"a{i}", "checks_complete": True, "description_required": True, "description_valid": True}
        for i in range(20)
    ]
    rows.append({"alpha_id": "blocked", "checks_complete": False, "description_required": True, "description_valid": True})
    selected = select_description_patch_pilot(rows, limit=100)
    assert len(selected) == 10
    assert "blocked" not in {row["alpha_id"] for row in selected}


def test_platform_reporting_writes_blocked_schema_without_fabricating_ledger(tmp_path: Path) -> None:
    from alpha_mining.platform.reporting import export_request_events, write_ledger_sync_report

    database = tmp_path / "report.sqlite"
    report = write_ledger_sync_report(database, tmp_path / "platform_ledger_sync_report.json")
    assert report["ledger_status"] == "MISSING"
    assert report["ledger_rows"] == 0
    assert report["sync_id"] == ""
    rows = export_request_events(database, tmp_path / "platform_request_events.csv")
    assert rows == 0
    assert (tmp_path / "platform_request_events.csv").read_text(encoding="utf-8-sig").startswith("timestamp,")


def test_recovery_report_writes_all_required_blocked_artifacts(tmp_path: Path) -> None:
    from alpha_mining.audit.access_recovery import write_access_recovery_reports

    result = write_access_recovery_reports(tmp_path / "audit.sqlite", tmp_path)
    assert result["status"] == "BLOCKED"
    for name in (
        "PLATFORM_ACCESS_RECOVERY_REPORT.md",
        "MINIMAL_BUSINESS_PILOT_REPORT.md",
        "platform_ledger_sync_report.json",
        "platform_reconciliation.csv",
        "platform_request_events.csv",
        "platform_readiness.json",
        "old_alpha_pilot.csv",
        "new_alpha_baseline_pilot.csv",
        "description_patch_pilot.csv",
        "submission_dry_run.csv",
        "submission_blocked.csv",
    ):
        assert (tmp_path / name).is_file(), name
