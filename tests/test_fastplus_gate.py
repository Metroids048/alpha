"""Regression tests for FastPlus expression preflight gate."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpha_mining.parser.fastplus_gate import check_expression, require_fastplus


pytest.importorskip("fastplus")


def test_fastplus_accepts_matrix_signal() -> None:
    result = check_expression("group_rank(ts_delay(close, 5), industry)")
    assert result.available is True
    assert result.ok is True
    assert result.operators is not None
    assert "ts_delay" in result.operators
    assert "group_rank" in result.operators
    assert result.fields is not None
    assert "close" in result.fields.get("matrix", [])
    assert "industry" in result.fields.get("group", [])


def test_fastplus_rejects_vector_type_mismatch() -> None:
    result = check_expression("vec_avg(rank(x))")
    assert result.available is True
    assert result.ok is False
    assert "Vector" in result.diagnostic or "vector" in result.diagnostic.lower()
    assert result.reason.startswith("fastplus:")


def test_fastplus_rejects_unbalanced_paren() -> None:
    result = check_expression("rank(close")
    assert result.ok is False
    assert result.reason.startswith("fastplus:")


def test_fastplus_rejects_group_as_final_signal() -> None:
    result = check_expression("bucket(rank(x), range='0, 1, 0.1')")
    assert result.ok is False
    assert "Matrix" in result.diagnostic or "Group" in result.diagnostic


def test_domain_preflight_surfaces_fastplus() -> None:
    from alpha_mining.domain.validation import PreflightValidator

    validator = PreflightValidator()
    ok, reason = validator.validate("vec_avg(rank(close))")
    assert ok is False
    assert "fastplus" in reason.lower() or "Vector" in reason or "vector" in reason.lower()


def test_v50_preflight_surfaces_fastplus() -> None:
    mod = importlib.import_module("auto_alpha_pipeline_rebuilt_v50")
    validator = mod.PreflightValidator()
    ok, reason = validator.validate("vec_avg(rank(close))")
    assert ok is False
    assert reason.startswith("fastplus:")


def test_require_fastplus_hard_fail_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "fastplus" or name.startswith("fastplus."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    soft = check_expression("rank(close)")
    assert soft.available is False
    assert soft.ok is True
    hard = require_fastplus("rank(close)")
    assert hard.available is False
    assert hard.ok is False
