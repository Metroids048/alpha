from __future__ import annotations

from types import SimpleNamespace

import test_wq_auth


class _Response:
    status_code = 200


class _Session:
    def __init__(self) -> None:
        self.auth = None
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []

    def post(self, *_args: object, **_kwargs: object) -> _Response:
        self.calls.append(dict(_kwargs))
        return _Response()


def _run_probe(monkeypatch, *, proxy: str | None, profile: str = "current") -> _Session:
    session = _Session()
    monkeypatch.setattr(test_wq_auth, "requests", SimpleNamespace(Session=lambda: session))
    monkeypatch.setattr(test_wq_auth, "HTTPBasicAuth", lambda *_args: object())
    monkeypatch.setattr(
        "alpha_mining.common.load_workspace_env", lambda *_args, **_kwargs: None
    )
    monkeypatch.setenv("WQ_USERNAME", "operator@example.test")
    monkeypatch.setenv("WQ_PASSWORD", "test-password")
    if proxy is None:
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
    else:
        monkeypatch.setenv("HTTPS_PROXY", proxy)
    assert test_wq_auth.main(("--profile", profile)) == 0
    return session


def test_auth_probe_does_not_assume_a_local_proxy_port(monkeypatch) -> None:
    session = _run_probe(monkeypatch, proxy=None)

    assert session.proxies == {}


def test_auth_probe_uses_only_an_explicit_proxy(monkeypatch) -> None:
    session = _run_probe(monkeypatch, proxy="http://127.0.0.1:7890")

    assert session.proxies == {"https": "http://127.0.0.1:7890"}


def test_auth_probe_legacy_profile_matches_historical_basic_auth_shape(monkeypatch) -> None:
    session = _run_probe(monkeypatch, proxy=None, profile="legacy")

    assert session.auth is None
    assert session.headers == {}
    assert session.calls == [
        {"timeout": (15, 60), "auth": ("operator@example.test", "test-password")}
    ]
