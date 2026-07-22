"""Explicit-only live submission adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .client import BASE_URL, PlatformReadError, ReadOnlyPlatformClient


@dataclass
class LiveSubmissionClient:
    state_path: str | Path = ".wq_auth_state.json"
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self._read = ReadOnlyPlatformClient(self.state_path, self.timeout)
        self._read.authenticate()

    def submit(self, alpha_id: str) -> dict:
        # Do not retry ambiguous server failures on a write endpoint. 401 is
        # refreshed at most once and 429 follows Retry-After in the shared client.
        response = self._read.request(
            "POST",
            f"{BASE_URL}/alphas/{alpha_id}/submit",
            allow_server_retry=False,
            endpoint_class="submit",
        )
        if response.status_code not in {200, 201}:
            raise PlatformReadError(f"submit failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except Exception:
            payload = {}
        return {
            "ok": True,
            "status_code": response.status_code,
            "response": payload if isinstance(payload, dict) else {},
        }
