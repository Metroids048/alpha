#!/usr/bin/env python3
"""
simulate_queue.py  —  把 alpha_resim_queue.csv 里的 alpha 逐条 simulate 到 BRAIN 平台

用法:
    python simulate_queue.py
    python simulate_queue.py --csv my_queue.csv --sleep 5

功能:
    - 从 CSV 读取 PENDING 的 alpha，逐条 POST /simulations
    - 轮询直到平台完成，回写 new_alpha_id 和 DONE 状态
    - 自动处理 429 / CONCURRENT_LIMIT / 401（401时暂停等你更新cookie）
    - 断点续跑：已 DONE 的直接跳过
    - 每完成1条立即保存，不丢进度
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE = "https://api.worldquantbrain.com"
SIM_URL = f"{BASE}/simulations"
COOKIE_FILE = ".wq_browser_cookie.json"
COOKIE_NEXT_FILE = ".wq_browser_cookie.next.json"

# ── auth ─────────────────────────────────────────────────────────────────────

def load_cookie(path: str | Path = COOKIE_FILE) -> str:
    p = Path(path)
    if p.is_file():
        try:
            return str(json.loads(p.read_text(encoding="utf-8")).get("cookie") or "")
        except Exception:
            pass
    return ""


def save_cookie(cookie: str) -> None:
    Path(COOKIE_FILE).write_text(
        json.dumps({"cookie": cookie}, ensure_ascii=False), encoding="utf-8"
    )


def make_session(cookie: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, */*",
        "Content-Type": "application/json",
        "Origin": "https://platform.worldquantbrain.com",
        "Referer": "https://platform.worldquantbrain.com/",
        "Cookie": cookie,
    })
    return sess


def test_session(sess: requests.Session) -> bool:
    try:
        r = sess.get(f"{BASE}/users/self/alphas", params={"limit": 1}, timeout=(10, 20))
        return r.status_code == 200
    except Exception:
        return False


def _promote_next_cookie() -> str:
    """Consume standby cookie file if present and valid."""
    nxt = Path(COOKIE_NEXT_FILE)
    if not nxt.is_file():
        return ""
    cookie = load_cookie(nxt)
    if not cookie:
        return ""
    sess = make_session(cookie)
    if not test_session(sess):
        print(f"[cookie] 备用文件无效，保留 {COOKIE_NEXT_FILE}")
        return ""
    save_cookie(cookie)
    try:
        nxt.unlink()
    except OSError:
        pass
    print(f"[cookie] 已启用备用 cookie（{COOKIE_NEXT_FILE} → {COOKIE_FILE}）")
    return cookie


def refresh_cookie_interactive() -> str:
    """Prefer standby/file reload; only fall back to stdin when available."""
    print("\n" + "=" * 60)
    print("Cookie 已过期（401），尝试自动刷新")
    print("=" * 60)

    promoted = _promote_next_cookie()
    if promoted:
        return promoted

    # Re-read active file in case it was updated externally without killing the job
    reloaded = load_cookie()
    if reloaded and test_session(make_session(reloaded)):
        print(f"[cookie] 已从 {COOKIE_FILE} 重载")
        return reloaded

    # Non-interactive: poll for file updates so the agent can drop a new cookie
    # without terminating this process.
    if not sys.stdin.isatty():
        print(f"[cookie] 无终端输入；等待更新 {COOKIE_NEXT_FILE} 或 {COOKIE_FILE} …")
        deadline = time.time() + 30 * 60
        while time.time() < deadline:
            promoted = _promote_next_cookie()
            if promoted:
                return promoted
            reloaded = load_cookie()
            if reloaded and test_session(make_session(reloaded)):
                print(f"[cookie] 已从 {COOKIE_FILE} 重载")
                return reloaded
            time.sleep(5)
        print("[cookie] 等待超时，未拿到可用 cookie")
        return ""

    print("请在 BRAIN 平台 (https://platform.worldquantbrain.com)")
    print("按 F12 → Network → 点任意请求 → 复制 cookie: 请求头")
    print(f"或写入 {COOKIE_NEXT_FILE} / {COOKIE_FILE}")
    print("=" * 60)
    cookie = input("粘贴新的 cookie 值: ").strip()
    if cookie:
        save_cookie(cookie)
    return cookie

# ── settings helpers ─────────────────────────────────────────────────────────

def clean_settings(raw: str) -> dict:
    try:
        s = json.loads(raw or "{}")
    except Exception:
        s = {}
    for k, d in [("decay", 4), ("truncation", 0.05), ("delay", 1)]:
        if k in s:
            try:
                v = float(s[k])
                if math.isnan(v) or math.isinf(v):
                    s[k] = d
            except (TypeError, ValueError):
                s[k] = d
    return s

# ── CSV helpers ───────────────────────────────────────────────────────────────

FIELDS = [
    "alpha_id", "expression", "settings_json", "region", "universe",
    "delay", "neutralization", "decay", "truncation",
    "sharpe", "fitness", "turnover", "returns", "drawdown", "margin",
    "date_created", "new_alpha_id", "resim_status",
]


def load_queue(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [dict(r) for r in csv.DictReader(f)]


def save_queue(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

# ── simulate one alpha ────────────────────────────────────────────────────────

def post_simulation(sess: requests.Session, expr: str, settings: dict,
                    sleep_between: float) -> tuple[str | None, str]:
    """POST /simulations. Returns (progress_url, status). Handles rate/concurrent."""
    payload = {"type": "REGULAR", "regular": expr, "settings": settings}
    for attempt in range(12):
        try:
            time.sleep(sleep_between if attempt == 0 else 0)
            r = sess.post(SIM_URL, json=payload, timeout=(15, 90))
        except requests.RequestException as e:
            print(f"    [网络错误] {e}，等10s重试")
            time.sleep(10)
            continue

        if r.status_code in (200, 201, 202):
            loc = r.headers.get("Location", "").strip()
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            if not loc and isinstance(body, dict):
                for k in ("location", "url", "href"):
                    v = body.get(k, "")
                    if isinstance(v, str) and v.strip():
                        loc = v.strip()
                        break
            if loc:
                url = loc if loc.startswith("http") else urljoin(f"{BASE}/", loc.lstrip("/"))
                return url, "ok"
            # alpha completed immediately
            for k in ("id", "alphaId", "alpha_id"):
                v = body.get(k)
                if isinstance(v, str) and len(v) > 4:
                    return f"{BASE}/alphas/{v}", "ok"
            return None, "no_location"

        if r.status_code == 401:
            return None, "auth_expired"

        if r.status_code == 400:
            return None, f"bad_request:{r.text[:120]}"

        body_text = r.text
        if "CONCURRENT" in body_text or r.status_code == 409:
            wait = 20 + attempt * 10
            print(f"    [并发限制] 等 {wait}s (第{attempt+1}次)…")
            time.sleep(wait)
            continue

        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 30))
            print(f"    [速率限制 429] 等 {wait:.0f}s…")
            time.sleep(wait)
            continue

        return None, f"http_{r.status_code}"

    return None, "failed_after_retries"


def poll_simulation(sess: requests.Session, progress_url: str,
                    max_wait: float = 360.0) -> tuple[str | None, str]:
    """Poll until alpha_id appears or timeout."""
    deadline = time.time() + max_wait
    sleep_s = 3.0
    while time.time() < deadline:
        try:
            r = sess.get(progress_url, timeout=(10, 30))
            if r.status_code == 200:
                body = r.json()
                status = str(body.get("status") or body.get("state") or "").lower()
                if status in ("failed", "error", "rejected"):
                    return None, f"platform_{status}"
                for k in ("id", "alphaId", "alpha_id"):
                    v = body.get(k)
                    if isinstance(v, str) and len(v) > 4:
                        return v.strip(), "ok"
        except Exception:
            pass
        time.sleep(sleep_s)
        sleep_s = min(sleep_s * 1.1, 8.0)
    return None, "poll_timeout"

# ── main loop ─────────────────────────────────────────────────────────────────

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _passes_threshold(rec: dict, min_sharpe: float, min_fitness: float,
                       min_turnover: float, max_turnover: float) -> bool:
    """Return True only when all three metrics are present and within bounds."""
    try:
        sh = float(rec.get("sharpe") or 0)
        fi = float(rec.get("fitness") or 0)
    except (TypeError, ValueError):
        return False
    if sh <= min_sharpe or fi <= min_fitness:
        return False
    to_raw = str(rec.get("turnover") or "").strip()
    if to_raw:
        try:
            to = float(to_raw)
            if to < min_turnover or to > max_turnover:
                return False
        except (TypeError, ValueError):
            pass
    return True


def run(csv_path: str, sleep_between: float, max_poll: float,
        min_sharpe: float = 1.57, min_fitness: float = 1.0,
        min_turnover: float = 0.01, max_turnover: float = 0.70) -> None:
    queue = load_queue(csv_path)
    total = len(queue)
    all_pending = [r for r in queue if r.get("resim_status", "PENDING") == "PENDING"]

    # Filter by composite threshold: Sharpe > min_sharpe AND Fitness > min_fitness
    # AND Turnover in [min_turnover, max_turnover]
    pending = [r for r in all_pending
               if _passes_threshold(r, min_sharpe, min_fitness, min_turnover, max_turnover)]
    skipped_below = len(all_pending) - len(pending)

    # Mark below-threshold rows as SKIP so they won't be attempted again
    if skipped_below > 0:
        idx_map = {r["alpha_id"]: i for i, r in enumerate(queue)}
        for rec in all_pending:
            if not _passes_threshold(rec, min_sharpe, min_fitness, min_turnover, max_turnover):
                aid = rec.get("alpha_id", "")
                if aid and aid in idx_map:
                    queue[idx_map[aid]]["resim_status"] = "SKIP:below_threshold"
        save_queue(csv_path, queue)

    done_before = total - len(all_pending)

    print(f"\n{'='*60}")
    print(f"  simulate_queue.py  —  {utc_now()}")
    print(f"  CSV:     {csv_path}")
    print(f"  总条数:  {total}  |  已完成: {done_before}  |  待提交: {len(pending)}")
    print(f"  门槛:    Sharpe>{min_sharpe}  Fitness>{min_fitness}  Turnover {min_turnover:.0%}-{max_turnover:.0%}")
    if skipped_below > 0:
        print(f"  跳过(不达门槛): {skipped_below}  → 已标记 SKIP:below_threshold")
    print(f"  提交间隔: {sleep_between}s  |  轮询超时: {max_poll}s")
    print(f"{'='*60}\n")

    if not pending:
        print("没有待提交的 alpha，全部已完成！")
        return

    cookie = load_cookie()
    sess = make_session(cookie)

    if not test_session(sess):
        print("现有 cookie 无效，请提供新 cookie：")
        cookie = refresh_cookie_interactive()
        sess = make_session(cookie)
        if not test_session(sess):
            print("Cookie 验证失败，退出。")
            sys.exit(1)

    print(f"Cookie 验证OK，开始提交…\n")

    idx_map = {r["alpha_id"]: i for i, r in enumerate(queue)}
    ok_count = 0
    fail_count = 0
    auth_expired = False

    for n, rec in enumerate(pending, 1):
        aid = rec["alpha_id"]
        expr = (rec.get("expression") or "").strip()

        if not expr:
            queue[idx_map[aid]]["resim_status"] = "FAIL:no_expression"
            fail_count += 1
            continue

        settings = clean_settings(rec.get("settings_json", "{}"))

        prefix = f"[{n}/{len(pending)}]"
        print(f"{prefix} {aid}  sh={rec.get('sharpe','?')}  ft={rec.get('fitness','?')}", end="  ", flush=True)

        # POST
        progress_url, post_status = post_simulation(sess, expr, settings, sleep_between)

        if post_status == "auth_expired":
            print("\n⚠️  Cookie 过期（401），需要刷新")
            cookie = refresh_cookie_interactive()
            sess = make_session(cookie)
            if not test_session(sess):
                print("Cookie 无效，停止。下次从断点继续运行。")
                break
            # retry this one
            progress_url, post_status = post_simulation(sess, expr, settings, sleep_between)

        if not progress_url:
            print(f"FAIL: {post_status}")
            queue[idx_map[aid]]["resim_status"] = f"FAIL:{post_status}"
            fail_count += 1
            save_queue(csv_path, queue)
            continue

        # Poll
        print(f"提交OK, 等待完成…", end="  ", flush=True)
        new_aid, poll_status = poll_simulation(sess, progress_url, max_wait=max_poll)

        if new_aid:
            queue[idx_map[aid]]["new_alpha_id"] = new_aid
            queue[idx_map[aid]]["resim_status"] = "DONE"
            ok_count += 1
            done_total = done_before + ok_count
            print(f"✓  new_id={new_aid}  (总完成:{done_total}/{total})")
        else:
            queue[idx_map[aid]]["resim_status"] = f"FAIL:{poll_status}"
            fail_count += 1
            print(f"FAIL poll: {poll_status}")

        # Save after every completion
        save_queue(csv_path, queue)

    print(f"\n{'='*60}")
    print(f"本次结束: ok={ok_count}  fail={fail_count}")
    final_done = sum(1 for r in queue if r.get("resim_status") == "DONE")
    print(f"CSV 总完成: {final_done}/{total}")
    remaining = sum(1 for r in queue if r.get("resim_status", "PENDING") == "PENDING")
    if remaining:
        print(f"剩余 PENDING: {remaining}  —  直接重新运行即可续跑")
    else:
        print("全部完成！")
    print(f"{'='*60}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="逐条 simulate BRAIN alpha 队列（只提交通过门槛的行）")
    ap.add_argument("--csv",   default="alpha_resim_queue.csv", help="队列CSV路径")
    ap.add_argument("--sleep", type=float, default=3.0, help="每次提交间隔秒数 (默认3s)")
    ap.add_argument("--poll",  type=float, default=360.0, help="每个alpha最长等待秒数 (默认360s)")
    ap.add_argument("--min-sharpe",   type=float, default=1.57, help="Sharpe 最低门槛 (默认1.57，严格>)")
    ap.add_argument("--min-fitness",  type=float, default=1.0,  help="Fitness 最低门槛 (默认1.0，严格>)")
    ap.add_argument("--min-turnover", type=float, default=0.01, help="Turnover 最低 (默认1%%)")
    ap.add_argument("--max-turnover", type=float, default=0.70, help="Turnover 最高 (默认70%%)")
    args = ap.parse_args()

    if not Path(args.csv).is_file():
        print(f"找不到队列文件: {args.csv}")
        sys.exit(1)

    run(args.csv, args.sleep, args.poll,
        min_sharpe=args.min_sharpe,
        min_fitness=args.min_fitness,
        min_turnover=args.min_turnover,
        max_turnover=args.max_turnover)


if __name__ == "__main__":
    main()
