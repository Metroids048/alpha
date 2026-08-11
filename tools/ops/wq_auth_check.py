#!/usr/bin/env python
"""Manual WorldQuant authentication probe; never runs during pytest import."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

import requests
from requests.auth import HTTPBasicAuth


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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
    parser.add_argument(
        "--cookie-env",
        default="",
        help="Import an explicitly provided browser Cookie header from this environment variable.",
    )
    args = parser.parse_args(list(argv) if argv is not None else [])
    from alpha_mining.common import load_workspace_env

    load_workspace_env(REPO_ROOT / ".env")
    username = os.environ.get("WQ_USERNAME", "").strip()
    password = os.environ.get("WQ_PASSWORD", "")
    # A local proxy port is machine-specific.  Only use one the operator has
    # configured explicitly; otherwise let requests connect directly.
    proxy = os.environ.get("HTTPS_PROXY", "").strip()
    if not username or not password:
        print("WQ_USERNAME or WQ_PASSWORD is not configured in .env")
        return 1
    if args.diagnose:
        print(
            "Credential configuration: "
            f"username_present={bool(username)} password_present={bool(password)} "
            "password_value_not_inspected=True"
        )
        return 0
    if args.cookie_env:
        print(
            "Cookie import is disabled in wq_auth_check; use "
            "tools/ops/import_cookie_now.py so the recovery identity probe is mandatory."
        )
        return 2

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
