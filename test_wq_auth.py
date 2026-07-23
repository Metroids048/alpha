#!/usr/bin/env python
"""Manual WorldQuant authentication probe; never runs during pytest import."""

from __future__ import annotations

import os
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth


def main() -> int:
    from alpha_mining.common import load_workspace_env

    load_workspace_env(Path(__file__).resolve().parent / ".env")
    username = os.environ.get("WQ_USERNAME", "").strip()
    password = os.environ.get("WQ_PASSWORD", "")
    proxy = os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7892")
    if not username or not password:
        print("WQ_USERNAME or WQ_PASSWORD is not configured in .env")
        return 1

    session = requests.Session()
    session.auth = HTTPBasicAuth(username, password)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, */*",
            "Content-Type": "application/json",
            "Origin": "https://platform.worldquantbrain.com",
        }
    )
    if proxy:
        session.proxies["https"] = proxy
    try:
        response = session.post(
            "https://api.worldquantbrain.com/authentication",
            timeout=(15, 60),
        )
    except requests.RequestException as exc:
        print(f"Authentication request failed: {type(exc).__name__}")
        return 1
    print(f"Authentication HTTP {response.status_code}")
    return 0 if response.status_code in {200, 201} else 1


if __name__ == "__main__":
    raise SystemExit(main())
