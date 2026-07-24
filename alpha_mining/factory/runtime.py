"""Authoritative vNext cycle entry used by the preserved outer loop."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import traceback
from typing import Sequence

from .control import FactoryControl


def recovery_exit_code(exc: BaseException) -> int:
    from alpha_mining.auth.session_manager import (
        AuthDailyLimitExceeded,
        AuthenticationFailed,
        AuthStateError,
    )
    from alpha_mining.platform.access import CircuitOpen

    if isinstance(exc, CircuitOpen):
        return 5
    if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower():
        return 6
    if isinstance(exc, (AuthenticationFailed, AuthDailyLimitExceeded, AuthStateError)):
        return 4
    message = str(exc).lower()
    if isinstance(exc, PermissionError) and any(
        token in message for token in ("authentication", "http 401", "session expired")
    ):
        return 4
    try:
        import requests

        if isinstance(exc, requests.RequestException):
            return 3
    except ImportError:
        pass
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return 3
    return 7


def _sanitize_diagnostic(text: str) -> str:
    return re.sub(
        r"(?i)\b(password|passwd|token|cookie|authorization)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )


def _sanitized_traceback() -> str:
    return _sanitize_diagnostic(traceback.format_exc())


def cycle_exit_code(summary: object) -> int:
    """Keep an empty batch visible to the outer recovery loop.

    A zero-candidate cycle is not a fatal factory stop, but reporting it as a
    success causes the loop to spin on the same exhausted request identities.
    """

    failed = int(getattr(summary, "failed", 0) or 0)
    generated = int(getattr(summary, "generated", 0) or 0)
    simulated = int(getattr(summary, "simulated", 0) or 0)
    return 1 if failed > 0 or (generated == 0 and simulated == 0) else 0


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
    if (
        args.mode not in {"preflight", "audit", "status"}
        and state.hard_stop
        and state.stop_kind in {"manual", "security", "data_integrity"}
    ):
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
        return 5
    except Exception as exc:
        detail = _sanitize_diagnostic(f"{type(exc).__name__}: {exc}")
        print(f"[factory] platform cycle failed: {detail}")
        print(_sanitized_traceback(), end="")
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
                    return 5
        except Exception:
            pass
        return recovery_exit_code(exc)
    print(f"[factory] {json.dumps(summary.__dict__, sort_keys=True)}")
    if summary.generated == 0 and summary.simulated == 0:
        print(
            "[factory] EMPTY_CANDIDATE_BATCH: no new simulation request was claimed; "
            "the outer loop will record a recoverable failure and back off"
        )
    return cycle_exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
