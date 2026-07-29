#!/usr/bin/env python3
"""
Safe WorldQuant BRAIN authentication and optional simulation diagnostic.

Purpose:
- Distinguish account/password authentication from proxy/network problems.
- Detect interactive/persona/inquiry challenges without printing secrets.
- Optionally verify that the authenticated Session can POST /simulations.
- Does NOT load the project's .env and does NOT read browser cookie files.
- Does NOT submit an Alpha for consultant review.

Windows PowerShell:
    $cred = Get-Credential
    $env:WQ_USERNAME = $cred.UserName
    $env:WQ_PASSWORD = $cred.GetNetworkCredential().Password

    # Authentication only:
    python diagnose_wq_auth.py --mode both

    # Authentication plus one real rank(close) simulation:
    python diagnose_wq_auth.py --mode both --simulate

    Remove-Item Env:WQ_USERNAME, Env:WQ_PASSWORD

Only paste the sanitized output back. Never paste credentials or cookie values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.auth import HTTPBasicAuth

API_BASE = "https://api.worldquantbrain.com"
AUTH_URL = API_BASE + "/authentication"
SIMULATIONS_URL = API_BASE + "/simulations"

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
}


def mask_email(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return "(empty)"
    if "@" not in value:
        return value[:2] + "***"
    local, domain = value.split("@", 1)
    return (local[:2] or "*") + "***@" + domain


def safe_url(value: str | None) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        # Remove query and fragment because challenge URLs can contain tokens.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return "(unparseable-url)"


def header(response: requests.Response, name: str) -> str:
    value = response.headers.get(name, "")
    if name.lower() == "location":
        return safe_url(value)
    if name.lower() in SENSITIVE_HEADERS:
        return "(redacted)"
    return value[:500]


def json_payload(response: requests.Response) -> tuple[Any, str]:
    content_type = response.headers.get("Content-Type", "")
    if "json" not in content_type.lower():
        digest = hashlib.sha256(response.content).hexdigest()[:16]
        return None, f"non-json sha256={digest} bytes={len(response.content)}"
    try:
        return response.json(), ""
    except (ValueError, json.JSONDecodeError):
        digest = hashlib.sha256(response.content).hexdigest()[:16]
        return None, f"invalid-json sha256={digest} bytes={len(response.content)}"


def json_shape(response: requests.Response) -> tuple[list[str], bool, bool, str]:
    payload, note = json_payload(response)
    if not isinstance(payload, dict):
        return [], False, False, note or f"json_type={type(payload).__name__}"
    keys = sorted(str(k) for k in payload.keys())
    has_user = bool(payload.get("user"))
    has_inquiry = bool(payload.get("inquiry"))
    return keys, has_user, has_inquiry, note


@dataclass
class Result:
    name: str
    classification: str
    status: int | None
    detail: str
    session: requests.Session | None = None


def classify_auth(
    *,
    status: int,
    has_user: bool,
    has_inquiry: bool,
    www_authenticate: str,
    location: str,
) -> str:
    auth_header = (www_authenticate or "").lower()
    if status in (200, 201) and has_user:
        return "PASSWORD_AUTH_OK"
    if has_inquiry or "persona" in auth_header or "biometric" in auth_header:
        return "INTERACTIVE_VERIFICATION_REQUIRED"
    if status in (301, 302, 303, 307, 308) and location:
        return "AUTH_REDIRECT_REQUIRES_INSPECTION"
    if status == 401:
        return "PASSWORD_REJECTED_OR_ACCOUNT_RESTRICTED"
    if status == 403:
        return "AUTHENTICATED_OR_POLICY_ACCESS_DENIED"
    if status == 429:
        return "RATE_LIMITED"
    if status >= 500:
        return "PLATFORM_OR_UPSTREAM_ERROR"
    return "UNEXPECTED_AUTH_RESPONSE"


def run_auth_case(name: str, trust_env: bool, username: str, password: str) -> Result:
    print(f"\n=== AUTH CASE {name} ===")
    print(f"trust_env={trust_env}")
    print(
        "proxy_env_present="
        + str(
            any(
                bool(os.getenv(k))
                for k in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                )
            )
        )
    )

    session = requests.Session()
    session.trust_env = trust_env
    session.auth = HTTPBasicAuth(username, password)
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "alpha-auth-diagnostic/1.1",
        }
    )

    try:
        response = session.post(
            AUTH_URL,
            timeout=(10, 30),
            allow_redirects=False,
        )
    except requests.exceptions.ProxyError as exc:
        print(f"exception=ProxyError:{type(exc.__cause__).__name__ if exc.__cause__ else ''}")
        return Result(name, "PROXY_FAILURE", None, type(exc).__name__)
    except requests.exceptions.SSLError as exc:
        print(f"exception=SSLError:{type(exc.__cause__).__name__ if exc.__cause__ else ''}")
        return Result(name, "TLS_OR_INTERCEPTION_FAILURE", None, type(exc).__name__)
    except requests.exceptions.ConnectTimeout:
        print("exception=ConnectTimeout")
        return Result(name, "CONNECT_TIMEOUT", None, "ConnectTimeout")
    except requests.exceptions.ReadTimeout:
        print("exception=ReadTimeout")
        return Result(name, "READ_TIMEOUT", None, "ReadTimeout")
    except requests.exceptions.ConnectionError as exc:
        print(f"exception=ConnectionError:{type(exc.__cause__).__name__ if exc.__cause__ else ''}")
        return Result(name, "NETWORK_OR_DNS_FAILURE", None, type(exc).__name__)
    except requests.RequestException as exc:
        print(f"exception={type(exc).__name__}")
        return Result(name, "REQUEST_FAILURE", None, type(exc).__name__)

    keys, has_user, has_inquiry, body_note = json_shape(response)
    www_authenticate = header(response, "WWW-Authenticate")
    raw_location = response.headers.get("Location", "")
    location = safe_url(raw_location)
    classification = classify_auth(
        status=response.status_code,
        has_user=has_user,
        has_inquiry=has_inquiry,
        www_authenticate=www_authenticate,
        location=location,
    )

    print(f"status={response.status_code}")
    print(f"final_url={safe_url(response.url)}")
    print(f"content_type={header(response, 'Content-Type')}")
    print(f"www_authenticate={www_authenticate}")
    print(f"location={location}")
    print(f"json_keys={keys}")
    print(f"has_user={has_user}")
    print(f"has_inquiry={has_inquiry}")
    print(f"cookie_names={sorted(cookie.name for cookie in session.cookies)}")
    if body_note:
        print(f"body_note={body_note}")
    print(f"classification={classification}")

    return Result(
        name=name,
        classification=classification,
        status=response.status_code,
        detail=body_note,
        session=session if classification == "PASSWORD_AUTH_OK" else None,
    )


def simulation_payload() -> dict[str, Any]:
    return {
        "type": "REGULAR",
        "settings": {
            "instrumentType": "EQUITY",
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "decay": 0,
            "neutralization": "INDUSTRY",
            "truncation": 0.08,
            "pasteurization": "ON",
            "testPeriod": "P0Y0M0D",
            "unitHandling": "VERIFY",
            "nanHandling": "OFF",
            "language": "FASTEXPR",
            "visualization": False,
        },
        "regular": "rank(close)",
    }


def run_simulation_probe(session: requests.Session, max_wait_seconds: int) -> str:
    print("\n=== OPTIONAL SIMULATION PROBE ===")
    print("expression=rank(close)")
    print("region=USA")
    print("universe=TOP3000")
    print("submit_for_review=false")

    try:
        response = session.post(
            SIMULATIONS_URL,
            json=simulation_payload(),
            timeout=(10, 60),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        print(f"simulation_exception={type(exc).__name__}")
        return "SIMULATION_NETWORK_OR_REQUEST_FAILURE"

    payload, body_note = json_payload(response)
    print(f"simulation_post_status={response.status_code}")
    print(f"simulation_post_location={safe_url(response.headers.get('Location', ''))}")
    print(f"simulation_post_retry_after={response.headers.get('Retry-After', '')}")
    print(
        "simulation_post_json_keys="
        + str(sorted(payload.keys()) if isinstance(payload, dict) else [])
    )
    if body_note:
        print(f"simulation_post_body_note={body_note}")

    if response.status_code == 401:
        print("simulation_classification=AUTH_SESSION_NOT_ACCEPTED_BY_SIMULATION")
        return "AUTH_SESSION_NOT_ACCEPTED_BY_SIMULATION"
    if response.status_code == 403:
        print("simulation_classification=SIMULATION_PERMISSION_OR_AGREEMENT_DENIED")
        return "SIMULATION_PERMISSION_OR_AGREEMENT_DENIED"
    if response.status_code == 429:
        print("simulation_classification=SIMULATION_RATE_LIMITED")
        return "SIMULATION_RATE_LIMITED"
    if response.status_code // 100 != 2:
        print("simulation_classification=SIMULATION_REQUEST_REJECTED")
        return "SIMULATION_REQUEST_REJECTED"

    progress_url = response.headers.get("Location", "")
    if not progress_url:
        print("simulation_classification=SIMULATION_ACCEPTED_WITHOUT_LOCATION")
        return "SIMULATION_ACCEPTED_WITHOUT_LOCATION"

    progress_url = urljoin(response.url, progress_url)
    print("simulation_classification=SIMULATION_ACCEPTED")

    deadline = time.monotonic() + max_wait_seconds
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        try:
            progress = session.get(progress_url, timeout=(10, 60))
        except requests.RequestException as exc:
            print(f"progress_exception={type(exc).__name__}")
            return "SIMULATION_PROGRESS_REQUEST_FAILURE"

        progress_payload, progress_note = json_payload(progress)
        retry_after_raw = progress.headers.get("Retry-After", "")
        print(
            f"poll={polls} status={progress.status_code} "
            f"retry_after={retry_after_raw or '0'}"
        )

        if progress.status_code == 401:
            print("simulation_final=SESSION_EXPIRED_DURING_POLL")
            return "SESSION_EXPIRED_DURING_POLL"
        if progress.status_code == 403:
            print("simulation_final=SIMULATION_PROGRESS_PERMISSION_DENIED")
            return "SIMULATION_PROGRESS_PERMISSION_DENIED"
        if progress.status_code // 100 != 2:
            print("simulation_final=SIMULATION_PROGRESS_REJECTED")
            return "SIMULATION_PROGRESS_REJECTED"

        if isinstance(progress_payload, dict):
            status_value = str(progress_payload.get("status", ""))
            alpha_value = progress_payload.get("alpha")
            print(f"progress_status={status_value}")
            print(f"alpha_present={bool(alpha_value)}")
            if alpha_value:
                print("simulation_final=SIMULATION_COMPLETED_WITH_ALPHA")
                return "SIMULATION_COMPLETED_WITH_ALPHA"
            if status_value.upper() == "ERROR":
                print("simulation_final=SIMULATION_COMPLETED_WITH_ERROR")
                return "SIMULATION_COMPLETED_WITH_ERROR"

        if progress_note:
            print(f"progress_body_note={progress_note}")

        try:
            delay = float(retry_after_raw) if retry_after_raw else 5.0
        except ValueError:
            delay = 5.0
        time.sleep(max(1.0, min(delay, 30.0)))

    print("simulation_final=SIMULATION_POLL_TIMEOUT")
    return "SIMULATION_POLL_TIMEOUT"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("no-proxy", "env-proxy", "both"),
        default="both",
        help="no-proxy ignores proxy environment; env-proxy uses it; both compares them.",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="After successful password auth, create and poll one rank(close) simulation. It does not submit for review.",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=int,
        default=600,
        help="Maximum time to poll the optional simulation.",
    )
    args = parser.parse_args()

    username = os.getenv("WQ_USERNAME", "").strip()
    password = os.getenv("WQ_PASSWORD", "")

    print("=== SAFE WORLDQUANT AUTH DIAGNOSTIC ===")
    print(f"python={platform.python_version()}")
    print(f"requests={requests.__version__}")
    print(f"platform={platform.platform()}")
    print(f"username={mask_email(username)}")
    print(f"username_present={bool(username)}")
    print(f"password_present={bool(password)}")
    print("project_dotenv_loaded=false")
    print("browser_cookie_loaded=false")

    if not username or not password:
        print("\nERROR: Set WQ_USERNAME and WQ_PASSWORD in the current process environment.")
        return 2

    results: list[Result] = []
    if args.mode in ("no-proxy", "both"):
        results.append(run_auth_case("DIRECT_NO_PROXY", False, username, password))
    if args.mode in ("env-proxy", "both"):
        results.append(run_auth_case("ENV_PROXY", True, username, password))

    print("\n=== AUTH SUMMARY ===")
    for result in results:
        print(
            f"{result.name}: classification={result.classification} "
            f"status={result.status}"
        )

    classes = {result.classification for result in results}
    successful = next(
        (result for result in results if result.classification == "PASSWORD_AUTH_OK"),
        None,
    )

    if successful:
        print("auth_verdict=ACCOUNT_PASSWORD_API_WORKS")
        simulation_result = ""
        if args.simulate and successful.session is not None:
            simulation_result = run_simulation_probe(
                successful.session,
                max_wait_seconds=max(30, args.max_wait_seconds),
            )
            print(f"\nsimulation_verdict={simulation_result}")

        if not args.simulate:
            print("next=Run again with --simulate to distinguish authentication from simulation permission.")
        elif simulation_result == "SIMULATION_COMPLETED_WITH_ALPHA":
            print("final_verdict=ACCOUNT_PASSWORD_AND_SIMULATION_API_WORK")
            print("next=The repository's vNext authentication/session path is the primary fault.")
        elif simulation_result in {
            "SIMULATION_PERMISSION_OR_AGREEMENT_DENIED",
            "SIMULATION_PROGRESS_PERMISSION_DENIED",
        }:
            print("final_verdict=PASSWORD_LOGIN_WORKS_BUT_SIMULATION_ACCESS_IS_RESTRICTED")
            print("next=Inspect platform agreements/consultant permissions and the sanitized API response in project diagnostics.")
        elif simulation_result == "AUTH_SESSION_NOT_ACCEPTED_BY_SIMULATION":
            print("final_verdict=AUTH_ENDPOINT_ACCEPTS_PASSWORD_BUT_SESSION_IS_NOT_VALID_FOR_SIMULATION")
            print("next=Inspect authentication challenge/completion and session cookie propagation.")
        else:
            print("final_verdict=PASSWORD_LOGIN_WORKS_SIMULATION_RESULT_NEEDS_INSPECTION")
        return 0

    if "INTERACTIVE_VERIFICATION_REQUIRED" in classes:
        print("auth_verdict=PASSWORD_FLOW_REQUIRES_INTERACTIVE_CHALLENGE")
        print("next=Implement the returned persona/biometric/inquiry flow; do not replace it with manual cookie auth.")
        return 3

    if len(results) == 2 and results[0].classification != results[1].classification:
        print("auth_verdict=PROXY_OR_NETWORK_PATH_CHANGES_RESULT")
        print("next=Fix proxy/TLS/redirect configuration before modifying authentication architecture.")
        return 4

    print("auth_verdict=NOT_YET_CONFIRMED")
    print("next=Inspect status, WWW-Authenticate and sanitized Location; verify credential source and account state.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
