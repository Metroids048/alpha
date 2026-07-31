#!/usr/bin/env python3
"""Watchdog: restart pipeline on crash; wait if another pipeline instance is already running."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
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
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _subprocess_hidden() -> dict[str, Any]:
    from alpha_mining.common import subprocess_no_window_kwargs

    return subprocess_no_window_kwargs()


@dataclass(frozen=True)
class PipelineProcess:
    pid: int
    parent_pid: int
    command_line: str


def _parse_pipeline_processes(output: str) -> list[PipelineProcess]:
    match_markers = ("auto_alpha_pipeline_rebuilt", "run_pipeline_cycle", "run_pipeline_loop")
    exclude = "run_pipeline_supervisor"
    rows: list[PipelineProcess] = []
    reader = csv.DictReader(io.StringIO(output or ""))
    for raw_row in reader:
        row = {str(key or "").strip(): value for key, value in raw_row.items()}
        command_line = str(row.get("CommandLine") or "").strip()
        if exclude in command_line or not any(marker in command_line for marker in match_markers):
            continue
        try:
            pid = int(str(row.get("ProcessId") or "").strip())
            parent_pid = int(str(row.get("ParentProcessId") or "0").strip())
        except ValueError:
            continue
        rows.append(PipelineProcess(pid, parent_pid, command_line))
    return rows


def _pipeline_processes() -> list[PipelineProcess]:
    """Return Python processes running Alpha pipeline scripts on Windows."""
    if os.name != "nt":
        return []
    try:
        r = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='python.exe'",
                "get",
                "ProcessId,ParentProcessId,CommandLine",
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
    return _parse_pipeline_processes(r.stdout or "")


def _pipeline_pids() -> list[int]:
    return [process.pid for process in _pipeline_processes()]


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _orphaned_pipeline_roots(
    processes: list[PipelineProcess],
    *,
    pid_exists: Any = _pid_exists,
) -> list[PipelineProcess]:
    pipeline_pids = {process.pid for process in processes}
    roots = [process for process in processes if process.parent_pid not in pipeline_pids]
    return [process for process in roots if not pid_exists(process.parent_pid)]


def _terminate_process_tree(process: PipelineProcess) -> bool:
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_ROOT),
            **_subprocess_hidden(),
        )
    except Exception:
        return False
    return int(result.returncode) == 0


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
        ]
    if "--resilient-async" in loop_args:
        _log(
            log_path,
            "[supervisor] WARNING --resilient-async is a legacy P2 path and is not enabled by default",
        )

    state = _load_state(state_path)
    restarts = int(state.get("restarts", 0))
    max_restarts = int(args.max_restarts)

    _log(log_path, f"[supervisor] start max_restarts={max_restarts or 'inf'} loop_args={loop_args!r}")

    if args.wait_for_idle:
        while True:
            processes = _pipeline_processes()
            if not processes:
                break
            orphaned_roots = _orphaned_pipeline_roots(processes)
            if orphaned_roots:
                stale_pids = [process.pid for process in processes]
                root_pids = [process.pid for process in orphaned_roots]
                _log(
                    log_path,
                    "[supervisor] taking over orphaned pipeline "
                    f"roots={root_pids} tree_pids={stale_pids}",
                )
                if not all(_terminate_process_tree(process) for process in orphaned_roots):
                    _log(log_path, "[supervisor] BLOCKED failed to stop orphaned pipeline")
                    return 2
                time.sleep(1)
                continue
            pids = [process.pid for process in processes]
            _log(
                log_path,
                f"[supervisor] existing pipeline is active PIDs={pids}; no duplicate started",
            )
            return 0

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
