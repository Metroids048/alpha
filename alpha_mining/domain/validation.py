"""Independent preflight validation for consultant expressions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .expression_normalization import extract_fields, extract_functions
from .field_catalog import FieldCatalog
from .operator_registry import BLOCKED_FUNCTIONS, FUNCTIONS, GROUPS


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"


class PreflightValidator:
    def __init__(
        self, catalog: FieldCatalog | None = None, *, min_ts_corr_window: int = 10
    ) -> None:
        self.catalog = catalog
        self.min_ts_corr_window = max(1, int(min_ts_corr_window))

    def issues(self, expression: str) -> tuple[ValidationIssue, ...]:
        text = str(expression or "").strip().lower()
        out: list[ValidationIssue] = []
        if not text:
            return (ValidationIssue("EMPTY", "empty expression"),)
        if any(x in text for x in ("http://", "https://", "www.", ".com")):
            out.append(ValidationIssue("URL_TOKEN", "URL-like token"))
        blocked = sorted(set(extract_functions(text)) & BLOCKED_FUNCTIONS)
        if blocked or re.search(r"[<>=!]=|[<>]", text):
            out.append(
                ValidationIssue(
                    "SPARSE_CONDITIONAL", ",".join(blocked) or "conditional operator"
                )
            )
        unknown_ops = sorted(set(extract_functions(text)) - FUNCTIONS)
        if unknown_ops:
            out.append(ValidationIssue("UNKNOWN_OPERATOR", ",".join(unknown_ops)))
        for match in re.finditer(r"ts_corr\([^)]*,\s*(\d+)\s*\)", text):
            if int(match.group(1)) < self.min_ts_corr_window:
                out.append(ValidationIssue("SHORT_CORRELATION_WINDOW", match.group(1)))
        if self.catalog:
            unknown = [
                field
                for field in extract_fields(text)
                if field not in self.catalog.ids and field not in GROUPS
            ]
            if unknown:
                out.append(ValidationIssue("UNKNOWN_FIELD", ",".join(unknown[:8])))
        return tuple(out)

    def validate(self, expression: str) -> tuple[bool, str]:
        issues = self.issues(expression)
        return (
            not issues,
            "ok" if not issues else f"{issues[0].code.lower()}:{issues[0].message}",
        )
