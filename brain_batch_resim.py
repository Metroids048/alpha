"""brain_batch_resim.py - full fetch + async batch resimulate

Steps:
  1. Fetch all UNSUBMITTED alphas (paginated, complete)
  2. Filter locally: sharpe>1.24 and fitness>1
  3. Write alpha_resim_queue.csv with all submission params
  4. Async concurrent clone+re-simulate
  5. Real-time update CSV with new_alpha_id
  6. Final count verification
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp
import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth

BASE = "https://api.worldquantbrain.com"
SELF_ALPHA_URL = f"{BASE}/users/self/alphas"
SIM_URL = f"{BASE}/simulations"
PAGE_SIZE = 100
QUEUE_CSV = "alpha_resim_queue.csv"
LOG_FILE = "brain_batch_resim.log"

CSV_FIELDS = [
    "alpha_id", "expression", "settings_json",
    "region", "universe", "delay", "neutralization", "decay", "truncation",
    "sharpe", "fitness", "turnover", "margin", "returns", "drawdown",
    "date_created", "new_alpha_id", "resim_status",
]


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    ts = _utc()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load_env() -> None:
    env = Path(__file__).resolve().parent / ".env"
    if not env.is_file():
        return
    for raw in env.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k and v:
            os.environ.setdefault(k, v)
            if k in ("WQ_USERNAME", "WQ_PASSWORD"):
                os.environ[k] = v


def _creds() -> tuple[str, str]:
    u = os.environ.get("WQ_USERNAME", "").strip()
    p = os.environ.get("WQ_PASSWORD", "").strip()
    if not u or not p:
        raise SystemExit("ERROR: set WQ_USERNAME and WQ_PASSWORD in .env")
    return u, p


def _metric(obj: dict | None, *keys: str) -> float | None:
    if not isinstance(obj, dict):
        return None
    for pool in [obj, obj.get("is"), obj.get("summary")]:
        if not isinstance(pool, dict):
            continue
        for k in keys:
            v = pool.get(k)
            if v is not None:
                try:
                    return float(v)
                except Exception:
                    pass
    return None


def _expr(row: dict) -> str:
    r = row.get("regular")
    if isinstance(r, dict):
        return str(r.get("code") or r.get("regular") or "").strip()
    if isinstance(r, str):
        return r.strip()
    return ""


def _settings(row: dict) -> dict:
    s = row.get("settings")
    return s if isinstance(s, dict) else {}


# ---- sync fetch phase -------------------------------------------------------

class SyncClient:
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, */*",
        "Content-Type": "application/json",
        "Origin": "https://platform.worldquantbrain.com",
    }

    def __init__(self, username: str, password: str) -> None:
        self._u = username
        self._p = password
        self._sess = requests.Session()
        self._sess.auth = HTTPBasicAuth(username, password)
        self._sess.headers.update(self._HEADERS)
        self._sess.trust_env = True
        proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()
        if proxy:
            self._sess.proxies["https"] = proxy

    def auth(self) -> None:
        r = self._sess.post(f"{BASE}/authentication", timeout=(15, 60))
        if r.status_code not in (200, 201):
            raise SystemExit(f"Auth failed HTTP {r.status_code}: {r.text[:300]}")
        _log(f"[auth] OK user={self._u[:4]}***")

    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(5):
            try:
                r = self._sess.get(url, params=params, timeout=(15, 90))
                if r.status_code == 401:
                    self._sess.post(f"{BASE}/authentication", timeout=(15, 60))
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = float(r.headers.get("Retry-After") or min(2 ** attempt, 60))
                    _log(f"  [retry] HTTP {r.status_code} sleep={wait:.0f}s")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                if attempt == 4:
                    raise
                time.sleep(min(2 ** attempt, 30))
        return {}

    def fetch_all_unsubmitted(
        self, min_sharpe: float = 1.24, min_fitness: float = 1.0, page_sleep: float = 0.5
    ) -> list[dict]:
        _log(f"[fetch] 开始拉取 UNSUBMITTED (sharpe>{min_sharpe} fitness>{min_fitness})")

        # First page to get total
        first = self._get(SELF_ALPHA_URL, params={
            "limit": PAGE_SIZE, "offset": 0, "order": "-dateCreated", "status": "UNSUBMITTED"
        })
        declared = int(first.get("count") or 0)
        _log(f"[fetch] API声明 UNSUBMITTED 总数: {declared}")

        if declared == 0:
            _log("[fetch] 无UNSUBMITTED alpha")
            return []

        all_rows: list[dict] = list(first.get("results") or [])
        failed_offsets: list[int] = []

        while len(all_rows) < declared:
            offset = len(all_rows)
            try:
                time.sleep(page_sleep)
                page = self._get(SELF_ALPHA_URL, params={
                    "limit": PAGE_SIZE, "offset": offset,
                    "order": "-dateCreated", "status": "UNSUBMITTED"
                })
                results = page.get("results") or []
                if not results:
                    _log(f"[fetch] offset={offset} 返回空，停止")
                    break
                all_rows.extend(results)
                pct = 100.0 * len(all_rows) / max(declared, 1)
                if len(all_rows) % 500 < PAGE_SIZE or len(all_rows) >= declared:
                    _log(f"[fetch] 已拉 {len(all_rows)}/{declared} ({pct:.0f}%)")
            except Exception as e:
                _log(f"[fetch] ⚠️ offset={offset} failed: {e}")
                failed_offsets.append(offset)
                time.sleep(5)

        if failed_offsets:
            _log(f"[fetch] 重试失败分页: {failed_offsets}")
            for off in list(failed_offsets):
                try:
                    time.sleep(3)
                    page = self._get(SELF_ALPHA_URL, params={
                        "limit": PAGE_SIZE, "offset": off,
                        "order": "-dateCreated", "status": "UNSUBMITTED"
                    })
                    results = page.get("results") or []
                    all_rows.extend(results)
                    failed_offsets.remove(off)
                    _log(f"[fetch] 重试offset={off} OK +{len(results)}")
                except Exception as e:
                    _log(f"[fetch] 重试offset={off} 仍失败: {e}")

        # Dedup
        seen: dict[str, dict] = {}
        for r in all_rows:
            if isinstance(r, dict):
                aid = str(r.get("id") or r.get("alphaId") or "").strip()
                if aid:
                    seen[aid] = r

        actual = len(seen)
        _log(f"[fetch] 拉取完成: 实际{actual} / 声明{declared}")
        if actual < declared * 0.95:
            _log(f"[fetch] ⚠️ 警告: 实际条数({actual})比声明({declared})少超5%! 失败分页: {failed_offsets}")

        # Filter by metrics
        qualified: list[dict] = []
        for row in seen.values():
            sh = _metric(row, "sharpe", "Sharpe")
            ft = _metric(row, "fitness", "Fitness")
            if sh is not None and ft is not None and sh > min_sharpe and ft >= min_fitness:
                qualified.append(row)

        _log(f"[fetch] sharpe>{min_sharpe} & fitness>={min_fitness}: {len(qualified)} / {actual}")
        return qualified


def build_record(row: dict) -> dict:
    aid = str(row.get("id") or row.get("alphaId") or "").strip()
    expr = _expr(row)
    setts = _settings(row)

    def _s(k: str) -> str:
        v = setts.get(k) or row.get(k)
        return str(v) if v is not None else ""

    def _i(k: str) -> str:
        v = setts.get(k) or row.get(k)
        return str(int(v)) if v is not None else ""

    def _f(k: str) -> str:
        v = setts.get(k) or row.get(k)
        try:
            return str(float(v)) if v is not None else ""
        except Exception:
            return ""

    return {
        "alpha_id":      aid,
        "expression":    expr,
        "settings_json": json.dumps(setts, ensure_ascii=False, separators=(",", ":")),
        "region":        _s("region"),
        "universe":      _s("universe"),
        "delay":         _i("delay"),
        "neutralization": _s("neutralization"),
        "decay":         _i("decay"),
        "truncation":    _f("truncation"),
        "sharpe":        str(_metric(row, "sharpe", "Sharpe") or ""),
        "fitness":       str(_metric(row, "fitness", "Fitness") or ""),
        "turnover":      str(_metric(row, "turnover", "Turnover") or ""),
        "margin":        str(_metric(row, "margin", "Margin") or ""),
        "returns":       str(_metric(row, "returns", "Returns") or ""),
        "drawdown":      str(_metric(row, "drawdown", "Drawdown") or ""),
        "date_created":  str(row.get("dateCreated") or row.get("date_created") or ""),
        "new_alpha_id":  "",
        "resim_status":  "PENDING",
    }


def save_queue_csv(records: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    _log(f"[queue] 已写入 {len(records)} 条 -> {path}")


def load_queue_csv(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(dict(r))
    return rows


def _update_row_in_csv(path: str, alpha_id: str, new_alpha_id: str, status: str) -> None:
    rows = load_queue_csv(path)
    for r in rows:
        if r["alpha_id"] == alpha_id:
            r["new_alpha_id"] = new_alpha_id
            r["resim_status"] = status
            break
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---- async sim phase -------------------------------------------------------

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, */*",
    "Content-Type": "application/json",
    "Origin": "https://platform.worldquantbrain.com",
}


def _alpha_id_from_body(body: dict | None) -> str | None:
    if not isinstance(body, dict):
        return None
    for key in ("id", "alphaId", "alpha_id", "alpha"):
        v = body.get(key)
        if isinstance(v, str) and len(v) > 4:
            return v.strip()
    child = body.get("alpha") if isinstance(body.get("alpha"), dict) else None
    if child:
        return _alpha_id_from_body(child)
    return None


async def _async_auth(session: aiohttp.ClientSession, proxy: str | None = None) -> None:
    kw: dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=60)}
    if proxy:
        kw["proxy"] = proxy
    async with session.post(f"{BASE}/authentication", **kw) as r:
        await r.read()
        if r.status not in (200, 201):
            _log(f"[async-auth] WARN HTTP {r.status}")


async def _post_one(
    session: aiohttp.ClientSession,
    payload: dict,
    sem: asyncio.Semaphore,
    proxy: str | None,
    reauth_lock: asyncio.Lock,
    rate_state: dict,
) -> tuple[str | None, str]:
    """POST /simulations, return (progress_url, status)."""
    for attempt in range(5):
        # Rate limit
        now = asyncio.get_event_loop().time()
        sleep_needed = max(0.0, rate_state["submit_sleep"] - (now - rate_state["last_submit_ts"]))
        if sleep_needed > 0:
            await asyncio.sleep(sleep_needed)
        rate_state["last_submit_ts"] = asyncio.get_event_loop().time()

        try:
            async with sem:
                kw: dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=120)}
                if proxy:
                    kw["proxy"] = proxy
                async with session.post(SIM_URL, json=payload, **kw) as resp:
                    text = await resp.text()
                    code = resp.status
                    headers = dict(resp.headers)
                    try:
                        body = json.loads(text) if text else {}
                    except Exception:
                        body = {}

            if code == 401:
                async with reauth_lock:
                    await _async_auth(session, proxy)
                continue
            if code == 400:
                return None, f"bad_request:{text[:200]}"
            if code == 403:
                return None, f"forbidden:{text[:200]}"
            if code in (429, 500, 502, 503, 504):
                wait = float(headers.get("Retry-After") or min(2 ** attempt, 60))
                if code == 429:
                    rate_state["submit_sleep"] = min(rate_state["submit_sleep"] * 1.5, 30.0)
                await asyncio.sleep(wait)
                continue

            # Success
            rate_state["submit_sleep"] = max(
                rate_state["min_sleep"],
                rate_state["submit_sleep"] * 0.95,
            )
            loc = headers.get("Location", "").strip()
            if not loc and isinstance(body, dict):
                for k in ("location", "url", "href"):
                    v = body.get(k, "")
                    if isinstance(v, str) and v.strip():
                        loc = v.strip()
                        break
            if loc:
                url = loc if loc.startswith("http") else urljoin(f"{BASE}/", loc.lstrip("/"))
                return url, "ok"
            aid = _alpha_id_from_body(body if isinstance(body, dict) else None)
            if aid:
                return f"{BASE}/alphas/{aid}", "ok"
            return None, "missing_location"

        except aiohttp.ClientError as e:
            if attempt == 4:
                return None, f"client_error:{e}"
            await asyncio.sleep(min(2 ** attempt, 15))
    return None, "submit_failed"


async def _poll_one(
    session: aiohttp.ClientSession,
    progress_url: str,
    poll_sem: asyncio.Semaphore,
    proxy: str | None,
    max_wait: float = 480.0,
) -> tuple[str | None, str]:
    """Poll until alpha_id appears or timeout. Returns (alpha_id, status)."""
    deadline = asyncio.get_event_loop().time() + max_wait
    sleep_s = 1.5
    while asyncio.get_event_loop().time() < deadline:
        try:
            async with poll_sem:
                kw: dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=60)}
                if proxy:
                    kw["proxy"] = proxy
                async with session.get(progress_url, **kw) as resp:
                    code = resp.status
                    text = await resp.text()
                    try:
                        body = json.loads(text) if text else {}
                    except Exception:
                        body = {}

            if code == 404:
                return None, "not_found"
            if code >= 400:
                await asyncio.sleep(sleep_s)
                continue

            if isinstance(body, dict):
                status = str(body.get("status") or body.get("state") or "").lower()
                if status in ("failed", "error", "rejected"):
                    return None, f"failed:{status}"
                aid = _alpha_id_from_body(body)
                if aid:
                    return aid, "ok"
        except Exception:
            pass
        await asyncio.sleep(sleep_s)
        sleep_s = min(sleep_s * 1.1, 8.0)
    return None, "poll_timeout"


async def run_async_resim(
    queue: list[dict],
    username: str,
    password: str,
    *,
    queue_csv: str = QUEUE_CSV,
    submit_sleep: float = 2.0,
    max_poll: float = 480.0,
    max_concurrent_polls: int = 20,
) -> dict[str, int]:
    """
    Async concurrent resimulation.
    Returns stats dict.
    """
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip() or None
    ssl_ctx = ssl.create_default_context()
    try:
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except Exception:
        pass

    connector = aiohttp.TCPConnector(
        ssl=ssl_ctx,
        limit=max(50, max_concurrent_polls + 5),
        enable_cleanup_closed=True,
        ttl_dns_cache=300,
    )
    auth = aiohttp.BasicAuth(username, password)
    rate_state: dict[str, float] = {
        "submit_sleep": submit_sleep,
        "min_sleep": submit_sleep,
        "last_submit_ts": 0.0,
    }
    submit_sem = asyncio.Semaphore(1)  # 1 concurrent POST at a time
    poll_sem = asyncio.Semaphore(max_concurrent_polls)
    reauth_lock = asyncio.Lock()

    stats = {"ok": 0, "fail": 0, "skip": 0}
    completed = 0
    total = len(queue)
    csv_lock = asyncio.Lock()

    async def _process_one(rec: dict) -> None:
        nonlocal completed
        alpha_id = rec["alpha_id"]
        expr = rec["expression"]
        try:
            setts = json.loads(rec.get("settings_json") or "{}")
        except Exception:
            setts = {}

        if not expr:
            stats["skip"] += 1
            completed += 1
            return

        payload = {"type": "REGULAR", "regular": {"code": expr}, "settings": setts}

        progress_url, sub_status = await _post_one(
            session, payload, submit_sem, proxy, reauth_lock, rate_state
        )

        if not progress_url:
            stats["fail"] += 1
            async with csv_lock:
                _update_row_in_csv(queue_csv, alpha_id, "", f"FAIL:{sub_status}")
            completed += 1
            if stats["fail"] <= 5 or stats["fail"] % 50 == 0:
                _log(f"  [fail] {alpha_id} post_status={sub_status}")
            return

        new_aid, poll_status = await _poll_one(
            session, progress_url, poll_sem, proxy, max_wait=max_poll
        )

        if new_aid:
            stats["ok"] += 1
            async with csv_lock:
                _update_row_in_csv(queue_csv, alpha_id, new_aid, "DONE")
        else:
            stats["fail"] += 1
            async with csv_lock:
                _update_row_in_csv(queue_csv, alpha_id, "", f"FAIL:{poll_status}")

        completed += 1
        if completed % 50 == 0 or completed == total or completed == 1:
            _log(
                f"[resim] {completed}/{total} ok={stats['ok']} fail={stats['fail']} "
                f"rate_sleep={rate_state['submit_sleep']:.1f}s"
            )

    async with aiohttp.ClientSession(
        auth=auth, connector=connector, headers=_DEFAULT_HEADERS, trust_env=True
    ) as session:
        await _async_auth(session, proxy)
        _log(f"[async-auth] OK, starting {total} simulations")

        tasks = [asyncio.create_task(_process_one(rec)) for rec in queue]
        await asyncio.gather(*tasks)

    return stats


# ---- main ------------------------------------------------------------------

def main() -> None:
    _log("[blocked] legacy resimulation writer disabled; use python -m alpha_mining legacy triage")
    return
    _load_env()
    username, password = _creds()

    ap = argparse.ArgumentParser(
        description="BRAIN全量拉取+批量重模拟 (目标2565个alpha)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--fetch-only",    action="store_true", help="只拉取写CSV，不模拟")
    ap.add_argument("--sim-only",      action="store_true", help="只模拟，从CSV加载队列")
    ap.add_argument("--min-sharpe",    type=float, default=1.24, help="最低Sharpe过滤 (默认1.24)")
    ap.add_argument("--min-fitness",   type=float, default=1.0,  help="最低Fitness过滤 (默认1.0)")
    ap.add_argument("--submit-sleep",  type=float, default=2.0,  help="提交间隔秒 (默认2.0)")
    ap.add_argument("--max-poll",      type=float, default=480.0, help="每个alpha最长等待秒 (默认480)")
    ap.add_argument("--concurrency",   type=int,   default=20,   help="并发poll数 (默认20)")
    ap.add_argument("--csv",           default=QUEUE_CSV, help=f"队列CSV路径 (默认{QUEUE_CSV})")
    ap.add_argument("--page-sleep",    type=float, default=0.4,  help="翻页间隔秒 (默认0.4)")
    args = ap.parse_args()

    _log(f"=== brain_batch_resim START ===")

    queue: list[dict] = []

    if not args.sim_only:
        client = SyncClient(username, password)
        client.auth()
        qualified = client.fetch_all_unsubmitted(
            min_sharpe=args.min_sharpe,
            min_fitness=args.min_fitness,
            page_sleep=args.page_sleep,
        )
        if not qualified:
            _log("ERROR: 未找到符合条件的alpha，请检查账号或参数")
            sys.exit(1)

        records = [build_record(r) for r in qualified]
        save_queue_csv(records, args.csv)
        _log(f"[queue] CSV已保存: {args.csv}  共 {len(records)} 条")

        if args.fetch_only:
            _log("=== fetch-only mode, done ===")
            return
        queue = records
    else:
        if not Path(args.csv).is_file():
            _log(f"ERROR: CSV不存在: {args.csv}  请先运行不带--sim-only以生成队列")
            sys.exit(1)
        queue = load_queue_csv(args.csv)
        _log(f"[queue] 从CSV加载 {len(queue)} 条")

    # Skip already done
    pending = [r for r in queue if r.get("resim_status", "PENDING") not in ("DONE",)]
    already_done = len(queue) - len(pending)
    if already_done:
        _log(f"[queue] 跳过已完成: {already_done}条  待模拟: {len(pending)}条")
    else:
        _log(f"[queue] 待模拟: {len(pending)}条")

    if not pending:
        _log("所有alpha已模拟完毕")
        _final_report(queue, args.csv)
        return

    _log(f"[resim] 开始异步并发模拟 concurrency={args.concurrency} submit_sleep={args.submit_sleep}s")
    _log(f"[resim] 预估时间: ~{len(pending)*args.submit_sleep/3600:.1f}h (实际取决于平台速度)")

    stats = asyncio.run(run_async_resim(
        pending, username, password,
        queue_csv=args.csv,
        submit_sleep=args.submit_sleep,
        max_poll=args.max_poll,
        max_concurrent_polls=args.concurrency,
    ))

    _log(f"[resim] DONE ok={stats['ok']} fail={stats['fail']} skip={stats['skip']}")
    _final_report(load_queue_csv(args.csv), args.csv)


def _final_report(queue: list[dict], csv_path: str) -> None:
    total = len(queue)
    done = sum(1 for r in queue if r.get("resim_status") == "DONE")
    fail = sum(1 for r in queue if str(r.get("resim_status","")).startswith("FAIL"))
    pending = total - done - fail
    _log(f"\n{'='*60}")
    _log(f"最终报告  CSV: {csv_path}")
    _log(f"  队列总数:    {total}")
    _log(f"  模拟成功:    {done}  {'✅' if done >= total*0.95 else '⚠️ 未达标'}")
    _log(f"  模拟失败:    {fail}")
    _log(f"  仍pending:   {pending}")
    if done < total * 0.95:
        _log(f"  ⚠️ 注意: 成功率{100*done/max(total,1):.1f}% 低于95%，建议用--sim-only重跑失败部分")
    _log(f"{'='*60}")


if __name__ == "__main__":
    main()
