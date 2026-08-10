"""TEMPORARY login driver.  Delete after fresh-alpha validation.

Calls the existing alpha_mining.auth.browser_login.login() only.  Implements no
auth, no HTTP, no cookie parsing of its own, and never touches
platform_access_state / pipeline_loop_state.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

import os

from alpha_mining.auth.browser_login import BrowserLoginError, login


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true", help="open a visible window for a face scan")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    username = os.environ.get("WQ_USERNAME", "").strip()
    if not username:
        print("BLOCKED: WQ_USERNAME absent from project-root .env")
        return 2

    mode = "headed (visible window, waits for you)" if args.headed else "headless (reuse trusted profile)"
    print(f"browser_login.login() - {mode}")
    try:
        result = login(
            username,
            profile_dir=_ROOT / ".wq_browser_profile",
            headless=not args.headed,
            timeout_seconds=args.timeout,
        )
    except BrowserLoginError as exc:
        print(f"  LOGIN FAILED: {exc}")
        return 3
    print(
        f"  session stored: generation={result.generation} "
        f"performed_scan={result.performed_scan} cf_clearance={result.had_cloudflare_clearance}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
