"""Persona-aware browser login for WorldQuant Brain.

The platform protects ``POST /authentication`` with a Persona biometric
challenge (``WWW-Authenticate: persona``).  A face/document scan can only be
completed in a real browser, and the resulting session token is bound to the
browser session that completed it -- plain Basic Auth from a fresh session is
always rejected.  This helper drives a *persistent* Playwright profile so the
platform's "remember this device" state survives across runs:

* headed mode (interactive): open the login page, wait for the operator to
  complete the scan once, then capture the ``t`` / ``cf_clearance`` cookies and
  hand them to :func:`import_browser_session` (existing DPAPI storage).
* headless mode (unattended): reuse the same profile and try to obtain a fresh
  token *without* a scan.  This only succeeds while the platform still trusts
  the profile; otherwise it fails closed so the caller can fall back to headed.

No password, cookie, or token value is printed or logged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from alpha_mining.auth.session_manager import AuthSettings, import_browser_session

PLATFORM_URL = "https://platform.worldquantbrain.com"
# Cookies that actually carry authentication; everything else is discarded by
# import_browser_session anyway, but we only need to detect these two.
_AUTH_COOKIE = "t"
_CLOUDFLARE_COOKIE = "cf_clearance"


class BrowserLoginError(RuntimeError):
    """Interactive/unattended browser login could not obtain a session."""


@dataclass(frozen=True)
class BrowserLoginResult:
    generation: int
    performed_scan: bool
    had_cloudflare_clearance: bool


def _cookie_header(cookies: list[dict]) -> str:
    parts = []
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if name in {_AUTH_COOKIE, _CLOUDFLARE_COOKIE} and value:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _find_auth_cookie(cookies: list[dict]) -> bool:
    return any(
        str(cookie.get("name") or "") == _AUTH_COOKIE and str(cookie.get("value") or "")
        for cookie in cookies
    )


def login(
    username: str,
    *,
    profile_dir: str | Path = ".wq_browser_profile",
    headless: bool = False,
    timeout_seconds: float = 300.0,
    poll_interval: float = 2.0,
    settings: AuthSettings | None = None,
) -> BrowserLoginResult:
    """Obtain a WorldQuant session via a persistent Playwright profile.

    Args:
        username: account whose DPAPI fingerprint the token is stored under.
        profile_dir: persistent browser profile so "remember this device"
            survives across runs (the key to unattended renewal).
        headless: ``False`` opens a visible window for a one-time scan;
            ``True`` reuses the trusted profile and never waits for a human,
            failing closed if a fresh token cannot be obtained quickly.
        timeout_seconds: max time to wait for the ``t`` cookie to appear.
        poll_interval: how often to re-read cookies while waiting.

    Returns:
        BrowserLoginResult with the new DPAPI generation.

    Raises:
        BrowserLoginError: no session cookie was obtained within the timeout.
    """
    if not str(username or "").strip():
        raise BrowserLoginError("username is required")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependency is installed
        raise BrowserLoginError("playwright is not installed") from exc

    profile_path = Path(profile_dir).expanduser().resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    # Headless waits are short: a trusted profile mints the token immediately,
    # so a long wait just means the profile is no longer trusted.
    deadline = time.monotonic() + (min(30.0, timeout_seconds) if headless else timeout_seconds)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=headless,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(f"{PLATFORM_URL}/", wait_until="domcontentloaded")
            while True:
                cookies = context.cookies(PLATFORM_URL)
                if _find_auth_cookie(cookies):
                    header = _cookie_header(cookies)
                    result = import_browser_session(username, header, settings)
                    has_cf = any(
                        str(c.get("name") or "") == _CLOUDFLARE_COOKIE
                        and str(c.get("value") or "")
                        for c in cookies
                    )
                    # performed_scan is best-effort: in headed mode the operator
                    # may or may not have needed a scan; we only assert a token
                    # was captured.
                    return BrowserLoginResult(
                        generation=result.generation,
                        performed_scan=not headless,
                        had_cloudflare_clearance=has_cf,
                    )
                if time.monotonic() >= deadline:
                    raise BrowserLoginError(
                        "no session cookie obtained "
                        + ("(profile no longer trusted; run headed login)"
                           if headless
                           else "(login/scan not completed in time)")
                    )
                time.sleep(max(0.5, float(poll_interval)))
        finally:
            context.close()


def _main() -> int:
    """CLI entry point: ``python -m alpha_mining.auth.browser_login [--headed]``

    ``--headed``  Open a visible browser so you can complete the Persona scan once.
                  After that, headless renewal works automatically.
    ``--headless`` (default) Try to renew silently using the trusted profile.
    """
    import argparse
    import os

    from alpha_mining.common import load_workspace_env
    from pathlib import Path as _Path

    load_workspace_env(_Path(__file__).resolve().parents[2] / ".env")
    parser = argparse.ArgumentParser(
        prog="python -m alpha_mining.auth.browser_login",
        description="Obtain/renew a WorldQuant Brain session via the persistent browser profile.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Open a visible browser for interactive Persona scan (required when profile is not trusted).",
    )
    parser.add_argument(
        "--profile-dir",
        default=".wq_browser_profile",
        help="Persistent Playwright profile directory (default: .wq_browser_profile)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for the session cookie (headed mode, default 300).",
    )
    args = parser.parse_args()
    username = os.environ.get("WQ_USERNAME", "").strip()
    if not username:
        print("ERROR: WQ_USERNAME is not set in .env")
        return 1
    headless = not args.headed
    mode = "headless (auto-renewal)" if headless else "headed (interactive Persona scan)"
    print(f"Starting browser login — mode={mode}, profile={args.profile_dir}")
    try:
        result = login(
            username,
            profile_dir=args.profile_dir,
            headless=headless,
            timeout_seconds=args.timeout,
        )
        print(
            f"Session obtained — generation={result.generation} "
            f"had_cloudflare={result.had_cloudflare_clearance}"
        )
        return 0
    except BrowserLoginError as exc:
        print(f"Login failed: {exc}")
        if headless:
            print(
                "\nProfile is no longer trusted. Run headed login once to re-establish:\n"
                "  & $env:AGENT_PYTHON -m alpha_mining.auth.browser_login --headed"
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
