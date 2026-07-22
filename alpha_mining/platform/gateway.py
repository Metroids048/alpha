"""The only production network gateway for simulate/check/PATCH/Submit."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from .client import BASE_URL, PlatformReadError, ReadOnlyPlatformClient
from .protocol import alpha_id_from_progress, extract_checks, extract_metrics


@dataclass
class PlatformGateway:
    state_path: str | Path = ".wq_auth_state.json"
    database: str | Path = "research_memory.sqlite"
    lock_path: str | Path = "worldquant_api.lock"
    timeout: float = 30.0
    min_interval: float = 2.0
    poll_interval: float = 2.0
    max_poll_seconds: float = 600.0
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.client = ReadOnlyPlatformClient(
            state_path=self.state_path,
            timeout=self.timeout,
            min_interval=self.min_interval,
            database=self.database,
            lock_path=self.lock_path,
            sleeper=self.sleeper,
        )

    def authenticate(self) -> None:
        self.client.authenticate()

    def fetch_alpha(self, alpha_id: str) -> dict[str, Any]:
        return self.client.fetch_alpha(alpha_id)

    def patch_alpha(self, alpha_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.request(
            "PATCH",
            f"{BASE_URL}/alphas/{alpha_id}",
            json=payload,
            endpoint_class="description_patch",
            allow_server_retry=False,
        )
        if response.status_code not in {200, 201, 204}:
            raise PlatformReadError(f"description PATCH failed with HTTP {response.status_code}")
        return {"status_code": int(response.status_code)}

    def submit_alpha(self, alpha_id: str) -> dict[str, Any]:
        response = self.client.request(
            "POST",
            f"{BASE_URL}/alphas/{alpha_id}/submit",
            endpoint_class="submit",
            allow_server_retry=False,
        )
        if response.status_code not in {200, 201, 202}:
            raise PlatformReadError(f"submit failed with HTTP {response.status_code}")
        return {"status_code": int(response.status_code)}

    def simulate(
        self, *, expression: str, settings: dict[str, Any], alpha_type: str = "REGULAR"
    ):
        from alpha_mining.factory.orchestrator import SimulationResult

        self.authenticate()
        kind = str(alpha_type or "REGULAR").upper()
        payload: dict[str, Any] = {"type": kind, "settings": dict(settings)}
        if kind == "REGULAR":
            payload["regular"] = expression
        else:
            payload["expression"] = expression
        response = self.client.request(
            "POST",
            f"{BASE_URL}/simulations",
            json=payload,
            endpoint_class="simulation_submit",
            allow_server_retry=False,
        )
        if response.status_code not in {200, 201, 202}:
            raise PlatformReadError(f"simulation submit failed with HTTP {response.status_code}")
        try:
            body = response.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        alpha_id = alpha_id_from_progress(body)
        location = str(response.headers.get("Location") or "").strip()
        if not alpha_id and not location:
            raise PlatformReadError("simulation response has no alpha id or progress location")
        if not alpha_id:
            progress_url = location if location.startswith("http") else urljoin(f"{BASE_URL}/", location.lstrip("/"))
            deadline = time.monotonic() + max(1.0, float(self.max_poll_seconds))
            while time.monotonic() < deadline:
                progress = self.client.request(
                    "GET", progress_url, endpoint_class="simulation_poll"
                )
                if progress.status_code != 200:
                    raise PlatformReadError(f"simulation poll failed with HTTP {progress.status_code}")
                try:
                    current = progress.json()
                except Exception:
                    current = {}
                current = current if isinstance(current, dict) else {}
                state = str(current.get("status") or current.get("state") or "").upper()
                if state in {"FAILED", "ERROR", "REJECTED"}:
                    return SimulationResult("", state, {}, extract_checks(current), current)
                alpha_id = alpha_id_from_progress(current)
                if alpha_id:
                    body = current
                    break
                self.sleeper(max(0.1, float(self.poll_interval)))
            if not alpha_id:
                raise PlatformReadError("simulation poll timed out without alpha id")
        detail = self.fetch_alpha(alpha_id)
        return SimulationResult(
            alpha_id=alpha_id,
            status="COMPLETE",
            metrics=extract_metrics(detail),
            checks=extract_checks(detail),
            raw=detail,
        )
