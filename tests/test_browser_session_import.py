from __future__ import annotations


def test_browser_session_import_persists_only_allowed_cookies(tmp_path, monkeypatch) -> None:
    import alpha_mining.auth.session_manager as manager

    monkeypatch.setattr(manager, "_protect_cookie_rows", lambda rows: str(rows))
    monkeypatch.setattr(manager, "_unprotect_cookie_rows", lambda _blob: [])
    result = manager.import_browser_session(
        "operator@example.test",
        "t=session-value; cf_clearance=clearance-value; analytics=ignored",
        manager.AuthSettings(state_path=tmp_path / "auth.json"),
    )

    assert result.restored_session
    saved = (tmp_path / "auth.json").read_text(encoding="utf-8")
    assert "analytics" not in saved
    assert "t" in saved and "cf_clearance" in saved


def test_browser_session_import_requires_authenticated_cookie(tmp_path) -> None:
    import pytest
    from alpha_mining.auth.session_manager import AuthSettings, AuthStateError, import_browser_session

    with pytest.raises(AuthStateError, match="required t cookie"):
        import_browser_session(
            "operator@example.test",
            "cf_clearance=clearance-only",
            AuthSettings(state_path=tmp_path / "auth.json"),
        )


def test_stale_browser_session_restores_before_basic_auth(tmp_path, monkeypatch) -> None:
    import json
    from datetime import timedelta

    import pytest
    import requests
    import alpha_mining.auth.session_manager as manager

    monkeypatch.setattr(manager, "_protect_cookie_rows", json.dumps)
    monkeypatch.setattr(manager, "_unprotect_cookie_rows", json.loads)
    settings = manager.AuthSettings(state_path=tmp_path / "auth.json")
    manager.import_browser_session("operator@example.test", "t=browser-session", settings)
    now = manager._utc_now()
    monkeypatch.setattr(manager, "_utc_now", lambda: now + timedelta(minutes=26))

    session = requests.Session()
    result = manager.ensure_authenticated(
        session,
        lambda: pytest.fail("stale browser session must be tried before Basic Auth"),
        "operator@example.test",
        settings,
    )

    assert result.restored_session
    assert session.cookies.get("t") == "browser-session"


def test_validated_browser_session_becomes_fresh(tmp_path, monkeypatch) -> None:
    import json

    import alpha_mining.auth.session_manager as manager

    monkeypatch.setattr(manager, "_protect_cookie_rows", json.dumps)
    monkeypatch.setattr(manager, "_unprotect_cookie_rows", json.loads)
    settings = manager.AuthSettings(state_path=tmp_path / "auth.json")
    manager.import_browser_session("operator@example.test", "t=browser-session", settings)
    manager.mark_session_validated("operator@example.test", settings)

    assert manager.auth_state_status(settings.state_path) == "fresh"
