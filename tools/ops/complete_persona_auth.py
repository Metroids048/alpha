#!/usr/bin/env python3
"""
Complete the WorldQuant BRAIN persona (biometric/face) verification challenge.

This confirms the diagnosis from diagnose_wq_auth.py: auth_verdict=
PASSWORD_FLOW_REQUIRES_INTERACTIVE_CHALLENGE. The password is accepted;
the account additionally requires a one-time interactive persona/biometric
check before a session cookie is issued.

Flow:
1. POST /authentication -> expect 401 with WWW-Authenticate: persona and a
   Location header pointing to the verification URL.
2. Print the full URL for the human to open in a browser and complete the
   face verification.
3. Poll the same URL with POST every few seconds until it stops returning
   the challenge (200/201), which means the session cookie has been issued.
4. On success, persist the resulting session cookies to a local file so the
   pipeline's ReadOnlyPlatformClient / session_manager can pick it up
   without re-triggering the challenge inside the 4-hour token window.

Does not print the password. Does not submit any Alpha.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests.auth import HTTPBasicAuth

API_BASE = "https://api.worldquantbrain.com"
AUTH_URL = API_BASE + "/authentication"
COOKIE_STATE_PATH = Path(__file__).parent / ".wq_persona_session_cookies.json"


def load_env_credentials() -> tuple[str, str]:
    username = os.environ.get("WQ_USERNAME", "").strip()
    password = os.environ.get("WQ_PASSWORD", "")
    if not username or not password:
        print("ERROR: WQ_USERNAME / WQ_PASSWORD not present in process environment.")
        sys.exit(2)
    return username, password


def main() -> int:
    username, password = load_env_credentials()

    session = requests.Session()
    session.trust_env = False
    session.auth = HTTPBasicAuth(username, password)
    session.headers.update({"Accept": "application/json"})

    print("=== STEP 1: initial authentication ===")
    response = session.post(AUTH_URL, timeout=(10, 30))
    print(f"status={response.status_code}")

    if response.status_code in (200, 201):
        print("Already authenticated without a challenge (no persona required this time).")
        _persist_cookies(session)
        return 0

    if response.status_code != 401 or response.headers.get("WWW-Authenticate", "").lower() != "persona":
        print("Unexpected response, not a persona challenge. Aborting.")
        print(f"WWW-Authenticate={response.headers.get('WWW-Authenticate', '')}")
        try:
            print(f"body={response.json()}")
        except Exception:
            pass
        return 1

    location = response.headers.get("Location", "")
    if not location:
        print("ERROR: 401 persona challenge but no Location header returned.")
        return 1

    persona_url = urljoin(response.url, location)
    print("\n=== STEP 2: biometric verification required ===")
    print(f"Open this URL in your browser and complete face verification:\n{persona_url}\n")

    print("=== STEP 3: polling until verification completes ===")
    max_wait_seconds = 15 * 60  # 15 minutes to complete the face scan
    poll_interval = 3.0
    deadline = time.monotonic() + max_wait_seconds
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        try:
            poll_response = session.post(persona_url, timeout=(10, 30))
        except requests.RequestException as exc:
            print(f"attempt={attempt} exception={type(exc).__name__}, retrying...")
            time.sleep(poll_interval)
            continue

        print(f"attempt={attempt} status={poll_response.status_code}")

        if poll_response.status_code in (200, 201):
            print("\nBiometric verification complete. Re-authenticating to confirm session...")
            confirm = session.post(AUTH_URL, timeout=(10, 30))
            print(f"confirm_status={confirm.status_code}")
            if confirm.status_code in (200, 201):
                print("SUCCESS: authenticated session established.")
                _persist_cookies(session)
                return 0
            print("Verification endpoint succeeded but re-auth did not return 200/201.")
            print(f"body={_safe_body(confirm)}")
            return 1

        if poll_response.status_code == 401:
            # Still waiting on the human to finish the face scan in the browser.
            time.sleep(poll_interval)
            continue

        print(f"Unexpected status during polling: {poll_response.status_code}")
        print(f"body={_safe_body(poll_response)}")
        time.sleep(poll_interval)

    print("\nTIMEOUT: biometric verification was not completed within 15 minutes.")
    print("Re-run this script after opening the URL and completing verification faster.")
    return 3


def _safe_body(response: requests.Response) -> str:
    try:
        return json.dumps(response.json())[:500]
    except Exception:
        return f"<non-json, {len(response.content)} bytes>"


def _persist_cookies(session: requests.Session) -> None:
    cookies = {c.name: c.value for c in session.cookies}
    data = {
        "cookies": cookies,
        "saved_at": time.time(),
    }
    COOKIE_STATE_PATH.write_text(json.dumps(data), encoding="utf-8")
    print(f"Session cookies saved to {COOKIE_STATE_PATH} (cookie names only, no secrets logged here).")
    print(f"cookie_names={sorted(cookies.keys())}")


if __name__ == "__main__":
    sys.exit(main())
