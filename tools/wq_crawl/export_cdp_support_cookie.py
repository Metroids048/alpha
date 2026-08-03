"""Export support cookies from a Chrome CDP session (port 9222).

Never prints cookie values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / ".wq_browser_cookie_support.json"
CDP = "http://127.0.0.1:9222"
BASE = "https://support.worldquantbrain.com"


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(
            f"{BASE}/hc/en-us/community/posts/19273239621399",
            wait_until="domcontentloaded",
            timeout=120000,
        )
        page.wait_for_timeout(3000)
        log(f"page={page.url[:100]} title={page.title()[:60]}")

        cookies = ctx.cookies(BASE)
        cookies += [
            c
            for c in ctx.cookies("https://.worldquantbrain.com")
            if c["name"] not in {x["name"] for x in cookies}
        ]
        # Deduplicate by name (last wins)
        merged: dict[str, str] = {}
        for c in cookies:
            merged[c["name"]] = c["value"]

        ua = page.evaluate("() => navigator.userAgent")
        hdr = "; ".join(f"{k}={v}" for k, v in merged.items())
        OUT.write_text(
            json.dumps(
                {"cookie": hdr, "user_agent": ua, "source": "cdp:9222"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        log(f"wrote {OUT.name} names={sorted(merged)} ua_len={len(ua)}")

        # Auth probe via page request (uses browser cookies)
        api = ctx.request.get(
            f"{BASE}/api/v2/community/posts/19273239621399.json", timeout=60000
        )
        log(f"api_status={api.status}")
        browser.close()
        return 0 if api.status == 200 else 2


if __name__ == "__main__":
    raise SystemExit(main())
