#!/usr/bin/env python3
"""Watchdog: restart pipeline on crash; wait if another pipeline instance is already running."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
LOG_NAME = "pipeline_supervisor.log"
STATE_NAME = "pipeline_supervisor_state.json"
AUTH_STATE_NAME = ".wq_auth_state.json"


def _utc() -> str:
    from alpha_mining.common import utc_iso

    return utc_iso()


def _log(path: Path, msg: str) -> None:
    line = f"{_utc()} {msg}"
    print(line, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _subprocess_hidden() -> dict[str, Any]:
    from alpha_mining.common import subprocess_no_window_kwargs

    return subprocess_no_window_kwargs()


def _pipeline_pids() -> list[int]:
    """Return PIDs of python processes running alpha pipeline scripts (Windows)."""
    if os.name != "nt":
        return []
    match_markers = ("auto_alpha_pipeline_rebuilt", "run_pipeline_cycle", "run_pipeline_loop")
    exclude = "run_pipeline_supervisor"
    try:
        r = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='python.exe'",
                "get",
                "ProcessId,CommandLine",
                "/FORMAT:CSV",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_ROOT),
            **_subprocess_hidden(),
        )
    except Exception:
        return []
    out: list[int] = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("Node,"):
            continue
        if exclude in line:
            continue
        if not any(marker in line for marker in match_markers):
            continue
        m = re.search(r",(\d+)\s*$", line)
        if m:
            out.append(int(m.group(1)))
    return out


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _child_environment(environment: dict[str, str], auth_state_path: Path) -> dict[str, str]:
    from alpha_mining.auth.session_manager import prepare_child_environment

    return prepare_child_environment(environment, auth_state_path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Supervise pipeline: wait / restart on failure")
    p.add_argument("--max-restarts", type=int, default=200, help="Max restart attempts (0=infinite).")
    p.add_argument("--restart-sleep", type=int, default=90, help="Seconds before restart after failure.")
    p.add_argument("--wait-for-idle", action="store_true", default=True, help="Wait until no pipeline PID (default).")
    p.add_argument("--poll-interval", type=int, default=45, help="Seconds between idle checks.")
    p.add_argument("--log-file", default=LOG_NAME)
    p.add_argument("--state-file", default=STATE_NAME)
    p.add_argument("--auth-state-file", default=AUTH_STATE_NAME)
    p.add_argument("--database", default="research_memory.sqlite")
    p.add_argument(
        "loop_args",
        nargs=argparse.REMAINDER,
        help="Args for run_pipeline_loop.py after --",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log_path = _ROOT / args.log_file
    state_path = _ROOT / args.state_file
    configured_auth_path = Path(args.auth_state_file)
    auth_state_path = configured_auth_path if configured_auth_path.is_absolute() else _ROOT / configured_auth_path
    loop_script = _ROOT / "run_pipeline_loop.py"
    if not loop_script.is_file():
        _log(log_path, f"[supervisor] FATAL missing {loop_script}")
        return 2

    from alpha_mining.factory.control import FactoryControl

    database = Path(args.database)
    if not database.is_absolute():
        database = _ROOT / database
    factory_state = FactoryControl(database).status()
    if factory_state.hard_stop:
        _log(log_path, f"[supervisor] BLOCKED hard_stop=1 reason={factory_state.reason}")
        return 2

    loop_args = list(args.loop_args or [])
    if loop_args and loop_args[0] == "--":
        loop_args = loop_args[1:]
    if not loop_args:
        # Do NOT pass --no-prebatch-recheck here: it is not a loop.py flag (argparse rc=2).
        # run_pipeline_loop.py auto-forwards it to run_pipeline_cycle.
        loop_args = [
            "--batch-size",
            "300",
            "--inter-cycle-sleep",
            "120",
            "--resilient-async",
        ]

    state = _load_state(state_path)
    restarts = int(state.get("restarts", 0))
    max_restarts = int(args.max_restarts)

    _log(log_path, f"[supervisor] start max_restarts={max_restarts or 'inf'} loop_args={loop_args!r}")

    if args.wait_for_idle:
        while True:
            pids = _pipeline_pids()
            if not pids:
                break
            _log(log_path, f"[supervisor] waiting for existing pipeline PIDs={pids}")
            time.sleep(max(10, int(args.poll_interval)))

    while True:
        if max_restarts > 0 and restarts >= max_restarts:
            _log(log_path, f"[supervisor] stop: reached max_restarts={max_restarts}")
            return 0

        cmd = [sys.executable, str(loop_script), *loop_args]
        from alpha_mining.auth.session_manager import auth_state_status

        auth_status = auth_state_status(auth_state_path)
        _log(log_path, f"[supervisor] launch restart#{restarts} auth_state={auth_status} cmd={' '.join(cmd)}")
        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_ROOT),
                capture_output=True,
                text=True,
                env=_child_environment(dict(os.environ), auth_state_path),
                **_subprocess_hidden(),
            )
            rc = int(proc.returncode)
            if proc.stdout:
                for line in proc.stdout.strip().splitlines()[-8:]:
                    _log(log_path, f"[loop/out] {line}")
            if rc != 0 and proc.stderr:
                _log(log_path, f"[loop/err] {proc.stderr.strip()[:800]}")
        except KeyboardInterrupt:
            _log(log_path, "[supervisor] interrupted")
            return 130
        except Exception as e:
            rc = -1
            _log(log_path, f"[supervisor] launch error: {e}")

        elapsed = time.time() - t0
        _log(log_path, f"[supervisor] exited rc={rc} elapsed={elapsed:.0f}s")
        state["last_rc"] = rc
        state["last_elapsed"] = round(elapsed, 1)
        state["last_utc"] = _utc()

        if rc == 0:
            _log(log_path, "[supervisor] loop exited cleanly (max_cycles reached or normal stop)")
            state["restarts"] = restarts
            _save_state(state_path, state)
            return 0

        restarts += 1
        state["restarts"] = restarts
        _save_state(state_path, state)
        sleep_s = max(30, int(args.restart_sleep))
        _log(log_path, f"[supervisor] restart in {sleep_s}s (attempt {restarts})")
        time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
