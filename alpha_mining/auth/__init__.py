"""Shared authentication/session protection for WorldQuant clients."""

from .session_manager import (
    AuthDailyLimitExceeded,
    AuthLockTimeout,
    AuthResult,
    AuthSettings,
    AuthStateError,
    AuthenticationFailed,
    ensure_authenticated,
    ensure_authenticated_async,
    prepare_child_environment,
)

__all__ = [
    "AuthDailyLimitExceeded",
    "AuthLockTimeout",
    "AuthResult",
    "AuthSettings",
    "AuthStateError",
    "AuthenticationFailed",
    "ensure_authenticated",
    "ensure_authenticated_async",
    "prepare_child_environment",
]
