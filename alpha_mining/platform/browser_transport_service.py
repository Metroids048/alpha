"""Loopback owner for one live browser-backed WorldQuant session.

The service deliberately exposes only the already-sanitized browser transport
response surface. Browser credentials remain inside the headed Chrome context.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .browser_transport import BrowserBackedWorldQuantTransport, BrowserTransportError


class BrowserTransportService(HTTPServer):
    transport: BrowserBackedWorldQuantTransport


class _Handler(BaseHTTPRequestHandler):
    server: BrowserTransportService

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "READY"})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/request":
            self._send(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request payload must be an object")
            response = self.server.transport.request(
                str(payload.get("method", "")),
                str(payload.get("url", "")),
                json=payload.get("json"),
                endpoint_class=str(payload.get("endpoint_class", "read")),
                recovery_probe=bool(payload.get("recovery_probe", False)),
            )
        except (BrowserTransportError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send(400, {"error": type(exc).__name__})
            return
        self._send(200, {"status_code": response.status_code, "text": response.text, "headers": dict(response.headers)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-dir", default=".validation_workspace/wq_browser_profile")
    parser.add_argument("--database", default="数据/本地运行产物/数据库/research_memory.sqlite")
    parser.add_argument("--lock-path", default="worldquant_api.lock")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    transport = BrowserBackedWorldQuantTransport(
        profile_dir=args.profile_dir,
        database=args.database,
        lock_path=args.lock_path,
        worker_url="",
    )
    transport.open()
    server = BrowserTransportService(("127.0.0.1", args.port), _Handler)
    server.transport = transport
    print("WORLDQUANT_BROWSER_READY_FOR_BIOMETRIC", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
