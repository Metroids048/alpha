"""Browser-backed WorldQuant transport with no credential export.

The dedicated Chrome profile is the authentication boundary.  Requests are
evaluated in a WorldQuant page with ``credentials: include`` so Chrome, rather
than Python, attaches HttpOnly and browser-bound session state.
"""

from __future__ import annotations

import json
import os
import time
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen

from .access import PlatformAccessController
from .client import BASE_URL, PlatformReadError


PROFILE_DEFAULT = Path(".validation_workspace") / "wq_browser_profile"
PLATFORM_UI_URL = "https://platform.worldquantbrain.com"
_WORLDQUANT_HOSTS = frozenset(("platform.worldquantbrain.com", "api.worldquantbrain.com"))
_SAFE_RESPONSE_HEADERS = ("location", "content-type")


class BrowserTransportError(PlatformReadError):
    """The browser context could not perform a platform request safely."""


class _CaseInsensitiveHeaders(Mapping[str, str]):
    """HTTP headers are case-insensitive (RFC 9110 §5.1).

    The in-page fetch shim reports them lower-cased, while callers use the
    canonical spelling (``Location``) that requests' CaseInsensitiveDict accepts.
    Without this, a browser-transport simulation POST looks like it returned no
    progress location at all.  Iteration preserves the original casing so
    ``dict(headers)`` still shows what the browser actually sent.
    """

    __slots__ = ("_store",)

    def __init__(self, items: Mapping[str, str] | None = None) -> None:
        self._store: dict[str, tuple[str, str]] = {}
        for key, value in dict(items or {}).items():
            self._store[str(key).lower()] = (str(key), str(value))

    def __getitem__(self, key: str) -> str:
        return self._store[str(key).lower()][1]

    def __iter__(self):
        return iter(original for original, _ in self._store.values())

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return repr({original: value for original, value in self._store.values()})


@dataclass(frozen=True)
class BrowserResponse:
    """A deliberately small response surface which cannot expose credentials."""

    status_code: int
    text: str
    headers: Mapping[str, str] = field(default_factory=_CaseInsensitiveHeaders)

    def __post_init__(self) -> None:
        if not isinstance(self.headers, _CaseInsensitiveHeaders):
            object.__setattr__(self, "headers", _CaseInsensitiveHeaders(self.headers))

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass
class BrowserBackedWorldQuantTransport:
    """Execute WorldQuant API calls from a visible, persistent system Chrome."""

    profile_dir: str | Path = PROFILE_DEFAULT
    database: str | Path = "research_memory.sqlite"
    lock_path: str | Path = "worldquant_api.lock"
    min_interval: float = 2.0
    timeout_ms: float = 60_000
    worker_url: str = ""
    write_capability: bool = False
    sleeper: Callable[[float], None] = field(default=time.sleep, repr=False)
    controller: PlatformAccessController | None = field(default=None, repr=False)
    _pw: Any = field(default=None, init=False, repr=False)
    _context: Any = field(default=None, init=False, repr=False)
    _page: Any = field(default=None, init=False, repr=False)
    _last_request_at: float = field(default=0.0, init=False, repr=False)
    _authenticated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.profile_dir = Path(self.profile_dir).expanduser().resolve()
        self.database = Path(self.database)
        self.lock_path = Path(self.lock_path)
        self.worker_url = str(self.worker_url or os.environ.get("WQ_BROWSER_TRANSPORT_URL", "")).rstrip("/")
        if self.controller is None:
            self.controller = PlatformAccessController(self.database, self.lock_path)

    def __enter__(self) -> "BrowserBackedWorldQuantTransport":
        self.open()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def open(self) -> None:
        """Launch system Chrome against the dedicated non-default profile."""

        if self.worker_url:
            self._worker_call("GET", "/health")
            return
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BrowserTransportError("Playwright is required for browser validation") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._pw = sync_playwright().start()
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="chrome",
                headless=False,
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.goto(f"{PLATFORM_UI_URL}/alphas/unsubmitted", wait_until="domcontentloaded", timeout=self.timeout_ms)
        except Exception as exc:
            self.close()
            raise BrowserTransportError("could not open the dedicated WorldQuant Chrome profile") from exc

    def close(self) -> None:
        """Close only the controlled browser context; profile data stays local."""

        if self.worker_url:
            return

        context, pw = self._context, self._pw
        self._page = self._context = self._pw = None
        self._authenticated = False
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass

    def _pace(self) -> None:
        wait = max(0.0, float(self.min_interval) - (time.monotonic() - self._last_request_at))
        if wait:
            self.sleeper(wait)

    @staticmethod
    def _safe_url(url: str, params: Mapping[str, object] | None = None) -> str:
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or parsed.hostname not in _WORLDQUANT_HOSTS:
            raise BrowserTransportError("browser transport only permits fixed HTTPS WorldQuant platform URLs")
        if params:
            query = urlencode({key: value for key, value in params.items() if value is not None}, doseq=True)
            separator = "&" if parsed.query else "?"
            return str(url) + separator + query
        return str(url)

    @staticmethod
    def _validate_operation(method: str, url: str, *, endpoint_class: str = "read", write_capability: bool = False) -> None:
        parsed = urlparse(url)
        verb = str(method).upper()
        if verb == "GET":
            return
        if verb == "POST" and parsed.hostname == "api.worldquantbrain.com" and parsed.path == "/simulations":
            return
        if write_capability and endpoint_class == "description_patch" and verb == "PATCH" and parsed.hostname == "api.worldquantbrain.com" and re.fullmatch(r"/alphas/[^/]+", parsed.path):
            return
        if write_capability and endpoint_class == "submit" and verb == "POST" and parsed.hostname == "api.worldquantbrain.com" and re.fullmatch(r"/alphas/[^/]+/submit", parsed.path):
            return
        raise BrowserTransportError("browser transport permits only reads and POST /simulations unless an explicit write capability is enabled")

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, object] | None = None,
        endpoint_class: str = "read",
        recovery_probe: bool = False,
        **_ignored: Any,
    ) -> BrowserResponse:
        """Fetch in the authenticated page without observing browser credentials."""

        verb = str(method).upper()
        target = self._safe_url(url, params)
        self._validate_operation(verb, target, endpoint_class=endpoint_class, write_capability=bool(self.write_capability))
        if self.worker_url:
            result = self._worker_call(
                "POST",
                "/request",
                {"method": verb, "url": target, "json": dict(json) if json is not None else None, "endpoint_class": endpoint_class, "recovery_probe": bool(recovery_probe)},
            )
            return BrowserResponse(
                status_code=int(result.get("status_code", 0)),
                text=str(result.get("text", "")),
                headers={str(key): str(value) for key, value in dict(result.get("headers", {})).items()},
            )
        self.open()
        self._pace()
        assert self.controller is not None
        assert self._page is not None
        with self.controller.global_lock():
            permit = self.controller.before_request(
                endpoint_class, verb, recovery_probe=recovery_probe
            )
            try:
                result = self._page.evaluate(
                    """async (request) => {
                        const headers = request.body === null
                          ? {} : {"content-type": "application/json"};
                        const response = await fetch(request.url, {
                          method: request.method,
                          credentials: "include",
                          headers,
                          body: request.body,
                        });
                        return {
                          status: response.status,
                          text: (await response.text()).slice(0, 1000000),
                          headers: {
                            location: response.headers.get("location") || "",
                            "content-type": response.headers.get("content-type") || "",
                          },
                        };
                    }""",
                    {"method": verb, "url": target, "body": None if json is None else json_module_dumps(json)},
                )
            except Exception as exc:
                self.controller.record_response(
                    permit, status_code=0, error_class=type(exc).__name__
                )
                raise BrowserTransportError("browser page request failed before a safe response was available") from exc
            self._last_request_at = time.monotonic()
            status = int(result.get("status", 0)) if isinstance(result, Mapping) else 0
            text = str(result.get("text", "")) if isinstance(result, Mapping) else ""
            raw_headers = result.get("headers", {}) if isinstance(result, Mapping) else {}
            headers = {
                key: str(raw_headers.get(key, ""))
                for key in _SAFE_RESPONSE_HEADERS
                if isinstance(raw_headers, Mapping) and raw_headers.get(key)
            }
            self.controller.record_response(permit, status_code=status, response_body=text.encode("utf-8"))
        return BrowserResponse(status_code=status, text=text, headers=headers)

    def _worker_call(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        """Call the loopback worker without exposing browser authentication state."""

        if not self.worker_url:
            raise BrowserTransportError("browser worker URL is not configured")
        body = None if payload is None else json_module_dumps(payload).encode("utf-8")
        request = Request(
            self.worker_url + path,
            data=body,
            method=method,
            headers={"content-type": "application/json"} if body is not None else {},
        )
        try:
            with urlopen(request, timeout=max(1.0, self.timeout_ms / 1000.0)) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            raise BrowserTransportError("browser transport worker is unavailable") from exc
        if not isinstance(parsed, Mapping):
            raise BrowserTransportError("browser transport worker returned an invalid response")
        return parsed

    def authenticate(self) -> None:
        """Prove the current browser session without reading cookies or storage."""

        response = self.request("GET", f"{BASE_URL}/users/self", endpoint_class="identity")
        self._authenticated = response.status_code == 200
        if response.status_code != 200:
            raise BrowserTransportError(f"browser identity probe failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BrowserTransportError("browser identity response is not JSON") from exc
        if not isinstance(payload, dict):
            raise BrowserTransportError("browser identity response is not an object")

    def wait_for_authentication(self, *, timeout_seconds: float = 900.0, poll_interval: float = 3.0) -> int:
        """Keep the headed window available while the operator completes login."""

        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        last_status = 0
        while time.monotonic() < deadline:
            try:
                response = self.request("GET", f"{BASE_URL}/users/self", endpoint_class="identity")
                last_status = response.status_code
                if last_status == 200:
                    self._authenticated = True
                    return last_status
            except BrowserTransportError:
                last_status = 0
            self.sleeper(max(0.5, float(poll_interval)))
        return last_status

    def readonly_probes(self, *, alpha_id: str = "") -> dict[str, Any]:
        """Run the required read-only proof before any simulation POST is enabled."""

        report: dict[str, Any] = {
            "BROWSER_TRANSPORT": "FAILED",
            "BROWSER_TRANSPORT_AUTH": "AUTH_PAUSED",
            "IDENTITY_PROBE": {"HTTP_STATUS": 0},
            "READONLY_PROBES": "FAIL",
            "SIMULATION_POC": {"status": "NOT_RUN", "no_submit": True},
        }
        identity = self.request("GET", f"{BASE_URL}/users/self", endpoint_class="identity")
        report["IDENTITY_PROBE"] = {"HTTP_STATUS": identity.status_code}
        if identity.status_code != 200:
            return report
        self._authenticated = True
        capability = self.request(
            "GET",
            f"{BASE_URL}/data-fields",
            params={
                "instrumentType": "EQUITY",
                "region": "USA",
                "universe": "TOP3000",
                "delay": 1,
                "limit": 1,
                "offset": 0,
            },
            endpoint_class="catalog",
        )
        report["SIMULATION_CAPABILITY_PROBE"] = {"HTTP_STATUS": capability.status_code}
        if capability.status_code != 200:
            return report
        try:
            rows = capability.json().get("results", [])
            observed = rows[0] if isinstance(rows, list) and rows else {}
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            observed = {}
        expected_context = {
            "instrumentType": "EQUITY",
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
        }
        actual_context = {key: observed.get(key) for key in expected_context} if isinstance(observed, dict) else {}
        report["SIMULATION_CAPABILITY_PROBE"]["context"] = actual_context
        if actual_context != expected_context:
            report["SIMULATION_CAPABILITY_PROBE"]["detail"] = "returned field does not match the requested platform context"
            return report
        selected_alpha = str(alpha_id or "").strip()
        if not selected_alpha:
            listing = self.request(
                "GET", f"{BASE_URL}/users/self/alphas",
                params={"limit": 1, "offset": 0, "status": "UNSUBMITTED"},
                endpoint_class="alpha_list",
            )
            if listing.status_code != 200:
                report["ALPHA_READ_PROBE"] = {"HTTP_STATUS": listing.status_code}
                return report
            try:
                rows = listing.json().get("results", [])
                selected_alpha = str(rows[0].get("id") or rows[0].get("alpha_id") or "")
            except (AttributeError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                selected_alpha = ""
        if not selected_alpha:
            report["ALPHA_READ_PROBE"] = {"HTTP_STATUS": 0, "detail": "no existing alpha is available for the read proof"}
            return report
        alpha = self.request("GET", f"{BASE_URL}/alphas/{selected_alpha}", endpoint_class="alpha_detail")
        report["ALPHA_READ_PROBE"] = {"HTTP_STATUS": alpha.status_code, "alpha_id": selected_alpha}
        if alpha.status_code != 200:
            return report
        try:
            payload = alpha.json()
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            report["ALPHA_READ_PROBE"]["detail"] = "alpha response is not an object"
            return report
        returned_id = str(payload.get("id") or payload.get("alpha_id") or "")
        if returned_id != selected_alpha:
            report["ALPHA_READ_PROBE"]["detail"] = "alpha response does not match the requested ID"
            return report
        if not any(key in payload for key in ("settings", "regular", "metrics", "checks", "is")):
            report["ALPHA_READ_PROBE"]["detail"] = "alpha response has no expected result fields"
            return report
        report.update({
            "BROWSER_TRANSPORT": "READY",
            "BROWSER_TRANSPORT_AUTH": "FRESH",
            "READONLY_PROBES": "PASS",
        })
        return report


def json_module_dumps(value: Mapping[str, Any]) -> str:
    """Compact JSON sent as a request body, never logged or persisted here."""

    return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
