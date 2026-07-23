"""Quota-aware authentication shared by requests, aiohttp, and child processes.

The persisted cookie payload is protected with Windows DPAPI for the current user.
No password, username, token, or plaintext cookie is written to disk.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import threading
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

STATE_VERSION = 1
AUTH_STATE_ENV = "WQ_AUTH_STATE_FILE"


class AuthStateError(RuntimeError):
    """Persisted authentication state is unsafe or unreadable."""


class AuthDailyLimitExceeded(RuntimeError):
    """The local UTC authentication safety cap has been reached."""


class AuthLockTimeout(RuntimeError):
    """Another authentication path held the shared lock too long."""


class AuthenticationFailed(RuntimeError):
    """A bounded authentication request failed."""


@dataclass(frozen=True)
class AuthSettings:
    state_path: str | Path = ".wq_auth_state.json"
    cooldown_seconds: float = 25 * 60
    daily_cap: int = 5
    max_attempts: int = 2
    lock_timeout_seconds: float = 120

    def resolved_state_path(self) -> Path:
        configured = os.environ.get(AUTH_STATE_ENV, "").strip()
        requested = Path(self.state_path)
        path = (
            Path(configured)
            if configured and str(requested) == ".wq_auth_state.json"
            else requested
        )
        return path.expanduser().resolve()


@dataclass(frozen=True)
class AuthResult:
    performed_login: bool
    restored_session: bool
    generation: int
    auth_attempts_today: int


@dataclass
class _HeldLock:
    stream: Any
    process_lock: threading.Lock


_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_SESSION_GUARD = threading.Lock()
_SESSION_GENERATIONS: weakref.WeakKeyDictionary[Any, int] = weakref.WeakKeyDictionary()


def _process_lock(path: Path) -> threading.Lock:
    key = str(path).casefold()
    with _LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


def _acquire_lock(state_path: Path, timeout: float) -> _HeldLock:
    if os.name != "nt":
        raise AuthStateError("DPAPI authentication state requires Windows")
    import msvcrt

    lock_path = Path(str(state_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    process_lock = _process_lock(lock_path)
    deadline = time.monotonic() + max(0.01, float(timeout))
    if not process_lock.acquire(timeout=max(0.01, float(timeout))):
        raise AuthLockTimeout(f"authentication lock timed out: {lock_path}")
    stream = None
    try:
        stream = lock_path.open("a+b")
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                return _HeldLock(stream, process_lock)
            except OSError:
                if time.monotonic() >= deadline:
                    raise AuthLockTimeout(f"authentication lock timed out: {lock_path}")
                time.sleep(0.05)
    except BaseException:
        if stream is not None:
            stream.close()
        process_lock.release()
        raise


def _release_lock(held: _HeldLock) -> None:
    import msvcrt

    try:
        held.stream.seek(0)
        msvcrt.locking(held.stream.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        held.stream.close()
        held.process_lock.release()


class _StateLock:
    def __init__(self, state_path: Path, timeout: float) -> None:
        self.state_path = state_path
        self.timeout = timeout
        self.held: _HeldLock | None = None

    def __enter__(self) -> None:
        self.held = _acquire_lock(self.state_path, self.timeout)

    def __exit__(self, *_exc: object) -> None:
        if self.held is not None:
            _release_lock(self.held)
            self.held = None


def _account_fingerprint(username: str) -> str:
    normalized = str(username or "").strip().casefold()
    if not normalized:
        raise AuthStateError("authentication account is empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_state(fingerprint: str, now: datetime) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "account_fingerprint": fingerprint,
        "utc_date": now.date().isoformat(),
        "auth_attempts": 0,
        "last_auth_utc": None,
        "generation": 0,
        "cookie_blob_dpapi_b64": None,
    }


def _load_state(path: Path, fingerprint: str, now: datetime) -> dict[str, Any]:
    if not path.exists():
        return _new_state(fingerprint, now)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AuthStateError(f"authentication state is unreadable: {path}") from exc
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        raise AuthStateError(f"authentication state version is unsupported: {path}")
    if raw.get("account_fingerprint") != fingerprint:
        raise AuthStateError(
            "authentication state account does not match the current account"
        )
    required = {
        "utc_date",
        "auth_attempts",
        "last_auth_utc",
        "generation",
        "cookie_blob_dpapi_b64",
    }
    if not required.issubset(raw):
        raise AuthStateError(f"authentication state schema is incomplete: {path}")
    try:
        raw["auth_attempts"] = int(raw["auth_attempts"])
        raw["generation"] = int(raw["generation"])
    except (TypeError, ValueError) as exc:
        raise AuthStateError(
            f"authentication state counters are invalid: {path}"
        ) from exc
    if raw["auth_attempts"] < 0 or raw["generation"] < 0:
        raise AuthStateError(f"authentication state counters are invalid: {path}")
    if raw["utc_date"] != now.date().isoformat():
        raw["utc_date"] = now.date().isoformat()
        raw["auth_attempts"] = 0
    return raw


def _save_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp.write_text(
            json.dumps(dict(state), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # On Windows, os.replace can raise PermissionError if the target is
        # transiently locked by another process reading it.  Retry briefly.
        for _attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if _attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _protect_cookie_rows(rows: list[dict[str, Any]]) -> str:
    try:
        import win32crypt

        payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        protected = win32crypt.CryptProtectData(
            payload, "alpha-wq-auth-cookie", None, None, None, 0
        )
        encrypted = protected[1] if isinstance(protected, tuple) else protected
        return base64.b64encode(encrypted).decode("ascii")
    except Exception as exc:
        raise AuthStateError("DPAPI could not encrypt authentication cookies") from exc


def _unprotect_cookie_rows(blob: str | None) -> list[dict[str, Any]]:
    if not blob:
        return []
    try:
        import win32crypt

        encrypted = base64.b64decode(blob, validate=True)
        unprotected = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
        clear = unprotected[1] if isinstance(unprotected, tuple) else unprotected
        rows = json.loads(clear.decode("utf-8"))
    except Exception as exc:
        raise AuthStateError("DPAPI could not decrypt authentication cookies") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise AuthStateError("decrypted authentication cookie payload is invalid")
    return rows


def _requests_cookie_rows(session: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain or "",
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "expires": cookie.expires,
        }
        for cookie in session.cookies
    ]


def _restore_requests_cookies(session: Any, rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    session.cookies.clear()
    for row in rows:
        kwargs: dict[str, Any] = {
            "path": str(row.get("path") or "/"),
            "secure": bool(row.get("secure", False)),
        }
        if row.get("domain"):
            kwargs["domain"] = str(row["domain"])
        if row.get("expires") is not None:
            kwargs["expires"] = int(row["expires"])
        session.cookies.set(str(row["name"]), str(row["value"]), **kwargs)
    return True


def _aiohttp_cookie_rows(session: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for morsel in session.cookie_jar:
        expires: str | None = str(morsel["expires"]) or None
        rows.append(
            {
                "name": morsel.key,
                "value": morsel.value,
                "domain": str(morsel["domain"] or ""),
                "path": str(morsel["path"] or "/"),
                "secure": bool(morsel["secure"]),
                "expires": expires,
            }
        )
    return rows


def _restore_aiohttp_cookies(session: Any, rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    from yarl import URL

    session.cookie_jar.clear()
    for row in rows:
        cookie = SimpleCookie()
        name = str(row["name"])
        cookie[name] = str(row["value"])
        cookie[name]["path"] = str(row.get("path") or "/")
        domain = str(row.get("domain") or "")
        if domain:
            cookie[name]["domain"] = domain
        if row.get("secure"):
            cookie[name]["secure"] = True
        if row.get("expires"):
            cookie[name]["expires"] = str(row["expires"])
        host = domain.lstrip(".") or "localhost"
        scheme = "https" if row.get("secure") else "http"
        session.cookie_jar.update_cookies(
            cookie, response_url=URL(f"{scheme}://{host}/")
        )
    return True


def _last_auth(state: Mapping[str, Any]) -> datetime | None:
    value = state.get("last_auth_utc")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise AuthStateError(
            "authentication state has an invalid last_auth_utc"
        ) from exc


def _within_cooldown(
    state: Mapping[str, Any], now: datetime, cooldown_seconds: float
) -> bool:
    last = _last_auth(state)
    return last is not None and 0 <= (now - last).total_seconds() < max(
        0.0, cooldown_seconds
    )


def _session_generation(session: Any) -> int | None:
    with _SESSION_GUARD:
        return _SESSION_GENERATIONS.get(session)


def _mark_session(session: Any, generation: int) -> None:
    with _SESSION_GUARD:
        _SESSION_GENERATIONS[session] = generation


def _status_code(response: Any) -> int:
    code = getattr(response, "status_code", getattr(response, "status", None))
    if code is None:
        raise AuthenticationFailed("authentication callback returned no HTTP status")
    return int(code)


def _raise_for_auth_status(code: int, response: Any = None) -> None:
    if 200 <= code < 300:
        return
    inquiry = ""
    try:
        payload = response.json() if response is not None and hasattr(response, "json") else {}
        if isinstance(payload, dict):
            inquiry = str(payload.get("inquiry") or "").strip()
    except Exception:
        inquiry = ""
    if inquiry:
        raise AuthenticationFailed(
            "password login requires Persona biometrics; open "
            f"https://platform.worldquantbrain.com/authenticate?inquiry={inquiry} "
            f"complete face/captcha, then reuse the browser session cookie (HTTP {code})"
        )
    raise AuthenticationFailed(f"authentication endpoint returned HTTP {code}")


def _check_settings(settings: AuthSettings) -> None:
    if not 1 <= int(settings.daily_cap) <= 5:
        raise ValueError("authentication daily_cap must be between 1 and 5")
    if not 1 <= int(settings.max_attempts) <= 2:
        raise ValueError("authentication max_attempts must be between 1 and 2")
    if float(settings.cooldown_seconds) < 0:
        raise ValueError("authentication cooldown_seconds must not be negative")


def _reserve_attempt(path: Path, state: dict[str, Any], settings: AuthSettings) -> None:
    if int(state["auth_attempts"]) >= int(settings.daily_cap):
        raise AuthDailyLimitExceeded(
            f"UTC daily authentication safety cap reached ({settings.daily_cap}); "
            "inspect abnormal retries before manually resetting the state"
        )
    state["auth_attempts"] = int(state["auth_attempts"]) + 1
    _save_state(path, state)


def _should_retry(code: int | None, attempt: int, settings: AuthSettings) -> bool:
    return attempt < int(settings.max_attempts) and (code is None or code >= 500)


def ensure_authenticated(
    requests_session: Any,
    login_callback: Callable[[], Any],
    username: str,
    settings: AuthSettings | None = None,
    *,
    force: bool = False,
) -> AuthResult:
    settings = settings or AuthSettings()
    _check_settings(settings)
    path = settings.resolved_state_path()
    fingerprint = _account_fingerprint(username)
    with _StateLock(path, settings.lock_timeout_seconds):
        now = _utc_now()
        state = _load_state(path, fingerprint, now)
        local_generation = _session_generation(requests_session)
        rows = _unprotect_cookie_rows(state.get("cookie_blob_dpapi_b64"))
        can_restore = bool(rows) and (
            (not force and _within_cooldown(state, now, settings.cooldown_seconds))
            or (
                force
                and (
                    local_generation is None
                    or local_generation < int(state["generation"])
                )
            )
        )
        if can_restore:
            restored = _restore_requests_cookies(requests_session, rows)
            _mark_session(requests_session, int(state["generation"]))
            _save_state(path, state)
            return AuthResult(
                False, restored, int(state["generation"]), int(state["auth_attempts"])
            )
        if not force and _within_cooldown(state, now, settings.cooldown_seconds):
            _mark_session(requests_session, int(state["generation"]))
            _save_state(path, state)
            return AuthResult(
                False, False, int(state["generation"]), int(state["auth_attempts"])
            )

        last_error: Exception | None = None
        for attempt in range(1, int(settings.max_attempts) + 1):
            _reserve_attempt(path, state, settings)
            code: int | None = None
            try:
                response = login_callback()
                code = _status_code(response)
                _raise_for_auth_status(code, response)
            except Exception as exc:
                last_error = exc
                if _should_retry(code, attempt, settings):
                    continue
                if isinstance(
                    exc, (AuthDailyLimitExceeded, AuthStateError, AuthenticationFailed)
                ):
                    raise
                raise AuthenticationFailed(
                    f"authentication request failed: {exc}"
                ) from exc
            cookie_rows = _requests_cookie_rows(requests_session)
            state["last_auth_utc"] = now.isoformat().replace("+00:00", "Z")
            state["generation"] = int(state["generation"]) + 1
            state["cookie_blob_dpapi_b64"] = _protect_cookie_rows(cookie_rows)
            _save_state(path, state)
            _mark_session(requests_session, int(state["generation"]))
            return AuthResult(
                True, False, int(state["generation"]), int(state["auth_attempts"])
            )
        raise AuthenticationFailed(f"authentication request failed: {last_error}")


async def ensure_authenticated_async(
    aiohttp_session: Any,
    login_callback: Callable[[], Awaitable[Any]],
    username: str,
    settings: AuthSettings | None = None,
    *,
    force: bool | None = None,
) -> AuthResult:
    settings = settings or AuthSettings()
    _check_settings(settings)
    path = settings.resolved_state_path()
    fingerprint = _account_fingerprint(username)
    effective_force = (
        (_session_generation(aiohttp_session) is not None)
        if force is None
        else bool(force)
    )
    held = await asyncio.to_thread(_acquire_lock, path, settings.lock_timeout_seconds)
    try:
        now = _utc_now()
        state = _load_state(path, fingerprint, now)
        local_generation = _session_generation(aiohttp_session)
        rows = _unprotect_cookie_rows(state.get("cookie_blob_dpapi_b64"))
        can_restore = bool(rows) and (
            (
                not effective_force
                and _within_cooldown(state, now, settings.cooldown_seconds)
            )
            or (
                effective_force
                and (
                    local_generation is None
                    or local_generation < int(state["generation"])
                )
            )
        )
        if can_restore:
            restored = _restore_aiohttp_cookies(aiohttp_session, rows)
            _mark_session(aiohttp_session, int(state["generation"]))
            _save_state(path, state)
            return AuthResult(
                False, restored, int(state["generation"]), int(state["auth_attempts"])
            )
        if not effective_force and _within_cooldown(
            state, now, settings.cooldown_seconds
        ):
            _mark_session(aiohttp_session, int(state["generation"]))
            _save_state(path, state)
            return AuthResult(
                False, False, int(state["generation"]), int(state["auth_attempts"])
            )

        last_error: Exception | None = None
        for attempt in range(1, int(settings.max_attempts) + 1):
            _reserve_attempt(path, state, settings)
            code: int | None = None
            try:
                response = await login_callback()
                code = _status_code(response)
                _raise_for_auth_status(code, response)
            except Exception as exc:
                last_error = exc
                if _should_retry(code, attempt, settings):
                    continue
                if isinstance(
                    exc, (AuthDailyLimitExceeded, AuthStateError, AuthenticationFailed)
                ):
                    raise
                raise AuthenticationFailed(
                    f"authentication request failed: {exc}"
                ) from exc
            cookie_rows = _aiohttp_cookie_rows(aiohttp_session)
            state["last_auth_utc"] = now.isoformat().replace("+00:00", "Z")
            state["generation"] = int(state["generation"]) + 1
            state["cookie_blob_dpapi_b64"] = _protect_cookie_rows(cookie_rows)
            _save_state(path, state)
            _mark_session(aiohttp_session, int(state["generation"]))
            return AuthResult(
                True, False, int(state["generation"]), int(state["auth_attempts"])
            )
        raise AuthenticationFailed(f"authentication request failed: {last_error}")
    finally:
        await asyncio.to_thread(_release_lock, held)


def prepare_child_environment(
    environment: Mapping[str, str], state_path: str | Path
) -> dict[str, str]:
    child = dict(environment)
    child[AUTH_STATE_ENV] = str(Path(state_path).expanduser().resolve())
    return child


def auth_state_status(
    state_path: str | Path, *, cooldown_seconds: float = 1500, daily_cap: int = 5
) -> str:
    path = Path(state_path).expanduser().resolve()
    if not path.exists():
        return "stale"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            return "unavailable"
        if (
            raw.get("utc_date") == _utc_now().date().isoformat()
            and int(raw.get("auth_attempts", 0)) >= daily_cap
        ):
            return "capped"
        last = _last_auth(raw)
        if last is not None and (_utc_now() - last).total_seconds() < cooldown_seconds:
            return "fresh"
        return "stale"
    except Exception:
        return "unavailable"


def clear_local_auth_artifacts(paths: list[str | Path]) -> tuple[str, ...]:
    """Delete only explicitly named local auth/session files; never scans Git or logs."""
    removed: list[str] = []
    for value in paths:
        target = Path(value).expanduser().resolve()
        if target.is_dir():
            raise AuthStateError(f"refusing to delete authentication directory: {target}")
        if target.is_file():
            target.unlink()
            removed.append(str(target))
    return tuple(removed)


def auth_state_metadata(
    state_path: str | Path,
    *,
    now: datetime | None = None,
    cooldown_seconds: float = 1500,
    daily_cap: int = 5,
) -> dict[str, Any]:
    """Return non-secret authentication timing/counter metadata only."""
    path = Path(state_path).expanduser().resolve()
    current = (now or _utc_now()).astimezone(timezone.utc)
    payload: dict[str, Any] = {
        "auth_status": auth_state_status(path, cooldown_seconds=cooldown_seconds, daily_cap=daily_cap),
        "last_successful_auth": None,
        "auth_age_seconds": None,
        "auth_attempts_today": 0,
        "generation": 0,
    }
    if not path.is_file():
        return payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        last = _last_auth(raw)
        payload["last_successful_auth"] = str(raw.get("last_auth_utc") or "") or None
        payload["auth_age_seconds"] = max(0.0, (current - last).total_seconds()) if last else None
        payload["auth_attempts_today"] = int(raw.get("auth_attempts") or 0)
        payload["generation"] = int(raw.get("generation") or 0)
    except Exception:
        payload["auth_status"] = "unavailable"
    return payload
