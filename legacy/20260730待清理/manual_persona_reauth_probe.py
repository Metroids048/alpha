"""Decisive live probe: does a completed browser persona let PURE basic-auth mint a token?

This is a read-only authentication diagnostic. It sends exactly ONE
POST /authentication with HTTP Basic Auth, carrying NO cookies and NO
stored session, and prints the outcome. Run it:
  * BEFORE the browser persona  -> expect 401 + WWW-Authenticate: persona
  * AFTER  the browser persona  -> if 201/200 => basic-auth re-auth works
"""

from __future__ import annotations

import os
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth


def test_pure_basic_auth_probe() -> None:
    from alpha_mining.common import load_workspace_env

    load_workspace_env(Path(__file__).resolve().parent.parent / ".env")
    username = os.environ.get("WQ_USERNAME", "").strip()
    password = os.environ.get("WQ_PASSWORD", "")
    assert username and password, "WQ_USERNAME/WQ_PASSWORD must be set in .env"

    session = requests.Session()  # brand new, no cookies, no stored state
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, */*",
            "Content-Type": "application/json",
            "Origin": "https://platform.worldquantbrain.com",
        }
    )
    response = session.post(
        "https://api.worldquantbrain.com/authentication",
        auth=HTTPBasicAuth(username, password),
        timeout=(15, 60),
    )
    print(f"STATUS: {response.status_code}")
    print(f"WWW-Authenticate: {response.headers.get('WWW-Authenticate', '<none>')}")
    print(f"Location: {response.headers.get('Location', '<none>')}")
    print(f"Set-Cookie present: {'set-cookie' in {k.lower() for k in response.headers}}")
    body = response.text or ""
    print(f"BODY[:300]: {body[:300]}")
    # Not an assertion on outcome — this probe reports state, both outcomes are informative.
    assert response.status_code in {200, 201, 401, 403}
