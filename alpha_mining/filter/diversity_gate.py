"""Two-stage expression diversity gate for Research Memory candidates."""

from __future__ import annotations

import inspect
import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from alpha_mining.generator.hypothesis import decode_embedding, encode_embedding


class DiversityGateError(RuntimeError):
    """The diversity gate could not compare or persist an embedding safely."""


@dataclass(frozen=True)
class DiversityDecision:
    accepted: bool
    reason: str
    token_similarity: float = 0.0
    embedding_similarity: float = 0.0
    expression_id: str | None = None


def _default_normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip()).lower()


def _default_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z_]+|\d+(?:\.\d+)?", _default_normalize(text)))


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise DiversityGateError(
            f"embedding dimension mismatch: candidate={len(left)} history={len(right)}"
        )
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise DiversityGateError("embedding vector must be non-zero")
    value = sum(float(a) * float(b) for a, b in zip(left, right)) / (
        left_norm * right_norm
    )
    if not math.isfinite(value):
        raise DiversityGateError("embedding similarity is not finite")
    return value


def _validate_vector(values: Iterable[float]) -> tuple[float, ...]:
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise DiversityGateError("embedding vector is invalid") from exc
    if not vector or any(not math.isfinite(value) for value in vector):
        raise DiversityGateError("embedding vector is invalid")
    if all(value == 0.0 for value in vector):
        raise DiversityGateError("embedding vector must be non-zero")
    return vector


class DiversityGate:
    """Apply deterministic token and semantic similarity checks before simulation."""

    _POOL_NAMES = (
        "toxic",
        "weak_fail",
        "near_pass",
        "passed",
        "generated",
        "self_corr_risk",
    )
    _SUBMITTED = {"submitted", "passed", "pass"}

    def __init__(
        self,
        database: str | Path,
        embedder: Any,
        *,
        tokenizer: Callable[[str], Iterable[str]] | None = None,
        normalizer: Callable[[str], str] | None = None,
        structure_signature: Callable[[str], str] | None = None,
        token_similarity_threshold: float = 0.88,
        embedding_similarity_threshold: float = 0.90,
        history_limit: int = 100,
        history_pools: Any | None = None,
        history_similarity_pools: Any | None = None,
        novelty_index: Any | None = None,
    ) -> None:
        if not 0.0 <= token_similarity_threshold <= 1.0:
            raise ValueError("token_similarity_threshold must be between 0 and 1")
        if not 0.0 <= embedding_similarity_threshold <= 1.0:
            raise ValueError("embedding_similarity_threshold must be between 0 and 1")
        if history_limit < 0:
            raise ValueError("history_limit must be non-negative")
        self.database = Path(database).expanduser().resolve()
        self.embedder = embedder
        self.tokenizer = tokenizer or _default_tokens
        self.normalizer = normalizer or _default_normalize
        self.structure_signature = structure_signature
        self.token_similarity_threshold = float(token_similarity_threshold)
        self.embedding_similarity_threshold = float(embedding_similarity_threshold)
        self.history_limit = int(history_limit)
        if (
            history_pools is not None
            and history_similarity_pools is not None
            and history_pools is not history_similarity_pools
        ):
            raise ValueError("provide only one history pool object")
        self.history_pools = (
            history_pools if history_pools is not None else history_similarity_pools
        )
        self.novelty_index = novelty_index

    def _tokens(self, text: str) -> set[str]:
        try:
            result = self.tokenizer(text)
            return {str(token).lower() for token in result if str(token).strip()}
        except Exception as exc:
            raise DiversityGateError("tokenizer failed") from exc

    def _structure(self, text: str) -> str:
        if self.structure_signature is None:
            return ""
        try:
            value = self.structure_signature(text)
        except Exception as exc:
            raise DiversityGateError("structure signature failed") from exc
        return str(value).strip() if value is not None else ""

    def _require_database(self) -> None:
        if not self.database.is_file():
            raise DiversityGateError("database unavailable")

    def _read_connection(self) -> sqlite3.Connection:
        self._require_database()
        try:
            return sqlite3.connect(f"{self.database.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error:
            raise DiversityGateError("database unavailable") from None

    def _write_connection(self) -> sqlite3.Connection:
        self._require_database()
        try:
            return sqlite3.connect(f"{self.database.as_uri()}?mode=rw", uri=True)
        except sqlite3.Error:
            raise DiversityGateError("database unavailable") from None

    def _history_rows(self, expression_id: str | None = None) -> list[dict[str, Any]]:
        if self.history_limit == 0:
            return []
        connection: sqlite3.Connection | None = None
        try:
            connection = self._read_connection()
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT e.expression_id, e.expression_text, e.normalized_text,
                       e.structure_sig, e.embedding, e.submission_priority_score,
                       sr.status, sr.queue_status, sr.sharpe, sr.fitness
                FROM expressions AS e
                LEFT JOIN simulation_runs AS sr
                  ON sr.expression_id=e.expression_id
                  OR (sr.expression_id IS NULL AND sr.expression=e.expression_text)
                WHERE e.submission_priority_score IS NOT NULL
                   OR lower(COALESCE(sr.status, '')) IN ('submitted','passed','pass')
                   OR lower(COALESCE(sr.queue_status, '')) IN ('submitted','passed','pass')
                """
            ).fetchall()
        except DiversityGateError:
            raise
        except sqlite3.Error:
            raise DiversityGateError("database query failed") from None
        finally:
            if connection is not None:
                connection.close()
        # A single expression can have several runs. Keep the best row and sort
        # by explicit priority first, then available outcome metrics.
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            key = str(item["expression_id"])
            if expression_id is not None and key == str(expression_id):
                continue
            previous = unique.get(key)
            score = self._history_sort_key(item)
            if previous is None or score > self._history_sort_key(previous):
                unique[key] = item
        ordered = sorted(unique.values(), key=self._history_sort_key, reverse=True)
        return ordered[: self.history_limit]

    def _token_rows(self) -> list[dict[str, Any]]:
        """Read expression text for the cheap gate, including unsuccessful history."""
        connection: sqlite3.Connection | None = None
        try:
            connection = self._read_connection()
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT expression_id, expression_text, normalized_text, structure_sig
                FROM expressions
                """
            ).fetchall()
            return [dict(row) for row in rows]
        except DiversityGateError:
            raise
        except sqlite3.Error:
            raise DiversityGateError("database query failed") from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _history_sort_key(row: dict[str, Any]) -> tuple[float, float, float]:
        def number(value: Any) -> float:
            try:
                result = float(value)
                return result if math.isfinite(result) else -math.inf
            except (TypeError, ValueError):
                return -math.inf

        return (
            number(row.get("submission_priority_score")),
            number(row.get("sharpe")),
            number(row.get("fitness")),
        )

    @staticmethod
    def _call_hook(
        method: Callable[..., Any],
        variants: Sequence[tuple[tuple[Any, ...], dict[str, Any]]],
        error_message: str,
    ) -> Any:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            for args, kwargs in variants:
                try:
                    signature.bind(*args, **kwargs)
                except TypeError:
                    continue
                try:
                    return method(*args, **kwargs)
                except (AttributeError, TypeError, ValueError):
                    raise DiversityGateError(error_message) from None
            raise DiversityGateError(error_message)
        for args, kwargs in variants:
            try:
                return method(*args, **kwargs)
            except TypeError:
                continue
            except (AttributeError, ValueError):
                raise DiversityGateError(error_message) from None
        raise DiversityGateError(error_message)

    def _injected_token_similarity(self, expression_text: str) -> float:
        pools = self.history_pools
        method = getattr(pools, "max_similarity", None) if pools is not None else None
        if not callable(method):
            return 0.0
        best = 0.0
        for pool_name in self._POOL_NAMES:
            early_exit = self.token_similarity_threshold or None
            value = self._call_hook(
                method,
                (
                    ((expression_text, pool_name), {"early_exit_at": early_exit}),
                    ((expression_text, pool_name), {}),
                    ((expression_text,), {}),
                ),
                "history similarity hook failed",
            )
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise DiversityGateError("history similarity hook failed") from None
            if not math.isfinite(value):
                raise DiversityGateError("history similarity hook failed")
            best = max(best, value)
        behavior = getattr(pools, "max_behavior_similarity", None)
        if callable(behavior):
            for pool_name in self._POOL_NAMES:
                value = self._call_hook(
                    behavior,
                    (
                        ((expression_text, pool_name), {"early_exit_at": early_exit}),
                        ((expression_text, pool_name), {}),
                        ((expression_text,), {}),
                    ),
                    "history similarity hook failed",
                )
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise DiversityGateError("history similarity hook failed") from None
                if not math.isfinite(value):
                    raise DiversityGateError("history similarity hook failed")
                best = max(best, value)
        return best

    def _novelty_rejection(self, expression_text: str) -> str | None:
        method = (
            getattr(self.novelty_index, "reject_reason", None)
            if self.novelty_index is not None
            else None
        )
        if not callable(method):
            return None
        reason = self._call_hook(
            method,
            (
                ((expression_text,), {"strictness": "balanced"}),
                ((expression_text,), {}),
            ),
            "novelty hook failed",
        )
        return str(reason) if reason else None

    def _embedding(self, text: str) -> tuple[float, ...]:
        try:
            return _validate_vector(self.embedder.embed(text))
        except DiversityGateError:
            raise
        except Exception as exc:
            raise DiversityGateError("embedding vector is invalid") from exc

    def _backfill_history(
        self, rows: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], tuple[float, ...]]]:
        resolved: list[tuple[dict[str, Any], tuple[float, ...]]] = []
        pending: list[tuple[str, bytes]] = []
        for row in rows:
            blob = row.get("embedding")
            if blob is None:
                vector = self._embedding(str(row["expression_text"]))
                pending.append((str(row["expression_id"]), encode_embedding(vector)))
                row["embedding"] = pending[-1][1]
            else:
                try:
                    vector = _validate_vector(decode_embedding(bytes(blob)))
                except (ValueError, DiversityGateError) as exc:
                    raise DiversityGateError("historical embedding is invalid") from exc
            resolved.append((row, vector))
        if pending:
            connection: sqlite3.Connection | None = None
            try:
                connection = self._write_connection()
                connection.executemany(
                    "UPDATE expressions SET embedding=? WHERE expression_id=?",
                    [(blob, expression_id) for expression_id, blob in pending],
                )
                connection.commit()
            except sqlite3.Error:
                raise DiversityGateError("database write failed") from None
            finally:
                if connection is not None:
                    connection.close()
        return resolved

    def check(
        self, expression_text: str, *, expression_id: str | None = None
    ) -> DiversityDecision:
        self._require_database()
        text = str(expression_text)
        candidate_normalized = str(self.normalizer(text)).strip().lower()
        candidate_tokens = self._tokens(text)

        novelty_reason = self._novelty_rejection(text)
        if novelty_reason:
            return DiversityDecision(False, novelty_reason, expression_id=expression_id)

        token_similarity = self._injected_token_similarity(text)
        rows = self._history_rows(expression_id)
        token_rows = self._token_rows()
        candidate_structure: str | None = None
        for row in token_rows:
            if expression_id is not None and str(row.get("expression_id")) == str(
                expression_id
            ):
                continue
            if (
                candidate_normalized
                and candidate_normalized
                == str(row.get("normalized_text") or "").strip().lower()
            ):
                return DiversityDecision(
                    False, "exact", 1.0, expression_id=expression_id
                )
            if self.structure_signature is not None:
                if candidate_structure is None:
                    candidate_structure = self._structure(text)
                row_structure = self._structure(str(row["expression_text"]))
                if candidate_structure and row_structure == candidate_structure:
                    token_similarity = max(token_similarity, 1.0)
                    return DiversityDecision(
                        False,
                        "structure",
                        token_similarity,
                        expression_id=expression_id,
                    )
            token_similarity = max(
                token_similarity,
                _jaccard(candidate_tokens, self._tokens(str(row["expression_text"]))),
            )
        # A zero threshold is useful for callers that intentionally disable
        # token screening; zero similarity is not evidence of duplication.
        if (
            self.token_similarity_threshold > 0.0
            and token_similarity >= self.token_similarity_threshold
        ):
            return DiversityDecision(
                False, "token_similarity", token_similarity, expression_id=expression_id
            )

        if not rows:
            return DiversityDecision(
                True, "no_reference_history", token_similarity, 0.0, expression_id
            )

        try:
            candidate = self._embedding(text)
        except DiversityGateError:
            return DiversityDecision(
                False,
                "embedding_invalid",
                token_similarity,
                expression_id=expression_id,
            )
        try:
            resolved = self._backfill_history(rows)
            embedding_similarity = max(
                (_cosine(candidate, vector) for _, vector in resolved), default=0.0
            )
        except DiversityGateError:
            raise
        if (
            self.embedding_similarity_threshold > 0.0
            and embedding_similarity >= self.embedding_similarity_threshold
        ):
            return DiversityDecision(
                False,
                "embedding_similarity",
                token_similarity,
                embedding_similarity,
                expression_id,
            )
        return DiversityDecision(
            True, "accepted", token_similarity, embedding_similarity, expression_id
        )

    def record_embedding(
        self,
        expression_id: str,
        embedding: Sequence[float] | bytes | bytearray | memoryview | str | None = None,
        *,
        expression_text: str | None = None,
        novelty_score: float | None = None,
    ) -> None:
        self._require_database()
        if isinstance(embedding, str):
            if expression_text is not None:
                raise DiversityGateError("embedding text is ambiguous")
            expression_text, embedding = embedding, None
        if embedding is None:
            read_connection: sqlite3.Connection | None = None
            try:
                read_connection = self._read_connection()
                row = read_connection.execute(
                    "SELECT expression_text FROM expressions WHERE expression_id=?",
                    (expression_id,),
                ).fetchone()
            except DiversityGateError:
                raise
            except sqlite3.Error:
                raise DiversityGateError("database query failed") from None
            finally:
                if read_connection is not None:
                    read_connection.close()
            if row is None and expression_text is None:
                raise DiversityGateError(f"expression does not exist: {expression_id}")
            expression_text = expression_text or str(row[0])
            vector = self._embedding(expression_text)
        elif isinstance(embedding, (bytes, bytearray, memoryview)):
            try:
                vector = _validate_vector(decode_embedding(bytes(embedding)))
            except (ValueError, DiversityGateError) as exc:
                raise DiversityGateError("embedding vector is invalid") from exc
        else:
            try:
                vector = _validate_vector(embedding)
            except DiversityGateError:
                raise
        blob = encode_embedding(vector)
        write_connection: sqlite3.Connection | None = None
        try:
            write_connection = self._write_connection()
            if novelty_score is None:
                cursor = write_connection.execute(
                    "UPDATE expressions SET embedding=? WHERE expression_id=?",
                    (blob, expression_id),
                )
            else:
                try:
                    novelty_value = float(novelty_score)
                except (TypeError, ValueError) as exc:
                    raise DiversityGateError("novelty_score is invalid") from exc
                if not math.isfinite(novelty_value):
                    raise DiversityGateError("novelty_score is invalid")
                cursor = write_connection.execute(
                    "UPDATE expressions SET embedding=?, novelty_score=? WHERE expression_id=?",
                    (blob, novelty_value, expression_id),
                )
            if cursor.rowcount != 1:
                raise DiversityGateError(f"expression does not exist: {expression_id}")
            write_connection.commit()
        except DiversityGateError:
            raise
        except sqlite3.Error:
            raise DiversityGateError("database write failed") from None
        finally:
            if write_connection is not None:
                write_connection.close()


__all__ = ["DiversityDecision", "DiversityGate", "DiversityGateError"]
