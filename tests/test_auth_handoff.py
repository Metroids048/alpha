from __future__ import annotations

import hashlib
from pathlib import Path


class _FakeProtector:
    def protect(self, payload: bytes) -> bytes:
        return hashlib.sha256(payload).digest() + payload

    def unprotect(self, payload: bytes) -> bytes:
        digest, clear = payload[:32], payload[32:]
        assert digest == hashlib.sha256(clear).digest()
        return clear


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.content = b"{}"

    def json(self) -> object:
        return self._payload


def _client(tmp_path: Path):
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    return ReadOnlyPlatformClient(
        state_path=tmp_path / ".wq_auth_state.json",
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        min_interval=0,
        auth_protector=_FakeProtector(),
    )


def test_stored_identity_probe_uses_single_get_and_marks_matching_state(
    tmp_path: Path, monkeypatch
) -> None:
    from alpha_mining.auth.session_manager import AuthSettings, auth_state_metadata, import_browser_session
    from alpha_mining.platform.client import BASE_URL

    monkeypatch.setenv("WQ_USERNAME", "operator@example.test")
    client = _client(tmp_path)
    import_browser_session(
        "operator@example.test",
        "t=browser-session",
        AuthSettings(state_path=client.state_path, protector=client.auth_protector),
    )
    calls: list[tuple[str, str, dict[str, object]]] = []
    client._restore_unexpired_session_cookies = lambda _username: True  # type: ignore[method-assign]

    def request(method: str, url: str, **kwargs: object) -> _Response:
        calls.append((method, url, kwargs))
        return _Response(200, {"id": "safe-id", "username": "operator@example.test"})

    client.request = request  # type: ignore[method-assign]

    assert client.probe_stored_identity() == 200
    assert calls == [
        (
            "GET",
            f"{BASE_URL}/users/self",
            {
                "allow_server_retry": False,
                "allow_auth_replay": False,
                "endpoint_class": "identity",
                "recovery_probe": True,
            },
        )
    ]
    metadata = auth_state_metadata(client.state_path)
    assert metadata["generation"] == 1


def test_stored_identity_probe_401_never_replays_authentication(
    tmp_path: Path, monkeypatch
) -> None:
    from alpha_mining.platform.client import BASE_URL

    monkeypatch.setenv("WQ_USERNAME", "operator@example.test")
    client = _client(tmp_path)
    calls: list[tuple[str, str, dict[str, object]]] = []
    client._restore_unexpired_session_cookies = lambda _username: False  # type: ignore[method-assign]

    def request(method: str, url: str, **kwargs: object) -> _Response:
        calls.append((method, url, kwargs))
        return _Response(401, {})

    client.request = request  # type: ignore[method-assign]

    assert client.probe_stored_identity() == 401
    assert calls == [
        (
            "GET",
            f"{BASE_URL}/users/self",
            {
                "allow_server_retry": False,
                "allow_auth_replay": False,
                "endpoint_class": "identity",
                "recovery_probe": True,
            },
        )
    ]


def test_stored_identity_probe_rejects_wrong_account(tmp_path: Path, monkeypatch) -> None:
    import pytest

    monkeypatch.setenv("WQ_USERNAME", "operator@example.test")
    client = _client(tmp_path)
    client._restore_unexpired_session_cookies = lambda _username: True  # type: ignore[method-assign]
    client.request = lambda *_args, **_kwargs: _Response(  # type: ignore[method-assign]
        200, {"username": "different@example.test"}
    )

    with pytest.raises(Exception, match="does not match WQ_USERNAME"):
        client.probe_stored_identity()


def test_strict_recovery_client_does_not_replay_401_with_password_auth(
    tmp_path: Path, monkeypatch
) -> None:
    from alpha_mining.platform.client import BASE_URL

    monkeypatch.setenv("WQ_USERNAME", "operator@example.test")
    monkeypatch.setenv("WQ_PASSWORD", "password-present-but-not-used")
    client = _client(tmp_path)
    client.require_stored_session = True
    client.allow_auth_replay = False
    client._restore_unexpired_session_cookies = lambda _username: True  # type: ignore[method-assign]
    calls: list[tuple[str, str]] = []

    def request(method: str, url: str, **_kwargs: object) -> _Response:
        calls.append((method, url))
        return _Response(401, {})

    client.session.request = request  # type: ignore[method-assign]
    client.authenticate()
    response = client.request(
        "POST",
        f"{BASE_URL}/simulations",
        endpoint_class="simulation_submit",
        allow_server_retry=False,
    )

    assert response.status_code == 401
    assert calls == [("POST", f"{BASE_URL}/simulations")]


def test_gateway_restores_strict_session_before_recovery_read(tmp_path: Path) -> None:
    from alpha_mining.platform.gateway import PlatformGateway

    gateway = PlatformGateway(
        state_path=tmp_path / ".wq_auth_state.json",
        database=tmp_path / "events.sqlite",
        lock_path=tmp_path / "worldquant_api.lock",
        require_stored_session=True,
        allow_auth_replay=False,
    )
    calls: list[str] = []
    gateway.client.authenticate = lambda: calls.append("authenticate")  # type: ignore[method-assign]
    gateway.client.fetch_alpha = lambda alpha_id: (  # type: ignore[method-assign]
        calls.append(f"fetch:{alpha_id}") or {"id": alpha_id}
    )

    assert gateway.fetch_alpha("alpha-1") == {"id": "alpha-1"}
    assert calls == ["authenticate", "fetch:alpha-1"]


def test_cookie_import_tool_targets_recovery_state_file() -> None:
    import importlib.util

    tool_path = Path("tools/ops/import_cookie_now.py").resolve()
    spec = importlib.util.spec_from_file_location("import_cookie_now_test", tool_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.STATE_PATH == Path.cwd() / ".wq_auth_state.json"


def test_recovery_resume_stops_before_runner_when_auth_probe_is_not_200(
    monkeypatch, capsys
) -> None:
    from argparse import Namespace

    import alpha_mining.main as main

    monkeypatch.setattr(
        main,
        "_recovery_auth_probe",
        lambda _args: {
            "RECOVERY_AUTH_SOURCE": {"path": "safe-path", "mechanism": "DPAPI"},
            "PROGRAM_PROBE": 401,
            "HANDOFF_RESULT": "BROKEN",
        },
    )
    monkeypatch.setattr(
        main,
        "_recovery_runner",
        lambda _args: (_ for _ in ()).throw(AssertionError("runner must not start")),
    )

    assert main._cmd_recovery_run(Namespace(resume=True)) == 1
    assert '"PROGRAM_PROBE": 401' in capsys.readouterr().out


def test_recovery_new_run_also_stops_before_runner_when_auth_probe_is_not_200(
    monkeypatch, capsys
) -> None:
    from argparse import Namespace

    import alpha_mining.main as main

    monkeypatch.setattr(
        main,
        "_recovery_auth_probe",
        lambda _args: {
            "RECOVERY_AUTH_SOURCE": {"path": "safe-path", "mechanism": "DPAPI"},
            "PROGRAM_PROBE": 401,
            "HANDOFF_RESULT": "BROKEN",
        },
    )
    monkeypatch.setattr(
        main,
        "_recovery_runner",
        lambda _args: (_ for _ in ()).throw(AssertionError("runner must not start")),
    )

    assert main._cmd_recovery_run(Namespace(resume=False)) == 1
    assert '"PROGRAM_PROBE": 401' in capsys.readouterr().out
