#!/usr/bin/env python3
"""Read-only diagnostic: list cookie NAMES (never values) stored in the
persistent Playwright profile for both the platform and api hosts.

Does not open a visible window (headless) and does not print secrets.
Purpose: find out why browser_login.py's cookie-detection loop failed to
see a session even though the human completed login/verification in the
browser controlled by that same profile.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).parent / ".wq_browser_profile"
URLS = [
    "https://platform.worldquantbrain.com",
    "https://api.worldquantbrain.com",
]


def main() -> int:
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=True,
        )
        try:
            all_cookies = context.cookies()
            print(f"total_cookies_in_profile={len(all_cookies)}")
            by_domain: dict[str, list[str]] = {}
            for c in all_cookies:
                domain = str(c.get("domain") or "")
                name = str(c.get("name") or "")
                has_value = bool(c.get("value"))
                expires = c.get("expires")
                by_domain.setdefault(domain, []).append(f"{name}(has_value={has_value},expires={expires})")
            for domain, names in sorted(by_domain.items()):
                print(f"domain={domain}")
                for n in names:
                    print(f"  {n}")

            print("\n--- per-URL scoped cookies() ---")
            for url in URLS:
                scoped = context.cookies(url)
                names = [c.get("name") for c in scoped]
                print(f"url={url} cookie_names={names}")
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
