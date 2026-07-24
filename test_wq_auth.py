#!/usr/bin/env python
"""Manual WorldQuant authentication probe; never runs during pytest import."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

import requests
from requests.auth import HTTPBasicAuth


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual WorldQuant authentication probe")
    parser.add_argument(
        "--profile",
        choices=("current", "legacy"),
        default="current",
        help="Request shape to probe; legacy matches the last known basic-auth implementation.",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Print non-secret credential configuration diagnostics without sending a request.",
    )
    args = parser.parse_args(list(argv) if argv is not None else [])
    from alpha_mining.common import load_workspace_env

    load_workspace_env(Path(__file__).resolve().parent / ".env")
    username = os.environ.get("WQ_USERNAME", "").strip()
    password = os.environ.get("WQ_PASSWORD", "")
    # A local proxy port is machine-specific.  Only use one the operator has
    # configured explicitly; otherwise let requests connect directly.
    proxy = os.environ.get("HTTPS_PROXY", "").strip()
    if not username or not password:
        print("WQ_USERNAME or WQ_PASSWORD is not configured in .env")
        return 1
    if args.diagnose:
        placeholder = password.strip().lower() in {
            "your_worldquant_password",
            "replace_with_password",
        } or "..." in password
        print(
            "Credential configuration: "
            f"username_present={bool(username)} password_present={bool(password)} "
            f"password_looks_placeholder={placeholder}"
        )
        return 0

    session = requests.Session()
    if args.profile == "current":
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
        request_kwargs: dict[str, object] = {"timeout": (15, 60)}
        if args.profile == "legacy":
            request_kwargs["auth"] = (username, password)
        response = session.post("https://api.worldquantbrain.com/authentication", **request_kwargs)
    except requests.RequestException as exc:
        print(f"Authentication request failed: {type(exc).__name__}")
        return 1
    print(f"Authentication HTTP {response.status_code} (profile={args.profile})")
    return 0 if response.status_code in {200, 201} else 1


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
