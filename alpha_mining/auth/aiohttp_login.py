"""Centralized aiohttp login callback construction."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp


def build_aiohttp_login_callback(
    session: Any,
    *,
    base_url: str,
    proxy: str | None = None,
    timeout_seconds: float = 90.0,
) -> Callable[[], Awaitable[Any]]:
    """Return the bounded login operation used by the shared session manager."""

    async def login_once() -> Any:
        kwargs: dict[str, Any] = {"proxy": proxy} if proxy else {}
        async with session.post(
            f"{str(base_url).rstrip('/')}/authentication",
            timeout=aiohttp.ClientTimeout(total=max(1.0, float(timeout_seconds))),
            **kwargs,
        ) as response:
            await response.read()
            return response

    return login_once
