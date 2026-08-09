"""The platform type checker must be armed in the interpreter that generates.

``requirements.txt`` declares ``py-fastplus>=0.3.1; python_version >= "3.12"``.
When that dependency is missing, ``check_expression`` returns
``ok=True, available=False`` by design, and ``LocalExpressionValidator`` gates
the platform type check on ``if fp.available and not fp.ok``. The two together
are fail-open: in an interpreter without the package the platform's own type
checker becomes a silent no-op and ``FASTPLUS`` can never be raised.

Measured: the project ``.venv`` had no ``py-fastplus`` while running batch
generation, so ``group_neutralize(<matrix>, rank(<matrix>))`` -- rejected by the
real platform as ``InvalidArgumentType { expected: Group, actual: Matrix }`` --
was validated as legal and enqueued.

``tests/test_fastplus_gate.py`` cannot catch this: its module-scope
``pytest.importorskip("fastplus")`` makes the whole file vanish in exactly the
interpreter where the defect is present. This file must therefore never skip on
a missing package -- that absence IS the failure under test.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from alpha_mining.generation.validation import LocalExpressionValidator
from alpha_mining.offline.metadata import MetadataCache
from alpha_mining.parser.fastplus_gate import check_expression, require_fastplus

# arg 2 of a group_* call must be a grouping axis; a Matrix expression is the
# shape the live platform refused.
_GROUP_SLOT_MATRIX = "group_neutralize(rank(close), rank(volume))"
_GROUP_SLOT_LEGAL = "group_neutralize(rank(close), subindustry)"

_REQUIREMENTS_MARKER_PY = (3, 12)


def _empty_catalog() -> MetadataCache:
    """The FASTPLUS check returns before any field lookup, so this stays bare.

    ``fetched_at`` is required even so: ``_context_issue`` runs first and
    returns CATALOG_UNAVAILABLE without it, which would mask the very check
    under test.
    """

    return MetadataCache(
        cache_dir=Path("."),
        operators={},
        fields={},
        datasets={},
        info={
            "region": "USA",
            "universe": "TOP3000",
            "source": "test",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    )


@pytest.mark.skipif(
    sys.version_info < _REQUIREMENTS_MARKER_PY,
    reason="requirements.txt only declares py-fastplus for python_version >= 3.12",
)
def test_fastplus_is_installed_as_requirements_declares() -> None:
    """A declared dependency that silently disarms an acceptance gate is a blocker."""

    result = check_expression(_GROUP_SLOT_LEGAL)

    assert result.available is True, (
        "py-fastplus is not importable in this interpreter, so the platform type "
        "check is a silent no-op and FASTPLUS can never be raised. "
        f"requirements.txt declares py-fastplus>=0.3.1 for python_version >= 3.12 "
        f"and this interpreter is {sys.version_info[0]}.{sys.version_info[1]}"
        f".{sys.version_info[2]} ({sys.executable}). "
        "Install the declared dependency; do not relax this assertion."
    )


@pytest.mark.skipif(
    sys.version_info < _REQUIREMENTS_MARKER_PY,
    reason="requirements.txt only declares py-fastplus for python_version >= 3.12",
)
def test_armed_gate_rejects_a_matrix_in_the_group_slot_end_to_end() -> None:
    """Proof the arming reaches production screening, not just the gate module.

    This is the exact fault that survived local validation and reached the
    queue while the checker was disarmed.
    """

    validator = LocalExpressionValidator(_empty_catalog(), allow_stale_catalog=True)

    issues = validator.validate(_GROUP_SLOT_MATRIX)

    assert [issue.code for issue in issues][:1] == ["FASTPLUS"], (
        "the platform type checker did not reject a Matrix in the grouping slot; "
        f"issues={[(i.code, i.message) for i in issues]}"
    )
    assert "Group" in issues[0].message or "Matrix" in issues[0].message


def test_require_fastplus_is_the_fail_closed_form() -> None:
    """The fail-open default is deliberate; the fail-closed helper already exists.

    Kept here so the distinction stays visible next to the arming assertion:
    ``check_expression`` tolerates absence, ``require_fastplus`` does not.
    """

    hard = require_fastplus(_GROUP_SLOT_LEGAL)

    if check_expression(_GROUP_SLOT_LEGAL).available:
        assert hard.ok is True
    else:
        assert hard.ok is False
        assert "not installed" in hard.diagnostic
