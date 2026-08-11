"""BUG: BrowserResponse header lookups were case-sensitive.

The in-page fetch shim stores response headers lower-cased ("location"), but
gateway.simulate() reads ``response.headers.get("Location")`` the way requests'
CaseInsensitiveDict allows.  On the browser transport that lookup always missed,
so every accepted simulation POST raised SimulationOutcomeUnknown
("accepted simulation response has no alpha id or progress location") and the
candidate landed in SIMULATION_UNCERTAIN with no alpha id -- an infrastructure
fault that reads like a platform verdict.
"""

from __future__ import annotations

from alpha_mining.platform.browser_transport import BrowserResponse


def test_location_header_is_case_insensitive() -> None:
    response = BrowserResponse(
        status_code=201,
        text="{}",
        headers={"location": "/simulations/abc123", "content-type": "application/json"},
    )

    # gateway.simulate() uses the canonical HTTP spelling.
    assert response.headers.get("Location") == "/simulations/abc123"
    assert response.headers.get("location") == "/simulations/abc123"
    assert response.headers.get("LOCATION") == "/simulations/abc123"
    assert response.headers.get("Content-Type") == "application/json"


def test_missing_header_still_returns_default() -> None:
    response = BrowserResponse(status_code=200, text="{}", headers={"content-type": "application/json"})

    assert response.headers.get("Location") is None
    assert response.headers.get("Location", "") == ""


def test_headers_remain_mapping_like() -> None:
    response = BrowserResponse(
        status_code=201, text="{}", headers={"location": "/simulations/abc123"}
    )

    assert "Location" in response.headers
    assert "location" in response.headers
    assert response.headers["Location"] == "/simulations/abc123"
    assert len(response.headers) == 1
    assert dict(response.headers) == {"location": "/simulations/abc123"}


def test_empty_headers_default() -> None:
    response = BrowserResponse(status_code=200, text="{}")

    assert response.headers.get("Location") is None
    assert len(response.headers) == 0
