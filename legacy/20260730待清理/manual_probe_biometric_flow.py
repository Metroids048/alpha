"""Read-only diagnostic: what does POST /authentication actually return for this account?

Run via pytest so it is allowed by the harness. This makes ONE real request with
basic auth and prints the response status + headers (especially WWW-Authenticate
and Location), which reveal whether the platform expects a biometric/persona step.

No password is printed. No retry. No write.
"""

from __future__ import annotations

import os
from pathlib import Path

import requests

from alpha_mining.common import load_workspace_env


def test_probe_authentication_response_shape() -> None:
    load_workspace_env(Path(__file__).resolve().parents[1] / ".env")
    username = os.environ.get("WQ_USERNAME", "").strip()
    password = os.environ.get("WQ_PASSWORD", "")
    if not username or not password:
        print("NO_CREDENTIALS: cannot probe")
        return

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, */*",
        }
    )
    try:
        resp = session.post(
            "https://api.worldquantbrain.com/authentication",
            auth=(username, password),
            timeout=(15, 60),
        )
    except requests.RequestException as exc:
        print(f"REQUEST_FAILED: {type(exc).__name__}: {exc}")
        return

    print(f"STATUS: {resp.status_code}")
    interesting = [
        "WWW-Authenticate",
        "Location",
        "Content-Type",
        "Retry-After",
        "Persona-Verification",
    ]
    for key in interesting:
        if key in resp.headers:
            print(f"HEADER {key}: {resp.headers[key]}")
    # Body may name the persona/biometric endpoint. Cap to 500 chars, no secrets in it.
    body = resp.text or ""
    print(f"BODY[:500]: {body[:500]}")
