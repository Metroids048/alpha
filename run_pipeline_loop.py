#!/usr/bin/env python3
"""Outer loop: simulate batch → record feedback → sleep → repeat (subprocess isolation)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

STATE_FILENAME = "pipeline_loop_state.json"
LOG_FILENAME = "pipeline_loop.log"
HOPEFUL_JSONL = "hopeful_alphas.jsonl"
SUBMISSION_JSONL = "submission_results.jsonl"
CYCLE_SCRIPT = "run_pipeline_cycle.py"

# Keep in sync with auto_alpha_pipeline_rebuilt_v50.AUTH_FATAL_EXIT_CODE.
# On this exit code the loop stops immediately and writes a sentinel file.
AUTH_FATAL_EXIT_CODE = 4
SENTINEL_FILENAME = "pipeline_loop_blocked.flag"


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


# Exit code emitted by the cycle subprocess (auto_alpha_pipeline_rebuilt_v50.NETWORK_EXIT_CODE)
# when the API/proxy is unreachable. Kept in sync as a literal to avoid importing the
# heavy pipeline module just to read one constant.
NETWORK_EXIT_CODE = 3


def _write_sentinel(root: Path, reason: str) -> None:
    path = root / SENTINEL_FILENAME
    path.write_text(f"{_utc()} {reason}\n", encoding="utf-8")
    print(f"[loop] BLOCKED sentinel written -> {path}")


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
) -> None:
    """Block until the configured proxy is reachable (pause-and-wait, auto-resume).

    No proxy configured → no cheap reachability target, so this is a no-op (the
    rc==NETWORK_EXIT_CODE path still handles a direct-API outage mid-cycle).
    Backoff escalates initial→2x→…→cap so a long outage doesn't hammer the port.
    """
    ep = _proxy_endpoint(passthrough, env=env)
    if ep is None:
        return
    host, port = ep
    wait = max(1, int(initial))
    waited_total = 0
    was_down = False
    while not _tcp_reachable(host, port, timeout=timeout):
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
        time.sleep(wait)
        waited_total += wait
        wait = min(int(cap), wait * 2)
    if was_down:
        print(f"[loop] network restored proxy={host}:{port} after ~{waited_total}s")
    state.update({"network_unreachable": False, "last_network_ok_utc": _utc()})
    _save_state(state_path, state)


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
) -> int:
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

    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
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
    return rc


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
        "--max-cycles", type=int, default=0, help="Stop after N cycles (0 = infinite)."
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
    p.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=10,
        help=(
            "Stop and write a sentinel file after this many consecutive non-network "
            "simulate failures (default: 10, 0 = disabled)."
        ),
    )
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
    factory_state = FactoryControl(database).status()
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
    cycle = max(1, int(args.resume_from_cycle))
    max_cycles = int(args.max_cycles)
    batch_size = max(1, int(args.batch_size))
    max_consecutive = int(getattr(args, "max_consecutive_failures", 10) or 0)
    consecutive_failures = 0

    print(
        f"[loop] start workspace={root} batch_size={batch_size} max_cycles={max_cycles or 'inf'} "
        f"resilient={args.resilient_async} submit_drain={run_submit_drain}"
    )
    if log_path is not None:
        print(f"[loop] log_file={log_path}")

    try:
        while True:
            if max_cycles > 0 and cycle > max_cycles:
                print(f"[loop] reached max_cycles={max_cycles}")
                break

            t0 = time.time()
            print(f"[loop] ===== cycle {cycle} =====")

            if bool(getattr(args, "network_gate", True)):
                _wait_for_network(
                    passthrough,
                    state,
                    state_path,
                    initial=int(args.network_wait_initial_seconds),
                    cap=int(args.network_wait_cap_seconds),
                    timeout=float(args.network_check_timeout),
                )

            sim_cmd = _build_cycle_cmd(
                cycle_script=cycle_script,
                mode="full",
                batch_size=batch_size,
                resilient=bool(args.resilient_async),
                passthrough=passthrough,
            )
            sim_rc = _run_subprocess(
                sim_cmd, cwd=root, label=f"cycle_{cycle}/simulate", log_path=log_path
            )
            elapsed = time.time() - t0
            print(
                f"[loop] cycle {cycle} simulate finished in {elapsed / 3600:.1f}h rc={sim_rc}"
            )

            if sim_rc == NETWORK_EXIT_CODE:
                # API/proxy was unreachable — not a real cycle. Do NOT advance the
                # cycle counter; pause for connectivity and retry the same cycle.
                consecutive_failures = 0
                state.update(
                    {
                        "last_failure_utc": _utc(),
                        "last_failure_kind": "network_unreachable",
                        "last_failure_exit": sim_rc,
                        "next_cycle": cycle,
                        "consecutive_failures": 0,
                    }
                )
                _save_state(state_path, state)
                print(
                    f"[loop] cycle {cycle} hit network failure (rc={sim_rc}); "
                    f"pausing for connectivity (cycle not advanced)"
                )
                if bool(getattr(args, "network_gate", True)):
                    _wait_for_network(
                        passthrough,
                        state,
                        state_path,
                        initial=int(args.network_wait_initial_seconds),
                        cap=int(args.network_wait_cap_seconds),
                        timeout=float(args.network_check_timeout),
                    )
                else:
                    time.sleep(max(60, int(args.inter_cycle_sleep)))
                continue

            recheck_rc = 0
            if _should_run_recheck_cycle(args, cycle):
                recheck_passthrough = [
                    x for x in passthrough if x != "--no-prebatch-recheck"
                ]
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
                recheck_rc = _run_subprocess(
                    recheck_cmd,
                    cwd=root,
                    label=f"cycle_{cycle}/recheck",
                    log_path=log_path,
                )
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
                    print(
                        f"[loop] cycle {cycle} submit round {submit_rounds} ready={ready}"
                    )
                    sub_cmd = _build_cycle_cmd(
                        cycle_script=cycle_script,
                        mode="submit",
                        batch_size=None,
                        resilient=False,
                        passthrough=passthrough,
                    )
                    submit_rc = _run_subprocess(
                        sub_cmd,
                        cwd=root,
                        label=f"cycle_{cycle}/submit_{submit_rounds}",
                        log_path=log_path,
                    )
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
                f"[loop] cycle {cycle} done elapsed={elapsed:.0f}s sim_rc={sim_rc} submit_rounds={submit_rounds}"
            )

            if sim_rc == AUTH_FATAL_EXIT_CODE:
                reason = f"auth_fatal rc={sim_rc} cycle={cycle} — check credentials / auth state"
                state.update(
                    {
                        "last_failure_utc": _utc(),
                        "last_failure_kind": "auth_fatal",
                        "last_failure_exit": sim_rc,
                        "next_cycle": cycle,
                    }
                )
                _save_state(state_path, state)
                _write_sentinel(root, reason)
                print(f"[loop] STOP: {reason}")
                return 1
            elif sim_rc != 0:
                consecutive_failures += 1
                state.update(
                    {
                        "last_failure_utc": _utc(),
                        "last_failure_kind": "recoverable_child_failure",
                        "last_failure_exit": sim_rc,
                        "next_cycle": cycle + 1,
                        "consecutive_failures": consecutive_failures,
                    }
                )
                _save_state(state_path, state)
                if max_consecutive > 0 and consecutive_failures >= max_consecutive:
                    reason = (
                        f"consecutive_failures={consecutive_failures} reached max={max_consecutive} "
                        f"(last rc={sim_rc} cycle={cycle})"
                    )
                    _write_sentinel(root, reason)
                    print(f"[loop] STOP: {reason}")
                    return 1
                print(
                    f"[loop] simulate failed (rc={sim_rc}) consecutive={consecutive_failures}; "
                    f"backing off, then continuing with cycle {cycle + 1}"
                )
                time.sleep(max(60, int(args.inter_cycle_sleep)))
            else:
                consecutive_failures = 0

            cycle += 1
            if max_cycles > 0 and cycle > max_cycles:
                break
            time.sleep(max(0, int(args.inter_cycle_sleep)))

    except KeyboardInterrupt:
        print("[loop] interrupted")
        state["interrupted_at"] = _utc()
        _save_state(state_path, state)
        return 130

    print("[loop] finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
