"""Authoritative vNext cycle entry used by the preserved outer loop."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from .control import FactoryControl


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alpha_mining.factory.runtime")
    parser.add_argument("--database", default="research_memory.sqlite")
    parser.add_argument("--mode", default="full")
    parser.add_argument("--run-payload-cap", type=int, default=60)
    parser.add_argument("--target-simulate-batch", type=int)
    parser.add_argument("--auth-state-file", default=".wq_auth_state.json")
    parser.add_argument("--lock-path", default="worldquant_api.lock")
    parser.add_argument("--min-interval", type=float, default=2.0)
    parser.add_argument("--resilient-async", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    control = FactoryControl(args.database)
    state = control.status()
    if args.mode not in {"preflight", "audit", "status"} and state.hard_stop:
        print(f"[factory] BLOCKED hard_stop=1 reason={state.reason}")
        return 2
    if args.mode in {"preflight", "audit", "status"}:
        print(json.dumps(state.__dict__, sort_keys=True))
        return 0
    if args.mode == "submit":
        print("[factory] BLOCKED: loop submit mode cannot bypass guarded submit execute")
        return 2
    if args.mode == "recheck":
        print("[factory] recheck queue is empty or handled by fresh ledger synchronization")
        return 0
    if args.mode not in {"full", "simulate"}:
        print(f"[factory] unsupported mode={args.mode}")
        return 2
    if not control.can_generate():
        print("[factory] BLOCKED: COMPLETE ledger and cluster freeze are required")
        return 2
    from alpha_mining.factory.orchestrator import FactoryOrchestrator
    from alpha_mining.platform.gateway import PlatformGateway
    from alpha_mining.platform.access import CircuitOpen

    batch_size = int(args.target_simulate_batch or args.run_payload_cap)
    gateway = PlatformGateway(
        state_path=args.auth_state_file,
        database=args.database,
        lock_path=args.lock_path,
        min_interval=max(0.0, float(args.min_interval)),
    )
    try:
        summary = FactoryOrchestrator(args.database, gateway).run_simulate(
            batch_size=batch_size
        )
    except CircuitOpen as exc:
        print(f"[factory] RATE_LIMITED: {exc}")
        return 4
    except Exception as exc:
        print(f"[factory] platform cycle failed: {type(exc).__name__}: {exc}")
        # Check if the error is due to 429 by inspecting access state
        import sqlite3
        from pathlib import Path
        try:
            with sqlite3.connect(Path(args.database)) as con:
                access_state = con.execute(
                    "SELECT state FROM platform_access_state WHERE singleton=1"
                ).fetchone()
                if access_state and str(access_state[0]) in {"RATE_LIMITED", "MANUAL_INTERVENTION"}:
                    print("[factory] RATE_LIMITED state detected after exception")
                    return 4
        except Exception:
            pass
        return 3
    print(f"[factory] {json.dumps(summary.__dict__, sort_keys=True)}")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
