from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alpha_mining.auth.session_manager import AuthSettings, ensure_authenticated


def main() -> int:
    state_path = Path(sys.argv[1])
    url = sys.argv[2]
    session = requests.Session()
    ensure_authenticated(
        session,
        lambda: session.post(url, timeout=3),
        "subprocess@example.test",
        AuthSettings(state_path=state_path, lock_timeout_seconds=5),
    )
    return 0 if session.cookies.get("session") == "test-cookie" else 3


if __name__ == "__main__":
    raise SystemExit(main())
