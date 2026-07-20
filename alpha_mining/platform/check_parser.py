"""Parse versionable gate observations from platform response payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from alpha_mining.common import to_float

LIMIT_KEYS = ("limit", "threshold", "minimum", "maximum")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_hash(payload: Any) -> str:
    data = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    pools = [payload, payload.get("is"), payload.get("summary")]
    for pool in pools:
        if isinstance(pool, dict) and isinstance(pool.get("checks"), list):
            return [item for item in pool["checks"] if isinstance(item, dict)]
    return []


def _direction(name: str, item: dict[str, Any]) -> str:
    explicit = str(item.get("direction") or item.get("operator") or "").upper()
    if explicit in {"MIN", "MAX"}:
        return explicit
    if name.startswith(("LOW_", "MIN_")):
        return "MIN"
    if name.startswith(("HIGH_", "MAX_")) or "CORRELATION" in name:
        return "MAX"
    return "UNKNOWN"


@dataclass(frozen=True)
class GateObservation:
    gate_name: str
    result: str
    limit: float | None
    value: float | None
    message: str
    region: str
    universe: str
    delay: str
    alpha_type: str
    theme_id: str
    pyramid_id: str
    source_alpha_id: str
    observed_at: str | None
    raw_payload_hash: str
    direction: str
    ingested_at: str
    timestamp_source: str
    freshness_eligible: bool
    source: str
    observation_id: str


def parse_gate_observations(
    payload: dict[str, Any],
    *,
    observed_at: str | None = None,
    source: str = "platform_payload",
) -> list[GateObservation]:
    if not isinstance(payload, dict):
        return []
    raw_settings = payload.get("settings")
    settings: dict[str, Any] = raw_settings if isinstance(raw_settings, dict) else {}
    raw_hash = _canonical_hash(payload)
    timestamp = (
        observed_at
        or payload.get("observed_at")
        or payload.get("createdAt")
        or payload.get("dateCreated")
    )
    timestamp_source = (
        "argument" if observed_at else "payload" if timestamp else "missing"
    )
    out: list[GateObservation] = []
    for index, item in enumerate(_checks(payload)):
        raw_name = str(item.get("name") or item.get("check") or "").strip().upper()
        name = raw_name or f"UNKNOWN_CHECK_{_canonical_hash(item)[:12].upper()}"
        limit = next(
            (
                to_float(item.get(key))
                for key in LIMIT_KEYS
                if to_float(item.get(key)) is not None
            ),
            None,
        )
        observation_id = hashlib.sha256(
            f"{raw_hash}\0{index}\0{name}\0{timestamp or ''}".encode()
        ).hexdigest()
        out.append(
            GateObservation(
                gate_name=name,
                result=str(
                    item.get("result") or item.get("status") or "UNKNOWN"
                ).upper(),
                limit=limit,
                value=to_float(item.get("value")),
                message=str(item.get("message") or ""),
                region=str(
                    settings.get("region") or payload.get("region") or "*"
                ).upper(),
                universe=str(
                    settings.get("universe") or payload.get("universe") or "*"
                ).upper(),
                delay=str(
                    settings.get("delay")
                    if settings.get("delay") is not None
                    else payload.get("delay")
                    if payload.get("delay") is not None
                    else "*"
                ),
                alpha_type=str(
                    payload.get("type") or payload.get("alpha_type") or "*"
                ).upper(),
                theme_id=str(payload.get("theme_id") or payload.get("themeId") or "*"),
                pyramid_id=str(
                    payload.get("pyramid_id") or payload.get("pyramidId") or "*"
                ),
                source_alpha_id=str(payload.get("id") or payload.get("alpha_id") or ""),
                observed_at=str(timestamp) if timestamp else None,
                raw_payload_hash=raw_hash,
                direction=_direction(name, item),
                ingested_at=_utc_now(),
                timestamp_source=timestamp_source,
                freshness_eligible=bool(timestamp),
                source=source,
                observation_id=observation_id,
            )
        )
    return out
