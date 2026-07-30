"""Deterministic, network-free scoring for the offline candidate queue."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from alpha_mining.domain.expression_ast import AstNode, ExpressionSyntaxError, depth, parse_expression

_RECENT_OUTCOME_LIMIT = 20
_FAILED_STATUS_TOKENS = ("FAIL", "REJECT", "ERROR", "INVALID")
_SUCCESS_STATUS_TOKENS = ("PASS", "COMPLETE", "SUCCESS", "SUBMITTED", "SIMULATED")
_STABLE_OPERATORS = {
    "abs",
    "add",
    "multiply",
    "rank",
    "sign",
    "subtract",
    "ts_decay_linear",
    "ts_delta",
    "ts_max",
    "ts_mean",
    "ts_min",
    "ts_rank",
    "ts_std_dev",
    "ts_sum",
    "ts_zscore",
}


@dataclass(frozen=True)
class ScoreComponents:
    local_score: float
    family_bonus: float
    failure_penalty: float

    @property
    def priority_score(self) -> float:
        return round(self.local_score + self.family_bonus - self.failure_penalty, 6)


def score_candidate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return scored rows in the canonical stable priority order."""

    family_counts = Counter(str(row.get("operator_family") or "") for row in rows)
    max_family_count = max(family_counts.values(), default=0)
    failed_signatures, family_failure_rates = _failure_profile(rows)

    scored: list[dict[str, str]] = []
    for source in rows:
        row = dict(source)
        family = str(row.get("operator_family") or "")
        local_score = _local_score(str(row.get("expression") or ""))
        family_bonus = (
            round(0.25 * (max_family_count - family_counts[family]) / max_family_count, 6)
            if max_family_count
            else 0.0
        )
        signature = _signature_key(str(row.get("canonical_signature") or ""))
        failure_penalty = round(
            (0.85 if signature and signature in failed_signatures else 0.0)
            + 0.25 * family_failure_rates.get(family, 0.0),
            6,
        )
        components = ScoreComponents(local_score, family_bonus, failure_penalty)
        row["local_score"] = f"{components.local_score:.6f}"
        row["priority_score"] = f"{components.priority_score:.6f}"
        scored.append(row)

    return sorted(
        scored,
        key=lambda row: (
            -float(row["priority_score"]),
            -float(row["local_score"]),
            str(row.get("operator_family") or ""),
            str(row.get("canonical_signature") or ""),
            str(row.get("candidate_id") or ""),
        ),
    )


def _local_score(expression: str) -> float:
    try:
        root = parse_expression(expression)
    except ExpressionSyntaxError:
        return 0.0

    operators = [node.value for node in _walk(root) if node.kind in {"call", "binary", "unary"}]
    redundant_wrappers = sum(
        1
        for node in _walk(root)
        if node.kind == "call"
        and node.value in {"rank", "zscore", "normalize"}
        and len(node.children) == 1
        and node.children[0].kind == "call"
        and node.children[0].value in {"rank", "zscore", "normalize"}
    )
    stable_bonus = 0.2 if root.kind == "call" and root.value == "rank" else 0.0
    if operators and all(operator in _STABLE_OPERATORS or operator in {"+", "-", "*"} for operator in operators):
        stable_bonus += 0.15
    risk_penalty = 0.12 * operators.count("divide") + 0.18 * operators.count("ts_corr")
    score = (
        4.0
        + stable_bonus
        - 0.10 * len(operators)
        - 0.08 * max(0, depth(root) - 2)
        - 0.20 * redundant_wrappers
        - risk_penalty
    )
    return round(max(0.0, score), 6)


def _failure_profile(rows: list[dict[str, str]]) -> tuple[set[str], dict[str, float]]:
    failed_signatures: set[str] = set()
    outcomes: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for row in rows:
        failed = _is_failed(row)
        succeeded = _is_success(row)
        if not failed and not succeeded:
            continue
        signature = _signature_key(str(row.get("canonical_signature") or ""))
        if failed and signature:
            failed_signatures.add(signature)
        family = str(row.get("operator_family") or "")
        recency = "\0".join(
            (
                str(row.get("updated_at") or ""),
                str(row.get("created_at") or ""),
                str(row.get("candidate_id") or ""),
            )
        )
        outcomes[family].append((recency, failed))

    rates: dict[str, float] = {}
    for family, family_outcomes in outcomes.items():
        recent = sorted(family_outcomes, reverse=True)[:_RECENT_OUTCOME_LIMIT]
        rates[family] = sum(failed for _, failed in recent) / len(recent)
    return failed_signatures, rates


def _is_failed(row: dict[str, str]) -> bool:
    status = str(row.get("queue_status") or "").upper()
    return bool(row.get("last_error_category") or row.get("last_error")) or any(
        token in status for token in _FAILED_STATUS_TOKENS
    )


def _is_success(row: dict[str, str]) -> bool:
    status = str(row.get("queue_status") or "").upper()
    return any(token in status for token in _SUCCESS_STATUS_TOKENS)


def _signature_key(raw: str) -> str:
    try:
        payload: Any = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw.strip()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _walk(root: AstNode) -> list[AstNode]:
    nodes = [root]
    for child in root.children:
        nodes.extend(_walk(child))
    return nodes
