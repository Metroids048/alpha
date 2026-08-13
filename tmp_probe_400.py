"""Capture the 400 body from the alpha_list date-window request.

One request, and it prints the platform's own error text plus the exact URL that
was sent, so the rejected parameter is identified instead of guessed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlencode

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

from alpha_mining.platform.client import BASE_URL, ReadOnlyPlatformClient

DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
LOCK = _ROOT / "worldquant_api.lock"
AUTH = _ROOT / ".wq_auth_state.json"

PARAMS = {
    "status": "UNSUBMITTED",
    "dateCreated>=": "2026-08-11T06:30:00Z",
    "dateCreated<": "2026-08-11T07:00:00Z",
    "order": "-dateCreated",
    "limit": 100,
    "offset": 0,
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("params as requests would encode them:")
    print("  " + urlencode(PARAMS))
    print()

    client = ReadOnlyPlatformClient(
        state_path=AUTH, database=DB, lock_path=LOCK,
        min_interval=2.0, max_attempts=1,
        require_stored_session=True, allow_auth_replay=False,
    )
    response = client.request(
        "GET", f"{BASE_URL}/users/self/alphas",
        params=dict(PARAMS), endpoint_class="alpha_list",
    )
    print(f"HTTP {response.status_code}")
    print(f"final url: {getattr(response, 'url', '(n/a)')}")
    body = getattr(response, "text", "") or ""
    print(f"body ({len(body)} chars):")
    print(body[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
