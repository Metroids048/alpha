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

import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

STATE_PATH = ".wq_auth_state.json"


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


def _report_jwt_expiry(state_path: Path) -> int:
    from alpha_mining.auth.session_manager import _unprotect_cookie_rows

    state = json.loads(state_path.read_text(encoding="utf-8"))
    for row in _unprotect_cookie_rows(state.get("cookie_blob_dpapi_b64")):
        if row.get("name") != "t":
            continue
        payload = str(row["value"]).split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
        remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            print(f"WARNING: this JWT expired {-remaining / 3600:.1f}h ago; log in again.")
            return 1
        print(
            f"JWT valid until {expires_at.strftime('%H:%M UTC')} "
            f"({remaining / 3600:.1f}h remaining)"
        )
        return 0
    print("WARNING: stored state has no `t` cookie.")
    return 1


def main() -> int:
    from alpha_mining.auth.session_manager import (
        AuthSettings,
        AuthStateError,
        import_browser_session,
    )
    from alpha_mining.common import load_workspace_env

    load_workspace_env(_ROOT / ".env")

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
            username, cookie_header, AuthSettings(state_path=STATE_PATH)
        )
    except AuthStateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Imported session for {username} (generation={result.generation})")
    return _report_jwt_expiry(_ROOT / STATE_PATH)


if __name__ == "__main__":
    raise SystemExit(main())
