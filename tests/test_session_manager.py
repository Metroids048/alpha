from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from datetime import datetime, timedelta, timezone

import aiohttp
import pytest
import requests
from yarl import URL

from alpha_mining.auth.session_manager import (
    AuthSettings,
    AuthStateError,
    AuthenticationFailed,
    ensure_authenticated,
    ensure_authenticated_async,
)
import alpha_mining.auth.session_manager as session_manager


class _AuthHandler(BaseHTTPRequestHandler):
    calls = 0
    statuses: list[int] = []

    def do_POST(self) -> None:  # noqa: N802
        type(self).calls += 1
        status = type(self).statuses.pop(0) if type(self).statuses else 200
        self.send_response(status)
        if status < 400:
            self.send_header("Set-Cookie", "session=test-cookie; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def auth_server():
    _AuthHandler.calls = 0
    _AuthHandler.statuses = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/authentication", _AuthHandler
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _settings(tmp_path: Path, **overrides: object) -> AuthSettings:
    values = {
        "state_path": tmp_path / "auth-state.json",
        "cooldown_seconds": 1500,
        "daily_cap": 5,
        "max_attempts": 2,
        "lock_timeout_seconds": 5,
    }
    values.update(overrides)
    return AuthSettings(**values)


def test_twenty_mixed_sync_async_calls_share_one_real_login(
    tmp_path: Path, auth_server
) -> None:
    url, handler = auth_server
    settings = _settings(tmp_path)

    sync_session = requests.Session()
    first = ensure_authenticated(
        sync_session,
        lambda: sync_session.post(url, timeout=3),
        "researcher@example.test",
        settings,
    )
    assert first.performed_login is True

    for _ in range(9):
        session = requests.Session()
        result = ensure_authenticated(
            session,
            lambda: pytest.fail("cooldown reuse must not invoke sync login callback"),
            "researcher@example.test",
            settings,
        )
        assert result.performed_login is False
        assert session.cookies.get("session") == "test-cookie"

    async def exercise_async() -> None:
        for _ in range(10):
            jar = aiohttp.CookieJar(unsafe=True)
            async with aiohttp.ClientSession(cookie_jar=jar) as session:

                async def should_not_login():
                    pytest.fail("cooldown reuse must not invoke async login callback")

                result = await ensure_authenticated_async(
                    session,
                    should_not_login,
                    "researcher@example.test",
                    settings,
                    force=False,
                )
                assert result.performed_login is False
                assert any(
                    morsel.value == "test-cookie" for morsel in session.cookie_jar
                )

    asyncio.run(exercise_async())
    assert handler.calls == 1


def test_daily_cap_is_diagnostic_and_does_not_block_forced_relogin(tmp_path: Path, auth_server) -> None:
    url, handler = auth_server
    settings = _settings(tmp_path, daily_cap=2, max_attempts=1)
    session = requests.Session()

    ensure_authenticated(
        session,
        lambda: session.post(url, timeout=3),
        "cap@example.test",
        settings,
        force=True,
    )
    ensure_authenticated(
        session,
        lambda: session.post(url, timeout=3),
        "cap@example.test",
        settings,
        force=True,
    )
    ensure_authenticated(
        session,
        lambda: session.post(url, timeout=3),
        "cap@example.test",
        settings,
        force=True,
    )

    assert handler.calls == 3
    state = json.loads(Path(settings.state_path).read_text(encoding="utf-8"))
    assert state["auth_attempts"] == 3
    assert set(state) == {
        "version",
        "account_fingerprint",
        "utc_date",
        "auth_attempts",
        "last_auth_utc",
        "generation",
        "cookie_blob_dpapi_b64",
    }
    assert "test-cookie" not in Path(settings.state_path).read_text(encoding="utf-8")


def test_5xx_retries_once_but_4xx_never_retries(tmp_path: Path, auth_server) -> None:
    url, handler = auth_server
    handler.statuses = [500, 200]
    settings = _settings(tmp_path)
    session = requests.Session()
    result = ensure_authenticated(
        session, lambda: session.post(url, timeout=3), "retry@example.test", settings
    )
    assert result.performed_login is True
    assert handler.calls == 2

    second_settings = _settings(tmp_path / "other")
    Path(second_settings.state_path).parent.mkdir(parents=True)
    handler.statuses = [429, 200]
    with pytest.raises(AuthenticationFailed, match="429"):
        ensure_authenticated(
            requests.Session(),
            lambda: requests.post(url, timeout=3),
            "no-retry@example.test",
            second_settings,
        )
    assert handler.calls == 3


def test_password_auth_accepts_only_200_or_201(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_attempts=1)

    class Response:
        status_code = 204

    with pytest.raises(AuthenticationFailed, match="HTTP 204"):
        ensure_authenticated(
            requests.Session(), lambda: Response(), "status@example.test", settings
        )


def test_corrupt_state_fails_closed_without_login(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    Path(settings.state_path).write_text("{broken", encoding="utf-8")
    called = False

    def login():
        nonlocal called
        called = True

    with pytest.raises(AuthStateError, match="state"):
        ensure_authenticated(requests.Session(), login, "broken@example.test", settings)
    assert called is False


def test_account_fingerprint_mismatch_fails_closed(tmp_path: Path, auth_server) -> None:
    url, _handler = auth_server
    settings = _settings(tmp_path)
    session = requests.Session()
    ensure_authenticated(
        session, lambda: session.post(url, timeout=3), "one@example.test", settings
    )

    with pytest.raises(AuthStateError, match="account"):
        ensure_authenticated(
            requests.Session(),
            lambda: pytest.fail("must not log in with mismatched persisted account"),
            "two@example.test",
            settings,
        )


def test_concurrent_sync_paths_only_send_one_login(tmp_path: Path, auth_server) -> None:
    url, handler = auth_server
    settings = _settings(tmp_path)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    cookies: list[str | None] = []

    def worker() -> None:
        session = requests.Session()
        try:
            barrier.wait(timeout=3)
            ensure_authenticated(
                session,
                lambda: session.post(url, timeout=3),
                "concurrent@example.test",
                settings,
            )
            cookies.append(session.cookies.get("session"))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert cookies == ["test-cookie", "test-cookie"]
    assert handler.calls == 1


def test_sync_and_async_paths_compete_for_one_login(
    tmp_path: Path, auth_server
) -> None:
    url, handler = auth_server
    settings = _settings(tmp_path)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    results: list[tuple[str, bool, bool, int, int]] = []

    def sync_worker() -> None:
        session = requests.Session()
        try:
            barrier.wait(timeout=3)
            result = ensure_authenticated(
                session,
                lambda: session.post(url, timeout=3),
                "mixed-lock@example.test",
                settings,
            )
            results.append(
                (
                    "sync",
                    result.performed_login,
                    result.restored_session,
                    result.generation,
                    result.auth_attempts_today,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def async_worker() -> None:
        async def run() -> None:
            jar = aiohttp.CookieJar(unsafe=True)
            async with aiohttp.ClientSession(cookie_jar=jar) as session:

                async def login():
                    return await session.post(url)

                barrier.wait(timeout=3)
                result = await ensure_authenticated_async(
                    session,
                    login,
                    "mixed-lock@example.test",
                    settings,
                    force=False,
                )
                results.append(
                    (
                        "async",
                        result.performed_login,
                        result.restored_session,
                        result.generation,
                        result.auth_attempts_today,
                    )
                )

        try:
            asyncio.run(run())
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=sync_worker),
        threading.Thread(target=async_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    state = json.loads(Path(settings.state_path).read_text(encoding="utf-8"))
    assert handler.calls == 1, {"results": results, "state": state}


def test_stale_session_restores_new_generation_before_reauthenticating(
    tmp_path: Path, auth_server
) -> None:
    url, handler = auth_server
    settings = _settings(tmp_path)
    stale = requests.Session()
    ensure_authenticated(
        stale, lambda: stale.post(url, timeout=3), "generation@example.test", settings
    )

    newer = requests.Session()
    ensure_authenticated(
        newer, lambda: newer.post(url, timeout=3), "generation@example.test", settings
    )
    ensure_authenticated(
        newer,
        lambda: newer.post(url, timeout=3),
        "generation@example.test",
        settings,
        force=True,
    )
    assert handler.calls == 2

    restored = ensure_authenticated(
        stale,
        lambda: pytest.fail("stale generation must restore before logging in"),
        "generation@example.test",
        settings,
        force=True,
    )
    assert restored.performed_login is False
    assert restored.restored_session is True
    assert restored.generation == 2
    assert handler.calls == 2


def test_lock_timeout_fails_without_login(tmp_path: Path) -> None:
    settings = _settings(tmp_path, lock_timeout_seconds=0.05)
    state_path = settings.resolved_state_path()
    held = session_manager._acquire_lock(state_path, 1)
    called = False

    def login():
        nonlocal called
        called = True

    try:
        with pytest.raises(session_manager.AuthLockTimeout, match="timed out"):
            ensure_authenticated(
                requests.Session(), login, "lock@example.test", settings
            )
    finally:
        session_manager._release_lock(held)
    assert called is False


def test_sync_path_reads_clock_after_waiting_for_shared_lock(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path, lock_timeout_seconds=2)
    state_path = settings.resolved_state_path()
    username = "clock-after-lock@example.test"
    before_login = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    after_login = before_login + timedelta(seconds=10)
    lock_released = threading.Event()
    worker_finished = threading.Event()
    errors: list[BaseException] = []
    results = []

    def controlled_now() -> datetime:
        return after_login if lock_released.is_set() else before_login

    monkeypatch.setattr(session_manager, "_utc_now", controlled_now)
    held = session_manager._acquire_lock(state_path, 1)

    def worker() -> None:
        try:
            results.append(
                ensure_authenticated(
                    requests.Session(),
                    lambda: pytest.fail("state written while waiting must be reused"),
                    username,
                    settings,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            worker_finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        state = session_manager._new_state(
            session_manager._account_fingerprint(username), after_login
        )
        state["last_auth_utc"] = after_login.isoformat().replace("+00:00", "Z")
        state["generation"] = 1
        session_manager._save_state(state_path, state)
    finally:
        lock_released.set()
        session_manager._release_lock(held)
    thread.join(timeout=5)

    assert worker_finished.is_set()
    assert errors == []
    assert len(results) == 1
    assert results[0].performed_login is False
    assert results[0].generation == 1


def test_explicit_state_path_wins_over_ambient_environment(
    tmp_path: Path, monkeypatch
) -> None:
    explicit = tmp_path / "explicit.json"
    ambient = tmp_path / "ambient.json"
    monkeypatch.setenv("WQ_AUTH_STATE_FILE", str(ambient))
    assert AuthSettings(state_path=explicit).resolved_state_path() == explicit.resolve()


def test_utc_day_change_resets_attempt_counter(
    tmp_path: Path, auth_server, monkeypatch
) -> None:
    url, handler = auth_server
    settings = _settings(tmp_path, daily_cap=1, max_attempts=1)
    day_one = datetime(2026, 7, 17, 23, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(session_manager, "_utc_now", lambda: day_one)
    first_session = requests.Session()
    ensure_authenticated(
        first_session,
        lambda: first_session.post(url, timeout=3),
        "utc@example.test",
        settings,
        force=True,
    )
    ensure_authenticated(
        first_session,
        lambda: first_session.post(url, timeout=3),
        "utc@example.test",
        settings,
        force=True,
    )

    monkeypatch.setattr(
        session_manager, "_utc_now", lambda: day_one + timedelta(minutes=2)
    )
    second_session = requests.Session()
    ensure_authenticated(
        second_session,
        lambda: second_session.post(url, timeout=3),
        "utc@example.test",
        settings,
        force=True,
    )
    ensure_authenticated(
        second_session,
        lambda: second_session.post(url, timeout=3),
        "utc@example.test",
        settings,
        force=True,
    )
    state = json.loads(Path(settings.state_path).read_text(encoding="utf-8"))
    assert state["utc_date"] == "2026-07-18"
    assert state["auth_attempts"] == 1
    assert handler.calls == 3


def test_dpapi_blob_tampering_fails_closed(tmp_path: Path, auth_server) -> None:
    url, handler = auth_server
    settings = _settings(tmp_path)
    session = requests.Session()
    ensure_authenticated(
        session, lambda: session.post(url, timeout=3), "tamper@example.test", settings
    )
    state_path = Path(settings.state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    encrypted = bytearray(base64.b64decode(state["cookie_blob_dpapi_b64"]))
    encrypted[-1] ^= 0xFF
    state["cookie_blob_dpapi_b64"] = base64.b64encode(encrypted).decode("ascii")
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(AuthStateError, match="DPAPI"):
        ensure_authenticated(
            requests.Session(),
            lambda: pytest.fail("tampered state must not trigger a login"),
            "tamper@example.test",
            settings,
        )
    assert handler.calls == 1


def test_requests_cookie_attributes_round_trip_through_dpapi(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = requests.Session()

    class Response:
        status_code = 200

    def login():
        source.cookies.set(
            "session",
            "attribute-cookie",
            domain="example.test",
            path="/research",
            secure=True,
            expires=2_000_000_000,
        )
        return Response()

    ensure_authenticated(source, login, "attributes@example.test", settings)
    restored = requests.Session()
    ensure_authenticated(
        restored,
        lambda: pytest.fail("restoration must not log in"),
        "attributes@example.test",
        settings,
    )
    cookie = next(iter(restored.cookies))
    assert (cookie.name, cookie.value) == ("session", "attribute-cookie")
    assert cookie.domain == "example.test"
    assert cookie.path == "/research"
    assert cookie.secure is True
    assert cookie.expires == 2_000_000_000


def test_aiohttp_cookie_attributes_round_trip_through_dpapi(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class Response:
        status = 200

    async def exercise() -> None:
        source_jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=source_jar) as source:

            async def login():
                cookie = SimpleCookie()
                cookie["session"] = "async-attribute-cookie"
                cookie["session"]["domain"] = "example.test"
                cookie["session"]["path"] = "/research"
                cookie["session"]["secure"] = True
                cookie["session"]["expires"] = "Wed, 18 May 2033 03:33:20 GMT"
                source.cookie_jar.update_cookies(
                    cookie, response_url=URL("https://example.test/research")
                )
                return Response()

            await ensure_authenticated_async(
                source, login, "async-attributes@example.test", settings, force=False
            )

        restored_jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(cookie_jar=restored_jar) as restored:

            async def must_not_login():
                pytest.fail("restoration must not log in")

            await ensure_authenticated_async(
                restored,
                must_not_login,
                "async-attributes@example.test",
                settings,
                force=False,
            )
            morsel = next(iter(restored.cookie_jar))
            assert (morsel.key, morsel.value) == ("session", "async-attribute-cookie")
            assert morsel["domain"] == "example.test"
            assert morsel["path"] == "/research"
            assert bool(morsel["secure"]) is True
            assert "2033" in morsel["expires"]

    asyncio.run(exercise())


def test_five_fresh_processes_restore_one_dpapi_session(
    tmp_path: Path, auth_server
) -> None:
    url, handler = auth_server
    state_path = tmp_path / "subprocess-auth.json"
    worker = Path(__file__).with_name("wq_auth_subprocess_worker.py")

    for _ in range(5):
        completed = subprocess.run(
            [sys.executable, str(worker), str(state_path), url],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    assert handler.calls == 1


def test_concurrent_processes_share_the_cross_process_lock(
    tmp_path: Path, auth_server
) -> None:
    url, handler = auth_server
    state_path = tmp_path / "concurrent-process-auth.json"
    worker = Path(__file__).with_name("wq_auth_subprocess_worker.py")
    processes = [
        subprocess.Popen(
            [sys.executable, str(worker), str(state_path), url],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(5)
    ]
    results = [process.communicate(timeout=20) for process in processes]

    assert [process.returncode for process in processes] == [0, 0, 0, 0, 0], results
    assert handler.calls == 1


def test_auth_tests_never_name_the_real_platform_host() -> None:
    test_source = Path(__file__).read_text(encoding="utf-8")
    worker_source = (
        Path(__file__)
        .with_name("wq_auth_subprocess_worker.py")
        .read_text(encoding="utf-8")
    )
    forbidden = "api." + "worldquantbrain.com"
    assert forbidden not in test_source
    assert forbidden not in worker_source
