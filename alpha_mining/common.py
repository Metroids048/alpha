"""Shared small helpers for the alpha_mining package (no dependency on the monolith)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_workspace_env(path: str | Path | None = None) -> Path | None:
    """Load repo-root ``.env`` into ``os.environ`` (stdlib; no python-dotenv).

    ``WQ_USERNAME`` / ``WQ_PASSWORD`` always override empty-or-stale process values
    so CLI entry points match the legacy v50 runner behavior.
    """
    env_path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return None
    for raw in env_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and val:
            os.environ.setdefault(key, val)
            if key in ("WQ_USERNAME", "WQ_PASSWORD"):
                os.environ[key] = val
    return env_path


def subprocess_no_window_kwargs() -> dict[str, Any]:
    """Windows-only: avoid flashing cmd/PowerShell when spawning child processes."""
    if os.name != "nt":
        return {}
    kwargs: dict[str, Any] = {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flags:
        kwargs["creationflags"] = flags
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    kwargs["startupinfo"] = si
    return kwargs


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def metric_get(obj: dict | None, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None
    pools: list[Any] = [obj, obj.get("is"), obj.get("summary")]
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        for key in keys:
            if key in pool:
                return pool[key]
            low = key.lower()
            for k, v in pool.items():
                if str(k).lower() == low:
                    return v
    return None


def merge_json_dicts(a: dict | None, b: dict | None) -> dict | None:
    if not isinstance(a, dict):
        return b if isinstance(b, dict) else a
    if not isinstance(b, dict):
        return a
    merged = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = merge_json_dicts(merged[k], v)
        else:
            merged[k] = v
    return merged


def merge_feedback_metrics_snapshot(
    pipeline: Any, alpha_id: str | None, merged: dict | None
) -> dict | None:
    getter = getattr(pipeline, "_feedback_metrics_for_alpha", None)
    if not callable(getter):
        return merged
    metrics = getter(alpha_id)
    if not isinstance(metrics, dict) or not metrics:
        return merged
    base = merged if isinstance(merged, dict) else {}
    iso = dict(base.get("is") or {}) if isinstance(base.get("is"), dict) else {}
    changed = False
    for key, alt in (
        ("sharpe", "Sharpe"),
        ("fitness", "Fitness"),
        ("turnover", "Turnover"),
        ("returns", "Returns"),
        ("drawdown", "Drawdown"),
        ("margin", "Margin"),
    ):
        if metrics.get(key) is not None and metric_get(base, key, alt) is None:
            iso[key] = metrics[key]
            changed = True
    if not changed:
        return merged
    return merge_json_dicts(base, {"is": iso})


def alpha_id_from_progress(body: dict) -> str | None:
    alpha = body.get("alpha")
    if isinstance(alpha, str) and alpha.strip():
        return alpha.strip()
    if isinstance(alpha, dict):
        for key in ("id", "alpha", "alphaId"):
            v = alpha.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    for key in ("alphaId", "alpha_id"):
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def safe_json_text(text: str) -> dict:
    try:
        obj = json.loads(text)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def is_dns_error(exc: BaseException | str) -> bool:
    s = str(exc).lower()
    return any(
        x in s
        for x in (
            "gaierror",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
            "getaddrinfo failed",
        )
    )


def is_transient_connect_error(exc: BaseException) -> bool:
    if is_dns_error(exc):
        return True
    s = str(exc).lower()
    return any(
        x in s
        for x in (
            "cannot connect to host",
            "connection reset",
            "connection refused",
            "connection aborted",
            "timed out",
            "timeout",
            "ssl",
            "broken pipe",
        )
    )


def sig(expr: str) -> str:
    return re.sub(r"\s+", " ", str(expr or "").strip())
