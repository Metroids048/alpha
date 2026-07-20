"""High-concurrency simulation: burst POST simulate, then concurrent progress polling."""

from __future__ import annotations

import asyncio
import contextlib
import json
import hashlib
import sqlite3
import os
import socket
import ssl
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import aiohttp

from alpha_mining.platform.client import retry_after_seconds

from alpha_mining.common import (
    alpha_id_from_progress,
    is_dns_error,
    merge_feedback_metrics_snapshot,
    merge_json_dicts,
    metric_get,
    safe_json_text,
    to_float,
    utc_iso,
)

BASE = "https://api.worldquantbrain.com"
SIM_URL = f"{BASE}/simulations"


def deduplicate_simulation_payloads(payloads: list[dict]) -> list[dict]:
    """Keep one deterministic occurrence of each exact simulation request."""
    seen: set[str] = set()
    unique: list[dict] = []
    for payload in payloads:
        canonical = json.dumps(
            _sim_payload(payload), sort_keys=True, separators=(",", ":"), default=str
        )
        if canonical in seen:
            continue
        seen.add(canonical)
        unique.append(payload)
    return unique


def claim_simulation_payloads(database: str, payloads: list[dict]) -> list[dict]:
    """Atomically claim exact requests so restarts cannot submit them twice."""
    from alpha_mining.storage.migrations import migrate

    migrate(database)
    claimed: list[dict] = []
    now = utc_iso()
    with sqlite3.connect(database) as con:
        con.execute("BEGIN IMMEDIATE")
        for payload in deduplicate_simulation_payloads(payloads):
            canonical = json.dumps(
                _sim_payload(payload),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            inserted = con.execute(
                "INSERT OR IGNORE INTO simulation_requests(request_hash,payload_json,status,created_at,updated_at) VALUES (?,?,?,?,?)",
                (request_hash, canonical, "CLAIMED", now, now),
            ).rowcount
            if inserted:
                claimed.append(payload)
        con.commit()
    return claimed


@dataclass
class _SubmitOutcome:
    payload: dict
    progress_url: str | None
    sim_json: dict | None
    status: str
    alpha_id_immediate: str | None = None


class _Rate429:
    __slots__ = (
        "_lock",
        "_cfg",
        "consecutive",
        "dynamic_sleep",
        "last_submit_ts",
        "next_allowed_ts",
    )

    def __init__(self, cfg: Any) -> None:
        self._lock = asyncio.Lock()
        self._cfg = cfg
        self.consecutive = 0
        self.dynamic_sleep = max(0.01, float(cfg.submit_sleep))
        self.last_submit_ts = 0.0
        self.next_allowed_ts = 0.0

    async def pace_submit(self) -> None:
        async with self._lock:
            now = time.time()
            wait = max(
                0.0,
                self.dynamic_sleep - (now - self.last_submit_ts),
                self.next_allowed_ts - now,
            )
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_submit_ts = time.time()

    async def defer(self, seconds: float) -> None:
        if seconds <= 0:
            return
        async with self._lock:
            self.next_allowed_ts = max(
                self.next_allowed_ts, time.time() + float(seconds)
            )

    async def on_response_code(self, code: int) -> float:
        """Update adaptive pacing; return cooldown seconds to sleep outside locks/semaphores."""
        cooldown = 0.0
        async with self._lock:
            if code == 429:
                self.consecutive += 1
                self.dynamic_sleep = min(
                    float(self._cfg.adaptive_max_sleep),
                    self.dynamic_sleep * float(self._cfg.adaptive_backoff_factor),
                )
                if self.consecutive >= int(self._cfg.hard_cooldown_429_count):
                    cooldown = float(self._cfg.hard_cooldown_seconds)
                    self.consecutive = 0
            else:
                self.consecutive = 0
                self.dynamic_sleep = max(
                    max(0.01, float(self._cfg.submit_sleep)),
                    self.dynamic_sleep * float(self._cfg.adaptive_recover_factor),
                )
        return cooldown


def _sim_payload(payload: dict) -> dict:
    return {
        "type": payload["type"],
        "settings": payload["settings"],
        "regular": payload["regular"],
    }


def _client_timeout(cfg: Any) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(
        total=float(cfg.timeout) + float(cfg.submit_timeout) + 30,
        connect=float(cfg.connect_timeout),
        sock_read=float(cfg.timeout),
    )


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, */*",
        "Content-Type": "application/json",
        "Origin": "https://platform.worldquantbrain.com",
    }


def _short_fail_reason(obj: dict | None) -> str:
    if not isinstance(obj, dict):
        return ""
    pools = [
        obj.get("is") if isinstance(obj.get("is"), dict) else obj,
        obj.get("summary"),
    ]
    parts: list[str] = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        checks = pool.get("checks")
        if isinstance(checks, list):
            for c in checks:
                if not isinstance(c, dict):
                    continue
                result = str(c.get("result") or c.get("status") or "").upper()
                if result not in ("FAIL", "FAILED", "ERROR", "REJECTED"):
                    continue
                name = str(c.get("name") or "CHECK").upper()
                value = c.get("value")
                limit = c.get("limit")
                parts.append(
                    f"{name}:{value}/{limit}"
                    if value is not None or limit is not None
                    else name
                )
        message = pool.get("message")
        if message:
            parts.append(str(message)[:120])
    return "; ".join(dict.fromkeys(parts))[:300]


def _build_ssl_connector(cfg: Any) -> aiohttp.TCPConnector:
    """Match sync ``requests`` TLS / IPv4 policy to avoid auth-OK-sync / fail-async splits."""
    if not cfg.tls_verify:
        ssl_param: bool | ssl.SSLContext = False
    else:
        ssl_param = ssl.create_default_context()
    fam = socket.AF_INET if cfg.force_ipv4 else socket.AF_UNSPEC
    n = max(1, int(getattr(cfg, "max_concurrent_simulations", 8) or 8))
    return aiohttp.TCPConnector(
        ssl=ssl_param,
        family=fam,
        limit=max(50, n * 4),
        enable_cleanup_closed=True,
        ttl_dns_cache=300,
    )


async def _authenticate(
    session: aiohttp.ClientSession, *, proxy: str | None, max_retries: int = 4
) -> None:
    from alpha_mining.auth.session_manager import (
        AuthSettings,
        ensure_authenticated_async,
    )

    kw: dict[str, Any] = {"proxy": proxy} if proxy else {}
    default_auth = getattr(session, "_default_auth", None)
    username = str(getattr(default_auth, "login", "") or "")
    settings = AuthSettings(
        state_path=os.environ.get("WQ_AUTH_STATE_FILE", ".wq_auth_state.json"),
        cooldown_seconds=float(os.environ.get("WQ_AUTH_COOLDOWN_SECONDS", "1500")),
        daily_cap=max(1, min(5, int(os.environ.get("WQ_AUTH_DAILY_CAP", "5")))),
        max_attempts=max(
            1, min(2, int(os.environ.get("WQ_AUTH_MAX_ATTEMPTS", str(max_retries))))
        ),
    )

    async def _login_once() -> aiohttp.ClientResponse:
        async with session.post(
            f"{BASE}/authentication",
            timeout=aiohttp.ClientTimeout(total=90),
            **kw,
        ) as response:
            await response.read()
            return response

    await ensure_authenticated_async(session, _login_once, username, settings)


async def probe_async_connection(cfg: Any) -> None:
    """Lightweight aiohttp connectivity check (used by --preflight)."""
    raw_proxy = (
        (str(cfg.https_proxy).strip() if cfg.https_proxy else "")
        or os.environ.get("HTTPS_PROXY", "")
        or os.environ.get("https_proxy", "")
    )
    proxy: str | None = raw_proxy.strip() or None
    auth = aiohttp.BasicAuth(cfg.username, cfg.password)
    connector = _build_ssl_connector(cfg)
    async with aiohttp.ClientSession(
        auth=auth,
        connector=connector,
        headers=_default_headers(),
        trust_env=True,
    ) as session:
        await _authenticate(session, proxy=proxy)


async def _submit_one(
    session: aiohttp.ClientSession,
    cfg: Any,
    payload: dict,
    sem: asyncio.Semaphore,
    rate: _Rate429,
    reauth_lock: asyncio.Lock,
    dns_state: list[int],
    proxy: str | None,
    stats: dict[str, int],
) -> _SubmitOutcome:
    sim_payload = _sim_payload(payload)
    last_err = ""
    reauthed = False
    for attempt in range(1, 1 + int(cfg.max_retries) + 1):
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
                return _SubmitOutcome(
                    payload, None, body, "submit_auth_failed:401", None
                )
            if code == 403:
                return _SubmitOutcome(
                    payload, None, body, f"submit_forbidden:{text[:400]}", None
                )
            if code == 400:
                return _SubmitOutcome(
                    payload, None, body, f"submit_bad_request:{text[:500]}", None
                )
            if code in (429, 500, 502, 503, 504):
                cooldown = await rate.on_response_code(code)
                ra = headers.get("Retry-After")
                parsed_retry_after = retry_after_seconds(ra)
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
                await asyncio.sleep(wait)
                continue
            await rate.on_response_code(code)
            if code >= 400:
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
                loc if loc.startswith("http") else urljoin(f"{BASE}/", loc.lstrip("/"))
            )
            stats["ok"] = stats.get("ok", 0) + 1
            return _SubmitOutcome(payload, progress_url, body, "ok", None)
        except aiohttp.ClientError as e:
            last_err = f"submit_error:{e}"
            if is_dns_error(e):
                dns_state[0] += 1
                if dns_state[0] >= int(cfg.dns_error_pause_count):
                    stats["failed"] = stats.get("failed", 0) + 1
                    return _SubmitOutcome(
                        payload, None, None, "network_dns_error_batch_paused", None
                    )
            if attempt <= int(cfg.max_retries):
                await asyncio.sleep(min(2 ** (attempt - 1), 10))
        except Exception as e:
            last_err = f"submit_error:{e}"
            if attempt <= int(cfg.max_retries):
                await asyncio.sleep(min(2 ** (attempt - 1), 10))
    stats["failed"] = stats.get("failed", 0) + 1
    return _SubmitOutcome(payload, None, None, last_err or "submit_failed", None)


async def _poll_progress(
    session: aiohttp.ClientSession,
    cfg: Any,
    progress_url: str,
    sem: asyncio.Semaphore,
    reauth_lock: asyncio.Lock,
    proxy: str | None,
) -> tuple[dict | None, str]:
    deadline = time.time() + float(cfg.max_poll_seconds_per_alpha)
    sleep_s = max(0.28, float(getattr(cfg, "poll_fallback_sleep", 0.75)))
    last_status = "polling"
    last_body: dict | None = None
    reauthed = False
    while time.time() < deadline:
        try:
            async with sem:
                get_kw: dict[str, Any] = {"timeout": _client_timeout(cfg)}
                if proxy:
                    get_kw["proxy"] = proxy
                async with session.get(progress_url, **get_kw) as pr:
                    text = await pr.text()
                    body = safe_json_text(text)
                    if pr.status == 401:
                        if reauthed:
                            return body, "poll_auth_failed:401"
                        reauthed = True
                        async with reauth_lock:
                            await _authenticate(session, proxy=proxy)
                        await asyncio.sleep(1.0)
                        continue
                    if pr.status == 403:
                        return body, f"poll_forbidden:{text[:300]}"
                    if pr.status == 429:
                        retry_after = retry_after_seconds(pr.headers.get("Retry-After"))
                        await asyncio.sleep(
                            retry_after
                            if retry_after > 0
                            else float(cfg.poll_error_sleep)
                        )
                        last_status = "poll_http_429"
                        continue
                    if pr.status >= 400:
                        last_status = f"poll_http_{pr.status}"
                        await asyncio.sleep(float(cfg.poll_error_sleep))
                        continue
                    if isinstance(body, dict):
                        last_body = body
                        status = str(
                            body.get("status") or body.get("state") or ""
                        ).lower()
                        if status:
                            last_status = status
                        if status in ("failed", "error", "rejected"):
                            return body, status
                        if alpha_id_from_progress(body):
                            return body, "ok"
                    await asyncio.sleep(sleep_s)
                    sleep_s = min(sleep_s * 1.12, 5.5)
        except aiohttp.ClientError as e:
            last_status = f"poll_error:{e}"
            await asyncio.sleep(float(cfg.poll_error_sleep))
    return last_body, f"poll_timeout:{last_status}"


async def _finalize_submit(
    session: aiohttp.ClientSession,
    cfg: Any,
    out: _SubmitOutcome,
    poll_sem: asyncio.Semaphore,
    reauth_lock: asyncio.Lock,
    proxy: str | None,
) -> tuple[dict | None, str]:
    if out.status == "network_dns_error_batch_paused":
        return out.sim_json, out.status
    if out.alpha_id_immediate:
        return out.sim_json, "ok"
    if out.status != "ok" or not out.progress_url:
        return out.sim_json, out.status or "submit_failed"
    body, st = await _poll_progress(
        session, cfg, out.progress_url, poll_sem, reauth_lock, proxy
    )
    return body, st


async def _await_with_heartbeat(
    awaitable: Any,
    label: str,
    interval: float = 25.0,
    status_fn: Any | None = None,
) -> Any:
    """Run an awaitable while printing a line every `interval` seconds so long phases are not silent."""
    stop = asyncio.Event()
    t0 = time.monotonic()

    async def _tick() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                status = f" {status_fn()}" if status_fn else ""
                print(
                    f"[simulate/async] {label} … {time.monotonic() - t0:.0f}s elapsed{status}"
                )

    hb = asyncio.create_task(_tick())
    try:
        return await awaitable
    finally:
        stop.set()
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb


async def run_async_simulation_batch(pipeline: Any, payloads: list[dict]) -> Any:
    """Run simulations with concurrent POST + concurrent polling; detail/check on thread pool."""
    import pandas as pd

    from alpha_mining.storage.sqlite_store import SqliteRunLog

    cfg = pipeline.cfg
    n = max(1, int(cfg.max_concurrent_simulations))
    submit_workers = max(
        1, min(n, int(getattr(cfg, "max_concurrent_simulation_posts", 1) or 1))
    )
    submit_sem = asyncio.Semaphore(submit_workers)
    poll_sem = asyncio.Semaphore(min(max(n + 10, 12), 48))
    reauth_lock = asyncio.Lock()
    rate = _Rate429(cfg)
    submit_stats: dict[str, int] = {
        "ok": 0,
        "retry_429": 0,
        "retry_5xx": 0,
        "failed": 0,
    }
    dns_state = [0]

    connector = _build_ssl_connector(cfg)
    raw_proxy = (
        (str(cfg.https_proxy).strip() if cfg.https_proxy else "")
        or os.environ.get("HTTPS_PROXY", "")
        or os.environ.get("https_proxy", "")
    )
    proxy: str | None = raw_proxy.strip() or None
    timeout = _client_timeout(cfg)
    auth = aiohttp.BasicAuth(cfg.username, cfg.password)
    sqlite_log = (
        SqliteRunLog(cfg.sqlite_runs_path)
        if getattr(cfg, "sqlite_runs_path", None)
        else None
    )

    rows: list[dict[str, Any]] = []
    idempotency_database = str(getattr(cfg, "sqlite_runs_path", "") or "").strip()
    if not idempotency_database:
        raise RuntimeError("sqlite_runs_path is required for simulation idempotency")
    unique_payloads = claim_simulation_payloads(idempotency_database, payloads)
    run_cap = (
        len(unique_payloads)
        if cfg.run_payload_cap is None
        else max(1, min(int(cfg.run_payload_cap), len(unique_payloads)))
    )
    run_payloads = unique_payloads[:run_cap]
    snap_exprs = len(getattr(pipeline, "_simulate_snapshot_exprs", None) or ())
    snap_ids = len(getattr(pipeline, "_simulate_snapshot_alpha_ids", None) or ())
    total_n = len(run_payloads)
    print(
        f"[simulate/async] START n={total_n} post={submit_workers} "
        f"poll={min(max(n + 10, 12), 48)} snapshot={snap_exprs}expr/{snap_ids}id"
    )

    combined: list[tuple[dict, _SubmitOutcome, dict | None, str]] = []
    poll_stats: dict[str, int] = {"scheduled": 0, "ok": 0, "failed": 0}
    poll_feedback_ids: set[str] = set()
    result_stats: dict[str, Any] = {"queue": Counter(), "invert": 0, "highlights": []}

    async with aiohttp.ClientSession(
        auth=auth,
        connector=connector,
        timeout=timeout,
        headers=_default_headers(),
        trust_env=True,
    ) as session:
        await _authenticate(session, proxy=proxy)
        print("[simulate/async] auth OK")

        submit_tasks = {
            asyncio.create_task(
                _submit_one(
                    session,
                    cfg,
                    p,
                    submit_sem,
                    rate,
                    reauth_lock,
                    dns_state,
                    proxy,
                    submit_stats,
                )
            )
            for p in run_payloads
        }
        poll_tasks: set[asyncio.Task[tuple[dict, _SubmitOutcome, dict | None, str]]] = (
            set()
        )

        platform_new_poll = 0

        async def _write_poll_feedback(
            payload: dict, body: dict | None, alpha_id: str
        ) -> None:
            if not alpha_id or alpha_id in poll_feedback_ids:
                return

            def _sync_write() -> None:
                detail = pipeline.fetch_alpha_detail(alpha_id)
                merged = merge_json_dicts(body, detail)
                pipeline._append_feedback(
                    payload,
                    alpha_id,
                    body,
                    "ok",
                    None,
                    "poll_only",
                    "poll_only:not_checked",
                    merged_json=merged,
                )

            await asyncio.to_thread(_sync_write)
            poll_feedback_ids.add(alpha_id)
            n_fb = len(poll_feedback_ids)
            if (
                n_fb == 1
                or n_fb == total_n
                or n_fb % max(50, int(cfg.save_every_n)) == 0
            ):
                print(f"[simulate/async] poll ledger {n_fb}/{total_n}")

        async def poll_from_submit(
            out: _SubmitOutcome,
        ) -> tuple[dict, _SubmitOutcome, dict | None, str]:
            nonlocal platform_new_poll
            body, st = await _finalize_submit(
                session, cfg, out, poll_sem, reauth_lock, proxy
            )
            if st == "ok":
                poll_stats["ok"] = poll_stats.get("ok", 0) + 1
                ok_n = poll_stats["ok"]
                aid = alpha_id_from_progress(body or {}) or out.alpha_id_immediate or ""
                expr_full = str(out.payload.get("regular") or "")
                is_new = getattr(pipeline, "is_platform_new_simulation", None)
                if callable(is_new) and is_new(aid, expr_full):
                    platform_new_poll += 1
                if aid:
                    await _write_poll_feedback(
                        out.payload, body if isinstance(body, dict) else None, aid
                    )
                if ok_n == 1 or ok_n == total_n or ok_n % 50 == 0:
                    print(
                        f"[simulate/async] poll {ok_n}/{total_n} ok new={platform_new_poll}"
                    )
            else:
                poll_stats["failed"] = poll_stats.get("failed", 0) + 1
                if poll_stats["failed"] <= 3:
                    expr = str(out.payload.get("regular") or "").replace("\n", " ")[:80]
                    print(f"[simulate/async] poll fail status={st} expr={expr}")
            return out.payload, out, body, st

        t0 = time.monotonic()
        while submit_tasks or poll_tasks:
            all_tasks: set[asyncio.Task[Any]] = set(submit_tasks) | set(poll_tasks)
            done, _pending = await asyncio.wait(
                all_tasks,
                timeout=120.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                post_ok = submit_stats.get("ok", 0)
                poll_ok = poll_stats.get("ok", 0)
                print(
                    f"[simulate/async] progress {time.monotonic() - t0:.0f}s "
                    f"POST {post_ok}/{total_n} POLL {poll_ok}/{total_n} "
                    f"429={submit_stats.get('retry_429', 0)}"
                )
                continue
            for task in done:
                if task in submit_tasks:
                    submit_tasks.remove(task)
                    try:
                        out = task.result()
                    except (aiohttp.ClientError, OSError) as e:
                        submit_stats["failed"] = submit_stats.get("failed", 0) + 1
                        print(f"[simulate/async] submit task error: {e}")
                        continue
                    if out.status == "ok" and (
                        out.progress_url or out.alpha_id_immediate
                    ):
                        poll_stats["scheduled"] = poll_stats.get("scheduled", 0) + 1
                        poll_tasks.add(asyncio.create_task(poll_from_submit(out)))
                    else:
                        combined.append(
                            (
                                out.payload,
                                out,
                                out.sim_json,
                                out.status or "submit_failed",
                            )
                        )
                        if out.status == "network_dns_error_batch_paused":
                            print(
                                "[simulate/async] DNS errors during submit; remaining submissions still tracked"
                            )
                        elif submit_stats.get("failed", 0) <= 5:
                            expr = str(out.payload.get("regular") or "").replace(
                                "\n", " "
                            )[:100]
                            print(
                                f"[simulate/async] submit not_ok status={out.status} expr={expr}"
                            )
                else:
                    poll_tasks.remove(task)
                    try:
                        combined.append(task.result())
                    except (aiohttp.ClientError, OSError) as e:
                        poll_stats["failed"] = poll_stats.get("failed", 0) + 1
                        print(f"[simulate/async] poll task error: {e}")

        print(
            f"[simulate/async] submit+poll done: post_ok={submit_stats.get('ok', 0)}/{len(run_payloads)} "
            f"poll_scheduled={poll_stats.get('scheduled', 0)} poll_ok={poll_stats.get('ok', 0)} "
            f"platform_new_poll={platform_new_poll} platform_resim_poll={max(0, poll_stats.get('ok', 0) - platform_new_poll)} "
            f"poll_failed={poll_stats.get('failed', 0)} 429={submit_stats.get('retry_429', 0)}"
        )

    check_sem = asyncio.Semaphore(max(2, min(6, max(1, n // 3))))

    async def detail_and_check(
        payload: dict, sim_json: dict | None, status: str
    ) -> tuple[str | None, dict | None, bool | None, str, dict | None, dict | None]:
        async with check_sem:
            if status == "network_dns_error_batch_paused":
                return None, sim_json, None, status, None, None
            body = sim_json if isinstance(sim_json, dict) else {}
            alpha_id = alpha_id_from_progress(body) if body else None
            if not alpha_id:
                return None, sim_json, None, status, None, None

            def fetch_detail() -> dict | None:
                return pipeline.fetch_alpha_detail(alpha_id)

            detail = await asyncio.to_thread(fetch_detail)

            # Fetch and persist daily PnL series (read-only endpoint, no simulation quota).
            def fetch_and_store_pnl() -> None:
                fetch_pnl = getattr(pipeline, "fetch_alpha_daily_pnl", None)
                if not callable(fetch_pnl) or sqlite_log is None:
                    return
                daily = fetch_pnl(alpha_id)
                if daily:
                    expr = str(payload.get("regular") or "")
                    sqlite_log.store_daily_returns(alpha_id, expr, daily)

            await asyncio.to_thread(fetch_and_store_pnl)
            merged = merge_json_dicts(sim_json, detail)
            merged = merge_feedback_metrics_snapshot(pipeline, alpha_id, merged)
            metrics = {
                "sharpe": to_float(metric_get(merged, "sharpe", "Sharpe")),
                "fitness": to_float(metric_get(merged, "fitness", "Fitness")),
                "turnover": to_float(metric_get(merged, "turnover", "Turnover")),
                "returns": to_float(metric_get(merged, "returns", "Returns")),
                "drawdown": to_float(metric_get(merged, "drawdown", "Drawdown")),
                "margin": to_float(metric_get(merged, "margin", "Margin")),
            }
            gate = getattr(pipeline, "_metric_gate", None)
            gate_ok, gate_note = (
                gate(metrics) if callable(gate) else (False, "missing_core_metrics")
            )

            def run_check_bounded() -> tuple[bool | None, dict | None, str]:
                wait_s: float | None = None
                initial_wait = getattr(pipeline, "_initial_simulate_check_wait", None)
                if callable(initial_wait):
                    wait_s = float(initial_wait(merged))
                if wait_s is None or wait_s <= 0.0:
                    return (
                        None,
                        None,
                        (
                            "skip_check:missing_core_metrics"
                            if gate_note == "missing_core_metrics"
                            else f"skip_check:{gate_note}"
                        ),
                    )
                return pipeline.check_alpha(alpha_id, max_wait_seconds=wait_s)

            check_passed, check_json, check_note = await asyncio.to_thread(
                run_check_bounded
            )
            merged = merge_json_dicts(merged, check_json)
            merged = merge_feedback_metrics_snapshot(pipeline, alpha_id, merged)
            return alpha_id, merged, check_passed, check_note, check_json, sim_json

    platform_new_detail = 0
    platform_resim_detail = 0

    async def _process_one_result(
        idx: int,
        payload: dict,
        sim_json: dict | None,
        status: str,
        ck: tuple,
    ) -> None:
        nonlocal platform_new_detail, platform_resim_detail
        alpha_id, merged, check_passed, check_note, check_json, raw_sim = ck
        expr = payload["regular"]
        if status == "ok" and alpha_id:
            if pipeline.is_platform_new_simulation(alpha_id, expr):
                platform_new_detail += 1
                pipeline.register_simulate_snapshot(alpha_id, expr)
            else:
                platform_resim_detail += 1
        profile = payload.get("meta", {}).get("profile", "?")
        sim_json_for_feedback = raw_sim if raw_sim is not None else sim_json

        if cfg.enable_auto_invert_retry and status == "ok" and alpha_id:
            sh = to_float(metric_get(merged, "sharpe", "Sharpe"))
            should_retry = (
                check_passed is not True and sh is not None and float(sh) < -0.15
            )
            if should_retry:
                inv_payload = {
                    "type": payload["type"],
                    "regular": pipeline._invert_expression(expr),
                    "settings": dict(payload.get("settings") or {}),
                    "meta": {
                        **dict(payload.get("meta") or {}),
                        "profile": f"{profile}:invert_retry",
                    },
                }

                def invert_sync() -> tuple[str | None, dict | None, str]:
                    return pipeline.submit_simulation(inv_payload)

                inv_alpha_id, inv_sim_json, inv_status = await asyncio.to_thread(
                    invert_sync
                )
                inv_check_passed: bool | None = None
                inv_check_note = ""
                inv_check_json: dict | None = None
                inv_merged = inv_sim_json
                if inv_alpha_id:

                    def inv_detail() -> dict | None:
                        return pipeline.fetch_alpha_detail(inv_alpha_id)

                    def inv_chk() -> tuple[bool | None, dict | None, str]:
                        initial_wait = getattr(
                            pipeline, "_initial_simulate_check_wait", None
                        )
                        wait_s = (
                            float(initial_wait(inv_merged))
                            if callable(initial_wait)
                            else max(
                                1.0,
                                float(
                                    getattr(cfg, "simulate_check_poll_seconds", 90.0)
                                    or 90.0
                                ),
                            )
                        )
                        return pipeline.check_alpha(
                            inv_alpha_id, max_wait_seconds=wait_s
                        )

                    inv_detail_d = await asyncio.to_thread(inv_detail)
                    inv_merged = merge_json_dicts(inv_sim_json, inv_detail_d)
                    (
                        inv_check_passed,
                        inv_check_json,
                        inv_check_note,
                    ) = await asyncio.to_thread(inv_chk)
                    inv_merged = merge_json_dicts(inv_merged, inv_check_json)
                base_score = (
                    to_float(metric_get(merged, "sharpe", "Sharpe")) or -999
                ) + (to_float(metric_get(merged, "fitness", "Fitness")) or -999)
                inv_score = (
                    to_float(metric_get(inv_merged, "sharpe", "Sharpe")) or -999
                ) + (to_float(metric_get(inv_merged, "fitness", "Fitness")) or -999)
                if inv_check_passed is True or inv_score > base_score:
                    payload = inv_payload
                    expr = inv_payload["regular"]
                    profile = inv_payload.get("meta", {}).get("profile", profile)
                    alpha_id, sim_json_for_feedback, status = (
                        inv_alpha_id,
                        inv_sim_json,
                        inv_status,
                    )
                    check_passed, check_json, check_note = (
                        inv_check_passed,
                        inv_check_json,
                        inv_check_note,
                    )
                    merged = inv_merged
                    result_stats["invert"] += 1

        queue_status, _ = pipeline.queue_decision(
            payload, alpha_id, merged, check_passed, check_note
        )
        if (
            alpha_id
            and alpha_id in poll_feedback_ids
            and hasattr(pipeline, "_upsert_feedback_by_alpha_id")
        ):
            pipeline._upsert_feedback_by_alpha_id(
                alpha_id,
                {
                    "utc_iso": utc_iso(),
                    "pipeline_version": str(getattr(cfg, "pipeline_version", "") or ""),
                    "expression": payload.get("regular", ""),
                    "family": (payload.get("meta") or {}).get("family", ""),
                    "source": (payload.get("meta") or {}).get("source", ""),
                    "profile": (payload.get("meta") or {}).get("profile", ""),
                    "status": status,
                    "queue_status": queue_status,
                    "check_passed": check_passed if check_passed is not None else "",
                    "check_note": check_note or "",
                    "Sharpe": to_float(metric_get(merged, "sharpe", "Sharpe")),
                    "Fitness": to_float(metric_get(merged, "fitness", "Fitness")),
                    "Turnover": to_float(metric_get(merged, "turnover", "Turnover")),
                    "Returns": to_float(metric_get(merged, "returns", "Returns")),
                    "Drawdown": to_float(metric_get(merged, "drawdown", "Drawdown")),
                    "Margin": to_float(metric_get(merged, "margin", "Margin")),
                    "Failure Reasons": pipeline.failure_reason_for_row(
                        merged,
                        sim_json=sim_json_for_feedback,
                        status=status,
                        check_note=check_note,
                    ),
                    "platform_check_json": __import__("json").dumps(check_json)[:8000]
                    if check_json
                    else "",
                },
            )
        else:
            pipeline._append_feedback(
                payload,
                alpha_id,
                sim_json_for_feedback,
                status,
                check_passed,
                check_note,
                queue_status,
                check_json=check_json,
                merged_json=merged,
            )
        sharpe = to_float(metric_get(merged, "sharpe", "Sharpe"))
        fitness = to_float(metric_get(merged, "fitness", "Fitness"))
        turnover = to_float(metric_get(merged, "turnover", "Turnover"))
        fail_text = pipeline.failure_reason_for_row(
            merged, sim_json=sim_json_for_feedback, status=status, check_note=check_note
        )
        result_note = (
            fail_text
            or _short_fail_reason(merged)
            or check_note
            or queue_status
            or status
        )
        result_stats["queue"][str(queue_status or status or "unknown")] += 1
        interesting = (
            check_passed is True
            or str(queue_status or "").startswith("needs_recheck")
            or status not in ("ok",)
        )
        if interesting and len(result_stats["highlights"]) < 12:
            result_stats["highlights"].append(
                f"{alpha_id or '-'} sh={sharpe} queue={queue_status} "
                f"{str(result_note).split(chr(10))[0][:80]}"
            )
        if sqlite_log:
            sqlite_log.append_row(
                utc_iso=utc_iso(),
                alpha_id=alpha_id or "",
                expression=str(expr or ""),
                status=str(status or ""),
                queue_status=queue_status,
                sharpe=sharpe,
                fitness=fitness,
                turnover=turnover,
                fail_reason=str(result_note)[:500],
            )
        rows.append(
            pipeline.build_simulate_row(
                index=idx,
                alpha_id=alpha_id,
                status=status,
                queue_status=queue_status,
                check_passed=check_passed,
                check_note=check_note,
                expression=expr,
                profile=profile,
                merged=merged,
                sim_json=sim_json_for_feedback,
            )
        )
        if len(rows) % int(cfg.save_every_n) == 0:
            pd.DataFrame(rows).to_csv(
                pipeline._path(f"{cfg.output_prefix}_checkpoint.csv"),
                index=False,
                encoding="utf-8-sig",
            )

    async def _run_detail_check_streaming() -> None:
        async def one(
            idx: int, payload: dict, sim_json: dict | None, status: str
        ) -> None:
            ck = await detail_and_check(payload, sim_json, status)
            await _process_one_result(idx, payload, sim_json, status, ck)

        tasks = [
            asyncio.create_task(one(idx, pl, sj, st))
            for idx, (pl, _o, sj, st) in enumerate(combined, start=1)
        ]
        done_n = 0
        total_tasks = len(tasks)
        milestone = max(1, total_tasks // 4)
        for task in asyncio.as_completed(tasks):
            await task
            done_n += 1
            if done_n in (1, total_tasks) or done_n % milestone == 0:
                print(f"[simulate/async] check {done_n}/{total_tasks}")

    print(f"[simulate/async] CHECK start n={len(combined)}")
    await _await_with_heartbeat(
        _run_detail_check_streaming(),
        "detail/check",
        interval=180.0,
    )
    q_summary = dict(result_stats["queue"].most_common(8))
    print(
        f"[simulate/async] RESULT n={len(rows)} queue={q_summary} "
        f"invert={result_stats['invert']}"
    )
    for hl in result_stats["highlights"][:8]:
        print(f"[simulate/async] highlight {hl}")

    df = __import__("pandas").DataFrame(rows)
    out = pipeline._path(f"{cfg.output_prefix}_results.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(
        f"[simulate/async] DONE rows={len(df)} saved={out.name} "
        f"poll_ok={poll_stats.get('ok', 0)} new={platform_new_detail} resim={platform_resim_detail}"
    )
    return df
