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

from alpha_mining.auth.session_manager import AuthSettings, ensure_authenticated

BASE_URL = "https://api.worldquantbrain.com"


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


@dataclass
class ReadOnlyPlatformClient:
    state_path: str | Path = ".wq_auth_state.json"
    timeout: float = 30.0
    min_interval: float = 0.5
    max_attempts: int = 3
    sleeper: Callable[[float], None] = field(default=time.sleep, repr=False)

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self._last_request_at = 0.0

    def _pace(self) -> None:
        wait = max(
            0.0, float(self.min_interval) - (time.monotonic() - self._last_request_at)
        )
        if wait:
            self.sleeper(wait)
        self._last_request_at = time.monotonic()

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
            return self.session.post(
                f"{BASE_URL}/authentication",
                auth=(username, password),
                timeout=self.timeout,
            )

        ensure_authenticated(
            self.session,
            login,
            username,
            AuthSettings(state_path=self.state_path),
            force=force,
        )

    def request(
        self, method: str, url: str, *, allow_server_retry: bool = True, **kwargs: Any
    ) -> Any:
        attempts = max(1, int(self.max_attempts))
        reauthenticated = False
        for attempt in range(1, attempts + 1):
            self._pace()
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            if response.status_code == 401:
                if reauthenticated:
                    return response
                reauthenticated = True
                self.authenticate(force=True)
                continue
            if response.status_code == 429 and attempt < attempts:
                wait = retry_after_seconds(response.headers.get("Retry-After"))
                self.sleeper(wait if wait > 0 else min(2 ** (attempt - 1), 30))
                continue
            if (
                allow_server_retry
                and response.status_code in {500, 502, 503, 504}
                and attempt < attempts
            ):
                self.sleeper(min(2 ** (attempt - 1), 30))
                continue
            return response
        raise PlatformReadError("platform request exhausted bounded retry attempts")

    def fetch_alpha(self, alpha_id: str) -> dict[str, Any]:
        response = self.request("GET", f"{BASE_URL}/alphas/{alpha_id}")
        if response.status_code != 200:
            raise PlatformReadError(
                f"read-only alpha detail failed with HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlatformReadError("alpha detail response is not an object")
        return payload

    def fetch_many(self, alpha_ids: Iterable[str]) -> list[dict[str, Any]]:
        self.authenticate()
        return [
            self.fetch_alpha(alpha_id)
            for alpha_id in alpha_ids
            if str(alpha_id).strip()
        ]
