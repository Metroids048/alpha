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
