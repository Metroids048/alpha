#!/usr/bin/env python3
"""Sync WorldQuant /users/self/alphas into local ledger + quality filter CSV.

Updates:
  1. alpha_submission_feedback.csv  — one row per alpha_id (platform source of truth)
  2. alpha_pipeline_results.csv     — sharpe>1.24, fitness>1, returns>1%, turnover>1%
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FEEDBACK_CSV = ROOT / "alpha_submission_feedback.csv"
FILTERED_CSV = ROOT / "alpha_pipeline_results.csv"
SYNC_VERSION = "platform_sync_v1"

FILTER_FIELDS = (
    "index",
    "alpha_id",
    "status",
    "queue_status",
    "check_passed",
    "check_note",
    "expression",
    "profile",
    "sharpe",
    "fitness",
    "turnover",
    "returns",
    "drawdown",
    "metric_gate_pass",
    "platform_non_self_pass",
    "self_correlation_status",
    "submission_candidate",
    "platform_pass_evidence",
    "platform_gate_reason",
    "pass_proxy_reason",
    "blocked_reason",
    "simulation_id",
    "failure_reasons",
)


def _load_v50():
    name = "auto_alpha_pipeline_rebuilt_v50"
    path = ROOT / "auto_alpha_pipeline_rebuilt_v50.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_bool(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _expr_from_row(row: dict[str, Any]) -> str:
    regular = row.get("regular")
    if isinstance(regular, dict):
        return str(regular.get("code") or regular.get("regular") or "").strip()
    if isinstance(regular, str):
        return regular.strip()
    return ""


def _settings_from_row(row: dict[str, Any]) -> dict[str, Any]:
    settings = row.get("settings")
    return settings if isinstance(settings, dict) else {}


def _platform_row_to_feedback(
    mod: Any,
    row: dict[str, Any],
    *,
    existing: dict[str, str] | None,
) -> dict[str, str]:
    fields = mod.FEEDBACK_FIELDS
    aid = str(row.get("id") or row.get("alpha") or "").strip()
    settings = _settings_from_row(row)
    expr = _expr_from_row(row)
    merged = row if isinstance(row, dict) else {}
    metrics = {
        "sharpe": mod._to_float(mod._metric_get(merged, "sharpe", "Sharpe")),
        "fitness": mod._to_float(mod._metric_get(merged, "fitness", "Fitness")),
        "turnover": mod._to_float(mod._metric_get(merged, "turnover", "Turnover")),
    }
    check_note = ""
    checks = mod._extract_checks(merged)
    if checks:
        check_note = "; ".join(
            f"{c.get('name','')}:{c.get('result','')}" for c in checks[:12]
        )
    check_passed: bool | None = None
    is_data = merged.get("is") if isinstance(merged.get("is"), dict) else {}
    cp = is_data.get("check_passed") if isinstance(is_data, dict) else None
    if cp is not None:
        check_passed = mod._norm_bool(cp) if hasattr(mod, "_norm_bool") else _norm_bool(cp)
    if check_passed is None and checks:
        hard = mod._hard_fail_checks(merged)
        if hard:
            check_passed = False
        elif mod._non_self_checks_all_pass(merged) and not mod._self_correlation_pending(merged):
            check_passed = True

    analysis = mod._feedback_analysis_fields(
        metrics,
        merged,
        check_passed=check_passed,
        check_note=check_note,
        queue_status=str((existing or {}).get("queue_status") or ""),
    )
    submitted = str((existing or {}).get("submitted") or "False")
    submit_note = str((existing or {}).get("submit_note") or "")
    date_sub = str(row.get("dateSubmitted") or "")
    if date_sub and date_sub.lower() not in ("none", "null", ""):
        submitted = "True"
        if not submit_note:
            submit_note = f"platform_dateSubmitted:{date_sub}"

    out = {k: "" for k in fields}
    out.update(
        {
            "utc_iso": _utc(),
            "pipeline_version": SYNC_VERSION,
            "alpha_id": aid,
            "simulation_id": "",
            "expression": expr,
            "family": str((existing or {}).get("family") or ""),
            "source": "platform_sync",
            "profile": str((existing or {}).get("profile") or ""),
            "status": str(row.get("status") or "ok").lower() or "ok",
            "queue_status": str((existing or {}).get("queue_status") or ""),
            "submitted": submitted,
            "submit_note": submit_note,
            "check_passed": check_passed if check_passed is not None else "",
            "check_note": check_note,
            "Region": str(settings.get("region") or ""),
            "Universe": str(settings.get("universe") or ""),
            "Neutralization": str(settings.get("neutralization") or ""),
            "Decay": str(settings.get("decay") or ""),
            "Truncation": str(settings.get("truncation") or ""),
            "Delay": str(settings.get("delay") or ""),
            "Sharpe": metrics["sharpe"] if metrics["sharpe"] is not None else "",
            "Fitness": metrics["fitness"] if metrics["fitness"] is not None else "",
            "Turnover": metrics["turnover"] if metrics["turnover"] is not None else "",
            "Returns": mod._to_float(mod._metric_get(merged, "returns", "Returns")) or "",
            "Drawdown": mod._to_float(mod._metric_get(merged, "drawdown", "Drawdown")) or "",
            "Margin": mod._to_float(mod._metric_get(merged, "margin", "Margin")) or "",
            **{k: ("" if v is None else v) for k, v in analysis.items()},
            "Failure Reasons": mod._failure_reason_for_ledger(
                merged, sim_json=None, status=str(row.get("status") or ""), check_note=check_note
            ),
            "platform_simulation_json": mod._json_compact(merged),
            "platform_check_json": mod._json_compact({"is": merged.get("is")} if merged.get("is") else None),
        }
    )
    # Preserve pipeline-only metadata when platform sync is metric-only refresh.
    if existing:
        for k in ("family", "source", "profile", "queue_status", "simulation_id"):
            if not str(out.get(k) or "").strip() and str(existing.get(k) or "").strip():
                out[k] = existing[k]
    return out


def _fetch_all_platform_alphas(pipe: Any, *, page_sleep: float = 0.12) -> list[dict[str, Any]]:
    """Paginate /users/self/alphas; API caps offset at ~9900, then dateCreated< cursor."""
    all_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    max_offset = 9900
    while offset <= max_offset:
        r = pipe._sess_request(
            "GET",
            pipe._SELF_ALPHA_URL,
            params={"limit": 100, "offset": offset, "order": "-dateCreated"},
            timeout=pipe._timeout(),
        )
        if r.status_code != 200:
            print(f"[sync] list HTTP {r.status_code} offset={offset}", flush=True)
            break
        data = r.json()
        if offset == 0:
            print(f"[sync] platform reports count={data.get('count')}", flush=True)
        batch = data.get("results") or []
        if not batch:
            break
        new_in_batch = 0
        for row in batch:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("id") or "").strip()
            if aid and aid in seen_ids:
                continue
            if aid:
                seen_ids.add(aid)
            all_rows.append(row)
            new_in_batch += 1
        print(
            f"[sync] page offset={offset} batch={len(batch)} new={new_in_batch} total={len(all_rows)}",
            flush=True,
        )
        if len(batch) < 100:
            break
        offset += 100
        time.sleep(page_sleep)

    cursor_dates = [str(r.get("dateCreated") or "") for r in all_rows if r.get("dateCreated")]
    cursor = min(cursor_dates) if cursor_dates else ""
    rounds = 0
    while cursor:
        rounds += 1
        round_new = 0
        offset = 0
        oldest_in_round = cursor
        while offset <= max_offset:
            r = pipe._sess_request(
                "GET",
                pipe._SELF_ALPHA_URL,
                params={
                    "limit": 100,
                    "offset": offset,
                    "order": "-dateCreated",
                    "dateCreated<": cursor,
                },
                timeout=pipe._timeout(),
            )
            if r.status_code != 200:
                print(f"[sync] cursor HTTP {r.status_code} offset={offset}", flush=True)
                break
            try:
                payload = r.json()
                batch = payload.get("results") or []
            except Exception:
                batch = []
            if not batch:
                break
            for row in batch:
                if not isinstance(row, dict):
                    continue
                aid = str(row.get("id") or "").strip()
                if aid and aid in seen_ids:
                    continue
                if aid:
                    seen_ids.add(aid)
                all_rows.append(row)
                round_new += 1
                dc = str(row.get("dateCreated") or "")
                if dc and dc < oldest_in_round:
                    oldest_in_round = dc
            if len(batch) < 100:
                break
            offset += 100
            time.sleep(page_sleep)
        print(
            f"[sync] cursor#{rounds} dateCreated<{cursor[:19]} new={round_new} total={len(all_rows)}",
            flush=True,
        )
        if round_new == 0:
            break
        if oldest_in_round >= cursor:
            break
        cursor = oldest_in_round
        if rounds > 300:
            print("[sync] cursor safety stop at 300 rounds", flush=True)
            break

    return all_rows


def _fetch_detail_batch(pipe: Any, alpha_ids: list[str], workers: int) -> dict[str, dict[str, Any]]:
    if not alpha_ids:
        return {}

    def one(aid: str) -> tuple[str, dict[str, Any] | None]:
        body = pipe.fetch_alpha_detail(aid, retries=2)
        return aid, body if isinstance(body, dict) else None

    out: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(one, aid): aid for aid in alpha_ids}
        done = 0
        for fut in as_completed(futs):
            done += 1
            aid, body = fut.result()
            if body:
                out[aid] = body
            if done % 500 == 0 or done == len(alpha_ids):
                print(f"[sync] detail fetch {done}/{len(alpha_ids)} ok={len(out)}")
    return out


def _passes_quality_filter(mod: Any, row: dict[str, str]) -> bool:
    sharpe = mod._to_float(row.get("Sharpe"))
    fitness = mod._to_float(row.get("Fitness"))
    turnover = mod._to_float(row.get("Turnover"))
    returns = mod._to_float(row.get("Returns"))
    if sharpe is None or fitness is None or turnover is None or returns is None:
        return False
    return sharpe > 1.24 and fitness > 1.0 and returns > 0.01 and turnover > 0.01


def _feedback_to_filtered_row(row: dict[str, str], idx: int) -> dict[str, str]:
    return {
        "index": str(idx),
        "alpha_id": row.get("alpha_id", ""),
        "status": row.get("status", ""),
        "queue_status": row.get("queue_status", ""),
        "check_passed": str(row.get("check_passed") or ""),
        "check_note": row.get("check_note", ""),
        "expression": row.get("expression", ""),
        "profile": row.get("profile", ""),
        "sharpe": str(row.get("Sharpe") or ""),
        "fitness": str(row.get("Fitness") or ""),
        "turnover": str(row.get("Turnover") or ""),
        "returns": str(row.get("Returns") or ""),
        "drawdown": str(row.get("Drawdown") or ""),
        "metric_gate_pass": str(row.get("metric_gate_pass") or ""),
        "platform_non_self_pass": str(row.get("platform_non_self_pass") or ""),
        "self_correlation_status": str(row.get("self_correlation_status") or ""),
        "submission_candidate": str(row.get("submission_candidate") or ""),
        "platform_pass_evidence": str(row.get("platform_pass_evidence") or ""),
        "platform_gate_reason": str(row.get("platform_gate_reason") or ""),
        "pass_proxy_reason": str(row.get("pass_proxy_reason") or ""),
        "blocked_reason": str(row.get("blocked_reason") or ""),
        "simulation_id": row.get("simulation_id", ""),
        "failure_reasons": row.get("Failure Reasons", ""),
    }


def _write_csv_atomic(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def main() -> int:
    print(
        "[blocked] legacy CSV platform sync is disabled; use "
        "python -m alpha_mining platform sync-ledger"
    )
    return 2
    p = argparse.ArgumentParser(description="Sync WQ platform alphas into local CSV ledgers")
    p.add_argument("--workers", type=int, default=8, help="Parallel detail fetch workers")
    p.add_argument("--target-count", type=int, default=26165, help="Expected platform simulate count")
    p.add_argument("--skip-detail", action="store_true", help="Skip detail fetch for rows missing metrics")
    p.add_argument("--dry-run", action="store_true", help="Fetch only, do not write files")
    args = p.parse_args()

    mod = _load_v50()
    mod._load_env_file()
    username, password = mod._credentials()
    if not username or not password:
        print("[error] Missing WQ_USERNAME / WQ_PASSWORD")
        return 2

    fields = list(mod.FEEDBACK_FIELDS)
    existing_by_id: dict[str, dict[str, str]] = {}
    rows_without_id: list[dict[str, str]] = []
    if FEEDBACK_CSV.is_file():
        print(f"[sync] reading existing ledger {FEEDBACK_CSV.name} ...", flush=True)
        n = 0
        with FEEDBACK_CSV.open("r", newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                n += 1
                if n % 5000 == 0:
                    print(f"[sync]   scanned {n} rows ids={len(existing_by_id)}", flush=True)
                aid = str(row.get("alpha_id") or "").strip()
                base = {k: str(row.get(k) or "") for k in fields}
                if not aid:
                    rows_without_id.append(base)
                    continue
                prev = existing_by_id.get(aid)
                if prev is None:
                    existing_by_id[aid] = base
                    continue

                def score(r: dict[str, str]) -> int:
                    s = sum(1 for k in ("Sharpe", "Fitness", "Turnover", "Returns") if str(r.get(k) or "").strip())
                    if str(r.get("utc_iso") or "").strip():
                        s += 1
                    return s

                if score(base) >= score(prev):
                    existing_by_id[aid] = base
        print(f"[sync] ledger scan done rows~={n} unique_ids={len(existing_by_id)}", flush=True)
    print(f"[sync] existing ledger alpha_ids={len(existing_by_id)} no_id_rows={len(rows_without_id)}", flush=True)

    cfg = mod.PipelineConfig(username=username, password=password)
    pipe = mod.WorldQuantAlphaPipeline(cfg)
    pipe.authenticate()

    platform_rows = _fetch_all_platform_alphas(pipe)
    print(f"[sync] fetched platform rows={len(platform_rows)}")

    need_detail: list[str] = []
    merged_by_id: dict[str, dict[str, str]] = dict(existing_by_id)
    for prow in platform_rows:
        aid = str(prow.get("id") or "").strip()
        if not aid:
            continue
        has_metrics = mod._metric_get(prow, "sharpe", "Sharpe") is not None
        if not has_metrics:
            need_detail.append(aid)
            continue
        merged_by_id[aid] = _platform_row_to_feedback(mod, prow, existing=merged_by_id.get(aid))

    if need_detail and not args.skip_detail:
        print(f"[sync] fetching detail for {len(need_detail)} alphas missing list metrics")
        details = _fetch_detail_batch(pipe, need_detail, args.workers)
        for aid, body in details.items():
            merged_by_id[aid] = _platform_row_to_feedback(mod, body, existing=merged_by_id.get(aid))
    elif need_detail:
        for aid in need_detail:
            # Placeholder row with id only — should be rare if list always has metrics.
            prev = merged_by_id.get(aid) or {k: "" for k in fields}
            prev["alpha_id"] = aid
            prev["source"] = prev.get("source") or "platform_sync"
            merged_by_id[aid] = prev

    # Keep local-only rows (simulate errors without alpha_id) at the tail.
    final_rows: list[dict[str, str]] = list(merged_by_id.values()) + rows_without_id
    final_rows.sort(key=lambda r: (str(r.get("utc_iso") or ""), str(r.get("alpha_id") or "")))

    filtered = [r for r in merged_by_id.values() if _passes_quality_filter(mod, r)]
    filtered.sort(
        key=lambda r: (
            -(mod._to_float(r.get("Sharpe")) or -999),
            -(mod._to_float(r.get("Fitness")) or -999),
        )
    )

    print(f"[sync] merged unique alpha_ids={len(merged_by_id)} total_rows={len(final_rows)}")
    print(f"[sync] quality filter matched={len(filtered)} (sharpe>1.24 fitness>1 returns>1% turnover>1%)")
    if len(platform_rows) < args.target_count * 0.95:
        print(
            f"[sync] WARNING: platform fetch {len(platform_rows)} < target {args.target_count}; "
            "pagination may be incomplete"
        )

    if args.dry_run:
        print("[sync] dry-run — no files written")
        return 0

    _write_csv_atomic(FEEDBACK_CSV, fields, final_rows)
    print(f"[sync] wrote {FEEDBACK_CSV.name} rows={len(final_rows)}")

    filtered_out = [_feedback_to_filtered_row(r, i) for i, r in enumerate(filtered, start=1)]
    _write_csv_atomic(FILTERED_CSV, list(FILTER_FIELDS), filtered_out)
    print(f"[sync] wrote {FILTERED_CSV.name} rows={len(filtered_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
