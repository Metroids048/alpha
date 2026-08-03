"""Wait until CDP Chrome is logged into support, then export cookie JSON."""

from __future__ import annotations

import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / ".wq_browser_cookie_support.json"
CDP = "http://127.0.0.1:9222"
BASE = "https://support.worldquantbrain.com"
API = f"{BASE}/api/v2/community/posts/19273239621399.json"
HUB = f"{BASE}/hc/en-us/community/posts/19273239621399"
WAIT_SEC = 600


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    from playwright.sync_api import sync_playwright

    log("Waiting for you to finish face/login in the Chrome window (CDP:9222)...")
    log(f"Target: {HUB}")
    deadline = time.time() + WAIT_SEC

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(HUB, wait_until="domcontentloaded", timeout=120000)
        except Exception as exc:  # noqa: BLE001
            log(f"initial goto warn: {exc}")

        while time.time() < deadline:
            try:
                api = ctx.request.get(API, timeout=30000)
                status = api.status
            except Exception as exc:  # noqa: BLE001
                status = f"err:{exc}"
            try:
                url = page.url
                title = page.title()
            except Exception:
                url, title = "?", "?"
            log(f"  api={status} url={url[:90]} title={title[:40]}")
            if isinstance(status, int) and status == 200:
                cookies = ctx.cookies(BASE)
                merged = {c["name"]: c["value"] for c in cookies}
                # pull parent domain too
                for c in ctx.cookies("https://platform.worldquantbrain.com"):
                    merged.setdefault(c["name"], c["value"])
                for c in ctx.cookies("https://api.worldquantbrain.com"):
                    merged.setdefault(c["name"], c["value"])
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
                log(f"AUTH OK — saved {OUT.name} names={sorted(merged)}")
                browser.close()
                return 0
            page.wait_for_timeout(5000)

        log("Timed out waiting for login.")
        browser.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
