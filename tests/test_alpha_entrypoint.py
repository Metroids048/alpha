from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("alpha_entrypoint", _ROOT / "启动Alpha主线.py")
assert _SPEC is not None and _SPEC.loader is not None
_ENTRY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ENTRY)


def test_entrypoint_skips_browser_login_when_auth_is_fresh(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("WQ_USERNAME", "user@example.test")
    monkeypatch.setattr(_ENTRY, "_read_auth_status", lambda _path: "FRESH")

    def runner(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0)

    assert _ENTRY._maybe_refresh_browser_session(runner=runner) == 0
    assert calls == []


def test_entrypoint_falls_back_to_headed_login_when_profile_is_untrusted(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("WQ_USERNAME", "user@example.test")
    monkeypatch.setattr(_ENTRY, "_read_auth_status", lambda _path: "STALE")

    def runner(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=1 if "--headless" in command else 0)

    assert _ENTRY._maybe_refresh_browser_session(runner=runner) == 0
    assert len(calls) == 2
    assert "--headless" in calls[0]
    assert "--headless" not in calls[1]
