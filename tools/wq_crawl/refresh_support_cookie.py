"""Open a headed browser for support.worldquantbrain.com login, then save cookie JSON.

Usage (project venv with playwright):
    .venv\\Scripts\\python.exe tools/wq_crawl/refresh_support_cookie.py

Completes face/login in the opened window. When auth is detected, writes
``.wq_browser_cookie_support.json`` (gitignored) and exits.
Never prints cookie values.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COOKIE_FILE = REPO / ".wq_browser_cookie_support.json"
PROFILE = REPO / ".wq_browser_profile"
BASE = "https://support.worldquantbrain.com"
HUB = f"{BASE}/hc/en-us/community/posts/19273239621399"
API = f"{BASE}/api/v2/community/posts/19273239621399.json"
WAIT_SEC = 300


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright missing — use project .venv: pip install playwright && playwright install chromium")
        return 1

    PROFILE.mkdir(parents=True, exist_ok=True)
    log(f"Opening headed browser (profile={PROFILE.name})")
    log("Please complete login / face scan in the window if prompted.")
    log(f"Waiting up to {WAIT_SEC}s for API access...")

    deadline = time.time() + WAIT_SEC
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(HUB, wait_until="domcontentloaded", timeout=120000)

        ok = False
        while time.time() < deadline:
            try:
                resp = ctx.request.get(API, timeout=30000)
                status = resp.status
            except Exception as exc:  # noqa: BLE001
                status = f"err:{exc}"
            url = page.url
            log(f"  poll api={status} page={url[:80]}")
            if isinstance(status, int) and status == 200:
                ok = True
                break
            # Also accept HTML hub without restricted redirect
            if "restricted" not in url.lower() and "login" not in url.lower():
                try:
                    body = page.inner_text("body")
                    if "Alpha" in body and len(body) > 500 and "请稍候" not in page.title():
                        # HTML may be readable while API still 401; keep waiting for API
                        pass
                except Exception:
                    pass
            page.wait_for_timeout(5000)

        if not ok:
            log("Timed out waiting for authenticated API access.")
            ctx.close()
            return 1

        cookies = ctx.cookies(BASE)
        # Also grab parent-domain cookies
        cookies += [c for c in ctx.cookies("https://worldquantbrain.com") if c not in cookies]
        merged: dict[str, str] = {}
        for c in cookies:
            merged[c["name"]] = c["value"]
        hdr = "; ".join(f"{k}={v}" for k, v in merged.items())
        ua = page.evaluate("() => navigator.userAgent")
        payload = {"cookie": hdr, "user_agent": ua}
        COOKIE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Saved {COOKIE_FILE.name}  cookie_names={sorted(merged)}  ua_len={len(ua)}")
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
