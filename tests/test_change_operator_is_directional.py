"""A dispersion magnitude must not be offered to the model as a change operator.

The research prompt tells the model "Change operators available here: {...}.
Include at least one." and that list was
``{ts_delta, ts_std_dev, ts_corr, ts_returns, subtract}``.

``ts_std_dev`` is not a change. It is a non-negative dispersion magnitude: it
answers "how much did this move" and discards "which way". A signal whose only
change operator is ``ts_std_dev`` therefore ranks stocks by volatility, and
``rank`` / ``ts_zscore`` on top of it preserve that ordering rather than
repairing it.

Measured on the real platform at frozen HEAD 164831f, three candidates whose
signal core was ts_std_dev:

  09e0656633e48fb0  group_rank(ts_delta(f1,21),industry)
                    - group_rank(ts_std_dev(f2,21),industry)   sharpe  0.03  fitness  0.00
  e7b26c4dbdcc5ec4  group_neutralize(rank(ts_zscore(
                    ts_std_dev(f,60),60)),sector)              sharpe  0.25  fitness  0.10
  75f9ff9af8bdabae  group_neutralize(ts_zscore(ts_std_dev(f,126)
                    - ts_delta(g,21),126),industry)            sharpe -0.88  fitness -0.36

Turnover was 0.067-0.127 and returns were non-zero in all three, so these were
live signals rather than dead expressions -- they were ordered by the wrong
quantity. The last one subtracts a signed change from a magnitude, which is the
shape this misclassification most directly invites.

ts_corr keeps its place: it is a relationship measure and it is signed, so it
can carry direction. The rule under test is specifically that a *non-negative*
transform must not be presented as a source of direction.
"""

from __future__ import annotations

import json
import re

from alpha_mining.generation import high_quality
from alpha_mining.generation.high_quality import HighQualityGenerator

# Non-negative by construction: |x| has no sign to give a signal.
_MAGNITUDE_ONLY = ("ts_std_dev", "ts_variance", "ts_stddev", "abs", "vec_stddev")

_DIRECTIONAL = ("ts_delta", "ts_returns", "subtract")


def _change_line(prompt: str) -> str:
    for line in re.split(r"[\n\"]", prompt):
        if "Change operators available here" in line:
            return line
    return ""


def test_change_operator_list_offers_only_signed_transforms(monkeypatch) -> None:
    """The literal set at the source of the prompt line must exclude magnitudes."""

    source = high_quality.__dict__["HighQualityGenerator"]
    del source  # touched only to assert the module imported

    import inspect

    text = inspect.getsource(HighQualityGenerator._research_prompt)
    match = re.search(r"_change_ops\s*=\s*sorted\(\s*_usable\s*&\s*\{([^}]*)\}", text)
    assert match, "could not locate the _change_ops literal; update this test with the source"
    listed = {item.strip().strip("\"'") for item in match.group(1).split(",") if item.strip()}

    offending = sorted(listed & set(_MAGNITUDE_ONLY))
    assert not offending, (
        f"{offending} appear in the change-operator list the research prompt shows the "
        "model, but they are non-negative magnitudes and carry no direction. The prompt "
        "says 'Include at least one', so the model can satisfy its directional "
        "requirement with a volatility measure and the resulting alpha ranks by "
        "volatility instead of expected return. Real platform results for that shape: "
        "sharpe 0.03 / 0.25 / -0.88 against a 1.58 limit."
    )
    assert listed & set(_DIRECTIONAL), (
        "the change-operator list must still offer at least one signed transform"
    )


def test_structural_score_does_not_credit_a_magnitude_as_change() -> None:
    """The local score must not reward the same confusion the prompt made.

    ``_structural_depth_component`` gave its change-operator credit for
    ``ts_std_dev``, so a volatility-ranked candidate scored as though it
    expressed a directional relationship. e7b26c4dbdcc5ec4 scored 73.75 locally
    and returned sharpe 0.25 on the platform.
    """

    fields = ("alpha_field_one", "alpha_field_two")
    magnitude_only = "group_neutralize(rank(ts_zscore(ts_std_dev(alpha_field_one,60),60)),sector)"
    directional = "group_neutralize(rank(ts_zscore(ts_delta(alpha_field_one,21),60)),sector)"

    magnitude_score = high_quality._structural_depth_component(magnitude_only, fields)
    directional_score = high_quality._structural_depth_component(directional, fields)

    assert directional_score > magnitude_score, (
        "a signed change must score above a pure dispersion magnitude; "
        f"directional={directional_score} magnitude_only={magnitude_score}"
    )
