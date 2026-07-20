"""Submission Judge — scores passed candidates for prioritized submission."""

from __future__ import annotations

import math
import re
import sqlite3
import struct
from dataclasses import dataclass

from alpha_mining.storage.sqlite_store import SqliteRunLog

# ─── scoring weights (user-configurable) ────────────────────────────────────

DEFAULT_WEIGHTS: dict[str, float] = {
    "novelty": 0.35,
    "data_category": 0.20,
    "operator_diversity": 0.20,
    "sharpe_norm": 0.15,
    "fitness_norm": 0.10,
}

_RECENT_SUBMITTED_LIMIT = 400


# ─── helpers ─────────────────────────────────────────────────────────────────


def _decode_embedding(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    n = len(blob) // 4
    if n == 0:
        return None
    return list(struct.unpack(f"{n}f", blob[: n * 4]))


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-10 or nb < 1e-10:
        return 0.0
    return dot / (na * nb)


def _structure_tokens(expr: str) -> set[str]:
    return set(re.findall(r"\b[a-z_]+\b", expr.lower()))


# ─── scoring dimensions ───────────────────────────────────────────────────────


def _score_novelty(
    candidate_emb: list[float] | None,
    reference_embs: list[list[float]],
) -> float:
    """1 - max_cosine_similarity; 1.0 if no embedding or no references."""
    if candidate_emb is None or not reference_embs:
        return 1.0
    max_sim = max(_cosine(candidate_emb, ref) for ref in reference_embs)
    return max(0.0, 1.0 - max_sim)


def _score_data_category(candidate_cat: str | None, recent_cats: list[str]) -> float:
    """Inverse frequency of candidate's data_category in recent submissions."""
    if not candidate_cat or not recent_cats:
        return 1.0
    count = recent_cats.count(candidate_cat)
    return 1.0 / (1.0 + count)


def _score_operator_diversity(
    candidate_expr: str,
    reference_exprs: list[str],
) -> float:
    """1 - mean Jaccard overlap between candidate tokens and reference tokens."""
    if not reference_exprs:
        return 1.0
    cand_tokens = _structure_tokens(candidate_expr)
    if not cand_tokens:
        return 1.0
    jaccard_scores = [
        len(cand_tokens & _structure_tokens(r))
        / len(cand_tokens | _structure_tokens(r) or {""})
        for r in reference_exprs
    ]
    return max(0.0, 1.0 - (sum(jaccard_scores) / len(jaccard_scores)))


def _normalize_metric(value: float | None, lo: float, hi: float) -> float:
    """Clamp-normalize to [0, 1]."""
    if value is None:
        return 0.0
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


# ─── result type ─────────────────────────────────────────────────────────────


@dataclass
class JudgeScore:
    expression_id: str
    priority_score: float
    novelty: float = 0.0
    data_category: float = 0.0
    operator_diversity: float = 0.0
    sharpe_norm: float = 0.0
    fitness_norm: float = 0.0


# ─── judge ───────────────────────────────────────────────────────────────────


class SubmissionJudge:
    """Rank simulation-passed candidates by a multi-dimensional priority score."""

    def __init__(self, *, weights: dict[str, float] | None = None) -> None:
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    def _load_reference(
        self, db: SqliteRunLog
    ) -> tuple[list[list[float]], list[str], list[str]]:
        """Return (embeddings, expressions, data_categories) for recent submitted alphas."""
        if not db.path:
            return [], [], []
        with sqlite3.connect(str(db.path)) as con:
            rows = con.execute(
                """
                SELECT e.embedding, e.expression_text,
                       COALESCE(h.topic_id, '') AS topic_id,
                       COALESCE(t.data_category, '') AS data_category
                FROM expressions e
                LEFT JOIN hypotheses h ON e.hypothesis_id = h.hypothesis_id
                LEFT JOIN research_topics t ON h.topic_id = t.topic_id
                WHERE e.expression_id IN (
                    SELECT DISTINCT expression_id FROM simulation_runs
                    WHERE status = 'submitted'
                    ORDER BY id DESC LIMIT ?
                )
                """,
                (_RECENT_SUBMITTED_LIMIT,),
            ).fetchall()
        embs = [_decode_embedding(r[0]) for r in rows]
        valid_embs = [e for e in embs if e is not None]
        exprs = [r[1] for r in rows]
        cats = [r[3] for r in rows if r[3]]
        return valid_embs, exprs, cats

    def score(
        self,
        *,
        expression_id: str,
        expression_text: str,
        sharpe: float | None,
        fitness: float | None,
        embedding: list[float] | None,
        data_category: str | None,
        ref_embeddings: list[list[float]],
        ref_expressions: list[str],
        ref_categories: list[str],
        sharpe_range: tuple[float, float] = (1.0, 3.0),
        fitness_range: tuple[float, float] = (0.5, 2.0),
    ) -> JudgeScore:
        novelty = _score_novelty(embedding, ref_embeddings)
        cat_score = _score_data_category(data_category, ref_categories)
        op_div = _score_operator_diversity(expression_text, ref_expressions)
        sh_norm = _normalize_metric(sharpe, *sharpe_range)
        fi_norm = _normalize_metric(fitness, *fitness_range)
        w = self.weights
        priority = (
            w.get("novelty", 0) * novelty
            + w.get("data_category", 0) * cat_score
            + w.get("operator_diversity", 0) * op_div
            + w.get("sharpe_norm", 0) * sh_norm
            + w.get("fitness_norm", 0) * fi_norm
        )
        return JudgeScore(
            expression_id=expression_id,
            priority_score=priority,
            novelty=novelty,
            data_category=cat_score,
            operator_diversity=op_div,
            sharpe_norm=sh_norm,
            fitness_norm=fi_norm,
        )

    def rank(self, scores: list[JudgeScore]) -> list[JudgeScore]:
        return sorted(scores, key=lambda s: s.priority_score, reverse=True)

    def persist_score(self, db: SqliteRunLog, score: JudgeScore) -> None:
        if not db.path:
            return
        with sqlite3.connect(str(db.path)) as con:
            con.execute(
                "UPDATE expressions SET submission_priority_score = ? "
                "WHERE expression_id = ?",
                (score.priority_score, score.expression_id),
            )
