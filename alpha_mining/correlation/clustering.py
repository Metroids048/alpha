"""Correlation-matrix medoid utilities."""

from __future__ import annotations

from typing import Mapping


def correlation_medoid(
    ids: list[str], correlations: Mapping[tuple[str, str], float]
) -> str:
    if not ids:
        raise ValueError("ids must not be empty")
    return min(
        sorted(ids),
        key=lambda candidate: (
            sum(
                1.0
                - abs(
                    correlations.get(
                        (candidate, other), correlations.get((other, candidate), 0.0)
                    )
                )
                for other in ids
                if other != candidate
            ),
            candidate,
        ),
    )
