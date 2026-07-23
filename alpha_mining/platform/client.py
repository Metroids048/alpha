"""Bounded, rate-limited WorldQuant platform adapter used by gate refresh."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from requests.auth import HTTPBasicAuth

from alpha_mining.auth.session_manager import AuthSettings, ensure_authenticated
from alpha_mining.platform.access import PlatformAccessController

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
    active_sync_id: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(SESSION_HEADERS)
        self.session.trust_env = True
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
                "WQ_USERNAME is required to match the protected auth-state fingerprint"
            )

        def login() -> Any:
            if not password:
                raise PlatformReadError(
                    "protected session unavailable and WQ_PASSWORD is not configured"
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

        ensure_authenticated(
            self.session,
            login,
            username,
            AuthSettings(state_path=self.state_path, max_attempts=1),
            force=force,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        allow_server_retry: bool = True,
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
            # A 429 is a global state transition, never an in-call retry. Auth
            # failures get one credential-login replay, then return unchanged.
            if (
                endpoint_class != "authentication"
                and _response_requires_reauthentication(response)
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

    def fetch_many(self, alpha_ids: Iterable[str]) -> list[dict[str, Any]]:
        self.authenticate()
        return [
            self.fetch_alpha(alpha_id)
            for alpha_id in alpha_ids
            if str(alpha_id).strip()
        ]
