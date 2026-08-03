"""FastPlus (py-fastplus) expression gate for WorldQuant FASTEXPR preflight.

Runs before platform simulation / candidate persistence. When the package is
missing, callers may fall back to heuristic validators without aborting the
process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FastPlusGateResult:
    ok: bool
    diagnostic: str
    available: bool = True
    fields: dict[str, list[str]] | None = None
    operators: tuple[str, ...] | None = None

    @property
    def reason(self) -> str:
        """Single-line reason suitable for existing validate() return codes."""
        if self.ok:
            return "ok" if self.available else "fastplus_unavailable"
        text = " ".join(str(self.diagnostic or "").split())
        if len(text) > 400:
            text = text[:397] + "..."
        return f"fastplus:{text}"


def _collapse_diagnostic(exc: BaseException) -> str:
    return " ".join(str(exc).split())


def check_expression(expression: str) -> FastPlusGateResult:
    """Parse and type-check a RegularAlpha expression via FastPlus.

    Returns ok=True with available=False when py-fastplus is not installed,
    so callers can soft-fallback.
    """
    text = str(expression or "").strip()
    if not text:
        return FastPlusGateResult(ok=False, diagnostic="empty expression", available=True)

    try:
        import fastplus
    except ImportError:
        return FastPlusGateResult(
            ok=True,
            diagnostic="fastplus_unavailable",
            available=False,
        )

    try:
        alpha = fastplus.parse(text)
    except ValueError as exc:
        return FastPlusGateResult(
            ok=False,
            diagnostic=_collapse_diagnostic(exc),
            available=True,
        )
    except Exception as exc:  # pragma: no cover - unexpected native errors
        return FastPlusGateResult(
            ok=False,
            diagnostic=f"unexpected:{_collapse_diagnostic(exc)}",
            available=True,
        )

    fields_raw: Any = getattr(alpha, "fields", None) or {}
    fields: dict[str, list[str]] = {}
    if isinstance(fields_raw, dict):
        for key, values in fields_raw.items():
            fields[str(key)] = [str(v) for v in (values or [])]

    ops_raw: Any = getattr(alpha, "operators", None) or []
    operators = tuple(str(op) for op in ops_raw)

    return FastPlusGateResult(
        ok=True,
        diagnostic="ok",
        available=True,
        fields=fields,
        operators=operators,
    )


def require_fastplus(expression: str) -> FastPlusGateResult:
    """Like check_expression, but treat missing package as hard failure."""
    result = check_expression(expression)
    if not result.available:
        return FastPlusGateResult(
            ok=False,
            diagnostic="py-fastplus is not installed (pip install py-fastplus)",
            available=False,
        )
    return result
