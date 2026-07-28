"""Bearer token authentication for WorldQuant Brain API.

Extracts JWT token from browser session and uses it as Bearer token for API requests.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alpha_mining.auth.session_manager import AuthSettings, _load_state


@dataclass
class BearerToken:
    """Represents a JWT bearer token with expiry tracking."""

    token: str
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        """Check if token is expired or will expire within 5 minutes."""
        if not self.expires_at:
            return True
        remaining = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return remaining < 300  # 5 minutes buffer

    @property
    def remaining_seconds(self) -> float:
        """Get remaining valid seconds."""
        if not self.expires_at:
            return 0.0
        return max(0.0, (self.expires_at - datetime.now(timezone.utc)).total_seconds())

    @classmethod
    def from_jwt(cls, token: str) -> BearerToken:
        """Parse JWT token and extract expiry."""
        parts = token.split('.')
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")

        try:
            # Decode JWT payload (base64url)
            payload = parts[1]
            payload += '=' * (4 - len(payload) % 4)  # Add padding
            decoded = json.loads(base64.urlsafe_b64decode(payload))

            exp_timestamp = decoded.get('exp')
            if not exp_timestamp:
                raise ValueError("JWT missing 'exp' claim")

            expires_at = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
            return cls(token=token, expires_at=expires_at)
        except Exception as e:
            raise ValueError(f"Failed to parse JWT: {e}") from e


def load_bearer_token(
    state_path: str | Path = ".wq_auth_state.json",
    username: str | None = None,
) -> BearerToken | None:
    """Load bearer token from saved browser session.

    Args:
        state_path: Path to authentication state file
        username: Account username (reads from WQ_USERNAME env if not provided)

    Returns:
        BearerToken if valid session exists, None otherwise
    """
    if username is None:
        username = os.environ.get("WQ_USERNAME", "").strip()

    if not username:
        return None

    try:
        settings = AuthSettings(state_path=state_path)
        path = settings.resolved_state_path()

        if not path.exists():
            return None

        # Load state without triggering authentication
        import hashlib
        fingerprint = hashlib.sha256(username.strip().casefold().encode()).hexdigest()
        state = _load_state(path, fingerprint, datetime.now(timezone.utc))

        # Cookies are DPAPI-encrypted in cookie_blob_dpapi_b64
        from alpha_mining.auth.session_manager import _unprotect_cookie_rows
        rows = _unprotect_cookie_rows(state.get("cookie_blob_dpapi_b64"))

        # Find the JWT token
        jwt_token = ""
        for row in rows:
            if isinstance(row, dict) and row.get("name") == "t":
                jwt_token = row.get("value", "").strip()
                break

        if not jwt_token:
            return None

        bearer = BearerToken.from_jwt(jwt_token)

        # Check if token is still valid
        if bearer.is_expired:
            return None

        return bearer

    except Exception:
        return None


def save_bearer_token_env(token: BearerToken) -> None:
    """Save bearer token to WQ_BEARER_TOKEN environment variable.

    This is a convenience for manual testing. Production code should
    extract token directly from browser session via load_bearer_token().
    """
    os.environ["WQ_BEARER_TOKEN"] = token.token
