"""Pure protocol parsing extracted from the legacy v50 runtime."""

from __future__ import annotations

from typing import Any


def alpha_id_from_progress(payload: dict[str, Any]) -> str:
    for key in ("alpha", "alphaId", "alpha_id", "id"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("id") or value.get("alphaId")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    is_metrics = payload.get("is") if isinstance(payload.get("is"), dict) else {}
    candidates = (
        is_metrics.get("checks"),
        payload.get("checks"),
        payload.get("checkResults"),
    )
    for value in candidates:
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, dict)]
    return []


def extract_metrics(payload: dict[str, Any]) -> dict[str, float]:
    source = payload.get("is") if isinstance(payload.get("is"), dict) else payload
    metrics: dict[str, float] = {}
    for key in ("sharpe", "fitness", "turnover", "returns", "drawdown", "margin"):
        value = source.get(key)
        try:
            if value is not None:
                metrics[key] = float(value)
        except (TypeError, ValueError):
            continue
    return metrics
