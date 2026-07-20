"""Dynamic consultant quality-buffer judge."""

from __future__ import annotations

from alpha_mining.policy.consultant_policy import ConsultantPolicy


def quality_buffer_pass(
    metrics: dict[str, float],
    gates: dict[str, tuple[float, str]],
    *,
    policy: ConsultantPolicy | None = None,
) -> tuple[bool, list[str]]:
    policy = policy or ConsultantPolicy()
    reasons = []
    mapping = {
        "LOW_SHARPE": "sharpe",
        "LOW_FITNESS": "fitness",
        "LOW_TURNOVER": "turnover",
        "HIGH_TURNOVER": "turnover",
        "LOW_SUB_UNIVERSE_SHARPE": "sub_universe_sharpe",
        "SELF_CORRELATION": "self_correlation",
        "PROD_CORRELATION": "production_correlation",
        "PRODUCTION_CORRELATION": "production_correlation",
    }
    for gate, (limit, direction) in gates.items():
        key = mapping.get(gate.upper())
        if not key:
            continue
        if metrics.get(key) is None:
            reasons.append(f"{gate.upper()}_VALUE_MISSING")
            continue
        internal = policy.internal_limit(
            live_limit=limit, direction=direction, gate_name=gate
        )
        value = float(metrics[key])
        if (direction.upper() == "MIN" and value < internal) or (
            direction.upper() == "MAX" and value > internal
        ):
            reasons.append(gate.upper())
    return not reasons, reasons
