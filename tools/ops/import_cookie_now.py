"""Import a browser session cookie into the DPAPI auth state.

Run this once after each biometric (Persona) login on platform.worldquantbrain.com.
The platform requires a browser-minted session; password POST /authentication is
answered with 401 on a Persona-gated account.

How to get the cookie header:
  1. Log in at https://platform.worldquantbrain.com (complete the face scan).
  2. F12 -> Application -> Cookies -> platform.worldquantbrain.com
  3. Copy the `t` value (and `cf_clearance` if present).

Usage (the cookie is read from stdin so it never lands in a file or shell history):
  python import_cookie_now.py
  # paste: t=<jwt>; cf_clearance=<value>
  # then press Enter on a blank line

Only the `t` and `cf_clearance` cookies are stored; everything else is discarded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Resolve from the repository root so invocation cwd cannot select a second
# auth-state file.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STATE_PATH = REPO_ROOT / ".wq_auth_state.json"
RECOVERY_DATABASE = REPO_ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
RECOVERY_LOCK = REPO_ROOT / "worldquant_api.lock"


def _read_cookie_header() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    print("Paste the Cookie header (t=...; cf_clearance=...), then a blank line:")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines)


def _probe_recovery_auth_state(
    state_path: Path = STATE_PATH,
    database: Path = RECOVERY_DATABASE,
    lock_path: Path = RECOVERY_LOCK,
) -> int:
    """Prove the imported state through recovery's exact read-only client."""
    from alpha_mining.auth.session_manager import auth_state_metadata
    from alpha_mining.platform.client import ReadOnlyPlatformClient

    metadata = auth_state_metadata(state_path)
    print(f"RECOVERY_AUTH_STATE_PATH={state_path}")
    print("RECOVERY_AUTH_MECHANISM=DPAPI")
    print(f"AUTH_STATE_GENERATION={metadata['generation']}")
    client = ReadOnlyPlatformClient(
        state_path=state_path,
        database=database,
        lock_path=lock_path,
        min_interval=2.0,
    )
    try:
        status = client.probe_stored_identity(recovery_probe=True)
    except Exception as exc:
        print(f"PROGRAM_PROBE_ERROR={type(exc).__name__}")
        print("HANDOFF_RESULT=BROKEN")
        return 1
    print(f"PROGRAM_PROBE={status}")
    if status != 200:
        print("HANDOFF_RESULT=BROKEN")
        return 1
    print("AUTHENTICATED_IDENTITY=CONFIRMED")
    print("JWT_SESSION=VALID")
    print("HANDOFF_RESULT=SUCCESS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import and prove a browser session for recovery")
    parser.add_argument("--auth-state-file", default=str(STATE_PATH))
    parser.add_argument("--database", default=str(RECOVERY_DATABASE))
    parser.add_argument("--lock-path", default=str(RECOVERY_LOCK))
    args = parser.parse_args()
    state_path = Path(args.auth_state_file).expanduser().resolve()
    database = Path(args.database).expanduser().resolve()
    lock_path = Path(args.lock_path).expanduser().resolve()
    from alpha_mining.auth.session_manager import (
        AuthSettings,
        AuthStateError,
        import_browser_session,
    )
    from alpha_mining.common import load_workspace_env

    load_workspace_env(REPO_ROOT / ".env")

    import os

    username = os.environ.get("WQ_USERNAME", "").strip()
    if not username:
        print("ERROR: WQ_USERNAME is not set in .env", file=sys.stderr)
        return 2

    cookie_header = _read_cookie_header()
    if not cookie_header:
        print("ERROR: no cookie provided", file=sys.stderr)
        return 2

    try:
        result = import_browser_session(
            username, cookie_header, AuthSettings(state_path=state_path)
        )
    except AuthStateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Imported protected session (generation={result.generation})")
    return _probe_recovery_auth_state(state_path, database, lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
