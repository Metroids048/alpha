"""One-way plaintext browser-cookie migration to the existing DPAPI state."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path

import requests

from .session_manager import (
    _account_fingerprint,
    _new_state,
    _protect_cookie_rows,
    _requests_cookie_rows,
    _save_state,
)


@dataclass(frozen=True)
class CookieMigrationResult:
    migrated: bool
    verified: bool
    quarantine_path: str = ""
    reason: str = ""


def migrate_browser_cookie(
    cookie_path: str | Path,
    *,
    state_path: str | Path,
    quarantine_dir: str | Path,
    username: str,
    verify_alpha_id: str,
    timeout: float = 30.0,
) -> CookieMigrationResult:
    """Move plaintext only after protected persistence and a successful GET."""
    source = Path(cookie_path)
    state_target = Path(state_path)
    quarantine = Path(quarantine_dir)
    if not source.is_file():
        return CookieMigrationResult(False, False, reason="cookie_file_missing")
    if not username.strip() or not verify_alpha_id.strip():
        return CookieMigrationResult(
            False, False, reason="username_and_verify_alpha_id_required"
        )
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        cookie_text = str(raw.get("cookie") or "")
    except Exception:
        return CookieMigrationResult(False, False, reason="cookie_file_invalid")
    parsed = SimpleCookie()
    parsed.load(cookie_text)
    session = requests.Session()
    for name, morsel in parsed.items():
        session.cookies.set(name, morsel.value, domain=".worldquantbrain.com", path="/")
    rows = _requests_cookie_rows(session)
    if not rows:
        return CookieMigrationResult(False, False, reason="cookie_rows_empty")
    now = datetime.now(timezone.utc)
    state = _new_state(_account_fingerprint(username), now)
    state["last_auth_utc"] = now.isoformat().replace("+00:00", "Z")
    state["generation"] = 1
    state["cookie_blob_dpapi_b64"] = _protect_cookie_rows(rows)
    _save_state(state_target, state)
    try:
        response = session.get(
            f"https://api.worldquantbrain.com/alphas/{verify_alpha_id}", timeout=timeout
        )
    except Exception:
        return CookieMigrationResult(
            True, False, reason="read_only_verification_failed"
        )
    if response.status_code != 200:
        return CookieMigrationResult(
            True, False, reason=f"read_only_verification_http_{response.status_code}"
        )
    quarantine.mkdir(parents=True, exist_ok=True)
    destination = quarantine / (source.name + ".quarantined")
    shutil.move(str(source), str(destination))
    return CookieMigrationResult(True, True, str(destination), "ok")
