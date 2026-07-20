from __future__ import annotations

import math
import inspect
import sqlite3
from pathlib import Path

import pytest

from alpha_mining.generator.hypothesis import encode_embedding
from alpha_mining.storage.sqlite_store import SqliteRunLog
import alpha_mining.filter.diversity_gate as diversity_gate_module
from alpha_mining.filter.diversity_gate import (
    DiversityDecision,
    DiversityGate,
    DiversityGateError,
)


class FakeEmbedder:
    def __init__(self, values: dict[str, tuple[float, ...]]) -> None:
        self.values = values
        self.calls: list[str] = []

    def embed(self, text: str):
        self.calls.append(text)
        return self.values[text]


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "memory.sqlite"
    SqliteRunLog(path).initialize_schema()
    return path


def _expression(
    path: Path,
    expression_id: str,
    text: str,
    *,
    embedding=None,
    priority=None,
    structure="raw::-",
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """INSERT INTO expressions (
                expression_id, expression_text, normalized_text, structure_sig,
                generation_strategy, generation_layer, embedding, created_at,
                submission_priority_score, novelty_score
            ) VALUES (?, ?, ?, ?, 'test', 'L4', ?, '2026-01-01', ?, NULL)""",
            (expression_id, text, text, structure, embedding, priority),
        )
        connection.commit()
    finally:
        connection.close()


def test_token_rejection_skips_embedder(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(path, "old", "rank(ts_rank(close, 20))")
    embedder = FakeEmbedder({"rank(ts_rank(close, 20))": (1.0, 0.0)})
    gate = DiversityGate(path, embedder, token_similarity_threshold=0.88)

    decision = gate.check("rank(ts_rank(close, 20))")

    assert decision.accepted is False
    assert decision.reason in {"exact", "token_similarity", "structure"}
    assert decision.token_similarity >= 0.88
    assert embedder.calls == []


def test_low_token_then_embedding_reject(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(
        path,
        "old",
        "ts_rank(close, 20)",
        embedding=encode_embedding((1.0, 0.0)),
        priority=1.0,
    )
    embedder = FakeEmbedder({"rank(open, 20)": (1.0, 0.0)})
    gate = DiversityGate(path, embedder)

    decision = gate.check("rank(open, 20)")

    assert decision.accepted is False
    assert decision.reason == "embedding_similarity"
    assert decision.embedding_similarity == pytest.approx(1.0)
    assert embedder.calls == ["rank(open, 20)"]


def test_history_null_embedding_lazy_backfill(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(path, "old", "ts_rank(close, 20)", priority=3.0)
    embedder = FakeEmbedder(
        {"ts_rank(close, 20)": (0.0, 1.0), "rank(open, 20)": (1.0, 0.0)}
    )
    gate = DiversityGate(path, embedder, token_similarity_threshold=0.99)

    decision = gate.check("rank(open, 20)")

    assert decision.accepted is True
    with sqlite3.connect(path) as connection:
        blob = connection.execute(
            "SELECT embedding FROM expressions WHERE expression_id='old'"
        ).fetchone()[0]
    assert blob is not None
    assert embedder.calls == ["rank(open, 20)", "ts_rank(close, 20)"]


def test_history_filter_excludes_weak_rows(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(path, "weak", "old_weak", embedding=encode_embedding((1.0, 0.0)))
    _expression(
        path, "submitted", "old_submitted", embedding=encode_embedding((0.0, 1.0))
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO simulation_runs (expression_id, expression, status) VALUES (?, ?, ?)",
            ("submitted", "old_submitted", "submitted"),
        )
    embedder = FakeEmbedder({"new_expr": (1.0, 0.0)})
    gate = DiversityGate(path, embedder, token_similarity_threshold=0.01)

    decision = gate.check("new_expr")

    assert decision.accepted is True
    assert decision.embedding_similarity == pytest.approx(0.0)


def test_record_embedding_persists_existing_candidate(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(path, "candidate", "new_expr")
    gate = DiversityGate(path, FakeEmbedder({"new_expr": (1.0, 0.0)}))

    gate.record_embedding("candidate", (1.0, 0.0), novelty_score=0.77)

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT embedding, novelty_score FROM expressions WHERE expression_id='candidate'"
        ).fetchone()
    assert row[0] == encode_embedding((1.0, 0.0))
    assert row[1] == pytest.approx(0.77)


def test_dimension_mismatch_and_invalid_vector(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(
        path, "old", "old_expr", embedding=encode_embedding((1.0, 0.0)), priority=1.0
    )
    gate = DiversityGate(
        path,
        FakeEmbedder({"new_expr": (1.0, 0.0, 0.0)}),
        token_similarity_threshold=0.01,
    )
    with pytest.raises(DiversityGateError, match="dimension"):
        gate.check("new_expr")

    invalid = DiversityGate(
        path,
        FakeEmbedder({"invalid": (math.nan, 0.0)}),
        token_similarity_threshold=0.01,
    )
    decision = invalid.check("invalid")
    assert decision.accepted is False
    assert decision.reason == "embedding_invalid"


def test_injected_novelty_index_rejection(tmp_path: Path) -> None:
    class Novelty:
        def reject_reason(self, expression: str, **kwargs):
            return "novelty_same_structure"

    gate = DiversityGate(
        _db(tmp_path), FakeEmbedder({"x": (1.0,)}), novelty_index=Novelty()
    )
    decision = gate.check("x")
    assert decision.accepted is False
    assert decision.reason == "novelty_same_structure"


def test_empty_reference_history_accepts_without_calling_embedder(
    tmp_path: Path,
) -> None:
    class FailingEmbedder:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def embed(self, text: str):
            self.calls.append(text)
            raise AssertionError(
                "embedder must not run without eligible reference history"
            )

    embedder = FailingEmbedder()
    gate = DiversityGate(_db(tmp_path), embedder)
    decision = gate.check("new")
    assert decision.accepted is True
    assert decision.reason == "no_reference_history"
    assert decision.embedding_similarity == 0.0
    assert isinstance(decision, DiversityDecision)
    assert embedder.calls == []


def test_explicit_structure_signature_can_reject_canonical_shape(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    canonical = "ts_rank>group_rank::close|sector::operator_skeleton"
    _expression(path, "old", "historical_unrelated", structure=canonical)
    embedder = FakeEmbedder({"candidate_unrelated": (1.0, 0.0)})

    decision = DiversityGate(
        path,
        embedder,
        structure_signature=lambda expression: canonical,
    ).check("candidate_unrelated")

    assert decision.accepted is False
    assert decision.reason == "structure"
    assert embedder.calls == []


def test_module_does_not_reference_monolith_or_importlib() -> None:
    source = Path(diversity_gate_module.__file__).read_text(encoding="utf-8")
    assert "auto_alpha" not in source
    assert "importlib" not in source


def test_injected_history_pool_real_signature_rejects_before_embedding(
    tmp_path: Path,
) -> None:
    class Pools:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, float | None]] = []

        def max_similarity(
            self, expr: str, pool_name: str, *, early_exit_at=None
        ) -> float:
            self.calls.append((expr, pool_name, early_exit_at))
            return 0.91 if pool_name == "passed" else 0.0

    pools = Pools()
    embedder = FakeEmbedder({"candidate": (1.0, 0.0)})
    gate = DiversityGate(_db(tmp_path), embedder, history_pools=pools)

    decision = gate.check("candidate")

    assert decision.reason == "token_similarity"
    assert decision.token_similarity == pytest.approx(0.91)
    assert any(pool_name == "passed" for _, pool_name, _ in pools.calls)
    assert embedder.calls == []


def test_history_limit_prefers_highest_priority_reference(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(
        path, "low", "low_history", embedding=encode_embedding((1.0, 0.0)), priority=1.0
    )
    _expression(
        path,
        "high",
        "high_history",
        embedding=encode_embedding((0.0, 1.0)),
        priority=10.0,
    )
    embedder = FakeEmbedder({"candidate": (1.0, 0.0)})

    decision = DiversityGate(path, embedder, history_limit=1).check("candidate")

    assert decision.accepted is True
    assert decision.embedding_similarity == pytest.approx(0.0)
    assert embedder.calls == ["candidate"]


def test_corrupt_historical_embedding_raises_clear_error(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(path, "old", "old_history", embedding=b"bad", priority=1.0)
    gate = DiversityGate(path, FakeEmbedder({"candidate": (1.0, 0.0)}))

    with pytest.raises(DiversityGateError, match="historical embedding is invalid"):
        gate.check("candidate")


def test_missing_database_fails_closed_without_creating_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite"
    gate = DiversityGate(path, FakeEmbedder({"candidate": (1.0, 0.0)}))

    with pytest.raises(DiversityGateError, match="database unavailable"):
        gate.check("candidate")

    assert not path.exists()


def test_missing_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "empty.sqlite"
    with sqlite3.connect(path):
        pass

    with pytest.raises(DiversityGateError, match="database query failed"):
        DiversityGate(path, FakeEmbedder({"candidate": (1.0, 0.0)})).check("candidate")


def test_expression_id_is_excluded_from_embedding_history(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(
        path,
        "candidate",
        "candidate",
        embedding=encode_embedding((1.0, 0.0)),
        priority=1.0,
    )
    embedder = FakeEmbedder({"candidate": (1.0, 0.0)})

    decision = DiversityGate(path, embedder).check(
        "candidate", expression_id="candidate"
    )

    assert decision.accepted is True
    assert decision.reason == "no_reference_history"
    assert embedder.calls == []


def test_run_with_expression_id_does_not_join_other_same_text_expression(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _expression(path, "bound", "shared_history", embedding=encode_embedding((0.0, 1.0)))
    _expression(path, "other", "shared_history", embedding=encode_embedding((1.0, 0.0)))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO simulation_runs (expression_id, expression, status) VALUES (?, ?, ?)",
            ("bound", "shared_history", "submitted"),
        )

    decision = DiversityGate(
        path,
        FakeEmbedder({"candidate": (1.0, 0.0)}),
    ).check("candidate")

    assert decision.accepted is True
    assert decision.embedding_similarity == pytest.approx(0.0)


def test_history_pool_hook_failure_is_not_silently_ignored(tmp_path: Path) -> None:
    class BrokenPools:
        def max_similarity(
            self, expr: str, pool_name: str, *, early_exit_at=None
        ) -> float:
            raise ValueError("sensitive internal detail")

    gate = DiversityGate(_db(tmp_path), FakeEmbedder({}), history_pools=BrokenPools())

    with pytest.raises(
        DiversityGateError, match="history similarity hook failed"
    ) as caught:
        gate.check("candidate")
    assert "sensitive internal detail" not in str(caught.value)


def test_novelty_hook_failure_is_not_silently_ignored(tmp_path: Path) -> None:
    class BrokenNovelty:
        def reject_reason(self, expr: str, *, strictness: str) -> str | None:
            raise AttributeError("sensitive internal detail")

    gate = DiversityGate(_db(tmp_path), FakeEmbedder({}), novelty_index=BrokenNovelty())

    with pytest.raises(DiversityGateError, match="novelty hook failed") as caught:
        gate.check("candidate")
    assert "sensitive internal detail" not in str(caught.value)


def test_zero_thresholds_disable_similarity_rejection(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _expression(
        path, "old", "rank(close)", embedding=encode_embedding((1.0, 0.0)), priority=1.0
    )
    embedder = FakeEmbedder({"rank(open)": (1.0, 0.0)})

    decision = DiversityGate(
        path,
        embedder,
        token_similarity_threshold=0.0,
        embedding_similarity_threshold=0.0,
    ).check("rank(open)")

    assert decision.accepted is True
    assert decision.token_similarity > 0.0
    assert decision.embedding_similarity == pytest.approx(1.0)


def test_record_embedding_supports_bytes_like_and_rejects_ambiguous_text(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _expression(path, "candidate", "candidate")
    gate = DiversityGate(path, FakeEmbedder({"candidate": (1.0, 0.0)}))
    blob = encode_embedding((1.0, 0.0))

    gate.record_embedding("candidate", memoryview(blob))
    annotation = (
        inspect.signature(DiversityGate.record_embedding)
        .parameters["embedding"]
        .annotation
    )
    assert all(name in str(annotation) for name in ("bytes", "bytearray", "memoryview"))

    with pytest.raises(DiversityGateError, match="ambiguous"):
        gate.record_embedding("candidate", "candidate", expression_text="candidate")


def test_history_backfill_does_not_recreate_database_deleted_before_write(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _expression(path, "old", "old_history", priority=1.0)

    class DeletingVector:
        def __iter__(self):
            path.unlink()
            return iter((0.0, 1.0))

    class DeletingEmbedder:
        def embed(self, text: str):
            if text == "old_history":
                return DeletingVector()
            return (1.0, 0.0)

    with pytest.raises(DiversityGateError, match="database (unavailable|write failed)"):
        DiversityGate(path, DeletingEmbedder()).check("candidate")

    assert not path.exists()


def test_record_embedding_does_not_recreate_database_deleted_before_write(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    _expression(path, "candidate", "candidate")

    class DeletingVector:
        def __iter__(self):
            path.unlink()
            return iter((1.0, 0.0))

    class DeletingEmbedder:
        def embed(self, text: str):
            return DeletingVector()

    gate = DiversityGate(path, DeletingEmbedder())
    with pytest.raises(DiversityGateError, match="database (unavailable|write failed)"):
        gate.record_embedding("candidate", expression_text="candidate")

    assert not path.exists()
