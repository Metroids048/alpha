"""Parse versionable gate observations from platform response payloads."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from alpha_mining.common import to_float

# Status constants for prod_corr (never use Boolean or numeric defaults).
PROD_CORR_PASS = "PASS"
PROD_CORR_FAIL = "FAIL"
PROD_CORR_PENDING = "PENDING"
PROD_CORR_MISSING = "MISSING"
PROD_CORR_UNKNOWN = "UNKNOWN"
PROD_CORR_ERROR = "ERROR"

# Matches: "Prod correlation 0.8379 is above cutoff of 0.7 and Sharpe not better by 10.0% or more"
# Also handles "Production correlation", mixed case, extra whitespace, percent sign optional.
_PROD_CORR_FAIL_PATTERN = re.compile(
    r"[Pp]rod(?:uction)?\s+corr(?:elation)?\s+([\d.]+)"
    r".*?cutoff\s+of\s+([\d.]+)"
    r"(?:.*?([0-9.]+)\s*%\s+or\s+more)?",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class ProdCorrDetails:
    """Structured result of a platform PROD_CORRELATION gate observation.

    status is always one of: PASS / FAIL / PENDING / MISSING / UNKNOWN / ERROR.
    Never a Boolean, never defaulting to 0 or 'PASS' when data is absent.
    """

    status: str                          # PASS/FAIL/PENDING/MISSING/UNKNOWN/ERROR
    prod_correlation: float | None       # observed value, e.g. 0.8379
    prod_cutoff: float | None            # live cutoff, e.g. 0.7
    required_sharpe_improvement: float | None  # as fraction, e.g. 0.10 for "10%"
    raw_message: str
    observed_at: str | None


def parse_prod_corr_details(gate_obs: "GateObservation") -> ProdCorrDetails:
    """Extract structured Prod Corr details from a GateObservation.

    Uses the gate's result field first (PASS/FAIL/PENDING), then tries to enrich
    with numeric values from the message text.  Parsing failure → UNKNOWN, not PASS.
    """
    gate_name = str(gate_obs.gate_name).upper()
    if "PROD" not in gate_name or "CORR" not in gate_name:
        return ProdCorrDetails(
            status=PROD_CORR_ERROR,
            prod_correlation=None,
            prod_cutoff=None,
            required_sharpe_improvement=None,
            raw_message=gate_obs.message,
            observed_at=gate_obs.observed_at,
        )

    result = str(gate_obs.result or "").upper().strip()
    # Map platform result to our status enum.
    if result == "PASS":
        status = PROD_CORR_PASS
    elif result in ("FAIL", "FAILED", "REJECTED"):
        status = PROD_CORR_FAIL
    elif result == "PENDING":
        status = PROD_CORR_PENDING
    elif result in ("MISSING", ""):
        status = PROD_CORR_MISSING
    else:
        status = PROD_CORR_UNKNOWN

    # Try to extract numeric details from the message.
    message = str(gate_obs.message or "")
    prod_correlation: float | None = gate_obs.value  # already parsed by check_parser
    prod_cutoff: float | None = gate_obs.limit       # already parsed by check_parser
    required_sharpe_improvement: float | None = None

    if message:
        m = _PROD_CORR_FAIL_PATTERN.search(message)
        if m:
            try:
                prod_correlation = float(m.group(1))
            except (TypeError, ValueError):
                pass
            try:
                prod_cutoff = float(m.group(2))
            except (TypeError, ValueError):
                pass
            if m.group(3):
                try:
                    required_sharpe_improvement = float(m.group(3)) / 100.0
                except (TypeError, ValueError):
                    pass

    return ProdCorrDetails(
        status=status,
        prod_correlation=prod_correlation,
        prod_cutoff=prod_cutoff,
        required_sharpe_improvement=required_sharpe_improvement,
        raw_message=message,
        observed_at=gate_obs.observed_at,
    )

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
