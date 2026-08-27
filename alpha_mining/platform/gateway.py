"""The only production network gateway for simulate/check/PATCH/Submit."""

from __future__ import annotations

import time
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests

from alpha_mining.factory.contracts import (
    SimulationAuthenticationPaused,
    SimulationCheckpoint,
    SimulationOutcomeUnknown,
)

from .browser_transport import BrowserBackedWorldQuantTransport, BrowserTransportError
from .client import BASE_URL, PlatformReadError, ReadOnlyPlatformClient
from .protocol import alpha_id_from_progress, extract_checks, extract_metrics
from .simulation_contract import SimulationSettingsContract


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
    settings_schema_path: str | Path = ".alpha_simulation_settings_cache.json"
    require_stored_session: bool = False
    allow_auth_replay: bool = True
    transport: BrowserBackedWorldQuantTransport | None = None
    client: ReadOnlyPlatformClient | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = ReadOnlyPlatformClient(
                state_path=self.state_path,
                timeout=self.timeout,
                min_interval=self.min_interval,
                database=self.database,
                lock_path=self.lock_path,
                sleeper=self.sleeper,
                require_stored_session=self.require_stored_session,
                allow_auth_replay=self.allow_auth_replay,
            )

    def authenticate(self) -> None:
        if self.transport is not None:
            self.transport.authenticate()
            return
        self.client.authenticate()

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self.transport is not None:
            return self.transport.request(method, url, **kwargs)
        return self.client.request(method, url, **kwargs)

    @property
    def simulation_settings_contract(self) -> SimulationSettingsContract:
        return SimulationSettingsContract.load(self.settings_schema_path)

    def fetch_alpha(self, alpha_id: str) -> dict[str, Any]:
        # Recovery's strict mode restores the proven DPAPI session before every
        # read, so a fresh worker never probes the platform anonymously.
        if self.require_stored_session:
            self.authenticate()
        if self.transport is None:
            try:
                return self.client.fetch_alpha(alpha_id)
            except PlatformReadError as exc:
                self._raise_auth_paused_from_text("alpha detail", str(exc))
                raise
        response = self._request("GET", f"{BASE_URL}/alphas/{alpha_id}", endpoint_class="alpha_detail")
        self._raise_auth_paused("alpha detail", response)
        if response.status_code != 200:
            raise PlatformReadError(f"read-only alpha detail failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise PlatformReadError("read-only alpha detail response is not an object")
        return payload

    def refresh_alpha_checks(self, alpha_id: str) -> dict[str, Any]:
        """Read current checks and PnL-bearing metadata without another POST."""
        detail = self.fetch_alpha(alpha_id)
        return {
            "alpha_id": alpha_id,
            "metrics": extract_metrics(detail),
            "checks": extract_checks(detail),
            "raw": detail,
        }

    def fetch_pnl_records(self, alpha_id: str) -> list[dict[str, Any]]:
        """Return platform PnL records when present; this is display-only data."""
        detail = self.fetch_alpha(alpha_id)
        for key in ("pnl", "pnlRecords", "dailyPnl", "returns"):
            value = detail.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def patch_alpha(self, alpha_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.transport is not None and not bool(getattr(self.transport, "write_capability", False)):
            raise PermissionError("browser transport does not allow alpha PATCH without write capability")
        response = self._request(
            "PATCH",
            f"{BASE_URL}/alphas/{alpha_id}",
            json=payload,
            endpoint_class="description_patch",
            allow_server_retry=False,
        )
        if response.status_code not in {200, 201, 204}:
            raise self._http_error("description PATCH", response)
        return {"status_code": int(response.status_code)}

    def submit_alpha(self, alpha_id: str) -> dict[str, Any]:
        if self.transport is not None and not bool(getattr(self.transport, "write_capability", False)):
            raise PermissionError("browser transport never submits Alphas without write capability")
        response = self._request(
            "POST",
            f"{BASE_URL}/alphas/{alpha_id}/submit",
            endpoint_class="submit",
            allow_server_retry=False,
        )
        if response.status_code not in {200, 201, 202}:
            raise self._http_error("submit", response)
        return {"status_code": int(response.status_code)}

    def simulate(
        self,
        *,
        expression: str,
        settings: dict[str, Any],
        alpha_type: str = "REGULAR",
        checkpoint: SimulationCheckpoint | None = None,
        checkpoint_sink: Callable[[SimulationCheckpoint], None] | None = None,
    ):
        from alpha_mining.factory.orchestrator import SimulationResult

        try:
            self.authenticate()
        except Exception as exc:
            self._raise_auth_paused_from_text("authentication", str(exc))
            raise
        resume = checkpoint or SimulationCheckpoint()
        alpha_id = str(resume.alpha_id or "").strip()
        location = str(resume.progress_location or "").strip()
        if alpha_id:
            detail = self.fetch_alpha(alpha_id)
            return SimulationResult(
                alpha_id=alpha_id,
                status="COMPLETE",
                metrics=extract_metrics(detail),
                checks=extract_checks(detail),
                raw=detail,
            )
        kind = str(alpha_type or "REGULAR").upper()
        body: dict[str, Any] = {}
        if not location:
            contract = self.simulation_settings_contract
            canonical_settings = contract.prepare(settings)
            if kind != str(contract.alpha_type(settings)).upper():
                raise ValueError("alpha_type does not match the synchronized simulation settings")
            payload: dict[str, Any] = {"type": kind, "settings": canonical_settings}
            if kind == "REGULAR":
                payload["regular"] = expression
            else:
                payload["expression"] = expression
            try:
                response = self._request(
                    "POST",
                    f"{BASE_URL}/simulations",
                    json=payload,
                    endpoint_class="simulation_submit",
                    allow_server_retry=False,
                )
            except (requests.Timeout, requests.ConnectionError, BrowserTransportError) as exc:
                raise SimulationOutcomeUnknown(
                    "simulation POST ended without a confirmable platform response"
                ) from exc
            if response.status_code not in {200, 201, 202}:
                self._raise_auth_paused("simulation submit", response)
                raise self._http_error("simulation submit", response)
            try:
                parsed = response.json()
            except Exception:
                parsed = {}
            body = parsed if isinstance(parsed, dict) else {}
            alpha_id = str(alpha_id_from_progress(body) or "").strip()
            location = str(response.headers.get("Location") or "").strip()
            if not alpha_id and not location:
                raise SimulationOutcomeUnknown(
                    "accepted simulation response has no alpha id or progress location"
                )
            if checkpoint_sink is not None:
                try:
                    checkpoint_sink(
                        SimulationCheckpoint(
                            progress_location=location,
                            alpha_id=alpha_id,
                        )
                    )
                except Exception as exc:
                    raise SimulationOutcomeUnknown(
                        "platform accepted simulation but its checkpoint could not be persisted"
                    ) from exc
        if not alpha_id:
            progress_url = location if location.startswith("http") else urljoin(f"{BASE_URL}/", location.lstrip("/"))
            deadline = time.monotonic() + max(1.0, float(self.max_poll_seconds))
            while time.monotonic() < deadline:
                progress = self._request(
                    "GET", progress_url, endpoint_class="simulation_poll"
                )
                if progress.status_code != 200:
                    self._raise_auth_paused("simulation poll", progress)
                    raise self._http_error("simulation poll", progress)
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
                    if checkpoint_sink is not None:
                        try:
                            checkpoint_sink(
                                SimulationCheckpoint(
                                    progress_location=location,
                                    alpha_id=str(alpha_id),
                                )
                            )
                        except Exception as exc:
                            raise SimulationOutcomeUnknown(
                                "simulation alpha id could not be persisted"
                            ) from exc
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

    @staticmethod
    def _raise_auth_paused(operation: str, response: Any) -> None:
        if int(getattr(response, "status_code", 0) or 0) in {401, 403}:
            raise SimulationAuthenticationPaused(f"{operation} returned HTTP {response.status_code}")

    @staticmethod
    def _raise_auth_paused_from_text(operation: str, detail: str) -> None:
        if "HTTP 401" in str(detail) or "HTTP 403" in str(detail):
            raise SimulationAuthenticationPaused(f"{operation} authentication expired: {detail}")

    @staticmethod
    def _http_error(operation: str, response: Any) -> PlatformReadError:
        """Expose a bounded, scrubbed body without leaking headers or credentials."""
        try:
            body = str(response.text or "")
        except Exception:
            body = ""
        body = re.sub(
            r"(?i)\b(cookie|authorization|token|password|secret)\s*[:=]\s*[^\s,;]+",
            "[REDACTED]",
            body,
        )
        body = " ".join(body.split())[:500]
        suffix = f": {body}" if body else ""
        return PlatformReadError(f"{operation} failed with HTTP {int(response.status_code)}{suffix}")
