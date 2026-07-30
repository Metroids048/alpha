"""Local FASTEXPR validation against synchronized metadata."""

from __future__ import annotations

from dataclasses import dataclass

from alpha_mining.domain.expression_ast import AstNode, ExpressionSyntaxError, parse_expression
from alpha_mining.offline.metadata import MetadataCache


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


class LocalExpressionValidator:
    def __init__(self, metadata: MetadataCache) -> None:
        self.metadata = metadata

    def validate(self, expression: str) -> list[ValidationIssue]:
        try:
            root = parse_expression(expression)
        except ExpressionSyntaxError as exc:
            return [ValidationIssue("INVALID_SYNTAX", str(exc))]
        issues: list[ValidationIssue] = []

        def walk(node: AstNode, *, callee: bool = False) -> None:
            if node.kind == "call":
                operator = self.metadata.operators.get(node.value)
                if operator is None:
                    issues.append(ValidationIssue("UNKNOWN_OPERATOR", node.value))
                elif len(node.children) != operator.arity:
                    issues.append(
                        ValidationIssue(
                            "INVALID_ARITY",
                            f"{node.value} expects {operator.arity}, got {len(node.children)}",
                        )
                    )
                for child in node.children:
                    walk(child)
                return
            if node.kind == "ident" and not callee and node.value not in self.metadata.fields:
                issues.append(ValidationIssue("UNKNOWN_FIELD", node.value))
            for child in node.children:
                walk(child)

        walk(root)
        return issues
