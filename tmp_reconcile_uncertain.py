"""READ-ONLY reconciliation for SIMULATION_UNCERTAIN candidates.

上轮 POST 已被平台接受，但 2fe45dc 之前 BrowserResponse 对 Location 大小写敏感，
导致本地未保存 progress_location。避免重复 simulation：先从平台列表找回已生成的 Alpha，
只对确认不存在的重新 POST。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.factory.operator_service import CandidateWorkflowService
from alpha_mining.platform import browser_transport as browser_transport_module
from alpha_mining.platform.gateway import PlatformGateway
from alpha_mining.platform.protocol import extract_checks, extract_metrics
from alpha_mining.storage.work_items import CandidateWorkStore

DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
SCHEMA = _ROOT / ".validation_workspace" / ".alpha_simulation_settings_cache.json"
PROFILE = _ROOT / ".validation_workspace" / "wq_browser_profile"
LOCK = _ROOT / "worldquant_api.lock"

UNCERTAIN_CIDS = [
    "2e54cbe98d9a3423790572d419f92dcd80bcc5995bef2aba22a06b6a467c9417",
    "91db14878734d8b6615d0e0ced69a297569e73f8344643327ac526b3907de41c",
    "8eb7d9167751fee81cd07935272db8143df70a72f6bdc2190492c34cb9f1024c",
    "195432396fb285c87e3b6b21eb8bb64776d6a2dfbd5cbc2dff80ba9d6381b6d0",
]


def normalize_expression(expr: str) -> str:
    """Whitespace-normalized expression for matching."""
    return re.sub(r"\s+", "", str(expr).strip())


def extract_platform_expression(alpha_detail: dict) -> str | None:
    """Extract expression from platform Alpha detail (regular.code / expression / regular)."""
    for key in ("regular", "expression"):
        val = alpha_detail.get(key)
        if isinstance(val, dict):
            code = val.get("code", "").strip()
            if code:
                return code
        elif isinstance(val, str) and val.strip():
            return val.strip()
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    store = CandidateWorkStore(DB)
    candidates = {}
    for cid in UNCERTAIN_CIDS:
        item = store.get_item(cid)
        if item is None:
            print(f"[WARN] {cid[:16]} MISSING in DB", flush=True)
            continue
        if item.state != "SIMULATION_UNCERTAIN":
            print(f"[WARN] {cid[:16]} state={item.state} (not UNCERTAIN, skipped)", flush=True)
            continue
        expr = item.payload.get("expression", "")
        dataset = item.payload.get("dataset", "")
        if not expr:
            print(f"[WARN] {cid[:16]} no expression in payload", flush=True)
            continue
        candidates[cid] = {"expression": expr, "dataset": dataset}
    if not candidates:
        print("No SIMULATION_UNCERTAIN candidates to reconcile.", flush=True)
        return 0
    print(f"Reconciling {len(candidates)} SIMULATION_UNCERTAIN candidates", flush=True)

    transport = browser_transport_module.BrowserBackedWorldQuantTransport(
        profile_dir=PROFILE, database=DB, lock_path=LOCK
    )
    try:
        transport.open()

        # 熔断器可能停在 RATE_LIMITED。retry_after_until 到期后，第一个请求必须显式
        # recovery_probe=True 的 GET 才被放行（access.py:216-219），普通 identity
        # 轮询会被 CircuitOpen 拒绝。这里只走正常 recovery 通道，不改写熔断状态。
        from alpha_mining.platform.access import CircuitOpen
        from alpha_mining.platform.client import BASE_URL

        assert transport.controller is not None
        access_state = transport.controller.status()
        print(f"[circuit] state={access_state.state} reason={access_state.reason!r}", flush=True)
        if access_state.state == "RATE_LIMITED":
            print(
                f"[circuit] retry_after_until={access_state.retry_after_until} "
                f"recovery_attempts={access_state.recovery_attempts}/{access_state.max_auto_recoveries}",
                flush=True,
            )
            try:
                probe = transport.request(
                    "GET",
                    f"{BASE_URL}/users/self",
                    endpoint_class="identity",
                    recovery_probe=True,
                )
            except CircuitOpen as exc:
                print(f"CIRCUIT_STILL_OPEN: {exc}", flush=True)
                print("熔断窗口未到期，请稍后重跑，不要手工改数据库。", flush=True)
                return 3
            print(f"[circuit] recovery probe -> HTTP {probe.status_code}", flush=True)
            after = transport.controller.status()
            print(f"[circuit] state={after.state} reason={after.reason!r}", flush=True)
            if after.state != "CLOSED":
                print(
                    "CIRCUIT_NOT_RECOVERED: recovery probe 未让熔断器闭合，停止本轮。",
                    flush=True,
                )
                return 3
            # 熔断器已恢复，但平台限流窗口可能未过期。等待 60 秒再发业务请求，
            # 避免紧接着的 list_alphas 再触发 429。
            import time
            print("[circuit] 熔断器已恢复，等待 60 秒让平台限流窗口过期...", flush=True)
            time.sleep(60)

        print("[auth] 若弹出登录/扫脸，请完成一次；之后自动继续。", flush=True)
        status = transport.wait_for_authentication(timeout_seconds=900.0)
        if status != 200:
            print(f"AUTH_REQUIRED: Chrome 已打开，identity probe = {status}", flush=True)
            print("等待用户完成扫脸。完成后重新运行此脚本。", flush=True)
            return 2

        print("[reconcile] 拉取平台最近 Alpha 列表 (UNSUBMITTED, -dateCreated, limit=100)", flush=True)
        gateway = PlatformGateway(
            database=DB, lock_path=LOCK, transport=transport, settings_schema_path=SCHEMA
        )
        try:
            listing = gateway.client.list_alphas(
                {"status": "UNSUBMITTED", "order": "-dateCreated", "limit": 100, "offset": 0}
            )
        except Exception as exc:
            print(f"ERROR: list_alphas failed: {type(exc).__name__}: {exc}", flush=True)
            print("平台仍在限流或网络故障，60秒等待不够。请 5 分钟后重新运行本脚本。", flush=True)
            return 3
        platform_alphas = listing.get("results") or listing.get("items") or []
        if not isinstance(platform_alphas, list):
            platform_alphas = []
        print(f"  平台最近 {len(platform_alphas)} 条 UNSUBMITTED Alpha", flush=True)

        # Build expression -> alpha_id map
        expr_map = {}
        for alpha in platform_alphas:
            alpha_id = str(alpha.get("id", "")).strip()
            if not alpha_id:
                continue
            expr = extract_platform_expression(alpha)
            if not expr:
                continue
            norm = normalize_expression(expr)
            if norm not in expr_map:
                expr_map[norm] = []
            expr_map[norm].append({"alpha_id": alpha_id, "created": alpha.get("dateCreated")})

        outcomes = []
        repost_queue = []
        for cid, meta in candidates.items():
            norm_expr = normalize_expression(meta["expression"])
            matches = expr_map.get(norm_expr, [])
            if len(matches) == 1:
                alpha_id = matches[0]["alpha_id"]
                print(f"[{cid[:16]}] RECOVERED from platform: {alpha_id}", flush=True)
                try:
                    detail = gateway.fetch_alpha(alpha_id)
                except Exception as exc:
                    print(f"  GET /alphas/{alpha_id} failed: {exc}", flush=True)
                    outcomes.append(
                        {
                            "candidate_id": cid,
                            "method": "RECOVERED_DETAIL_FAILED",
                            "alpha_id": alpha_id,
                            "error": str(exc)[:120],
                        }
                    )
                    continue
                metrics = extract_metrics(detail)
                checks = extract_checks(detail)
                store.transition(
                    cid,
                    "WAITING_CHECKS",
                    event_type="UNCERTAIN_RECONCILED_FROM_PLATFORM",
                    details={
                        "alpha_id": alpha_id,
                        "reason": "2fe45dc header bug: POST accepted but Location not captured",
                    },
                    alpha_id=alpha_id,
                    metrics=metrics,
                    checks=checks,
                )
                service = CandidateWorkflowService(DB, gateway, max_simulations_per_24h=100)
                try:
                    service.retry_item(cid)
                except Exception as exc:
                    print(f"  retry_item warning: {type(exc).__name__}: {exc}", flush=True)
                final_item = store.get_item(cid)
                outcomes.append(
                    {
                        "candidate_id": cid,
                        "method": "RECOVERED",
                        "alpha_id": alpha_id,
                        "sharpe": metrics.get("sharpe"),
                        "fitness": metrics.get("fitness"),
                        "turnover": metrics.get("turnover"),
                        "checks": checks,
                        "state": final_item.state if final_item else "UNKNOWN",
                    }
                )
            elif len(matches) > 1:
                print(
                    f"[{cid[:16]}] AMBIGUOUS: {len(matches)} platform Alphas match expression",
                    flush=True,
                )
                outcomes.append(
                    {
                        "candidate_id": cid,
                        "method": "AMBIGUOUS",
                        "alpha_id": "",
                        "error": f"{len(matches)} matches",
                    }
                )
            else:
                print(f"[{cid[:16]}] NOT_FOUND on platform -> will repost", flush=True)
                repost_queue.append(cid)

        if repost_queue:
            print(f"\n[repost] {len(repost_queue)} candidates NOT found on platform, simulating now", flush=True)
            service = CandidateWorkflowService(DB, gateway, max_simulations_per_24h=100)
            for cid in repost_queue:
                store.transition(
                    cid,
                    "PENDING_SIMULATION",
                    event_type="INFRA_BUG_REVERTED",
                    details={"reason": "BrowserResponse header bug fixed in 2fe45dc, not found on platform"},
                )
                item = store.get_item(cid)
                if item is None:
                    outcomes.append({"candidate_id": cid, "method": "REPOST_MISSING", "alpha_id": "", "error": "item lost"})
                    continue
                print(f"  [{cid[:16]}] simulating ...", flush=True)
                sim_outcome = service._simulate(item)
                final_item = store.get_item(cid)
                if sim_outcome == "SIMULATED" and final_item:
                    try:
                        detail = gateway.fetch_alpha(final_item.alpha_id)
                        metrics = extract_metrics(detail)
                        checks = extract_checks(detail)
                    except Exception:
                        metrics, checks = dict(final_item.metrics or {}), list(final_item.checks or [])
                    outcomes.append(
                        {
                            "candidate_id": cid,
                            "method": "REPOSTED",
                            "alpha_id": final_item.alpha_id or "",
                            "sharpe": metrics.get("sharpe"),
                            "fitness": metrics.get("fitness"),
                            "turnover": metrics.get("turnover"),
                            "checks": checks,
                            "state": final_item.state,
                        }
                    )
                else:
                    outcomes.append(
                        {
                            "candidate_id": cid,
                            "method": "REPOST_FAILED",
                            "alpha_id": getattr(final_item, "alpha_id", "") if final_item else "",
                            "state": final_item.state if final_item else "UNKNOWN",
                            "error": getattr(final_item, "last_error", "")[:200] if final_item else "",
                        }
                    )
    finally:
        transport.close()

    Path("tmp_reconcile_results.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== RECONCILIATION OUTCOMES ===", flush=True)
    ready_count = sum(1 for o in outcomes if o.get("state") == "READY_TO_SUBMIT")
    print(f"| candidate | method | alpha_id | Sharpe | Fitness | Turnover | checks | state |", flush=True)
    print(f"|---|---|---|---:|---:|---:|---|---|", flush=True)
    for o in outcomes:
        cid_short = o["candidate_id"][:16]
        method = o.get("method", "")
        alpha_id = o.get("alpha_id", "")[:16]
        sharpe = o.get("sharpe", "")
        fitness = o.get("fitness", "")
        turnover = o.get("turnover", "")
        checks_raw = o.get("checks") or []
        if isinstance(checks_raw, list) and checks_raw:
            passed = sum(1 for c in checks_raw if str(c.get("result", "")).upper() == "PASS")
            failed = [str(c.get("name", "?")) for c in checks_raw if str(c.get("result", "")).upper() == "FAIL"]
            checks_str = f"{passed}/{len(checks_raw)} PASS"
            if failed:
                checks_str += " | FAIL: " + ",".join(failed[:3])
        else:
            checks_str = ""
        state = o.get("state", "")
        error = o.get("error", "")
        if error:
            print(f"| {cid_short} | {method} | {alpha_id} | | | | {error[:30]} | {state} |", flush=True)
        else:
            print(
                f"| {cid_short} | {method} | {alpha_id} | {sharpe} | {fitness} | {turnover} | {checks_str} | {state} |",
                flush=True,
            )
    print(f"\nREADY_TO_SUBMIT = {ready_count} / {len(candidates)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
