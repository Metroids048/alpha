"""Bounded, rate-limited WorldQuant platform adapter used by gate refresh."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from requests.auth import HTTPBasicAuth

from alpha_mining.auth.session_manager import (
    AuthSettings,
    CookieProtector,
    ensure_authenticated,
    mark_session_validated,
)
from alpha_mining.platform.access import PlatformAccessController
from alpha_mining.platform.bearer_auth import load_bearer_token

BASE_URL = "https://api.worldquantbrain.com"
SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, */*",
    "Content-Type": "application/json",
    "Origin": "https://platform.worldquantbrain.com",
}


class PlatformReadError(RuntimeError):
    pass


def retry_after_seconds(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return max(0.0, float(text))
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


def _response_requires_reauthentication(response: Any) -> bool:
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 401:
        return True
    if status != 403:
        url = str(getattr(response, "url", "") or "").lower()
        return "/authentication" in url or "/login" in url
    url = str(getattr(response, "url", "") or "").lower()
    history = getattr(response, "history", ()) or ()
    if "/authentication" in url or "/login" in url:
        return True
    for item in history:
        if int(getattr(item, "status_code", 0) or 0) not in {301, 302, 303, 307, 308}:
            continue
        redirect_text = " ".join(
            (
                str(getattr(item, "url", "") or ""),
                str((getattr(item, "headers", {}) or {}).get("Location", "")),
            )
        ).lower()
        if "/authentication" in redirect_text or "/login" in redirect_text:
            return True
    try:
        payload = response.json()
    except Exception:
        payload = {}
    text = str(payload).lower()
    return any(token in text for token in ("session expired", "not authenticated", "authentication required"))


@dataclass
class ReadOnlyPlatformClient:
    state_path: str | Path = ".wq_auth_state.json"
    timeout: float = 30.0
    min_interval: float = 0.5
    max_attempts: int = 3
    sleeper: Callable[[float], None] = field(default=time.sleep, repr=False)
    database: str | Path = "research_memory.sqlite"
    lock_path: str | Path = "worldquant_api.lock"
    controller: PlatformAccessController | None = field(default=None, repr=False)
    auth_protector: CookieProtector | None = field(default=None, repr=False)
    use_environment_proxy: bool | None = None
    require_stored_session: bool = False
    allow_auth_replay: bool = True
    active_sync_id: str = field(default="", init=False, repr=False)
    _authenticated_username: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(SESSION_HEADERS)
        if self.use_environment_proxy is None:
            disabled = os.environ.get("WQ_NO_PROXY", "").strip().lower()
            self.session.trust_env = disabled not in {"1", "true", "yes", "on"}
        else:
            self.session.trust_env = bool(self.use_environment_proxy)
        self._last_request_at = 0.0
        if self.controller is None:
            self.controller = PlatformAccessController(self.database, self.lock_path)

    def _pace(self) -> None:
        wait = max(
            0.0, float(self.min_interval) - (time.monotonic() - self._last_request_at)
        )
        if wait:
            self.sleeper(wait)
        self._last_request_at = time.monotonic()

    def set_sync_id(self, sync_id: str) -> None:
        self.active_sync_id = str(sync_id or "")

    def authenticate(self, *, force: bool = False) -> None:
        username = os.environ.get("WQ_USERNAME", "").strip()
        password = os.environ.get("WQ_PASSWORD", "")
        if not username:
            raise PlatformReadError(
                "WQ_USERNAME is not configured; it must match the protected auth-state account"
            )

        # The platform authenticates via cookies (the `t` JWT cookie set by POST
        # /authentication).  Never use Authorization: Bearer — the API ignores it.
        # Priority:
        #   1. Restore stored cookies if they are within the cooldown window (not force).
        #   2. POST /authentication with Basic Auth (password login) to get a fresh cookie.

        def login() -> Any:
            if not password:
                raise PlatformReadError(
                    "no stored session and WQ_PASSWORD is not configured; "
                    "import fresh cookies via: python tools/ops/import_cookie_now.py"
                )
            self._pace()
            basic_auth = HTTPBasicAuth(username, password)
            self.session.auth = basic_auth
            return self.request(
                "POST",
                f"{BASE_URL}/authentication",
                endpoint_class="authentication",
                allow_server_retry=False,
                auth=basic_auth,
            )

        # Remove any stale Bearer header that might have been set previously.
        self.session.headers.pop("Authorization", None)
        self.session.auth = None

        # Recovery runs are browser-session-only: a missing or expired saved
        # session must pause validation rather than falling back to password auth.
        if self.require_stored_session:
            restored = self._restore_unexpired_session_cookies(username)
            if not restored:
                raise PlatformReadError("stored browser session is unavailable or expired")
            self._authenticated_username = username
            return

        # Step 1: reuse the stored session while its `t` JWT is genuinely unexpired.
        # The auth-state cooldown window (25 min) is far shorter than the JWT's real
        # lifetime (~4 h), so relying on the cooldown alone discards usable cookies
        # and forces a password login that the platform now rejects with 401.
        if not force:
            restored = self._restore_unexpired_session_cookies(username)
            if restored:
                self._authenticated_username = username
                return

        # Step 2: carry Basic Auth credentials from the start. Password login stays
        # deferred inside ensure_authenticated (allow_password_login=force), so the
        # next protected read is what actually exercises the credentials; a 401 there
        # triggers exactly one forced replay via the auth-replay path in request().
        if password:
            self.session.auth = HTTPBasicAuth(username, password)

        ensure_authenticated(
            self.session,
            login,
            username,
            AuthSettings(
                state_path=self.state_path,
                max_attempts=2,
                protector=self.auth_protector,
            ),
            force=force,
            # Deferred by design: with no usable stored session, do NOT spend a
            # POST /authentication here. Let the first protected read return 401
            # and trigger exactly one forced credential replay. Eagerly logging in
            # burns the daily auth cap and, on a Persona-gated account, produces a
            # 401 storm that trips the platform 429 circuit.
            allow_password_login=force,
        )
        self._authenticated_username = username

    def _restore_unexpired_session_cookies(self, username: str) -> bool:
        """Load stored cookies into the session when their `t` JWT is still valid.

        Returns True only when a usable session was restored. The JWT `exp` claim
        is the authority on freshness; the local auth-state cooldown is not.
        """
        from alpha_mining.auth.session_manager import (  # noqa: PLC0415
            _account_fingerprint,
            _load_state,
            _restore_requests_cookies,
            _unprotect_cookie_rows,
        )

        try:
            bearer = load_bearer_token(self.state_path, username)
            if bearer is None or bearer.is_expired:
                return False
            path = AuthSettings(
                state_path=self.state_path,
                protector=self.auth_protector,
            ).resolved_state_path()
            state = _load_state(
                path, _account_fingerprint(username), datetime.now(timezone.utc)
            )
            rows = _unprotect_cookie_rows(state.get("cookie_blob_dpapi_b64"))
            if not _restore_requests_cookies(self.session, rows):
                return False
        except Exception:
            return False
        print(
            "[platform/auth] reusing stored session cookies; "
            f"jwt_remaining={int(bearer.remaining_seconds)}s"
        )
        return True

    def probe_basic_identity(self) -> int:
        """Issue exactly one identity GET with Basic Auth and no stored session.

        This is a transport/authentication diagnostic only.  It deliberately
        bypasses the persisted cookie state and suppresses credential-login
        replay so a 401 cannot turn into a hidden POST /authentication.
        """
        username = os.environ.get("WQ_USERNAME", "").strip()
        password = os.environ.get("WQ_PASSWORD", "")
        if not username or not password:
            raise PlatformReadError("WQ_USERNAME and WQ_PASSWORD are required")
        self.session.auth = HTTPBasicAuth(username, password)
        response = self.request(
            "GET",
            f"{BASE_URL}/users/self",
            allow_server_retry=False,
            allow_auth_replay=False,
            endpoint_class="identity",
        )
        return int(response.status_code)

    def request(
        self,
        method: str,
        url: str,
        *,
        allow_server_retry: bool = True,
        allow_auth_replay: bool = True,
        endpoint_class: str = "read",
        recovery_probe: bool = False,
        sync_id: str = "",
        **kwargs: Any,
    ) -> Any:
        verb = str(method).upper()
        attempts = max(1, int(self.max_attempts)) if verb == "GET" and allow_server_retry else 1
        auth_replayed = False
        server_attempt = 1
        request_attempt = 0
        while server_attempt <= attempts:
            request_attempt += 1
            self._pace()
            assert self.controller is not None
            with self.controller.global_lock():
                permit = self.controller.before_request(
                    endpoint_class,
                    verb,
                    recovery_probe=recovery_probe,
                    attempt=request_attempt,
                    sync_id=sync_id or self.active_sync_id,
                )
                try:
                    response = self.session.request(verb, url, timeout=self.timeout, **kwargs)
                except Exception as exc:
                    self.controller.record_response(
                        permit,
                        status_code=0,
                        error_class=type(exc).__name__,
                    )
                    if server_attempt >= attempts:
                        raise
                    self.sleeper(min(2 ** (server_attempt - 1), 30))
                    server_attempt += 1
                    continue
                headers = getattr(response, "headers", {}) or {}
                request_id = (
                    headers.get("X-Request-ID")
                    or headers.get("X-Correlation-ID")
                    or headers.get("Traceparent")
                    or ""
                )
                self.controller.record_response(
                    permit,
                    status_code=int(response.status_code),
                    retry_after=headers.get("Retry-After"),
                    request_id=str(request_id),
                    response_body=getattr(response, "content", b""),
                )
            if (
                endpoint_class != "authentication"
                and 200 <= int(response.status_code) < 300
                and self._authenticated_username
            ):
                try:
                    mark_session_validated(
                        self._authenticated_username,
                        AuthSettings(
                            state_path=self.state_path,
                            max_attempts=1,
                            protector=self.auth_protector,
                        ),
                    )
                except Exception:
                    pass
            # A 429 is a global state transition, never an in-call retry. Auth
            # failures get one credential-login replay, then return unchanged.
            if (
                endpoint_class != "authentication"
                and _response_requires_reauthentication(response)
                and allow_auth_replay
                and self.allow_auth_replay
                and not auth_replayed
            ):
                auth_replayed = True
                self.authenticate(force=True)
                continue
            if response.status_code in {401, 403, 429}:
                return response
            if (
                verb == "GET"
                and
                allow_server_retry
                and response.status_code in {500, 502, 503, 504}
                and server_attempt < attempts
            ):
                self.sleeper(min(2 ** (server_attempt - 1), 30))
                server_attempt += 1
                continue
            return response
        raise PlatformReadError("platform request exhausted bounded retry attempts")

    def fetch_alpha(self, alpha_id: str) -> dict[str, Any]:
        response = self.request("GET", f"{BASE_URL}/alphas/{alpha_id}", endpoint_class="alpha_detail")
        if response.status_code != 200:
            raise PlatformReadError(
                f"read-only alpha detail failed with HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlatformReadError("alpha detail response is not an object")
        return payload

    def list_alphas(self, params: dict[str, object]) -> dict[str, Any]:
        self.authenticate()
        endpoint_class = "alpha_count" if int(params.get("limit", 0) or 0) == 0 else "alpha_list"
        response = self.request(
            "GET", f"{BASE_URL}/users/self/alphas", params=dict(params), endpoint_class=endpoint_class
        )
        if response.status_code != 200:
            raise PlatformReadError(f"read-only alpha list failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlatformReadError("alpha list response is not an object")
        return payload

    def _catalog_json(self, resource: str, params: dict[str, object]) -> Any:
        """Fetch one catalog resource and decode it, without asserting a shape.

        Shape enforcement belongs to the callers: /data-sets and /data-fields are
        paged objects, while /operators is an unpaged array.
        """
        self.authenticate()
        response = self.request("GET", f"{BASE_URL}/{resource}", params=dict(params), endpoint_class="catalog")
        if response.status_code != 200:
            detail = ""
            try:
                error = response.json()
            except Exception:
                error = None
            if isinstance(error, dict):
                message = error.get("message") or error.get("error") or error.get("code")
                if isinstance(message, dict):
                    message = message.get("message") or message.get("code")
                if isinstance(message, (str, int, float)):
                    detail = str(message).replace("\r", " ").replace("\n", " ")[:300]
            if not detail:
                raw = getattr(response, "content", b"")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                if isinstance(raw, str):
                    detail = re.sub(r"\s+", " ", raw).strip()[:300]
            suffix = f": {detail}" if detail else ""
            raise PlatformReadError(
                f"read-only {resource} catalog failed with HTTP {response.status_code}{suffix}"
            )
        return response.json()

    def _catalog_page(self, resource: str, params: dict[str, object]) -> dict[str, Any]:
        """Strict paged-object read for /data-sets and /data-fields."""
        payload = self._catalog_json(resource, params)
        if not isinstance(payload, dict):
            raise PlatformReadError(f"read-only {resource} catalog response is not an object")
        return payload

    def list_datasets(self, params: dict[str, object]) -> dict[str, Any]:
        return self._catalog_page("data-sets", params)

    def list_data_fields(self, params: dict[str, object]) -> dict[str, Any]:
        return self._catalog_page("data-fields", params)

    def list_operators(self, params: dict[str, object]) -> list[dict[str, Any]]:
        """Read /operators, which the platform serves as an unpaged JSON array.

        Verified against the live read-only endpoint on 2026-08-08: HTTP 200,
        top-level ``list`` of 82 objects. A paged object is still accepted so an
        older contract keeps working, but any other shape -- and any non-object
        element -- fails closed rather than being silently dropped.
        """
        payload = self._catalog_json("operators", params)
        if isinstance(payload, dict):
            rows = payload.get("results")
            if not isinstance(rows, list):
                raise PlatformReadError("read-only operators catalog object has no results list")
        elif isinstance(payload, list):
            rows = payload
        else:
            raise PlatformReadError("read-only operators catalog response is neither an array nor an object")
        if not all(isinstance(row, dict) for row in rows):
            raise PlatformReadError("read-only operators catalog contains a non-object entry")
        return list(rows)

    def count_alphas(self, params: dict[str, object]) -> int:
        self.authenticate()
        request_params = dict(params)
        request_params.update({"limit": 1, "offset": 0})
        response = self.request(
            "GET",
            f"{BASE_URL}/users/self/alphas",
            params=request_params,
            endpoint_class="alpha_count",
        )
        if response.status_code != 200:
            raise PlatformReadError(f"read-only alpha count failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlatformReadError("alpha count response is not an object")
        try:
            return int(payload["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlatformReadError("alpha count response has no valid count") from exc

    def fetch_identity(self, *, recovery_probe: bool = False) -> dict[str, Any]:
        self.authenticate()
        response = self.request(
            "GET",
            f"{BASE_URL}/users/self",
            endpoint_class="identity",
            recovery_probe=recovery_probe,
        )
        if response.status_code != 200:
            raise PlatformReadError(f"identity probe failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlatformReadError("identity response is not an object")
        return payload

    def probe_stored_identity(self, *, recovery_probe: bool = True) -> int:
        """Probe the persisted session with one identity GET and no auth replay.

        This is the handoff proof used before recovery may resume.  It restores
        only this client's configured DPAPI state, never sends a password-login
        POST, and therefore cannot turn an expired imported cookie into an
        invisible authentication attempt.
        """
        username = os.environ.get("WQ_USERNAME", "").strip()
        if not username:
            raise PlatformReadError(
                "WQ_USERNAME is not configured; it must match the protected auth-state account"
            )

        self.session.headers.pop("Authorization", None)
        self.session.auth = None
        restored = self._restore_unexpired_session_cookies(username)
        response = self.request(
            "GET",
            f"{BASE_URL}/users/self",
            allow_server_retry=False,
            allow_auth_replay=False,
            endpoint_class="identity",
            recovery_probe=recovery_probe,
        )
        status_code = int(response.status_code)
        if status_code != 200:
            return status_code
        if not restored:
            raise PlatformReadError(
                "identity returned HTTP 200 without restoring the configured auth state"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlatformReadError("identity response is not an object")
        identity = str(
            payload.get("username") or payload.get("email") or payload.get("user_name") or ""
        ).strip()
        if not identity:
            raise PlatformReadError("identity response does not identify the authenticated account")
        if identity.casefold() != username.casefold():
            raise PlatformReadError("identity response does not match WQ_USERNAME")
        mark_session_validated(
            username,
            AuthSettings(state_path=self.state_path, protector=self.auth_protector),
        )
        self._authenticated_username = username
        return status_code

    def fetch_many(self, alpha_ids: Iterable[str]) -> list[dict[str, Any]]:
        self.authenticate()
        return [
            self.fetch_alpha(alpha_id)
            for alpha_id in alpha_ids
            if str(alpha_id).strip()
        ]
