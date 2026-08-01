from __future__ import annotations


def test_browser_login_collects_auth_cookies_across_platform_and_api_domains() -> None:
    from alpha_mining.auth.browser_login import _cookie_header, _find_auth_cookie, _worldquant_cookies

    cookies = [
        {"name": "t", "value": "platform-token", "domain": "platform.worldquantbrain.com"},
        {"name": "cf_clearance", "value": "api-clearance", "domain": ".worldquantbrain.com"},
        {"name": "t", "value": "unrelated", "domain": "example.com"},
    ]

    selected = _worldquant_cookies(cookies)

    assert _find_auth_cookie(selected)
    assert _cookie_header(selected) == "t=platform-token; cf_clearance=api-clearance"
