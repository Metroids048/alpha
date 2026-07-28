"""Diagnostic: open the persistent Playwright profile headless and list which
worldquantbrain cookies it holds. Prints names only (never values)."""
from pathlib import Path


def test_dump_profile_cookies() -> None:
    from playwright.sync_api import sync_playwright

    profile = Path(".wq_browser_profile").resolve()
    print(f"PROFILE_EXISTS: {profile.exists()} PATH={profile}")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(user_data_dir=str(profile), headless=True)
        try:
            cookies = ctx.cookies("https://platform.worldquantbrain.com")
            names = sorted({str(c.get("name") or "") for c in cookies})
            print(f"COOKIE_COUNT: {len(cookies)}")
            print(f"COOKIE_NAMES: {names}")
            has_t = any(str(c.get('name'))=='t' and str(c.get('value') or '') for c in cookies)
            has_cf = any(str(c.get('name'))=='cf_clearance' and str(c.get('value') or '') for c in cookies)
            print(f"HAS_T: {has_t}  HAS_CF: {has_cf}")
        finally:
            ctx.close()
