"""brain_scan_pipeline.py — BRAIN全量扫描+筛选+重模拟流水线

CLI subcommands:
    fetch-all      分页拉取账号下所有UNSUBMITTED alpha，严禁漏数据
    sync           fetch-all别名（增量合并，不覆盖本地历史）
    filter-local   本地粗筛（sharpe/fitness/turnover阈值，可CLI配置）
    verify-checks  对CANDIDATE逐个调平台check接口，拿权威判定
    aggregate      汇总SUBMITTABLE到CSV
    resimulate     对SUBMITTABLE批量clone+re-simulate，对齐当前IS周期

Usage:
    python brain_scan_pipeline.py fetch-all
    python brain_scan_pipeline.py filter-local --min-sharpe 1.3 --max-turnover 0.6
    python brain_scan_pipeline.py verify-checks
    python brain_scan_pipeline.py aggregate
    python brain_scan_pipeline.py resimulate
    python brain_scan_pipeline.py sync    # fetch-all + filter-local + verify-checks
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth

# ─── constants ────────────────────────────────────────────────────────────────

BASE = "https://api.worldquantbrain.com"
SELF_ALPHA_URL = f"{BASE}/users/self/alphas"
SIM_URL = f"{BASE}/simulations"
PAGE_SIZE = 100
DEFAULT_DB = "alpha_scan.db"
DEFAULT_CSV = "submittable_alphas.csv"
STATE_FILE = "scan_state.json"


# ─── env helpers ──────────────────────────────────────────────────────────────

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
        raise SystemExit("错误：请在.env中设置 WQ_USERNAME 和 WQ_PASSWORD")
    return u, p


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── HTTP client ──────────────────────────────────────────────────────────────

class _ForceTLSv12(HTTPAdapter):
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        ctx = ssl.create_default_context()
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            pass
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


class BrainClient:
    """Thin requests wrapper: auth, retry, rate-limit."""

    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, */*",
        "Content-Type": "application/json",
        "Origin": "https://platform.worldquantbrain.com",
    }

    def __init__(self, username: str, password: str, *, page_sleep: float = 0.4) -> None:
        self.username = username
        self.page_sleep = page_sleep
        self._sess = requests.Session()
        self._sess.auth = HTTPBasicAuth(username, password)
        self._sess.headers.update(self._HEADERS)
        self._sess.trust_env = True
        self._sess.mount("https://", _ForceTLSv12())
        proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()
        if proxy:
            self._sess.proxies["https"] = proxy

    def _req(
        self, method: str, url: str, *, params: dict | None = None,
        json_body: dict | None = None, timeout: tuple = (15, 90),
    ) -> requests.Response:
        reauthed = False
        for attempt in range(4):
            resp = self._sess.request(method, url, params=params, json=json_body, timeout=timeout)
            if resp.status_code == 401 and not reauthed:
                reauthed = True
                self._sess.post(f"{BASE}/authentication", timeout=(15, 60))
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = float(resp.headers.get("Retry-After") or min(2 ** attempt, 60))
                print(f"  [retry] HTTP {resp.status_code} attempt={attempt+1} sleep={wait:.0f}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    def authenticate(self) -> None:
        r = self._sess.post(f"{BASE}/authentication", timeout=(15, 60))
        if r.status_code not in (200, 201):
            raise SystemExit(f"认证失败 HTTP {r.status_code}: {r.text[:300]}")
        print(f"[auth] 登录成功 user={self.username[:4]}***")

    def get_alpha_detail(self, alpha_id: str) -> dict | None:
        for attempt in range(3):
            try:
                r = self._req("GET", f"{BASE}/alphas/{alpha_id}")
                if r.status_code == 200:
                    body = r.json()
                    return body if isinstance(body, dict) else None
            except Exception as e:
                if attempt == 2:
                    print(f"  [warn] get_alpha_detail {alpha_id} failed: {e}")
                time.sleep(1.5)
        return None

    def fetch_page(self, offset: int, limit: int = PAGE_SIZE, extra_params: dict | None = None) -> dict:
        params: dict[str, Any] = {"limit": limit, "offset": offset, "order": "-dateCreated"}
        if extra_params:
            params.update(extra_params)
        r = self._req("GET", SELF_ALPHA_URL, params=params)
        return r.json()

    def post_simulation(self, payload: dict) -> tuple[str | None, str]:
        """POST to /simulations; returns (progress_url_or_None, status_str)."""
        for attempt in range(4):
            try:
                r = self._sess.post(SIM_URL, json=payload, timeout=(15, 120))
                if r.status_code == 401:
                    self._sess.post(f"{BASE}/authentication", timeout=(15, 60))
                    continue
                if r.status_code == 400:
                    return None, f"bad_request:{r.text[:300]}"
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = float(r.headers.get("Retry-After") or min(2 ** attempt, 30))
                    print(f"  [sim/retry] HTTP {r.status_code} sleep={wait:.0f}s")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                body = r.json() if r.text else {}
                loc = r.headers.get("Location", "")
                if not loc and isinstance(body, dict):
                    for k in ("location", "url", "href"):
                        v = body.get(k, "")
                        if isinstance(v, str) and v.strip():
                            loc = v.strip()
                            break
                if loc:
                    url = loc if loc.startswith("http") else urljoin(f"{BASE}/", loc.lstrip("/"))
                    return url, "ok"
                aid = _alpha_id_from_body(body)
                if aid:
                    return f"{BASE}/alphas/{aid}", "ok"
                return None, "missing_location"
            except Exception as e:
                if attempt == 3:
                    return None, f"error:{e}"
                time.sleep(min(2 ** attempt, 10))
        return None, "failed"

    def poll_simulation(self, progress_url: str, max_wait: float = 300.0) -> tuple[str | None, dict | None]:
        """Poll progress URL until done; returns (alpha_id, detail_dict)."""
        deadline = time.time() + max_wait
        sleep_s = 1.0
        while time.time() < deadline:
            try:
                r = self._req("GET", progress_url)
                if r.status_code == 200:
                    body = r.json()
                    if isinstance(body, dict):
                        status = str(body.get("status") or body.get("state") or "").lower()
                        if status in ("failed", "error", "rejected"):
                            return None, body
                        aid = _alpha_id_from_body(body)
                        if aid:
                            return aid, body
            except Exception:
                pass
            time.sleep(sleep_s)
            sleep_s = min(sleep_s * 1.2, 8.0)
        return None, None


# ─── alpha-ID / expression helpers ───────────────────────────────────────────

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


def _expr_from_alpha(row: dict) -> str:
    regular = row.get("regular")
    if isinstance(regular, dict):
        return str(regular.get("code") or regular.get("regular") or "").strip()
    if isinstance(regular, str):
        return regular.strip()
    return ""


def _settings_from_alpha(row: dict) -> dict:
    s = row.get("settings")
    return s if isinstance(s, dict) else {}


def _expr_hash(expr: str, settings: dict) -> str:
    key = expr.strip() + "||" + json.dumps(settings, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _metric(obj: dict | None, *keys: str) -> float | None:
    if not isinstance(obj, dict):
        return None
    pools = [obj, obj.get("is"), obj.get("summary")]
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        for k in keys:
            v = pool.get(k)
            if v is not None:
                try:
                    return float(v)
                except Exception:
                    pass
            for dk, dv in pool.items():
                if str(dk).lower() == k.lower() and dv is not None:
                    try:
                        return float(dv)
                    except Exception:
                        pass
    return None


# ─── local SQLite store ───────────────────────────────────────────────────────

class AlphaStore:
    """
    SQLite-backed alpha record store.
    Key: alpha_id (primary); fallback: expr_hash.
    Incremental upsert: only update changed fields, never overwrite history.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS alphas (
        alpha_id         TEXT PRIMARY KEY,
        expr_hash        TEXT,
        expression       TEXT,
        settings_json    TEXT,
        region           TEXT,
        universe         TEXT,
        delay            INTEGER,
        neutralization   TEXT,
        decay            INTEGER,
        truncation       REAL,
        sharpe           REAL,
        fitness          REAL,
        turnover         REAL,
        margin           REAL,
        returns_         REAL,
        drawdown         REAL,
        checks_json      TEXT,
        check_timestamp  TEXT,
        pipeline_status  TEXT DEFAULT 'PENDING',
        filter_reason    TEXT,
        source_alpha_id  TEXT,
        new_alpha_id     TEXT,
        last_synced_at   TEXT,
        date_created     TEXT,
        raw_json         TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_pipeline_status ON alphas(pipeline_status);
    CREATE INDEX IF NOT EXISTS idx_expr_hash ON alphas(expr_hash);
    """

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.path))
        self._con.row_factory = sqlite3.Row
        self._con.executescript(self.SCHEMA)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def count(self, status: str | None = None) -> int:
        if status:
            return self._con.execute(
                "SELECT COUNT(*) FROM alphas WHERE pipeline_status=?", (status,)
            ).fetchone()[0]
        return self._con.execute("SELECT COUNT(*) FROM alphas").fetchone()[0]

    def upsert(self, alpha_id: str, row: dict[str, Any]) -> tuple[str, bool]:
        """
        Insert or merge-update a record.
        Returns ("new"|"updated"|"unchanged", changed_bool).
        Never overwrites: pipeline_status, filter_reason, new_alpha_id if already set.
        """
        existing = self._con.execute(
            "SELECT * FROM alphas WHERE alpha_id=?", (alpha_id,)
        ).fetchone()

        now = _utc()
        new_data = {
            "alpha_id":        alpha_id,
            "expr_hash":       row.get("expr_hash", ""),
            "expression":      row.get("expression", ""),
            "settings_json":   json.dumps(row.get("settings", {}), ensure_ascii=False),
            "region":          str(row.get("region") or ""),
            "universe":        str(row.get("universe") or ""),
            "delay":           row.get("delay"),
            "neutralization":  str(row.get("neutralization") or ""),
            "decay":           row.get("decay"),
            "truncation":      row.get("truncation"),
            "sharpe":          row.get("sharpe"),
            "fitness":         row.get("fitness"),
            "turnover":        row.get("turnover"),
            "margin":          row.get("margin"),
            "returns_":        row.get("returns"),
            "drawdown":        row.get("drawdown"),
            "date_created":    str(row.get("date_created") or ""),
            "last_synced_at":  now,
            "raw_json":        json.dumps(row.get("raw"), ensure_ascii=False) if row.get("raw") else "",
        }

        if existing is None:
            new_data["pipeline_status"] = "PENDING"
            cols = ", ".join(new_data.keys())
            placeholders = ", ".join("?" * len(new_data))
            self._con.execute(
                f"INSERT INTO alphas ({cols}) VALUES ({placeholders})",
                list(new_data.values()),
            )
            self._con.commit()
            return "new", True

        # Merge: update metrics and last_synced_at; preserve status/filter/new_alpha_id
        updates: dict[str, Any] = {"last_synced_at": now}
        metric_fields = ["sharpe", "fitness", "turnover", "margin", "returns_", "drawdown",
                         "expression", "settings_json", "region", "universe", "delay",
                         "neutralization", "decay", "truncation", "date_created", "raw_json", "expr_hash"]
        for f in metric_fields:
            v = new_data.get(f)
            old_v = existing[f] if f in existing.keys() else None
            if v != old_v and v is not None:
                updates[f] = v

        if len(updates) == 1:  # only last_synced_at changed
            # Still update the timestamp
            self._con.execute(
                "UPDATE alphas SET last_synced_at=? WHERE alpha_id=?",
                (now, alpha_id),
            )
            self._con.commit()
            return "unchanged", False

        set_clause = ", ".join(f"{k}=?" for k in updates)
        self._con.execute(
            f"UPDATE alphas SET {set_clause} WHERE alpha_id=?",
            list(updates.values()) + [alpha_id],
        )
        self._con.commit()
        return "updated", True

    def set_pipeline_status(self, alpha_id: str, status: str, reason: str = "") -> None:
        self._con.execute(
            "UPDATE alphas SET pipeline_status=?, filter_reason=? WHERE alpha_id=?",
            (status, reason, alpha_id),
        )
        self._con.commit()

    def set_checks(self, alpha_id: str, checks_json: str, check_timestamp: str, status: str, reason: str) -> None:
        self._con.execute(
            "UPDATE alphas SET checks_json=?, check_timestamp=?, pipeline_status=?, filter_reason=? WHERE alpha_id=?",
            (checks_json, check_timestamp, status, reason, alpha_id),
        )
        self._con.commit()

    def set_new_alpha_id(self, source_id: str, new_id: str) -> None:
        self._con.execute(
            "UPDATE alphas SET new_alpha_id=? WHERE alpha_id=?",
            (new_id, source_id),
        )
        self._con.commit()

    def get_by_status(self, status: str) -> list[sqlite3.Row]:
        return self._con.execute(
            "SELECT * FROM alphas WHERE pipeline_status=? ORDER BY sharpe DESC",
            (status,),
        ).fetchall()

    def get_all(self) -> list[sqlite3.Row]:
        return self._con.execute("SELECT * FROM alphas ORDER BY sharpe DESC NULLS LAST").fetchall()

    def get_alpha(self, alpha_id: str) -> sqlite3.Row | None:
        return self._con.execute(
            "SELECT * FROM alphas WHERE alpha_id=?", (alpha_id,)
        ).fetchone()


# ─── state / resume helpers ───────────────────────────────────────────────────

def _load_state(path: str) -> dict:
    p = Path(path)
    if p.is_file():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            pass
    return {}


def _save_state(path: str, state: dict) -> None:
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


# ─── row builder ─────────────────────────────────────────────────────────────

def _build_record(row: dict) -> dict:
    """Normalize a raw /users/self/alphas or /alphas/{id} row into our schema."""
    alpha_id = str(row.get("id") or row.get("alphaId") or row.get("alpha_id") or "").strip()
    expr = _expr_from_alpha(row)
    settings = _settings_from_alpha(row)
    is_data = row.get("is") if isinstance(row.get("is"), dict) else {}

    def _s(key: str) -> str:
        v = settings.get(key) or row.get(key)
        return str(v) if v is not None else ""

    def _i(key: str) -> int | None:
        v = settings.get(key) or row.get(key)
        try:
            return int(v) if v is not None else None
        except Exception:
            return None

    def _f(key: str) -> float | None:
        v = settings.get(key) or row.get(key)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    checks = is_data.get("checks") if isinstance(is_data, dict) else None

    return {
        "alpha_id":     alpha_id,
        "expr_hash":    _expr_hash(expr, settings),
        "expression":   expr,
        "settings":     settings,
        "region":       _s("region"),
        "universe":     _s("universe"),
        "delay":        _i("delay"),
        "neutralization": _s("neutralization"),
        "decay":        _i("decay"),
        "truncation":   _f("truncation"),
        "sharpe":       _metric(row, "sharpe", "Sharpe"),
        "fitness":      _metric(row, "fitness", "Fitness"),
        "turnover":     _metric(row, "turnover", "Turnover"),
        "margin":       _metric(row, "margin", "Margin"),
        "returns":      _metric(row, "returns", "Returns"),
        "drawdown":     _metric(row, "drawdown", "Drawdown"),
        "checks":       checks,
        "date_created": str(row.get("dateCreated") or row.get("date_created") or ""),
        "raw":          row,
    }


# ─── checks helpers ───────────────────────────────────────────────────────────

def _extract_checks(detail: dict | None) -> list[dict]:
    if not isinstance(detail, dict):
        return []
    is_d = detail.get("is")
    if isinstance(is_d, dict):
        chks = is_d.get("checks")
        if isinstance(chks, list):
            return [c for c in chks if isinstance(c, dict)]
    chks = detail.get("checks")
    if isinstance(chks, list):
        return [c for c in chks if isinstance(c, dict)]
    return []


def _check_verdict(checks: list[dict]) -> tuple[str, str]:
    """
    Returns (verdict, reason).
    verdict: SUBMITTABLE | CHECK_FAILED | PENDING_RESIM | PENDING
    """
    if not checks:
        return "PENDING", "no_checks_yet"

    failed = []
    pending = []
    for c in checks:
        name = str(c.get("name") or "").upper()
        result = str(c.get("result") or c.get("status") or "").upper()
        if result in ("FAIL", "FAILED", "ERROR", "REJECTED"):
            val = c.get("value")
            lim = c.get("limit")
            detail = f"{name}:{val}/{lim}" if (val is not None or lim is not None) else name
            failed.append(detail)
        elif result in ("PENDING", ""):
            pending.append(name)

    if failed:
        return "CHECK_FAILED", "; ".join(failed)

    # Check if any check says "needs resim" (expired IS period)
    for c in checks:
        msg = str(c.get("message") or c.get("description") or "").lower()
        if "expired" in msg or "outdated" in msg or "resimulat" in msg:
            return "PENDING_RESIM", "IS_period_expired_needs_resim"

    if pending:
        return "PENDING", f"checks_pending: {', '.join(pending)}"

    return "SUBMITTABLE", "all_checks_pass"


# ─── subcommand: fetch-all ────────────────────────────────────────────────────

def cmd_fetch_all(
    client: BrainClient,
    store: AlphaStore,
    *,
    status_filter: str = "UNSUBMITTED",
    state_path: str = STATE_FILE,
    page_sleep: float = 0.4,
    extra_params: dict | None = None,
) -> None:
    """
    Paginate GET /users/self/alphas, merge every record into the local store.
    Resumes from last successful offset if interrupted.
    Alerts if fetched count != API-declared count.
    """
    print(f"\n{'='*60}")
    print(f"[fetch-all] status={status_filter}")
    print(f"{'='*60}")

    state = _load_state(state_path)
    fetch_params: dict[str, Any] = {"status": status_filter}
    if extra_params:
        fetch_params.update(extra_params)

    # Probe first page to get total count
    first = client.fetch_page(0, PAGE_SIZE, fetch_params)
    declared_total: int = int(first.get("count") or 0)
    print(f"[fetch-all] API声明总数: {declared_total}")

    if declared_total == 0:
        print("[fetch-all] 没有符合条件的alpha，退出")
        return

    # Collect all pages
    all_rows: list[dict] = []
    failed_offsets: list[int] = []
    offset = 0

    # Process first page results
    first_results = first.get("results") or []
    all_rows.extend(first_results)

    while len(all_rows) < declared_total:
        offset = len(all_rows)
        if offset >= declared_total:
            break
        try:
            time.sleep(page_sleep)
            page = client.fetch_page(offset, PAGE_SIZE, fetch_params)
            results = page.get("results") or []
            if not results:
                print(f"[fetch-all] offset={offset} 返回0条，停止翻页")
                break
            all_rows.extend(results)
            done_pct = 100.0 * len(all_rows) / max(declared_total, 1)
            print(f"[fetch-all] 已拉 {len(all_rows)}/{declared_total} ({done_pct:.0f}%)")
        except Exception as e:
            print(f"[fetch-all] ⚠️  offset={offset} 失败: {e}")
            failed_offsets.append(offset)
            time.sleep(5)

    # Retry failed offsets
    if failed_offsets:
        print(f"\n[fetch-all] 重试失败分页: {failed_offsets}")
        for fail_off in list(failed_offsets):
            try:
                time.sleep(2)
                page = client.fetch_page(fail_off, PAGE_SIZE, fetch_params)
                results = page.get("results") or []
                all_rows.extend(results)
                failed_offsets.remove(fail_off)
                print(f"[fetch-all] 重试offset={fail_off} OK +{len(results)}条")
            except Exception as e:
                print(f"[fetch-all] ⚠️  重试offset={fail_off} 仍失败: {e}")

    # Dedup by alpha_id
    seen: dict[str, dict] = {}
    for r in all_rows:
        if not isinstance(r, dict):
            continue
        aid = str(r.get("id") or r.get("alphaId") or "").strip()
        if aid:
            seen[aid] = r

    actual_total = len(seen)
    print(f"\n[fetch-all] 实际拉取去重后: {actual_total} 条 (API声明: {declared_total})")

    if actual_total < declared_total * 0.95:
        print(
            f"[fetch-all] ⚠️  警告：实际条数({actual_total}) 比声明总数({declared_total}) 少超过5%！"
            f" 失败分页: {failed_offsets}。建议检查网络并重试。"
        )

    # Upsert into store
    new_count = updated_count = unchanged_count = 0
    for aid, raw_row in seen.items():
        record = _build_record(raw_row)
        action, _ = store.upsert(aid, record)
        if action == "new":
            new_count += 1
        elif action == "updated":
            updated_count += 1
        else:
            unchanged_count += 1

    total_in_db = store.count()
    print(
        f"[fetch-all] 同步完成 ➜ 新增{new_count}条 / 更新{updated_count}条 / "
        f"无变化{unchanged_count}条 / 本地文件总条数{total_in_db}条"
    )
    if failed_offsets:
        print(f"[fetch-all] ⚠️  仍有失败分页未恢复: {failed_offsets}")

    _save_state(state_path, {
        "last_fetch_all_utc": _utc(),
        "declared_total": declared_total,
        "actual_fetched": actual_total,
        "status_filter": status_filter,
    })


# ─── subcommand: filter-local ─────────────────────────────────────────────────

def cmd_filter_local(
    store: AlphaStore,
    *,
    min_sharpe: float = 1.25,
    min_fitness: float = 1.0,
    min_turnover: float = 0.01,
    max_turnover: float = 0.70,
    reset_previous: bool = False,
) -> None:
    """
    本地粗筛: 基于IS指标打标签 CANDIDATE / LOCAL_FILTERED_OUT.
    不删除任何记录。
    """
    print(f"\n{'='*60}")
    print(f"[filter-local] sharpe>={min_sharpe}  fitness>={min_fitness}  "
          f"turnover∈[{min_turnover*100:.1f}%, {max_turnover*100:.1f}%]")
    print(f"{'='*60}")

    all_rows = store.get_all()
    total = len(all_rows)

    if reset_previous:
        # Reset CANDIDATE/LOCAL_FILTERED_OUT back to PENDING for re-filter
        store._con.execute(
            "UPDATE alphas SET pipeline_status='PENDING' "
            "WHERE pipeline_status IN ('CANDIDATE','LOCAL_FILTERED_OUT')"
        )
        store._con.commit()
        print(f"[filter-local] 已重置 CANDIDATE/LOCAL_FILTERED_OUT → PENDING")

    # Only filter PENDING records
    pending = [r for r in all_rows if r["pipeline_status"] in ("PENDING", "CANDIDATE", "LOCAL_FILTERED_OUT")]
    before_count = len([r for r in all_rows if r["pipeline_status"] == "CANDIDATE"])

    candidate = 0
    filtered_out = 0
    skipped_no_metrics = 0

    for row in pending:
        sharpe = row["sharpe"]
        fitness = row["fitness"]
        turnover = row["turnover"]
        aid = row["alpha_id"]

        if sharpe is None or fitness is None or turnover is None:
            skipped_no_metrics += 1
            store.set_pipeline_status(aid, "LOCAL_FILTERED_OUT", "missing_metrics")
            filtered_out += 1
            continue

        reasons: list[str] = []
        if sharpe < min_sharpe:
            reasons.append(f"sharpe={sharpe:.3f}<{min_sharpe}")
        if fitness < min_fitness:
            reasons.append(f"fitness={fitness:.3f}<{min_fitness}")
        if turnover < min_turnover:
            reasons.append(f"turnover={turnover*100:.2f}%<{min_turnover*100:.1f}%")
        if turnover >= max_turnover:
            reasons.append(f"turnover={turnover*100:.2f}%>={max_turnover*100:.1f}%")

        if reasons:
            store.set_pipeline_status(aid, "LOCAL_FILTERED_OUT", "; ".join(reasons))
            filtered_out += 1
        else:
            store.set_pipeline_status(aid, "CANDIDATE", "local_pass")
            candidate += 1

    after_candidate = store.count("CANDIDATE")
    print(
        f"[filter-local] 粗筛完成 ➜ "
        f"总数{total} | 候选CANDIDATE: {after_candidate} | "
        f"本轮淘汰: {filtered_out} (其中无指标: {skipped_no_metrics})"
    )


# ─── subcommand: verify-checks ───────────────────────────────────────────────

def cmd_verify_checks(
    client: BrainClient,
    store: AlphaStore,
    *,
    concurrency: int = 4,
    skip_already_verified: bool = True,
    state_path: str = STATE_FILE,
) -> None:
    """
    对所有CANDIDATE逐个调平台 GET /alphas/{id} 拿checks数组，做权威判定。
    - SUBMITTABLE: 所有check PASS
    - CHECK_FAILED: 有check FAIL
    - PENDING_RESIM: IS周期过期需重模拟
    存储checks原始数组+时间戳。
    """
    print(f"\n{'='*60}")
    print(f"[verify-checks] 开始验证CANDIDATE")
    print(f"{'='*60}")

    candidates = store.get_by_status("CANDIDATE")
    if skip_already_verified:
        # Skip if already verified in this run (checks_json exists and is recent)
        # Actually re-verify all CANDIDATE each time for fresh snapshot
        pass

    total = len(candidates)
    print(f"[verify-checks] CANDIDATE数量: {total}")
    if total == 0:
        print("[verify-checks] 无候选，退出。请先运行 filter-local")
        return

    submittable = 0
    check_failed = 0
    pending = 0
    errors = 0

    for i, row in enumerate(candidates, 1):
        aid = row["alpha_id"]
        if i % 50 == 0 or i == 1 or i == total:
            print(f"[verify-checks] {i}/{total} submittable={submittable} failed={check_failed}")

        try:
            detail = client.get_alpha_detail(aid)
            if not isinstance(detail, dict):
                errors += 1
                store.set_checks(aid, "[]", _utc(), "CANDIDATE", "detail_fetch_failed")
                continue

            checks = _extract_checks(detail)
            verdict, reason = _check_verdict(checks)
            checks_json = json.dumps(checks, ensure_ascii=False)
            ts = _utc()

            store.set_checks(aid, checks_json, ts, verdict, reason)

            if verdict == "SUBMITTABLE":
                submittable += 1
            elif verdict == "CHECK_FAILED":
                check_failed += 1
            elif verdict == "PENDING_RESIM":
                # Treat as SUBMITTABLE for resimulation purposes
                store.set_checks(aid, checks_json, ts, "CANDIDATE", "pending_resim_candidate")
                pending += 1
            else:
                pending += 1

            # Small rate limit
            time.sleep(0.25)

        except Exception as e:
            errors += 1
            print(f"  [warn] {aid} verify failed: {e}")
            time.sleep(1)

    submittable_final = store.count("SUBMITTABLE")
    print(
        f"\n[verify-checks] 验证完成 ➜ "
        f"SUBMITTABLE={submittable_final} | CHECK_FAILED={check_failed} | "
        f"PENDING={pending} | 错误={errors}"
    )
    print(f"[verify-checks] ⚠️  注意：SUBMITTABLE名单是'此刻'快照，SELF_CORRELATION会随提交顺序动态变化")

    _save_state(state_path, {
        **_load_state(state_path),
        "last_verify_checks_utc": _utc(),
        "submittable_count": submittable_final,
    })


# ─── subcommand: aggregate ───────────────────────────────────────────────────

def cmd_aggregate(
    store: AlphaStore,
    *,
    output_csv: str = DEFAULT_CSV,
    expected_min: int = 100,
) -> None:
    """
    把所有SUBMITTABLE记录写入CSV，打印数量汇总。
    """
    print(f"\n{'='*60}")
    print(f"[aggregate] 汇总SUBMITTABLE → {output_csv}")
    print(f"{'='*60}")

    rows = store.get_by_status("SUBMITTABLE")
    total_db = store.count()
    candidate_count = store.count("CANDIDATE")
    submittable_count = len(rows)

    print(f"[aggregate] 本地总条数: {total_db} | CANDIDATE: {candidate_count} | SUBMITTABLE: {submittable_count}")

    if submittable_count < expected_min:
        print(
            f"[aggregate] ⚠️  警告：SUBMITTABLE数量({submittable_count})远低于预期({expected_min}+)！"
            f" 请检查筛选阈值或check结果是否正常。"
        )

    import csv
    fieldnames = [
        "alpha_id", "expression", "settings", "region", "universe", "delay",
        "neutralization", "decay", "truncation",
        "sharpe", "fitness", "turnover", "margin", "returns", "drawdown",
        "checks_json", "check_timestamp", "source_alpha_id",
    ]

    out_path = Path(output_csv)
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "alpha_id":        row["alpha_id"],
                "expression":      row["expression"],
                "settings":        row["settings_json"],
                "region":          row["region"],
                "universe":        row["universe"],
                "delay":           row["delay"],
                "neutralization":  row["neutralization"],
                "decay":           row["decay"],
                "truncation":      row["truncation"],
                "sharpe":          row["sharpe"],
                "fitness":         row["fitness"],
                "turnover":        row["turnover"],
                "margin":          row["margin"],
                "returns":         row["returns_"],
                "drawdown":        row["drawdown"],
                "checks_json":     row["checks_json"] or "[]",
                "check_timestamp": row["check_timestamp"] or "",
                "source_alpha_id": row["source_alpha_id"] or "",
            })

    print(f"[aggregate] 已写入 {submittable_count} 条 → {out_path.resolve()}")


# ─── subcommand: resimulate ──────────────────────────────────────────────────

def _build_sim_payload(row: sqlite3.Row) -> dict | None:
    expr = row["expression"]
    if not expr:
        return None
    try:
        settings = json.loads(row["settings_json"] or "{}")
    except Exception:
        settings = {}
    return {
        "type": "REGULAR",
        "regular": {"code": expr},
        "settings": settings,
    }


def cmd_resimulate(
    client: BrainClient,
    store: AlphaStore,
    *,
    submit_sleep: float = 2.0,
    max_poll_seconds: float = 300.0,
    skip_already_resimmed: bool = True,
    batch_size: int = 0,  # 0 = all
    state_path: str = STATE_FILE,
) -> None:
    """
    对SUBMITTABLE名单批量clone+re-simulate，更新new_alpha_id。
    遵守限流、断点续跑、失败重试。
    """
    print(f"\n{'='*60}")
    print(f"[resimulate] 开始批量重新模拟SUBMITTABLE名单")
    print(f"{'='*60}")

    rows = store.get_by_status("SUBMITTABLE")
    if skip_already_resimmed:
        rows = [r for r in rows if not r["new_alpha_id"]]

    total = len(rows) if not batch_size else min(batch_size, len(rows))
    if batch_size:
        rows = rows[:batch_size]

    print(f"[resimulate] 待模拟: {total} 条 (skip_already_resimmed={skip_already_resimmed})")
    if total == 0:
        print("[resimulate] 无待模拟alpha，退出")
        return

    ok_count = 0
    fail_count = 0
    skip_count = 0

    for i, row in enumerate(rows, 1):
        aid = row["alpha_id"]
        payload = _build_sim_payload(row)
        if not payload:
            skip_count += 1
            print(f"  [skip] {aid} 无法构建payload（expression为空）")
            continue

        if i % 20 == 0 or i == 1 or i == total:
            print(f"[resimulate] {i}/{total}  ok={ok_count} fail={fail_count}")

        # Rate limit between submits
        if i > 1:
            time.sleep(submit_sleep)

        try:
            progress_url, sub_status = client.post_simulation(payload)
            if not progress_url:
                fail_count += 1
                print(f"  [fail] {aid} submit: {sub_status}")
                continue

            new_aid, _ = client.poll_simulation(progress_url, max_wait=max_poll_seconds)
            if new_aid:
                store.set_new_alpha_id(aid, new_aid)
                ok_count += 1
            else:
                fail_count += 1
                print(f"  [fail] {aid} poll timeout/failed")

        except Exception as e:
            fail_count += 1
            print(f"  [error] {aid}: {e}")
            time.sleep(5)

    print(
        f"\n[resimulate] 完成 ➜ ok={ok_count} | fail={fail_count} | skip={skip_count}"
    )
    _save_state(state_path, {
        **_load_state(state_path),
        "last_resimulate_utc": _utc(),
        "resim_ok": ok_count,
        "resim_fail": fail_count,
    })


# ─── subcommand: sync (all-in-one) ───────────────────────────────────────────

def cmd_sync(
    client: BrainClient,
    store: AlphaStore,
    args: argparse.Namespace,
) -> None:
    """fetch-all + filter-local + verify-checks in sequence."""
    cmd_fetch_all(client, store, status_filter="UNSUBMITTED", state_path=args.state)
    cmd_filter_local(
        store,
        min_sharpe=args.min_sharpe,
        min_fitness=args.min_fitness,
        min_turnover=args.min_turnover,
        max_turnover=args.max_turnover,
    )
    cmd_verify_checks(client, store, state_path=args.state)
    cmd_aggregate(store, output_csv=args.csv)


# ─── main CLI ─────────────────────────────────────────────────────────────────

def _add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--min-sharpe",   type=float, default=1.25,  help="最低Sharpe (默认1.25)")
    p.add_argument("--min-fitness",  type=float, default=1.0,   help="最低Fitness (默认1.0)")
    p.add_argument("--min-turnover", type=float, default=0.01,  help="最低Turnover (默认0.01=1%%)")
    p.add_argument("--max-turnover", type=float, default=0.70,  help="最高Turnover (默认0.70=70%%)")


def main(argv: list[str] | None = None) -> None:
    print("[blocked] legacy scan/resimulation writer disabled; use python -m alpha_mining")
    return
    _load_env()

    parser = argparse.ArgumentParser(
        prog="brain_scan_pipeline",
        description="BRAIN全量扫描+筛选+重模拟流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令:
  fetch-all      分页拉取所有UNSUBMITTED alpha，合并进本地DB
  sync           fetch-all + filter-local + verify-checks + aggregate 一键运行
  filter-local   本地粗筛 (sharpe/fitness/turnover阈值)
  verify-checks  对CANDIDATE调平台check API，拿权威判定
  aggregate      汇总SUBMITTABLE输出CSV
  resimulate     对SUBMITTABLE批量clone+re-simulate

示例:
  python brain_scan_pipeline.py fetch-all
  python brain_scan_pipeline.py filter-local --min-sharpe 1.3 --max-turnover 0.6
  python brain_scan_pipeline.py verify-checks
  python brain_scan_pipeline.py aggregate
  python brain_scan_pipeline.py resimulate
  python brain_scan_pipeline.py sync --min-sharpe 1.3
""",
    )
    parser.add_argument("--db",    default=DEFAULT_DB,  help=f"本地SQLite路径 (默认: {DEFAULT_DB})")
    parser.add_argument("--csv",   default=DEFAULT_CSV, help=f"输出CSV路径 (默认: {DEFAULT_CSV})")
    parser.add_argument("--state", default=STATE_FILE,  help=f"状态文件路径 (默认: {STATE_FILE})")
    parser.add_argument("--page-sleep", type=float, default=0.4, help="翻页间隔秒数 (默认0.4)")

    subs = parser.add_subparsers(dest="cmd", required=True)

    # fetch-all
    p_fetch = subs.add_parser("fetch-all", help="拉取所有UNSUBMITTED alpha")
    p_fetch.add_argument("--status", default="UNSUBMITTED",
                         help="alpha状态过滤 (默认: UNSUBMITTED)")

    # sync
    p_sync = subs.add_parser("sync", help="fetch-all + filter-local + verify-checks + aggregate")
    _add_filter_args(p_sync)
    p_sync.add_argument("--status", default="UNSUBMITTED")

    # filter-local
    p_filter = subs.add_parser("filter-local", help="本地粗筛")
    _add_filter_args(p_filter)
    p_filter.add_argument("--reset", action="store_true",
                          help="重置之前的粗筛结果，重新过滤")

    # verify-checks
    p_verify = subs.add_parser("verify-checks", help="平台check API验证")
    p_verify.add_argument("--no-skip-verified", action="store_true",
                          help="强制重新验证所有CANDIDATE（包括已验证的）")

    # aggregate
    p_agg = subs.add_parser("aggregate", help="汇总SUBMITTABLE到CSV")
    p_agg.add_argument("--expected-min", type=int, default=100,
                       help="预期SUBMITTABLE最低数量，低于此值发出警告 (默认100)")

    # resimulate
    p_resim = subs.add_parser("resimulate", help="批量clone+re-simulate")
    p_resim.add_argument("--submit-sleep", type=float, default=2.0,
                         help="每次提交间隔秒数 (默认2.0)")
    p_resim.add_argument("--max-poll", type=float, default=300.0,
                         help="每个alpha最长等待秒数 (默认300)")
    p_resim.add_argument("--batch-size", type=int, default=0,
                         help="只处理前N个（0=全部）")
    p_resim.add_argument("--no-skip-resimmed", action="store_true",
                         help="强制重新模拟已有new_alpha_id的记录")

    args = parser.parse_args(argv)

    # Build client and store
    username, password = _creds()
    client = BrainClient(username, password, page_sleep=args.page_sleep)
    store = AlphaStore(args.db)

    try:
        if args.cmd in ("fetch-all", "sync"):
            client.authenticate()

        if args.cmd == "fetch-all":
            cmd_fetch_all(client, store, status_filter=args.status, state_path=args.state,
                          page_sleep=args.page_sleep)

        elif args.cmd == "sync":
            cmd_sync(client, store, args)

        elif args.cmd == "filter-local":
            cmd_filter_local(
                store,
                min_sharpe=args.min_sharpe,
                min_fitness=args.min_fitness,
                min_turnover=args.min_turnover,
                max_turnover=args.max_turnover,
                reset_previous=args.reset,
            )

        elif args.cmd == "verify-checks":
            client.authenticate()
            cmd_verify_checks(
                client, store,
                skip_already_verified=not args.no_skip_verified,
                state_path=args.state,
            )

        elif args.cmd == "aggregate":
            cmd_aggregate(store, output_csv=args.csv, expected_min=args.expected_min)

        elif args.cmd == "resimulate":
            client.authenticate()
            cmd_resimulate(
                client, store,
                submit_sleep=args.submit_sleep,
                max_poll_seconds=args.max_poll,
                skip_already_resimmed=not args.no_skip_resimmed,
                batch_size=args.batch_size,
                state_path=args.state,
            )

    finally:
        store.close()


if __name__ == "__main__":
    main()

