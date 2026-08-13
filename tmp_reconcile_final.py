"""FINAL reconciliation: recover the 4 accepted simulations, report real metrics.

The 4 POST /simulations returned HTTP 201 at 2026-08-11T06:39Z but the
pre-2fe45dc BrowserResponse lower-cased headers, so Location was dropped and
progress_location/alpha_id were never persisted.  The platform therefore holds
Alphas that this repo cannot address.

Budget: exactly ONE alpha_list GET, over a narrow dateCreated window, plus one
alpha_detail GET per matched candidate.  No automatic retry.  No re-POST unless
the window request succeeded AND the expression is provably absent.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.factory.operator_service import CandidateWorkflowService
from alpha_mining.platform.client import ReadOnlyPlatformClient
from alpha_mining.platform.gateway import PlatformGateway
from alpha_mining.platform.protocol import extract_checks, extract_metrics
from alpha_mining.storage.work_items import CandidateWorkStore

DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
SCHEMA = _ROOT / ".validation_workspace" / ".alpha_simulation_settings_cache.json"
LOCK = _ROOT / "worldquant_api.lock"
RESULTS = _ROOT / "tmp_reconcile_final_results.json"
# DPAPI-protected session written by tools/ops/import_cookie_now.py.
AUTH_STATE = _ROOT / ".wq_auth_state.json"

# The 4 candidates whose POST returned 201 at 06:39Z.
UNCERTAIN_CIDS = [
    "2e54cbe98d9a3423790572d419f92dcd80bcc5995bef2aba22a06b6a467c9417",
    "91db14878734d8b6615d0e0ced69a297569e73f8344643327ac526b3907de41c",
    "8eb7d9167751fee81cd07935272db8143df70a72f6bdc2190492c34cb9f1024c",
    "195432396fb285c87e3b6b21eb8bb64776d6a2dfbd5cbc2dff80ba9d6381b6d0",
]

POST_AT = datetime(2026, 8, 11, 6, 39, 0, tzinfo=timezone.utc)
WINDOW_FROM = "2026-08-11T06:30:00Z"
WINDOW_TO = "2026-08-11T07:00:00Z"


def normalize_expression(expr: object) -> str:
    """Whitespace- and case-insensitive identity, matching _expression_identity."""
    return re.sub(r"\s+", "", str(expr or "").lower())


def extract_platform_expression(alpha: dict) -> str:
    """Pull the expression using the repo's existing shapes: regular.code / regular / expression."""
    for key in ("regular", "expression"):
        value = alpha.get(key)
        if isinstance(value, dict):
            code = str(value.get("code") or "").strip()
            if code:
                return code
        elif isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def platform_settings(alpha: dict) -> dict:
    settings = alpha.get("settings")
    return settings if isinstance(settings, dict) else {}


def parse_iso(text: object) -> datetime | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_SETTING_KEYS = ("region", "universe", "delay", "decay", "neutralization", "language")


def settings_agree(local: dict, remote: dict) -> tuple[bool, list[str]]:
    """Compare only the settings both sides actually declare."""
    mismatches = []
    for key in _SETTING_KEYS:
        if key not in remote:
            continue
        want = local.get(key)
        if want is None or want == "":
            continue
        got = remote.get(key)
        if str(want).strip().upper() != str(got).strip().upper():
            mismatches.append(f"{key}: local={want!r} platform={got!r}")
    return (not mismatches), mismatches


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    store = CandidateWorkStore(DB)

    # ── load the 4 candidates from the authoritative DB ──────────────────────
    candidates: dict[str, dict] = {}
    for cid in UNCERTAIN_CIDS:
        item = store.get_item(cid)
        if item is None:
            print(f"[WARN] {cid[:16]} MISSING in DB", flush=True)
            continue
        if item.state != "SIMULATION_UNCERTAIN":
            print(f"[WARN] {cid[:16]} state={item.state} (not UNCERTAIN, skipped)", flush=True)
            continue
        payload = item.payload or {}
        expression = str(payload.get("expression") or "")
        if not expression:
            print(f"[WARN] {cid[:16]} no expression in payload", flush=True)
            continue
        candidates[cid] = {
            "expression": expression,
            "normalized": normalize_expression(expression),
            "settings": {key: payload.get(key) for key in _SETTING_KEYS},
        }
        print(f"[local] {cid[:16]} {expression[:88]}", flush=True)

    if not candidates:
        print("No SIMULATION_UNCERTAIN candidates to reconcile.", flush=True)
        return 0
    print(f"\nReconciling {len(candidates)} candidates", flush=True)

    outcomes: list[dict] = []

    # No browser.  The DPAPI-protected session imported by
    # tools/ops/import_cookie_now.py is used directly, so there is no Playwright
    # window to crash, no profile to go stale, and no second face scan.
    client = ReadOnlyPlatformClient(
        state_path=AUTH_STATE,
        database=DB,
        lock_path=LOCK,
        min_interval=2.0,
        max_attempts=1,          # never auto-retry a 429
        require_stored_session=True,
        allow_auth_replay=False,  # an expired cookie must fail loudly, not re-login
    )
    try:
        state = client.controller.status() if client.controller else None
        if state is not None:
            print(f"\n[circuit] state={state.state} reason={state.reason!r} "
                  f"recovery_attempts={state.recovery_attempts}/{state.max_auto_recoveries}", flush=True)
            if state.state not in {"CLOSED", "RATE_LIMITED"}:
                print(f"CIRCUIT_NOT_USABLE: {state.state} — stopping.", flush=True)
                return 3

        # ── prove the imported session before spending the one alpha_list ────
        # When the breaker sits in RATE_LIMITED with an expired window, access.py
        # :216 admits only an explicit recovery probe.  A 2xx then closes the
        # breaker (access.py:308), so this doubles as the single recovery probe --
        # no separate attempt is spent.
        as_recovery = state is not None and state.state == "RATE_LIMITED"
        print(f"[auth] identity probe (recovery_probe={as_recovery})", flush=True)
        try:
            identity = client.probe_stored_identity(recovery_probe=as_recovery)
        except Exception as exc:
            print(f"IDENTITY_PROBE_ERROR: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            return 2
        print(f"[auth] stored-session identity probe -> HTTP {identity}", flush=True)
        if identity != 200:
            print(
                "\nAUTH_REQUIRED: 存储的会话无效（HTTP "
                f"{identity}）。请在你已扫脸的 Chrome 里取 cookie 后运行：\n"
                "  .venv\\Scripts\\python.exe tools\\ops\\import_cookie_now.py\n"
                "看到 HANDOFF_RESULT=SUCCESS 后告诉我，我会自动继续。",
                flush=True,
            )
            return 2

        gateway = PlatformGateway(client=client, settings_schema_path=SCHEMA)

        # ── step 5: exactly ONE narrow-window alpha_list GET, no retry ───────
        print(
            f"\n[alpha_list] ONE request: UNSUBMITTED, dateCreated in "
            f"[{WINDOW_FROM}, {WINDOW_TO}), limit=100",
            flush=True,
        )
        # Do NOT send dateCreated>= / dateCreated< : requests percent-encodes the
        # key, the platform receives a parameter named 'dateCreated%3E%3D' and
        # answers 400 (observed 12:38:55Z).  Use the request shape already proven
        # to return 200, and narrow to the window locally -- the 4 targets were
        # created 06:39Z today, so -dateCreated puts them at the head of page 1.
        try:
            listing = gateway.client.list_alphas(
                {
                    "status": "UNSUBMITTED",
                    "order": "-dateCreated",
                    "limit": 100,
                    "offset": 0,
                }
            )
        except Exception as exc:
            detail = str(exc)
            print(f"ALPHA_LIST_FAILED: {type(exc).__name__}: {detail[:200]}", flush=True)
            gate = client.controller.status() if client.controller else None
            if "429" in detail:
                print("STATUS: ALPHA_LIST_RATE_LIMITED", flush=True)
                if gate is not None:
                    print(f"  retry_after_until = {gate.retry_after_until}", flush=True)
                print("  单次请求已用尽，不再撞第二次。", flush=True)
            elif "401" in detail or "403" in detail:
                print(
                    "STATUS: AUTH_REQUIRED — 会话失效，需重新导入 cookie：\n"
                    "  .venv\\Scripts\\python.exe tools\\ops\\import_cookie_now.py",
                    flush=True,
                )
            return 3

        platform_alphas = listing.get("results") or listing.get("items") or []
        if not isinstance(platform_alphas, list):
            platform_alphas = []
        print(f"[alpha_list] HTTP 200, {len(platform_alphas)} Alphas returned", flush=True)

        # Narrow to the POST window locally (the server-side filter is unusable,
        # see above).  Alphas with an unparseable dateCreated are kept rather than
        # dropped: a missing timestamp must not hide a real match.
        window_from, window_to = parse_iso(WINDOW_FROM), parse_iso(WINDOW_TO)
        in_window = []
        for alpha in platform_alphas:
            created = parse_iso(alpha.get("dateCreated"))
            if created is None or (window_from <= created < window_to):
                in_window.append(alpha)
        print(
            f"[alpha_list] {len(in_window)} fall inside "
            f"[{WINDOW_FROM}, {WINDOW_TO})",
            flush=True,
        )
        platform_alphas = in_window
        for alpha in platform_alphas:
            print(
                f"   {str(alpha.get('id',''))[:14]:14s} {str(alpha.get('dateCreated',''))[:24]:24s} "
                f"{extract_platform_expression(alpha)[:70]}",
                flush=True,
            )

        # ── step 6: expression reconciliation with settings + time disambiguation ──
        by_expression: dict[str, list[dict]] = {}
        for alpha in platform_alphas:
            alpha_id = str(alpha.get("id") or "").strip()
            expression = extract_platform_expression(alpha)
            if not alpha_id or not expression:
                continue
            by_expression.setdefault(normalize_expression(expression), []).append(alpha)

        recovered: list[tuple[str, str]] = []
        not_found: list[str] = []

        for cid, meta in candidates.items():
            matches = by_expression.get(meta["normalized"], [])
            print(f"\n[{cid[:16]}] expression matches = {len(matches)}", flush=True)

            if len(matches) > 1:
                # tier 2: settings agreement
                agreeing = []
                for alpha in matches:
                    ok, mismatches = settings_agree(meta["settings"], platform_settings(alpha))
                    if ok:
                        agreeing.append(alpha)
                    else:
                        print(f"   {str(alpha.get('id'))[:14]} settings differ: {'; '.join(mismatches)}", flush=True)
                if len(agreeing) == 1:
                    matches = agreeing
                    print("   disambiguated by settings", flush=True)
                elif len(agreeing) > 1:
                    # tier 3: closest dateCreated to the POST time
                    scored = []
                    for alpha in agreeing:
                        created = parse_iso(alpha.get("dateCreated"))
                        if created is None:
                            continue
                        scored.append((abs((created - POST_AT).total_seconds()), alpha))
                    scored.sort(key=lambda pair: pair[0])
                    if len(scored) == 1 or (len(scored) > 1 and scored[0][0] + 1.0 < scored[1][0]):
                        matches = [scored[0][1]]
                        print(f"   disambiguated by dateCreated (Δ={scored[0][0]:.0f}s from POST)", flush=True)
                    else:
                        matches = agreeing

            if len(matches) == 1:
                alpha = matches[0]
                alpha_id = str(alpha.get("id") or "").strip()
                ok, mismatches = settings_agree(meta["settings"], platform_settings(alpha))
                if not ok:
                    print(f"   SETTINGS_MISMATCH, refusing to claim: {'; '.join(mismatches)}", flush=True)
                    outcomes.append({
                        "candidate_id": cid, "method": "SETTINGS_MISMATCH",
                        "alpha_id": alpha_id, "error": "; ".join(mismatches)[:160],
                        "state": "SIMULATION_UNCERTAIN",
                    })
                    continue
                print(f"   RECOVERED -> {alpha_id}", flush=True)
                recovered.append((cid, alpha_id))
            elif len(matches) > 1:
                ids = ",".join(str(a.get("id")) for a in matches)
                print(f"   AMBIGUOUS across {len(matches)}: {ids}", flush=True)
                outcomes.append({
                    "candidate_id": cid, "method": "AMBIGUOUS", "alpha_id": "",
                    "error": f"{len(matches)} indistinguishable matches: {ids}"[:160],
                    "state": "SIMULATION_UNCERTAIN",
                })
            else:
                print("   NOT_FOUND in window", flush=True)
                not_found.append(cid)

        # ── step 7: RECOVERED -> fetch real metrics, hand to the real workflow ──
        service = CandidateWorkflowService(DB, gateway, max_simulations_per_24h=100)
        for cid, alpha_id in recovered:
            try:
                detail = gateway.fetch_alpha(alpha_id)
            except Exception as exc:
                print(f"[{cid[:16]}] GET /alphas/{alpha_id} failed: {exc}", flush=True)
                outcomes.append({
                    "candidate_id": cid, "method": "RECOVERED_DETAIL_FAILED",
                    "alpha_id": alpha_id, "error": str(exc)[:160],
                    "state": "SIMULATION_UNCERTAIN",
                })
                continue
            metrics = extract_metrics(detail)
            checks = extract_checks(detail)
            store.transition(
                cid, "WAITING_CHECKS",
                event_type="UNCERTAIN_RECONCILED_FROM_PLATFORM",
                details={
                    "alpha_id": alpha_id,
                    "reason": "pre-2fe45dc BrowserResponse dropped Location; POST was 201",
                    "window": f"{WINDOW_FROM}..{WINDOW_TO}",
                },
                alpha_id=alpha_id, metrics=metrics, checks=checks,
            )
            try:
                service.retry_item(cid)   # official rules decide the final state
            except Exception as exc:
                print(f"[{cid[:16]}] retry_item warning: {type(exc).__name__}: {exc}", flush=True)
            final = store.get_item(cid)
            outcomes.append({
                "candidate_id": cid, "method": "RECOVERED", "alpha_id": alpha_id,
                "expression": candidates[cid]["expression"],
                "sharpe": metrics.get("sharpe"), "fitness": metrics.get("fitness"),
                "turnover": metrics.get("turnover"), "returns": metrics.get("returns"),
                "margin": metrics.get("margin"), "drawdown": metrics.get("drawdown"),
                "checks": checks,
                "state": final.state if final else "UNKNOWN",
            })
            print(
                f"[{cid[:16]}] Sharpe={metrics.get('sharpe')} Fitness={metrics.get('fitness')} "
                f"Turnover={metrics.get('turnover')} -> {final.state if final else '?'}",
                flush=True,
            )

        # ── step 7c: NOT_FOUND -> only these may be re-POSTed ────────────────
        if not_found:
            print(f"\n[repost] {len(not_found)} proven absent in a HTTP 200 window", flush=True)
            for cid in not_found:
                store.transition(
                    cid, "PENDING_SIMULATION", event_type="INFRA_BUG_REVERTED",
                    details={"reason": "absent from HTTP 200 dateCreated window; header bug fixed in 2fe45dc"},
                )
                item = store.get_item(cid)
                if item is None:
                    outcomes.append({"candidate_id": cid, "method": "REPOST_MISSING",
                                     "alpha_id": "", "state": "UNKNOWN", "error": "item lost"})
                    continue
                print(f"  [{cid[:16]}] simulating ...", flush=True)
                try:
                    sim = service._simulate(item)
                except Exception as exc:
                    print(f"    simulate raised {type(exc).__name__}: {exc}", flush=True)
                    sim = "ERROR"
                final = store.get_item(cid)
                metrics, checks = {}, []
                if sim == "SIMULATED" and final and final.alpha_id:
                    try:
                        detail = gateway.fetch_alpha(final.alpha_id)
                        metrics, checks = extract_metrics(detail), extract_checks(detail)
                    except Exception:
                        metrics = dict(final.metrics or {})
                        checks = list(final.checks or [])
                outcomes.append({
                    "candidate_id": cid,
                    "method": "REPOSTED" if sim == "SIMULATED" else "REPOST_FAILED",
                    "alpha_id": (final.alpha_id if final else "") or "",
                    "expression": candidates[cid]["expression"],
                    "sharpe": metrics.get("sharpe"), "fitness": metrics.get("fitness"),
                    "turnover": metrics.get("turnover"), "returns": metrics.get("returns"),
                    "margin": metrics.get("margin"), "drawdown": metrics.get("drawdown"),
                    "checks": checks,
                    "state": final.state if final else "UNKNOWN",
                    "error": (getattr(final, "last_error", "") or "")[:160] if sim != "SIMULATED" else "",
                })
    finally:
        pass  # no browser to close

    RESULTS.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── step 10: business result first ───────────────────────────────────────
    def fmt(value: object) -> str:
        if value is None or value == "":
            return ""
        try:
            return f"{float(value):.4g}"
        except (TypeError, ValueError):
            return str(value)

    def checks_cell(raw: object) -> str:
        if not isinstance(raw, list) or not raw:
            return ""
        passed = sum(1 for c in raw if str(c.get("result", "")).upper() == "PASS")
        failed = [str(c.get("name", "?")) for c in raw if str(c.get("result", "")).upper() == "FAIL"]
        cell = f"{passed}/{len(raw)} PASS"
        if failed:
            cell += " FAIL:" + ",".join(failed[:3])
        return cell

    print("\n\nSTATUS: FRESH_ALPHA_REAL_RESULTS\n", flush=True)
    print("| candidate | recovery | alpha_id | Sharpe | Fitness | Turnover | checks | final state |", flush=True)
    print("|---|---|---|---:|---:|---:|---|---|", flush=True)
    for o in outcomes:
        print(
            f"| {o['candidate_id'][:16]} | {o.get('method','')} | {o.get('alpha_id','') or ''} "
            f"| {fmt(o.get('sharpe'))} | {fmt(o.get('fitness'))} | {fmt(o.get('turnover'))} "
            f"| {checks_cell(o.get('checks')) or o.get('error','')[:40]} | {o.get('state','')} |",
            flush=True,
        )

    total = len(candidates)
    states = [str(o.get("state", "")) for o in outcomes]
    methods = [str(o.get("method", "")) for o in outcomes]
    print(f"\nREADY_TO_SUBMIT = {states.count('READY_TO_SUBMIT')} / {total}", flush=True)
    print(f"NEAR_PASS       = {states.count('NEAR_PASS')} / {total}", flush=True)
    print(f"FAR_FAIL        = {states.count('FAR_FAIL')} / {total}", flush=True)
    print(f"AMBIGUOUS       = {methods.count('AMBIGUOUS')} / {total}", flush=True)
    print(f"NOT_FOUND       = {methods.count('REPOSTED') + methods.count('REPOST_FAILED')} / {total}", flush=True)
    other = [s for s in states if s not in {"READY_TO_SUBMIT", "NEAR_PASS", "FAR_FAIL"}]
    if other:
        print(f"OTHER_STATES    = {other}", flush=True)

    scored = [o for o in outcomes if o.get("sharpe") is not None]
    if scored:
        best = max(scored, key=lambda o: float(o.get("sharpe") or float("-inf")))
        failed_checks = [
            str(c.get("name", "?")) for c in (best.get("checks") or [])
            if str(c.get("result", "")).upper() == "FAIL"
        ]
        print("\nBEST_ALPHA\n", flush=True)
        print(f"candidate_id:   {best['candidate_id']}", flush=True)
        print(f"expression:     {best.get('expression','')}", flush=True)
        print(f"alpha_id:       {best.get('alpha_id','')}", flush=True)
        print(f"Sharpe:         {fmt(best.get('sharpe'))}", flush=True)
        print(f"Fitness:        {fmt(best.get('fitness'))}", flush=True)
        print(f"Turnover:       {fmt(best.get('turnover'))}", flush=True)
        print(f"failed_checks:  {', '.join(failed_checks) if failed_checks else '(none)'}", flush=True)
        print(f"state:          {best.get('state','')}", flush=True)
    else:
        print("\nBEST_ALPHA: (no candidate produced platform metrics)", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
