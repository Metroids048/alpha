"""Robustness scoring over one-parameter-at-a-time trials."""

from __future__ import annotations


def robustness_score(trials: list[dict]) -> float:
    if not trials:
        return 0.0
    passed = sum(bool(trial.get("quality_buffer_pass")) for trial in trials) / len(
        trials
    )
    worst_margin = min(float(trial.get("normalized_margin", 0.0)) for trial in trials)
    return max(0.0, min(1.0, 0.5 * passed + 0.5 * max(0.0, worst_margin)))
