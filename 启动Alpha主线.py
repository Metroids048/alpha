"""唯一中文生产入口：恢复会话后转发到受控 Alpha 主线 supervisor。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from run_pipeline_supervisor import main


_ROOT = Path(__file__).resolve().parent


def _auth_state_path() -> Path:
    configured = os.environ.get("WQ_AUTH_STATE_FILE", "").strip()
    path = Path(configured or ".wq_auth_state.json")
    return path if path.is_absolute() else _ROOT / path


def _read_auth_status(path: Path) -> str:
    try:
        from alpha_mining.auth.session_manager import auth_state_status

        return str(auth_state_status(path)).upper()
    except Exception:
        return "UNKNOWN"


def _maybe_refresh_browser_session(*, runner=subprocess.run) -> int:
    """Renew stale browser state before the loop; headed login is the safe fallback."""
    if any(flag in sys.argv[1:] for flag in ("-h", "--help")):
        return 0
    if not os.environ.get("WQ_USERNAME", "").strip():
        return 0
    state_path = _auth_state_path()
    if _read_auth_status(state_path) not in {"STALE", "MISSING", "INVALID"}:
        return 0

    profile_dir = _ROOT / ".wq_browser_profile"
    base = [
        sys.executable,
        "-m",
        "alpha_mining",
        "platform",
        "browser-login",
        "--auth-state-file",
        str(state_path),
        "--profile-dir",
        str(profile_dir),
    ]
    print("[entry] authentication session is stale; trying trusted browser renewal", flush=True)
    headless = runner(
        [*base, "--headless", "--timeout", "30"],
        cwd=str(_ROOT),
        check=False,
    )
    if int(headless.returncode) == 0:
        return 0

    print(
        "[entry] trusted browser renewal failed; opening headed login for one-time verification",
        flush=True,
    )
    headed = runner(
        [*base, "--timeout", "300"],
        cwd=str(_ROOT),
        check=False,
    )
    return int(headed.returncode)

if __name__ == "__main__":
    from alpha_mining.common import load_workspace_env

    load_workspace_env(_ROOT / ".env")
    auth_rc = _maybe_refresh_browser_session()
    if auth_rc:
        raise SystemExit(auth_rc)
    raise SystemExit(main())
