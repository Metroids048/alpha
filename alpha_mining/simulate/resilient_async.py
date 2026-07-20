"""Resilient async simulate: circuit breaker + dead-letter queue (monkey-patch entry)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp

from alpha_mining.common import (
    alpha_id_from_progress,
    is_dns_error,
    is_transient_connect_error,
    safe_json_text,
    utc_iso,
)
from alpha_mining.simulate import async_batch as _ab

SIM_URL = _ab.SIM_URL
_SubmitOutcome = _ab._SubmitOutcome
_sim_payload = _ab._sim_payload
_authenticate = _ab._authenticate

DEAD_LETTER_FILENAME = "simulate_network_retry.jsonl"
_PATCHED = False
_BATCH_CTX: dict[str, Any] = {}


class NetworkCircuitBreaker:
    """Pause all simulate POSTs briefly after a burst of transient network errors."""

    def __init__(
        self, *, pause_seconds: float = 120.0, window_seconds: float = 300.0
    ) -> None:
        self.pause_seconds = pause_seconds
        self.window_seconds = window_seconds
        self._lock = asyncio.Lock()
        self._paused_until = 0.0
        self._hit_times: list[float] = []

    async def wait_if_open(self) -> None:
        while True:
            async with self._lock:
                wait = self._paused_until - time.time()
            if wait <= 0:
                return
            print(f"[simulate/resilient] circuit open; sleeping {wait:.0f}s")
            await asyncio.sleep(min(wait, 30.0))

    async def record_transient(self, cfg: Any) -> bool:
        """Record a transient fault; trip breaker if threshold exceeded. Returns True if tripped."""
        async with self._lock:
            now = time.time()
            self._hit_times = [
                t for t in self._hit_times if now - t <= self.window_seconds
            ]
            self._hit_times.append(now)
            threshold = max(1, int(getattr(cfg, "dns_error_pause_count", 3)))
            if len(self._hit_times) < threshold:
                return False
            pause = float(getattr(cfg, "dns_error_pause_seconds", 180.0))
            self._paused_until = max(self._paused_until, now + pause)
            self._hit_times.clear()
            print(f"[simulate/resilient] circuit tripped; pause POSTs for {pause:.0f}s")
            return True


def _dead_letter_path(cfg: Any) -> Path:
    root = Path(__file__).resolve().parent.parent.parent
    name = str(getattr(cfg, "simulate_retry_jsonl", None) or DEAD_LETTER_FILENAME)
    p = Path(name)
    return p if p.is_absolute() else root / p


def append_dead_letter(cfg: Any, payload: dict, reason: str) -> None:
    path = _dead_letter_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "utc_iso": utc_iso(),
        "reason": reason,
        "regular": payload.get("regular"),
        "settings": payload.get("settings"),
        "meta": payload.get("meta"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    buf = _BATCH_CTX.get("dead_letters")
    if isinstance(buf, list):
        buf.append(payload)


async def _submit_one_resilient(
    session: aiohttp.ClientSession,
    cfg: Any,
    payload: dict,
    sem: asyncio.Semaphore,
    rate: _ab._Rate429,
    reauth_lock: asyncio.Lock,
    dns_state: list[int],
    proxy: str | None,
    stats: dict[str, int],
) -> _SubmitOutcome:
    breaker: NetworkCircuitBreaker | None = _BATCH_CTX.get("breaker")
    sim_payload = _sim_payload(payload)
    last_err = ""
    reauthed = False
    max_attempts = 1 + int(cfg.max_retries)
    for attempt in range(1, max_attempts + 1):
        if breaker is not None:
            await breaker.wait_if_open()
        try:
            post_kw: dict[str, Any] = {
                "timeout": aiohttp.ClientTimeout(total=float(cfg.submit_timeout) + 20)
            }
            if proxy:
                post_kw["proxy"] = proxy
            async with sem:
                await rate.pace_submit()
                async with session.post(SIM_URL, json=sim_payload, **post_kw) as resp:
                    text = await resp.text()
                    body = safe_json_text(text)
                    code = resp.status
                    headers = dict(resp.headers)
            if code == 401:
                if not reauthed:
                    reauthed = True
                    async with reauth_lock:
                        await _authenticate(session, proxy=proxy)
                    continue
                append_dead_letter(cfg, payload, "submit_auth_failed:401")
                stats["failed"] = stats.get("failed", 0) + 1
                return _SubmitOutcome(
                    payload, None, body, "submit_auth_failed:401", None
                )
            if code == 403:
                append_dead_letter(cfg, payload, f"submit_forbidden:{text[:200]}")
                stats["failed"] = stats.get("failed", 0) + 1
                return _SubmitOutcome(
                    payload, None, body, f"submit_forbidden:{text[:400]}", None
                )
            if code == 400:
                append_dead_letter(cfg, payload, f"submit_bad_request:{text[:200]}")
                stats["failed"] = stats.get("failed", 0) + 1
                return _SubmitOutcome(
                    payload, None, body, f"submit_bad_request:{text[:500]}", None
                )
            if code in (429, 500, 502, 503, 504):
                cooldown = await rate.on_response_code(code)
                ra = headers.get("Retry-After")
                parsed_retry_after = _ab.retry_after_seconds(ra)
                wait = (
                    parsed_retry_after
                    if parsed_retry_after > 0
                    else float(min(2 ** (attempt - 1), 30))
                )
                if code == 429:
                    stats["retry_429"] = stats.get("retry_429", 0) + 1
                    wait = max(
                        wait, float(cfg.submit_429_min_sleep), rate.dynamic_sleep
                    )
                else:
                    stats["retry_5xx"] = stats.get("retry_5xx", 0) + 1
                wait = max(wait, cooldown)
                await rate.defer(wait)
                if breaker is not None:
                    await breaker.record_transient(cfg)
                await asyncio.sleep(wait)
                continue
            await rate.on_response_code(code)
            if code >= 400:
                append_dead_letter(cfg, payload, f"submit_http_{code}")
                stats["failed"] = stats.get("failed", 0) + 1
                return _SubmitOutcome(
                    payload, None, body, f"submit_http_{code}:{text[:200]}", None
                )
            dns_state[0] = 0
            aid = alpha_id_from_progress(body) if body else None
            if aid:
                stats["ok"] = stats.get("ok", 0) + 1
                return _SubmitOutcome(payload, None, body, "ok", aid)
            loc = (headers.get("Location") or "").strip()
            if not loc and isinstance(body, dict):
                for key in ("location", "url", "href", "self"):
                    v = body.get(key)
                    if isinstance(v, str) and v.strip():
                        loc = v.strip()
                        break
            if not loc:
                last_err = "missing_location"
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
                continue
            progress_url = (
                loc
                if loc.startswith("http")
                else urljoin(f"{_ab.BASE}/", loc.lstrip("/"))
            )
            stats["ok"] = stats.get("ok", 0) + 1
            return _SubmitOutcome(payload, progress_url, body, "ok", None)
        except aiohttp.ClientError as e:
            last_err = f"submit_error:{e}"
            transient = is_dns_error(e) or is_transient_connect_error(e)
            if transient and breaker is not None:
                await breaker.record_transient(cfg)
                await breaker.wait_if_open()
            if attempt < max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 12))
                continue
        except Exception as e:
            last_err = f"submit_error:{e}"
            if attempt < max_attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 12))
    append_dead_letter(cfg, payload, last_err or "submit_failed")
    stats["failed"] = stats.get("failed", 0) + 1
    return _SubmitOutcome(payload, None, None, last_err or "submit_failed", None)


def _load_dead_letter_payloads(cfg: Any) -> list[dict]:
    path = _dead_letter_path(cfg)
    if not path.is_file():
        return []
    seen: set[str] = set()
    out: list[dict] = []
    with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            expr = str(obj.get("regular") or "")
            if not expr or expr in seen:
                continue
            seen.add(expr)
            out.append(
                {
                    "type": "REGULAR",
                    "regular": expr,
                    "settings": obj.get("settings")
                    if isinstance(obj.get("settings"), dict)
                    else {},
                    "meta": obj.get("meta")
                    if isinstance(obj.get("meta"), dict)
                    else {},
                }
            )
    return out


async def run_async_simulation_batch_resilient(
    pipeline: Any, payloads: list[dict]
) -> Any:
    """Wrapped batch run: resilient POST + one low-concurrency retry pass for dead letters."""
    cfg = pipeline.cfg
    _BATCH_CTX["breaker"] = NetworkCircuitBreaker(
        pause_seconds=float(getattr(cfg, "dns_error_pause_seconds", 180.0)),
        window_seconds=300.0,
    )
    _BATCH_CTX["dead_letters"] = []
    run_batch = getattr(_ab, "_ORIGINAL_RUN_BATCH", _ab.run_async_simulation_batch)

    try:
        df = await run_batch(pipeline, payloads)
    finally:
        _BATCH_CTX.pop("breaker", None)

    retry_payloads: list[dict] = []
    for p in _BATCH_CTX.get("dead_letters") or []:
        if isinstance(p, dict) and p.get("regular"):
            retry_payloads.append(p)
    for p in _load_dead_letter_payloads(cfg):
        expr = str(p.get("regular") or "")
        if expr and expr not in {str(x.get("regular") or "") for x in retry_payloads}:
            retry_payloads.append(p)

    uniq: dict[str, dict] = {}
    for p in retry_payloads:
        expr = str(p.get("regular") or "")
        if expr:
            uniq[expr] = p
    retry_payloads = list(uniq.values())

    if not retry_payloads:
        return df

    print(
        f"[simulate/resilient] retry pass payloads={len(retry_payloads)} post_concurrent=1"
    )
    _BATCH_CTX["breaker"] = NetworkCircuitBreaker(
        pause_seconds=float(getattr(cfg, "dns_error_pause_seconds", 180.0)),
        window_seconds=300.0,
    )
    _BATCH_CTX["dead_letters"] = []
    old_cap = cfg.run_payload_cap
    old_posts = cfg.max_concurrent_simulation_posts
    try:
        cfg.run_payload_cap = len(retry_payloads)
        cfg.max_concurrent_simulation_posts = 1
        retry_df = await run_batch(pipeline, retry_payloads)
    finally:
        cfg.run_payload_cap = old_cap
        cfg.max_concurrent_simulation_posts = old_posts
        _BATCH_CTX.pop("breaker", None)
        _BATCH_CTX.pop("dead_letters", None)

    if retry_df is not None and hasattr(retry_df, "__len__") and len(retry_df) > 0:
        import pandas as pd

        if df is not None and len(df) > 0:
            combined = pd.concat([df, retry_df], ignore_index=True)
            print(
                f"[simulate/resilient] merged retry rows={len(retry_df)} total={len(combined)}"
            )
            return combined
        return retry_df
    return df


def apply_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return
    if not hasattr(_ab, "_ORIGINAL_SUBMIT_ONE"):
        setattr(_ab, "_ORIGINAL_SUBMIT_ONE", _ab._submit_one)
    if not hasattr(_ab, "_ORIGINAL_RUN_BATCH"):
        setattr(_ab, "_ORIGINAL_RUN_BATCH", _ab.run_async_simulation_batch)
    _ab._submit_one = _submit_one_resilient
    _ab.run_async_simulation_batch = run_async_simulation_batch_resilient
    _PATCHED = True
    print(
        "[simulate/resilient] patched async_batch (circuit breaker + dead-letter retry)"
    )
