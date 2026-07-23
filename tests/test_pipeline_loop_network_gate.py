"""Tests for the proxy/network recovery gate (v50.4 pipeline-recovery).

Covers proxy-endpoint resolution + reachability gate in run_pipeline_loop, and the
proxy-error classification + network exit code in auto_alpha_pipeline_rebuilt_v50.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

import run_pipeline_loop as LOOP


MODULE_PATH = Path(__file__).resolve().parents[1] / "auto_alpha_pipeline_rebuilt_v50.py"
SPEC = importlib.util.spec_from_file_location(
    "auto_alpha_pipeline_rebuilt_v50", MODULE_PATH
)
V50 = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = V50
SPEC.loader.exec_module(V50)


# The exact ProxyError text seen in the field when the local proxy (127.0.0.1:7892)
# is down — note the OS-level message is locale-dependent (zh-CN WinError 10061),
# so classification must NOT rely on the English "connection refused" substring.
PROXY_DOWN_MSG = (
    "HTTPSConnectionPool(host='api.worldquantbrain.com', port=443): Max retries "
    "exceeded with url: /authentication (Caused by ProxyError('Unable to connect to "
    "proxy', NewConnectionError(\"HTTPSConnection(host='127.0.0.1', port=7892): "
    'Failed to establish a new connection: [WinError 10061]")))'
)


class ProxyEndpointResolutionTests(unittest.TestCase):
    def test_parse_host_port_plain_and_scheme(self) -> None:
        self.assertEqual(
            LOOP._parse_host_port("http://127.0.0.1:7892"), ("127.0.0.1", 7892)
        )
        self.assertEqual(LOOP._parse_host_port("127.0.0.1:7892"), ("127.0.0.1", 7892))
        self.assertEqual(
            LOOP._parse_host_port("http://proxy.local"), ("proxy.local", 80)
        )
        self.assertIsNone(LOOP._parse_host_port(""))
        self.assertIsNone(LOOP._parse_host_port("   "))

    def test_proxy_from_passthrough_space_form(self) -> None:
        passthrough = ["--mode", "full", "--https-proxy", "http://127.0.0.1:7892"]
        self.assertEqual(LOOP._proxy_endpoint(passthrough, env={}), ("127.0.0.1", 7892))

    def test_proxy_from_passthrough_equals_form(self) -> None:
        passthrough = ["--https-proxy=http://10.0.0.5:1080"]
        self.assertEqual(LOOP._proxy_endpoint(passthrough, env={}), ("10.0.0.5", 1080))

    def test_passthrough_wins_over_env(self) -> None:
        passthrough = ["--https-proxy", "http://127.0.0.1:7892"]
        env = {"HTTPS_PROXY": "http://9.9.9.9:3128"}
        self.assertEqual(
            LOOP._proxy_endpoint(passthrough, env=env), ("127.0.0.1", 7892)
        )

    def test_env_fallback_upper_and_lower(self) -> None:
        self.assertEqual(
            LOOP._proxy_endpoint([], env={"HTTPS_PROXY": "http://9.9.9.9:3128"}),
            ("9.9.9.9", 3128),
        )
        self.assertEqual(
            LOOP._proxy_endpoint([], env={"https_proxy": "http://8.8.8.8:1080"}),
            ("8.8.8.8", 1080),
        )

    def test_no_proxy_returns_none(self) -> None:
        self.assertIsNone(LOOP._proxy_endpoint(["--mode", "full"], env={}))


class WaitForNetworkTests(unittest.TestCase):
    def test_noop_when_no_proxy(self) -> None:
        calls = []
        orig = LOOP._tcp_reachable
        LOOP._tcp_reachable = lambda *a, **k: calls.append(a) or True  # type: ignore[assignment]
        try:
            state: dict = {}
            # No proxy configured → must not probe and must not block.
            LOOP._wait_for_network([], state, Path("nonexistent_state.json"), env={})
        finally:
            LOOP._tcp_reachable = orig  # type: ignore[assignment]
        self.assertEqual(calls, [])

    def test_returns_immediately_when_reachable(self) -> None:
        state: dict = {}
        saved: list = []
        orig_reach = LOOP._tcp_reachable
        orig_save = LOOP._save_state
        LOOP._tcp_reachable = lambda *a, **k: True  # type: ignore[assignment]
        LOOP._save_state = lambda p, s: saved.append(dict(s))  # type: ignore[assignment]
        try:
            LOOP._wait_for_network(
                ["--https-proxy", "http://127.0.0.1:7892"],
                state,
                Path("x.json"),
                env={},
            )
        finally:
            LOOP._tcp_reachable = orig_reach  # type: ignore[assignment]
            LOOP._save_state = orig_save  # type: ignore[assignment]
        self.assertIs(state["network_unreachable"], False)
        self.assertIn("last_network_ok_utc", state)

    def test_blocks_then_resumes_when_proxy_returns(self) -> None:
        # Unreachable twice, then reachable — verifies it loops and clears state.
        results = iter([False, False, True])
        slept: list = []
        orig_reach = LOOP._tcp_reachable
        orig_sleep = LOOP.time.sleep
        orig_save = LOOP._save_state
        LOOP._tcp_reachable = lambda *a, **k: next(results)  # type: ignore[assignment]
        LOOP.time.sleep = lambda s: slept.append(s)  # type: ignore[assignment]
        LOOP._save_state = lambda p, s: None  # type: ignore[assignment]
        try:
            state: dict = {}
            LOOP._wait_for_network(
                ["--https-proxy", "http://127.0.0.1:7892"],
                state,
                Path("x.json"),
                initial=60,
                cap=900,
                env={},
            )
        finally:
            LOOP._tcp_reachable = orig_reach  # type: ignore[assignment]
            LOOP.time.sleep = orig_sleep  # type: ignore[assignment]
            LOOP._save_state = orig_save  # type: ignore[assignment]
        self.assertEqual(slept, [60, 120])  # escalating backoff
        self.assertIs(state["network_unreachable"], False)

    def test_explicit_stop_interrupts_network_wait(self) -> None:
        stopped = False
        orig_reach = LOOP._tcp_reachable
        orig_save = LOOP._save_state
        LOOP._tcp_reachable = lambda *a, **k: False  # type: ignore[assignment]
        LOOP._save_state = lambda p, s: None  # type: ignore[assignment]

        def sleeper(_seconds: float) -> None:
            nonlocal stopped
            stopped = True

        try:
            reachable = LOOP._wait_for_network(
                ["--https-proxy", "http://127.0.0.1:7892"],
                {},
                Path("x.json"),
                initial=60,
                env={},
                sleeper=sleeper,
                stop_requested=lambda: stopped,
            )
        finally:
            LOOP._tcp_reachable = orig_reach  # type: ignore[assignment]
            LOOP._save_state = orig_save  # type: ignore[assignment]
        self.assertFalse(reachable)


class V50ClassificationTests(unittest.TestCase):
    def test_proxy_down_is_transient(self) -> None:
        self.assertTrue(V50._is_transient_connect_error(Exception(PROXY_DOWN_MSG)))

    def test_proxy_down_maps_to_network_exit_code(self) -> None:
        self.assertEqual(V50.NETWORK_EXIT_CODE, LOOP.NETWORK_EXIT_CODE)
        self.assertEqual(
            V50._exit_code_for_fatal(Exception(PROXY_DOWN_MSG)), V50.NETWORK_EXIT_CODE
        )

    def test_real_proxy_error_instance_maps_to_network_code(self) -> None:
        err = V50.requests.exceptions.ProxyError("Unable to connect to proxy")
        self.assertEqual(V50._exit_code_for_fatal(err), V50.NETWORK_EXIT_CODE)

    def test_generic_error_maps_to_one(self) -> None:
        self.assertEqual(V50._exit_code_for_fatal(ValueError("bad config")), 1)

    def test_http_429_maps_to_network_code(self) -> None:
        resp = V50.requests.Response()
        resp.status_code = 429
        err = V50.requests.exceptions.HTTPError("429 Client Error: Too Many Requests")
        err.response = resp
        self.assertEqual(V50._exit_code_for_fatal(err), V50.NETWORK_EXIT_CODE)

    def test_too_many_requests_message_maps_to_network_code(self) -> None:
        self.assertEqual(
            V50._exit_code_for_fatal(
                RuntimeError("Too Many Requests for url: /data-sets")
            ),
            V50.NETWORK_EXIT_CODE,
        )


if __name__ == "__main__":
    unittest.main()
