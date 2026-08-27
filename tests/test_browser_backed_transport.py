from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha_mining.platform.browser_transport import (
    BrowserBackedWorldQuantTransport,
    BrowserResponse,
    BrowserTransportError,
    PLATFORM_UI_URL,
)


class _Page:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def evaluate(self, _script: str, request: dict[str, object]) -> dict[str, object]:
        self.calls.append(request)
        return self.responses.pop(0)


def _transport(tmp_path: Path, responses: list[dict[str, object]]) -> tuple[BrowserBackedWorldQuantTransport, _Page]:
    transport = BrowserBackedWorldQuantTransport(
        profile_dir=tmp_path / "profile",
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant.lock",
        min_interval=0,
    )
    page = _Page(responses)
    transport._page = page
    return transport, page


def test_browser_transport_uses_page_fetch_and_exposes_no_auth_headers(tmp_path: Path) -> None:
    transport, page = _transport(
        tmp_path,
        [{"status": 200, "text": json.dumps({"username": "operator"}), "headers": {"location": "/progress", "set-cookie": "secret"}}],
    )

    response = transport.request("GET", "https://platform.worldquantbrain.com/users/self", endpoint_class="identity")

    assert response.status_code == 200
    assert response.json() == {"username": "operator"}
    assert response.headers == {"location": "/progress"}
    assert page.calls == [{"method": "GET", "url": "https://platform.worldquantbrain.com/users/self", "body": None}]
    source = Path("alpha_mining/platform/browser_transport.py").read_text(encoding="utf-8")
    assert "context.cookies" not in source
    assert "storage_state" not in source


def test_browser_transport_rejects_non_platform_url(tmp_path: Path) -> None:
    transport, _page = _transport(tmp_path, [])

    with pytest.raises(BrowserTransportError, match="only permits"):
        transport.request("GET", "https://example.test/users/self")


def test_browser_transport_navigates_to_the_platform_ui_not_the_api_host() -> None:
    source = Path("alpha_mining/platform/browser_transport.py").read_text(encoding="utf-8")

    assert PLATFORM_UI_URL == "https://platform.worldquantbrain.com"
    assert 'self._page.goto(f"{PLATFORM_UI_URL}/alphas/unsubmitted"' in source


def test_browser_transport_rejects_direct_mutations_except_simulation_post(tmp_path: Path) -> None:
    transport, page = _transport(tmp_path, [])

    with pytest.raises(BrowserTransportError, match="only reads and POST /simulations"):
        transport.request("POST", "https://api.worldquantbrain.com/alphas/alpha-1/submit")
    with pytest.raises(BrowserTransportError, match="only reads and POST /simulations"):
        transport.request("PATCH", "https://api.worldquantbrain.com/alphas/alpha-1")
    assert page.calls == []


def test_readonly_probes_require_identity_settings_and_alpha_read(tmp_path: Path) -> None:
    transport, page = _transport(
        tmp_path,
        [
            {"status": 200, "text": "{}", "headers": {}},
            {"status": 200, "text": json.dumps({"results": [{"instrumentType": "EQUITY", "region": "USA", "universe": "TOP3000", "delay": 1}]}), "headers": {}},
            {"status": 200, "text": json.dumps({"id": "alpha-1", "checks": []}), "headers": {}},
        ],
    )

    report = transport.readonly_probes(alpha_id="alpha-1")

    assert report["BROWSER_TRANSPORT"] == "READY"
    assert report["BROWSER_TRANSPORT_AUTH"] == "FRESH"
    assert report["IDENTITY_PROBE"] == {"HTTP_STATUS": 200}
    assert report["READONLY_PROBES"] == "PASS"
    assert report["SIMULATION_POC"] == {"status": "NOT_RUN", "no_submit": True}
    assert [str(call["url"]) for call in page.calls] == [
        "https://api.worldquantbrain.com/users/self",
        "https://api.worldquantbrain.com/data-fields?instrumentType=EQUITY&region=USA&universe=TOP3000&delay=1&limit=1&offset=0",
        "https://api.worldquantbrain.com/alphas/alpha-1",
    ]


def test_gateway_blocks_patch_and_submit_for_browser_transport(tmp_path: Path) -> None:
    from alpha_mining.platform.gateway import PlatformGateway

    transport, _page = _transport(tmp_path, [])
    gateway = PlatformGateway(
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant.lock",
        transport=transport,
    )

    with pytest.raises(PermissionError, match="does not allow alpha PATCH"):
        gateway.patch_alpha("alpha-1", {})
    with pytest.raises(PermissionError, match="never submits"):
        gateway.submit_alpha("alpha-1")


def test_browser_transport_allows_only_explicit_alpha_writes(tmp_path: Path) -> None:
    transport, page = _transport(
        tmp_path,
        [
            {"status": 204, "text": "", "headers": {}},
            {"status": 202, "text": "{}", "headers": {}},
        ],
    )
    transport.write_capability = True

    transport.request("PATCH", "https://api.worldquantbrain.com/alphas/alpha-1", json={}, endpoint_class="description_patch")
    transport.request("POST", "https://api.worldquantbrain.com/alphas/alpha-1/submit", endpoint_class="submit")

    assert [call["method"] for call in page.calls] == ["PATCH", "POST"]


def test_recovery_probe_prefers_a_fresh_browser_transport(monkeypatch, tmp_path: Path) -> None:
    from argparse import Namespace

    import alpha_mining.main as main

    class _Browser:
        def __init__(self, **_kwargs: object) -> None:
            self.profile_dir = tmp_path / "profile"

        def request(self, *_args: object, **_kwargs: object) -> BrowserResponse:
            return BrowserResponse(200, "{}")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "alpha_mining.platform.browser_transport.BrowserBackedWorldQuantTransport", _Browser
    )
    args = Namespace(
        transport="auto",
        browser_profile_dir=str(tmp_path / "profile"),
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant.lock",
        auth_state_file=tmp_path / ".wq_auth_state.json",
    )

    result = main._recovery_auth_probe(args)

    assert result["PROGRAM_PROBE"] == 200
    assert result["SELECTED_TRANSPORT"] == "browser"
    assert result["BROWSER_TRANSPORT_AUTH"] == "FRESH"
    assert args._selected_transport == "browser"


def test_recovery_simulation_poc_requires_browser_probe(monkeypatch, capsys) -> None:
    from argparse import Namespace

    import alpha_mining.main as main

    monkeypatch.setattr(
        main,
        "_recovery_auth_probe",
        lambda _args: {"PROGRAM_PROBE": 200, "SELECTED_TRANSPORT": "dpapi"},
    )
    monkeypatch.setattr(
        main,
        "_recovery_runner",
        lambda _args: (_ for _ in ()).throw(AssertionError("POC must not start without browser")),
    )

    assert main._cmd_recovery_simulation_poc(Namespace()) == 1
    assert '"SELECTED_TRANSPORT": "dpapi"' in capsys.readouterr().out


def test_recovery_simulation_poc_stops_when_readonly_probes_fail(monkeypatch, capsys, tmp_path: Path) -> None:
    from argparse import Namespace

    import alpha_mining.main as main

    class _Browser:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def readonly_probes(self, **_kwargs: object) -> dict[str, object]:
            return {"BROWSER_TRANSPORT": "FAILED", "READONLY_PROBES": "FAIL"}

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        main,
        "_recovery_auth_probe",
        lambda _args: {"PROGRAM_PROBE": 200, "SELECTED_TRANSPORT": "browser"},
    )
    monkeypatch.setattr(
        "alpha_mining.platform.browser_transport.BrowserBackedWorldQuantTransport", _Browser
    )
    monkeypatch.setattr(
        main,
        "_recovery_runner",
        lambda _args: (_ for _ in ()).throw(AssertionError("POC must not start before readonly readiness")),
    )
    args = Namespace(
        browser_profile_dir=str(tmp_path / "profile"), database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant.lock", alpha_id="alpha-1",
    )

    assert main._cmd_recovery_simulation_poc(args) == 1
    assert '"BROWSER_TRANSPORT": "FAILED"' in capsys.readouterr().out


def test_auth_pause_defers_checkpointed_request_for_resume(tmp_path: Path) -> None:
    from alpha_mining.factory.simulation_requests import SimulationRequestStore
    from alpha_mining.storage.migrations import migrate

    database = tmp_path / "recovery.sqlite"
    migrate(database)
    requests = SimulationRequestStore(database)
    claim = requests.claim("rank(field_a)", {"region": "USA"})
    assert claim.claimed
    first = requests.acquire(1, request_hash=claim.request_hash)
    assert len(first) == 1
    requests.checkpoint(
        claim.request_hash,
        lease_started_at=first[0].lease_started_at,
        progress_location="/simulations/progress-1",
    )

    assert requests.defer_for_authentication(
        claim.request_hash,
        lease_started_at=first[0].lease_started_at,
        error="HTTP 401",
    )
    resumed_claim = requests.claim("rank(field_a)", {"region": "USA"})
    assert resumed_claim.claimed
    resumed = requests.acquire(1, request_hash=claim.request_hash)
    assert len(resumed) == 1
    assert resumed[0].progress_location == "/simulations/progress-1"


def _submit_entry():
    """Load the real operator submission entry point by path."""

    import importlib.util
    import sys

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("submit_alpha_entry", root / "提交Alpha.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["submit_alpha_entry"] = module
    spec.loader.exec_module(module)
    return module


class _FakeBrowserTransport:
    """Record the validation transport lifecycle without launching Chrome."""

    instances: list["_FakeBrowserTransport"] = []
    auth_status = 200
    auth_raises: BaseException | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)
        self.opened = 0
        self.waits: list[object] = []
        self.closed = 0
        self.requests: list[tuple[str, str]] = []
        type(self).instances.append(self)

    def open(self) -> None:
        self.opened += 1

    def wait_for_authentication(self, *, timeout_seconds: float = 900.0, **_kwargs: object) -> int:
        self.waits.append(timeout_seconds)
        if type(self).auth_raises is not None:
            raise type(self).auth_raises
        return int(type(self).auth_status)

    def close(self) -> None:
        self.closed += 1

    def request(self, method: str, url: str, **_kwargs: object) -> BrowserResponse:
        self.requests.append((method, url))
        return BrowserResponse(200, "{}")


@pytest.fixture()
def fake_browser(monkeypatch):
    _FakeBrowserTransport.instances = []
    _FakeBrowserTransport.auth_status = 200
    _FakeBrowserTransport.auth_raises = None
    monkeypatch.setattr(
        "alpha_mining.platform.browser_transport.BrowserBackedWorldQuantTransport",
        _FakeBrowserTransport,
    )
    return _FakeBrowserTransport


def test_submit_entry_validation_service_uses_one_persistent_browser_transport(fake_browser, tmp_path: Path) -> None:
    from alpha_mining.platform.gateway import PlatformGateway

    entry = _submit_entry()

    service, transport = entry._build_validation_service(
        tmp_path / "research.sqlite",
        transport_mode="browser",
        browser_profile_dir=tmp_path / "profile",
        lock_path=tmp_path / "worldquant.lock",
        auth_timeout=123.0,
    )

    assert len(fake_browser.instances) == 1
    assert transport is fake_browser.instances[0]
    assert transport.opened == 1
    assert transport.waits == [123.0]
    assert transport.closed == 0
    assert isinstance(service.gateway, PlatformGateway)
    assert service.gateway.transport is transport
    assert service.orchestrator.simulation is service.gateway
    assert Path(transport.kwargs["profile_dir"]) == tmp_path / "profile"


def test_submit_entry_validation_service_fails_closed_when_browser_is_unauthenticated(fake_browser, tmp_path: Path) -> None:
    entry = _submit_entry()
    fake_browser.auth_status = 401

    with pytest.raises(entry.ValidationAuthPaused, match="AUTH_PAUSED"):
        entry._build_validation_service(
            tmp_path / "research.sqlite",
            transport_mode="browser",
            browser_profile_dir=tmp_path / "profile",
            lock_path=tmp_path / "worldquant.lock",
            auth_timeout=5.0,
        )

    assert len(fake_browser.instances) == 1
    transport = fake_browser.instances[0]
    assert transport.closed == 1
    assert transport.requests == []


def test_submit_entry_direct_transport_never_constructs_a_browser(fake_browser, tmp_path: Path) -> None:
    entry = _submit_entry()

    service, transport = entry._build_validation_service(
        tmp_path / "research.sqlite",
        transport_mode="direct",
        browser_profile_dir=tmp_path / "profile",
        lock_path=tmp_path / "worldquant.lock",
        auth_timeout=5.0,
    )

    assert fake_browser.instances == []
    assert transport is None
    assert getattr(service.gateway, "transport", None) is None


def test_submit_entry_defaults_to_browser_validation_transport() -> None:
    entry = _submit_entry()
    parser_defaults = entry._parser().parse_args([])

    assert parser_defaults.validation_transport == "browser"
    assert parser_defaults.browser_profile_dir == str(Path(".validation_workspace") / "wq_browser_profile")
    assert parser_defaults.browser_auth_timeout == 900.0
    assert parser_defaults.lock_path == "worldquant_api.lock"
    assert entry._parser().parse_args(["--validation-transport", "direct"]).validation_transport == "direct"


def test_submit_entry_real_submission_uses_browser_transport_with_explicit_write_capability(fake_browser, tmp_path: Path) -> None:
    import inspect

    entry = _submit_entry()
    submit_branch = inspect.getsource(entry._run_real_submission)
    dispatch = inspect.getsource(entry.main).split("if args.允许提交:", 1)[1].split("\n", 2)[1]

    assert "_run_real_submission" in dispatch
    assert "_build_validation_service" in submit_branch
    assert "allow_writes=True" in submit_branch
    assert "confirmation=args.确认短语" in submit_branch
    assert "execute=True" in submit_branch
    assert fake_browser.instances == []


def _validation_argv(tmp_path: Path, database: Path | None = None) -> tuple[list[str], Path]:
    """Standard validation-branch argv pointing entirely at an isolated workspace."""

    queue = tmp_path / "queue.csv"
    queue.write_text("candidate_id,request_hash,expression,queue_status\n", encoding="utf-8")
    return [
        "--database", str(database if database is not None else tmp_path / "research.sqlite"),
        "--input", str(queue),
        "--once",
        "--browser-profile-dir", str(tmp_path / "profile"),
        "--lock-path", str(tmp_path / "worldquant.lock"),
    ], queue


def test_submit_entry_closes_the_browser_exactly_once_on_a_normal_round(fake_browser, tmp_path: Path) -> None:
    entry = _submit_entry()
    argv, _queue = _validation_argv(tmp_path)

    assert entry.main(argv) == 0

    assert len(fake_browser.instances) == 1
    transport = fake_browser.instances[0]
    assert (transport.opened, transport.closed) == (1, 1)


def test_submit_entry_closes_the_browser_when_queue_projection_is_locked(fake_browser, tmp_path: Path) -> None:
    """A stale .lock beside the CSV is what a killed run leaves behind."""

    from alpha_mining.storage.csv_queue import QueueLockedError

    entry = _submit_entry()
    argv, queue = _validation_argv(tmp_path)
    lock = queue.with_suffix(queue.suffix + ".lock")
    lock.write_text('{"pid": 999999}', encoding="utf-8")

    with pytest.raises(QueueLockedError):
        entry.main(argv)

    assert len(fake_browser.instances) == 1
    transport = fake_browser.instances[0]
    assert transport.opened == 1
    assert transport.closed == 1, "an authenticated browser must not survive a locked queue"


def _build(entry, tmp_path: Path, database: Path | None = None):
    return entry._build_validation_service(
        database if database is not None else tmp_path / "research.sqlite",
        transport_mode="browser",
        browser_profile_dir=tmp_path / "profile",
        lock_path=tmp_path / "worldquant.lock",
        auth_timeout=5.0,
    )


@pytest.mark.parametrize("abort", [KeyboardInterrupt, SystemExit])
def test_submit_entry_closes_the_browser_when_the_operator_aborts_authentication(fake_browser, tmp_path: Path, abort) -> None:
    """Ctrl+C during the long face-scan wait is the most likely abort of all."""

    entry = _submit_entry()
    fake_browser.auth_raises = abort("operator aborted the login wait")

    with pytest.raises(abort):
        _build(entry, tmp_path)

    assert len(fake_browser.instances) == 1
    transport = fake_browser.instances[0]
    assert (transport.opened, transport.closed) == (1, 1)


def test_submit_entry_closes_the_browser_when_the_gateway_cannot_open_its_database(fake_browser, tmp_path: Path) -> None:
    """PlatformGateway construction does real sqlite work and can fail."""

    import sqlite3

    entry = _submit_entry()
    unusable = tmp_path / "database_as_directory"
    unusable.mkdir()

    with pytest.raises(sqlite3.OperationalError):
        _build(entry, tmp_path, database=unusable)

    assert len(fake_browser.instances) == 1
    transport = fake_browser.instances[0]
    assert (transport.opened, transport.closed) == (1, 1)


def test_submit_entry_closes_the_browser_when_the_workflow_service_cannot_be_built(fake_browser, monkeypatch, tmp_path: Path) -> None:
    import sqlite3

    entry = _submit_entry()

    def _explode(*_args: object, **_kwargs: object):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("alpha_mining.factory.operator_service.CandidateWorkflowService", _explode)

    with pytest.raises(sqlite3.OperationalError):
        _build(entry, tmp_path)

    assert len(fake_browser.instances) == 1
    transport = fake_browser.instances[0]
    assert (transport.opened, transport.closed) == (1, 1)
