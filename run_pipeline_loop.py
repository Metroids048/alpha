#!/usr/bin/env python3
"""Outer loop: simulate batch → record feedback → sleep → repeat (subprocess isolation)."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

STATE_FILENAME = "pipeline_loop_state.json"
LOG_FILENAME = "pipeline_loop.log"
HOPEFUL_JSONL = "hopeful_alphas.jsonl"
SUBMISSION_JSONL = "submission_results.jsonl"
CYCLE_SCRIPT = "run_pipeline_cycle.py"


class RecoveryCategory(str, Enum):
    SUCCESS = "SUCCESS"
    RECOVERABLE_CYCLE_FAILURE = "RECOVERABLE_CYCLE_FAILURE"
    NETWORK_ERROR = "NETWORK_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    DATABASE_LOCKED = "DATABASE_LOCKED"
    CHILD_PROCESS_CRASH = "CHILD_PROCESS_CRASH"
    UNKNOWN_RUNTIME_ERROR = "UNKNOWN_RUNTIME_ERROR"


@dataclass(frozen=True)
class CycleOutcome:
    cycle: int
    rc: int
    category: RecoveryCategory
    consecutive_failures: int = 0
    task_id: str = ""
    input_id: str = ""
    retry_after_seconds: float | None = None
    traceback_text: str = ""
    detail: str = ""
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class ChildProcessResult:
    rc: int
    output_tail: str = ""


def _sanitize_diagnostic(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)\b(password|passwd|token|cookie|authorization)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    return re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", text)


def _outcome_from_child_result(
    *, cycle: int, task_id: str, result: ChildProcessResult
) -> CycleOutcome:
    outcome = _outcome_from_rc(cycle, result.rc)
    tail = _sanitize_diagnostic(result.output_tail)
    if result.rc in {1} and "traceback (most recent call last)" in tail.lower():
        outcome = replace(outcome, category=RecoveryCategory.CHILD_PROCESS_CRASH)
    return replace(
        outcome,
        task_id=str(task_id),
        traceback_text=tail if "traceback" in tail.lower() else "",
        detail=tail[-4000:] if result.rc != 0 else "",
    )


def _outcome_from_rc(cycle: int, rc: int) -> CycleOutcome:
    categories = {
        0: RecoveryCategory.SUCCESS,
        1: RecoveryCategory.RECOVERABLE_CYCLE_FAILURE,
        3: RecoveryCategory.NETWORK_ERROR,
        4: RecoveryCategory.AUTH_ERROR,
        5: RecoveryCategory.RATE_LIMITED,
        6: RecoveryCategory.DATABASE_LOCKED,
    }
    category = (
        RecoveryCategory.CHILD_PROCESS_CRASH
        if int(rc) < 0
        else categories.get(int(rc), RecoveryCategory.UNKNOWN_RUNTIME_ERROR)
    )
    return CycleOutcome(cycle=int(cycle), rc=int(rc), category=category)


def _recovery_delay(
    outcome: CycleOutcome, *, consecutive_failures: int, inter_cycle_sleep: float
) -> float:
    if outcome.retry_after_seconds is not None:
        return max(0.0, float(outcome.retry_after_seconds))
    base = max(0.0, float(inter_cycle_sleep))
    if outcome.category is RecoveryCategory.SUCCESS:
        return base
    exponent = min(max(0, int(consecutive_failures) - 1), 6)
    return min(900.0, max(1.0, base) * (2**exponent))


def run_forever(
    *,
    cycle_runner: Callable[[int], int | CycleOutcome],
    stop_requested: Callable[[], bool],
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_outcome: Callable[[CycleOutcome], None] | None = None,
    inter_cycle_sleep: float = 120.0,
    start_cycle: int = 1,
) -> int:
    """Run recoverable cycles until an explicit stop request is observed."""

    cycle = max(1, int(start_cycle))
    consecutive_failures = 0

    def should_stop() -> bool:
        try:
            return bool(stop_requested())
        except Exception as exc:
            detail = _sanitize_diagnostic(f"{type(exc).__name__}: {exc}")
            print(f"[loop] warn stop probe failed: {detail}")
            return False

    while not should_stop():
        started_at = 0.0
        try:
            started_at = float(clock())
            raw = cycle_runner(cycle)
            outcome = (
                raw
                if isinstance(raw, CycleOutcome)
                else _outcome_from_rc(cycle, int(raw))
            )
        except Exception as exc:
            outcome = CycleOutcome(
                cycle=cycle,
                rc=1,
                category=RecoveryCategory.UNKNOWN_RUNTIME_ERROR,
                traceback_text=_sanitize_diagnostic(traceback.format_exc()),
                detail=_sanitize_diagnostic(f"{type(exc).__name__}: {exc}"),
            )
        if outcome.category is RecoveryCategory.SUCCESS:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
        try:
            elapsed_seconds = max(0.0, float(clock()) - started_at)
        except Exception:
            elapsed_seconds = 0.0
        outcome = replace(
            outcome,
            consecutive_failures=consecutive_failures,
            elapsed_seconds=elapsed_seconds,
        )
        if on_outcome is not None:
            try:
                on_outcome(outcome)
            except Exception as exc:
                detail = _sanitize_diagnostic(f"{type(exc).__name__}: {exc}")
                print(f"[loop] warn outcome persistence failed: {detail}")
        cycle += 1
        if should_stop():
            break
        sleeper(
            _recovery_delay(
                outcome,
                consecutive_failures=consecutive_failures,
                inter_cycle_sleep=inter_cycle_sleep,
            )
        )
    return 0


def _utc() -> str:
    from alpha_mining.common import utc_iso

    return utc_iso()


def _python_exe() -> str:
    return sys.executable


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _persisted_retry_after_seconds(
    database: Path, *, now: datetime | None = None
) -> float | None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        with sqlite3.connect(database) as con:
            row = con.execute(
                "SELECT state,retry_after_until FROM platform_access_state WHERE singleton=1"
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row or str(row[0]) != "RATE_LIMITED" or not row[1]:
        return None
    try:
        deadline = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, (deadline.astimezone(timezone.utc) - current).total_seconds())


# Exit code emitted by the cycle subprocess (auto_alpha_pipeline_rebuilt_v50.NETWORK_EXIT_CODE)
# when the API/proxy is unreachable. Kept in sync as a literal to avoid importing the
# heavy pipeline module just to read one constant.
NETWORK_EXIT_CODE = 3


def _parse_host_port(proxy: str) -> tuple[str, int] | None:
    from urllib.parse import urlparse

    proxy = proxy.strip()
    if not proxy:
        return None
    u = urlparse(proxy if "://" in proxy else "http://" + proxy)
    if not u.hostname:
        return None
    return (u.hostname, int(u.port or 80))


def _proxy_endpoint(
    passthrough: list[str], env: dict[str, str] | None = None
) -> tuple[str, int] | None:
    """Resolve the HTTPS proxy (host, port) the cycle subprocess will use, or None.

    Mirrors the precedence in auto_alpha_pipeline_rebuilt_v50: explicit --https-proxy
    forwarded through the loop wins, then HTTPS_PROXY / https_proxy env vars.
    """
    env = env if env is not None else os.environ  # type: ignore[assignment]
    proxy = ""
    for i, tok in enumerate(passthrough):
        if tok == "--https-proxy" and i + 1 < len(passthrough):
            proxy = passthrough[i + 1]
        elif tok.startswith("--https-proxy="):
            proxy = tok.split("=", 1)[1]
    if not proxy:
        proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or ""
    return _parse_host_port(proxy)


def _tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_network(
    passthrough: list[str],
    state: dict[str, Any],
    state_path: Path,
    *,
    initial: int = 60,
    cap: int = 900,
    timeout: float = 3.0,
    env: dict[str, str] | None = None,
    sleeper: Callable[[float], object] | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
) -> bool:
    """Block until the configured proxy is reachable (pause-and-wait, auto-resume).

    No proxy configured → no cheap reachability target, so this is a no-op (the
    rc==NETWORK_EXIT_CODE path still handles a direct-API outage mid-cycle).
    Backoff escalates initial→2x→…→cap so a long outage doesn't hammer the port.
    """
    ep = _proxy_endpoint(passthrough, env=env)
    if ep is None:
        return True
    host, port = ep
    sleep = sleeper or time.sleep
    wait = max(1, int(initial))
    waited_total = 0
    was_down = False
    while not _tcp_reachable(host, port, timeout=timeout):
        if stop_requested():
            return False
        was_down = True
        print(f"[loop] network unreachable proxy={host}:{port}; waiting {wait}s")
        state.update(
            {
                "network_unreachable": True,
                "network_proxy": f"{host}:{port}",
                "last_network_wait_utc": _utc(),
            }
        )
        _save_state(state_path, state)
        sleep(wait)
        waited_total += wait
        if stop_requested():
            return False
        wait = min(int(cap), wait * 2)
    if was_down:
        print(f"[loop] network restored proxy={host}:{port} after ~{waited_total}s")
    state.update({"network_unreachable": False, "last_network_ok_utc": _utc()})
    _save_state(state_path, state)
    return True


def _build_cycle_cmd(
    *,
    cycle_script: Path,
    mode: str,
    batch_size: int | None,
    resilient: bool,
    passthrough: list[str],
    recheck_extra: list[str] | None = None,
) -> list[str]:
    # -u: unbuffered stdout/stderr so loop terminal shows progress immediately
    cmd = [_python_exe(), "-u", str(cycle_script)]
    if resilient:
        cmd.append("--resilient-async")
    cmd.extend(["--mode", mode])
    if batch_size is not None and mode in ("full", "simulate"):
        cmd.extend(
            [
                "--run-payload-cap",
                str(batch_size),
                "--target-simulate-batch",
                str(batch_size),
                "--min-simulate-batch",
                str(batch_size),
            ]
        )
    cmd.extend(passthrough)
    if recheck_extra:
        cmd.extend(recheck_extra)
    return cmd


def _build_recheck_extra_args(args: argparse.Namespace) -> list[str]:
    """Bounded recheck for outer loop — avoid multi-day 256-item marathon drain."""
    out: list[str] = []
    max_items = int(getattr(args, "recheck_max_items", 15) or 0)
    wall = float(getattr(args, "recheck_wall_budget_seconds", 1800.0) or 0.0)
    quick = float(getattr(args, "recheck_quick_timeout_seconds", 600.0) or 600.0)
    if max_items > 0:
        out.extend(["--recheck-max-items", str(max_items)])
    if wall > 0:
        out.extend(["--recheck-wall-budget-seconds", str(wall)])
    if quick > 0:
        out.extend(["--recheck-quick-timeout-seconds", str(quick)])
    return out


def _build_passthrough_args(args: argparse.Namespace) -> list[str]:
    passthrough: list[str] = []
    for item in args.passthrough or []:
        if item == "--":
            continue
        passthrough.append(item)
    has_preset = any(
        item == "--preset" or item.startswith("--preset=") for item in passthrough
    )
    if not has_preset:
        passthrough[0:0] = [
            "--preset",
            str(getattr(args, "strategy_preset", "diverse_exploration")),
        ]
    if args.execute_submit:
        passthrough.append("--execute-submit")
    if args.dry_run_submit and _should_run_submit_drain(args):
        passthrough.append("--dry-run-submit")
    skip_prebatch = bool(getattr(args, "no_prebatch_recheck", False)) or not bool(
        getattr(args, "inline_recheck", False)
    )
    if skip_prebatch and "--no-prebatch-recheck" not in passthrough:
        passthrough.append("--no-prebatch-recheck")
    # Post-batch recheck stays on by default (bounded) — digest needs_recheck without blocking the loop.
    if (
        bool(getattr(args, "no_postbatch_recheck", False))
        and "--no-postbatch-recheck" not in passthrough
    ):
        passthrough.append("--no-postbatch-recheck")
    if "--recheck-postbatch-max-items" not in passthrough:
        passthrough.extend(
            [
                "--recheck-postbatch-max-items",
                str(int(getattr(args, "postbatch_recheck_max_items", 4))),
            ]
        )
    if "--recheck-postbatch-wall-budget-seconds" not in passthrough:
        passthrough.extend(
            [
                "--recheck-postbatch-wall-budget-seconds",
                str(
                    float(getattr(args, "postbatch_recheck_wall_budget_seconds", 180.0))
                ),
            ]
        )
    return passthrough


def _should_run_recheck_cycle(args: argparse.Namespace, cycle: int) -> bool:
    every = int(getattr(args, "recheck_every_cycles", 0) or 0)
    if every <= 0:
        return False
    return int(cycle) > 0 and int(cycle) % every == 0


def _should_run_submit_drain(args: argparse.Namespace) -> bool:
    if bool(getattr(args, "skip_submit", False)):
        return False
    return bool(
        getattr(args, "submit_drain", False) or getattr(args, "execute_submit", False)
    )


def _run_subprocess(
    cmd: list[str], *, cwd: Path, label: str, log_path: Path | None = None
) -> ChildProcessResult:
    """Run child process with live stdout (no popup window on Windows).

    Previously used capture_output=True, which hid all v50 progress until exit.
    """
    from alpha_mining.common import subprocess_no_window_kwargs

    mode = "?"
    if "--mode" in cmd:
        mi = cmd.index("--mode")
        if mi + 1 < len(cmd):
            mode = cmd[mi + 1]
    log_tag = f" log={log_path.name}" if log_path else ""
    print(f"[loop] {label} mode={mode}{log_tag}")

    popen_kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        **subprocess_no_window_kwargs(),
    }
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    popen_kwargs["env"] = env

    log_file = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")
        log_file.write(f"\n--- {_utc()} {label} ---\n")
        log_file.flush()

    output_tail: deque[str] = deque(maxlen=80)
    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            output_tail.append(line)
            sys.stdout.write(line)
            if not line.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
            if log_file is not None:
                log_file.write(line if line.endswith("\n") else line + "\n")
                log_file.flush()
    finally:
        if log_file is not None:
            log_file.close()

    rc = int(proc.wait())
    print(f"[loop] {label} exit_code={rc}")
    return ChildProcessResult(rc=rc, output_tail="".join(output_tail))


def _count_ready(
    root: Path,
    *,
    max_queue_similarity: float,
) -> int:
    from alpha_mining.scheduler.queue_probe import count_ready_to_submit

    return count_ready_to_submit(
        root / HOPEFUL_JSONL,
        root / SUBMISSION_JSONL,
        max_queue_similarity=max_queue_similarity,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pipeline outer loop: simulate-only batch → feedback → repeat"
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=60,
        help="Simulate payloads per cycle (default: 60 pilot).",
    )
    p.add_argument(
        "--strategy-preset",
        choices=[
            "conservative",
            "pv",
            "fundamental",
            "mixed",
            "challenge",
            "diverse_exploration",
        ],
        default="diverse_exploration",
        help="Pipeline strategy preset forwarded to every cycle.",
    )
    p.add_argument(
        "--resume-from-cycle", type=int, default=1, help="First cycle number to run."
    )
    p.add_argument(
        "--inter-cycle-sleep", type=int, default=120, help="Seconds between cycles."
    )
    p.add_argument(
        "--no-network-gate",
        dest="network_gate",
        action="store_false",
        default=True,
        help="Disable the pre-cycle proxy reachability gate (default: gate enabled).",
    )
    p.add_argument(
        "--network-check-timeout",
        type=float,
        default=3.0,
        help="TCP connect timeout (seconds) for the proxy reachability check.",
    )
    p.add_argument(
        "--network-wait-initial-seconds",
        type=int,
        default=60,
        help="First backoff when the proxy is unreachable (doubles up to the cap).",
    )
    p.add_argument(
        "--network-wait-cap-seconds",
        type=int,
        default=900,
        help="Maximum backoff (seconds) while waiting for the proxy to return.",
    )
    p.add_argument(
        "--max-submit-rounds",
        type=int,
        default=50,
        help="Max submit subprocess rounds per cycle.",
    )
    p.add_argument(
        "--submit-sleep",
        type=int,
        default=45,
        help="Seconds between submit rounds when queue non-empty.",
    )
    p.add_argument(
        "--resilient-async",
        action="store_true",
        help="Enable network-resilient async simulate patch.",
    )
    p.add_argument(
        "--no-prebatch-recheck",
        action="store_true",
        help="Forward to pipeline: skip pre-batch recheck only (post-batch still runs, bounded).",
    )
    p.add_argument(
        "--no-postbatch-recheck",
        action="store_true",
        help="Forward to pipeline: skip bounded post-batch recheck after simulate.",
    )
    p.add_argument(
        "--postbatch-recheck-max-items",
        type=int,
        default=4,
        help="Max needs_recheck rows per simulate cycle via post-batch recheck (default 4).",
    )
    p.add_argument(
        "--postbatch-recheck-wall-budget-seconds",
        type=float,
        default=180.0,
        help="Wall time for post-batch recheck each cycle (default 180s).",
    )
    p.add_argument(
        "--inline-recheck",
        action="store_true",
        help="Restore old behavior: run pre/post recheck inside each simulate cycle.",
    )
    p.add_argument(
        "--recheck-every-cycles",
        type=int,
        default=0,
        help="Run a bounded --mode recheck every N cycles (0 disables). Default: 0 (simulate-only loop).",
    )
    p.add_argument(
        "--recheck-max-items",
        type=int,
        default=15,
        help="When periodic recheck runs, process at most this many needs_recheck rows (default 15).",
    )
    p.add_argument(
        "--recheck-wall-budget-seconds",
        type=float,
        default=1800.0,
        help="Wall time cap for periodic recheck subprocess (default 1800s = 30min).",
    )
    p.add_argument(
        "--recheck-quick-timeout-seconds",
        type=float,
        default=600.0,
        help="Per-alpha check timeout during periodic recheck (default 600s).",
    )
    p.add_argument(
        "--skip-submit",
        action="store_true",
        help="Only run simulate (full) each cycle.",
    )
    p.add_argument(
        "--submit-drain",
        action="store_true",
        help="Explicitly drain the submit queue after each simulate cycle. Default is simulate-only.",
    )
    p.add_argument(
        "--execute-submit",
        action="store_true",
        help="Deprecated and blocked; use vNext guarded submit execute.",
    )
    p.add_argument(
        "--dry-run-submit",
        action="store_true",
        help="Pass --dry-run-submit to pipeline.",
    )
    p.add_argument("--max-queue-similarity", type=float, default=0.72)
    p.add_argument("--state-file", default=STATE_FILENAME)
    p.add_argument(
        "--log-file",
        default=LOG_FILENAME,
        help="Append child stdout to this file (default: pipeline_loop.log in workspace).",
    )
    p.add_argument(
        "--no-log-file", action="store_true", help="Do not write pipeline_loop.log."
    )
    p.add_argument(
        "--workspace",
        default="",
        help="Repo root (default: directory containing this script).",
    )
    p.add_argument("--database", default="research_memory.sqlite")
    p.add_argument(
        "--pipeline-script",
        default="alpha_mining.factory.runtime",
        help="Recorded for compatibility logs only; cycle entry uses the vNext factory runtime.",
    )
    p.add_argument(
        "passthrough",
        nargs=argparse.REMAINDER,
        help="Extra args forwarded to run_pipeline_cycle (prefix with --)",
    )
    return p.parse_args()


def _run_pipeline_cycle_once(
    *,
    args: argparse.Namespace,
    cycle: int,
    root: Path,
    cycle_script: Path,
    passthrough: list[str],
    state: dict[str, Any],
    state_path: Path,
    log_path: Path | None,
    batch_size: int,
    run_submit_drain: bool,
    stop_requested: Callable[[], bool],
    network_sleeper: Callable[[float], object] = time.sleep,
) -> CycleOutcome:
    t0 = time.time()
    print(f"[loop] ===== cycle {cycle} =====")

    if bool(getattr(args, "network_gate", True)):
        network_ready = _wait_for_network(
            passthrough,
            state,
            state_path,
            initial=int(args.network_wait_initial_seconds),
            cap=int(args.network_wait_cap_seconds),
            timeout=float(args.network_check_timeout),
            sleeper=network_sleeper,
            stop_requested=stop_requested,
        )
        if not network_ready:
            return CycleOutcome(
                cycle=cycle,
                rc=0,
                category=RecoveryCategory.SUCCESS,
                task_id=f"cycle_{cycle}/network_wait",
                detail="explicit stop requested during network wait",
            )

    sim_cmd = _build_cycle_cmd(
        cycle_script=cycle_script,
        mode="full",
        batch_size=batch_size,
        resilient=bool(args.resilient_async),
        passthrough=passthrough,
    )
    sim_result = _run_subprocess(
        sim_cmd, cwd=root, label=f"cycle_{cycle}/simulate", log_path=log_path
    )
    sim_rc = sim_result.rc
    elapsed = time.time() - t0
    print(f"[loop] cycle {cycle} simulate finished in {elapsed / 3600:.1f}h rc={sim_rc}")

    recheck_rc = 0
    if _should_run_recheck_cycle(args, cycle):
        recheck_passthrough = [x for x in passthrough if x != "--no-prebatch-recheck"]
        recheck_extra = _build_recheck_extra_args(args)
        print(
            f"[loop] periodic recheck: max_items={int(args.recheck_max_items)} "
            f"wall={float(args.recheck_wall_budget_seconds):.0f}s "
            f"per_alpha={float(args.recheck_quick_timeout_seconds):.0f}s"
        )
        recheck_cmd = _build_cycle_cmd(
            cycle_script=cycle_script,
            mode="recheck",
            batch_size=None,
            resilient=False,
            passthrough=recheck_passthrough,
            recheck_extra=recheck_extra,
        )
        recheck_result = _run_subprocess(
            recheck_cmd,
            cwd=root,
            label=f"cycle_{cycle}/recheck",
            log_path=log_path,
        )
        recheck_rc = recheck_result.rc
        if recheck_rc != 0:
            print(f"[loop] warn recheck exit={recheck_rc}; continuing loop")

    submit_rounds = 0
    submit_rc = 0
    if run_submit_drain:
        while submit_rounds < int(args.max_submit_rounds):
            ready = _count_ready(
                root,
                max_queue_similarity=float(args.max_queue_similarity),
            )
            if ready <= 0:
                print(f"[loop] cycle {cycle} submit queue empty (ready=0)")
                break
            submit_rounds += 1
            print(f"[loop] cycle {cycle} submit round {submit_rounds} ready={ready}")
            sub_cmd = _build_cycle_cmd(
                cycle_script=cycle_script,
                mode="submit",
                batch_size=None,
                resilient=False,
                passthrough=passthrough,
            )
            submit_result = _run_subprocess(
                sub_cmd,
                cwd=root,
                label=f"cycle_{cycle}/submit_{submit_rounds}",
                log_path=log_path,
            )
            submit_rc = submit_result.rc
            if submit_rc != 0:
                print(f"[loop] warn submit exit={submit_rc}; rechecking queue")
            ready_after = _count_ready(
                root,
                max_queue_similarity=float(args.max_queue_similarity),
            )
            if ready_after <= 0:
                break
            time.sleep(max(5, int(args.submit_sleep)))

    elapsed = time.time() - t0
    state.update(
        {
            "last_cycle": cycle,
            "last_utc": _utc(),
            "last_sim_exit": sim_rc,
            "last_recheck_exit": recheck_rc,
            "last_submit_exit": submit_rc,
            "last_submit_rounds": submit_rounds,
            "batch_size": batch_size,
            "elapsed_seconds": round(elapsed, 1),
        }
    )
    _save_state(state_path, state)
    print(
        f"[loop] cycle {cycle} done elapsed={elapsed:.0f}s "
        f"sim_rc={sim_rc} submit_rounds={submit_rounds}"
    )
    return _outcome_from_child_result(
        cycle=cycle,
        task_id=f"cycle_{cycle}/simulate",
        result=sim_result,
    )


def main() -> int:
    from alpha_mining.common import load_workspace_env

    load_workspace_env(_ROOT / ".env")
    args = parse_args()
    if args.execute_submit:
        print(
            "[loop] BLOCKED: legacy --execute-submit is disabled; use python -m alpha_mining submit execute"
        )
        return 2
    root = Path(args.workspace).resolve() if args.workspace else _ROOT
    from alpha_mining.factory.control import FactoryControl

    database = Path(args.database)
    if not database.is_absolute():
        database = root / database
    control = FactoryControl(database)
    factory_state = control.status()
    if factory_state.hard_stop:
        print(f"[loop] BLOCKED hard_stop=1 reason={factory_state.reason}")
        return 2
    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = root / state_path
    log_path: Path | None = None
    if not args.no_log_file:
        log_path = Path(args.log_file)
        if not log_path.is_absolute():
            log_path = root / log_path
    cycle_script = root / CYCLE_SCRIPT
    if not cycle_script.is_file():
        print(f"[loop] error: missing {cycle_script}")
        return 2

    passthrough = _build_passthrough_args(args)
    run_submit_drain = _should_run_submit_drain(args)

    state = _load_state(state_path)
    batch_size = max(1, int(args.batch_size))

    print(
        f"[loop] start workspace={root} batch_size={batch_size} cycles=unbounded "
        f"resilient={args.resilient_async} submit_drain={run_submit_drain}"
    )
    if log_path is not None:
        print(f"[loop] log_file={log_path}")

    stop_event = threading.Event()
    signal_number = 0

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal signal_number
        signal_number = int(signum)
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, request_stop)

    def stop_requested() -> bool:
        if stop_event.is_set():
            return True
        try:
            current = control.status()
            return bool(
                current.hard_stop
                and current.stop_kind in {"manual", "security", "data_integrity"}
            )
        except (OSError, RuntimeError):
            return False

    def cycle_runner(cycle: int) -> CycleOutcome:
        outcome = _run_pipeline_cycle_once(
            args=args,
            cycle=cycle,
            root=root,
            cycle_script=cycle_script,
            passthrough=passthrough,
            state=state,
            state_path=state_path,
            log_path=log_path,
            batch_size=batch_size,
            run_submit_drain=run_submit_drain,
            stop_requested=stop_requested,
            network_sleeper=stop_event.wait,
        )
        if outcome.category is RecoveryCategory.RATE_LIMITED:
            outcome = replace(
                outcome,
                retry_after_seconds=_persisted_retry_after_seconds(database),
            )
        return outcome

    def record_outcome(outcome: CycleOutcome) -> None:
        state.update(
            {
                "consecutive_cycle_failures": outcome.consecutive_failures,
                "last_outcome_category": outcome.category.value,
                "last_exception": outcome.detail,
                "last_traceback": outcome.traceback_text,
                "next_cycle": outcome.cycle + 1,
            }
        )
        if outcome.category is RecoveryCategory.SUCCESS:
            state["last_success_at"] = _utc()
        else:
            state["last_failure_at"] = _utc()
            state["last_failure_category"] = outcome.category.value
        _save_state(state_path, state)
        try:
            control.record_cycle_outcome(
                cycle=outcome.cycle,
                category=outcome.category.value,
                rc=outcome.rc,
                consecutive_failures=outcome.consecutive_failures,
                task_id=outcome.task_id,
                input_id=outcome.input_id,
                retry_after_seconds=outcome.retry_after_seconds,
                detail=outcome.detail,
                traceback_text=outcome.traceback_text,
            )
        except Exception as exc:
            print(f"[loop] warn incident persistence failed: {type(exc).__name__}: {exc}")

    try:
        result = run_forever(
            cycle_runner=cycle_runner,
            stop_requested=stop_requested,
            sleeper=stop_event.wait,
            on_outcome=record_outcome,
            inter_cycle_sleep=max(0, int(args.inter_cycle_sleep)),
            start_cycle=max(1, int(args.resume_from_cycle)),
        )
    except KeyboardInterrupt:
        signal_number = int(getattr(signal, "SIGINT", 2))
        stop_event.set()
        result = 0

    state["stopped_at"] = _utc()
    state["stop_signal"] = signal_number
    _save_state(state_path, state)
    if signal_number:
        print(f"[loop] stopped by signal={signal_number}")
        return 130 if signal_number == int(getattr(signal, "SIGINT", 2)) else 0
    print("[loop] stopped by factory control")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
