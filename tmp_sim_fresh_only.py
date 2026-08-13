"""TEMPORARY: simulate ONLY the explicitly listed fresh candidates.

prepare_once() scans the whole PENDING_SIMULATION queue, which would burn
simulations on the 17 pre-fix candidates.  This harness reuses the production
service/transport built by 提交Alpha.py and calls _simulate() on the given IDs
only.  No description PATCH, no real submit.  Delete after Batch A.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import importlib.util

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

_spec = importlib.util.spec_from_file_location("submit_entry", _ROOT / "提交Alpha.py")
assert _spec is not None and _spec.loader is not None
submit_entry = importlib.util.module_from_spec(_spec)
sys.modules["submit_entry"] = submit_entry
_spec.loader.exec_module(submit_entry)

DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-id", action="append", required=True)
    parser.add_argument("--browser-profile-dir", default=".validation_workspace/wq_browser_profile")
    parser.add_argument("--lock-path", default="worldquant_api.lock")
    parser.add_argument("--auth-timeout", type=float, default=900.0)
    parser.add_argument(
        "--settings-schema",
        default=".validation_workspace/.alpha_simulation_settings_cache.json",
    )
    parser.add_argument(
        "--reset-far-fail",
        action="store_true",
        help="restore listed candidates from an environment-caused FAR_FAIL back to PENDING_SIMULATION",
    )
    args = parser.parse_args()

    if args.reset_far_fail:
        from alpha_mining.storage.work_items import CandidateWorkStore

        store = CandidateWorkStore(DB)
        for cid in args.candidate_id:
            item = store.get_item(cid)
            if item is None:
                print(f"  {cid[:16]} MISSING", flush=True)
                continue
            if item.state != "FAR_FAIL":
                print(f"  {cid[:16]} state={item.state} (not FAR_FAIL, left alone)", flush=True)
                continue
            if str(item.last_error_category or "") != "INVALID_SIMULATION_SETTINGS":
                print(
                    f"  {cid[:16]} FAR_FAIL but error_category="
                    f"{item.last_error_category!r} -> NOT an environment fault, left alone",
                    flush=True,
                )
                continue
            store.transition(
                cid,
                "PENDING_SIMULATION",
                event_type="ENVIRONMENT_FAULT_REVERTED",
                details={"reason": "local settings-schema path fault, not a platform quality verdict"},
            )
            print(f"  {cid[:16]} FAR_FAIL -> PENDING_SIMULATION (restored)", flush=True)
        print("reset complete", flush=True)
        return 0

    # The production gateway defaults settings_schema_path to the project root,
    # but this validation run keeps its catalog + settings snapshot under
    # .validation_workspace (same dir 生成Alpha.py used via --catalog-dir).
    schema = Path(args.settings_schema)
    if not schema.is_file():
        print(f"BLOCKED: settings schema not found: {schema}", flush=True)
        return 2
    print(f"settings schema: {schema}", flush=True)

    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha_mining.platform import browser_transport as browser_transport_module
    from alpha_mining.platform.gateway import PlatformGateway

    transport = browser_transport_module.BrowserBackedWorldQuantTransport(
        profile_dir=Path(args.browser_profile_dir),
        database=DB,
        lock_path=Path(args.lock_path),
    )
    try:
        transport.open()
        print("[sim] 若弹出登录/扫脸，请完成一次；4 条共用同一会话。", flush=True)
        status = transport.wait_for_authentication(timeout_seconds=args.auth_timeout)
        if status != 200:
            raise RuntimeError(f"AUTH_PAUSED: browser identity probe returned HTTP {status}")
        gateway = PlatformGateway(
            database=DB,
            lock_path=Path(args.lock_path),
            transport=transport,
            settings_schema_path=schema,
        )
        service = CandidateWorkflowService(DB, gateway, max_simulations_per_24h=100)
    except BaseException:
        transport.close()
        raise
    print(f"service ready; simulating {len(args.candidate_id)} fresh candidates only", flush=True)

    results = []
    try:
        for idx, cid in enumerate(args.candidate_id, 1):
            item = service.store.get_item(cid)
            if item is None:
                print(f"[{idx}] {cid[:16]} MISSING", flush=True)
                results.append({"candidate_id": cid, "outcome": "MISSING"})
                continue
            if item.state != "PENDING_SIMULATION":
                print(f"[{idx}] {cid[:16]} state={item.state} -> skipped", flush=True)
                results.append({"candidate_id": cid, "outcome": f"SKIPPED_STATE:{item.state}"})
                continue
            print(f"[{idx}] {cid[:16]} simulating ...", flush=True)
            outcome = service._simulate(item)
            after = service.store.get_item(cid)
            row = {
                "candidate_id": cid,
                "outcome": outcome,
                "state": after.state if after else None,
                "alpha_id": getattr(after, "alpha_id", None),
                "error_category": getattr(after, "last_error_category", None),
                "error": (getattr(after, "last_error", None) or "")[:200],
            }
            results.append(row)
            print(f"    -> {json.dumps(row, ensure_ascii=False)}", flush=True)
            if outcome == "AUTH_PAUSED":
                print("AUTH_PAUSED: stopping batch (session needs a login)", flush=True)
                break
    finally:
        if transport is not None:
            transport.close()

    Path("tmp_sim_fresh_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n=== BATCH A OUTCOMES ===", flush=True)
    for r in results:
        print(json.dumps(r, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
