"""Local FASTEXPR validation against synchronized metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from alpha_mining.domain.expression_ast import AstNode, ExpressionSyntaxError, parse_expression
from alpha_mining.offline.metadata import MetadataCache
from alpha_mining.parser.fastplus_gate import check_expression


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


class ExpressionCatalog(Protocol):
    """Read-only expression validation contract used by production screening."""

    def validate(
        self,
        expression: str,
        *,
        expected_dataset_id: str | None = None,
        region: str | None = None,
        universe: str | None = None,
        delay: int | str | None = None,
    ) -> list[ValidationIssue]: ...


class LocalExpressionValidator:
    """Validate FASTEXPR exclusively against one immutable metadata snapshot."""

    def __init__(
        self,
        metadata: MetadataCache,
        *,
        max_age_hours: float = 168,
        allow_stale_catalog: bool = False,
    ) -> None:
        self.metadata = metadata
        self.max_age_hours = float(max_age_hours)
        self.allow_stale_catalog = bool(allow_stale_catalog)

    def validate(
        self,
        expression: str,
        *,
        expected_dataset_id: str | None = None,
        region: str | None = None,
        universe: str | None = None,
        delay: int | str | None = None,
    ) -> list[ValidationIssue]:
        context_issue = self._context_issue(region=region, universe=universe, delay=delay)
        if context_issue is not None:
            return [context_issue]
        fp = check_expression(expression)
        if fp.available and not fp.ok:
            return [ValidationIssue("FASTPLUS", fp.diagnostic)]
        try:
            root = parse_expression(expression)
        except ExpressionSyntaxError as exc:
            return [ValidationIssue("INVALID_SYNTAX", str(exc))]
        issues: list[ValidationIssue] = []
        arity_trusted = bool(self.metadata.info.get("operator_arity_trusted", True))

        def walk(node: AstNode, *, callee: bool = False) -> None:
            if node.kind == "call":
                operator = self.metadata.operators.get(node.value.lower())
                if operator is None:
                    issues.append(ValidationIssue("UNKNOWN_OPERATOR", node.value))
                elif arity_trusted and len(node.children) != operator.arity:
                    issues.append(
                        ValidationIssue(
                            "INVALID_ARITY",
                            f"{node.value} expects {operator.arity}, got {len(node.children)}",
                        )
                    )
                for child in node.children:
                    walk(child)
                return
            if node.kind == "ident" and not callee:
                field = self.metadata.fields.get(node.value)
                if field is None:
                    issues.append(ValidationIssue("UNKNOWN_FIELD", node.value))
                elif expected_dataset_id and field.dataset_id != expected_dataset_id:
                    issues.append(
                        ValidationIssue(
                            "FIELD_DATASET_MISMATCH",
                            f"{node.value} belongs to {field.dataset_id}, expected {expected_dataset_id}",
                        )
                    )
            for child in node.children:
                walk(child)

        walk(root)
        return issues

    def _context_issue(
        self,
        *,
        region: str | None,
        universe: str | None,
        delay: int | str | None,
    ) -> ValidationIssue | None:
        try:
            fetched_at = datetime.fromisoformat(
                str(self.metadata.info["fetched_at"]).replace("Z", "+00:00")
            )
            if fetched_at.tzinfo is None:
                return ValidationIssue("CATALOG_UNAVAILABLE", "catalog timestamp has no timezone")
            age_hours = (datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)).total_seconds() / 3600
        except (KeyError, TypeError, ValueError):
            return ValidationIssue("CATALOG_UNAVAILABLE", "catalog timestamp is unavailable")
        if age_hours > self.max_age_hours and not self.allow_stale_catalog:
            return ValidationIssue("CATALOG_STALE", f"catalog age {age_hours:.1f}h exceeds {self.max_age_hours:g}h")
        expected = {"region": region, "universe": universe, "delay": delay}
        for key, value in expected.items():
            if value is not None and str(self.metadata.info.get(key)) != str(value):
                return ValidationIssue("CATALOG_CONTEXT_MISMATCH", f"catalog {key} does not match simulation")
        return None
