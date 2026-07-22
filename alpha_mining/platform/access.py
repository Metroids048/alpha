"""Cross-process platform access lock, persistent 429 circuit, and safe request audit."""

from __future__ import annotations

import hashlib
import os
import random
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable

from alpha_mining.storage.migrations import migrate


class PlatformAccessBusy(RuntimeError):
    """Another local process currently owns the platform request right."""


class CircuitOpen(RuntimeError):
    """Platform calls are blocked by the global rate-limit circuit."""


_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}


def _process_lock(path: Path) -> threading.Lock:
    key = str(path.resolve()).casefold()
    with _LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


class GlobalPlatformLock:
    """Nonblocking machine-local file lock for every WorldQuant API request."""

    def __init__(self, path: str | Path = "worldquant_api.lock") -> None:
        self.path = Path(path).expanduser().resolve()
        self._process = _process_lock(self.path)
        self._stream = None

    def __enter__(self) -> "GlobalPlatformLock":
        if not self._process.acquire(blocking=False):
            raise PlatformAccessBusy("another process owns the platform API lock")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = self.path.open("a+b")
            if self.path.stat().st_size == 0:
                self._stream.write(b"0")
                self._stream.flush()
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except OSError as exc:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            self._process.release()
            raise PlatformAccessBusy("another process owns the platform API lock") from exc

    def __exit__(self, *_exc: object) -> None:
        try:
            if self._stream is not None:
                self._stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            self._process.release()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(value: object, now: datetime) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return max(0.0, float(text))
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


@dataclass(frozen=True)
class AccessState:
    state: str
    opened_at: str | None
    retry_after_until: str | None
    recovery_attempts: int
    max_auto_recoveries: int
    last_successful_auth: str | None
    last_401: str | None
    last_403: str | None
    last_429: str | None
    reason: str


@dataclass(frozen=True)
class RequestPermit:
    event_id: str
    timestamp: str
    endpoint_class: str
    method: str
    auth_session_id: str
    process_id: int
    attempt: int
    recovery_probe: bool
    sync_id: str


class PlatformAccessController:
    def __init__(
        self,
        database: str | Path = "research_memory.sqlite",
        lock_path: str | Path = "worldquant_api.lock",
        *,
        clock: Callable[[], datetime] = _utc_now,
        jitter: Callable[[float, float], float] = random.uniform,
        max_auto_recoveries: int = 4,
        fallback_backoff_seconds: tuple[float, ...] = (60, 180, 600, 1800),
        session_id: str | None = None,
    ) -> None:
        self.database = Path(database)
        self.lock_path = Path(lock_path)
        self.clock = clock
        self.jitter = jitter
        self.max_auto_recoveries = max(1, int(max_auto_recoveries))
        self.fallback_backoff_seconds = tuple(float(value) for value in fallback_backoff_seconds)
        self.session_id = session_id or uuid.uuid4().hex[:16]
        migrate(self.database)
        with sqlite3.connect(self.database) as con:
            con.execute(
                "UPDATE platform_access_state SET max_auto_recoveries=? WHERE singleton=1",
                (self.max_auto_recoveries,),
            )

    def global_lock(self) -> GlobalPlatformLock:
        return GlobalPlatformLock(self.lock_path)

    def status(self) -> AccessState:
        with sqlite3.connect(self.database) as con:
            row = con.execute(
                "SELECT state,opened_at,retry_after_until,recovery_attempts,max_auto_recoveries,"
                "last_successful_auth,last_401,last_403,last_429,reason "
                "FROM platform_access_state WHERE singleton=1"
            ).fetchone()
        if row is None:
            raise CircuitOpen("platform access state is missing")
        return AccessState(str(row[0]), row[1], row[2], int(row[3]), int(row[4]), row[5], row[6], row[7], row[8], str(row[9] or ""))

    def before_request(
        self,
        endpoint_class: str,
        method: str,
        *,
        recovery_probe: bool = False,
        attempt: int = 1,
        sync_id: str = "",
    ) -> RequestPermit:
        now = self.clock().astimezone(timezone.utc)
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT state,retry_after_until,recovery_attempts,max_auto_recoveries FROM platform_access_state WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise CircuitOpen("platform access state is missing")
            state, until_text, recoveries, maximum = str(row[0]), row[1], int(row[2]), int(row[3])
            until = _parse_time(until_text)
            if state == "MANUAL_INTERVENTION":
                raise CircuitOpen("manual platform access recovery is required")
            if state == "HALF_OPEN":
                raise CircuitOpen("a single recovery probe is already in flight")
            if state == "RATE_LIMITED":
                if until is None or now < until:
                    raise CircuitOpen(f"platform rate-limit circuit open until {until_text or 'manual review'}")
                if not recovery_probe:
                    raise CircuitOpen("rate-limit interval expired; only one explicit read probe is allowed")
                if method.upper() != "GET":
                    raise CircuitOpen("rate-limit recovery probe must be a GET")
                if recoveries >= maximum:
                    con.execute(
                        "UPDATE platform_access_state SET state='MANUAL_INTERVENTION',reason='max_auto_recoveries_exceeded',updated_at=? WHERE singleton=1",
                        (_iso(now),),
                    )
                    raise CircuitOpen("maximum automatic recovery probes exceeded")
                con.execute(
                    "UPDATE platform_access_state SET state='HALF_OPEN',recovery_attempts=recovery_attempts+1,"
                    "reason='single_recovery_probe',updated_at=? WHERE singleton=1",
                    (_iso(now),),
                )
            elif recovery_probe and state != "CLOSED":
                raise CircuitOpen(f"recovery probe is not allowed while state={state}")
        return RequestPermit(
            uuid.uuid4().hex,
            _iso(now) or "",
            str(endpoint_class),
            str(method).upper(),
            self.session_id,
            os.getpid(),
            max(1, int(attempt)),
            bool(recovery_probe),
            str(sync_id or ""),
        )

    def record_response(
        self,
        permit: RequestPermit,
        *,
        status_code: int,
        retry_after: object = None,
        request_id: str = "",
        response_body: bytes | str | None = None,
        error_class: str = "",
    ) -> None:
        now = self.clock().astimezone(timezone.utc)
        code = int(status_code)
        raw = response_body if isinstance(response_body, bytes) else str(response_body or "").encode("utf-8")
        response_hash = hashlib.sha256(raw).hexdigest() if raw else ""
        state = self.status()
        retry_seconds = _retry_after_seconds(retry_after, now)
        backoff = 0.0
        until: datetime | None = None
        if code == 429:
            if retry_seconds <= 0:
                index = min(state.recovery_attempts, max(0, len(self.fallback_backoff_seconds) - 1))
                base = self.fallback_backoff_seconds[index] if self.fallback_backoff_seconds else 60.0
                backoff = max(0.0, base + self.jitter(0.0, max(1.0, base * 0.5)))
                retry_seconds = backoff
            until = now + timedelta(seconds=retry_seconds)
        with sqlite3.connect(self.database) as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """INSERT INTO platform_request_events
                (event_id,timestamp,endpoint_class,method,status_code,retry_after_seconds,retry_after_until,
                 auth_session_id,process_id,request_id,attempt,backoff_seconds,response_hash,error_class,sync_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    permit.event_id, permit.timestamp, permit.endpoint_class, permit.method, code,
                    retry_seconds, _iso(until), permit.auth_session_id, permit.process_id,
                    str(request_id or ""), permit.attempt, backoff, response_hash,
                    str(error_class or ""), permit.sync_id,
                ),
            )
            fields = ["last_request_id=?", "last_session_id=?", "updated_at=?"]
            values: list[object] = [str(request_id or ""), permit.auth_session_id, _iso(now)]
            if code == 429:
                manual = state.state == "HALF_OPEN" and state.recovery_attempts >= state.max_auto_recoveries
                fields.extend(["state=?", "opened_at=?", "retry_after_until=?", "last_429=?", "reason=?"])
                values.extend([
                    "MANUAL_INTERVENTION" if manual else "RATE_LIMITED",
                    _iso(now), _iso(until), _iso(now),
                    "max_auto_recoveries_exceeded" if manual else "http_429",
                ])
            elif code == 401:
                fields.extend(["last_401=?", "reason=?"])
                values.extend([_iso(now), "http_401"])
            elif code == 403:
                fields.extend(["last_403=?", "reason=?"])
                values.extend([_iso(now), "http_403"])
            elif 200 <= code < 300 and state.state == "HALF_OPEN":
                fields.extend(["state='CLOSED'", "retry_after_until=NULL", "recovery_attempts=0", "reason='recovery_probe_passed'"])
            if 200 <= code < 300 and permit.endpoint_class == "authentication":
                fields.append("last_successful_auth=?")
                values.append(_iso(now))
            values.append(1)
            con.execute(f"UPDATE platform_access_state SET {','.join(fields)} WHERE singleton=?", values)

