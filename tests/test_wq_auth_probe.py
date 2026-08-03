from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_wq_auth_check() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "tools" / "ops" / "wq_auth_check.py"
    spec = importlib.util.spec_from_file_location("wq_auth_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wq_auth_check = _load_wq_auth_check()


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
    monkeypatch.setattr(wq_auth_check, "requests", SimpleNamespace(Session=lambda: session))
    monkeypatch.setattr(wq_auth_check, "HTTPBasicAuth", lambda *_args: object())
    monkeypatch.setattr(
        "alpha_mining.common.load_workspace_env", lambda *_args, **_kwargs: None
    )
    monkeypatch.setenv("WQ_USERNAME", "operator@example.test")
    monkeypatch.setenv("WQ_PASSWORD", "test-password")
    if proxy is None:
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
    else:
        monkeypatch.setenv("HTTPS_PROXY", proxy)
    assert wq_auth_check.main(("--profile", profile)) == 0
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
