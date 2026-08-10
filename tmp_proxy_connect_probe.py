"""Raw HTTP CONNECT tunnel probe — TEMPORARY_VALIDATION_HARNESS / NOT_FOR_COMMIT.

Verifies the FlClash -> api.worldquantbrain.com:443 leg WITHOUT sending any
WorldQuant HTTP request. Establishes a CONNECT tunnel, reads the proxy's
status line, then closes immediately. Does not consume catalog/API rate limit.
"""

from __future__ import annotations

import socket
import time

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 7892
TARGET_HOST = "api.worldquantbrain.com"
TARGET_PORT = 443
PROBES = 3
INTERVAL_S = 5.0
TIMEOUT_S = 15.0


def probe_once(index: int) -> tuple[bool, str, float]:
    """Open one CONNECT tunnel. Returns (ok, detail, elapsed_ms)."""
    started = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT_S)
    try:
        sock.connect((PROXY_HOST, PROXY_PORT))
        request = (
            f"CONNECT {TARGET_HOST}:{TARGET_PORT} HTTP/1.1\r\n"
            f"Host: {TARGET_HOST}:{TARGET_PORT}\r\n"
            f"Proxy-Connection: close\r\n"
            f"\r\n"
        )
        sock.sendall(request.encode("ascii"))

        # Read only the proxy status line / headers. No TLS handshake,
        # no GET/HEAD/POST is ever written into the tunnel.
        chunks: list[bytes] = []
        while b"\r\n\r\n" not in b"".join(chunks):
            piece = sock.recv(1024)
            if not piece:
                break
            chunks.append(piece)
            if sum(len(c) for c in chunks) > 8192:
                break

        raw = b"".join(chunks)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if not raw:
            return False, "empty response from proxy", elapsed_ms

        status_line = raw.split(b"\r\n", 1)[0].decode("latin-1", "replace").strip()
        parts = status_line.split(None, 2)
        code = parts[1] if len(parts) >= 2 else "?"
        ok = code == "200"
        return ok, status_line, elapsed_ms
    except Exception as exc:  # noqa: BLE001 - probe must classify, not raise
        elapsed_ms = (time.monotonic() - started) * 1000.0
        return False, f"{type(exc).__name__}: {exc}", elapsed_ms
    finally:
        try:
            sock.close()
        except OSError:
            pass


def main() -> int:
    print(f"proxy={PROXY_HOST}:{PROXY_PORT} target={TARGET_HOST}:{TARGET_PORT}")
    print(f"probes={PROBES} interval={INTERVAL_S}s timeout={TIMEOUT_S}s")
    print("mode=CONNECT_ONLY (no GET/HEAD/POST, no TLS handshake)")
    print("-" * 60)

    results: list[bool] = []
    for i in range(1, PROBES + 1):
        ok, detail, elapsed_ms = probe_once(i)
        results.append(ok)
        label = "OK  " if ok else "FAIL"
        print(f"probe {i}/{PROBES}: {label} [{elapsed_ms:7.1f} ms] {detail}")
        if i < PROBES:
            time.sleep(INTERVAL_S)

    print("-" * 60)
    passed = sum(results)
    print(f"passed={passed}/{PROBES}")
    if passed == PROBES:
        print("VERDICT: PROXY_STABLE_FOR_SYNC")
        return 0
    print("VERDICT: LOCAL_PROXY_UNSTABLE")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
