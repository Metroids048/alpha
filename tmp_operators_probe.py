"""ONE read-only /operators shape probe — TEMPORARY_VALIDATION_HARNESS / NOT_FOR_COMMIT.

Deliberately bypasses ReadOnlyPlatformClient.list_operators(): that method is the
object under diagnosis (client.py:408 -> _catalog_page -> client.py:398 rejects a
non-dict payload before catalog.py is ever reached).

Uses the existing client.request() so the shared PlatformAccessController, proxy,
rate limiter and min_interval all still apply. Prints structure only — never the
full body, never headers that could carry cookies/JWT.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.common import load_workspace_env

load_workspace_env(_ROOT / ".env")

import os

from alpha_mining.platform.client import BASE_URL, ReadOnlyPlatformClient

DB = _ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite"
LOCK = _ROOT / "worldquant_api.lock"
STATE = _ROOT / ".wq_auth_state.json"

CONTEXT = {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
}


def describe(payload: object) -> None:
    kind = type(payload).__name__
    print(f"  top_level_type        = {kind}")

    if isinstance(payload, list):
        print(f"  length                = {len(payload)}")
        all_objects = all(isinstance(item, dict) for item in payload)
        print(f"  all_items_are_objects = {all_objects}")
        if payload:
            first, last = payload[0], payload[-1]
            print(f"  first_item_type       = {type(first).__name__}")
            if isinstance(first, dict):
                print(f"  first_item_keys       = {sorted(first)}")
            print(f"  last_item_type        = {type(last).__name__}")
            if isinstance(last, dict):
                print(f"  last_item_keys        = {sorted(last)}")
            non_object = [i for i, item in enumerate(payload) if not isinstance(item, dict)]
            if non_object:
                print(f"  non_object_indices    = {non_object[:10]} (showing <=10)")
    elif isinstance(payload, dict):
        print(f"  top_level_keys        = {sorted(payload)}")
        count = payload.get("count")
        print(f"  count                 = {type(count).__name__} / {count!r}")
        results = payload.get("results")
        print(f"  results_type          = {type(results).__name__}")
        if isinstance(results, list):
            print(f"  results_length        = {len(results)}")
            if results:
                print(f"  results_first_type    = {type(results[0]).__name__}")
                if isinstance(results[0], dict):
                    print(f"  results_first_keys    = {sorted(results[0])}")
    else:
        print("  (neither list nor dict)")


def main() -> int:
    if not os.environ.get("WQ_USERNAME") or not os.environ.get("WQ_PASSWORD"):
        print("BLOCKED: WQ_USERNAME / WQ_PASSWORD absent from project-root .env")
        return 2

    print("=== effective paths ===")
    print(f"  CODE_ROOT                 {_ROOT}")
    print(f"  PLATFORM_ACCESS_DATABASE  {DB}")
    print(f"  AUTH_STATE_PATH           {STATE}")
    print(f"  context                   {CONTEXT}")
    print()

    client = ReadOnlyPlatformClient(
        state_path=str(STATE),
        database=str(DB),
        lock_path=str(LOCK),
        min_interval=3.0,
        timeout=60.0,
    )
    client.authenticate()

    url = f"{BASE_URL}/operators"
    print(f"GET {url}  (exactly one request)")
    response = client.request("GET", url, params=dict(CONTEXT), endpoint_class="catalog")

    status = response.status_code
    headers = getattr(response, "headers", {}) or {}
    print(f"  http_status           = {status}")
    print(f"  content_type          = {headers.get('Content-Type', '<absent>')!r}")

    if status != 200:
        raw = getattr(response, "content", b"")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        print(f"  body_prefix           = {str(raw)[:200]!r}")
        print(f"\nVERDICT: NON_200 status={status} -> classify per platform failure rules")
        return 3

    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  json_decode_error     = {type(exc).__name__}: {exc}")
        print("\nVERDICT: RESPONSE_NOT_JSON")
        return 4

    print()
    print("=== response structure (no body dump) ===")
    describe(payload)

    print()
    if isinstance(payload, list) and payload and all(isinstance(i, dict) for i in payload):
        print("VERDICT: CATALOG_PROTOCOL_MISMATCH_CONFIRMED (operators = unpaged list[dict])")
    elif isinstance(payload, dict):
        print("VERDICT: operators IS a dict -> root cause is elsewhere, do not proceed to fix")
    else:
        print("VERDICT: unexpected shape -> stop and report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
