from __future__ import annotations

import asyncio
from pathlib import Path


def test_production_root_entries_do_not_post_authentication_directly() -> None:
    root = Path(__file__).resolve().parents[1]
    production_entries = [
        root / "生成Alpha.py",
        root / "提交Alpha.py",
        root / "验证提交链路.py",
    ]

    production_modules = [
        path
        for path in (root / "alpha_mining").rglob("*.py")
        if "auth" not in path.relative_to(root / "alpha_mining").parts
        and path.relative_to(root).as_posix() != "alpha_mining/platform/client.py"
    ]
    violations = [
        path.relative_to(root).as_posix()
        for path in (*production_entries, *production_modules)
        if path.is_file() and "/authentication" in path.read_text(encoding="utf-8")
    ]

    assert violations == []


def test_central_aiohttp_login_callback_posts_once() -> None:
    from alpha_mining.auth.aiohttp_login import build_aiohttp_login_callback

    class Response:
        status = 201

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def read(self) -> bytes:
            return b""

    class Session:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def post(self, url: str, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    callback = build_aiohttp_login_callback(
        session,
        base_url="https://api.example.test/",
        proxy="http://proxy.example.test:8080",
    )

    response = asyncio.run(callback())

    assert response.status == 201
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == "https://api.example.test/authentication"
    assert kwargs["proxy"] == "http://proxy.example.test:8080"
    assert kwargs["timeout"].total == 90.0


def test_retired_direct_auth_entries_fail_closed() -> None:
    """Retired root auth/resim entries must stay gone after freeze retirement."""
    root = Path(__file__).resolve().parents[1]
    for name in (
        "brain_batch_resim.py",
        "brain_scan_pipeline.py",
        "run_pipeline_cycle.py",
        "run_pipeline_loop.py",
        "run_pipeline_supervisor.py",
        "生成Alpha候选.py",
        "启动Alpha主线.py",
    ):
        assert not (root / name).exists(), f"retired entry unexpectedly present: {name}"
