from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_real_worldquant_network(monkeypatch: pytest.MonkeyPatch):
    """Tests may use localhost fixtures, but must never resolve the real platform."""
    original = socket.getaddrinfo

    def guarded(host, *args, **kwargs):
        normalized = str(host or "").lower()
        if normalized.endswith("worldquantbrain.com"):
            raise AssertionError("real WorldQuant network access is forbidden in tests")
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
