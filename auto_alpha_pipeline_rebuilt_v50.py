
from __future__ import annotations

import argparse
import ast
import csv
import glob
import json
import math
import os
import random
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth

# Single ledger for all pipeline script versions (v34/v37/v39/v40 share one file).
PIPELINE_VERSION = "v50.7"  # v50.7: disk-cache fields/datasets + 429→NETWORK_EXIT + guard underfill (stop empty-cycle spin)
# Which file is actually running (helps when multiple v4x copies exist).
_PIPELINE_SCRIPT = Path(__file__).resolve()
UNIFIED_FEEDBACK_CSV = "alpha_submission_feedback.csv"

UNIFIED_REGISTRY_CSV = "alpha_generated_expressions.csv"
UNIFIED_HOPEFUL_JSONL = "hopeful_alphas.jsonl"
UNIFIED_SUBMISSION_JSONL = "submission_results.jsonl"
UNIFIED_OUTPUT_PREFIX = "alpha_pipeline"
FEEDBACK_DIAGNOSTICS_CSV = "alpha_feedback_diagnostics.csv"
# Cross-process cache (each loop cycle is a fresh Python process).
_DATAFIELDS_DISK_CACHE = ".alpha_datafields_cache.json"
_DATASETS_DISK_CACHE = ".alpha_datasets_cache.json"
_DISK_CACHE_TTL_SECONDS = 4 * 3600
_FALLBACK_DATASET_IDS = (
    "fundamental6",
    "fundamental65",
    "analyst14",
    "analyst15",
    "model262",
    "pv1",
    "analyst4",
    "analyst10",
)
FEEDBACK_LEARNING_SUMMARY_CSV = "alpha_feedback_learning_summary.csv"
FEEDBACK_LEARNING_SUMMARY_JSON = "alpha_feedback_learning_summary.json"
CHECK_DISTRIBUTION_CSV = "alpha_feedback_check_distribution.csv"
BATCH_DIAGNOSTICS_CSV = "alpha_batch_diagnostics.csv"
NOVELTY_INDEX_JSON = "alpha_novelty_index.json"


# ---------------------------- bootstrap pandas ----------------------------

def _import_pandas_with_bootstrap():
    try:
        import pandas as _pd  # type: ignore
        return _pd
    except Exception as e:
        msg = str(e).lower()
        if "dateutil" not in msg:
            raise
        print("[bootstrap] missing dependency detected: python-dateutil; installing...")
        from alpha_mining.common import subprocess_no_window_kwargs

        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "python-dateutil"],
            stdout=sys.stdout,
            stderr=sys.stderr,
            **subprocess_no_window_kwargs(),
        )
        import pandas as _pd  # type: ignore
        return _pd


pd = _import_pandas_with_bootstrap()


# ---------------------------- helpers ----------------------------

def _load_env_file(path: Path | None = None) -> None:
    env_path = path or (Path(__file__).resolve().parent / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and val:
            os.environ.setdefault(key, val)
            if key in ("WQ_USERNAME", "WQ_PASSWORD"):
                os.environ[key] = val


def _credentials() -> tuple[str, str]:
    return (os.environ.get("WQ_USERNAME", "").strip(), os.environ.get("WQ_PASSWORD", "").strip())


def _mask(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "(empty)"
    if "@" in s:
        local, _, domain = s.partition("@")
        return f"{local[:2]}***@{domain}"
    return f"{s[:2]}***"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc_like(ts: Any) -> datetime | None:
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sig(expr: str) -> str:
    return re.sub(r"\s+", " ", str(expr or "").strip())


def _expression_identity(expr: str) -> str:
    """Whitespace-insensitive identity for exact platform simulation deduplication."""
    return re.sub(r"\s+", "", str(expr or "").lower())


def _dedupe_payloads_best_per_expr(payloads: list[dict]) -> list[dict]:
    """Keep the highest-scoring settings variant per normalized expression."""
    best: dict[str, dict] = {}
    for p in payloads:
        if not isinstance(p, dict):
            continue
        expr = _sig(p.get("regular") or "")
        if not expr:
            continue
        sc = float((p.get("meta") or {}).get("candidate_score") or 0.0)
        prev = best.get(expr)
        if prev is None or sc > float((prev.get("meta") or {}).get("candidate_score") or 0.0):
            best[expr] = p
    out = list(best.values())
    out.sort(key=lambda p: -float((p.get("meta") or {}).get("candidate_score") or 0.0))
    return out


def _skel(expr: str) -> str:
    s = _sig(expr).lower()
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "#", s)
    s = re.sub(r"\b[a-z_][a-z0-9_]*\b", "f", s)
    return s


def _shape_key(expr: str) -> str:
    """Preserve operators but normalize fields/groups/numbers for in-run diversity control."""
    s = _sig(expr).lower()
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "#", s)
    reserved = FUNCTIONS | {"trade_when", "if_else", "bucket"} | {"cap", "close", "open", "high", "low", "volume", "vwap", "adv20", "returns"}

    def repl(m: re.Match[str]) -> str:
        token = m.group(0)
        if token in GROUPS:
            return "g"
        if token in reserved:
            return token
        return "f"

    return re.sub(r"\b[a-z_][a-z0-9_]*\b", repl, s)


def _extract_functions(expr: str) -> list[str]:
    return re.findall(r"\b([a-z_][a-z0-9_]*)\s*\(", str(expr or "").lower())


def _extract_identifiers(expr: str) -> list[str]:
    return re.findall(r"\b[a-z_][a-z0-9_]*\b", str(expr or "").lower())


def _expression_fields(expr: str) -> list[str]:
    fields: list[str] = []
    func_set = FUNCTIONS | {"trade_when", "if_else", "bucket"}
    for ident in _extract_identifiers(expr):
        if ident in func_set or ident in GROUPS or ident in BASE_VARS:
            continue
        if ident in ("true", "false", "nan", "inf", "range", "rettype"):
            continue
        fields.append(ident)
    return list(dict.fromkeys(fields))


def _operator_skeleton(expr: str) -> str:
    """Normalize fields and numbers while preserving operator/function topology."""
    s = _shape_key(expr)
    s = re.sub(r"\s+", "", s.lower())
    s = re.sub(r"#+(?:\.#*)?", "#", s)
    return s


def _normalized_expression(expr: str) -> str:
    """Collapse cosmetic/numeric changes so parameter-only variants collide."""
    from alpha_mining.domain.expression_normalization import normalized_expression

    return normalized_expression(expr)


def _strip_outer_parens(expr: str) -> str:
    s = str(expr or "").strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        balanced = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        s = s[1:-1].strip()
    return s


def _behavior_operator_skeleton(expr: str) -> str:
    """Normalize sign/offset/coefficient tweaks that usually remain self-correlated."""
    s = _operator_skeleton(expr)
    s = _strip_outer_parens(s)
    # Whole-expression sign inversions are still near-perfect behavior clones.
    for _ in range(3):
        if s.startswith("-(") and s.endswith(")"):
            s = _strip_outer_parens(s[1:])
        elif s.startswith("-"):
            s = s[1:]
        else:
            break
        s = _strip_outer_parens(s)
    # Common rank-centering and coefficient-only variants should collide.
    s = re.sub(r"(?<=\))[-+]#", "", s)
    s = re.sub(r"(?<=\w)[-+]#", "", s)
    s = re.sub(r"\*-?#", "", s)
    s = re.sub(r"-rank\(", "rank(", s)
    s = re.sub(r"\+-", "+", s)
    s = re.sub(r"--", "", s)
    return _strip_outer_parens(s)


def _behavior_signature(expr: str) -> str:
    """Expression-level fingerprint for self-correlation avoidance.

    Unlike exact/skeleton checks, this intentionally treats sign flips, rank
    centering, coefficient tweaks, and settings-only variants as the same
    behavior family.
    """
    from alpha_mining.domain.expression_normalization import behavior_signature

    return behavior_signature(expr)


def _behavior_token_set(expr: str) -> set[str]:
    sig = _behavior_signature(expr)
    return set(re.findall(r"[a-z_]+|\d+|#", sig.lower()))


def _operator_field_key(expr: str) -> tuple[str, str]:
    return (_behavior_operator_skeleton(expr), _field_signature(expr, max_fields=8))


def _quality_diverse_enabled(cfg: Any) -> bool:
    return str(getattr(cfg, "diversity_mode", "quality_diverse") or "quality_diverse").lower() == "quality_diverse"


def _field_signature(expr: str, max_fields: int = 4) -> str:
    fields = sorted(_expression_fields(expr))[:max_fields]
    return "|".join(fields) if fields else "-"


def _structure_signature(expr: str) -> str:
    from alpha_mining.domain.expression_normalization import structure_signature

    return structure_signature(expr)


def _token_jaccard(a: set[str], b: set[str]) -> float:
    den = len(a | b)
    return (len(a & b) / den) if den else 0.0


def _expr_token_set(expr: str) -> set[str]:
    return set(re.findall(r"[a-z_]+|\d+", _sig(expr).lower()))


def _max_pool_similarity(
    toks: set[str],
    pool: list[set[str]],
    *,
    early_exit_at: float | None = None,
) -> float:
    if not toks or not pool:
        return 0.0
    best = 0.0
    for old in pool:
        best = max(best, _token_jaccard(toks, old))
        if early_exit_at is not None and best >= early_exit_at:
            return best
        if best >= 0.999:
            break
    return best


@dataclass
class HistorySimilarityPools:
    """Tiered historical expression tokens for nuanced self-correlation control."""

    toxic: list[set[str]] = field(default_factory=list)
    weak_fail: list[set[str]] = field(default_factory=list)
    near_pass: list[set[str]] = field(default_factory=list)
    passed: list[set[str]] = field(default_factory=list)
    generated: list[set[str]] = field(default_factory=list)
    self_corr_risk: list[set[str]] = field(default_factory=list)

    def max_similarity(
        self,
        expr: str,
        pool_name: str,
        *,
        early_exit_at: float | None = None,
    ) -> float:
        pool = getattr(self, pool_name, None)
        if not isinstance(pool, list):
            return 0.0
        return _max_pool_similarity(_expr_token_set(expr), pool, early_exit_at=early_exit_at)

    def max_behavior_similarity(
        self,
        expr: str,
        pool_name: str,
        *,
        early_exit_at: float | None = None,
    ) -> float:
        pool = getattr(self, pool_name, None)
        if not isinstance(pool, list):
            return 0.0
        return _max_pool_similarity(_behavior_token_set(expr), pool, early_exit_at=early_exit_at)

    def append_tokens(self, expr: str, tier: str) -> None:
        toks = _behavior_token_set(expr) if tier == "self_corr_risk" else _expr_token_set(expr)
        if not toks:
            return
        bucket = {
            "toxic": self.toxic,
            "weak_fail": self.weak_fail,
            "near_pass": self.near_pass,
            "passed": self.passed,
            "generated": self.generated,
            "self_corr_risk": self.self_corr_risk,
        }.get(tier, self.weak_fail)
        bucket.append(toks)

    def append_behavior_tokens(self, expr: str, tier: str) -> None:
        toks = _behavior_token_set(expr)
        if not toks:
            return
        bucket = {
            "self_corr_risk": self.self_corr_risk,
            "generated": self.generated,
            "near_pass": self.near_pass,
            "passed": self.passed,
            "weak_fail": self.weak_fail,
            "toxic": self.toxic,
        }.get(tier, self.weak_fail)
        bucket.append(toks)


def _family_stats_bucket(family: str) -> str:
    """Group families for historical pass-rate stats (feedback CSV)."""
    fam = (family or "").lower()
    if fam.startswith("near_pass"):
        return "near_pass_variant"
    if fam.startswith("pass_fundamental"):
        return "pass_fundamental"
    if fam.startswith("alpha_models") or fam.startswith("formulaic_") or fam == "external_template":
        return "template"
    if fam.startswith("arch_level_liquid"):
        return "arch_level_liquid"
    if fam.startswith("arch_delta"):
        return "arch_delta_liquid"
    if fam.startswith("arch_hybrid") or fam.startswith("arch_analyst") or fam.startswith("arch_pv"):
        return "arch_quality"
    if fam.startswith("arch_ts_rank") or fam.startswith("arch_regime") or fam.startswith("arch_zscore"):
        return "arch_momentum"
    if fam.startswith("arch_"):
        return "arch_other"
    return "other"


def _platform_pass_proxy(
    sharpe: float | None,
    fitness: float | None,
    *,
    check_passed: bool = False,
    min_sharpe: float = 1.24,
    min_fitness: float = 1.0,
) -> bool:
    if check_passed:
        return True
    if sharpe is None or fitness is None:
        return False
    return float(sharpe) >= float(min_sharpe) and float(fitness) >= float(min_fitness)


def _batch_priority_tier(family: str, source: str = "") -> int:
    """Lower tier = earlier in prescreen fine-rank and allocator (feedback-driven)."""
    fam = (family or "").lower()
    src = (source or "").lower()
    if fam.startswith("near_pass"):
        return 0
    if _is_priority_arch_quality_family(fam):
        return 0
    if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
        return 0
    if fam.startswith("pass_fundamental") or src == "pass_first":
        return 0
    if fam.startswith("arch_"):
        return 5
    return 5


def _is_priority_arch_quality_family(fam: str) -> bool:
    """No arch family is quality by default after the platform-sync audit."""
    return False


def _is_quality_simulate_family(fam: str, source: str = "") -> bool:
    """Proven pass-proxy families only — never arch_* (including hybrid/analyst)."""
    fam = (fam or "").lower()
    src = (source or "").lower()
    if _is_priority_arch_quality_family(fam):
        return True
    if fam.startswith("arch_"):
        return False
    if fam.startswith(("near_pass_variant", "near_pass_cross_field_variant")):
        return True
    if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
        return False
    if fam.startswith("pass_fundamental") or src in ("pass_first", "feedback_clone", "near_pass"):
        return True
    if fam.startswith("pass_pv"):
        return True
    return False


def _is_low_yield_arch_family(fam: str) -> bool:
    """All arch families — historical pass proxy ~0; never use for coarse/quality top-up."""
    fam = (fam or "").lower()
    return fam.startswith("arch_") and not _is_priority_arch_quality_family(fam)


def _toxic_similarity_cap_for_family(cfg: Any, family: str) -> float:
    """Resolve a family-specific token-similarity wall with a legacy fallback."""
    fam = str(family or "").lower()
    configured = getattr(cfg, "toxic_similarity_by_family", {}) or {}
    for prefix, cap in configured.items():
        if fam.startswith(str(prefix).lower()):
            return float(cap)
    return float(getattr(cfg, "prescreen_max_toxic_similarity", 0.85) or 0.85)


_TOXIC_CLOSE_DELTA_RE = re.compile(r"ts_delta\s*\(\s*close\s*,\s*(\d+)\s*\)", re.I)
_HYBRID_PRICE_LEG_RE = re.compile(
    r"0\.(?:70|75|80|30|25|20)\s*\*.*(?:rank\s*\(\s*ts_delta\s*\(\s*close|ts_delta\s*\(\s*close)",
    re.I,
)


def _has_toxic_near_pass_price_leg(expr: str) -> bool:
    """Fundamental + short-horizon price leg — historically ~0%% platform pass."""
    s = _sig(expr or "")
    if not s:
        return False
    low = s.lower()
    if _HYBRID_PRICE_LEG_RE.search(low):
        return True
    # Pure vwap/close technical legs remain high-turnover / low-Sharpe clusters.
    if "vwap/close" in low or "vwap / close" in low:
        if "/cap" not in low and "cap)" not in low:
            return True
    for m in _TOXIC_CLOSE_DELTA_RE.finditer(low):
        try:
            if int(m.group(1)) <= 42:
                return True
        except ValueError:
            continue
    if ("rank(ts_delta(close" in low or "rank( ts_delta(close" in low) and re.search(
        r"ts_delta\s*\(\s*close\s*,\s*(\d+)", low
    ):
        m = re.search(r"ts_delta\s*\(\s*close\s*,\s*(\d+)", low)
        if m and int(m.group(1)) <= 42:
            return True
    return False


def _expression_pattern_bucket(expr: str, family: str = "") -> str:
    fam = (family or "").lower()
    low = _sig(expr or "").lower()
    if fam.startswith("near_pass") or _has_toxic_near_pass_price_leg(expr):
        if _has_toxic_near_pass_price_leg(expr):
            return "hybrid_close_delta"
    if fam.startswith("arch_") or any(x in low for x in ("arch_ts_rank", "arch_regime", "arch_zscore")):
        return "arch_explore"
    if fam.startswith("alpha_models") or fam.startswith("formulaic_") or "external_template" in fam:
        return "template"
    if fam.startswith("pass_fundamental") or "/cap" in low or "cap)" in low:
        return "pure_fundamental"
    if "ts_delta(close" in low or "ts_delta( close" in low:
        return "price_momentum"
    return "other"


def _payload_fine_rank_key(
    payload: dict,
    family_pass_rates: dict[str, float] | None = None,
    family_quality_stats: dict[str, dict[str, float]] | None = None,
    fast_signal_penalty: float = 0.0,
) -> tuple[int, float, float, float, float, int]:
    """Sort key for prescreen/allocator: prioritize proven pass-rate and penalize bad metric families."""
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    fam = str(meta.get("family") or "").lower()
    src = str(meta.get("source") or "").lower()
    sc = float(meta.get("candidate_score") or 0.0)
    variant = int(meta.get("variant") or 0)
    tier = _batch_priority_tier(fam, src)
    bucket = _family_stats_bucket(fam)
    rates = family_pass_rates or {}
    rate = float(rates.get(bucket, 0.02))
    qstats = family_quality_stats or {}
    q = qstats.get(fam) or qstats.get(bucket) or {}
    bad_metric = float(q.get("bad_metric_rate") or 0.0)
    hard_fail = float(q.get("hard_fail_rate") or 0.0)
    concentrated = float(q.get("concentrated_weight_fail_rate") or 0.0)
    pass_proxy = float(q.get("pass_proxy_rate") or rate)
    penalty = bad_metric * 0.75 + hard_fail * 0.35 + concentrated * 0.25
    expr = str(payload.get("regular") or "") if isinstance(payload, dict) else ""
    function_calls = len(re.findall(r"\b[a-z_][a-z0-9_]*\s*\(", expr.lower()))
    depth = PreSimulationScreener._nesting_depth(expr) if expr else 0
    complexity_penalty = max(0, function_calls - 5) * 0.05 + max(0, depth - 4) * 0.08
    if (fam.startswith("alpha_models_template") or fam.startswith("formulaic_")) and pass_proxy < 0.01:
        penalty += 0.30
    if _is_low_yield_arch_family(fam):
        penalty += 0.55
    if fam.startswith("arch_"):
        penalty += 0.25
    # Diversity: down-weight fast/short-window price signals when penalty is configured
    if fast_signal_penalty > 0.0 and expr:
        try:
            from alpha_mining.filter.ladder_check import is_fast_signal
            if is_fast_signal(expr):
                penalty += float(fast_signal_penalty)
        except Exception:
            pass
    adjusted_score = sc - penalty * 4.0
    return (tier, penalty + complexity_penalty, -rate, -adjusted_score, -sc, variant)


def _low_yield_bucket_key_for_payload(payload: dict) -> tuple[str, str, str, str, str, str]:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    expr = str(payload.get("regular") or "")
    family = str(meta.get("family") or "").strip() or "unknown"
    source = str(meta.get("source") or "").strip() or "unknown"
    window = _learning_window(expr)
    neutral = str(settings.get("neutralization") or "").strip().upper()
    decay = str(settings.get("decay") if settings.get("decay") is not None else "").strip()
    trunc = str(settings.get("truncation") if settings.get("truncation") is not None else "").strip()
    return (family, source, window, neutral, decay, trunc)


def _learning_window(expr: str) -> str:
    wins = [int(x) for x in re.findall(r"ts_[a-z_]+\([^)]*,\s*(\d+)", str(expr or ""), flags=re.I)]
    return str(max(wins)) if wins else ""


def _history_quality_tier(
    *,
    sharpe: float | None,
    fitness: float | None,
    check_passed: bool,
    feedback_text: str,
    near_pass_min_sharpe: float = 0.75,
    near_pass_seed_min_composite: float = 1.38,
) -> str:
    """Classify a historical alpha for similarity-pool routing (not submission)."""
    if check_passed:
        return "passed"
    low = (feedback_text or "").lower()
    sh = sharpe
    fit = fitness
    fail_tags = sum(
        1
        for tag in (
            "low_sharpe",
            "low_fitness",
            "low_turnover",
            "low_sub_universe",
            "concentrated_weight",
            "high_turnover",
            "check_failed",
        )
        if tag in low
    )
    if sh is not None:
        composite = float(sh) + 1.05 * float(fit or 0.0)
        if not check_passed and sh >= near_pass_min_sharpe and composite >= near_pass_seed_min_composite:
            return "near_pass"
    # Toxic = only clearly broken alphas (negative / multi-fail junk). Borderline failures stay weak_fail.
    if sh is not None and sh < 0.0:
        return "toxic"
    if sh is not None and sh < 0.05:
        return "toxic"
    if fit is not None and fit < -0.05 and sh is not None and sh < 0.15:
        return "toxic"
    if fail_tags >= 4 and sh is not None and sh < 0.15:
        return "toxic"
    if fail_tags >= 3 and sh is not None and sh < 0.0:
        return "toxic"
    return "weak_fail"


def _novelty_profile(strictness: str) -> dict[str, Any]:
    mode = (strictness or "strict").lower()
    if mode == "balanced":
        return {"same_shape_per_batch": 5, "field_jaccard": 0.86, "block_operator_field": False}
    if mode == "paranoid":
        return {"same_shape_per_batch": 1, "field_jaccard": 0.70, "block_operator_field": True}
    return {"same_shape_per_batch": 3, "field_jaccard": 0.78, "block_operator_field": True}


class NoveltyIndex:
    """Compact expression fingerprint index for self-correlation avoidance."""

    def __init__(self) -> None:
        self.normalized: set[str] = set()
        self.operator_skeletons: set[str] = set()
        self.field_signatures: set[str] = set()
        self.structure_signatures: set[str] = set()
        self.operator_field_pairs: set[tuple[str, str]] = set()
        self.field_token_sets: list[set[str]] = []

    def add(self, expr: str) -> None:
        e = _sig(expr)
        if not e:
            return
        norm = _normalized_expression(e)
        op = _operator_skeleton(e)
        fields = _field_signature(e)
        struct = _structure_signature(e)
        self.normalized.add(norm)
        self.operator_skeletons.add(op)
        self.field_signatures.add(fields)
        self.structure_signatures.add(struct)
        self.operator_field_pairs.add((op, fields))
        fset = set(fields.split("|")) - {"-"}
        if fset:
            self.field_token_sets.append(fset)

    def add_many(self, exprs: Iterable[str]) -> None:
        for expr in exprs:
            self.add(expr)

    def reject_reason(self, expr: str, *, strictness: str) -> str | None:
        e = _sig(expr)
        if not e:
            return "novelty_empty"
        profile = _novelty_profile(strictness)
        norm = _normalized_expression(e)
        op = _operator_skeleton(e)
        fields = _field_signature(e)
        struct = _structure_signature(e)
        if norm in self.normalized:
            return "novelty_exact_normalized"
        if struct in self.structure_signatures:
            return "novelty_same_structure"
        if profile["block_operator_field"] and (op, fields) in self.operator_field_pairs:
            return "novelty_same_operator_fields"
        fset = set(fields.split("|")) - {"-"}
        if len(fset) >= 2 and (strictness or "strict").lower() == "paranoid":
            threshold = float(profile["field_jaccard"])
            for old in self.field_token_sets:
                if _token_jaccard(fset, old) >= threshold:
                    return f"novelty_field_overlap>={threshold:.2f}"
        return None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "version": PIPELINE_VERSION,
            "normalized_count": len(self.normalized),
            "operator_skeleton_count": len(self.operator_skeletons),
            "field_signature_count": len(self.field_signatures),
            "structure_signature_count": len(self.structure_signatures),
            "sample_normalized": sorted(self.normalized)[:30],
            "sample_operator_skeletons": sorted(self.operator_skeletons)[:30],
            "sample_field_signatures": sorted(self.field_signatures)[:30],
        }


def _feedback_csv_candidates(base_dir: Path) -> list[Path]:
    patterns = (
        "alpha_submission_feedback*.csv",
        "worldquant_alphas_repo_feedback.csv",
        "*_results.csv",
        "*_checkpoint.csv",
    )
    out: list[Path] = []
    for pat in patterns:
        out.extend(base_dir.glob(pat))
    seen: set[str] = set()
    unique: list[Path] = []
    for p in sorted(out, key=lambda x: (x.name, str(x))):
        key = str(p.resolve()).lower()
        if key not in seen and p.is_file():
            seen.add(key)
            unique.append(p)
    return unique


def _json_load_maybe(text: Any) -> Any:
    if not text:
        return None
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(str(text))
    except Exception:
        return None


def _feedback_text_from_row(row: dict[str, Any]) -> str:
    parts = [
        row.get("Failure Reasons"),
        row.get("failure_reasons"),
        row.get("check_note"),
        row.get("status"),
        row.get("submit_note"),
    ]
    for col in ("platform_simulation_json", "platform_check_json"):
        obj = _json_load_maybe(row.get(col))
        if isinstance(obj, dict):
            parts.append(obj.get("message"))
            checks = _deep_get(obj, "is", "checks") or obj.get("checks")
            if isinstance(checks, list):
                for c in checks:
                    if isinstance(c, dict):
                        parts.append(" ".join(str(c.get(k) or "") for k in ("name", "result", "message", "limit", "value")))
    return " | ".join(str(x or "") for x in parts if x)


def _classify_feedback_reason(text: str) -> str:
    low = str(text or "").lower()
    if not low:
        return "missing_reason"
    if "self_correlation" in low or "self correlation" in low:
        return "SELF_CORRELATION"
    if "low_sharpe" in low or "sharpe" in low:
        return "LOW_SHARPE"
    if "fitness" in low:
        return "LOW_FITNESS"
    if "turnover" in low:
        return "TURNOVER"
    if "sub-universe" in low or "sub universe" in low:
        return "SUB_UNIVERSE"
    if "unknown operator" in low or "inaccessible or unknown operator" in low:
        return "UNKNOWN_OPERATOR"
    if "unknown variable" in low:
        return "UNKNOWN_VARIABLE"
    if "timeout" in low or "pending" in low:
        return "PENDING_OR_TIMEOUT"
    if "forbidden" in low or "403" in low:
        return "SUBMIT_FORBIDDEN"
    if "not found" in low or "404" in low:
        return "SUBMIT_NOT_FOUND"
    if "error" in low or "failed" in low:
        return "PLATFORM_ERROR"
    return "OTHER"


def _feedback_operator(text: str) -> str:
    m = re.search(r'operator\s+"?([a-zA-Z_][a-zA-Z0-9_]*)"?', str(text or ""), flags=re.I)
    return m.group(1).lower() if m else ""


def _feedback_variable(text: str) -> str:
    m = re.search(r'variable\s+"?([a-zA-Z_][a-zA-Z0-9_]*)"?', str(text or ""), flags=re.I)
    return m.group(1).lower() if m else ""


def _payload_fingerprint(expr: str, settings: dict | None) -> str:
    """Fingerprint a simulation payload by expression and the settings that change IS metrics."""
    settings = settings if isinstance(settings, dict) else {}

    def pick(*keys: str) -> str:
        for key in keys:
            if key in settings:
                return str(settings.get(key) or "").upper()
            low = key.lower()
            for k, v in settings.items():
                if str(k).lower() == low:
                    return str(v or "").upper()
        return ""

    parts = [
        _expression_identity(expr),
        pick("universe", "Universe"),
        pick("neutralization", "Neutralization"),
        pick("decay", "Decay"),
        pick("truncation", "Truncation"),
        pick("delay", "Delay"),
        pick("nanHandling", "NaN_Handling", "NaN Handling"),
    ]
    return "||".join(parts)


def _deep_get(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except Exception:
        return None


def _metric_get(obj: dict | None, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None
    pools: list[Any] = [obj, obj.get("is"), obj.get("summary")]
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        for key in keys:
            if key in pool:
                return pool[key]
            low = key.lower()
            for k, v in pool.items():
                if str(k).lower() == low:
                    return v
    return None


def _merge_json_dicts(a: dict | None, b: dict | None) -> dict | None:
    if not isinstance(a, dict):
        return b if isinstance(b, dict) else a
    if not isinstance(b, dict):
        return a
    merged = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _merge_json_dicts(merged[k], v)
        else:
            merged[k] = v
    return merged


def _json_compact(obj: Any, max_chars: int = 60000) -> str:
    if obj is None:
        return ""
    try:
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        s = str(obj)
    if len(s) > max_chars:
        return s[:max_chars] + "...<truncated>"
    return s


def _alpha_id_from_progress(body: dict) -> str | None:
    """WorldQuant progress responses use top-level id for the simulation task.

    The real alpha id can be body["alpha"] as a string. v33 sometimes fell back to
    body["id"], which caused 404 when the simulation id was submitted as an alpha.
    """
    alpha = body.get("alpha")
    if isinstance(alpha, str) and alpha.strip():
        return alpha.strip()
    if isinstance(alpha, dict):
        for key in ("id", "alpha", "alphaId"):
            v = alpha.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    for key in ("alphaId", "alpha_id"):
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_checks(detail: dict | None) -> list[dict]:
    if not isinstance(detail, dict):
        return []
    is_data = detail.get("is") if isinstance(detail.get("is"), dict) else detail
    checks = is_data.get("checks") if isinstance(is_data, dict) else None
    return [c for c in checks if isinstance(c, dict)] if isinstance(checks, list) else []


def _check_summary(detail: dict | None) -> str:
    parts = []
    for c in _extract_checks(detail):
        name = str(c.get("name") or "").upper()
        result = str(c.get("result") or c.get("status") or "").upper()
        if name and result:
            value = c.get("value")
            limit = c.get("limit")
            if value is not None or limit is not None:
                parts.append(f"{name}:{result}:{value}/{limit}")
            else:
                parts.append(f"{name}:{result}")
    return "; ".join(parts)


def _platform_message(detail: dict | None) -> str:
    if not isinstance(detail, dict):
        return ""
    candidates: list[str] = []
    containers = [detail]
    inner = detail.get("is")
    if isinstance(inner, dict):
        containers.append(inner)
    for obj in containers:
        for key in ("message", "error", "detail", "reason", "description", "status_message", "statusMessage"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                candidates.append(val.strip())
    return " | ".join(dict.fromkeys(candidates))


def _is_incompatible_unit_result(detail: dict | None) -> bool:
    return "incompatible unit" in _platform_message(detail).lower()


def _format_num(v: Any, nd: int = 2) -> str:
    fv = _to_float(v)
    if fv is None:
        return "N/A"
    return f"{fv:.{nd}f}"


def _format_pct(v: Any, nd: int = 2) -> str:
    fv = _to_float(v)
    if fv is None:
        return "N/A"
    return f"{fv * 100:.{nd}f}%"


def _platform_fail_reason_text(detail: dict | None, sim_json: dict | None = None, status: str = "") -> str:
    checks = _extract_checks(detail)
    lines: list[str] = []
    for c in checks:
        name = str(c.get("name") or "").upper()
        result = str(c.get("result") or c.get("status") or "").upper()
        if result not in ("FAIL", "FAILED", "ERROR", "REJECTED"):
            continue
        value = c.get("value")
        limit = c.get("limit")
        if name == "LOW_SHARPE":
            lines.append(f"Sharpe of {_format_num(value)} is below cutoff of {_format_num(limit)}.")
        elif name == "LOW_FITNESS":
            lines.append(f"Fitness of {_format_num(value)} is below cutoff of {_format_num(limit)}.")
        elif name == "LOW_TURNOVER":
            lines.append(f"Turnover of {_format_pct(value)} is below cutoff of {_format_pct(limit)}.")
        elif name == "HIGH_TURNOVER":
            lines.append(f"Turnover of {_format_pct(value)} is above cutoff of {_format_pct(limit)}.")
        elif name == "LOW_SUB_UNIVERSE_SHARPE":
            lines.append(f"Sub-universe Sharpe of {_format_num(value)} is below cutoff of {_format_num(limit)}.")
        elif name == "CONCENTRATED_WEIGHT":
            dt = c.get("date") or c.get("at") or c.get("timestamp")
            if dt:
                lines.append(f"Weight concentration {_format_pct(value, 0)} is above cutoff of {_format_pct(limit, 0)} on {dt}.")
            else:
                lines.append(f"Weight concentration {_format_pct(value, 0)} is above cutoff of {_format_pct(limit, 0)}.")
        else:
            if value is not None or limit is not None:
                lines.append(f"{name} failed: value={value}, cutoff={limit}.")
            else:
                lines.append(f"{name} failed.")

    merged = detail if isinstance(detail, dict) else sim_json
    sharpe = _to_float(_metric_get(merged, "sharpe", "Sharpe"))
    fitness = _to_float(_metric_get(merged, "fitness", "Fitness"))
    turnover = _to_float(_metric_get(merged, "turnover", "Turnover"))
    returns = _to_float(_metric_get(merged, "returns", "Returns"))
    drawdown = _to_float(_metric_get(merged, "drawdown", "Drawdown"))
    margin = _to_float(_metric_get(merged, "margin", "Margin"))

    agg = [
        "",
        "Aggregate Data:",
        "Sharpe",
        _format_num(sharpe),
        "Turnover",
        _format_pct(turnover),
        "Fitness",
        _format_num(fitness),
        "Returns",
        _format_pct(returns),
        "Drawdown",
        _format_pct(drawdown),
        "Margin",
        f"{_format_num(margin)}‱" if margin is not None else "N/A",
    ]

    if not lines:
        summary = _check_summary(detail)
        if summary:
            lines.append(summary)
        sim_msg = ""
        if isinstance(sim_json, dict):
            sim_msg = str(sim_json.get("message") or "")
        if sim_msg:
            lines.append(sim_msg)
        elif status:
            lines.append(status)
        elif not summary:
            lines.append("unknown_failure")
    return "\n".join(lines + agg)


def _failure_reason_for_ledger(
    merged: dict | None,
    *,
    sim_json: dict | None = None,
    status: str = "",
    check_note: str = "",
) -> str:
    text = _platform_fail_reason_text(merged, sim_json=sim_json, status=status)
    note = str(check_note or "").strip()
    if note and note not in text:
        text = f"check: {note}\n{text}"
    return text


def _simulate_result_row(
    *,
    index: int,
    alpha_id: str | None,
    status: str,
    queue_status: str,
    check_passed: bool | None,
    check_note: str,
    expression: str,
    profile: str,
    merged: dict | None,
    sim_json: dict | None = None,
) -> dict[str, Any]:
    metrics = {
        "sharpe": _to_float(_metric_get(merged, "sharpe", "Sharpe")),
        "fitness": _to_float(_metric_get(merged, "fitness", "Fitness")),
        "turnover": _to_float(_metric_get(merged, "turnover", "Turnover")),
    }
    analysis = _feedback_analysis_fields(
        metrics,
        merged,
        check_passed=check_passed,
        check_note=check_note,
        queue_status=queue_status,
    )
    return {
        "index": index,
        "alpha_id": alpha_id or "",
        "status": status,
        "queue_status": queue_status,
        "check_passed": check_passed,
        "check_note": check_note,
        "expression": expression,
        "profile": profile,
        "sharpe": metrics["sharpe"],
        "fitness": metrics["fitness"],
        "turnover": metrics["turnover"],
        "returns": _to_float(_metric_get(merged, "returns", "Returns")),
        "drawdown": _to_float(_metric_get(merged, "drawdown", "Drawdown")),
        **analysis,
        "simulation_id": str((sim_json or {}).get("id") or ""),
        "failure_reasons": _failure_reason_for_ledger(merged, sim_json=sim_json, status=status, check_note=check_note)[:8000],
    }


def _non_self_checks_all_pass(detail: dict | None) -> bool:
    checks = _extract_checks(detail)
    non_self = [c for c in checks if str(c.get("name") or "").upper() != "SELF_CORRELATION"]
    return bool(non_self) and all(str(c.get("result") or "").upper() == "PASS" for c in non_self)


def _self_correlation_pending(detail: dict | None) -> bool:
    for c in _extract_checks(detail):
        if str(c.get("name") or "").upper() == "SELF_CORRELATION":
            return str(c.get("result") or "").upper() == "PENDING"
    return False


def _self_correlation_status(detail: dict | None) -> str:
    for c in _extract_checks(detail):
        if str(c.get("name") or "").upper() == "SELF_CORRELATION":
            result = str(c.get("result") or c.get("status") or "").upper()
            return result or "MISSING"
    return "MISSING"


def _try_classify_detail_without_poll(detail: dict | None) -> tuple[bool | None, str] | None:
    """If platform detail already has enough check info, return (check_passed, note). Else None → need poll."""
    if not isinstance(detail, dict):
        return None
    hard = _hard_fail_checks(detail)
    if hard:
        return False, "check_failed:" + ",".join(hard)
    if _non_self_checks_all_pass(detail):
        if _self_correlation_pending(detail):
            return None, "metric_pass:self_correlation_pending"
        if _self_correlation_status(detail) == "PASS":
            return True, "check_passed"
        return None, "metric_pass:self_correlation_missing_or_incomplete"
    checks = _extract_checks(detail)
    if not checks:
        return None
    for c in checks:
        name = str(c.get("name") or "").upper()
        result = str(c.get("result") or c.get("status") or "").upper()
        if name != "SELF_CORRELATION" and result in ("PENDING", ""):
            return None
    return None


def _hard_fail_checks(detail: dict | None) -> list[str]:
    hard = []
    for c in _extract_checks(detail):
        name = str(c.get("name") or "").upper()
        result = str(c.get("result") or c.get("status") or "").upper()
        if result in ("FAIL", "FAILED", "ERROR", "REJECTED"):
            hard.append(name or "UNKNOWN_CHECK")
    return hard


def _metric_gate_pass_proxy(metrics: dict[str, Any]) -> tuple[bool, str]:
    sharpe = _to_float(metrics.get("sharpe") if isinstance(metrics, dict) else None)
    fitness = _to_float(metrics.get("fitness") if isinstance(metrics, dict) else None)
    turnover = _to_float(metrics.get("turnover") if isinstance(metrics, dict) else None)
    if sharpe is None or fitness is None or turnover is None:
        return False, "missing_core_metrics"
    if sharpe <= 1.25:
        return False, "sharpe_not_above_1.25"
    if fitness <= 1.0:
        return False, "fitness_not_above_1.0"
    if turnover < 0.01:
        return False, "turnover_below_1pct"
    if turnover >= 0.70:
        return False, "turnover_at_or_above_70pct"
    return True, "ok"


def _feedback_analysis_fields(
    metrics: dict[str, Any],
    detail: dict | None,
    *,
    check_passed: bool | None,
    check_note: str = "",
    queue_status: str = "",
) -> dict[str, Any]:
    metric_gate_pass, metric_gate_reason = _metric_gate_pass_proxy(metrics)
    non_self_pass = _non_self_checks_all_pass(detail)
    self_status = _self_correlation_status(detail)
    hard = _hard_fail_checks(detail)
    checks = _extract_checks(detail)
    incompatible_unit = _is_incompatible_unit_result(detail)
    platform_message = _platform_message(detail)
    check_pending = (
        check_passed is None
        and (
            str(check_note or "").startswith("check_timeout")
            or str(queue_status or "").startswith("needs_recheck")
        )
        and not checks
    )
    platform_pass_evidence = bool(check_passed is True and non_self_pass and self_status == "PASS" and not hard and not incompatible_unit)
    # Fail closed: PENDING/MISSING/UNKNOWN correlation is research evidence only,
    # never a submission candidate. Recheck scheduling remains unchanged.
    # Compatibility analytics may retain metric research filters, but a
    # submission candidacy claim requires complete platform PASS evidence.
    submission_candidate = platform_pass_evidence

    sharpe = _to_float(metrics.get("sharpe"))
    fitness = _to_float(metrics.get("fitness"))
    turnover = _to_float(metrics.get("turnover"))
    metric_parts = [
        f"Sharpe {_format_num(sharpe)} > 1.25",
        f"Fitness {_format_num(fitness)} > 1.0",
        f"Turnover {_format_pct(turnover)} within [1%,70%)",
    ]
    check_parts: list[str] = []
    for c in _extract_checks(detail):
        name = str(c.get("name") or "").upper()
        if name in {
            "LOW_SHARPE",
            "LOW_FITNESS",
            "LOW_TURNOVER",
            "HIGH_TURNOVER",
            "LOW_SUB_UNIVERSE_SHARPE",
            "CONCENTRATED_WEIGHT",
            "SELF_CORRELATION",
        }:
            result = str(c.get("result") or c.get("status") or "MISSING").upper()
            check_parts.append(f"{name}={result}")

    if incompatible_unit:
        blocked_reason = "incompatible_unit"
    elif not metric_gate_pass:
        blocked_reason = metric_gate_reason
    elif hard:
        blocked_reason = ",".join(hard)
    elif self_status == "PENDING":
        blocked_reason = "self_correlation_pending"
    elif check_pending:
        blocked_reason = "self_or_platform_check_pending"
    elif not non_self_pass:
        blocked_reason = "platform_non_self_not_pass"
    elif self_status == "MISSING":
        blocked_reason = "self_correlation_missing"
    else:
        blocked_reason = ""

    return {
        "metric_gate_pass": metric_gate_pass,
        "platform_non_self_pass": non_self_pass,
        "self_correlation_status": self_status,
        "submission_candidate": submission_candidate,
        "platform_pass_evidence": platform_pass_evidence,
        "platform_gate_reason": _check_summary(detail) or platform_message or str(check_note or queue_status or ""),
        "pass_proxy_reason": "; ".join(metric_parts + check_parts),
        "blocked_reason": blocked_reason,
    }


def _is_dns_error(exc: BaseException | str) -> bool:
    s = str(exc).lower()
    return any(x in s for x in ("nameresolutionerror", "getaddrinfo failed", "temporary failure in name resolution", "gaierror", "getaddrinfo"))


def _is_ssl_error(exc: BaseException | str) -> bool:
    if isinstance(exc, requests.exceptions.SSLError):
        return True
    s = str(exc).lower()
    return any(
        x in s
        for x in (
            "ssl",
            "unexpected_eof",
            "eof occurred in violation of protocol",
            "certificate verify failed",
            "wrong version number",
        )
    )


def _is_transient_connect_error(exc: BaseException) -> bool:
    if _is_dns_error(exc):
        return True
    if _is_ssl_error(exc):
        return True
    s = str(exc).lower()
    return any(
        x in s
        for x in (
            "cannot connect to host",
            "connection reset",
            "connection refused",
            "connection aborted",
            "timed out",
            "timeout",
            "broken pipe",
            "semaphore timeout",
            # Proxy / local-tunnel down. The OS-level "connection refused" text is
            # locale-dependent (e.g. zh-CN WinError 10061), so match on the
            # locale-independent requests/urllib3 wrappers instead.
            "unable to connect to proxy",
            "failed to establish a new connection",
            "proxyerror",
            "winerror 10061",
        )
    )


def _short_err(exc: BaseException, *, limit: int = 140) -> str:
    s = str(exc).replace("\n", " ").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


# Process exit code reserved for "the API/proxy was unreachable" — distinct from a
# generic failure (1) so the outer loop (run_pipeline_loop.py) can pause-and-wait
# for connectivity instead of burning a real cycle.
NETWORK_EXIT_CODE = 3
# Auth failure requiring human intervention (401, daily cap exceeded).
# The outer loop must NOT retry on this code — it stops and writes a sentinel.
AUTH_FATAL_EXIT_CODE = 4


def _exit_code_for_fatal(exc: BaseException) -> int:
    """Map an uncaught fatal exception to a process exit code.

    Network/proxy unreachability → NETWORK_EXIT_CODE so the loop can pause-and-wait.
    Auth failure (401, daily cap) → AUTH_FATAL_EXIT_CODE so the loop stops immediately.
    """
    try:
        if isinstance(exc, requests.exceptions.ProxyError) or _is_transient_connect_error(exc):
            return NETWORK_EXIT_CODE
    except Exception:
        pass
    # Rate-limit exhaustion is a transient platform throttle, not a logic bug.
    # Map to NETWORK_EXIT so run_pipeline_loop pauses the same cycle instead of
    # burning consecutive_failures toward the hard stop.
    try:
        resp = getattr(exc, "response", None)
        code = int(getattr(resp, "status_code", 0) or 0)
        if code == 429:
            return NETWORK_EXIT_CODE
    except Exception:
        pass
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg:
        return NETWORK_EXIT_CODE
    try:
        from alpha_mining.auth.session_manager import AuthDailyLimitExceeded, AuthenticationFailed
        if isinstance(exc, AuthDailyLimitExceeded):
            return AUTH_FATAL_EXIT_CODE
        if isinstance(exc, AuthenticationFailed) and "401" in str(exc):
            return AUTH_FATAL_EXIT_CODE
    except Exception:
        pass
    return 1


class _TLSAdapter(HTTPAdapter):
    def __init__(self, ctx: ssl.SSLContext, **kw: Any):
        self._ctx = ctx
        super().__init__(**kw)

    def init_poolmanager(self, *a, **kw):
        kw["ssl_context"] = self._ctx
        return super().init_poolmanager(*a, **kw)

    def proxy_manager_for(self, proxy, **kw):
        kw["ssl_context"] = self._ctx
        return super().proxy_manager_for(proxy, **kw)


# ---------------------------- config / constants ----------------------------

BASE_VARS = {
    "open", "close", "high", "low", "volume", "returns", "return", "vwap", "adv20",
    "cap", "sharesout", "sector", "industry", "subindustry", "market",
}

GROUPS = {"market", "sector", "industry", "subindustry"}

FUNCTIONS = {
    "abs", "log", "rank", "zscore", "power", "signed_power", "divide",
    "sum", "mean", "std", "delay", "ts_delay", "ts_delta", "ts_mean", "ts_sum",
    "ts_rank", "ts_zscore", "ts_std_dev", "ts_corr", "correlation", "ts_backfill",
    "ts_av_diff", "ts_decay_linear", "ts_decay_exp_window", "group_rank",
    "group_neutralize", "group_mean", "trade_when", "bucket",
    "ts_regression", "if_else", "min", "max",
}

# Operators that either error on common BRAIN tiers ("inaccessible or unknown operator")
# or are historically unreliable for automated submission passes.
BLOCKED_FUNCTIONS = {"exp", "ts_skewness", "ts_ir", "vector_neut", "regression_neut"}

FEEDBACK_FIELDS = (
    "utc_iso", "pipeline_version", "alpha_id", "simulation_id", "expression", "family", "source", "profile",
    "status", "queue_status", "submitted", "submit_note", "check_passed", "check_note",
    "Region", "Universe", "Neutralization", "Decay", "Truncation", "Delay",
    "Sharpe", "Fitness", "Turnover", "Returns", "Drawdown", "Margin",
    "metric_gate_pass", "platform_non_self_pass", "self_correlation_status", "submission_candidate",
    "platform_pass_evidence", "platform_gate_reason", "pass_proxy_reason", "blocked_reason",
    "Failure Reasons", "platform_simulation_json", "platform_check_json",
)


@dataclass
class PipelineConfig:
    username: str
    password: str
    mode: str = "full"
    preset: str = "mixed"
    region: str = "USA"
    universe: str = "TOP3000"
    instrument_type: str = "EQUITY"
    delay: int = 1
    dataset_ids: tuple[str, ...] | None = None
    dataset_auto_max: int = 8
    field_top_n: int = 450
    min_coverage: float = 0.30
    min_date_coverage: float = 0.50
    budget: int = 300
    run_payload_cap: int | None = None
    # Soft cap per run (0 = no cap except min floor).
    target_simulate_batch: int = 180
    max_simulate_batch_per_run: int = 240
    # ---- batch simulate policy ----
    min_simulate_batch: int = 180
    target_platform_pass_rate: float = 0.05
    prescreen_relax_to_hit_min_batch: bool = True
    # When loosening prescreen: **raise** these Jaccard caps (higher = allow more similar past alphas through).
    prescreen_similarity_relax_step: float = 0.03
    prescreen_similarity_relax_ceiling: float = 0.92
    # Hard ceiling for *building* payloads from candidates (each candidate yields a few settings variants).
    max_payload_expand_cap: int = 500_000
    candidate_multiplier: int = 30
    max_generated_ceiling: int = 20000
    pair_limit: int = 60
    allow_sentiment_datasets: bool = False
    allow_option_datasets: bool = False
    allow_universe_grid: bool = False
    min_sharpe_threshold: float = 1.25
    min_fitness_threshold: float = 1.0
    min_turnover_threshold: float = 0.01
    max_turnover_threshold: float = 0.70
    # Very short ts_corr windows often breach HIGH_TURNOVER on the platform.
    min_ts_corr_window: int = 10
    queue_min_sharpe: float = 1.25
    queue_min_fitness: float = 1.0
    auto_submit_when_passed: bool = False
    dry_run_submit: bool = True
    max_submit: int = 20
    submit_batch_size: int = 3
    max_consecutive_submit_failures: int = 3
    queue_recheck_seconds: int = 3 * 3600
    library_expression_fetch_max: int = 2000
    # Merge platform /users/self/alphas into prescreen "already simulated" so poll_ok ≠ false new alphas.
    sync_platform_tried_before_simulate: bool = True
    dedup_against_library_skeleton: bool = True
    success_blacklist_similarity_threshold: float = 0.88
    max_family_share: float = 0.14
    # Token Jaccard vs *all* historically simulated / generated expressions (self-corr proxy).
    # With 4500+ generated rows, defaults must be stricter than v34 early defaults.
    max_history_similarity: float = 0.88  # v50: was 0.72
    # Toxic pool: multi-fail / negative / far-below-threshold alphas — must not resemble these.
    # Toxic block only near-clones of clearly bad alphas (negative / catastrophic fails).
    max_toxic_history_similarity: float = 0.85  # v50: was 0.48 — blocked all arch_ families
    template_max_history_similarity: float = 0.82
    formulaic_max_history_similarity: float = 0.78
    prescreen_max_toxic_similarity: float = 0.85  # v50: was 0.48 — blocked all arch_ families
    # Family-specific toxic similarity walls. This remains a syntax proxy until the
    # separate daily-PnL correlation phase has a verified read-only platform endpoint.
    toxic_similarity_by_family: dict[str, float] = field(default_factory=lambda: {
        "near_pass_variant": 0.55,
        "near_pass_cross_field_variant": 0.55,
        "pass_fundamental": 0.70,
        "arch_": 0.85,
    })
    template_skip_toxic_similarity: bool = True
    prescreen_skip_toxic_for_near_pass: bool = True
    prescreen_skip_weak_for_template: bool = True
    prescreen_skip_weak_for_near_pass: bool = True
    prescreen_skip_weak_for_arch: bool = True  # v50: arch_/pass_ families skip weak_fail similarity
    prescreen_skip_novelty_for_template: bool = True
    near_pass_min_core_candidates: int = 12
    near_pass_skip_when_template_core: int = 20
    # Generate: block exact re-run of platform-tried exprs only (not whole feedback ledger).
    generate_exact_seen_from_tried: bool = True
    generate_use_toxic_similarity: bool = False
    template_skip_weak_similarity: bool = True
    generate_template_rescue: bool = True
    similarity_toxic_max_rows: int = 12000
    similarity_weak_max_rows: int = 800  # v50.1: was 2000 — 只保留最明确的失败案例
    similarity_near_pass_max_rows: int = 2500
    formulaic_primitives_enabled: bool = True
    template_skip_history_skeleton: bool = True
    timeout: int = 45
    connect_timeout: int = 90
    submit_timeout: int = 180  # v50.1: was 120
    max_poll_seconds_per_alpha: int = 720
    # Platform SELF_CORRELATION can stay PENDING for many minutes under load; 20min is often too short.
    # v50.1: 45min -> 90min — 给 SELF_CORRELATION 充分时间完成
    max_check_poll_seconds: float = 5400.0
    check_poll_interval_seconds: float = 5.0  # v50.1: 3s -> 5s — 减轻平台轮询压力
    simulate_check_poll_seconds: float = 120.0
    simulate_quality_check_poll_seconds: float = 300.0
    # Self-correlation checks can take many minutes; short quick rechecks falsely
    # leave alphas stuck in needs_recheck forever.
    recheck_quick_timeout_seconds: float = 900.0
    # Pre-batch recheck runs *before* generate/simulate and is strictly sequential (one alpha at a time).
    # v50.1: 60s -> 600s per alpha, 240s -> 1800s total wall budget — 给 check 足够时间
    recheck_prebatch_quick_timeout_seconds: float = 120.0
    recheck_prebatch_max_items: int = 10
    # v50.1: 240s -> 1800s total wall budget for pre-batch recheck
    recheck_prebatch_wall_budget_seconds: float = 600.0
    recheck_postbatch_max_items: int = 4
    recheck_postbatch_quick_timeout_seconds: float = 90.0
    recheck_postbatch_wall_budget_seconds: float = 180.0
    # Standalone ``--mode recheck`` defaults: bounded drain (loop-friendly). Use --recheck-deep for marathon.
    recheck_standalone_max_items: int = 20
    recheck_standalone_wall_budget_seconds: float = 1800.0
    recheck_standalone_quick_timeout_seconds: float = 600.0
    recheck_deep: bool = False
    # If simulation progress polling hits the wall, extend once (handles slow queues / SSL hiccups).
    simulation_poll_retry_extend_seconds: int = 420
    recheck_heartbeat_every_polls: int = 60  # v50.3: was 5 — 约每 5min 一次心跳（配合 5s poll 间隔）
    recheck_heartbeat_min_seconds: float = 120.0
    cleanup_poll_only_max_per_run: int = 20  # bounded legacy compatibility; vNext owns production flow
    cleanup_check_max_seconds: float = 15.0  # cleanup 短 check，不等待自相关（v50.6: 45→15，快速失败）
    submit_sleep: float = 1.2
    page_sleep: float = 0.15
    pre_simulate_cooldown_seconds: float = 3.0
    # Cross-process fields/datasets cache (loop cycles are separate processes).
    enable_fields_disk_cache: bool = True
    fields_disk_cache_ttl_seconds: float = 4 * 3600
    poll_fallback_sleep: float = 0.6
    poll_error_sleep: float = 2.0
    adaptive_base_sleep: float = 2.0
    adaptive_max_sleep: float = 90.0
    adaptive_backoff_factor: float = 2.0
    adaptive_recover_factor: float = 0.85
    hard_cooldown_429_count: int = 3
    hard_cooldown_seconds: float = 90.0
    submit_429_min_sleep: float = 12.0
    dns_error_pause_count: int = 5           # v50.1: was 3 — 多容忍几次 DNS 波动
    dns_error_pause_seconds: float = 300.0     # v50.1: was 180s — 暂停5分钟
    max_retries: int = 8  # v50.1: was 5 — 更多重试机会
    auth_state_file: str = ".wq_auth_state.json"
    auth_cooldown_seconds: float = 25 * 60
    auth_daily_cap: int = 5
    auth_max_retries: int = 2
    ssl_error_pause_seconds: float = 10.0
    ssl_error_rebuild_session: bool = True
    force_ipv4: bool = True
    https_proxy: str | None = None
    tls_verify: bool = True
    output_prefix: str = UNIFIED_OUTPUT_PREFIX
    feedback_ledger_filename: str = UNIFIED_FEEDBACK_CSV
    generated_expression_registry_filename: str = UNIFIED_REGISTRY_CSV
    hopeful_queue_filename: str = UNIFIED_HOPEFUL_JSONL
    submission_results_filename: str = UNIFIED_SUBMISSION_JSONL
    pipeline_version: str = PIPELINE_VERSION
    alpha_models_filename: str = "Alpha Models.csv"
    alpha_models_enabled: bool = False
    # v50.1: 6 -> 10 — 更多实例化变体
    alpha_models_instances_per_template: int = 10
    alpha_models_max_templates: int = 64
    alpha_models_candidate_score: float = 2.85
    alpha_models_batch_quota: int = 0
    near_pass_max_family_share: float = 0.75
    near_pass_only_when_short: bool = True
    near_pass_shortfall_ratio: float = 0.45
    near_pass_always_amplify: bool = True
    near_pass_amplify_cap: int = 180
    near_pass_max_seeds: int = 64
    # v50.7: was 2. This caps variants picked from _variants_for() per seed,
    # independent of max_behavior_per_seed. Needs to be >= the number of
    # cross-field swaps we want to try (near_pass_cross_field_variants_per_seed)
    # or the field-swap variants that survive validation get truncated before
    # the parameter-tweak variants even get a chance to run.
    near_pass_max_variants_per_seed: int = 4
    diversity_mode: str = "quality_diverse"
    behavior_similarity_cap: float = 0.84  # v50.6: 0.78 -> 0.84 — 缓解 throughput_behavior_block
    # v50.7: was 1. At 1, emitting the raw seed clone (base behavior) already
    # consumes the whole per-seed behavior budget, so the variant loop in
    # NearPassAmplifier.amplify() never runs — cross-field variants were dead
    # code in quality_diverse mode. Raise so the seed clone plus a handful of
    # distinct-behavior variants (cross-field swaps in particular) can all be
    # emitted per seed; the batch-level max_behavior_per_batch gate still caps
    # how many of any single behavior survive into the simulated batch.
    max_behavior_per_seed: int = 4
    max_behavior_per_batch: int = 2
    max_operator_field_per_batch: int = 1
    diverse_family_share_cap: float = 0.55
    allocator_enforce_mix_when_full: bool = True
    # Quality mix (tuned for ~5%% platform pass proxy: Sharpe>=1.24 & Fitness>=1.0)
    near_pass_batch_quota: int = 120
    # v50.7: was 0.62. At 0.62 min-share + max_arch_explore_batch_share=0.02, the
    # base preset structurally guaranteed near_pass would crowd out every other
    # family — historical feedback shows near_pass_variant at 94.8% of all
    # simulated rows vs <1% for every arch_* family combined, with 0% platform
    # pass evidence despite a 94.8% local metric-gate pass rate (pure self-corr
    # among near-clones, not real signal diversity). Lowered so near_pass stays
    # the largest single bucket without mathematically excluding exploration.
    min_near_pass_batch_share: float = 0.40
    min_template_batch_share: float = 0.0
    min_pass_quality_batch_share: float = 0.22  # v50.1: was 0.15 — 增加优质家族配额
    min_quality_batch_share: float = 0.22
    # v50.7: was 0.02 — see min_near_pass_batch_share note above. 0.02 left no
    # real room for arch_* exploration once near_pass filled its floor share,
    # which is the batch-quota half of the self-locking exploration trap.
    max_arch_explore_batch_share: float = 0.12
    batch_guard_allow_underfill: bool = True
    batch_guard_min_selected: int = 120
    batch_guard_raw_near_drop_threshold: int = 12
    near_pass_rescue_toxic_similarity: float = 0.90
    max_arch_level_liquid_per_batch: int = 4
    max_arch_delta_liquid_per_batch: int = 4
    max_arch_other_per_batch: int = 6
    # The pilot reserves an explicit shared allocation for every arch_* family.
    # A zero value preserves the legacy allocator outside diverse_exploration.
    arch_explore_batch_quota: int = 0
    prescreen_coarse_topup_quality_only: bool = True
    prescreen_near_pass_intrabatch_cap: float = 0.86
    near_pass_fine_shape_quota: int = 24  # quality_diverse: keep near-pass high quality without cloning one shape.
    feedback_clone_max: int = 96
    alpha50_filename: str = "alpha50.csv"
    save_every_n: int = 20
    enable_auto_invert_retry: bool = True
    pass_first_mode: bool = True
    exploration_ratio: float = 0.02
    # v50.1: 启用探索家族 — 增加新颖信号来源，regime_shift/pair/ts_corr 带来多样性
    enable_explore_families: bool = True
    max_operator_depth: int = 3
    max_nested_functions: int = 8
    # Log analysis: bare `group_rank(f/cap,subindustry)-0.5` → LOW_SHARPE; hybrids pass far more often.
    max_pass_fundamental_level_fields: int = 28
    # Extra poll budget when Sharpe/Fitness already PASS but SELF_CORRELATION is PENDING.
    check_self_correlation_extra_seconds: float = 3600.0
    max_queue_similarity: float = 0.72
    queue_min_returns: float = -0.05
    queue_max_drawdown: float = 0.30
    queue_min_margin: float = -0.05
    queue_prefer_low_similarity: bool = True
    min_candidates_floor: int = 300
    fallback_disable_library_skeleton_dedup: bool = True
    fallback_disable_history_skeleton_dedup: bool = True
    block_history_skeleton_always: bool = True
    block_generated_registry_exact: bool = False
    # Pre-simulate screening
    prescreen_enabled: bool = True
    prescreen_max_nesting_depth: int = 6
    prescreen_max_function_calls: int = 9
    prescreen_max_history_similarity: float = 0.82
    prescreen_near_pass_similarity: float = 0.79
    # Additional in-batch diversity guard: keep newly kept payloads from collapsing
    # into one near-duplicate cluster while still allowing batch-size relaxation.
    prescreen_intrabatch_similarity: float = 0.82
    prescreen_intrabatch_similarity_relax_step: float = 0.03
    prescreen_intrabatch_similarity_relax_ceiling: float = 0.96
    # Two-stage prescreen (v41): coarse keeps quality/history gates but skips batch-local quotas;
    # fine pass greedily picks a diverse simulate set up to min_simulate_batch.
    prescreen_two_stage: bool = True
    prescreen_coarse_skip_intrabatch: bool = True
    prescreen_coarse_skip_shape_quota: bool = True
    prescreen_coarse_relax_to_fill: bool = True
    prescreen_fine_fill_to_target: bool = True
    prescreen_fine_history_relax_step: float = 0.05   # v50.1: 0.04 -> 0.05
    prescreen_fine_intrabatch_relax_ceiling: float = 0.90
    prescreen_fine_desperate_fill: bool = False
    recheck_skip_prebatch: bool = True
    recheck_skip_postbatch: bool = False
    novelty_enabled: bool = True
    novelty_strictness: str = "balanced"  # v50.1: strict -> balanced — 降低字段重叠敏感度
    novelty_index_filename: str = NOVELTY_INDEX_JSON
    batch_diagnostics_filename: str = BATCH_DIAGNOSTICS_CSV
    feedback_diagnostics_filename: str = FEEDBACK_DIAGNOSTICS_CSV
    feedback_learning_summary_csv: str = FEEDBACK_LEARNING_SUMMARY_CSV
    feedback_learning_summary_json: str = FEEDBACK_LEARNING_SUMMARY_JSON
    feedback_check_distribution_csv: str = CHECK_DISTRIBUTION_CSV
    # v50.7: near_pass variants that only tweak parameters collide on
    # _behavior_signature (field_signature + operator skeleton) with their own
    # seed and with each other, so the batch-level throughput_behavior_block
    # gate (max_behavior_per_batch=1) throws almost all of them away before
    # simulate ever sees them. Swapping the field to a same-category sibling
    # changes the field_signature, so cross-field variants survive that gate.
    enable_near_pass_cross_field_variants: bool = True
    # Max distinct field-swap variants to build per near-pass seed (in addition
    # to the parameter-tweak variants from _variants_for).
    near_pass_cross_field_variants_per_seed: int = 3
    # v50.7: was False. top_fields() previously rewarded high-userCount fields
    # (+0.15 popularity weight), pushing candidate generation toward the same
    # crowded fields other researchers already mine — the fields most likely
    # to trip platform SELF_CORRELATION. Default now penalizes popularity
    # (-0.10) and reserves underused_field_share of the selected pool for
    # below-median-userCount fields.
    prefer_underused_fields: bool = True
    underused_field_share: float = 0.15
    batch_diagnostics_only: bool = False
    feedback_diagnostics_only: bool = False
    # Append every expression from generated registries into the prescreen similarity pool
    # (in addition to rows read from alpha_submission_feedback*.csv).
    include_generated_registry_in_similarity: bool = False  # v50: was True — 22k entries blocked all candidates
    # Cap token-sets kept for Jaccard comparisons (random reservoir) to bound CPU.
    similarity_history_max_token_rows: int = 18000
    prescreen_block_already_simulated: bool = True
    prescreen_skip_low_sharpe_cluster: bool = True
    prescreen_cluster_max_avg_sharpe: float = 0.42
    prescreen_cluster_min_samples: int = 3
    prescreen_allow_near_pass_settings_retry: bool = True
    prescreen_skip_negative_sharpe: bool = True
    prescreen_negative_sharpe_floor: float = 0.30
    # Near-pass amplifier (variants around historical near-pass alphas)
    near_pass_enabled: bool = True
    # v50.1: 0.75 -> 0.90 — 只放大确实接近平台门槛 (1.25) 的种子
    near_pass_min_sharpe: float = 0.90
    # v50.1: 1.38 -> 1.55 — 种子综合得分门槛提高，避免放大垃圾
    near_pass_seed_min_composite: float = 1.55
    near_pass_primary_decay: int = 1
    # Payload allocation for one simulation batch (near_pass_batch_quota set above with quality mix)
    # Proven hybrid (fundamental + price leg) — highest pass rate in historical hopeful queue.
    pass_hybrid_batch_quota: int = 24
    # Prefer high-prior templates so batches are not dominated by weak explores.
    robust_batch_quota: int = 0
    pass_first_batch_quota: int = 24
    delta_liquid_batch_quota: int = 8
    explore_batch_quota: int = 0
    pass_fundamental_ts_max_per_batch: int = 30
    max_same_shape_per_run: int = 6  # quality_diverse: prefer underfill over same-shape near clones
    # Async simulate (aiohttp): 0 = legacy sequential requests.
    max_concurrent_simulations: int = 6
    # POST /simulations is rate-limited; prefer smooth single-file submit.
    max_concurrent_simulation_posts: int = 1
    sqlite_runs_path: str | None = None
    # Phase 4 stays local and opt-in until its offline evidence is reviewed.
    phase4_mutation_enabled: bool = False
    phase4_repair_enabled: bool = False
    submission_observe_enabled: bool = False
    submission_observe_description_limit: int = 20
    # Phase 2/3: LLM-driven hypothesis→expression generation. Default on; disable with --no-phase2-llm / --no-phase3-llm.
    phase2_llm_enabled: bool = True
    phase3_llm_grammar_enabled: bool = True
    phase3_diversity_gate_enabled: bool = False
    # Phase 5: Submission Judge priority scoring. Opt-in.
    phase5_judge_enabled: bool = False
    # Max hypotheses to drive LLM expression generation per generate_candidates() call.
    phase23_hypotheses_per_call: int = 3
    analyze_recent_workers: int = 6
    max_low_yield_arch_generate_share: float = 0.05
    # Use historical generated expressions as a near-clone guard, not a broad weak-fail block.
    include_generated_registry_in_similarity: bool = True
    generated_near_clone_similarity: float = 0.92
    cleanup_poll_only_wall_budget_seconds: float = 300.0
    low_yield_bucket_min_count: int = 20
    low_yield_bucket_hard_cap: int = 2
    # IS ladder Sharpe pre-check (Prompt 2). Opt-in; requires extra platform simulation calls.
    ladder_check_enabled: bool = False
    # WQ platform IS ladder check: per-year minimum Sharpe for year-by-year
    # consistency check. 1.0 is the original default; the composite triage
    # filter (Sharpe>1.57 AND Fitness>1 AND Turnover 1%-70%) is enforced
    # separately in legacy_triage.filter_worth_resubmitting().
    ladder_check_min_sharpe: float = 1.0
    ladder_check_start_year: int = 2019
    ladder_check_end_year: int = 2023
    # Signal diversity: down-weight fast/short-window price signals in fine-rank (Prompt 3).
    # Set via failure_stats.compute_failure_stats() recommendation rather than guessing.
    diversity_fast_signal_penalty: float = 0.0   # 0.0 = off; positive values add rank penalty
    # Region/Universe/Neutralization exploration pools (Prompt C).
    # Flows are proportionally split: exploration_region_share of payloads go to non-primary
    # region/universe/neutralization settings.  0.0 = disabled (default, conservative).
    exploration_region_share: float = 0.0
    region_exploration_pool: list = field(default_factory=lambda: ["USA", "EUR", "ASI", "CHN", "KOR"])
    universe_exploration_pool: list = field(default_factory=lambda: ["TOP3000", "TOP1000", "TOP500"])
    neutralization_exploration_pool: list = field(default_factory=lambda: ["MARKET", "CROWDING", "REVERSION_AND_MOMENTUM"])

    def apply_preset(self) -> None:
        preset = (self.preset or "mixed").lower()
        if preset == "conservative":
            self.dataset_auto_max = min(self.dataset_auto_max, 6)
            self.field_top_n = min(self.field_top_n, 320)
            self.pair_limit = min(self.pair_limit, 30)
            self.max_family_share = 0.18
            self.exploration_ratio = min(self.exploration_ratio, 0.12)
        elif preset == "pv":
            self.dataset_ids = self.dataset_ids or ("pv1", "pv13", "pv29")
            self.field_top_n = min(self.field_top_n, 220)
            self.pair_limit = min(self.pair_limit, 20)
            self.exploration_ratio = min(self.exploration_ratio, 0.18)
        elif preset == "fundamental":
            self.dataset_ids = self.dataset_ids or ("fundamental6", "fundamental65", "fundamental2", "fundamental17")
            self.field_top_n = max(self.field_top_n, 420)
            self.exploration_ratio = min(self.exploration_ratio, 0.14)
        elif preset == "challenge":
            self.allow_universe_grid = True
            self.queue_min_sharpe = max(self.queue_min_sharpe, 1.60)
            self.queue_min_fitness = max(self.queue_min_fitness, 1.10)
            self.max_check_poll_seconds = max(self.max_check_poll_seconds, 2700.0)
            self.max_queue_similarity = min(self.max_queue_similarity, 0.88)
        elif preset == "diverse_exploration":
            # A bounded observational pilot. Keep the platform integration paths
            # unchanged and prefer an underfilled clean batch to clone relaxation.
            self.diversity_mode = "quality_diverse"
            self.min_simulate_batch = 60
            self.target_simulate_batch = 60
            self.max_simulate_batch_per_run = 60
            self.near_pass_batch_quota = 21
            self.min_near_pass_batch_share = 0.35
            self.min_pass_quality_batch_share = 0.22
            self.min_quality_batch_share = 0.22
            self.max_arch_explore_batch_share = 0.15
            self.arch_explore_batch_quota = 9
            self.max_arch_level_liquid_per_batch = 9
            self.max_arch_delta_liquid_per_batch = 9
            self.max_arch_other_per_batch = 9
            self.max_low_yield_arch_generate_share = 0.25
            self.prescreen_relax_to_hit_min_batch = False
            self.prescreen_coarse_relax_to_fill = False
            self.prescreen_fine_desperate_fill = False
            self.prescreen_allow_near_pass_settings_retry = False
        else:
            self.dataset_ids = self.dataset_ids


@dataclass
class ExpressionCandidate:
    expression: str
    family: str
    source: str
    score: float = 0.0


@dataclass
class FieldCatalog:
    df: Any
    ids: set[str]
    by_ds: dict[str, list[str]]
    fund: list[str]
    analyst: list[str]
    model: list[str]
    sent: list[str]
    pv: list[str]
    other: list[str]
    base_vars: set[str] = field(default_factory=lambda: set(BASE_VARS))
    field_dataset: dict[str, str] = field(default_factory=dict)
    field_user_count: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_df(cls, df: Any) -> "FieldCatalog":
        ids = set(df["id"].dropna().astype(str).str.strip().tolist()) if "id" in df.columns else set()
        by_ds: dict[str, list[str]] = defaultdict(list)
        fund: list[str] = []
        analyst: list[str] = []
        model: list[str] = []
        sent: list[str] = []
        pv: list[str] = []
        other: list[str] = []
        field_dataset: dict[str, str] = {}
        field_user_count: dict[str, float] = {}
        for _, row in df.iterrows():
            fid = str(row.get("id", "")).strip()
            if not fid:
                continue
            ds = str(row.get("_ds", "")).lower()
            by_ds[ds].append(fid)
            field_dataset[fid] = ds
            field_user_count[fid] = float(_to_float(row.get("userCount")) or 0.0)
            low = fid.lower()
            if "fundamental" in ds or any(x in low for x in ("sales", "revenue", "income", "profit", "ebit", "asset", "debt", "cash", "book", "capex", "inventory", "receivable", "dividend", "eps", "roe", "roa")):
                fund.append(fid)
            elif "analyst" in ds or any(x in low for x in ("analyst", "estimate", "forecast", "rating", "revision", "target")):
                analyst.append(fid)
            elif "model" in ds or low.startswith("mdl"):
                model.append(fid)
            elif any(x in ds for x in ("sentiment", "news", "social")):
                sent.append(fid)
            elif ds.startswith("pv") or fid in BASE_VARS or any(x in low for x in ("close", "volume", "vwap", "adv", "return", "open", "high", "low", "price")):
                pv.append(fid)
            else:
                other.append(fid)
        return cls(
            df=df,
            ids=ids,
            by_ds=dict(by_ds),
            fund=list(dict.fromkeys(fund)),
            analyst=list(dict.fromkeys(analyst)),
            model=list(dict.fromkeys(model)),
            sent=list(dict.fromkeys(sent)),
            pv=list(dict.fromkeys(pv)),
            other=list(dict.fromkeys(other)),
            field_dataset=field_dataset,
            field_user_count=field_user_count,
        )

    def best(self, pools: Iterable[list[str]], tokens: tuple[str, ...] = ()) -> str | None:
        fields: list[str] = []
        for pool in pools:
            fields.extend(pool)
        fields = list(dict.fromkeys([f for f in fields if f and not is_bad_field_name(f)]))
        if not fields:
            return None
        if tokens:
            scored = []
            for f in fields:
                low = f.lower()
                score = sum(1 for t in tokens if t in low)
                scored.append((score, f))
            scored.sort(key=lambda x: (x[0], field_quality_score(x[1])), reverse=True)
            if scored and scored[0][0] > 0:
                return scored[0][1]
        fields.sort(key=field_quality_score, reverse=True)
        return fields[0]

    def replacement_for(self, field_name: str) -> str | None:
        """Return a same-category field from a different dataset for phase-two variants."""
        source_ds = self.field_dataset.get(field_name, "")
        pools = (self.fund, self.analyst, self.model, self.sent, self.pv, self.other)
        pool = next((p for p in pools if field_name in p), [])
        choices = [f for f in pool if f != field_name and self.field_dataset.get(f, "") != source_ds]
        choices.sort(key=lambda f: (field_quality_score(f), -self.field_user_count.get(f, 0.0), f), reverse=True)
        return choices[0] if choices else None


def is_bad_field_name(field_name: str) -> bool:
    low = str(field_name or "").lower()
    if not low or low in BASE_VARS:
        return False
    bad_tokens = (
        "currency", "curcd", "country", "zipcode", "zip", "city", "address",
        "weburl", "url", "phone", "fax", "email", "name", "description", "desc",
        "ticker", "cusip", "isin", "sedol", "gvkey", "permno", "exchange",
        "sector_code", "industry_code", "sic", "naics", "date", "year",
        "quarter", "period", "fiscal", "calendar", "flag", "indicator",
    )
    if any(t in low for t in bad_tokens):
        return True
    return low.endswith(("_id", "_code", "_cd", "_key"))


def is_weak_fundamental_field(field_name: str) -> bool:
    """Balance-sheet line items / sparse guidance — historically negative Sharpe in bulk sims."""
    low = str(field_name or "").lower()
    if not low:
        return True
    weak_tokens = (
        "receivable", "inventory", "payable", "income_tax", "liabilit",
        "xidoc", "xrent", "txbco", "exre", "optca", "cld", "recd", "aco",
        "mrc1", "rdipa", "mrcta", "rent", "tax",
    )
    if any(t in low for t in weak_tokens):
        return True
    if "guidance" in low and not any(x in low for x in ("ebitda", "eps", "sales", "revenue", "profit")):
        return True
    return False


def field_quality_score(field_name: str) -> float:
    low = str(field_name or "").lower()
    good_tokens = (
        "sales", "revenue", "income", "profit", "ebit", "ebitda", "cashflow",
        "free_cash", "assets", "asset", "liabilities", "debt", "equity", "book",
        "margin", "expense", "capex", "accrual", "inventory", "receivable",
        "payable", "dividend", "shares", "return_equity", "operating", "eps",
    )
    score = sum(1.0 for t in good_tokens if t in low)
    if "estimate" in low or "guidance" in low:
        score += 0.5
    if low.startswith(("mdf_", "fnd6_", "fn_", "fundamental")):
        score += 0.25
    return min(score, 5.0)


# ---------------------------- preflight ----------------------------

class PreflightValidator:
    def __init__(self, catalog: FieldCatalog | None = None, *, min_ts_corr_window: int = 10):
        self.catalog = catalog
        self.min_ts_corr_window = max(1, int(min_ts_corr_window))

    def validate(self, expr: str) -> tuple[bool, str]:
        s = _sig(expr)
        if not s:
            return False, "empty"
        low = s.lower()
        if any(x in low for x in ("http://", "https://", "www.", ".com")):
            return False, "url_like_token"
        if re.search(r"(?:\b1(?:\.0+)?\s*\+\s*adv20\b|\badv20\s*\+\s*1(?:\.0+)?\b)", low):
            return False, "dimensioned_constant_addition:adv20"
        # Sparse / gated signals tend to fail CONCENTRATED_WEIGHT and LOW_SUB_UNIVERSE_SHARPE more often.
        # Keep them out of the default generator pool.
        if any(x in low for x in ("if_else(", "bucket(")):
            return False, "sparse_signal_blocked"
        if re.search(r"[<>=!]=|[<>]", low):
            return False, "conditional_operator_blocked"
        bad_funcs = sorted(fn for fn in BLOCKED_FUNCTIONS if re.search(rf"\b{re.escape(fn)}\s*\(", low))
        if bad_funcs:
            return False, "blocked_operator:" + ",".join(bad_funcs)
        short_corr = self._short_ts_corr_windows(low)
        if short_corr:
            return False, "ts_corr_window_too_short:" + ",".join(str(x) for x in short_corr)
        unknown_funcs = self._unknown_functions(low)
        if unknown_funcs:
            return False, "unknown_operator:" + ",".join(unknown_funcs[:5])
        if not self._valid_group_args(low):
            return False, "invalid_group_argument"
        unknown_ids = self._unknown_identifiers(low)
        if unknown_ids:
            return False, "unknown_variable:" + ",".join(unknown_ids[:8])
        return True, "ok"

    def _short_ts_corr_windows(self, low: str) -> list[int]:
        """Reject ts_corr(..., ..., w) when w < min_ts_corr_window (platform turnover risk)."""
        bad: list[int] = []
        token = "ts_corr("
        pos = 0
        while True:
            j = low.find(token, pos)
            if j < 0:
                break
            start = j + len(token)
            depth = 1
            k = start
            while k < len(low) and depth > 0:
                ch = low[k]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        segment = low[start:k]
                        parts: list[str] = []
                        cur: list[str] = []
                        inner = 0
                        for ch2 in segment:
                            if ch2 == "(":
                                inner += 1
                                cur.append(ch2)
                            elif ch2 == ")":
                                inner -= 1
                                cur.append(ch2)
                            elif ch2 == "," and inner == 0:
                                parts.append("".join(cur).strip())
                                cur = []
                            else:
                                cur.append(ch2)
                        if cur:
                            parts.append("".join(cur).strip())
                        if len(parts) >= 3:
                            tail = parts[2].strip()
                            m2 = re.match(r"^(\d+)", tail)
                            if m2:
                                w = int(m2.group(1))
                                if w < self.min_ts_corr_window:
                                    bad.append(w)
                        break
                k += 1
            pos = start
        return bad

    def _assigned_names(self, low: str) -> set[str]:
        return set(re.findall(r"(?:^|;)\s*([a-z_][a-z0-9_]*)\s*=", low))

    def _unknown_functions(self, low: str) -> list[str]:
        funcs = set(re.findall(r"\b([a-z_][a-z0-9_]*)\s*\(", low))
        return sorted(f for f in funcs if f not in FUNCTIONS)

    def _unknown_identifiers(self, low: str) -> list[str]:
        if self.catalog is None:
            return []
        allowed = {x.lower() for x in self.catalog.ids} | {x.lower() for x in self.catalog.base_vars}
        allowed |= FUNCTIONS | GROUPS | self._assigned_names(low)
        all_ids = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", low))
        out = []
        for ident in sorted(all_ids):
            if ident in allowed:
                continue
            if ident in {"true", "false", "nan", "inf", "rettype", "range"}:
                continue
            if len(ident) <= 2 and ident in self._assigned_names(low):
                continue
            out.append(ident)
        return out

    def _valid_group_args(self, low: str) -> bool:
        for fn in ("group_rank", "group_neutralize", "group_mean"):
            pattern = rf"{fn}\s*\(([^()]|\([^()]*\))*?,\s*([a-z_][a-z0-9_]*)\s*\)"
            for m in re.finditer(pattern, low):
                if m.group(2).lower() not in GROUPS:
                    return False
        return True


# ---------------------------- generation ----------------------------


# ══════════════════════════════════════════════════════════════════════════════
#  ExpressionFactory v48 — ground-truth-driven alpha generation
# ══════════════════════════════════════════════════════════════════════════════
#
#  ROOT CAUSE ANALYSIS of prior failure (from platform screenshot):
#  ─────────────────────────────────────────────────────────────────
#  Symptom 1 — Very few candidates (10-21 per run):
#    • _submission_quality_gate had ~15 rules that were mutually contradictory:
#      - "missing_cross_section" caught group_rank(...)-0.5 expressions
#      - "bare_ts_rank" / "bare_level_rank" regex was buggy — caught valid exprs
#      - "short_delta_noise" threshold w<10 let w=2,5 through (should be w<21)
#      - min_projected_score=2.35 eliminated 90%+ of raw candidates
#      - max_same_shape_per_run=8 choked on any family with variation
#    • _pass_first_hybrid_family generated ts_delta(close, 2/5) — platform's
#      own HIGH_TURNOVER filter then rejected all of them
#
#  Symptom 2 — Sharpe=0, negative Sharpe, no passes:
#    • ts_delta(close, 2) and ts_delta(close, 5) → HIGH_TURNOVER → 0 / negative
#    • group_rank(vwap/close, g)-0.5 with NO smoothing → HIGH_TURNOVER
#    • _formulaic_primitives_family used ts_delta(vwap, 1) and ts_delta(close, 5)
#      (window=1 and window=5!) — guaranteed HIGH_TURNOVER on TOP3000
#    • Scoring gave group_rank only 0.22 vs group_neutralize 0.40 → wrong sort
#
#  SOLUTION — Complete rewrite based on verified BRAIN passing archetypes:
#  ────────────────────────────────────────────────────────────────────────
#  1. ALL time windows ≥ 21 days (no exceptions in any family)
#  2. ALL ts_corr windows ≥ 42 days (short windows trigger HIGH_TURNOVER)
#  3. EVERY expression has group_neutralize() OR group_rank() (cross-sectional)
#  4. Quality gate has 4 rules only — no over-filtering
#  5. min_projected_score REMOVED — let prescreen handle diversity
#  6. max_same_shape_per_run raised to 40 — shape dedup was decimating candidates
#  7. 12 proven archetypes × hundreds of fields = 5000+ raw candidates per run
#
#  Verified passing archetypes (Sharpe ≥ 1.25, Fitness ≥ 1.0 on TOP3000 USA):
#    A. group_neutralize(ts_rank(f/cap, 252)-0.5, subindustry)
#    B. group_neutralize(ts_zscore(f/cap, 126), industry)
#    C. (group_rank(ts_delta(f,252)/cap, subindustry)-0.5)*rank(ts_mean(vol,63)/adv20)
#    D. group_neutralize(ts_rank(vwap/close, 126)-0.5, market)
#    E. group_neutralize(-rank(ts_corr(rank(ret),rank(vol),63)), sector)
#    F. group_neutralize(ts_zscore(f/cap,126)*rank(ts_mean(vol,63)/adv20), subindustry)
#    G. group_neutralize(ts_zscore(f/cap,63)-ts_zscore(f/cap,252), industry)
#    H. (group_rank(f/cap,subindustry)-0.5)*rank(ts_mean(vol,63)/adv20)
#    I. group_neutralize(-rank(ts_std_dev(ret,126))*rank(ts_mean(vol,63)/adv20), market)
#    J. group_neutralize(ts_zscore(f/cap,126)+ts_rank(vwap/close,126)*0.3, subindustry)
# ══════════════════════════════════════════════════════════════════════════════

class ExpressionFactory:
    """Ground-truth-driven alpha factory for WorldQuant BRAIN submission.

    Design guarantees:
    - Every expression has cross-sectional normalization
    - Every time-series window ≥ 21 days (no HIGH_TURNOVER)
    - Every ts_corr window ≥ 42 days
    - Generates 5000–20000 raw candidates per run
    - Minimal 4-rule quality gate (no over-filtering)
    """

    # ── Proven window sets ──────────────────────────────────────────────────
    _W_SLOW   = (63, 126, 252)          # fundamental / slow momentum
    _W_MED    = (42, 63, 126)           # medium-horizon
    _W_CORR   = (42, 63, 126)           # correlation (≥42 required)

    # v49 FIX — Fitness = Sharpe × sqrt(coverage)
    # subindustry → ~25-40% coverage → Fitness ≈ 0.45×Sharpe (almost never ≥1.0)
    # market      → ~90-100% coverage → Fitness ≈ 0.90×Sharpe (easiest to pass)
    # Weight market at 50%, sector at 25%, industry at 17%, subindustry at 8%
    _GROUPS   = ("market", "market", "sector", "market", "sector", "industry")
    _GROUPS_F = ("market", "sector", "market", "industry", "sector", "market")

    def __init__(self, cfg: "PipelineConfig", catalog: "FieldCatalog",
                 validator: "PreflightValidator"):
        self.cfg       = cfg
        self.catalog   = catalog
        self.validator = validator
        self.rng       = random.Random(time.time_ns() & 0xFFFFFFFF)

    # ══════════════════════════════════════════════════════════════════════
    #  Entry point
    # ══════════════════════════════════════════════════════════════════════

    def generate(
        self,
        history_seen: set[str],
        history_skeletons: set[str],
        history_pools: "HistorySimilarityPools",
        library_skeletons: set[str],
        *,
        tried_exact: set[str] | None = None,
    ) -> list["ExpressionCandidate"]:
        """Build a large, diverse, submission-ready candidate pool.

        Pipeline:
          1. Emit raw candidates from 12 proven archetypes (~5000-20000).
          2. Apply minimal 4-rule quality gate.
          3. Deduplicate (exact + history skeleton).
          4. Sort by quality score, cap to budget.
        """
        raw: list[ExpressionCandidate] = []

        # ── Archetype A: fundamental ts_rank slow momentum ────────────────
        raw.extend(self._arch_A_fundamental_ts_rank())

        # ── Archetype B: fundamental ts_zscore ───────────────────────────
        raw.extend(self._arch_B_fundamental_zscore())

        # ── Archetype C: fundamental delta × liquidity ────────────────────
        raw.extend(self._arch_C_delta_liquid())

        # ── Archetype D: fundamental level × liquidity ────────────────────
        raw.extend(self._arch_D_level_liquid())

        # ── Archetype E: regime-shift (fast_z - slow_z) ───────────────────
        raw.extend(self._arch_E_regime_shift())

        # ── Archetype F: PV slow mean-reversion ───────────────────────────
        raw.extend(self._arch_F_pv_slow())

        # ── Archetype G: volume-price divergence ──────────────────────────
        raw.extend(self._arch_G_vol_price_div())

        # ── Archetype H: return-volume correlation reversal ───────────────
        raw.extend(self._arch_H_ret_vol_corr())

        # ── Archetype I: analyst revision × liquidity ─────────────────────
        raw.extend(self._arch_I_analyst())

        # ── Archetype J: hybrid fundamental + PV leg ─────────────────────
        raw.extend(self._arch_J_hybrid())

        # ── Archetype K: vol-scaled fundamental ──────────────────────────
        raw.extend(self._arch_K_vol_scaled())

        # ── Archetype L: multi-field fundamental spread ───────────────────
        raw.extend(self._arch_L_multi_field())

        # ── External: Alpha Models.csv templates ──────────────────────────
        if self.cfg.alpha_models_enabled:
            raw.extend(self._alpha_models_template_family())

        raw = self._cap_low_yield_arch_candidates(raw)

        tried = tried_exact if tried_exact is not None else history_seen
        out, reject_counts = self._screen_candidates(
            raw, history_seen, history_skeletons, history_pools,
            library_skeletons, tried_exact=tried,
            use_library_skeleton_dedup=self.cfg.dedup_against_library_skeleton,
        )

        floor = max(1, min(
            int(self.cfg.min_candidates_floor),
            max(1, int(self.cfg.budget * self.cfg.candidate_multiplier)),
        ))

        # Fallback 1: relax library skeleton dedup
        if len(out) < floor and self.cfg.fallback_disable_library_skeleton_dedup:
            print(f"[generate] fallback-1: relax library_skeleton_dedup (kept={len(out)} < floor={floor})")
            out, reject_counts = self._screen_candidates(
                raw, history_seen, history_skeletons, history_pools,
                library_skeletons, tried_exact=tried,
                use_library_skeleton_dedup=False,
            )

        # Fallback 2: relax history skeleton dedup
        if len(out) < floor and self.cfg.fallback_disable_history_skeleton_dedup:
            print(f"[generate] fallback-2: relax history_skeleton_dedup (kept={len(out)} < floor={floor})")
            out, reject_counts = self._screen_candidates(
                raw, history_seen, set(), history_pools,
                library_skeletons, tried_exact=tried,
                use_library_skeleton_dedup=False,
                skip_history_skeleton=True,
            )

        # Fallback 3: template rescue (syntax-only gate)
        if len(out) < max(12, floor // 25) and bool(getattr(self.cfg, "generate_template_rescue", True)):
            rescued, rescue_rejects = self._rescue_template_candidates(raw, tried)
            if rescued:
                print(f"[generate] template_rescue kept={len(rescued)}")
                existing = {c.expression for c in out}
                out = rescued + [c for c in out if c.expression not in existing]
                reject_counts.update(rescue_rejects)

        if reject_counts:
            print(f"[generate] raw={len(raw)} kept={len(out)} top_rejects: "
                  + ", ".join(f"{k}:{v}" for k, v in reject_counts.most_common(8)))
        else:
            print(f"[generate] raw={len(raw)} kept={len(out)}")

        out.sort(key=self._priority_key)
        budget = max(self.cfg.budget * self.cfg.candidate_multiplier, self.cfg.budget)
        return out[:budget]

    _LOW_YIELD_ARCH_PREFIXES = ("arch_ts_rank", "arch_zscore", "arch_regime")

    def _cap_low_yield_arch_candidates(self, raw: list["ExpressionCandidate"]) -> list["ExpressionCandidate"]:
        """Cap ts_rank/zscore/regime flood so generate pool leaves room for pass_fundamental/template."""
        max_share = float(getattr(self.cfg, "max_low_yield_arch_generate_share", 0.15))
        if max_share <= 0 or not raw:
            return raw
        arch: list[ExpressionCandidate] = []
        other: list[ExpressionCandidate] = []
        for c in raw:
            fam = (c.family or "").lower()
            if any(fam.startswith(p) for p in self._LOW_YIELD_ARCH_PREFIXES):
                arch.append(c)
            else:
                other.append(c)
        cap = max(1, int(len(raw) * max_share))
        if len(arch) > cap:
            arch.sort(key=self._priority_key, reverse=True)
            dropped = len(arch) - cap
            arch = arch[:cap]
            print(f"[generate] arch_ts_rank/zscore/regime capped {dropped} -> keep {len(arch)} (max {max_share:.0%} of raw)")
        merged = arch + other
        merged.sort(key=self._priority_key, reverse=True)
        return merged

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype A — fundamental ts_rank slow momentum
    # ══════════════════════════════════════════════════════════════════════

    def _arch_A_fundamental_ts_rank(self) -> list["ExpressionCandidate"]:
        """group_neutralize(ts_rank(f/cap, W)-0.5, G)

        Slow fundamental momentum. Sharpe 0.9–1.8, Turnover ~3–12%.
        Verified pass rate ~35% on BRAIN TOP3000 USA Delay-1.

        v49 FIX: Explicitly generate market group for ALL fields (not just 25%).
        market-level → coverage=100% → Fitness≈Sharpe → much easier to pass threshold.
        """
        out: list[ExpressionCandidate] = []
        funds = self._top_funds(320)
        for i, f in enumerate(funds):
            qs = field_quality_score(f)
            g  = self._g(i)   # now weighted 50% market
            for w in self._W_SLOW:
                sc = 3.2 + qs + self._wb(w)
                # Always emit a market-level variant — highest Fitness
                self._add(out, f"group_neutralize(ts_rank({f}/cap,{w})-0.5,market)",
                          "arch_ts_rank", "proven", sc + 0.3)
                # Also emit the cycled-group variant for diversity
                self._add(out, f"group_neutralize(ts_rank({f}/cap,{w})-0.5,{g})",
                          "arch_ts_rank", "proven", sc)
                # Sector variant (coverage=60-80%)
                self._add(out, f"group_neutralize(ts_rank({f}/cap,{w})-0.5,sector)",
                          "arch_ts_rank", "proven", sc + 0.15)
            # group_rank — market and sector explicit
            self._add(out, f"group_rank({f}/cap,market)-0.5",
                      "arch_ts_rank_gr", "proven", 3.1 + qs)
            self._add(out, f"group_rank({f}/cap,sector)-0.5",
                      "arch_ts_rank_gr", "proven", 2.95 + qs)
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype B — fundamental ts_zscore
    # ══════════════════════════════════════════════════════════════════════

    def _arch_B_fundamental_zscore(self) -> list["ExpressionCandidate"]:
        """group_neutralize(ts_zscore(f/cap, W), G)

        Z-score removes look-ahead level effects. Turnover ~5–15%.

        v49 FIX: Always generate market group — ts_zscore at market level
        gives Fitness≈Sharpe and has verified pass rates on BRAIN.
        """
        out: list[ExpressionCandidate] = []
        funds = self._top_funds(280)
        for i, f in enumerate(funds):
            qs = field_quality_score(f)
            g  = self._g(i)
            for w in self._W_SLOW:
                sc = 3.0 + qs + self._wb(w)
                # Market — always, highest Fitness
                self._add(out, f"group_neutralize(ts_zscore({f}/cap,{w}),market)",
                          "arch_zscore", "proven", sc + 0.3)
                # Sector — good coverage
                self._add(out, f"group_neutralize(ts_zscore({f}/cap,{w}),sector)",
                          "arch_zscore", "proven", sc + 0.15)
                # Cycled group for diversity
                self._add(out, f"group_neutralize(ts_zscore({f}/cap,{w}),{g})",
                          "arch_zscore", "proven", sc)
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype C — fundamental delta × liquidity
    # ══════════════════════════════════════════════════════════════════════

    def _arch_C_delta_liquid(self) -> list["ExpressionCandidate"]:
        """(group_rank(ts_delta(f,W)/cap, G)-0.5) * rank(ts_mean(vol,V)/adv20)

        YoY change × liquidity — highest historical pass rate (40–55%).
        Turnover ~8–20%, Sharpe 1.1–2.0.

        v49 FIX: Explicitly generate market and sector variants for every field.
        These have coverage=90-100%, making Fitness≈Sharpe → easiest to pass.
        """
        out: list[ExpressionCandidate] = []
        vol = self._pv("volume")
        adv = self._pv("adv20")
        funds = self._top_funds(140)
        for i, f in enumerate(funds):
            qs = field_quality_score(f)
            for dw in (126, 252):
                sc = 3.6 + qs + (0.2 if dw == 252 else 0.0)
                # ── MARKET group (coverage=100%, Fitness≈Sharpe) ── priority
                self._add(
                    out,
                    f"(group_rank(ts_delta({f},{dw})/cap,market)-0.5)*rank(ts_mean({vol},63)/{adv})",
                    "arch_delta_liquid", "proven", sc + 0.3,
                )
                self._add(
                    out,
                    f"group_neutralize(ts_zscore(ts_delta({f},{dw})/cap,126)*rank(ts_mean({vol},63)/{adv}),market)",
                    "arch_delta_liquid", "proven", sc + 0.35,
                )
                self._add(
                    out,
                    f"group_neutralize(ts_rank(ts_delta({f},{dw})/cap,126)-0.5,market)",
                    "arch_delta_ts_rank", "proven", sc + 0.2,
                )
                # ── SECTOR group (coverage=60-80%) ──
                self._add(
                    out,
                    f"(group_rank(ts_delta({f},{dw})/cap,sector)-0.5)*rank(ts_mean({vol},63)/{adv})",
                    "arch_delta_liquid", "proven", sc + 0.15,
                )
                self._add(
                    out,
                    f"group_neutralize(ts_zscore(ts_delta({f},{dw})/cap,126)*rank(ts_mean({vol},63)/{adv}),sector)",
                    "arch_delta_liquid", "proven", sc + 0.20,
                )
                # ── INDUSTRY group (coverage=40-60%) ──
                g_ind = self._g(i)  # cycles through market/sector/industry now
                self._add(
                    out,
                    f"(group_rank(ts_delta({f},{dw})/cap,industry)-0.5)*rank(ts_mean({vol},63)/{adv})",
                    "arch_delta_liquid", "proven", sc + 0.05,
                )
                self._add(
                    out,
                    f"group_neutralize(ts_rank(ts_delta({f},{dw})/cap,126)-0.5,{g_ind})",
                    "arch_delta_ts_rank", "proven", sc,
                )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype D — fundamental level × liquidity
    # ══════════════════════════════════════════════════════════════════════

    def _arch_D_level_liquid(self) -> list["ExpressionCandidate"]:
        """group_neutralize(ts_zscore(f/cap,W) * rank(ts_mean(vol,V)/adv20), G)

        Level signal × volume participation — reduces CONCENTRATED_WEIGHT failures.
        """
        out: list[ExpressionCandidate] = []
        vol = self._pv("volume")
        adv = self._pv("adv20")
        funds = self._top_funds(100)
        for i, f in enumerate(funds):
            qs = field_quality_score(f)
            g  = self._g(i)
            gf = self._gf(i)
            for zw in (126, 252):
                for vw in (63, 126):
                    sc = 3.3 + qs
                    self._add(
                        out,
                        f"group_neutralize(ts_zscore({f}/cap,{zw})*rank(ts_mean({vol},{vw})/{adv}),{g})",
                        "arch_level_liquid", "proven", sc,
                    )
            # simple: group_rank × volume liquidity
            self._add(
                out,
                f"(group_rank({f}/cap,{gf})-0.5)*rank(ts_mean({vol},63)/{adv})",
                "arch_level_liquid", "proven", 3.1 + qs,
            )
            self._add(
                out,
                f"(group_rank({f}/cap,{gf})-0.5)*rank({vol}/{adv})",
                "arch_level_liquid", "proven", 2.9 + qs,
            )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype E — regime-shift (fast_z - slow_z)
    # ══════════════════════════════════════════════════════════════════════

    def _arch_E_regime_shift(self) -> list["ExpressionCandidate"]:
        """group_neutralize(ts_zscore(f/cap,W1)-ts_zscore(f/cap,W2), G)

        Cross-window spread captures acceleration in fundamentals.
        """
        out: list[ExpressionCandidate] = []
        vol = self._pv("volume")
        adv = self._pv("adv20")
        funds = self._top_funds(200)
        pairs = [(63, 252), (63, 126), (126, 252)]
        for i, f in enumerate(funds):
            qs = field_quality_score(f)
            g  = self._g(i)
            for wf, ws in pairs:
                sc = 3.2 + qs
                self._add(
                    out,
                    f"group_neutralize(ts_zscore({f}/cap,{wf})-ts_zscore({f}/cap,{ws}),{g})",
                    "arch_regime_shift", "proven", sc,
                )
                # Liquidity-weighted
                self._add(
                    out,
                    f"group_neutralize((ts_zscore({f}/cap,{wf})-ts_zscore({f}/cap,{ws}))*rank(ts_mean({vol},63)/{adv}),{g})",
                    "arch_regime_shift", "proven", sc + 0.25,
                )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype F — PV slow mean-reversion
    # ══════════════════════════════════════════════════════════════════════

    def _arch_F_pv_slow(self) -> list["ExpressionCandidate"]:
        """Slow price/VWAP signals — windows ≥ 42 ONLY (no HIGH_TURNOVER).

        Verified passes on BRAIN: vwap/close ts_rank ≥ 63 days.
        """
        out: list[ExpressionCandidate] = []
        close = self._pv("close")
        vwap  = self._pv("vwap")
        vol   = self._pv("volume")
        adv   = self._pv("adv20")
        ret   = self._pv("returns")

        for g in self._GROUPS:
            sb = 2.7 + (0.3 if g == "market" else 0.2 if g == "sector" else 0.1 if g == "industry" else 0.0)
            for w in (63, 126, 252):
                sc = sb + self._wb(w)
                # Core: slow vwap/close ts_rank mean-reversion
                self._add(out, f"group_neutralize(ts_rank({vwap}/{close},{w})-0.5,{g})",
                          "arch_pv_slow", "proven", sc)
                # ts_decay_linear of zscore — smoother variant
                dw = min(w, 63)
                self._add(out, f"group_neutralize(ts_decay_linear(zscore({vwap}/{close}),{dw}),{g})",
                          "arch_pv_slow", "proven", sc - 0.05)
            # Slow close-to-mean deviation
            for w in (63, 126):
                self._add(out, f"group_neutralize(ts_rank(ts_mean({close},21)/{close},{w})-0.5,{g})",
                          "arch_pv_slow", "proven", sb)
            # Volume surge × slow price reversion
            for vw in (42, 63, 126):
                for pw in (63, 126):
                    self._add(
                        out,
                        f"group_neutralize(rank(ts_mean({vol},{vw})/{adv})*-rank(ts_delta({close},{pw})),{g})",
                        "arch_pv_vol", "proven", sb + 0.1,
                    )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype G — volume-price divergence
    # ══════════════════════════════════════════════════════════════════════

    def _arch_G_vol_price_div(self) -> list["ExpressionCandidate"]:
        """Volume trend diverges from price trend — long-window only."""
        out: list[ExpressionCandidate] = []
        close = self._pv("close")
        vol   = self._pv("volume")
        adv   = self._pv("adv20")
        vwap  = self._pv("vwap")

        for g in self._GROUPS:
            sb = 2.5 + (0.2 if g in ("market", "sector") else 0.0)
            for vw in (42, 63, 126):
                for pw in (63, 126):
                    self._add(
                        out,
                        f"group_neutralize(ts_rank(ts_mean({vol},{vw})/{adv},{pw})*-rank(ts_delta({close},{pw})),{g})",
                        "arch_vol_price_div", "proven", sb,
                    )
            for w in (63, 126):
                self._add(
                    out,
                    f"group_neutralize(ts_rank(({vwap}-{close})/{close},{w})-0.5,{g})",
                    "arch_vwap_dev", "proven", sb + 0.1,
                )
                self._add(
                    out,
                    f"group_neutralize(rank(({vwap}-{close})/{close})*rank(ts_mean({vol},{w})/{adv}),{g})",
                    "arch_vwap_vol", "proven", sb + 0.15,
                )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype H — return-volume correlation reversal
    # ══════════════════════════════════════════════════════════════════════

    def _arch_H_ret_vol_corr(self) -> list["ExpressionCandidate"]:
        """group_neutralize(-rank(ts_corr(rank(ret),rank(vol),W)),G)

        All windows ≥ 42. Verified pass on BRAIN (Sharpe ~1.0–1.6).
        """
        out: list[ExpressionCandidate] = []
        vol = self._pv("volume")
        ret = self._pv("returns")
        adv = self._pv("adv20")

        for g in self._GROUPS:
            sb = 2.6 + (0.2 if g in ("market", "sector") else 0.0)
            for w in self._W_CORR:
                sc = sb + self._wb(w)
                self._add(out, f"group_neutralize(-rank(ts_corr(rank({ret}),rank({vol}),{w})),{g})",
                          "arch_ret_vol_corr", "proven", sc)
                self._add(out, f"group_neutralize(-rank(ts_corr(abs({ret}),{vol},{w})),{g})",
                          "arch_ret_vol_corr", "proven", sc - 0.1)
                # Low vol tilt
                self._add(
                    out,
                    f"group_neutralize(-rank(ts_std_dev({ret},{w}))*rank(ts_mean({vol},63)/{adv}),{g})",
                    "arch_vol_liq", "proven", sc - 0.05,
                )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype I — analyst revision × liquidity
    # ══════════════════════════════════════════════════════════════════════

    def _arch_I_analyst(self) -> list["ExpressionCandidate"]:
        """Analyst estimate revisions — sparse but high information content."""
        out: list[ExpressionCandidate] = []
        vol   = self._pv("volume")
        adv   = self._pv("adv20")
        vwap  = self._pv("vwap")
        close = self._pv("close")

        fields = [
            f for f in (self.catalog.analyst + self.catalog.model)[:200]
            if not is_bad_field_name(f) and not is_weak_fundamental_field(f)
        ]
        fields = list(dict.fromkeys(fields))
        fields.sort(key=field_quality_score, reverse=True)

        for i, f in enumerate(fields[:80]):
            qs = field_quality_score(f)
            g  = self._g(i)
            gf = self._gf(i)
            sc = 2.9 + qs
            for w in (63, 126):
                self._add(out, f"group_neutralize(ts_zscore({f},{w}),{g})",
                          "arch_analyst", "proven", sc)
                self._add(
                    out,
                    f"group_neutralize(ts_zscore({f},{w})*rank(ts_mean({vol},63)/{adv}),{gf})",
                    "arch_analyst_liquid", "proven", sc + 0.3,
                )
            self._add(out, f"group_neutralize(rank(ts_delta({f},63)),{g})",
                      "arch_analyst_delta", "proven", sc + 0.1)
            self._add(out, f"group_neutralize(rank(ts_delta({f},126)),{g})",
                      "arch_analyst_delta", "proven", sc + 0.15)
            self._add(out, f"group_neutralize(ts_rank({f},126)-0.5,{g})",
                      "arch_analyst_ts", "proven", sc - 0.05)
            if i < 30:
                self._add(
                    out,
                    f"group_neutralize(ts_zscore({f},63)+ts_decay_linear(zscore({vwap}/{close}),42)*0.3,{gf})",
                    "arch_analyst_hybrid", "proven", sc + 0.4,
                )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype J — hybrid fundamental + PV
    # ══════════════════════════════════════════════════════════════════════

    def _arch_J_hybrid(self) -> list["ExpressionCandidate"]:
        """Combines slow fundamental z-score with a PV leg (window ≥ 42 only).

        No ts_delta(close, w<42) anywhere in this family.
        """
        out: list[ExpressionCandidate] = []
        vwap  = self._pv("vwap")
        close = self._pv("close")
        vol   = self._pv("volume")
        adv   = self._pv("adv20")
        ret   = self._pv("returns")

        funds = self._top_funds(150)
        for i, f in enumerate(funds):
            qs = field_quality_score(f)
            g  = self._g(i)
            gf = self._gf(i)
            sc = 3.6 + qs

            fz  = f"ts_zscore({f}/cap,126)"
            fr  = f"group_rank({f}/cap,{gf})-0.5"
            fdr = f"group_rank(ts_delta({f},252)/cap,{gf})-0.5"
            pv  = f"ts_rank({vwap}/{close},126)-0.5"
            liq = f"rank(ts_mean({vol},63)/{adv})"

            # Fund z + slow vwap reversion
            self._add(out, f"group_neutralize({fz}+({pv})*0.3,{g})",
                      "arch_hybrid_z_pv", "proven", sc)
            # Fund rank × liquidity
            self._add(out, f"({fr})*{liq}",
                      "arch_hybrid_level_liq", "proven", sc + 0.1)
            # Fund delta × slow PV
            if i < 60:
                self._add(out, f"({fdr})*0.7+({pv})*0.3",
                          "arch_hybrid_delta_pv", "proven", sc + 0.2)
                self._add(out, f"group_neutralize({fz}*{liq},{g})",
                          "arch_hybrid_z_liq", "proven", sc + 0.15)
            # Fund × low-vol tilt
            if i < 40:
                self._add(
                    out,
                    f"group_neutralize({fz}*-rank(ts_std_dev({ret},126)),{g})",
                    "arch_hybrid_lowvol", "proven", sc,
                )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype K — volatility-scaled fundamental
    # ══════════════════════════════════════════════════════════════════════

    def _arch_K_vol_scaled(self) -> list["ExpressionCandidate"]:
        """Fundamental signal / realized vol — prefers low-vol quality stocks."""
        out: list[ExpressionCandidate] = []
        ret  = self._pv("returns")
        vol  = self._pv("volume")
        adv  = self._pv("adv20")
        funds = self._top_funds(120)
        for i, f in enumerate(funds):
            qs = field_quality_score(f)
            g  = self._g(i)
            sc = 3.0 + qs
            self._add(
                out,
                f"group_neutralize(ts_zscore({f}/cap,126)*-rank(ts_std_dev({ret},126)),{g})",
                "arch_vol_scaled", "proven", sc,
            )
            self._add(
                out,
                f"group_neutralize((group_rank({f}/cap,{g})-0.5)*-rank(ts_std_dev({ret},126)),{g})",
                "arch_vol_scaled_rank", "proven", sc - 0.1,
            )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Archetype L — multi-field spread (quality vs growth)
    # ══════════════════════════════════════════════════════════════════════

    def _arch_L_multi_field(self) -> list["ExpressionCandidate"]:
        """Spread between two complementary fundamental z-scores.

        Removes common macro factor, leaves idiosyncratic quality-vs-growth signal.
        """
        out: list[ExpressionCandidate] = []
        q_tok = ("profit", "income", "margin", "cashflow", "ebitda", "roe", "roa", "eps")
        g_tok = ("revenue", "sales", "asset", "debt", "capex", "equity")
        q_flds = sorted(
            [f for f in self.catalog.fund[:200]
             if not is_bad_field_name(f) and not is_weak_fundamental_field(f)
             and any(t in f.lower() for t in q_tok)],
            key=field_quality_score, reverse=True,
        )
        g_flds = sorted(
            [f for f in self.catalog.fund[:200]
             if not is_bad_field_name(f) and not is_weak_fundamental_field(f)
             and any(t in f.lower() for t in g_tok)],
            key=field_quality_score, reverse=True,
        )
        limit = min(40, self.cfg.pair_limit // 2 if hasattr(self.cfg, "pair_limit") else 40)
        for i, (f1, f2) in enumerate(zip(q_flds[:limit], g_flds[:limit])):
            if f1 == f2:
                continue
            g  = self._GROUPS[i % len(self._GROUPS)]
            sc = 2.8 + (field_quality_score(f1) + field_quality_score(f2)) / 2
            self._add(
                out,
                f"group_neutralize(ts_zscore({f1}/cap,126)-ts_zscore({f2}/cap,126),{g})",
                "arch_spread", "proven", sc,
            )
            self._add(
                out,
                f"group_neutralize(ts_rank({f1}/cap,126)-ts_rank({f2}/cap,126),{g})",
                "arch_spread", "proven", sc - 0.1,
            )
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Alpha Models.csv template instantiation
    # ══════════════════════════════════════════════════════════════════════

    def _alpha_models_template_family(self) -> list["ExpressionCandidate"]:
        path = Path(__file__).resolve().parent / self.cfg.alpha_models_filename
        if not path.is_file():
            return []
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except Exception as e:
            print(f"[templates] read failed: {e}")
            return []
        skeletons = re.findall(r"alpha\s*=\s*(.+?)(?=\n\s*\d+\)|\Z)", text, flags=re.I | re.S)
        max_t = max(1, int(self.cfg.alpha_models_max_templates))
        per_t = max(1, int(self.cfg.alpha_models_instances_per_template))
        score = float(self.cfg.alpha_models_candidate_score)
        out: list[ExpressionCandidate] = []
        seen: set[str] = set()
        for skel in skeletons[:max_t]:
            skel = _sig(skel).rstrip(";")
            if not skel:
                continue
            got = 0
            for _ in range(per_t * 4):
                if got >= per_t:
                    break
                mapped = self._map_placeholders(skel)
                if not mapped or mapped in seen:
                    continue
                seen.add(mapped)
                self._add(out, mapped, "alpha_models_template", "Alpha Models.csv", score)
                got += 1
        print(f"[templates] {path.name} skeletons={len(skeletons)} instantiated={len(out)}")
        return out

    # ══════════════════════════════════════════════════════════════════════
    #  Screening — minimal 4-rule gate, no over-filtering
    # ══════════════════════════════════════════════════════════════════════

    def _screen_candidates(
        self,
        raw: list["ExpressionCandidate"],
        history_seen: set[str],
        history_skeletons: set[str],
        history_pools: "HistorySimilarityPools",
        library_skeletons: set[str],
        *,
        tried_exact: set[str] | None = None,
        use_library_skeleton_dedup: bool,
        skip_history_skeleton: bool = False,
    ) -> tuple[list["ExpressionCandidate"], "Counter[str]"]:
        """Minimal screening — only exact dedup + 4-rule quality gate.

        Removed vs v46:
        ✗ min_projected_score (was 2.35 — eliminated 90% of valid candidates)
        ✗ max_same_shape_per_run=8 (shape dedup is prescreen's job)
        ✗ non_pass_first_family filter (all our families are proven now)
        ✗ complex cross-condition filtering

        Kept:
        ✓ exact duplicate within run
        ✓ already tried exact
        ✓ history skeleton (optional)
        ✓ library skeleton (optional)
        ✓ toxic pool similarity (optional)
        ✓ validator (syntax, blocked ops)
        ✓ minimal 4-rule quality gate
        """
        out: list[ExpressionCandidate] = []
        seen: set[str] = set()
        structure_counts: Counter[str] = Counter()  # v50.1: 结构预算计数器
        reject_counts: Counter[str] = Counter()
        tried = tried_exact if tried_exact is not None else history_seen
        use_toxic = bool(getattr(self.cfg, "generate_use_toxic_similarity", False))
        use_hist_skel = self.cfg.block_history_skeleton_always and not skip_history_skeleton

        for c in raw:
            expr = _sig(c.expression)
            if not expr:
                reject_counts["empty"] += 1
                continue
            if expr in seen:
                reject_counts["duplicate_in_run"] += 1
                continue
            if expr in tried:
                reject_counts["already_tried_exact"] += 1
                continue

            fam_low = (c.family or "").lower()
            template_like = (
                fam_low.startswith("alpha_models_template")
                or fam_low.startswith("formulaic_")
                or fam_low == "external_template"
            )

            if use_hist_skel and not template_like:
                if _skel(expr) in history_skeletons:
                    reject_counts["history_skeleton_seen"] += 1
                    continue

            if use_library_skeleton_dedup and not template_like:
                if _skel(expr) in library_skeletons:
                    reject_counts["library_skeleton_seen"] += 1
                    continue

            if use_toxic:
                toxic_cap = float(self.cfg.max_toxic_history_similarity)
                if history_pools.max_similarity(expr, "toxic", early_exit_at=toxic_cap) >= toxic_cap:
                    reject_counts["toxic_history_similarity"] += 1
                    continue

            # ── Minimal quality gate (4 rules) ────────────────────────
            ok, note = self._quality_gate(expr)
            if not ok:
                reject_counts[f"gate_{note}"] += 1
                continue

            # ── Validator (syntax, blocked ops, unknown ids) ───────────
            ok2, note2 = self.validator.validate(expr)
            if not ok2:
                reject_counts[f"val_{note2}"] += 1
                continue

            # ── v50.1: Structure budget — 每结构每家族限制数量 ──────────
            struct = _operator_skeleton(expr)
            fam_key = (c.family or "unknown").lower()
            struct_budget_key = f"{fam_key}::{struct}"
            max_per_structure = int(getattr(self.cfg, "max_same_shape_per_run", 16))
            if structure_counts[struct_budget_key] >= max_per_structure:
                reject_counts["structure_budget_exceeded"] += 1
                continue

            c.expression = expr
            c.score      = c.score + self._score_expression(c)
            seen.add(expr)
            structure_counts[struct_budget_key] += 1
            out.append(c)

        return out, reject_counts

    def _quality_gate(self, expr: str) -> tuple[bool, str]:
        """4-rule minimal gate — only rejects provably broken expressions.

        Rule 1: Must have cross-sectional operator.
        Rule 2: Must have at least one time-series operator.
        Rule 3: ts_corr window ≥ 21 days.
        Rule 4: ts_rank window ≥ 21 days.

        (Short close-delta rejection is handled inside each archetype by design.)
        """
        low = expr.lower()

        # Rule 1: cross-sectional normalization required
        if not any(t in low for t in ("group_neutralize(", "group_rank(")):
            return False, "no_cross_section"

        # Rule 2: time-series stabilizer required
        if not any(t in low for t in (
            "ts_mean(", "ts_zscore(", "ts_rank(", "ts_decay_linear(",
            "ts_delta(", "ts_corr(", "ts_std_dev(", "zscore(",
        )):
            return False, "no_ts_operator"

        # Rule 3: ts_corr minimum window ≥ 21
        for ws in re.findall(r"ts_corr\([^,]+,\s*[^,]+,\s*(\d+)\)", low):
            if int(ws) < 21:
                return False, "ts_corr_window_too_short"

        # Rule 4: ts_rank minimum window ≥ 21
        for ws in re.findall(r"ts_rank\([^,]+,\s*(\d+)\)", low):
            if int(ws) < 21:
                return False, "ts_rank_window_too_short"

        return True, "ok"

    # ══════════════════════════════════════════════════════════════════════
    #  Scoring — used for sorting priority only
    # ══════════════════════════════════════════════════════════════════════

    def _score_expression(self, c: "ExpressionCandidate") -> float:
        score = 0.0
        low   = c.expression.lower()

        # Time-series operators — reward stable, slow signals
        if "ts_zscore("     in low: score += 1.0
        if "ts_rank("       in low: score += 0.8
        if "ts_mean("       in low: score += 0.7
        if "ts_decay_linear(" in low: score += 0.5
        if "ts_delta("      in low: score += 0.4
        if "ts_corr("       in low: score += 0.6
        if "ts_std_dev("    in low: score += 0.5

        # Cross-sectional — both group_neutralize and group_rank are equally valid
        if "group_neutralize(" in low: score += 0.5
        if "group_rank("       in low: score += 0.5

        # Liquidity filter — key for passing CONCENTRATED_WEIGHT check
        if "adv20" in low and "ts_mean(volume" in low: score += 1.2
        elif "adv20"                           in low: score += 0.6

        # Broader group → better sub-universe coverage
        if ", market)"      in low: score += 0.5
        elif ", sector)"    in low: score += 0.3
        elif ", industry)"  in low: score += 0.15

        # Long windows → stable, low-turnover
        for ws in re.findall(r"\b(\d+)\b", low):
            try:
                w = int(ws)
                if   w >= 252: score += 0.40
                elif w >= 126: score += 0.25
                elif w >= 63:  score += 0.10
                elif w <  21:  score -= 0.40
            except ValueError:
                pass

        # Source bonus
        if c.source == "proven":              score += 1.5
        if c.family.startswith("alpha_models_template"): score += 0.5

        # Feedback: near_pass / pass_fundamental pass; arch_*_liquid rarely passes IS checks.
        if c.family.startswith("near_pass"):            score += 0.6
        if c.family.startswith("pass_fundamental"):     score += 0.45
        if c.family.startswith("arch_hybrid"):          score += 0.35
        if c.family.startswith("arch_analyst"):         score += 0.25
        if c.family.startswith("arch_regime_shift"):    score += 0.2

        # ── v50.1: Token diversity bonus — 奖励新颖多样的表达式 ──────────
        fields = _expression_fields(c.expression)
        unique_fields = len(set(fields))
        # 多因子表达式 > 单因子 — 天然低自相关
        if unique_fields >= 4:
            score += 1.2
        elif unique_fields >= 3:
            score += 0.7
        elif unique_fields >= 2:
            score += 0.3
        # ts_corr / ts_regression 天然与其他 alpha 低相关
        if "ts_corr(" in low:
            score += 0.8
        if "ts_regression(" in low:
            score += 1.0
        # 使用稀有字段（不在历史常见 top 字段中）的 alpha 更不容易自相关
        top_fields = getattr(self, "_top_field_in_expression", set())
        if top_fields and fields:
            novel_fields = [f for f in fields if f not in top_fields]
            score += len(novel_fields) * 0.15

        return score

    def _priority_key(self, c: "ExpressionCandidate") -> tuple[int, float, int]:
        tier = _batch_priority_tier((c.family or "").lower(), (c.source or "").lower())
        return (tier, -float(c.score), len(c.expression))

    # ══════════════════════════════════════════════════════════════════════
    #  Template rescue (fallback — syntax only)
    # ══════════════════════════════════════════════════════════════════════

    def _rescue_template_candidates(
        self,
        raw: list["ExpressionCandidate"],
        tried_exact: set[str],
    ) -> tuple[list["ExpressionCandidate"], "Counter[str]"]:
        out: list[ExpressionCandidate] = []
        reject_counts: Counter[str] = Counter()
        seen: set[str] = set()
        for c in raw:
            fl = (c.family or "").lower()
            if not (fl.startswith("alpha_models_template") or fl.startswith("formulaic_") or fl == "external_template"):
                continue
            expr = _sig(c.expression)
            if not expr or expr in seen or expr in tried_exact:
                continue
            ok, note = self._quality_gate(expr)
            if not ok:
                reject_counts[f"rescue_gate_{note}"] += 1
                continue
            ok2, note2 = self.validator.validate(expr)
            if not ok2:
                reject_counts[f"rescue_val_{note2}"] += 1
                continue
            c.expression = expr
            c.score      = c.score + self._score_expression(c)
            seen.add(expr)
            out.append(c)
        return out, reject_counts

    # ══════════════════════════════════════════════════════════════════════
    #  Helpers
    # ══════════════════════════════════════════════════════════════════════

    def _pv(self, name: str) -> str:
        low_map = {f.lower(): f for f in self.catalog.pv}
        return low_map.get(name, name)

    def _top_funds(self, limit: int) -> list[str]:
        fields = [
            f for f in self.catalog.fund[: limit * 2]
            if not is_bad_field_name(f)
            and not is_weak_fundamental_field(f)
            and f.lower() != "cap"
        ]
        fields = list(dict.fromkeys(fields))
        fields.sort(key=field_quality_score, reverse=True)
        return fields[:limit]

    def _g(self, idx: int) -> str:
        """Cycle through all 4 groups — broad neutralization."""
        return self._GROUPS[idx % len(self._GROUPS)]

    def _gf(self, idx: int) -> str:
        """Cycle through fine groups only."""
        return self._GROUPS_F[idx % len(self._GROUPS_F)]

    def _wb(self, w: int) -> float:
        """Window bonus for scoring."""
        if w >= 252: return 0.30
        if w >= 126: return 0.20
        if w >= 63:  return 0.10
        return 0.0

    def _add(self, out: list["ExpressionCandidate"], expr: str,
             family: str, source: str, score: float = 0.0) -> None:
        out.append(ExpressionCandidate(_sig(expr), family, source, score))

    def _map_placeholders(self, expr: str) -> str | None:
        expr = _sig(expr)
        fund = self.catalog.fund + self.catalog.other
        pools = {
            "x":        fund + self.catalog.analyst,
            "y":        fund + self.catalog.model,
            "a":        self.catalog.fund + self.catalog.analyst,
            "b":        self.catalog.fund + self.catalog.model,
            "c":        fund,
            "f1":       self.catalog.fund + self.catalog.analyst,
            "f2":       self.catalog.fund + self.catalog.model,
            "f3":       fund + self.catalog.other,
            "y1":       self.catalog.fund + self.catalog.model,
            "y2":       fund + self.catalog.analyst,
            "analyst":  self.catalog.analyst + self.catalog.model,
            "sentiment":self.catalog.sent + self.catalog.analyst,
        }
        for ph in sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", expr))):
            pool = [f for f in pools.get(ph, []) if not is_bad_field_name(f)]
            if not pool:
                return None
            expr = expr.replace("{" + ph + "}", self.rng.choice(pool))
        return expr

    # Legacy stubs — keep so any external code that imports them doesn't break
    def _group_cycle(self, *, broad: bool = False) -> list[str]:
        return list(self._GROUPS if broad else self._GROUPS_F)

    def _priority_fields(self, pools, limit, tokens=()):
        fields: list[str] = []
        for pool in pools:
            fields.extend(pool)
        fields = list(dict.fromkeys([f for f in fields if f and not is_bad_field_name(f)]))
        if tokens:
            fields.sort(key=lambda f: sum(1 for t in tokens if t in f.lower()) + field_quality_score(f) * 0.1, reverse=True)
        else:
            fields.sort(key=field_quality_score, reverse=True)
        return fields[:limit]

    def _pv_var(self, name: str) -> str:
        return self._pv(name)

    def _candidate_priority_key(self, c: "ExpressionCandidate") -> tuple[int, float, int]:
        return self._priority_key(c)

    def _apply_family_quota(self, candidates: list["ExpressionCandidate"], cap: int) -> list["ExpressionCandidate"]:
        return candidates[:cap]

class ProfileSelector:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.alpha50_profiles = self._load_alpha50_profiles()

    def payloads_for(self, candidates: list[ExpressionCandidate], max_payloads: int) -> list[dict]:
        payloads: list[dict] = []
        seen: set[tuple[str, str]] = set()
        pending_variants: list[tuple[ExpressionCandidate, int, str, dict]] = []
        quality_diverse = _quality_diverse_enabled(self.cfg)
        for c in candidates:
            variants = self._variants_for(c)
            if not variants:
                continue
            name, settings = variants[0]
            added = self._append_payload(payloads, seen, c, 0, name, settings)
            if len(payloads) >= max_payloads:
                return payloads
            if added and not quality_diverse:
                for idx, (v_name, v_settings) in enumerate(variants[1:], start=1):
                    pending_variants.append((c, idx, v_name, v_settings))
        for c, idx, name, settings in pending_variants:
            self._append_payload(payloads, seen, c, idx, name, settings)
            if len(payloads) >= max_payloads:
                return payloads
        return payloads

    def _append_payload(self, payloads: list[dict], seen: set[tuple[str, str]], c: ExpressionCandidate, idx: int, name: str, settings: dict) -> bool:
        key = (c.expression, json.dumps(settings, sort_keys=True))
        if key in seen:
            return False
        seen.add(key)
        payloads.append({
            "type": "REGULAR",
            "regular": c.expression,
            "settings": settings,
            "meta": {"profile": name, "family": c.family, "source": c.source, "candidate_score": c.score, "variant": idx},
        })
        return True

    def _base(self) -> dict:
        return {
            "instrumentType": self.cfg.instrument_type,
            "region": self.cfg.region,
            "universe": self.cfg.universe,
            "delay": self.cfg.delay,
            "decay": 0,
            "neutralization": "MARKET",  # v49: match expression group → no double-neutralize
            "truncation": 0.08,
            "pasteurization": "ON",
            "unitHandling": "VERIFY",
            "nanHandling": "ON",
            "language": "FASTEXPR",
            "visualization": False,
        }

    def _variants_for(self, c: ExpressionCandidate) -> list[tuple[str, dict]]:
        """v49: match platform neutralization to the group inside the expression.

        CRITICAL: never set platform neutralization to a BROADER group than the
        expression uses — that causes double-neutralization → negative Sharpe.
        Rule: platform neutralization = same group as in expression (or MARKET if none).
        """
        low = c.expression.lower()
        base = self._base()
        fam = c.family
        # near_pass / pass_fundamental: MARKET primary for Fitness (coverage); ignore in-expression group
        if fam.startswith("near_pass") or fam.startswith("pass_fundamental") or fam.startswith("pass_fundamental_clone"):
            neutral = "MARKET"
        elif ", market)" in low:
            neutral = "MARKET"
        elif ", sector)" in low:
            neutral = "SECTOR"
        elif ", industry)" in low:
            neutral = "INDUSTRY"
        elif ", subindustry)" in low:
            neutral = "SUBINDUSTRY"
        else:
            neutral = "MARKET"
        base["neutralization"] = neutral
        # v49: calibrated decay/truncation per family
        # decay=0: no smoothing (best for very slow fundamentals, ts_rank w=252)
        # decay=4: mild smoothing (balanced for most fundamental alphas)
        # decay=8: stronger smoothing (for regime-shift, volatile signals)
        # truncation: 0.04-0.07 (was 0.03-0.035 → too tight → CONCENTRATED_WEIGHT)
        fam = c.family
        if fam.startswith("arch_delta_liquid") or fam.startswith("arch_level_liquid"):
            base.update({"decay": 4, "truncation": 0.05})
        elif fam.startswith("arch_hybrid"):
            base.update({"decay": 4, "truncation": 0.05})
        elif fam.startswith("arch_regime"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("arch_ts_rank") or fam.startswith("arch_delta_ts_rank"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("arch_zscore"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("arch_pv") or fam.startswith("arch_vwap"):
            base.update({"decay": 6, "truncation": 0.06})
        elif fam.startswith("arch_vol_price") or fam.startswith("arch_vol_liq"):
            base.update({"decay": 4, "truncation": 0.06})
        elif fam.startswith("arch_ret_vol"):
            base.update({"decay": 4, "truncation": 0.07})
        elif fam.startswith("arch_analyst"):
            base.update({"decay": 4, "truncation": 0.06})
        elif fam.startswith("arch_vol_scaled"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("arch_spread"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("arch_ts_rank_gr"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("arch_"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("near_pass") or fam.startswith("pass_fundamental"):
            base["neutralization"] = "MARKET"
            base.update({"decay": max(4, int(self.cfg.near_pass_primary_decay)), "truncation": 0.05})
        elif fam.startswith("alpha_models_template") or fam.startswith("formulaic_"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("pass_fundamental"):
            base.update({"decay": 6, "truncation": 0.05})
        elif fam.startswith("pass_pv") or fam.startswith("pv"):
            base.update({"decay": 6, "truncation": 0.06})
        elif c.source in ("robust", "hybrid", "submission"):
            base.update({"decay": 8, "truncation": 0.05})
        else:
            base.update({"decay": 6, "truncation": 0.05})
        variants: list[tuple[str, dict]] = [(f"{c.family}:primary", dict(base))]

        if c.family.startswith("near_pass"):
            for decay in (4, 6, 8):
                v = dict(base)
                v.update({"decay": decay, "neutralization": "MARKET"})
                variants.append((f"{c.family}:d{decay}_market", v))
            return variants

        if c.family.startswith("pass_fundamental") or c.family.startswith("pass_fundamental_clone"):
            for decay in (4, 6, 8):
                v = dict(base)
                v.update({"decay": max(4, decay), "neutralization": "MARKET"})
                variants.append((f"{c.family}:d{decay}_market", v))
            return variants

        if c.family.startswith("alpha_models_template") or c.family.startswith("formulaic_"):
            for decay, neut in ((4, "MARKET"), (8, "MARKET"), (6, "SECTOR"), (6, "INDUSTRY")):
                v = dict(base); v.update({"decay": decay, "neutralization": neut})
                variants.append((f"{c.family}:d{decay}_{neut.lower()}", v))
            return variants

        # Always add a MARKET-level variant as Fitness insurance
        if neutral != "MARKET":
            v = dict(base); v["neutralization"] = "MARKET"
            variants.append((f"{c.family}:neut_market", v))

        # Decay sweep — cheap parameter to test
        primary_decay = int(base.get("decay", 6))
        for alt_decay in (0, 4, 8):
            if alt_decay == primary_decay or len(variants) >= 5:
                break
            v = dict(base); v["decay"] = alt_decay
            variants.append((f"{c.family}:d{alt_decay}", v))

        # alpha50 profile if available
        if c.score >= 2.0 and len(variants) < 5:
            variants.append((f"{c.family}:alpha50", self._alpha50_like(base, c)))

        return variants

    def _alpha50_like(self, base: dict, c: ExpressionCandidate) -> dict:
        if not self.alpha50_profiles:
            return dict(base)
        pref = dict(base)
        profile = self.alpha50_profiles[abs(hash(c.family)) % len(self.alpha50_profiles)]
        pref["decay"] = profile.get("decay", pref["decay"])
        pref["truncation"] = profile.get("truncation", pref["truncation"])
        pref["neutralization"] = profile.get("neutralization", pref["neutralization"])
        if self.cfg.allow_universe_grid:
            pref["universe"] = profile.get("universe", pref["universe"])
        pref["nanHandling"] = profile.get("nanHandling", pref.get("nanHandling", "ON"))
        return pref

    def _load_alpha50_profiles(self) -> list[dict]:
        path = Path(__file__).resolve().parent / self.cfg.alpha50_filename
        if not path.is_file():
            return []
        profiles: list[dict] = []
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    raw = row.get("settingdict") or ""
                    try:
                        sd = ast.literal_eval(raw)
                    except Exception:
                        continue
                    if not isinstance(sd, dict):
                        continue
                    profiles.append({
                        "universe": str(sd.get("Universe") or self.cfg.universe).upper(),
                        "decay": int(float(sd.get("Decay") or 0)),
                        "truncation": float(sd.get("Truncation") or 0.08),
                        "neutralization": str(sd.get("Neutralization") or "Subindustry").upper(),
                        "nanHandling": "ON" if str(sd.get("NaN_Handling") or "On").lower() == "on" else "OFF",
                    })
        except Exception as e:
            print(f"[alpha50] profile read failed: {e}")
        return profiles


# ---------------------------- near-pass amplifier ----------------------------

class NearPassAmplifier:
    """Generate parameter/structural variants around historical near-pass alphas.

    Most of the highest-Sharpe items in feedback land at Sh=0.8~1.13 but still
    miss the 1.25 cutoff. Instead of re-testing the same exact expression, we
    spin up cheap structural variants (mirror sign, alternative windows, paired
    with a price reversion filter) so the simulation budget targets the most
    promising neighbourhood.
    """

    def __init__(self, cfg: PipelineConfig, catalog: FieldCatalog, validator: PreflightValidator) -> None:
        self.cfg = cfg
        self.catalog = catalog
        self.validator = validator

    def amplify(self, records: list[dict[str, Any]], tried_exact: set[str]) -> list[ExpressionCandidate]:
        if not self.cfg.near_pass_enabled or not records:
            return []
        out: list[ExpressionCandidate] = []
        skipped_toxic = 0
        skipped_complex = 0
        tried_behaviors = {_behavior_signature(expr) for expr in tried_exact if _sig(expr)}
        emitted_seed_behaviors: set[str] = set()
        for rec in records:
            expr = _sig(rec.get("expression") or "")
            if not expr:
                continue
            seed_behavior = _behavior_signature(expr)
            if (
                _quality_diverse_enabled(self.cfg)
                and seed_behavior
                and emitted_seed_behaviors.__contains__(seed_behavior)
            ):
                continue
            if _has_toxic_near_pass_price_leg(expr):
                skipped_toxic += 1
                continue
            sh = float(rec.get("sharpe") or 0.0)
            base_score = 2.5 + (sh - self.cfg.near_pass_min_sharpe)
            emitted_for_seed = 0
            max_per_seed = (
                max(1, int(getattr(self.cfg, "max_behavior_per_seed", 1) or 1))
                if _quality_diverse_enabled(self.cfg)
                else max(1, int(self.cfg.near_pass_max_variants_per_seed) + 1)
            )
            if self._within_prescreen_complexity(expr):
                # Seed clones already ran on the platform — allow unknown fields when
                # this cycle's catalog is incomplete (analyst-only fetch / 429 skips).
                if self._accept_near_pass_expr(expr, allow_unknown_fields=True):
                    self._emit(out, expr, "near_pass_variant", "near_pass", base_score + 0.15)
                    emitted_for_seed += 1
            else:
                skipped_complex += 1
            variants = self._variants_for(expr)
            picked = 0
            for v in variants:
                if picked >= int(self.cfg.near_pass_max_variants_per_seed) or emitted_for_seed >= max_per_seed:
                    break
                if v in tried_exact or v == expr:
                    continue
                if _quality_diverse_enabled(self.cfg):
                    vb = _behavior_signature(v)
                    if vb and (vb in tried_behaviors or vb == seed_behavior or vb in emitted_seed_behaviors):
                        continue
                    if not self._semantic_variant_changed(expr, v):
                        continue
                if not self._within_prescreen_complexity(v):
                    skipped_complex += 1
                    continue
                if _has_toxic_near_pass_price_leg(v):
                    continue
                if not self._accept_near_pass_expr(v, allow_unknown_fields=True):
                    continue
                cross_field = bool(getattr(self.cfg, "enable_near_pass_cross_field_variants", False)) and (
                    _field_signature(expr, max_fields=8) != _field_signature(v, max_fields=8)
                )
                family = "near_pass_cross_field_variant" if cross_field else "near_pass_variant"
                self._emit(out, v, family, "near_pass", base_score)
                picked += 1
                emitted_for_seed += 1
            if emitted_for_seed > 0 and seed_behavior:
                emitted_seed_behaviors.add(seed_behavior)
        if skipped_toxic:
            print(f"[near_pass] skipped {skipped_toxic} toxic price-leg seeds")
        if skipped_complex:
            print(f"[near_pass] skipped {skipped_complex} over-complex variants before prescreen")
        if not out:
            print(
                f"[near_pass] amplified +0 "
                f"(toxic={skipped_toxic} complex={skipped_complex} seeds={len(records)})"
            )
        return out

    def _accept_near_pass_expr(self, expr: str, *, allow_unknown_fields: bool = False) -> bool:
        ok, reason = self.validator.validate(expr)
        if ok:
            return True
        if allow_unknown_fields and str(reason).startswith("unknown_variable:"):
            return True
        return False

    def _emit(self, out: list[ExpressionCandidate], expr: str, family: str, source: str, score: float) -> None:
        out.append(ExpressionCandidate(_sig(expr), family, source, score))

    def _within_prescreen_complexity(self, expr: str) -> bool:
        max_depth = int(getattr(self.cfg, "prescreen_max_nesting_depth", 6) or 6)
        max_fcalls = int(getattr(self.cfg, "prescreen_max_function_calls", 9) or 9)
        return (
            PreSimulationScreener._nesting_depth(expr) <= max_depth
            and PreSimulationScreener._function_calls(expr) <= max_fcalls
        )

    def _semantic_variant_changed(self, seed: str, variant: str) -> bool:
        if _behavior_signature(seed) == _behavior_signature(variant):
            return False
        if _field_signature(seed, max_fields=8) != _field_signature(variant, max_fields=8):
            return True
        if _behavior_operator_skeleton(seed) != _behavior_operator_skeleton(variant):
            return True
        seed_windows = set(re.findall(r"\b(21|42|63|126|252)\b", seed))
        var_windows = set(re.findall(r"\b(21|42|63|126|252)\b", variant))
        return seed_windows != var_windows

    def _variants_for(self, expr: str) -> list[str]:
        """Build conservative but structurally different variants around a seed.

        The old implementation over-produced short-horizon price boosters, which
        raised turnover without fixing sub-universe breadth. This version prefers
        slower windows, neutralization shifts, and liquidity-smoothed mixes.
        """
        s = _sig(expr)
        low = s.lower()
        results: list[str] = []

        def add(v: str) -> None:
            v = _sig(v)
            if v and v != s and self._within_prescreen_complexity(v):
                results.append(v)

        # Neutralization rotations.
        for src, dst in (
            (", subindustry)", ", industry)"),
            (", subindustry)", ", sector)"),
            (", sector)", ", industry)"),
            (", industry)", ", subindustry)"),
            (", sector)", ", market)"),
            (", industry)", ", market)"),
        ):
            if src in s:
                add(s.replace(src, dst, 1))

        # Replace short delta windows with slower ones; or long windows with a small ladder.
        for m in re.finditer(r"ts_delta\(([^,]+),\s*(\d+)\)", s):
            field = m.group(1).strip()
            current = int(m.group(2))
            if current <= 10:
                window_grid = (21, 42, 63, 126)
            elif current <= 63:
                window_grid = (63, 126, 252)
            else:
                window_grid = (126, 252)
            for w in window_grid:
                if w == current:
                    continue
                add(s[: m.start()] + f"ts_delta({field}, {w})" + s[m.end() :])

        # Replace short ts_rank windows with calmer ones.
        for m in re.finditer(r"ts_rank\(([^,]+),\s*(\d+)\)", s):
            field = m.group(1).strip()
            current = int(m.group(2))
            for w in (63, 126, 252):
                if w == current:
                    continue
                add(s[: m.start()] + f"ts_rank({field}, {w})" + s[m.end() :])

        # Add liquidity-smoothed siblings rather than raw short-horizon boosters.
        close = "close"
        if "volume" not in low and "adv20" not in low:
            for w in (21, 63, 126):
                add(f"({s})*rank(ts_mean(volume, {w})/adv20)")

        # For fundamental-style seeds, add a smoother long-window sibling.
        if "/cap" in low or "cap)" in low:
            for w in (63, 126, 252):
                add(f"group_neutralize(ts_zscore(({s}), {w}), subindustry)")
                add(f"group_neutralize(ts_mean(({s}), {w}), sector)")

        # Liquidity smoothing only — do not mix short price legs into fundamental seeds (kills Sharpe).
        if "vwap" not in low and "ts_delta(close" not in low and "/cap" not in low and "cap)" not in low:
            add(f"({s})*0.7+rank(ts_mean(volume,63)/adv20)*0.3")

        if "trade_when(" not in low and "volume" not in low:
            add(f"trade_when(rank(ts_mean(volume,63)/adv20),({s}),-1)")

        cross_field_variants: list[str] = []
        if bool(getattr(self.cfg, "enable_near_pass_cross_field_variants", False)):
            max_swaps = max(1, int(getattr(self.cfg, "near_pass_cross_field_variants_per_seed", 3) or 3))
            for field_name in _expression_fields(s):
                if len(cross_field_variants) >= max_swaps:
                    break
                if field_name in self.catalog.base_vars:
                    continue
                replacement = self.catalog.replacement_for(field_name)
                if not replacement:
                    continue
                swapped = _sig(re.sub(rf"\b{re.escape(field_name)}\b", replacement, s, count=1))
                if swapped and swapped != s and self._within_prescreen_complexity(swapped):
                    cross_field_variants.append(swapped)

        seen: set[str] = set()
        unique: list[str] = []
        # Field-swap variants change field_signature (and therefore
        # _behavior_signature), so they must be ordered first: the batch-level
        # dedup gate keeps only the first survivor per behavior signature, and
        # parameter-only tweaks below still collide with the seed's own behavior.
        for v in cross_field_variants + results:
            if v not in seen:
                seen.add(v)
                unique.append(v)
        return unique
# ---------------------------- pre-simulation screener ----------------------------

class PreSimulationScreener:
    """Final guardrail between candidate payloads and the platform simulation API.

    Responsibilities:
    1. Block exact expressions already simulated (regardless of pass/fail) so we
       don't burn API budget re-testing them.
    2. Block "(skeleton, primary field)" clusters with multiple historical low-Sharpe
       outcomes - same template + same field repeatedly underperforms is a strong
       prior that the next variant will too.
    3. Block payloads whose structure is too complex (deep nesting or too many
       operator calls).
    4. Block payloads whose token Jaccard similarity to *any* historical submission
       is above ``prescreen_max_history_similarity`` - this is the per-payload
       self-correlation gate.
    """

    def __init__(
        self,
        cfg: PipelineConfig,
        *,
        tried_exact: set[str],
        tried_payload_keys: set[str],
        near_pass_expressions: set[str],
        failed_cluster: dict[tuple[str, str], list[float]],
        history_pools: HistorySimilarityPools,
        top_field_lookup: Callable[[str], str | None],
        tried_metrics: dict[str, dict[str, float | None]] | None = None,
        novelty_index: NoveltyIndex | None = None,
        family_pass_rates: dict[str, float] | None = None,
        family_quality_stats: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.tried_exact = tried_exact
        self.tried_expression_ids = {_expression_identity(expr) for expr in tried_exact if expr}
        self.tried_payload_keys = tried_payload_keys
        self.near_pass_expressions = near_pass_expressions
        self.near_pass_expression_ids = {_expression_identity(expr) for expr in near_pass_expressions if expr}
        self.failed_cluster = failed_cluster
        self.history_pools = history_pools
        self.top_field_lookup = top_field_lookup
        self.tried_metrics = tried_metrics or {}
        self.novelty_index = novelty_index
        self.family_pass_rates = family_pass_rates or {}
        self.family_quality_stats = family_quality_stats or {}
        self.family_low_yield_buckets = {}

    def screen(
        self,
        payloads: list[dict],
        *,
        stage: str = "single",
    ) -> tuple[list[dict], Counter[str], list[tuple[str, str]]]:
        """Prescreen payloads.

        ``stage="coarse"``: hard quality + history/novelty gates only (no batch shape/intrabatch caps).
        ``stage="single"``: legacy one-pass (v40 behaviour).
        """
        coarse = (stage or "single").lower() == "coarse"
        skip_intrabatch = coarse and bool(self.cfg.prescreen_coarse_skip_intrabatch)
        skip_shape = coarse and bool(self.cfg.prescreen_coarse_skip_shape_quota)
        kept: list[dict] = []
        reject_counts: Counter[str] = Counter()
        family_reject_counts: Counter[tuple[str, str]] = Counter()
        dropped_samples: list[tuple[str, str]] = []
        max_depth = int(self.cfg.prescreen_max_nesting_depth)
        max_fcalls = int(self.cfg.prescreen_max_function_calls)
        cluster_threshold = float(self.cfg.prescreen_cluster_max_avg_sharpe)
        cluster_min_samples = int(self.cfg.prescreen_cluster_min_samples)
        kept_tokens: list[set[str]] = []
        kept_exprs: set[str] = set()
        kept_structures: Counter[str] = Counter()
        kept_behaviors: Counter[str] = Counter()
        kept_operator_fields: Counter[tuple[str, str]] = Counter()
        kept_bucket_counts: Counter[tuple[str, str, str, str, str, str]] = Counter()
        quality_diverse = _quality_diverse_enabled(self.cfg)

        def reject(reason: str, fam: str, expr: str) -> None:
            reject_counts[reason] += 1
            family_reject_counts[(fam or "unknown", reason)] += 1
            self._record_drop(dropped_samples, reason, expr)

        for payload in payloads:
            expr = _sig(payload.get("regular") or "")
            fam = str((payload.get("meta") or {}).get("family") or "").lower()
            if not expr:
                reject("empty", fam, expr)
                continue
            if self.cfg.prescreen_block_already_simulated:
                payload_key = _payload_fingerprint(expr, payload.get("settings") if isinstance(payload, dict) else {})
                if payload_key in self.tried_payload_keys:
                    reject("already_simulated_payload", fam, expr)
                    continue
                if _expression_identity(expr) in self.tried_expression_ids:
                    can_retry_settings = (
                        self.cfg.prescreen_allow_near_pass_settings_retry
                        and _expression_identity(expr) in self.near_pass_expression_ids
                    )
                    if not can_retry_settings:
                        reject("already_simulated_expr", fam, expr)
                        continue
            if self.cfg.prescreen_skip_negative_sharpe:
                hist = self.tried_metrics.get(expr)
                if isinstance(hist, dict):
                    sh = hist.get("sharpe")
                    if sh is not None and float(sh) < float(self.cfg.prescreen_negative_sharpe_floor):
                        reject("negative_sharpe_history", fam, expr)
                        continue
            if not self._low_yield_bucket_allowed(payload, kept_bucket_counts):
                reject("low_yield_bucket_cap", fam, expr)
                continue
            if self._nesting_depth(expr) > max_depth:
                reject("too_deep_nesting", fam, expr)
                continue
            if self._function_calls(expr) > max_fcalls:
                reject("too_many_operators", fam, expr)
                continue
            if fam.startswith("near_pass") and _has_toxic_near_pass_price_leg(expr):
                reject("near_pass_toxic_price_leg", fam, expr)
                continue
            toxic_cap = _toxic_similarity_cap_for_family(self.cfg, fam)
            toxic_sim = self.history_pools.max_similarity(expr, "toxic", early_exit_at=toxic_cap)
            if toxic_sim >= toxic_cap:
                reject(f"toxic_history>={toxic_cap:.2f}", fam, expr)
                continue
            template_like = fam.startswith("alpha_models_template") or fam.startswith("formulaic_")
            near_pass_fam = fam.startswith("near_pass")
            arch_fam = fam.startswith("arch_") or fam.startswith("pass_fundamental") or fam.startswith("pass_pv")
            skip_weak = (
                (template_like and bool(getattr(self.cfg, "prescreen_skip_weak_for_template", True)))
                or (near_pass_fam and bool(getattr(self.cfg, "prescreen_skip_weak_for_near_pass", True)))
                or (arch_fam and bool(getattr(self.cfg, "prescreen_skip_weak_for_arch", True)))  # v50
            )
            if not skip_weak:
                max_sim = self._sim_threshold_for(payload)
                weak_sim = self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=max_sim)
                if weak_sim >= max_sim:
                    reject(f"high_self_corr>={max_sim:.2f}", fam, expr)
                    continue
                if not near_pass_fam:
                    near_cap = max(max_sim, float(self.cfg.prescreen_near_pass_similarity) - 0.04)
                    near_sim = self.history_pools.max_similarity(expr, "near_pass", early_exit_at=near_cap)
                    if near_sim >= near_cap:
                        reject(f"near_pass_history>={near_cap:.2f}", fam, expr)
                        continue
            if quality_diverse:
                risk_cap = float(getattr(self.cfg, "behavior_similarity_cap", 0.78))
                risk_sim = self.history_pools.max_behavior_similarity(expr, "self_corr_risk", early_exit_at=risk_cap)
                if risk_sim >= risk_cap:
                    reject(f"self_corr_risk>={risk_cap:.2f}", fam, expr)
                    continue
            if quality_diverse or not near_pass_fam:
                generated_cap = float(getattr(self.cfg, "generated_near_clone_similarity", 0.92))
                generated_sim = max(
                    self.history_pools.max_similarity(expr, "generated", early_exit_at=generated_cap),
                    self.history_pools.max_behavior_similarity(expr, "generated", early_exit_at=generated_cap),
                )
                if generated_sim >= generated_cap:
                    reject(f"generated_near_clone>={generated_cap:.2f}", fam, expr)
                    continue
            if self.cfg.novelty_enabled and self.novelty_index is not None and not near_pass_fam:
                novelty_strict = self.cfg.novelty_strictness
                if template_like and bool(getattr(self.cfg, "prescreen_skip_novelty_for_template", True)):
                    novelty_strict = "balanced"
                reason = self.novelty_index.reject_reason(expr, strictness=novelty_strict)
                if reason:
                    reject(reason, fam, expr)
                    continue

            toks = set(re.findall(r"[a-z_]+|\d+", expr.lower()))
            struct = _structure_signature(expr)
            same_shape_limit = int(_novelty_profile(self.cfg.novelty_strictness)["same_shape_per_batch"])
            if (
                not skip_shape
                and self.cfg.novelty_enabled
                and same_shape_limit >= 0
            ):
                if kept_structures[struct] >= same_shape_limit:
                    reject(f"novelty_intrabatch_shape_quota>{same_shape_limit}", fam, expr)
                    continue
            if not skip_intrabatch:
                if quality_diverse:
                    behavior = _behavior_signature(expr)
                    behavior_cap = int(getattr(self.cfg, "max_behavior_per_batch", 1) or 1)
                    if behavior and behavior_cap >= 0 and kept_behaviors[behavior] >= behavior_cap:
                        reject(f"behavior_quota>{behavior_cap}", fam, expr)
                        continue
                    op_field = _operator_field_key(expr)
                    op_field_cap = int(getattr(self.cfg, "max_operator_field_per_batch", 2) or 2)
                    if op_field_cap >= 0 and kept_operator_fields[op_field] >= op_field_cap:
                        reject(f"operator_field_quota>{op_field_cap}", fam, expr)
                        continue
                intra_max = self._intrabatch_threshold_for(payload)
                intra_sim = self._max_token_similarity(toks, kept_tokens, early_exit_at=intra_max)
                if intra_sim >= intra_max:
                    reject(f"high_intrabatch_self_corr>={intra_max:.2f}", fam, expr)
                    continue

            if self.cfg.prescreen_skip_low_sharpe_cluster:
                field = self.top_field_lookup(expr)
                if field is not None:
                    key = (_skel(expr), field)
                    samples = self.failed_cluster.get(key) or []
                    if (
                        len(samples) >= cluster_min_samples
                        and (sum(samples) / max(1, len(samples))) < cluster_threshold
                    ):
                        reject("low_sharpe_cluster", fam, expr)
                        continue
            kept.append(payload)
            kept_exprs.add(expr)
            kept_tokens.append(toks)
            kept_structures[struct] += 1
            kept_bucket_counts[_low_yield_bucket_key_for_payload(payload)] += 1
            if quality_diverse:
                behavior = _behavior_signature(expr)
                if behavior:
                    kept_behaviors[behavior] += 1
                kept_operator_fields[_operator_field_key(expr)] += 1
        self.family_drop_counts = family_reject_counts
        return kept, reject_counts, dropped_samples

    @staticmethod
    def _record_drop(buf: list[tuple[str, str]], reason: str, expr: str) -> None:
        if len(buf) < 8:
            buf.append((reason, expr[:140]))

    @staticmethod
    def _nesting_depth(expr: str) -> int:
        depth = 0
        max_depth = 0
        for ch in expr:
            if ch == "(":
                depth += 1
                max_depth = max(max_depth, depth)
            elif ch == ")":
                depth = max(0, depth - 1)
        return max_depth

    @staticmethod
    def _function_calls(expr: str) -> int:
        return len(re.findall(r"\b[a-z_][a-z0-9_]*\s*\(", expr.lower()))

    def _max_history_similarity(self, expr: str, *, early_exit_at: float | None = None) -> float:
        """Legacy helper: max similarity vs weak-fail pool (toxic checked separately in screen)."""
        return self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=early_exit_at)

    def _weak_history_cap_for(self, payload: dict, default_cap: float) -> float | None:
        """Per-family weak-pool cap; None = skip weak-fail pool (must match coarse screen skips)."""
        fam = str((payload.get("meta") or {}).get("family") or "").lower()
        if fam.startswith("near_pass") and bool(
            getattr(self.cfg, "prescreen_skip_weak_for_near_pass", True)
        ):
            return None
        if (
            fam.startswith("alpha_models_template")
            or fam.startswith("formulaic_")
            or fam == "external_template"
        ) and bool(getattr(self.cfg, "prescreen_skip_weak_for_template", True)):
            return None
        arch_fam = fam.startswith("arch_") or fam.startswith("pass_fundamental") or fam.startswith("pass_pv")
        if arch_fam and bool(getattr(self.cfg, "prescreen_skip_weak_for_arch", True)):
            return None
        return default_cap

    @staticmethod
    def _max_token_similarity(toks: set[str], token_pool: list[set[str]], *, early_exit_at: float | None = None) -> float:
        if not toks or not token_pool:
            return 0.0
        best = 0.0
        for old in token_pool:
            denom = len(toks | old)
            if denom <= 0:
                continue
            best = max(best, len(toks & old) / denom)
            if early_exit_at is not None and best >= early_exit_at:
                return best
            if best >= 0.999:
                break
        return best

    def _sim_threshold_for(self, payload: dict) -> float:
        """Use wider similarity tolerance for near-pass variants only."""
        base = float(self.cfg.prescreen_max_history_similarity)
        fam = str((payload.get("meta") or {}).get("family") or "").lower() if isinstance(payload, dict) else ""
        if fam.startswith("near_pass"):
            return max(base, float(self.cfg.prescreen_near_pass_similarity))
        return base

    def _intrabatch_threshold_for(self, payload: dict) -> float:
        """Keep the current batch from collapsing into one near-duplicate cluster."""
        fam = str((payload.get("meta") or {}).get("family") or "").lower() if isinstance(payload, dict) else ""
        if fam.startswith("near_pass") or fam.startswith("pass_fundamental"):
            return float(getattr(self.cfg, "prescreen_near_pass_intrabatch_cap", 0.96))
        return float(self.cfg.prescreen_intrabatch_similarity)

    def _low_yield_bucket_allowed(
        self,
        payload: dict,
        selected_bucket_counts: Counter[tuple[str, str, str, str, str, str]] | None = None,
    ) -> bool:
        fam = str((payload.get("meta") or {}).get("family") or "").lower() if isinstance(payload, dict) else ""
        if fam.startswith("near_pass"):
            return True
        key = _low_yield_bucket_key_for_payload(payload)
        capped = getattr(self, "family_low_yield_buckets", {}) or {}
        if key not in capped:
            return True
        limit = int(getattr(self.cfg, "low_yield_bucket_hard_cap", 2) or 2)
        if selected_bucket_counts is None:
            return False
        return selected_bucket_counts[key] < limit

    def select_diverse_for_simulate(
        self,
        payloads: list[dict],
        target_n: int,
        *,
        history_similarity_cap: float | None = None,
        intrabatch_cap_override: float | None = None,
        shape_quota_override: int | None = None,
        rank_key: Callable[[dict], tuple] | None = None,
    ) -> tuple[list[dict], Counter[str], list[tuple[str, str]]]:
        """Greedy diverse selection for the simulate batch (fine stage).

        Sorts by template priority + candidate score, then picks up to ``target_n`` payloads that
        respect structure quotas, intrabatch similarity, and (strict) history similarity.
        """
        if target_n <= 0 or not payloads:
            return [], Counter(), []
        hist_cap = (
            float(history_similarity_cap)
            if history_similarity_cap is not None
            else float(self.cfg.prescreen_max_history_similarity)
        )
        if shape_quota_override is not None:
            shape_limit = int(shape_quota_override)
        else:
            shape_limit = int(_novelty_profile(self.cfg.novelty_strictness)["same_shape_per_batch"])
        reject_counts: Counter[str] = Counter()
        dropped_samples: list[tuple[str, str]] = []
        rates = self.family_pass_rates or {}
        qstats = getattr(self, "family_quality_stats", {}) or {}
        key_fn = rank_key or (lambda p: _payload_fine_rank_key(p, rates, qstats))
        ordered = sorted(payloads, key=key_fn)
        selected: list[dict] = []
        selected_tokens: list[set[str]] = []
        selected_behavior_tokens: list[set[str]] = []
        shape_counts: Counter[str] = Counter()
        behavior_counts: Counter[str] = Counter()
        operator_field_counts: Counter[tuple[str, str]] = Counter()
        family_counts: Counter[str] = Counter()
        bucket_counts: Counter[tuple[str, str, str, str, str, str]] = Counter()
        seen_expr: set[str] = set()
        quality_diverse = _quality_diverse_enabled(self.cfg)

        behavior_batch_cap = int(getattr(self.cfg, "max_behavior_per_batch", 1) or 1)
        op_field_cap = int(getattr(self.cfg, "max_operator_field_per_batch", 2) or 2)
        behavior_sim_cap = float(getattr(self.cfg, "behavior_similarity_cap", 0.78))
        generated_cap = float(getattr(self.cfg, "generated_near_clone_similarity", 0.92))

        def fam_of(payload: dict) -> str:
            return str((payload.get("meta") or {}).get("family") or "").lower() if isinstance(payload, dict) else ""

        def candidate_quality(payload: dict) -> float:
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            try:
                score = float(meta.get("candidate_score") or 0.0)
            except Exception:
                score = 0.0
            fam = fam_of(payload)
            return score + 2.0 * float(rates.get(_family_stats_bucket(fam), rates.get(fam, 0.0)) or 0.0)

        def reject_fine(reason: str, expr: str) -> None:
            reject_counts[reason] += 1
            self._record_drop(dropped_samples, reason, expr)

        def eligible(
            payload: dict,
        ) -> tuple[bool, str, str, set[str], set[str], str, tuple[str, str], str]:
            expr = _sig(payload.get("regular") or "")
            if not expr:
                return False, "fine_empty", expr, set(), set(), "", ("", ""), ""
            if expr in seen_expr:
                return False, "fine_duplicate_expr", expr, set(), set(), "", ("", ""), ""
            struct = _structure_signature(expr)
            if self.cfg.novelty_enabled and shape_limit >= 0 and shape_counts[struct] >= shape_limit:
                return False, f"fine_shape_quota>{shape_limit}", expr, set(), set(), struct, ("", ""), ""
            fam = fam_of(payload)
            behavior = _behavior_signature(expr)
            if quality_diverse and behavior and behavior_batch_cap >= 0 and behavior_counts[behavior] >= behavior_batch_cap:
                return False, f"fine_behavior_quota>{behavior_batch_cap}", expr, set(), set(), struct, ("", ""), behavior
            op_field = _operator_field_key(expr)
            if quality_diverse and op_field_cap >= 0 and operator_field_counts[op_field] >= op_field_cap:
                return False, f"fine_operator_field_quota>{op_field_cap}", expr, set(), set(), struct, op_field, behavior
            if not self._low_yield_bucket_allowed(payload, bucket_counts):
                return False, "fine_low_yield_bucket_cap", expr, set(), set(), struct, op_field, behavior
            toks = set(re.findall(r"[a-z_]+|\d+", expr.lower()))
            intra_max = (
                float(intrabatch_cap_override)
                if intrabatch_cap_override is not None
                else self._intrabatch_threshold_for(payload)
            )
            intra_sim = self._max_token_similarity(toks, selected_tokens, early_exit_at=intra_max)
            if intra_sim >= intra_max:
                return False, f"fine_intrabatch>={intra_max:.2f}", expr, toks, set(), struct, op_field, behavior
            behavior_toks = _behavior_token_set(expr)
            if quality_diverse:
                behavior_sim = self._max_token_similarity(
                    behavior_toks,
                    selected_behavior_tokens,
                    early_exit_at=behavior_sim_cap,
                )
                if behavior_sim >= behavior_sim_cap:
                    return False, f"fine_behavior_similarity>={behavior_sim_cap:.2f}", expr, toks, behavior_toks, struct, op_field, behavior
                risk_sim = self.history_pools.max_behavior_similarity(
                    expr,
                    "self_corr_risk",
                    early_exit_at=behavior_sim_cap,
                )
                if risk_sim >= behavior_sim_cap:
                    return False, f"fine_self_corr_risk>={behavior_sim_cap:.2f}", expr, toks, behavior_toks, struct, op_field, behavior
            weak_cap = self._weak_history_cap_for(payload, hist_cap)
            if weak_cap is not None:
                hist_sim = self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=weak_cap)
                if hist_sim >= weak_cap:
                    return False, f"fine_history>={weak_cap:.2f}", expr, toks, behavior_toks, struct, op_field, behavior
            if quality_diverse or not fam.startswith("near_pass"):
                generated_sim = max(
                    self.history_pools.max_similarity(expr, "generated", early_exit_at=generated_cap),
                    self.history_pools.max_behavior_similarity(expr, "generated", early_exit_at=generated_cap),
                )
                if generated_sim >= generated_cap:
                    return False, f"fine_generated_near_clone>={generated_cap:.2f}", expr, toks, behavior_toks, struct, op_field, behavior
            return True, "", expr, toks, behavior_toks, struct, op_field, behavior

        if quality_diverse:
            remaining = list(ordered)
            while remaining and len(selected) < target_n:
                best_idx = -1
                best_pack: tuple[str, set[str], set[str], str, tuple[str, str], str, str] | None = None
                best_score: float | None = None
                blocked: list[tuple[str, str]] = []
                for idx, payload in enumerate(remaining):
                    ok, reason, expr, toks, behavior_toks, struct, op_field, behavior = eligible(payload)
                    if not ok:
                        blocked.append((reason, expr))
                        continue
                    fam = fam_of(payload)
                    behavior_sim = self._max_token_similarity(behavior_toks, selected_behavior_tokens)
                    same_family = family_counts[fam]
                    same_operator = operator_field_counts[op_field]
                    mmr = (
                        candidate_quality(payload)
                        - 2.25 * behavior_sim
                        - 0.35 * same_family
                        - 0.45 * same_operator
                    )
                    if best_score is None or mmr > best_score:
                        best_score = mmr
                        best_idx = idx
                        best_pack = (expr, toks, behavior_toks, struct, op_field, behavior, fam)
                if best_idx < 0 or best_pack is None:
                    for reason, expr in blocked:
                        reject_fine(reason, expr)
                    break
                payload = remaining.pop(best_idx)
                expr, toks, behavior_toks, struct, op_field, behavior, fam = best_pack
                selected.append(payload)
                seen_expr.add(expr)
                selected_tokens.append(toks)
                selected_behavior_tokens.append(behavior_toks)
                shape_counts[struct] += 1
                if behavior:
                    behavior_counts[behavior] += 1
                operator_field_counts[op_field] += 1
                family_counts[fam] += 1
                bucket_counts[_low_yield_bucket_key_for_payload(payload)] += 1
            return selected, reject_counts, dropped_samples

        for payload in ordered:
            if len(selected) >= target_n:
                break
            ok, reason, expr, toks, _behavior_toks, struct, _op_field, fam = eligible(payload)
            if not ok:
                reject_fine(reason, expr)
                continue
            selected.append(payload)
            seen_expr.add(expr)
            selected_tokens.append(toks)
            shape_counts[struct] += 1
            family_counts[fam] += 1
            bucket_counts[_low_yield_bucket_key_for_payload(payload)] += 1

        return selected, reject_counts, dropped_samples

    def select_near_pass_rescue(
        self,
        payloads: list[dict],
        already: list[dict],
        target_n: int,
    ) -> tuple[list[dict], Counter[str]]:
        """Rescue near-pass rows when diversity/novelty gates drop the whole stratum."""
        if target_n <= 0 or not payloads:
            return [], Counter()
        seen_payloads = {
            _payload_fingerprint(
                _sig(p.get("regular") or ""),
                p.get("settings") if isinstance(p.get("settings"), dict) else {},
            )
            for p in already
            if _sig(p.get("regular") or "")
        }
        max_depth = int(self.cfg.prescreen_max_nesting_depth)
        max_fcalls = int(self.cfg.prescreen_max_function_calls)
        out: list[dict] = []
        reject_counts: Counter[str] = Counter()
        for payload in sorted(payloads, key=self._fine_rank_key_bulk):
            if len(out) >= target_n:
                break
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            fam = str(meta.get("family") or "").lower()
            if not fam.startswith("near_pass"):
                continue
            expr = _sig(payload.get("regular") or "")
            payload_key = _payload_fingerprint(
                expr,
                payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
            )
            if not expr or payload_key in seen_payloads:
                reject_counts["near_pass_rescue_duplicate"] += 1
                continue
            if self.cfg.prescreen_block_already_simulated and _expression_identity(expr) in self.tried_expression_ids:
                can_retry_settings = (
                    self.cfg.prescreen_allow_near_pass_settings_retry
                    and _expression_identity(expr) in self.near_pass_expression_ids
                )
                if not can_retry_settings:
                    reject_counts["near_pass_rescue_already_simulated_expr"] += 1
                    continue
            hist = self.tried_metrics.get(expr)
            if self.cfg.prescreen_skip_negative_sharpe and isinstance(hist, dict):
                sh = hist.get("sharpe")
                if sh is not None and float(sh) < float(self.cfg.prescreen_negative_sharpe_floor):
                    reject_counts["near_pass_rescue_negative_sharpe"] += 1
                    continue
            if self._nesting_depth(expr) > max_depth:
                reject_counts["near_pass_rescue_too_deep"] += 1
                continue
            if self._function_calls(expr) > max_fcalls:
                reject_counts["near_pass_rescue_too_many_operators"] += 1
                continue
            if _has_toxic_near_pass_price_leg(expr):
                reject_counts["near_pass_rescue_toxic_price_leg"] += 1
                continue
            toxic_cap = _toxic_similarity_cap_for_family(self.cfg, fam)
            if self.history_pools.max_similarity(expr, "toxic", early_exit_at=toxic_cap) >= toxic_cap:
                reject_counts["near_pass_rescue_toxic_history"] += 1
                continue
            out.append(payload)
            seen_payloads.add(payload_key)
        if out:
            reject_counts["near_pass_rescue_selected"] = len(out)
        return out, reject_counts

    def select_coarse_topup(
        self,
        coarse: list[dict],
        already: list[dict],
        need: int,
        *,
        history_similarity_cap: float,
    ) -> list[dict]:
        if need <= 0 or not coarse:
            return []
        seen_payloads = {
            _payload_fingerprint(
                _sig(p.get("regular") or ""),
                p.get("settings") if isinstance(p.get("settings"), dict) else {},
            )
            for p in already
            if _sig(p.get("regular") or "")
        }
        out: list[dict] = []
        selected_exprs = [_sig(p.get("regular") or "") for p in already if _sig(p.get("regular") or "")]
        selected_behaviors = Counter(_behavior_signature(e) for e in selected_exprs if _behavior_signature(e))
        selected_operator_fields = Counter(_operator_field_key(e) for e in selected_exprs)
        selected_behavior_tokens = [_behavior_token_set(e) for e in selected_exprs]
        selected_bucket_counts = Counter(_low_yield_bucket_key_for_payload(p) for p in already if isinstance(p, dict))
        for payload in sorted(coarse, key=self._fine_rank_key_bulk):
            if len(out) >= need:
                break
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            fam = str(meta.get("family") or "").lower()
            src = str(meta.get("source") or "").lower()
            if _is_low_yield_arch_family(fam):
                continue
            if bool(getattr(self.cfg, "prescreen_coarse_topup_quality_only", True)):
                if not _is_quality_simulate_family(fam, src):
                    continue
            expr = _sig(payload.get("regular") or "")
            payload_key = _payload_fingerprint(
                expr,
                payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
            )
            if not expr or payload_key in seen_payloads:
                continue
            if not self._low_yield_bucket_allowed(payload, selected_bucket_counts):
                continue
            if _quality_diverse_enabled(self.cfg) and not self._quality_diverse_payload_allowed(
                expr,
                selected_behaviors,
                selected_operator_fields,
                selected_behavior_tokens,
            ):
                continue
            cap = self._weak_history_cap_for(payload, history_similarity_cap)
            if cap is not None:
                if self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=cap) >= cap:
                    continue
            out.append(payload)
            seen_payloads.add(payload_key)
            selected_bucket_counts[_low_yield_bucket_key_for_payload(payload)] += 1
            if _quality_diverse_enabled(self.cfg):
                self._quality_diverse_payload_remember(expr, selected_behaviors, selected_operator_fields, selected_behavior_tokens)
        return out

    def select_salvage_topup(
        self,
        coarse: list[dict],
        already: list[dict],
        need: int,
        *,
        target_n: int,
        history_similarity_cap: float,
    ) -> tuple[list[dict], dict[str, int]]:
        """Throughput-first top-up from coarse rows after strict fine selection underfills."""
        if need <= 0 or not coarse:
            return [], {}
        seen_payloads = {
            _payload_fingerprint(
                _sig(p.get("regular") or ""),
                p.get("settings") if isinstance(p.get("settings"), dict) else {},
            )
            for p in already
            if _sig(p.get("regular") or "")
        }
        out: list[dict] = []
        stats: Counter[str] = Counter()
        selected_exprs = [_sig(p.get("regular") or "") for p in already if _sig(p.get("regular") or "")]
        selected_behaviors = Counter(_behavior_signature(e) for e in selected_exprs if _behavior_signature(e))
        selected_operator_fields = Counter(_operator_field_key(e) for e in selected_exprs)
        selected_behavior_tokens = [_behavior_token_set(e) for e in selected_exprs]
        selected_bucket_counts = Counter(_low_yield_bucket_key_for_payload(p) for p in already if isinstance(p, dict))
        arch_cap = max(0, int(max(1, target_n) * float(getattr(self.cfg, "max_arch_explore_batch_share", 0.10))))
        arch_seen = sum(
            1
            for p in already
            if str((p.get("meta") or {}).get("family") or "").lower().startswith("arch_")
        )

        def tier(payload: dict) -> tuple[int, tuple]:
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            fam = str(meta.get("family") or "").lower()
            src = str(meta.get("source") or "").lower()
            if fam.startswith("near_pass"):
                lane = 0
            elif _is_quality_simulate_family(fam, src):
                lane = 1
            elif fam.startswith("arch_"):
                lane = 2
            else:
                lane = 3
            return (lane, self._fine_rank_key_bulk(payload))

        for payload in sorted(coarse, key=tier):
            if len(out) >= need:
                break
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            fam = str(meta.get("family") or "").lower()
            expr = _sig(payload.get("regular") or "")
            payload_key = _payload_fingerprint(
                expr,
                payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
            )
            if not expr or payload_key in seen_payloads:
                continue
            if not self._low_yield_bucket_allowed(payload, selected_bucket_counts):
                stats["salvage_low_yield_bucket_block"] += 1
                continue
            if _quality_diverse_enabled(self.cfg) and not self._quality_diverse_payload_allowed(
                expr,
                selected_behaviors,
                selected_operator_fields,
                selected_behavior_tokens,
            ):
                stats["salvage_behavior_block"] += 1
                continue
            if fam.startswith("arch_") and arch_seen >= arch_cap:
                continue
            cap = self._weak_history_cap_for(payload, history_similarity_cap)
            if cap is not None and self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=cap) >= cap:
                continue
            out.append(payload)
            seen_payloads.add(payload_key)
            selected_bucket_counts[_low_yield_bucket_key_for_payload(payload)] += 1
            if _quality_diverse_enabled(self.cfg):
                self._quality_diverse_payload_remember(expr, selected_behaviors, selected_operator_fields, selected_behavior_tokens)
            stats["salvage_topup"] += 1
            if fam.startswith("near_pass"):
                stats["salvage_near_pass"] += 1
            elif fam.startswith("alpha_models_template") or fam.startswith("formulaic_"):
                stats["salvage_template"] += 1
            elif fam.startswith("arch_"):
                stats["salvage_arch"] += 1
                arch_seen += 1
            else:
                stats["salvage_other"] += 1
        return out, dict(stats)

    def select_coarse_throughput_fill(
        self,
        coarse: list[dict],
        already: list[dict],
        target_n: int,
        *,
        history_similarity_cap: float,
    ) -> tuple[list[dict], Counter[str]]:
        """Fill up to min(target_n, len(coarse)) after strict fine passes — throughput floor."""
        cap = min(max(0, int(target_n)), len(coarse))
        if cap <= 0 or len(already) >= cap:
            return [], Counter()
        need = cap - len(already)
        seen_payloads = {
            _payload_fingerprint(
                _sig(p.get("regular") or ""),
                p.get("settings") if isinstance(p.get("settings"), dict) else {},
            )
            for p in already
            if _sig(p.get("regular") or "")
        }
        toxic_cap = float(getattr(self.cfg, "prescreen_max_toxic_similarity", 0.85) or 0.85)
        reject_counts: Counter[str] = Counter()
        out: list[dict] = []
        selected_exprs = [_sig(p.get("regular") or "") for p in already if _sig(p.get("regular") or "")]
        selected_behaviors = Counter(_behavior_signature(e) for e in selected_exprs if _behavior_signature(e))
        selected_operator_fields = Counter(_operator_field_key(e) for e in selected_exprs)
        selected_behavior_tokens = [_behavior_token_set(e) for e in selected_exprs]
        selected_bucket_counts = Counter(_low_yield_bucket_key_for_payload(p) for p in already if isinstance(p, dict))
        for payload in sorted(coarse, key=self._fine_rank_key_bulk):
            if len(out) >= need:
                break
            expr = _sig(payload.get("regular") or "")
            payload_key = _payload_fingerprint(
                expr,
                payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
            )
            if not expr or payload_key in seen_payloads:
                reject_counts["throughput_duplicate"] += 1
                continue
            if not self._low_yield_bucket_allowed(payload, selected_bucket_counts):
                reject_counts["throughput_low_yield_bucket_cap"] += 1
                continue
            if _quality_diverse_enabled(self.cfg) and not self._quality_diverse_payload_allowed(
                expr,
                selected_behaviors,
                selected_operator_fields,
                selected_behavior_tokens,
            ):
                reject_counts["throughput_behavior_block"] += 1
                continue
            if self.history_pools.max_similarity(expr, "toxic", early_exit_at=toxic_cap) >= toxic_cap:
                reject_counts["throughput_toxic"] += 1
                continue
            weak_cap = self._weak_history_cap_for(payload, history_similarity_cap)
            if weak_cap is not None:
                if self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=weak_cap) >= weak_cap:
                    reject_counts["throughput_weak_history"] += 1
                    continue
            out.append(payload)
            seen_payloads.add(payload_key)
            selected_bucket_counts[_low_yield_bucket_key_for_payload(payload)] += 1
            if _quality_diverse_enabled(self.cfg):
                self._quality_diverse_payload_remember(expr, selected_behaviors, selected_operator_fields, selected_behavior_tokens)
        return out, reject_counts

    def _quality_diverse_payload_allowed(
        self,
        expr: str,
        selected_behaviors: Counter[str],
        selected_operator_fields: Counter[tuple[str, str]],
        selected_behavior_tokens: list[set[str]],
    ) -> bool:
        behavior = _behavior_signature(expr)
        behavior_cap = int(getattr(self.cfg, "max_behavior_per_batch", 1) or 1)
        if behavior and behavior_cap >= 0 and selected_behaviors[behavior] >= behavior_cap:
            return False
        op_field = _operator_field_key(expr)
        op_field_cap = int(getattr(self.cfg, "max_operator_field_per_batch", 2) or 2)
        if op_field_cap >= 0 and selected_operator_fields[op_field] >= op_field_cap:
            return False
        behavior_cap_sim = float(getattr(self.cfg, "behavior_similarity_cap", 0.78))
        toks = _behavior_token_set(expr)
        if PreSimulationScreener._max_token_similarity(toks, selected_behavior_tokens, early_exit_at=behavior_cap_sim) >= behavior_cap_sim:
            return False
        return True

    def _quality_diverse_payload_remember(
        self,
        expr: str,
        selected_behaviors: Counter[str],
        selected_operator_fields: Counter[tuple[str, str]],
        selected_behavior_tokens: list[set[str]],
    ) -> None:
        behavior = _behavior_signature(expr)
        if behavior:
            selected_behaviors[behavior] += 1
        selected_operator_fields[_operator_field_key(expr)] += 1
        selected_behavior_tokens.append(_behavior_token_set(expr))

    @staticmethod
    def _behavior_diagnostics(payloads: list[dict]) -> dict[str, Any]:
        exprs = [_sig(p.get("regular") or "") for p in payloads if isinstance(p, dict) and _sig(p.get("regular") or "")]
        behaviors = [_behavior_signature(e) for e in exprs if _behavior_signature(e)]
        behavior_counts = Counter(behaviors)
        seed_counts = Counter(
            str((p.get("meta") or {}).get("seed") or (p.get("meta") or {}).get("source_expression") or p.get("regular") or "")
            for p in payloads
            if isinstance(p, dict)
        )
        token_sets = [_behavior_token_set(e) for e in exprs]
        max_sim = 0.0
        for i, toks in enumerate(token_sets):
            if not toks:
                continue
            sim = PreSimulationScreener._max_token_similarity(toks, token_sets[:i])
            max_sim = max(max_sim, sim)
        return {
            "behavior_signature_count": len(behavior_counts),
            "max_behavior_bucket": max(behavior_counts.values(), default=0),
            "seed_variant_count": max(seed_counts.values(), default=0),
            "max_intrabatch_behavior_similarity": round(max_sim, 4),
        }

    def _fine_rank_key_bulk(self, payload: dict) -> tuple:
        rates = getattr(self, "family_pass_rates", None) or {}
        qstats = getattr(self, "family_quality_stats", None) or {}
        return _payload_fine_rank_key(payload, rates, qstats)

    def _fine_rank_key(self, payload: dict) -> tuple:
        return self._fine_rank_key_bulk(payload)


# ---------------------------- queues / feedback ----------------------------

class HopefulQueue:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.path = self._resolve(cfg.hopeful_queue_filename)
        self.submission_path = self._resolve(cfg.submission_results_filename)

    def _resolve(self, name: str) -> Path:
        p = Path(name)
        return p if p.is_absolute() else Path(__file__).resolve().parent / p

    def append(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    def load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        rows: list[dict] = []
        with self.path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
        # Append-only JSONL: keep the latest snapshot per alpha_id so recheck updates win.
        last_idx: dict[str, int] = {}
        for i, obj in enumerate(rows):
            aid = str(obj.get("alpha_id") or "").strip()
            if aid:
                last_idx[aid] = i
        out: list[dict] = []
        for i, obj in enumerate(rows):
            aid = str(obj.get("alpha_id") or "").strip()
            if aid:
                if last_idx.get(aid) != i:
                    continue
            out.append(obj)
        return out

    def submitted_ids(self) -> set[str]:
        if not self.submission_path.is_file():
            return set()
        out = set()
        with self.submission_path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict) and obj.get("status") in ("submitted", "already_submitted", "dry_run"):
                    aid = str(obj.get("alpha_id") or "").strip()
                    if aid:
                        out.add(aid)
        return out

    def log_submission(self, entry: dict) -> None:
        self.submission_path.parent.mkdir(parents=True, exist_ok=True)
        with self.submission_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


# ---------------------------- API pipeline ----------------------------

class WorldQuantAlphaPipeline:
    _BASE = "https://api.worldquantbrain.com"
    _SIM_URL = f"{_BASE}/simulations"
    _SELF_ALPHA_URL = f"{_BASE}/users/self/alphas"

    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.cfg.apply_preset()
        self.queue = HopefulQueue(cfg)
        self._dynamic_submit_sleep = max(0.01, float(cfg.submit_sleep))
        self._last_submit_ts = 0.0
        self._consecutive_429 = 0
        self._consecutive_dns_errors = 0
        self._retry_transient_hits: int = 0
        self._retry_transient_log_ts: float = 0.0
        self._cached_dataset_ids: list[str] | None = None
        self._dataset_ids_cached_at: float = 0.0
        self._run_salt = time.time_ns() & 0xFFFFFFFF
        self._history_seen_exact: set[str] = set()
        self._history_seen_skeleton: set[str] = set()
        self._generated_registry_exact: set[str] = set()
        self._success_blacklist_tokens: list[set[str]] = []
        self._history_submission_tokens: list[set[str]] = []
        self._history_pools = HistorySimilarityPools()
        self._positive_field_counter: Counter[str] = Counter()
        # Feedback-driven indices for pre-simulate screening and near-pass amplification.
        self._tried_expressions: set[str] = set()
        self._tried_payload_keys: set[str] = set()
        self._platform_alpha_ids: set[str] = set()
        self._platform_expression_tokens: list[set[str]] = []  # v50.1: 平台去重 token 池
        self._simulate_snapshot_exprs: set[str] = set()
        self._simulate_snapshot_alpha_ids: set[str] = set()
        self._failed_expressions: set[str] = set()
        self._passed_expressions: set[str] = set()
        self._tried_metrics: dict[str, dict[str, float | None]] = {}
        self._failed_cluster: dict[tuple[str, str], list[float]] = {}
        self._near_pass_records: list[dict[str, Any]] = []
        self._near_pass_expression_set: set[str] = set()
        self._family_pass_rates: dict[str, float] = {}
        self._family_quality_stats: dict[str, dict[str, float]] = {}
        self._family_simulation_counts: Counter[str] = Counter()
        self._novelty_index = NoveltyIndex()
        self._last_prescreen_coarse_count: int = 0
        self._last_prescreen_diagnostic_stats: dict[str, int] = {}
        self._last_prescreen_family_drop_counts: Counter[tuple[str, str]] = Counter()
        self._feedback_blocked_operators: set[str] = set()
        self._feedback_blocked_variables: set[str] = set()
        self._submission_observer: Any | None = None
        self._ssl_ctx = self._make_ssl_context()
        self._apply_ipv4_preference()
        if self.cfg.auth_state_file != PipelineConfig.auth_state_file or not os.environ.get("WQ_AUTH_STATE_FILE"):
            os.environ["WQ_AUTH_STATE_FILE"] = str(self.cfg.auth_state_file)
        os.environ["WQ_AUTH_COOLDOWN_SECONDS"] = str(self.cfg.auth_cooldown_seconds)
        os.environ["WQ_AUTH_DAILY_CAP"] = str(self.cfg.auth_daily_cap)
        os.environ["WQ_AUTH_MAX_ATTEMPTS"] = str(self.cfg.auth_max_retries)
        self.sess = requests.Session()
        self._init_http_session()
        # requests.Session is not thread-safe; async simulate uses to_thread with concurrency.
        self._sess_lock = threading.RLock()

    # ---- paths / ledger ----

    def _path(self, filename: str) -> Path:
        p = Path(filename)
        return p if p.is_absolute() else Path(__file__).resolve().parent / p

    def _feedback_path(self) -> Path:
        return self._path(self.cfg.feedback_ledger_filename)

    def _registry_path(self) -> Path:
        return self._path(self.cfg.generated_expression_registry_filename)

    def _migrate_legacy_feedback_files(self) -> None:
        """Merge versioned feedback CSVs into the single unified ledger (once)."""
        target = self._feedback_path()
        if target.is_file():
            return
        base_dir = Path(__file__).resolve().parent
        sources = _feedback_csv_candidates(base_dir)
        sources = [p for p in sources if p.name != target.name]
        if not sources:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        merged_rows: list[dict[str, str]] = []
        for src in sources:
            try:
                with src.open("r", newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if not isinstance(row, dict):
                            continue
                        out = {k: "" for k in FEEDBACK_FIELDS}
                        for k, v in row.items():
                            if k in out:
                                out[k] = str(v or "")
                        if not out.get("pipeline_version"):
                            m = re.search(r"_v(\d+)", src.stem)
                            out["pipeline_version"] = f"v{m.group(1)}" if m else "legacy"
                        merged_rows.append(out)
            except Exception as e:
                print(f"[feedback] migrate skip {src.name}: {e}")
        if not merged_rows:
            return
        with target.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(FEEDBACK_FIELDS), extrasaction="ignore")
            w.writeheader()
            w.writerows(merged_rows)
        print(f"[feedback] migrated {len(merged_rows)} rows from {len(sources)} legacy file(s) -> {target.name}")

    def _ensure_feedback_header(self) -> None:
        self._migrate_legacy_feedback_files()
        path = self._feedback_path()
        if not path.is_file():
            return
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                header = next(csv.reader(f), [])
        except Exception:
            return
        if header == list(FEEDBACK_FIELDS):
            return
        if set(header).issubset(set(FEEDBACK_FIELDS)):
            try:
                with path.open("r", newline="", encoding="utf-8-sig", errors="ignore") as f:
                    rows = list(csv.DictReader(f))
                upgraded: list[dict[str, Any]] = []
                for row in rows:
                    out = {k: "" for k in FEEDBACK_FIELDS}
                    for k, v in row.items():
                        if k in out:
                            out[k] = v
                    upgraded.append(out)
                with path.open("w", newline="", encoding="utf-8-sig") as f:
                    w = csv.DictWriter(f, fieldnames=list(FEEDBACK_FIELDS), extrasaction="ignore")
                    w.writeheader()
                    w.writerows(upgraded)
                print(f"[feedback] upgraded header -> {path.name} rows={len(upgraded)}")
                return
            except Exception as e:
                print(f"[feedback] header upgrade failed: {e}")
        legacy = path.with_name(f"{path.stem}.legacy_{int(time.time())}{path.suffix}")
        path.replace(legacy)
        print(f"[feedback] rotated legacy header -> {legacy.name}")

    def run_feedback_diagnostics(self) -> Any:
        """Aggregate platform failure text into a reusable diagnostics CSV."""
        base_dir = Path(__file__).resolve().parent
        paths = _feedback_csv_candidates(base_dir)
        out_path = self._path(self.cfg.feedback_diagnostics_filename)
        rows_out: list[dict[str, Any]] = []
        aggregates: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        if not paths:
            rows_out.append({
                "category": "NO_FEEDBACK_FILES",
                "operator": "",
                "variable": "",
                "family": "",
                "source_file": "",
                "count": 0,
                "sample_expression": "",
                "sample_reason": "No feedback/result CSV files were found in the workspace.",
            })
        for path in paths:
            try:
                with path.open("r", newline="", encoding="utf-8-sig", errors="ignore") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if not isinstance(row, dict):
                            continue
                        text = _feedback_text_from_row(row)
                        category = _classify_feedback_reason(text)
                        operator = _feedback_operator(text)
                        variable = _feedback_variable(text)
                        family = str(row.get("family") or row.get("profile") or row.get("source") or "").strip()
                        key = (category, operator, variable, family, path.name)
                        rec = aggregates.get(key)
                        if rec is None:
                            rec = {
                                "category": category,
                                "operator": operator,
                                "variable": variable,
                                "family": family,
                                "source_file": path.name,
                                "count": 0,
                                "sample_expression": _sig(row.get("expression") or "")[:220],
                                "sample_reason": text[:500],
                            }
                            aggregates[key] = rec
                        rec["count"] = int(rec["count"]) + 1
            except Exception as e:
                rows_out.append({
                    "category": "READ_FAILED",
                    "operator": "",
                    "variable": "",
                    "family": "",
                    "source_file": path.name,
                    "count": 0,
                    "sample_expression": "",
                    "sample_reason": str(e)[:500],
                })
        rows_out.extend(sorted(aggregates.values(), key=lambda r: int(r.get("count") or 0), reverse=True))
        fields = ["category", "operator", "variable", "family", "source_file", "count", "sample_expression", "sample_reason"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows_out)
        print(f"[feedback] diagnostics -> {out_path.resolve()} rows={len(rows_out)} sources={len(paths)}")
        if rows_out:
            print("[feedback] top reasons:", {str(r["category"]): int(r.get("count") or 0) for r in rows_out[:8]})
        return pd.DataFrame(rows_out)

    @staticmethod
    def _learning_window(expr: str) -> str:
        wins = [int(x) for x in re.findall(r"ts_[a-z_]+\([^)]*,\s*(\d+)", str(expr or ""), flags=re.I)]
        return str(max(wins)) if wins else ""

    @staticmethod
    def _summary_ratio(num: int, den: int) -> float:
        return round(float(num) / float(den), 6) if den else 0.0

    def write_feedback_learning_summary(self) -> Any:
        """Persist feedback aggregates used by the next simulate-only strategy iteration."""
        self._ensure_feedback_header()
        path = self._feedback_path()
        out_csv = self._path(self.cfg.feedback_learning_summary_csv)
        out_json = self._path(self.cfg.feedback_learning_summary_json)
        check_distribution_csv = self._path(self.cfg.feedback_check_distribution_csv)
        check_distribution: Counter[tuple[str, str]] = Counter()
        check_total = 0
        if not path.is_file():
            rows_out = [{
                "family": "",
                "source": "",
                "operator": "",
                "field": "",
                "window": "",
                "Neutralization": "",
                "Decay": "",
                "Truncation": "",
                "count": 0,
                "metric_gate_pass_rate": 0.0,
                "platform_pass_evidence_rate": 0.0,
                "negative_rate": 0.0,
                "zeroish_rate": 0.0,
                "turnover_fail_rate": 0.0,
                "self_correlation_pending_rate": 0.0,
                "self_correlation_fail_rate": 0.0,
                "prod_correlation_pending_rate": 0.0,
                "prod_correlation_fail_rate": 0.0,
                "low_sub_universe_fail_rate": 0.0,
                "concentrated_weight_fail_rate": 0.0,
                "top_blocked_reason": "NO_FEEDBACK",
            }]
        else:
            groups: dict[tuple[str, ...], dict[str, Any]] = {}
            with path.open("r", newline="", encoding="utf-8-sig", errors="ignore") as f:
                for row in csv.DictReader(f):
                    expr = row.get("expression") or ""
                    family = row.get("family") or "unknown"
                    source = row.get("source") or "unknown"
                    operator = _feedback_operator(expr) or "unknown"
                    field = _feedback_variable(expr) or "unknown"
                    window = self._learning_window(expr)
                    key = (
                        family,
                        source,
                        operator,
                        field,
                        window,
                        str(row.get("Neutralization") or ""),
                        str(row.get("Decay") or ""),
                        str(row.get("Truncation") or ""),
                    )
                    g = groups.setdefault(
                        key,
                        {
                            "count": 0,
                            "metric_gate_pass": 0,
                            "platform_pass_evidence": 0,
                            "negative": 0,
                            "zeroish": 0,
                            "turnover_fail": 0,
                            "self_correlation_pending": 0,
                            "self_correlation_fail": 0,
                            "prod_correlation_pending": 0,
                            "prod_correlation_fail": 0,
                            "low_sub_universe_fail": 0,
                            "concentrated_weight_fail": 0,
                            "blocked": Counter(),
                        },
                    )
                    g["count"] += 1
                    sh = _to_float(row.get("Sharpe"))
                    fi = _to_float(row.get("Fitness"))
                    to = _to_float(row.get("Turnover"))
                    metrics = {"sharpe": sh, "fitness": fi, "turnover": to}
                    metric_pass = str(row.get("metric_gate_pass") or "").lower() == "true"
                    if not row.get("metric_gate_pass"):
                        metric_pass = _metric_gate_pass_proxy(metrics)[0]
                    if metric_pass:
                        g["metric_gate_pass"] += 1
                    if str(row.get("platform_pass_evidence") or "").lower() == "true":
                        g["platform_pass_evidence"] += 1
                    if (sh is not None and sh < 0) or (fi is not None and fi < 0):
                        g["negative"] += 1
                    if (sh is not None and abs(sh) < 0.05) or (fi is not None and abs(fi) < 0.05):
                        g["zeroish"] += 1
                    if to is not None and (to < 0.01 or to >= 0.70):
                        g["turnover_fail"] += 1
                    self_status = str(row.get("self_correlation_status") or "").upper()
                    if self_status == "PENDING":
                        g["self_correlation_pending"] += 1
                    if self_status == "FAIL":
                        g["self_correlation_fail"] += 1
                    checks = _check_summary(_json_load_maybe(row.get("platform_check_json")))
                    for check in _extract_checks(_json_load_maybe(row.get("platform_check_json"))):
                        name = str(check.get("name") or "UNKNOWN").upper()
                        result = str(check.get("result") or "UNKNOWN").upper()
                        check_distribution[(name, result)] += 1
                        check_total += 1
                        if name == "PROD_CORRELATION":
                            if result == "PENDING":
                                g["prod_correlation_pending"] += 1
                            elif result in ("FAIL", "FAILED", "REJECTED"):
                                g["prod_correlation_fail"] += 1
                    if "LOW_SUB_UNIVERSE_SHARPE:FAIL" in checks:
                        g["low_sub_universe_fail"] += 1
                    if "CONCENTRATED_WEIGHT:FAIL" in checks:
                        g["concentrated_weight_fail"] += 1
                    blocked = str(row.get("blocked_reason") or row.get("queue_status") or "").strip()
                    if blocked:
                        g["blocked"][blocked] += 1

            rows_out = []
            for key, g in groups.items():
                n = int(g["count"])
                blocked = g["blocked"].most_common(1)
                rows_out.append({
                    "family": key[0],
                    "source": key[1],
                    "operator": key[2],
                    "field": key[3],
                    "window": key[4],
                    "Neutralization": key[5],
                    "Decay": key[6],
                    "Truncation": key[7],
                    "count": n,
                    "metric_gate_pass_rate": self._summary_ratio(int(g["metric_gate_pass"]), n),
                    "platform_pass_evidence_rate": self._summary_ratio(int(g["platform_pass_evidence"]), n),
                    "negative_rate": self._summary_ratio(int(g["negative"]), n),
                    "zeroish_rate": self._summary_ratio(int(g["zeroish"]), n),
                    "turnover_fail_rate": self._summary_ratio(int(g["turnover_fail"]), n),
                    "self_correlation_pending_rate": self._summary_ratio(int(g["self_correlation_pending"]), n),
                    "self_correlation_fail_rate": self._summary_ratio(int(g["self_correlation_fail"]), n),
                    "prod_correlation_pending_rate": self._summary_ratio(int(g["prod_correlation_pending"]), n),
                    "prod_correlation_fail_rate": self._summary_ratio(int(g["prod_correlation_fail"]), n),
                    "low_sub_universe_fail_rate": self._summary_ratio(int(g["low_sub_universe_fail"]), n),
                    "concentrated_weight_fail_rate": self._summary_ratio(int(g["concentrated_weight_fail"]), n),
                    "top_blocked_reason": blocked[0][0] if blocked else "",
                })
            rows_out.sort(
                key=lambda r: (
                    -float(r["metric_gate_pass_rate"]),
                    float(r["self_correlation_fail_rate"]),
                    float(r["negative_rate"]),
                    -int(r["count"]),
                )
            )

        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
            fieldnames = list(rows_out[0].keys()) if rows_out else []
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows_out)
        with out_json.open("w", encoding="utf-8") as f:
            json.dump(rows_out, f, ensure_ascii=False, indent=2)
        distribution_rows = [
            {
                "check_name": name,
                "result": result,
                "count": count,
                "rate": self._summary_ratio(count, check_total),
            }
            for (name, result), count in sorted(check_distribution.items(), key=lambda item: (-item[1], item[0]))
        ]
        with check_distribution_csv.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["check_name", "result", "count", "rate"])
            w.writeheader()
            w.writerows(distribution_rows)
        print(f"[feedback] learning summary -> {out_csv.name} / {out_json.name} rows={len(rows_out)}")
        return pd.DataFrame(rows_out)

    def failure_reason_for_row(
        self,
        merged: dict | None,
        *,
        sim_json: dict | None = None,
        status: str = "",
        check_note: str = "",
    ) -> str:
        return _failure_reason_for_ledger(merged, sim_json=sim_json, status=status, check_note=check_note)

    def build_simulate_row(self, **kwargs: Any) -> dict[str, Any]:
        return _simulate_result_row(**kwargs)

    def _phase4_bridge(self) -> Any | None:
        if not getattr(self.cfg, "sqlite_runs_path", None):
            return None
        from alpha_mining.integration.phase4 import Phase4ResearchMemoryBridge
        from alpha_mining.storage.sqlite_store import SqliteRunLog

        return Phase4ResearchMemoryBridge(SqliteRunLog(self.cfg.sqlite_runs_path))

    def _submission_observation_service(self) -> Any | None:
        if not getattr(self.cfg, "submission_observe_enabled", False):
            return None
        if not getattr(self.cfg, "sqlite_runs_path", None):
            return None
        if self._submission_observer is None:
            from alpha_mining.llm.deepseek import DeepSeekStructuredLLM
            from alpha_mining.storage.sqlite_store import SqliteRunLog
            from alpha_mining.submitter.observation import SubmissionObservationService

            self._submission_observer = SubmissionObservationService(
                SqliteRunLog(self.cfg.sqlite_runs_path),
                llm_factory=DeepSeekStructuredLLM,
                description_limit=int(getattr(self.cfg, "submission_observe_description_limit", 20)),
            )
        return self._submission_observer

    def _phase235_llm_candidates(
        self,
        catalog: Any,
        factory: Any,
        *,
        existing_expressions: set[str],
    ) -> list[Any]:
        """Generate LLM-grammar candidates via Phase 2/3 modules. Returns ExpressionCandidate list."""
        if not getattr(self.cfg, "sqlite_runs_path", None):
            return []
        try:
            from alpha_mining.generator.expression import ExpressionGenerator, ExpressionGenerationError
            from alpha_mining.filter.diversity_gate import DiversityGate, DiversityGateError
            from alpha_mining.llm.deepseek import DeepSeekStructuredLLM
            from alpha_mining.llm.local_embedding import LocalSentenceTransformerEmbedder
            from alpha_mining.storage.sqlite_store import SqliteRunLog
        except ImportError as exc:
            print(f"[phase235] import failed, skipping: {exc}")
            return []

        db_path = str(self.cfg.sqlite_runs_path)
        limit = max(1, int(getattr(self.cfg, "phase23_hypotheses_per_call", 3)))

        # Auto-install seed topics on first run if research_topics table is empty.
        try:
            import sqlite3 as _sqlite3
            SqliteRunLog(db_path).initialize_schema()
            with _sqlite3.connect(db_path) as _con:
                _count = _con.execute(
                    "SELECT COUNT(*) FROM research_topics WHERE active=1"
                ).fetchone()[0]
            if _count == 0:
                from alpha_mining.knowledge.ontology import install_seed_topics
                _installed = install_seed_topics(db_path)
                print(f"[phase235] auto-installed {_installed} seed topics (first run)")
        except Exception as _exc:
            print(f"[phase235] seed-topic auto-init warning: {_exc}")

        try:
            llm = DeepSeekStructuredLLM()
        except (ValueError, Exception) as exc:
            print(f"[phase235] LLM init failed, skipping: {exc}")
            return []

        hypothesis_ids: list[str] = []

        if getattr(self.cfg, "phase2_llm_enabled", False):
            try:
                from alpha_mining.generator.idea import IdeaGenerator, InsufficientCategoryCoverage
                from alpha_mining.generator.hypothesis import HypothesisGenerator, DuplicateHypothesisError
                from alpha_mining.generator.data_mapping import DataMappingGenerator
                embedder = LocalSentenceTransformerEmbedder()
                idea_gen = IdeaGenerator(db_path)
                hyp_gen = HypothesisGenerator(
                    db_path, llm=llm, embedder=embedder, model_id=llm.model_id
                )
                mapping_gen = DataMappingGenerator(db_path, llm=llm)
                batch = idea_gen.select_topics(count=max(3, limit))
                for topic_id in list(batch.topic_ids)[:limit]:
                    try:
                        hyp = hyp_gen.generate(topic_id)
                        try:
                            mapping_gen.generate(hyp.hypothesis_id, catalog)
                        except Exception as exc2:
                            print(f"[phase235] data_mapping failed for {hyp.hypothesis_id}: {exc2}")
                        hypothesis_ids.append(hyp.hypothesis_id)
                    except (DuplicateHypothesisError, Exception) as exc2:
                        print(f"[phase235] hypothesis gen failed for topic {topic_id}: {exc2}")
            except (InsufficientCategoryCoverage, Exception) as exc:
                print(f"[phase235] phase2 pipeline failed: {exc}")
        else:
            import sqlite3 as _sqlite3
            try:
                with _sqlite3.connect(db_path) as con:
                    rows = con.execute(
                        "SELECT hypothesis_id FROM hypotheses WHERE status='active' "
                        "ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                hypothesis_ids = [str(row[0]) for row in rows]
            except Exception as exc:
                print(f"[phase235] failed to load hypothesis_ids from DB: {exc}")

        if not hypothesis_ids:
            print("[phase235] no hypothesis_ids available, skipping LLM expression generation")
            return []

        # ExpressionGenerator calls validator.validate() and factory._quality_gate().
        # The factory carries the real PreflightValidator on .validator; passing the
        # factory itself as the validator raises "no attribute 'validate'".
        expr_validator = getattr(factory, "validator", None) or factory
        try:
            expr_gen = ExpressionGenerator(
                db_path, llm=llm, validator=expr_validator, factory=factory
            )
        except Exception as exc:
            print(f"[phase235] ExpressionGenerator init failed: {exc}")
            return []

        diversity_gate: Any = None
        if getattr(self.cfg, "phase3_diversity_gate_enabled", False):
            try:
                embedder2 = LocalSentenceTransformerEmbedder()
                diversity_gate = DiversityGate(db_path, embedder2)
            except Exception as exc:
                print(f"[phase235] DiversityGate init failed: {exc}")

        seen = set(existing_expressions)
        results: list[Any] = []
        for hyp_id in hypothesis_ids:
            try:
                generated = expr_gen.generate_llm_grammar(hyp_id, limit=8)
            except ExpressionGenerationError as exc:
                print(f"[phase235] llm_grammar failed for hypothesis {hyp_id}: {exc}")
                continue
            except Exception as exc:
                print(f"[phase235] unexpected error for hypothesis {hyp_id}: {exc}")
                continue
            for expr in generated:
                if expr.expression_text in seen:
                    continue
                if diversity_gate is not None:
                    try:
                        decision = diversity_gate.check(
                            expr.expression_text, expression_id=expr.expression_id
                        )
                        if not decision.accepted:
                            print(
                                f"[phase235] diversity gate rejected {expr.expression_id}: "
                                f"{decision.reason}"
                            )
                            continue
                    except DiversityGateError as exc:
                        print(f"[phase235] diversity gate error: {exc}")
                        continue
                results.append(
                    ExpressionCandidate(
                        expr.expression_text,
                        expr.generation_strategy,
                        "phase3_llm_grammar",
                        0.0,
                    )
                )
                seen.add(expr.expression_text)

        if results:
            print(f"[phase235] +{len(results)} llm_grammar from {len(hypothesis_ids)} hypotheses")
        return results

    def _phase5_update_judge_scores(self) -> None:
        """Score all metric_pass expressions in Research Memory using SubmissionJudge."""
        if not getattr(self.cfg, "sqlite_runs_path", None):
            return
        try:
            from alpha_mining.filter.submission_judge import SubmissionJudge
            from alpha_mining.storage.sqlite_store import SqliteRunLog
        except ImportError as exc:
            print(f"[phase5] import failed: {exc}")
            return
        db = SqliteRunLog(self.cfg.sqlite_runs_path)
        try:
            judge = SubmissionJudge()
            ref_embs, ref_exprs, ref_cats = judge._load_reference(db)
        except Exception as exc:
            print(f"[phase5] SubmissionJudge init failed: {exc}")
            return
        import sqlite3 as _sqlite3
        import struct as _struct
        try:
            with _sqlite3.connect(str(self.cfg.sqlite_runs_path)) as con:
                rows = con.execute(
                    """
                    SELECT e.expression_id, e.expression_text, e.embedding,
                           sr.sharpe, sr.fitness,
                           COALESCE(t.data_category, '') AS data_category
                    FROM expressions e
                    JOIN simulation_runs sr ON sr.expression_id = e.expression_id
                    LEFT JOIN hypotheses h ON e.hypothesis_id = h.hypothesis_id
                    LEFT JOIN research_topics t ON h.topic_id = t.topic_id
                    WHERE (sr.status = 'metric_pass' OR sr.status = 'submitted')
                    """,
                ).fetchall()
        except Exception as exc:
            print(f"[phase5] DB read failed: {exc}")
            return
        scored = 0
        for expression_id, expression_text, emb_blob, sharpe, fitness, data_category in rows:
            emb: list[float] | None = None
            if emb_blob:
                n = len(emb_blob) // 4
                if n > 0:
                    emb = list(_struct.unpack(f"{n}f", emb_blob[:n * 4]))
            try:
                js = judge.score(
                    expression_id=expression_id,
                    expression_text=expression_text,
                    sharpe=sharpe,
                    fitness=fitness,
                    embedding=emb,
                    data_category=data_category or None,
                    ref_embeddings=ref_embs,
                    ref_expressions=ref_exprs,
                    ref_categories=ref_cats,
                )
                judge.persist_score(db, js)
                scored += 1
            except Exception as exc:
                print(f"[phase5] score failed for {expression_id}: {exc}")
        if scored:
            print(f"[phase5] updated submission_priority_score for {scored} expressions")

    def phase4_mutate_near_pass_records(
        self,
        records: list[dict[str, Any]],
        validator: PreflightValidator,
        *,
        existing_expressions: set[str],
    ) -> list[dict[str, Any]]:
        """Bridge existing near-pass seeds into offline L5 candidate records."""
        if not getattr(self.cfg, "phase4_mutation_enabled", False):
            return []
        bridge = self._phase4_bridge()
        if bridge is None:
            return []
        return bridge.mutate_near_pass_records(
            records,
            validate=validator.validate,
            existing_expressions=existing_expressions,
        )

    def _append_feedback(
        self,
        payload: dict,
        alpha_id: str | None,
        sim_json: dict | None,
        status: str,
        check_passed: bool | None,
        check_note: str,
        queue_status: str,
        submitted: bool = False,
        submit_note: str = "",
        check_json: dict | None = None,
        merged_json: dict | None = None,
    ) -> None:
        path = self._feedback_path()
        self._ensure_feedback_header()
        path.parent.mkdir(parents=True, exist_ok=True)
        settings = payload.get("settings", {})
        result_json = merged_json if isinstance(merged_json, dict) else _merge_json_dicts(sim_json, check_json)
        metrics = {
            "sharpe": _to_float(_metric_get(result_json, "sharpe", "Sharpe")),
            "fitness": _to_float(_metric_get(result_json, "fitness", "Fitness")),
            "turnover": _to_float(_metric_get(result_json, "turnover", "Turnover")),
        }
        analysis = _feedback_analysis_fields(
            metrics,
            result_json,
            check_passed=check_passed,
            check_note=check_note,
            queue_status=queue_status,
        )
        row = {
            "utc_iso": _utc(),
            "pipeline_version": str(self.cfg.pipeline_version or PIPELINE_VERSION),
            "alpha_id": alpha_id or "",
            "simulation_id": str((sim_json or {}).get("id") or ""),
            "expression": payload.get("regular", ""),
            "family": payload.get("meta", {}).get("family", ""),
            "source": payload.get("meta", {}).get("source", ""),
            "profile": payload.get("meta", {}).get("profile", ""),
            "status": status,
            "queue_status": queue_status,
            "submitted": submitted,
            "submit_note": submit_note,
            "check_passed": check_passed,
            "check_note": check_note,
            "Region": settings.get("region", ""),
            "Universe": settings.get("universe", ""),
            "Neutralization": settings.get("neutralization", ""),
            "Decay": settings.get("decay", ""),
            "Truncation": settings.get("truncation", ""),
            "Delay": settings.get("delay", ""),
            "Sharpe": metrics["sharpe"],
            "Fitness": metrics["fitness"],
            "Turnover": metrics["turnover"],
            "Returns": _to_float(_metric_get(result_json, "returns", "Returns")),
            "Drawdown": _to_float(_metric_get(result_json, "drawdown", "Drawdown")),
            "Margin": _to_float(_metric_get(result_json, "margin", "Margin")),
            **analysis,
            "Failure Reasons": _failure_reason_for_ledger(result_json, sim_json=sim_json, status=status, check_note=check_note),
            "platform_simulation_json": _json_compact(sim_json),
            "platform_check_json": _json_compact(check_json),
        }
        if getattr(self.cfg, "phase4_repair_enabled", False):
            bridge = self._phase4_bridge()
            if bridge is not None:
                bridge.record_feedback_result(
                    expression=str(row["expression"]),
                    failure_detail=str(row["Failure Reasons"]),
                    check_passed=check_passed,
                    generation_strategy=str(row["family"] or "pipeline_feedback"),
                )
        if getattr(self.cfg, "submission_observe_enabled", False):
            try:
                observer = self._submission_observation_service()
                if observer is not None:
                    observer.observe(
                        alpha_id=alpha_id,
                        expression=str(row["expression"]),
                        checks=_extract_checks(result_json),
                        metrics=metrics,
                        queue_status=queue_status,
                        check_passed=check_passed,
                        failure_detail=str(row["Failure Reasons"]),
                        family=str(row["family"]),
                        source=str(row["source"]),
                    )
            except Exception as exc:
                print(f"[submission-observe] skipped: {type(exc).__name__}")
        new_file = not path.is_file()
        with path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(FEEDBACK_FIELDS), extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerow(row)
        if new_file:
            print(f"[feedback] created ledger -> {path.resolve()}")

    def _cleanup_stale_poll_only(self) -> None:
        """v50.3: 修复上次运行遗留的 poll_only:not_checked 条目 — 重新跑 check.

        async_batch 在 simulate 完成后立即写 poll_only:not_checked，
        然后 detail_and_check 异步更新。如果进程中断，这些条目会永远保留
        poll_only 状态。此方法每次 pre-flight 时清理它们。
        """
        path = self._feedback_path()
        if not path.is_file():
            return
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return
        stale = [r for r in rows if str(r.get("queue_status") or "").strip() == "poll_only:not_checked"
                 and str(r.get("alpha_id") or "").strip()]
        if not stale:
            return
        stale.sort(key=self._feedback_cleanup_priority_key)
        max_n = max(1, int(getattr(self.cfg, "cleanup_poll_only_max_per_run", 20) or 20))
        batch = stale[:max_n]
        defer = len(stale) - len(batch)
        print(
            f"[cleanup] poll_only stale={len(stale)} run={len(batch)}"
            + (f" defer={defer}" if defer else "")
        )
        fixed = 0
        outcomes: Counter[str] = Counter()
        fail_n = 0
        t0 = time.time()
        last_prog_ts = t0
        quick_s = float(getattr(self.cfg, "cleanup_check_max_seconds", 45.0) or 45.0)
        wall_budget = max(0.0, float(getattr(self.cfg, "cleanup_poll_only_wall_budget_seconds", 300.0) or 0.0))
        for i, row in enumerate(batch, start=1):
            if wall_budget > 0 and time.time() - t0 >= wall_budget:
                remaining = len(batch) - i + 1
                print(
                    f"[cleanup] wall budget reached {wall_budget:.0f}s; "
                    f"fixed={fixed}/{len(batch)} defer={defer + remaining}"
                )
                break
            alpha_id = str(row.get("alpha_id") or "").strip()
            if not alpha_id:
                continue
            item_t0 = time.time()
            try:
                detail = self.fetch_alpha_detail(alpha_id)
                if not isinstance(detail, dict):
                    outcomes["no_detail"] += 1
                    # v50.6: alpha不存在/已失效 → 直接标记，不重试
                    for j, r in enumerate(rows):
                        if str(r.get("alpha_id") or "").strip() == alpha_id:
                            rows[j]["queue_status"] = "not_queued:detail_fetch_failed"
                            rows[j]["check_note"] = "detail_fetch_failed"
                            fixed += 1
                            break
                    continue
                classified = _try_classify_detail_without_poll(detail)
                check_json: dict | None = None
                if classified is not None:
                    check_passed, check_note = classified
                else:
                    check_passed, check_json, check_note = self.check_alpha(
                        alpha_id,
                        max_wait_seconds=quick_s,
                        allow_self_corr_extend=False,
                    )
                merged = _merge_json_dicts(detail, check_json) if check_json else detail
                expr = _sig(row.get("expression") or "")
                payload = {"regular": expr, "settings": {}, "meta": {"profile": "cleanup"}}
                queue_status, _ = self.queue_decision(payload, alpha_id, merged, check_passed, check_note)
                note_key = (check_note or queue_status or "unknown").split(":")[0][:40]
                outcomes[note_key] += 1
                for j, r in enumerate(rows):
                    if str(r.get("alpha_id") or "").strip() == alpha_id:
                        rows[j]["queue_status"] = queue_status
                        rows[j]["check_passed"] = str(check_passed) if check_passed is not None else ""
                        rows[j]["check_note"] = check_note
                        if check_json:
                            rows[j]["platform_check_json"] = json.dumps(check_json)[:8000]
                        fixed += 1
                        break
            except Exception as e:
                fail_n += 1
                outcomes["error"] += 1
                if fail_n <= 2:
                    print(f"[cleanup] error alpha_id={alpha_id[:12]}: {e}")
            now = time.time()
            if i == 1 or i == len(batch) or i % 5 == 0 or (now - last_prog_ts) >= 30:
                elapsed = now - t0
                item_s = now - item_t0
                print(
                    f"[cleanup] {i}/{len(batch)} fixed={fixed} last={item_s:.0f}s "
                    f"top={dict(outcomes.most_common(3))} total={elapsed:.0f}s"
                )
                last_prog_ts = now
        if fixed:
            with path.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(FEEDBACK_FIELDS), extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
        elapsed = time.time() - t0
        print(
            f"[cleanup] done fixed={fixed}/{len(batch)} elapsed={elapsed:.0f}s "
            f"outcomes={dict(outcomes.most_common(6))}"
            + (f" defer={defer}" if defer else "")
        )

    def _upsert_feedback_by_alpha_id(self, alpha_id: str, row_patch: dict[str, Any]) -> None:
        """Update the last ledger row for alpha_id, or append if missing."""
        aid = str(alpha_id or "").strip()
        if not aid:
            return
        path = self._feedback_path()
        self._ensure_feedback_header()
        rows: list[dict[str, str]] = []
        if path.is_file():
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        idx = None
        for i in range(len(rows) - 1, -1, -1):
            if str(rows[i].get("alpha_id") or "").strip() == aid:
                idx = i
                break
        if idx is not None:
            for k, v in row_patch.items():
                if k in FEEDBACK_FIELDS and v is not None:
                    rows[idx][k] = v if not isinstance(v, bool) else str(v)
            target_row = rows[idx]
        else:
            base = {k: "" for k in FEEDBACK_FIELDS}
            base.update(row_patch)
            rows.append(base)
            target_row = base
        detail = _json_load_maybe(target_row.get("platform_check_json")) or {}
        metrics = {
            "sharpe": _to_float(target_row.get("Sharpe")),
            "fitness": _to_float(target_row.get("Fitness")),
            "turnover": _to_float(target_row.get("Turnover")),
        }
        analysis = _feedback_analysis_fields(
            metrics,
            detail,
            check_passed=(str(target_row.get("check_passed") or "").lower() == "true"),
            check_note=str(target_row.get("check_note") or ""),
            queue_status=str(target_row.get("queue_status") or ""),
        )
        for k, v in analysis.items():
            target_row[k] = str(v) if isinstance(v, bool) else v
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(FEEDBACK_FIELDS), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    def _feedback_metrics_for_alpha(self, alpha_id: str | None) -> dict[str, float | None]:
        aid = str(alpha_id or "").strip()
        if not aid:
            return {}
        path = self._feedback_path()
        if not path.is_file():
            return {}
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return {}
        for row in reversed(rows):
            if str(row.get("alpha_id") or "").strip() != aid:
                continue
            metrics = {
                "sharpe": _to_float(row.get("Sharpe")),
                "fitness": _to_float(row.get("Fitness")),
                "turnover": _to_float(row.get("Turnover")),
                "returns": _to_float(row.get("Returns")),
                "drawdown": _to_float(row.get("Drawdown")),
                "margin": _to_float(row.get("Margin")),
            }
            return {k: v for k, v in metrics.items() if v is not None}
        return {}

    def fetch_recent_alphas(self, limit: int = 500) -> list[dict[str, Any]]:
        """Paginate GET /users/self/alphas ordered by -dateCreated."""
        limit = max(1, int(limit))
        out: list[dict[str, Any]] = []
        offset = 0
        while len(out) < limit:
            try:
                r = self._sess_request(
                    "GET",
                    self._SELF_ALPHA_URL,
                    params={"limit": 100, "offset": offset, "order": "-dateCreated"},
                    timeout=self._timeout(),
                )
            except Exception as e:
                print(f"[analyze] list alphas failed offset={offset}: {e}")
                break
            if r.status_code != 200:
                print(f"[analyze] list HTTP {r.status_code} offset={offset}")
                break
            data = r.json()
            rows = data.get("results") or []
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                aid = str(row.get("id") or row.get("alpha") or "").strip()
                expr = ""
                regular = row.get("regular")
                if isinstance(regular, dict):
                    expr = _sig(regular.get("code") or regular.get("regular") or "")
                elif isinstance(regular, str):
                    expr = _sig(regular)
                out.append({
                    "alpha_id": aid,
                    "expression": expr,
                    "date_created": str(row.get("dateCreated") or row.get("created") or ""),
                    "name": str(row.get("name") or ""),
                })
                if len(out) >= limit:
                    break
            if len(rows) < 100:
                break
            offset += 100
            time.sleep(self.cfg.page_sleep)
        return out[:limit]

    def run_analyze_recent(self, limit: int = 500) -> pd.DataFrame:
        """Pull recent platform alphas, fetch metrics, write alpha_recent_platform_analysis.csv."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        self.ensure_authenticated(force=False)
        summaries = self.fetch_recent_alphas(limit)
        print(f"[analyze] listed {len(summaries)} alphas from platform")
        if not summaries:
            return pd.DataFrame()

        MIN_S = float(self.cfg.min_sharpe_threshold) - 0.01
        MIN_F = float(self.cfg.min_fitness_threshold)
        results: list[dict[str, Any]] = []

        def fetch_one(item: dict[str, Any]) -> dict[str, Any]:
            aid = item["alpha_id"]
            detail = self.fetch_alpha_detail(aid) if aid else None
            merged = detail if isinstance(detail, dict) else {}
            sh = _to_float(_metric_get(merged, "sharpe", "Sharpe"))
            ft = _to_float(_metric_get(merged, "fitness", "Fitness"))
            to = _to_float(_metric_get(merged, "turnover", "Turnover"))
            expr = item.get("expression") or _sig(
                str((merged.get("regular") or {}) if isinstance(merged.get("regular"), dict) else merged.get("regular") or "")
            )
            fam = str(item.get("family") or "")
            bucket = _expression_pattern_bucket(expr, fam)
            pass_both = sh is not None and ft is not None and sh >= MIN_S and ft >= MIN_F
            return {
                "alpha_id": aid,
                "date_created": item.get("date_created", ""),
                "expression": expr[:500],
                "pattern_bucket": bucket,
                "toxic_price_leg": _has_toxic_near_pass_price_leg(expr),
                "Sharpe": sh,
                "Fitness": ft,
                "Turnover": to,
                "pass_sharpe": bool(sh is not None and sh >= MIN_S),
                "pass_fitness": bool(ft is not None and ft >= MIN_F),
                "pass_both": pass_both,
            }

        workers = max(2, min(8, int(getattr(self.cfg, "analyze_recent_workers", 6) or 6)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(fetch_one, it): it for it in summaries if it.get("alpha_id")}
            done_n = 0
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as e:
                    print(f"[analyze] detail failed: {e}")
                done_n += 1
                if done_n == 1 or done_n % 50 == 0 or done_n == len(futs):
                    print(f"[analyze] detail progress {done_n}/{len(futs)}")

        df = pd.DataFrame(results)
        if df.empty:
            print("[analyze] no metrics retrieved")
            return df

        n = len(df)
        pb = int(df["pass_both"].sum()) if "pass_both" in df.columns else 0
        ps = int(df["pass_sharpe"].sum()) if "pass_sharpe" in df.columns else 0
        pf = int(df["pass_fitness"].sum()) if "pass_fitness" in df.columns else 0
        print(
            f"[analyze] n={n} pass_both={pb} ({100*pb/n:.1f}%) "
            f"sharpe_ok={ps} ({100*ps/n:.1f}%) fitness_ok={pf} ({100*pf/n:.1f}%)"
        )
        if "Sharpe" in df.columns:
            sh = df["Sharpe"].dropna()
            if len(sh):
                print(f"[analyze] sharpe median={sh.median():.3f} p90={sh.quantile(0.9):.3f} max={sh.max():.3f}")
        if "pattern_bucket" in df.columns:
            grp = df.groupby("pattern_bucket").agg(
                n=("alpha_id", "count"),
                pass_both=("pass_both", "sum"),
                sharpe_med=("Sharpe", "median"),
            ).sort_values("n", ascending=False)
            print("[analyze] by_pattern:")
            for bucket, row in grp.iterrows():
                cnt = int(row["n"])
                p = int(row["pass_both"])
                print(f"  {bucket}: n={cnt} pass_both={p} ({100*p/max(1,cnt):.1f}%) sharpe_med={row.get('sharpe_med', float('nan')):.3f}")

        toxic = df[df["toxic_price_leg"] == True] if "toxic_price_leg" in df.columns else df.iloc[0:0]
        if len(toxic):
            tp = int(toxic["pass_both"].sum()) if "pass_both" in toxic.columns else 0
            print(f"[analyze] toxic_price_leg n={len(toxic)} pass_both={tp} ({100*tp/len(toxic):.1f}%)")

        top = df.sort_values(["pass_both", "Sharpe", "Fitness"], ascending=False).head(10)
        print("[analyze] top10 pass:")
        for _, r in top.iterrows():
            print(f"  sh={r.get('Sharpe')} ft={r.get('Fitness')} {r.get('pattern_bucket')} {str(r.get('alpha_id',''))[:12]}")

        out_path = self._path("alpha_recent_platform_analysis.csv")
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"[analyze] saved -> {out_path.resolve()}")
        return df

    def _expr_tokens(self, expr: str) -> set[str]:
        return set(re.findall(r"[a-z_]+|\d+", _sig(expr).lower()))

    def _similarity_to_winners(self, expr: str) -> float:
        toks = self._expr_tokens(expr)
        pools = self._success_blacklist_tokens or self._history_submission_tokens
        if not toks or not pools:
            return 0.0
        best = 0.0
        for old in pools:
            inter = len(toks & old)
            union = len(toks | old)
            if union <= 0:
                continue
            best = max(best, inter / union)
        return best

    def _platform_library_similarity(self, expr: str, *, early_exit_at: float | None = 0.88) -> float:
        """v50.1: 快速检查与平台已有 alpha 的 Jaccard 相似度，用于 pre-submit 去重."""
        toks = _expr_token_set(expr)
        pool = getattr(self, "_platform_expression_tokens", None)
        if not toks or not isinstance(pool, list) or not pool:
            return 0.0
        return _max_pool_similarity(toks, pool, early_exit_at=early_exit_at)

    def _metric_gate(self, metrics: dict[str, float | None]) -> tuple[bool, str]:
        sharpe = metrics.get("sharpe")
        fitness = metrics.get("fitness")
        turnover = metrics.get("turnover")
        returns = metrics.get("returns")
        drawdown = metrics.get("drawdown")
        margin = metrics.get("margin")
        if sharpe is None or fitness is None or turnover is None:
            return False, "missing_core_metrics"
        if sharpe <= self.cfg.min_sharpe_threshold:
            return False, "sharpe_below_threshold"
        if fitness <= self.cfg.min_fitness_threshold:
            return False, "fitness_below_threshold"
        if turnover < self.cfg.min_turnover_threshold or turnover >= self.cfg.max_turnover_threshold:
            return False, "turnover_out_of_range"
        if returns is not None and returns < self.cfg.queue_min_returns:
            return False, "returns_too_low"
        if drawdown is not None and drawdown > self.cfg.queue_max_drawdown:
            return False, "drawdown_too_high"
        if margin is not None and margin < self.cfg.queue_min_margin:
            return False, "margin_too_low"
        return True, "ok"

    @staticmethod
    def _invert_expression(expr: str) -> str:
        s = _sig(expr)
        if s.startswith("-(") and s.endswith(")"):
            return _sig(s[2:-1])
        if s.startswith("-") and "(" not in s[:2]:
            return _sig(s[1:])
        return _sig(f"-({s})")

    def _load_generated_registry(self) -> None:
        self._generated_registry_exact = set()
        path = self._registry_path()
        if not path.is_file():
            return
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    expr = _sig(row.get("expression") or "")
                    if expr:
                        self._generated_registry_exact.add(expr)
        except Exception as e:
            print(f"[registry] read failed: {e}")

    def _append_generated_registry(self, candidates: list[ExpressionCandidate]) -> None:
        if not candidates:
            return
        path = self._registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for c in candidates:
            expr = _sig(c.expression)
            if not expr or expr in self._generated_registry_exact:
                continue
            self._generated_registry_exact.add(expr)
            self._history_seen_exact.add(expr)
            self._history_seen_skeleton.add(_skel(expr))
            rows.append({"utc_iso": _utc(), "run_salt": self._run_salt, "family": c.family, "source": c.source, "score": f"{c.score:.4f}", "expression": expr})
        if not rows:
            return
        new_file = not path.is_file()
        with path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["utc_iso", "run_salt", "family", "source", "score", "expression"], extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerows(rows)
        print(f"[registry] appended={len(rows)}")

    def _ingest_similarity_tokens_from_generated_files(self) -> None:
        """Time-layered ingestion: keep 100% recent, sampled older, cap total ~150k.

        v50.1: 48万条历史数据 → 分层采样 + 总量控制。
        直接针对大规模下自相关过滤过严的问题。
        """
        if not self.cfg.include_generated_registry_in_similarity:
            return
        import random
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td

        now = _dt.now(_tz.utc)
        cutoff_7d = now - _td(days=7)
        cutoff_30d = now - _td(days=30)

        seen: set[str] = set()
        recent: list[str] = []
        mid: list[str] = []
        old: list[str] = []

        base_dir = Path(__file__).resolve().parent
        for path in sorted(glob.glob(str(base_dir / "alpha_generated_expressions*.csv"))):
            try:
                with open(path, "r", newline="", encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        e = _sig(row.get("expression") or "")
                        if not e or e in seen:
                            continue
                        seen.add(e)
                        ts_str = row.get("utc_iso", "")
                        try:
                            ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            ts = now
                        if ts >= cutoff_7d:
                            recent.append(e)
                        elif ts >= cutoff_30d:
                            mid.append(e)
                        else:
                            old.append(e)
            except Exception as ex:
                print(f"[history] generated expressions read failed {Path(path).name}: {ex}")

        # v50.1: 分层采样 — 近期全保留，中期保留20%，远期保留5%
        random.seed(42)
        mid_keep = random.sample(mid, min(len(mid), max(1, int(len(mid) * 0.20)))) if mid else []
        old_keep = random.sample(old, min(len(old), max(1, int(len(old) * 0.05)))) if old else []

        acc = recent + mid_keep + old_keep
        total_cap = int(self.cfg.similarity_history_max_token_rows)
        if len(acc) > total_cap:
            acc = random.sample(acc, total_cap)

        added = 0
        for e in acc:
            self._novelty_index.add(e)
            self._history_pools.append_tokens(e, "generated")
            if _quality_diverse_enabled(self.cfg):
                self._history_pools.append_behavior_tokens(e, "generated")
            added += 1

        kept_pct = 100.0 * len(acc) / max(len(seen), 1)
        print(
            f"[history] similarity_pool +generated_csv token_rows={added} "
            f"(kept {kept_pct:.0f}% of {len(seen)} unique — "
            f"recent={len(recent)} mid={len(mid_keep)}/{len(mid)} old={len(old_keep)}/{len(old)})"
        )
        self._write_novelty_index()

    def _write_novelty_index(self) -> None:
        if not self.cfg.novelty_enabled:
            return
        path = self._path(self.cfg.novelty_index_filename)
        try:
            payload = self._novelty_index.to_jsonable()
            payload["strictness"] = self.cfg.novelty_strictness
            payload["updated_utc"] = _utc()
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[novelty] index -> {path.name} structures={payload['structure_signature_count']}")
        except Exception as e:
            print(f"[novelty] index write failed: {e}")

    def _compress_history_pools(self) -> None:
        caps = {
            "toxic": int(self.cfg.similarity_toxic_max_rows),
            "weak_fail": min(int(self.cfg.similarity_weak_max_rows), int(self.cfg.similarity_history_max_token_rows)),
            "near_pass": int(self.cfg.similarity_near_pass_max_rows),
            "passed": int(self.cfg.similarity_near_pass_max_rows),
            "generated": int(self.cfg.similarity_history_max_token_rows),
            "self_corr_risk": int(self.cfg.similarity_near_pass_max_rows),
        }
        rng = random.Random(int(self._run_salt) & 0xFFFFFFFF)
        for name, cap in caps.items():
            pool: list[set[str]] = getattr(self._history_pools, name)
            if cap > 0 and len(pool) > cap:
                setattr(self._history_pools, name, rng.sample(pool, cap))
        self._history_submission_tokens = list(self._history_pools.weak_fail)
        print(
            f"[history] similarity_pool reservoir "
            f"toxic={len(self._history_pools.toxic)} weak={len(self._history_pools.weak_fail)} "
            f"near={len(self._history_pools.near_pass)} passed={len(self._history_pools.passed)} "
            f"generated={len(self._history_pools.generated)} self_corr_risk={len(self._history_pools.self_corr_risk)}"
        )

    # ---- auth / http ----

    def _make_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self.cfg.tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            pass
        return ctx

    def _apply_ipv4_preference(self) -> None:
        if self.cfg.force_ipv4:
            try:
                import urllib3.util.connection as _u3
                _u3.allowed_gai_family = lambda: socket.AF_INET  # type: ignore
            except Exception:
                pass

    def _init_http_session(self) -> None:
        self.sess.trust_env = True
        # Do NOT set sess.auth globally — BasicAuth on every request causes WQ Brain
        # to reject valid browser-cookie sessions with 401 on data endpoints.
        # BasicAuth is passed only to _login_once (POST /authentication).
        self.sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, */*",
            "Content-Type": "application/json",
            "Origin": "https://platform.worldquantbrain.com",
        })
        self.sess.mount("https://", _TLSAdapter(self._ssl_ctx))
        proxy = (str(self.cfg.https_proxy).strip() if self.cfg.https_proxy else "") or os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", "")
        if proxy:
            self.sess.proxies["https"] = proxy
            print(f"[network] HTTPS proxy={proxy}")

    def _rebuild_http_session(self, *, reason: str = "") -> None:
        if not self.cfg.ssl_error_rebuild_session:
            return
        with self._sess_lock:
            try:
                self.sess.close()
            except Exception:
                pass
            self._ssl_ctx = self._make_ssl_context()
            self.sess = requests.Session()
            self._init_http_session()
        tag = f" ({reason})" if reason else ""
        print(f"[network] rebuilt HTTPS session{tag}")

    def _print_ssl_help(self) -> None:
        print(
            "[network] 无法与 api.worldquantbrain.com 完成 TLS 握手（常见于 VPN/代理/防火墙/线路抖动）。"
            "可尝试：换网络或开关 VPN；设置 HTTPS_PROXY 或 --https-proxy；"
            "公司网证书劫持时用 --tls-no-verify（仅排障）；不稳定时可试 --no-ipv4。"
        )

    def _maybe_inject_browser_cookie(self, *, force: bool = False) -> None:
        """Seed DPAPI auth state from .wq_browser_cookie.json when no valid session exists."""
        cookie_file = Path(".wq_browser_cookie.json")
        if not cookie_file.exists():
            return
        from alpha_mining.auth.session_manager import (
            _account_fingerprint, _load_state, _new_state, _protect_cookie_rows,
            _requests_cookie_rows, _save_state, _utc_now, _within_cooldown,
        )
        state_path = Path(self.cfg.auth_state_file).expanduser().resolve()
        fingerprint = _account_fingerprint(self.cfg.username)
        now = _utc_now()
        try:
            state = _load_state(state_path, fingerprint, now)
        except Exception:
            state = _new_state(fingerprint, now)
        if not force and bool(state.get("cookie_blob_dpapi_b64")) and _within_cooldown(
            state, now, float(self.cfg.auth_cooldown_seconds)
        ):
            return  # still within cooldown — nothing to do
        try:
            data = json.loads(cookie_file.read_text(encoding="utf-8"))
            cookie_str = str(data.get("cookie") or "").strip()
            if not cookie_str:
                return
        except Exception:
            return
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            name, _, value = pair.partition("=")
            name, value = name.strip(), value.strip()
            if name:
                self.sess.cookies.set(name, value, domain="worldquantbrain.com")
                self.sess.cookies.set(name, value, domain=".worldquantbrain.com")
        cookie_rows = _requests_cookie_rows(self.sess)
        if not cookie_rows:
            return
        try:
            state["last_auth_utc"] = now.isoformat().replace("+00:00", "Z")
            state["generation"] = int(state.get("generation", 0)) + 1
            state["cookie_blob_dpapi_b64"] = _protect_cookie_rows(cookie_rows)
            _save_state(state_path, state)
            print("[auth] seeded DPAPI state from .wq_browser_cookie.json")
        except Exception as exc:
            print(f"[auth] browser-cookie injection failed: {exc}")

    def ensure_authenticated(self, *, force: bool = False) -> None:
        # Restore original auth: set BasicAuth directly on session so every
        # request carries credentials. WQ Brain accepts BasicAuth on all endpoints.
        # Browser cookie (if present) is injected on top — WQ Brain will prefer it
        # when valid, and fall back to BasicAuth when it expires.
        self.sess.auth = HTTPBasicAuth(self.cfg.username, self.cfg.password)
        self._maybe_inject_browser_cookie()
        print(f"[auth] OK user={_mask(self.cfg.username)}")

    def _login_with_credentials(self) -> bool:
        """Perform a real username/password login against WQ Brain to mint a fresh
        session cookie. This is the authoritative recovery path when a cookie expires;
        it does NOT depend on the local .wq_browser_cookie.json file, so the loop stays
        fully autonomous. Returns True on HTTP 2xx."""
        username = str(self.cfg.username or "").strip()
        password = str(self.cfg.password or "")
        if not username or not password:
            print("[auth] cannot re-login: WQ_USERNAME/WQ_PASSWORD not configured")
            return False
        # Ensure BasicAuth is on the session (it is the credential source WQ Brain
        # reads on POST /authentication) and clear any stale cookie that would make
        # the platform skip the credential check.
        self.sess.auth = HTTPBasicAuth(username, password)
        try:
            # POST /authentication is a BasicAuth login with no body; temporarily
            # clear Content-Type and other headers that might confuse the auth endpoint.
            # Use minimal headers to match a clean POST.
            resp = self._sess_request(
                "POST",
                f"{self._BASE}/authentication",
                timeout=self._timeout(),
                headers={
                    "Content-Type": None,
                    "Accept": "*/*",
                    "Origin": None,
                },
            )
        except requests.RequestException as exc:
            print(f"[auth] credential re-login request failed: {_short_err(exc)}")
            return False
        if 200 <= resp.status_code < 300:
            # Persist the freshly minted cookie jar to DPAPI state for child processes.
            try:
                from alpha_mining.auth.session_manager import (
                    _account_fingerprint, _load_state, _new_state, _protect_cookie_rows,
                    _requests_cookie_rows, _save_state, _utc_now,
                )
                state_path = Path(self.cfg.auth_state_file).expanduser().resolve()
                fingerprint = _account_fingerprint(username)
                now = _utc_now()
                try:
                    state = _load_state(state_path, fingerprint, now)
                except Exception:
                    state = _new_state(fingerprint, now)
                cookie_rows = _requests_cookie_rows(self.sess)
                if cookie_rows:
                    state["last_auth_utc"] = now.isoformat().replace("+00:00", "Z")
                    state["generation"] = int(state.get("generation", 0)) + 1
                    state["cookie_blob_dpapi_b64"] = _protect_cookie_rows(cookie_rows)
                    _save_state(state_path, state)
            except Exception as exc:
                print(f"[auth] re-login succeeded but state persist failed: {exc}")
            print(f"[auth] credential re-login OK user={_mask(username)}")
            return True
        print(f"[auth] credential re-login returned HTTP {resp.status_code}")
        return False

    def authenticate(self) -> None:
        """Backward-compatible protected authentication entrypoint."""
        self.ensure_authenticated(force=False)

    def _timeout(self) -> tuple[float, float]:
        return (float(self.cfg.connect_timeout), float(self.cfg.timeout))

    def _sess_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        with self._sess_lock:
            return self.sess.request(method, url, **kwargs)

    def _retry(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        timeout: tuple | None = None,
        max_attempts: int | None = None,
    ) -> requests.Response:
        to = timeout or self._timeout()
        last_attempt = int(max_attempts if max_attempts is not None else self.cfg.max_retries)
        reauthenticated = False
        for attempt in range(last_attempt + 1):
            try:
                resp = self._sess_request(method, url, params=params, json=json_body, timeout=to)
                self._consecutive_dns_errors = 0
                if resp.status_code == 401:
                    if reauthenticated or attempt >= last_attempt:
                        raise PermissionError("bounded authentication refresh exhausted after HTTP 401")
                    reauthenticated = True
                    tail = url.rsplit('/', 1)[-1][:52]
                    print(f"[retry] HTTP 401 {method} …/{tail}; re-login with credentials")
                    # Primary recovery: real username/password login (autonomous, no
                    # dependency on a possibly-stale browser cookie file). BasicAuth
                    # stays on the session as an always-present fallback.
                    if not self._login_with_credentials():
                        # Secondary: seed from a browser cookie file if one exists.
                        self._maybe_inject_browser_cookie(force=True)
                    continue
                if resp.status_code in (429, 500, 502, 503, 504):
                    if attempt == last_attempt:
                        resp.raise_for_status()
                    from alpha_mining.platform.client import retry_after_seconds
                    parsed_retry_after = retry_after_seconds(resp.headers.get("Retry-After"))
                    wait = parsed_retry_after if parsed_retry_after > 0 else float(min(2 ** attempt, 30))
                    if resp.status_code == 429:
                        # Adaptive submit sleep + hard cooldown — was unused by _retry before.
                        self._on_status(429)
                        wait = max(
                            wait,
                            float(getattr(self.cfg, "submit_429_min_sleep", 12.0) or 12.0),
                            float(self._dynamic_submit_sleep or 0.0),
                        )
                    self._retry_transient_hits += 1
                    now = time.time()
                    tail = url.rsplit("/", 1)[-1]
                    if len(tail) > 52:
                        tail = tail[:49] + "..."
                    window = 14.0
                    should_log = (now - self._retry_transient_log_ts >= window) or (attempt == last_attempt)
                    if should_log:
                        n = self._retry_transient_hits
                        sfx = " (last attempt)" if attempt == last_attempt else ""
                        print(f"[retry] HTTP {resp.status_code} x{n} {method} …/{tail} sleep={wait:.1f}s{sfx}")
                        self._retry_transient_hits = 0
                        self._retry_transient_log_ts = now
                    time.sleep(wait)
                    continue
                if self._retry_transient_hits:
                    self._retry_transient_hits = 0
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                ssl_err = _is_ssl_error(e)
                transient = _is_transient_connect_error(e)
                if transient and self.cfg.ssl_error_rebuild_session:
                    if ssl_err or any(
                        x in str(e).lower()
                        for x in ("connection reset", "broken pipe", "connection aborted")
                    ):
                        self._rebuild_http_session(reason="ssl" if ssl_err else "connect")
                if _is_dns_error(e):
                    self._consecutive_dns_errors += 1
                    if self._consecutive_dns_errors >= self.cfg.dns_error_pause_count:
                        print(f"[network] DNS errors={self._consecutive_dns_errors}; pause {self.cfg.dns_error_pause_seconds:.0f}s")
                        time.sleep(self.cfg.dns_error_pause_seconds)
                        self._consecutive_dns_errors = 0
                if attempt == last_attempt:
                    if ssl_err:
                        self._print_ssl_help()
                    raise
                if ssl_err:
                    wait = min(90.0, float(self.cfg.ssl_error_pause_seconds) * (1.5 ** attempt))
                    print(
                        f"[network] SSL retry {attempt + 1}/{last_attempt + 1} "
                        f"sleep={wait:.0f}s: {_short_err(e)}"
                    )
                else:
                    wait = min(2 ** attempt, 30)
                time.sleep(wait)
        raise RuntimeError("unreachable")

    def _rate_limit(self) -> None:
        wait = max(0.0, self._dynamic_submit_sleep - (time.time() - self._last_submit_ts))
        if wait > 0:
            time.sleep(wait)

    def _on_status(self, code: int) -> bool:
        if code == 429:
            self._consecutive_429 += 1
            self._dynamic_submit_sleep = min(self.cfg.adaptive_max_sleep, self._dynamic_submit_sleep * self.cfg.adaptive_backoff_factor)
            if self._consecutive_429 >= self.cfg.hard_cooldown_429_count:
                print(f"[cooldown] consecutive 429={self._consecutive_429}; sleep={self.cfg.hard_cooldown_seconds:.0f}s")
                time.sleep(self.cfg.hard_cooldown_seconds)
                self._consecutive_429 = 0
                return True
        else:
            self._consecutive_429 = 0
            self._dynamic_submit_sleep = max(max(0.01, self.cfg.submit_sleep), self._dynamic_submit_sleep * self.cfg.adaptive_recover_factor)
        return False

    # ---- data ----

    def _datasets_disk_cache_path(self) -> Path:
        return self._path(_DATASETS_DISK_CACHE)

    def _datafields_disk_cache_path(self) -> Path:
        return self._path(_DATAFIELDS_DISK_CACHE)

    def _load_datasets_disk_cache(self, *, ttl: float | None = None) -> list[str] | None:
        if not bool(getattr(self.cfg, "enable_fields_disk_cache", True)):
            return None
        if ttl is None:
            ttl = float(getattr(self.cfg, "fields_disk_cache_ttl_seconds", _DISK_CACHE_TTL_SECONDS) or 0.0)
        path = self._datasets_disk_cache_path()
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_at = float(payload.get("cached_at") or 0.0)
            if ttl > 0 and (time.time() - cached_at) > ttl:
                return None
            ids = [str(x) for x in (payload.get("dataset_ids") or []) if str(x).strip()]
            return ids or None
        except Exception as e:
            print(f"[datasets] disk_cache read failed: {e}")
            return None

    def _save_datasets_disk_cache(self, dataset_ids: list[str]) -> None:
        if not bool(getattr(self.cfg, "enable_fields_disk_cache", True)):
            return
        path = self._datasets_disk_cache_path()
        try:
            path.write_text(
                json.dumps(
                    {"cached_at": time.time(), "dataset_ids": list(dataset_ids)},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[datasets] disk_cache write failed: {e}")

    def _load_datafields_disk_cache(self, *, ttl: float | None = None) -> Any | None:
        if not bool(getattr(self.cfg, "enable_fields_disk_cache", True)):
            return None
        if ttl is None:
            ttl = float(getattr(self.cfg, "fields_disk_cache_ttl_seconds", _DISK_CACHE_TTL_SECONDS) or 0.0)
        path = self._datafields_disk_cache_path()
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_at = float(payload.get("cached_at") or 0.0)
            if ttl > 0 and (time.time() - cached_at) > ttl:
                return None
            rows = payload.get("rows") or []
            if not rows:
                return None
            df = pd.DataFrame(rows)
            if df.empty or "id" not in df.columns:
                return None
            return df
        except Exception as e:
            print(f"[fields] disk_cache read failed: {e}")
            return None

    def _save_datafields_disk_cache(self, df: Any) -> None:
        if not bool(getattr(self.cfg, "enable_fields_disk_cache", True)):
            return
        path = self._datafields_disk_cache_path()
        try:
            rows = df.to_dict(orient="records")
            path.write_text(
                json.dumps({"cached_at": time.time(), "rows": rows}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[fields] disk_cache write failed: {e}")

    @staticmethod
    def _ensure_core_datasets(dataset_ids: list[str], *, max_n: int) -> list[str]:
        """Keep analyst discovery but always reserve slots for fundamental/pv cores."""
        core = ["fundamental6", "fundamental65", "pv1"]
        out: list[str] = []
        for did in core + list(dataset_ids) + list(_FALLBACK_DATASET_IDS):
            if did and did not in out:
                out.append(did)
            if len(out) >= max_n:
                break
        return out[:max_n] or list(_FALLBACK_DATASET_IDS)[:max_n]

    def _dataset_ids(self) -> list[str]:
        if self.cfg.dataset_ids:
            return list(self.cfg.dataset_ids)
        _cache_ttl = float(getattr(self.cfg, "fields_disk_cache_ttl_seconds", _DISK_CACHE_TTL_SECONDS) or _DISK_CACHE_TTL_SECONDS)
        if self._cached_dataset_ids and (time.time() - self._dataset_ids_cached_at) < _cache_ttl:
            return list(self._cached_dataset_ids)
        disk_hit = self._load_datasets_disk_cache(ttl=_cache_ttl)
        if disk_hit:
            result = self._ensure_core_datasets(disk_hit, max_n=int(self.cfg.dataset_auto_max))
            self._cached_dataset_ids = result
            self._dataset_ids_cached_at = time.time()
            print(f"[datasets] disk_cache hit n={len(result)}")
            return list(result)

        url = f"{self._BASE}/data-sets"
        out: list[str] = []
        try:
            for offset in range(0, max(50, self.cfg.dataset_auto_max * 2), 50):
                resp = self._retry("GET", url, params={
                    "instrumentType": self.cfg.instrument_type,
                    "region": self.cfg.region,
                    "delay": self.cfg.delay,
                    "universe": self.cfg.universe,
                    "limit": 50,
                    "offset": offset,
                })
                chunk = resp.json().get("results", [])
                for it in chunk:
                    if not isinstance(it, dict) or not it.get("id"):
                        continue
                    did = str(it["id"])
                    low = did.lower()
                    if not self.cfg.allow_sentiment_datasets and any(x in low for x in ("sentiment", "news", "social")):
                        continue
                    if not self.cfg.allow_option_datasets and any(x in low for x in ("option", "short")):
                        continue
                    if did not in out:
                        out.append(did)
                if len(chunk) < 50 or len(out) >= self.cfg.dataset_auto_max:
                    break
                time.sleep(self.cfg.page_sleep)
        except requests.RequestException as e:
            stale = self._load_datasets_disk_cache(ttl=0)  # ignore TTL on failure
            if stale:
                result = self._ensure_core_datasets(stale, max_n=int(self.cfg.dataset_auto_max))
                self._cached_dataset_ids = result
                self._dataset_ids_cached_at = time.time()
                print(f"[datasets] WARN fetch failed ({_short_err(e)}); using stale disk_cache n={len(result)}")
                return list(result)
            result = self._ensure_core_datasets(list(_FALLBACK_DATASET_IDS), max_n=int(self.cfg.dataset_auto_max))
            self._cached_dataset_ids = result
            self._dataset_ids_cached_at = time.time()
            print(f"[datasets] WARN fetch failed ({_short_err(e)}); using fallback n={len(result)}")
            return list(result)

        def priority(ds: str) -> int:
            s = ds.lower()
            if "fundamental" in s:
                return 0
            if "analyst" in s:
                return 1
            if "model" in s:
                return 2
            if s.startswith("pv"):
                return 3
            return 5

        ranked = sorted(out, key=priority)[: self.cfg.dataset_auto_max] or list(_FALLBACK_DATASET_IDS)
        result = self._ensure_core_datasets(ranked, max_n=int(self.cfg.dataset_auto_max))
        self._cached_dataset_ids = result
        self._dataset_ids_cached_at = time.time()
        self._save_datasets_disk_cache(result)
        return result

    def fetch_datafields(self) -> Any:
        disk_hit = self._load_datafields_disk_cache()
        if disk_hit is not None:
            print(f"[fields] disk_cache hit rows={len(disk_hit)}")
            return disk_hit

        base = f"{self._BASE}/data-fields"
        all_rows: list[dict] = []
        for did in self._dataset_ids():
            params: dict[str, Any] = {
                "instrumentType": self.cfg.instrument_type,
                "region": self.cfg.region,
                "delay": self.cfg.delay,
                "universe": self.cfg.universe,
                "dataset.id": did,
                "limit": 50,
                "offset": 0,
            }
            try:
                first = self._retry("GET", base, params=params).json()
                total = int(first.get("count", 0))
                rows = list(first.get("results", []))
                for offset in range(50, total, 50):
                    params["offset"] = offset
                    rows.extend(self._retry("GET", base, params=params).json().get("results", []))
                    time.sleep(self.cfg.page_sleep)
            except requests.RequestException as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code == 429:
                    sleep_s = max(float(getattr(self.cfg, "submit_429_min_sleep", 25.0)), self._dynamic_submit_sleep)
                    print(f"[fields] WARN {did}: HTTP 429; sleep={sleep_s:.0f}s then skip dataset")
                    time.sleep(sleep_s)
                    continue
                if _is_ssl_error(e) or _is_transient_connect_error(e):
                    print(f"[fields] WARN {did}: transient network error; skip dataset: {_short_err(e)}")
                    continue
                raise
            print(f"[fields] {did}: {len(rows)}")
            for r in rows:
                row = dict(r) if isinstance(r, dict) else {"id": str(r)}
                row["_ds"] = did
                all_rows.append(row)
        df = pd.DataFrame(all_rows)
        if df.empty:
            stale = self._load_datafields_disk_cache(ttl=0)
            if stale is not None:
                print(f"[fields] WARN empty fetch; using stale disk_cache rows={len(stale)}")
                return stale
            raise RuntimeError("No datafields fetched; check credentials/network/dataset.")
        if "type" in df.columns:
            df = df[df["type"] == "MATRIX"].copy()
        if "coverage" in df.columns:
            df["coverage"] = pd.to_numeric(df["coverage"], errors="coerce")
            df = df[df["coverage"].fillna(0) >= self.cfg.min_coverage]
        if "dateCoverage" in df.columns:
            df["dateCoverage"] = pd.to_numeric(df["dateCoverage"], errors="coerce")
            df = df[df["dateCoverage"].fillna(0) >= self.cfg.min_date_coverage]
        if "id" in df.columns:
            df = df[~df["id"].astype(str).map(is_bad_field_name)].copy()
            df = df.drop_duplicates(subset=["id"]).reset_index(drop=True)
        print(f"[fields] total after filters: {len(df)}")
        if not df.empty:
            self._save_datafields_disk_cache(df)
        return df

    def top_fields(self, df: Any) -> Any:
        ranked = df.copy()
        for col in ("coverage", "dateCoverage", "userCount"):
            if col not in ranked.columns:
                ranked[col] = 0.0
        user = pd.to_numeric(ranked["userCount"], errors="coerce").fillna(0)
        popularity = user / (user.max() + 1)
        popularity_weight = -0.10 if bool(getattr(self.cfg, "prefer_underused_fields", False)) else 0.15
        ranked["_score"] = (
            pd.to_numeric(ranked["coverage"], errors="coerce").fillna(0) * 0.35
            + pd.to_numeric(ranked["dateCoverage"], errors="coerce").fillna(0) * 0.35
            + popularity * popularity_weight
            + ranked["id"].astype(str).map(field_quality_score) * 0.15
        )
        ranked = ranked.sort_values("_score", ascending=False)
        underused: set[int] = set()
        if bool(getattr(self.cfg, "prefer_underused_fields", False)) and len(ranked):
            cutoff = user.quantile(0.5)
            underused = set(ranked.index[user.loc[ranked.index] <= cutoff])
        buckets: dict[str, list[int]] = {}
        for ds, sub in ranked.groupby("_ds", sort=False):
            buckets[str(ds)] = list(sub.index)
        selected: list[int] = []
        seen: set[str] = set()
        while len(selected) < self.cfg.field_top_n:
            added = False
            for key in list(buckets.keys()):
                lst = buckets[key]
                while lst:
                    idx = lst.pop(0)
                    fid = str(ranked.loc[idx, "id"])
                    if fid not in seen:
                        selected.append(idx)
                        seen.add(fid)
                        added = True
                        break
                if len(selected) >= self.cfg.field_top_n:
                    break
            if not added:
                break
        if underused:
            reserve = min(len(selected), int(round(self.cfg.field_top_n * float(self.cfg.underused_field_share))))
            present = sum(1 for idx in selected if idx in underused)
            if present < reserve:
                replacements = [idx for idx in ranked.index if idx in underused and idx not in selected]
                for idx in replacements[: reserve - present]:
                    for pos in range(len(selected) - 1, -1, -1):
                        if selected[pos] not in underused:
                            selected[pos] = idx
                            break
        return ranked.loc[selected].copy().reset_index(drop=True)

    # ---- history / dedup ----

    def load_history_learning(self) -> None:
        self._load_generated_registry()
        # Do not hard-block all historically generated expressions by default.
        # Otherwise the search space collapses after multiple runs (candidates -> 1 or 0).
        self._history_seen_exact = set(self._generated_registry_exact) if self.cfg.block_generated_registry_exact else set()
        self._history_seen_skeleton = {_skel(x) for x in self._history_seen_exact}
        self._success_blacklist_tokens = []
        self._history_submission_tokens = []
        self._history_pools = HistorySimilarityPools()
        tier_counts: Counter[str] = Counter()
        self._positive_field_counter = Counter()
        self._tried_expressions = set()
        self._tried_payload_keys = set()
        self._failed_expressions = set()
        self._passed_expressions = set()
        self._tried_metrics = {}
        self._failed_cluster = {}
        self._near_pass_expression_set = set()
        self._family_pass_rates: dict[str, float] = {}
        self._family_quality_stats = {}
        self._family_low_yield_buckets: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
        self._novelty_index = NoveltyIndex()
        self._feedback_blocked_operators = set()
        self._feedback_blocked_variables = set()
        near_pass_buf: dict[str, dict[str, Any]] = {}
        family_attempts: Counter[str] = Counter()
        self._family_simulation_counts = Counter()
        family_pass_hits: Counter[str] = Counter()
        family_bad_metric_hits: Counter[str] = Counter()
        family_hard_fail_hits: Counter[str] = Counter()
        family_low_sharpe_hits: Counter[str] = Counter()
        family_low_fitness_hits: Counter[str] = Counter()
        family_low_sub_universe_hits: Counter[str] = Counter()
        family_concentrated_hits: Counter[str] = Counter()
        base_dir = Path(__file__).resolve().parent
        files = [self._feedback_path()]
        files.extend(_feedback_csv_candidates(base_dir))
        for path in list(dict.fromkeys(files)):
            if not path.is_file():
                continue
            try:
                with path.open("r", newline="", encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        expr = _sig(row.get("expression") or "")
                        if not expr:
                            continue
                        self._novelty_index.add(expr)
                        self._history_seen_exact.add(expr)
                        self._history_seen_skeleton.add(_skel(expr))
                        feedback_text = _feedback_text_from_row(row)
                        self_status = str(row.get("self_correlation_status") or "").upper().strip()
                        risk_text = " | ".join(
                            str(row.get(k) or "")
                            for k in (
                                "blocked_reason",
                                "check_note",
                                "platform_gate_reason",
                                "Failure Reasons",
                                "failure_reasons",
                            )
                        ).lower()
                        if (
                            self_status in ("FAIL", "FAILED", "REJECTED")
                            or "self_correlation:fail" in risk_text
                            or "self_correlation_pending" in risk_text
                        ):
                            self._history_pools.append_behavior_tokens(expr, "self_corr_risk")
                        op = _feedback_operator(feedback_text)
                        var = _feedback_variable(feedback_text)
                        if op:
                            self._feedback_blocked_operators.add(op)
                            BLOCKED_FUNCTIONS.add(op)
                        if var:
                            self._feedback_blocked_variables.add(var)
                        check_passed = str(row.get("check_passed") or "").lower() in ("true", "1", "yes")
                        status = str(row.get("status") or "").lower()
                        sharpe_row = _to_float(row.get("Sharpe"))
                        fitness_row = _to_float(row.get("Fitness"))
                        tier = _history_quality_tier(
                            sharpe=sharpe_row,
                            fitness=fitness_row,
                            check_passed=check_passed,
                            feedback_text=feedback_text,
                            near_pass_min_sharpe=float(self.cfg.near_pass_min_sharpe),
                            near_pass_seed_min_composite=float(self.cfg.near_pass_seed_min_composite),
                        )
                        if status == "ok" or expr:
                            self._history_pools.append_tokens(expr, tier)
                            tier_counts[tier] += 1
                        if status == "ok":
                            self._tried_expressions.add(expr)
                            family_name = str(row.get("family") or "unknown").lower().strip()
                            self._family_simulation_counts[family_name] += 1
                            sharpe = _to_float(row.get("Sharpe"))
                            fitness = _to_float(row.get("Fitness"))
                            turnover = _to_float(row.get("Turnover"))
                            fbucket = _family_stats_bucket(str(row.get("family") or ""))
                            family_key = str(row.get("family") or "").lower().strip()
                            stat_keys = [fbucket]
                            if family_key and family_key != fbucket:
                                stat_keys.append(family_key)
                            for key in stat_keys:
                                family_attempts[key] += 1
                            bad_metric = (
                                (sharpe is not None and float(sharpe) < 0.02)
                                or (fitness is not None and float(fitness) < 0.02)
                            )
                            hard_checks = set(_hard_fail_checks(_json_load_maybe(row.get("platform_check_json"))))
                            hard_checks.update(_hard_fail_checks(_json_load_maybe(row.get("platform_simulation_json"))))
                            for key in stat_keys:
                                if bad_metric:
                                    family_bad_metric_hits[key] += 1
                                if hard_checks:
                                    family_hard_fail_hits[key] += 1
                                if "LOW_SHARPE" in hard_checks:
                                    family_low_sharpe_hits[key] += 1
                                if "LOW_FITNESS" in hard_checks:
                                    family_low_fitness_hits[key] += 1
                                if "LOW_SUB_UNIVERSE_SHARPE" in hard_checks:
                                    family_low_sub_universe_hits[key] += 1
                                if "CONCENTRATED_WEIGHT" in hard_checks:
                                    family_concentrated_hits[key] += 1
                            if _platform_pass_proxy(
                                sharpe,
                                fitness,
                                check_passed=check_passed,
                                min_sharpe=float(self.cfg.min_sharpe_threshold) - 0.01,
                                min_fitness=float(self.cfg.min_fitness_threshold),
                            ):
                                for key in stat_keys:
                                    family_pass_hits[key] += 1
                            self._tried_metrics[expr] = {
                                "sharpe": sharpe,
                                "fitness": fitness,
                                "turnover": turnover,
                            }
                            row_settings = {
                                "universe": row.get("Universe"),
                                "neutralization": row.get("Neutralization"),
                                "decay": row.get("Decay"),
                                "truncation": row.get("Truncation"),
                                "delay": row.get("Delay"),
                                "nanHandling": row.get("NaN_Handling") or row.get("NaN Handling"),
                            }
                            self._tried_payload_keys.add(_payload_fingerprint(expr, row_settings))
                            if check_passed:
                                self._passed_expressions.add(expr)
                            else:
                                self._failed_expressions.add(expr)
                                if sharpe is not None:
                                    field = self._top_field_in_expression(expr)
                                    if field:
                                        key = (_skel(expr), field)
                                        self._failed_cluster.setdefault(key, []).append(float(sharpe))
                            # Track near-pass seeds: Sharpe close to or above the cutoff.
                            if (
                                sharpe is not None
                                and sharpe >= self.cfg.near_pass_min_sharpe
                                and not check_passed
                            ):
                                if _has_toxic_near_pass_price_leg(expr):
                                    continue
                                score = float(sharpe) + 1.05 * float(fitness or 0.0)
                                if score < float(self.cfg.near_pass_seed_min_composite):
                                    continue
                                rec = near_pass_buf.get(expr)
                                if not rec or rec.get("score", -1) < score:
                                    near_pass_buf[expr] = {
                                        "expression": expr,
                                        "sharpe": float(sharpe),
                                        "fitness": float(fitness or 0.0),
                                        "turnover": float(turnover or 0.0),
                                        "family": row.get("family") or "",
                                        "score": score,
                                    }
                        if check_passed:
                            toks = _expr_token_set(expr)
                            if toks:
                                self._success_blacklist_tokens.append(toks)
                            for ident in re.findall(r"\b[a-z_][a-z0-9_]*\b", expr.lower()):
                                self._positive_field_counter[ident] += 1
            except Exception as e:
                print(f"[history] read failed {path.name}: {e}")
        self._ingest_similarity_tokens_from_generated_files()
        self._history_submission_tokens = list(self._history_pools.weak_fail)
        self._compress_history_pools()
        hopeful_added = self._ingest_hopeful_jsonl_seeds(near_pass_buf)
        self._near_pass_records = sorted(
            near_pass_buf.values(), key=lambda r: r.get("score", 0.0), reverse=True
        )[: max(0, int(self.cfg.near_pass_max_seeds))]
        self._near_pass_expression_set = {_sig(str(r.get("expression") or "")) for r in self._near_pass_records}
        for bucket in set(family_attempts) | set(family_pass_hits):
            n = int(family_attempts[bucket])
            p = int(family_pass_hits[bucket])
            if n >= 8:
                rate = p / max(1, n)
            elif bucket in ("near_pass_variant", "pass_fundamental", "template"):
                rate = 0.08
            elif bucket in ("arch_level_liquid", "arch_delta_liquid"):
                rate = 0.005
            else:
                rate = 0.02
            self._family_pass_rates[bucket] = rate
            denom = max(1, n)
            self._family_quality_stats[bucket] = {
                "attempts": float(n),
                "pass_proxy_rate": p / denom,
                "bad_metric_rate": int(family_bad_metric_hits[bucket]) / denom,
                "hard_fail_rate": int(family_hard_fail_hits[bucket]) / denom,
                "low_sharpe_fail_rate": int(family_low_sharpe_hits[bucket]) / denom,
                "low_fitness_fail_rate": int(family_low_fitness_hits[bucket]) / denom,
                "low_sub_universe_fail_rate": int(family_low_sub_universe_hits[bucket]) / denom,
                "concentrated_weight_fail_rate": int(family_concentrated_hits[bucket]) / denom,
            }
        if self._family_pass_rates:
            top_rates = sorted(self._family_pass_rates.items(), key=lambda x: -x[1])[:6]
            print(
                f"[history] family_pass_proxy (Sharpe>={self.cfg.min_sharpe_threshold - 0.01:.2f} "
                f"Fitness>={self.cfg.min_fitness_threshold:.1f}) "
                + ", ".join(f"{k}:{v:.3f}" for k, v in top_rates)
            )
        if self._family_quality_stats:
            risky = sorted(
                self._family_quality_stats.items(),
                key=lambda x: (float(x[1].get("bad_metric_rate") or 0.0), float(x[1].get("hard_fail_rate") or 0.0)),
                reverse=True,
            )[:6]
            print(
                "[history] family_quality_risk "
                + ", ".join(
                    f"{k}:bad={v.get('bad_metric_rate', 0.0):.2f}/hard={v.get('hard_fail_rate', 0.0):.2f}"
                    for k, v in risky
                )
            )
        try:
            summary_path = self._path(self.cfg.feedback_learning_summary_csv)
            if summary_path.is_file():
                with summary_path.open("r", newline="", encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        family = str(row.get("family") or "").strip()
                        source = str(row.get("source") or "").strip()
                        if not family or family.lower().startswith("near_pass_variant"):
                            continue
                        count = int(float(row.get("count") or 0))
                        if count < int(getattr(self.cfg, "low_yield_bucket_min_count", 20) or 20):
                            continue
                        metric_rate = float(row.get("metric_gate_pass_rate") or 0.0)
                        blocked = str(row.get("top_blocked_reason") or "").strip()
                        if (
                            metric_rate < 0.10
                            or blocked in ("missing_core_metrics", "not_queued:missing_core_metrics", "sharpe_not_above_1.25")
                        ):
                            key = (
                                family,
                                source or "unknown",
                                str(row.get("window") or ""),
                                str(row.get("Neutralization") or "").strip().upper(),
                                str(row.get("Decay") or "").strip(),
                                str(row.get("Truncation") or "").strip(),
                            )
                            self._family_low_yield_buckets[key] = {
                                "count": count,
                                "metric_gate_pass_rate": metric_rate,
                                "top_blocked_reason": blocked,
                            }
        except Exception as e:
            print(f"[history] low-yield bucket summary load failed: {e}")
        if self._family_low_yield_buckets:
            print(f"[history] low_yield_bucket_caps loaded={len(self._family_low_yield_buckets)}")
        if hopeful_added:
            print(f"[history] hopeful_jsonl near_pass seeds +{hopeful_added}")
        # Important: v34 dedup is based on v34 registry only. We still learn from older ledgers,
        # but we do NOT block generation just because an expression existed in older runs/tools.
        print(
            f"[history] registry_seen={len(self._history_seen_exact)} "
            f"registry_skeletons={len(self._history_seen_skeleton)} "
            f"similarity_tiers toxic={len(self._history_pools.toxic)} weak={len(self._history_pools.weak_fail)} "
            f"near_pass={len(self._history_pools.near_pass)} passed={len(self._history_pools.passed)} "
            f"self_corr_risk={len(self._history_pools.self_corr_risk)} "
            f"(ingest {dict(tier_counts)}) "
            f"tried={len(self._tried_expressions)} payloads={len(self._tried_payload_keys)} failed={len(self._failed_expressions)} "
            f"passed={len(self._passed_expressions)} near_pass_seeds={len(self._near_pass_records)} "
            f"feedback_blocked_ops={len(self._feedback_blocked_operators)} feedback_unknown_vars={len(self._feedback_blocked_variables)}"
        )
        # Prompt 3: signal diversity audit — fast vs slow expression breakdown in history
        try:
            from alpha_mining.filter.ladder_check import is_fast_signal
            fast_n = sum(1 for e in self._history_seen_exact if is_fast_signal(e))
            total_n = len(self._history_seen_exact)
            slow_n = total_n - fast_n
            if total_n > 0:
                print(
                    f"[diversity] history fast_signal={fast_n}({fast_n/total_n:.0%}) "
                    f"slow_signal={slow_n}({slow_n/total_n:.0%}) "
                    f"total={total_n} "
                    f"fast_penalty={'ON('+str(getattr(self.cfg,'diversity_fast_signal_penalty',0.0))+')' if getattr(self.cfg,'diversity_fast_signal_penalty',0.0) > 0 else 'OFF'}"
                )
        except Exception:
            pass

    def _ingest_hopeful_jsonl_seeds(self, near_pass_buf: dict[str, dict[str, Any]]) -> int:
        """Load metric-pass alphas stuck on SELF_CORRELATION from hopeful JSONL as amplifier seeds."""
        added = 0
        paths = [
            self._path(self.cfg.hopeful_queue_filename),
            self._path("hopeful_alphas_v34.jsonl"),
        ]
        seen_paths: set[str] = set()
        for path in paths:
            rp = str(path.resolve())
            if rp in seen_paths or not path.is_file():
                continue
            seen_paths.add(rp)
            try:
                for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    expr = _sig(str(row.get("expression") or ""))
                    if not expr:
                        continue
                    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
                    sharpe = _to_float(metrics.get("sharpe"))
                    fitness = _to_float(metrics.get("fitness"))
                    turnover = _to_float(metrics.get("turnover"))
                    checks = row.get("checks") if isinstance(row.get("checks"), list) else []
                    low_sh_pass = any(
                        isinstance(c, dict)
                        and str(c.get("name") or "").upper() == "LOW_SHARPE"
                        and str(c.get("result") or "").upper() == "PASS"
                        for c in checks
                    )
                    if sharpe is None or float(sharpe) < 1.0:
                        continue
                    if checks and not low_sh_pass:
                        continue
                    score = float(sharpe) + 1.05 * float(fitness or 0.0)
                    rec = near_pass_buf.get(expr)
                    if rec and rec.get("score", -1) >= score:
                        continue
                    near_pass_buf[expr] = {
                        "expression": expr,
                        "sharpe": float(sharpe),
                        "fitness": float(fitness or 0.0),
                        "turnover": float(turnover or 0.0),
                        "family": str((row.get("meta") or {}).get("family") or "hopeful"),
                        "score": score,
                    }
                    added += 1
            except Exception as e:
                print(f"[history] hopeful read failed {path.name}: {e}")
        return added

    @staticmethod
    def _top_field_in_expression(expr: str) -> str | None:
        """Pick the first non-reserved identifier as the 'primary field' for clustering."""
        low = expr.lower()
        reserved = FUNCTIONS | GROUPS | BASE_VARS | {"d", "n", "a", "b", "c"}
        for ident in re.findall(r"\b[a-z_][a-z0-9_]*\b", low):
            if ident in reserved:
                continue
            if ident.isdigit():
                continue
            return ident
        return None

    def fetch_library_fingerprints(self) -> tuple[set[str], set[str], set[str]]:
        cap = self.cfg.library_expression_fetch_max
        if cap <= 0:
            return set(), set(), set()
        exact: set[str] = set()
        skeletons: set[str] = set()
        alpha_ids: set[str] = set()
        offset = 0
        while len(exact) < cap:
            try:
                r = self._sess_request(
                    "GET",
                    self._SELF_ALPHA_URL,
                    params={"limit": 100, "offset": offset, "order": "-dateCreated"},
                    timeout=self._timeout(),
                )
            except Exception:
                break
            if r.status_code != 200:
                break
            data = r.json()
            rows = data.get("results") or []
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                aid = str(row.get("id") or row.get("alpha") or "").strip()
                if aid:
                    alpha_ids.add(aid)
                expr = ""
                regular = row.get("regular")
                if isinstance(regular, dict):
                    expr = _sig(regular.get("code") or regular.get("regular") or "")
                elif isinstance(regular, str):
                    expr = _sig(regular)
                if expr:
                    exact.add(expr)
                    skeletons.add(_skel(expr))
            if len(rows) < 100:
                break
            offset += 100
            time.sleep(self.cfg.page_sleep)
        return exact, skeletons, alpha_ids

    def _merge_platform_tried(self, expressions: set[str], alpha_ids: set[str]) -> None:
        """Treat platform library as already simulated for prescreen + generation dedup.

        v50.1: 同时构建平台表达式 token 缓存，用于 pre-submit 快速去重。
        """
        if not expressions and not alpha_ids:
            return
        before = len(self._tried_expressions)
        self._tried_expressions.update(expressions)
        self._history_seen_exact.update(expressions)
        for expr in expressions:
            self._history_seen_skeleton.add(_skel(expr))
            # v50.1: 将平台表达式 token 加入 passed 池用于预去重
            toks = _expr_token_set(expr)
            if toks:
                self._platform_expression_tokens.append(toks)
        self._platform_alpha_ids.update(alpha_ids)
        added = len(self._tried_expressions) - before
        print(
            f"[history] platform_sync expressions={len(expressions)} alpha_ids={len(alpha_ids)} "
            f"tried+={added} tried_total={len(self._tried_expressions)} "
            f"platform_token_pool={len(self._platform_expression_tokens)}"
        )

    def is_platform_new_simulation(self, alpha_id: str | None, expression: str) -> bool:
        """True when this simulate result is a new platform alpha (not a re-run of a known id/expr)."""
        aid = str(alpha_id or "").strip()
        expr = _sig(expression or "")
        if aid and aid in self._simulate_snapshot_alpha_ids:
            return False
        if expr and expr in self._simulate_snapshot_exprs:
            return False
        return bool(aid or expr)

    def register_simulate_snapshot(self, alpha_id: str | None, expression: str) -> None:
        expr = _sig(expression or "")
        if expr:
            self._simulate_snapshot_exprs.add(expr)
            self._tried_expressions.add(expr)
        aid = str(alpha_id or "").strip()
        if aid:
            self._simulate_snapshot_alpha_ids.add(aid)
            self._platform_alpha_ids.add(aid)

    # ---- simulation / checking ----

    def submit_simulation(self, payload: dict) -> tuple[str | None, dict | None, str]:
        from alpha_mining.simulate.async_batch import claim_simulation_payloads

        idempotency_database = str(getattr(self.cfg, "sqlite_runs_path", "") or "").strip()
        if not idempotency_database:
            return None, None, "idempotency_store_required"
        if not claim_simulation_payloads(idempotency_database, [payload]):
            return None, None, "duplicate_simulation_blocked"
        sim_payload = {"type": payload["type"], "settings": payload["settings"], "regular": payload["regular"]}
        progress_url: str | None = None
        last_err = ""
        reauthed = False
        for attempt in range(1, 1 + self.cfg.max_retries):
            try:
                self._rate_limit()
                resp = self._sess_request("POST", self._SIM_URL, json=sim_payload, timeout=(15, self.cfg.submit_timeout))
                self._last_submit_ts = time.time()
                if resp.status_code == 401:
                    if not reauthed:
                        reauthed = True
                        self.ensure_authenticated(force=True)
                        continue
                    return None, None, "submit_auth_failed:401"
                if resp.status_code == 403:
                    return None, None, f"submit_forbidden:{(resp.text or '')[:400]}"
                if resp.status_code == 400:
                    return None, self._safe_json(resp), f"submit_bad_request:{(resp.text or '')[:500]}"
                if resp.status_code in (429, 500, 502, 503, 504):
                    did_cooldown = self._on_status(resp.status_code)
                    wait = float(resp.headers.get("Retry-After") or min(2 ** (attempt - 1), 30))
                    if resp.status_code == 429:
                        wait = max(wait, self.cfg.submit_429_min_sleep, self._dynamic_submit_sleep)
                    if not did_cooldown:
                        time.sleep(wait)
                    continue
                resp.raise_for_status()
                self._on_status(resp.status_code)
                loc = (resp.headers.get("Location") or "").strip()
                body = self._safe_json(resp)
                if isinstance(body, dict):
                    alpha_id = _alpha_id_from_progress(body)
                    if alpha_id:
                        return alpha_id, body, "ok"
                    for key in ("location", "url", "href", "self"):
                        v = body.get(key)
                        if isinstance(v, str) and v.strip():
                            loc = v.strip()
                            break
                if not loc:
                    last_err = "missing_location"
                    time.sleep(min(2 ** (attempt - 1), 8))
                    continue
                progress_url = loc if loc.startswith("http") else urljoin(f"{self._BASE}/", loc.lstrip("/"))
                break
            except requests.RequestException as e:
                last_err = f"submit_error:{e}"
                if _is_dns_error(e):
                    self._consecutive_dns_errors += 1
                    if self._consecutive_dns_errors >= self.cfg.dns_error_pause_count:
                        return None, None, "network_dns_error_batch_paused"
                if attempt < self.cfg.max_retries:
                    time.sleep(min(2 ** (attempt - 1), 10))
        if not progress_url:
            return None, None, last_err or "submit_failed"
        aid, body, st = self._monitor_simulation(progress_url)
        ext = int(getattr(self.cfg, "simulation_poll_retry_extend_seconds", 0) or 0)
        if ext > 0 and isinstance(st, str) and st.startswith("poll_timeout"):
            print(f"[simulate] poll_timeout; extending poll by {ext}s …")
            aid2, body2, st2 = self._monitor_simulation(progress_url, extra_seconds=float(ext))
            if aid2:
                return aid2, body2, st2
            return aid2, body2 or body, st2
        return aid, body, st

    def _monitor_simulation(self, progress_url: str, *, extra_seconds: float = 0.0) -> tuple[str | None, dict | None, str]:
        base_secs = float(self.cfg.max_poll_seconds_per_alpha)
        deadline = time.time() + base_secs + max(0.0, float(extra_seconds))
        sleep_s = max(0.5, float(self.cfg.poll_fallback_sleep))
        last_status = "polling"
        last_body: dict | None = None
        while time.time() < deadline:
            try:
                pr = self._sess_request("GET", progress_url, timeout=self._timeout())
                if pr.status_code == 401:
                    self.ensure_authenticated(force=True)
                    time.sleep(1.0)
                    continue
                if pr.status_code == 403:
                    return None, self._safe_json(pr), f"poll_forbidden:{(pr.text or '')[:300]}"
                pr.raise_for_status()
                body = self._safe_json(pr)
                if isinstance(body, dict):
                    last_body = body
                    status = str(body.get("status") or body.get("state") or "").lower()
                    if status:
                        last_status = status
                    if status in ("failed", "error", "rejected"):
                        return None, body, status
                    alpha_id = _alpha_id_from_progress(body)
                    if alpha_id:
                        return alpha_id, body, "ok"
                time.sleep(sleep_s)
                sleep_s = min(sleep_s * 1.2, 8.0)
            except requests.RequestException as e:
                last_status = f"poll_error:{e}"
                time.sleep(self.cfg.poll_error_sleep)
        return None, last_body, f"poll_timeout:{last_status}"

    def _safe_json(self, resp: requests.Response) -> dict:
        try:
            obj = resp.json()
        except Exception:
            return {}
        return obj if isinstance(obj, dict) else {}

    def fetch_alpha_detail(self, alpha_id: str, *, retries: int = 2) -> dict | None:
        last: dict | None = None
        for attempt in range(max(1, int(retries))):
            for url in (f"{self._BASE}/alphas/{alpha_id}", f"{self._BASE}/alphas/{alpha_id}/"):
                try:
                    r = self._sess_request("GET", url, timeout=self._timeout())
                    if r.status_code == 200:
                        body = r.json()
                        if isinstance(body, dict):
                            last = body
                            if _metric_get(body, "sharpe", "Sharpe") is not None:
                                return body
                except Exception:
                    continue
            if attempt + 1 < retries:
                time.sleep(1.5)
        return last

    def fetch_alpha_daily_pnl(self, alpha_id: str) -> list[tuple[str, float]]:
        """Fetch daily PnL series from the read-only recordsets endpoint.

        Calls GET /alphas/{id}/recordsets/pnl — a lightweight read-only query
        that does NOT consume a simulation queue slot.  Returns a sorted list
        of (date_str, return) pairs, or [] on any error or missing data.
        """
        for url in (
            f"{self._BASE}/alphas/{alpha_id}/recordsets/pnl",
            f"{self._BASE}/alphas/{alpha_id}/recordsets/pnl/",
        ):
            try:
                r = self._sess_request("GET", url, timeout=self._timeout())
                if r.status_code == 404:
                    break  # endpoint not available for this alpha
                if r.status_code != 200:
                    continue
                body = r.json()
                if not isinstance(body, dict):
                    continue
                # Platform returns e.g. {"records": [{"date": "2020-01-02", "pnl": 0.003}, ...]}
                records = body.get("records") or body.get("data") or body.get("pnl") or []
                if not isinstance(records, list):
                    continue
                pairs: list[tuple[str, float]] = []
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    date = str(rec.get("date") or rec.get("Date") or "")
                    ret_raw = rec.get("pnl") or rec.get("return") or rec.get("dailyReturn") or rec.get("dailyPnl")
                    if not date or ret_raw is None:
                        continue
                    try:
                        pairs.append((date, float(ret_raw)))
                    except (TypeError, ValueError):
                        continue
                if pairs:
                    return sorted(pairs, key=lambda x: x[0])
            except Exception:
                continue
        return []

    def check_alpha(
        self,
        alpha_id: str,
        *,
        max_wait_seconds: float | None = None,
        heartbeat_label: str = "",
        allow_self_corr_extend: bool = True,
    ) -> tuple[bool | None, dict | None, str]:
        wait_s = float(self.cfg.max_check_poll_seconds) if max_wait_seconds is None else float(max_wait_seconds)
        deadline = time.time() + max(1.0, wait_s)
        metric_pass_deadline: float | None = None
        last_note = "pending"
        polls = 0
        last_detail: dict | None = None
        last_hb_ts = time.time()
        hb_polls = max(1, int(self.cfg.recheck_heartbeat_every_polls))
        hb_min_s = float(getattr(self.cfg, "recheck_heartbeat_min_seconds", 120.0) or 120.0)
        while time.time() < deadline:
            polls += 1
            detail = self.fetch_alpha_detail(alpha_id)
            if not isinstance(detail, dict):
                if heartbeat_label and (
                    polls % hb_polls == 0 or (time.time() - last_hb_ts) >= hb_min_s
                ):
                    print(f"[recheck] {heartbeat_label} alpha_id={alpha_id} polls={polls} note=no_detail")
                    last_hb_ts = time.time()
                time.sleep(self.cfg.check_poll_interval_seconds)
                continue
            last_detail = detail
            hard = _hard_fail_checks(detail)
            if hard:
                return False, detail, "check_failed:" + ",".join(hard)
            if _non_self_checks_all_pass(detail):
                if not _self_correlation_pending(detail):
                    return True, detail, "check_passed"
                last_note = "self_correlation_pending"
                if metric_pass_deadline is None and allow_self_corr_extend:
                    extra = float(getattr(self.cfg, "check_self_correlation_extra_seconds", 0.0) or 0.0)
                    # v50.1: 允许 async_batch 等调用方传入 max_wait_seconds 时也扩展
                    if extra > 0:
                        max_total = float(self.cfg.max_check_poll_seconds)
                        metric_pass_deadline = time.time() + min(extra, max_total * 0.6)
                        deadline = max(deadline, min(metric_pass_deadline, time.time() + max_total))
                        if heartbeat_label:
                            print(
                                f"[recheck] {heartbeat_label} alpha_id={alpha_id} "
                                f"metric checks PASS — extended self_correlation poll +{min(extra, max_total * 0.6):.0f}s"
                            )
            is_data = detail.get("is") if isinstance(detail.get("is"), dict) else {}
            if str(is_data.get("check_passed") or "").lower() in ("true", "1", "yes"):
                return True, detail, "check_passed"
            if polls % hb_polls == 0 or (time.time() - last_hb_ts) >= hb_min_s:
                if heartbeat_label:
                    print(
                        f"[recheck] {heartbeat_label} alpha_id={alpha_id} "
                        f"polls={polls} note={last_note}"
                    )
                    last_hb_ts = time.time()
            time.sleep(self.cfg.check_poll_interval_seconds)
        if last_detail and _non_self_checks_all_pass(last_detail) and _self_correlation_pending(last_detail):
            return None, last_detail, "metric_pass:self_correlation_pending"
        return None, last_detail, f"check_timeout:{last_note}"

    def _initial_simulate_check_wait(self, merged: dict | None) -> float:
        """Return poll seconds for initial platform-check after simulate.

        Metric-pass alphas get a short quality wait; metric-miss alphas skip inline check.
        """
        wait = max(1.0, float(getattr(self.cfg, "simulate_check_poll_seconds", 120.0) or 120.0))
        if not isinstance(merged, dict):
            return wait
        sh_pre = _to_float(_metric_get(merged, "sharpe", "Sharpe"))
        fi_pre = _to_float(_metric_get(merged, "fitness", "Fitness"))
        metrics = {
            "sharpe": sh_pre,
            "fitness": fi_pre,
            "turnover": _to_float(_metric_get(merged, "turnover", "Turnover")),
            "returns": _to_float(_metric_get(merged, "returns", "Returns")),
            "drawdown": _to_float(_metric_get(merged, "drawdown", "Drawdown")),
            "margin": _to_float(_metric_get(merged, "margin", "Margin")),
        }
        gate_ok, gate_reason = self._metric_gate(metrics)
        if gate_reason == "missing_core_metrics":
            return 0.0
        if not gate_ok:
            return 0.0
        quality_wait = max(wait, float(getattr(self.cfg, "simulate_quality_check_poll_seconds", 300.0) or 300.0))
        return min(quality_wait, float(self.cfg.max_check_poll_seconds))

    def queue_decision(self, payload: dict, alpha_id: str | None, result_json: dict | None, check_passed: bool | None, check_note: str) -> tuple[str, dict | None]:
        if not alpha_id or not isinstance(result_json, dict):
            return "not_queued", None
        if _is_incompatible_unit_result(result_json):
            return "not_queued:incompatible_unit", None
        hard = _hard_fail_checks(result_json)
        if hard:
            return "not_queued:hard_fail", None
        sharpe = _to_float(_metric_get(result_json, "sharpe", "Sharpe"))
        fitness = _to_float(_metric_get(result_json, "fitness", "Fitness"))
        turnover = _to_float(_metric_get(result_json, "turnover", "Turnover"))
        ledger_metrics = self._feedback_metrics_for_alpha(alpha_id)
        if sharpe is None:
            sharpe = _to_float(ledger_metrics.get("sharpe"))
        if fitness is None:
            fitness = _to_float(ledger_metrics.get("fitness"))
        if turnover is None:
            turnover = _to_float(ledger_metrics.get("turnover"))
        # Queue gate: platform checks first. Metrics are NOT a substitute for passing checks.
        checks = _extract_checks(result_json)
        non_self_checks = [c for c in checks if str(c.get("name") or "").upper() != "SELF_CORRELATION"]
        non_self_all_pass = bool(non_self_checks) and all(str(c.get("result") or "").upper() == "PASS" for c in non_self_checks)
        self_pending = any(
            str(c.get("name") or "").upper() == "SELF_CORRELATION" and str(c.get("result") or "").upper() == "PENDING"
            for c in checks
            if isinstance(c, dict)
        )

        similarity_to_winners = self._similarity_to_winners(str(payload.get("regular") or ""))
        metrics = {
            "sharpe": sharpe,
            "fitness": fitness,
            "turnover": turnover,
            "returns": _to_float(_metric_get(result_json, "returns")),
            "drawdown": _to_float(_metric_get(result_json, "drawdown")),
            "margin": _to_float(_metric_get(result_json, "margin")),
        }
        for key in ("returns", "drawdown", "margin"):
            if metrics[key] is None:
                metrics[key] = _to_float(ledger_metrics.get(key))
        gate_ok, gate_note = self._metric_gate(metrics)
        pending_recheck = (
            (non_self_all_pass and self_pending)
            or (check_passed is None and str(check_note or "").startswith("check_timeout"))
        )
        self_status = _self_correlation_status(result_json)
        all_checks_pass = bool(checks) and all(
            str(c.get("result") or c.get("status") or "UNKNOWN").upper() == "PASS"
            for c in checks
            if isinstance(c, dict)
        )
        if (check_passed is True and non_self_all_pass and self_status == "PASS" and all_checks_pass) or pending_recheck:
            if not gate_ok:
                return f"not_queued:{gate_note}", None
            if similarity_to_winners >= self.cfg.max_queue_similarity:
                return "not_queued:too_similar_to_winner", None
            entry = {
                "queued_at": _utc(),
                "status": "ready" if check_passed is True else "needs_recheck",
                "alpha_id": alpha_id,
                "expression": payload.get("regular"),
                "settings": payload.get("settings"),
                "meta": payload.get("meta", {}),
                "metrics": metrics,
                "similarity_to_winners": similarity_to_winners,
                "check_passed": check_passed,
                "check_note": check_note,
                "checks": checks,
            }
            self.queue.append(entry)
            return entry["status"], entry
        # If checks did not pass, do not queue (even if metrics look ok).
        if not gate_ok:
            return f"not_queued:checks_not_passed_{gate_note}", None
        return "not_queued:checks_not_passed", None

    def submit_alpha(self, alpha_id: str) -> tuple[bool, str]:
        if self.cfg.dry_run_submit:
            return True, "dry_run"
        return False, "legacy_live_submit_disabled:use_python_m_alpha_mining_submit_execute"

    def patch_alpha_description(self, alpha_id: str, description_text: str) -> bool:
        """PATCH /alphas/{alpha_id} with description field. Returns True on success, False on any error."""
        if self.cfg.dry_run_submit:
            return True
        if not alpha_id or not description_text:
            return False
        url = f"{self._BASE}/alphas/{alpha_id}"
        try:
            resp = self._sess_request("PATCH", url, json={"description": description_text}, timeout=self._timeout())
            if resp.status_code == 401:
                self.ensure_authenticated(force=True)
                resp = self._sess_request("PATCH", url, json={"description": description_text}, timeout=self._timeout())
            if resp.status_code in (200, 201, 204):
                return True
            print(f"[description-patch] HTTP {resp.status_code} alpha_id={alpha_id[:12]}")
            return False
        except Exception as exc:
            print(f"[description-patch] error: {type(exc).__name__}: {exc}")
            return False

    def _get_description_for(self, alpha_id: str, expression: str, family: str = "", source: str = "") -> str | None:
        """Return a description for this alpha: SQLite first, then LLM/template fallback."""
        sqlite_path = getattr(self.cfg, "sqlite_runs_path", None)
        if sqlite_path:
            try:
                from alpha_mining.integration.phase4 import expression_id_for
                from alpha_mining.submitter.observation import SubmissionObservationService
                from alpha_mining.storage.sqlite_store import SqliteRunLog

                svc = self._submission_observation_service()
                if svc is None:
                    # Build a read-only service instance even when observe is disabled
                    svc = SubmissionObservationService(SqliteRunLog(sqlite_path))
                expr_id = expression_id_for(expression)
                cached = svc.fetch_description(expr_id, alpha_id)
                if cached:
                    return cached
            except Exception as exc:
                print(f"[description-fetch] sqlite lookup failed: {type(exc).__name__}: {exc}")
        # Fallback: generate on the fly (LLM if available, else template)
        try:
            from alpha_mining.submitter.description import generate_description

            llm = None
            try:
                from alpha_mining.llm.deepseek import DeepSeekStructuredLLM
                llm = DeepSeekStructuredLLM()
            except Exception:
                pass
            try:
                draft = generate_description(expression, llm=llm, family=family, source=source)
                return draft.text
            finally:
                close = getattr(llm, "close", None)
                if callable(close):
                    close()
        except Exception as exc:
            print(f"[description-fetch] generate fallback failed: {type(exc).__name__}: {exc}")
            return None

    def run_yearly_ladder_check(self, row: dict) -> tuple[bool, str]:
        """Run IS ladder Sharpe check.  Returns (passes, note).

        Priority 1 (zero extra platform calls): use locally stored daily return
        series to compute per-year Sharpe.  This path is taken whenever the
        alpha_id has daily returns in the SQLite store.

        Priority 2 (fallback, only when ladder_check_enabled=True): fire one
        platform simulation per year.  Used when daily returns are not yet
        cached (e.g., first run after upgrade, or recordsets endpoint absent).

        Year range is derived dynamically from the available data; the config
        fields ladder_check_start_year / ladder_check_end_year serve only as
        fallback bounds for the platform-call path.
        """
        if not getattr(self.cfg, "ladder_check_enabled", False):
            return True, "ladder_check_disabled"
        expression = str(row.get("expression") or "").strip()
        if not expression:
            return True, "ladder_check_skipped:missing_expression"
        threshold = float(getattr(self.cfg, "ladder_check_min_sharpe", 1.0))
        from alpha_mining.filter.ladder_check import (
            check_yearly_sharpes,
            year_range,
            yearly_sharpes_from_daily_returns,
        )

        # ── path 1: local daily returns ───────────────────────────────────────
        alpha_id = str(row.get("alpha_id") or "").strip()
        if alpha_id:
            sqlite_path = getattr(self.cfg, "sqlite_runs_path", None)
            if sqlite_path:
                from alpha_mining.storage.sqlite_store import SqliteRunLog
                store = SqliteRunLog(sqlite_path)
                daily = store.fetch_daily_returns(alpha_id)
                if daily:
                    yearly = yearly_sharpes_from_daily_returns(daily)
                    if yearly:
                        result = check_yearly_sharpes(yearly, threshold=threshold)
                        tag = "ladder_local"
                        if not result.passes:
                            return False, f"{tag}:{result.note}"
                        return True, f"{tag}:pass:{len(yearly)}years"

        # ── path 2: platform simulation per year (fallback) ───────────────────
        settings = row.get("settings")
        if not isinstance(settings, dict):
            return True, "ladder_check_skipped:missing_settings"
        start_year = int(getattr(self.cfg, "ladder_check_start_year", 2019))
        end_year = int(getattr(self.cfg, "ladder_check_end_year", 2023))
        yearly: list[tuple[int, float]] = []
        for yr in year_range(start_year, end_year):
            year_settings = {**settings, "startDate": f"{yr}-01-01", "endDate": f"{yr}-12-31"}
            payload = {"type": "REGULAR", "settings": year_settings, "regular": expression}
            try:
                _aid, body, status = self.submit_simulation(payload)
                if status == "ok" and isinstance(body, dict):
                    sh = _to_float(_metric_get(body, "sharpe", "Sharpe"))
                    if sh is not None:
                        yearly.append((yr, sh))
                        continue
                print(f"[ladder-check] year={yr} sim status={status!r}")
            except Exception as exc:
                print(f"[ladder-check] year={yr} error: {type(exc).__name__}: {exc}")
        if not yearly:
            return True, "ladder_check_skipped:no_data"
        result = check_yearly_sharpes(yearly, threshold=threshold)
        if not result.passes:
            return False, f"ladder_check:{result.note}"
        return True, f"ladder_check:pass:{len(yearly)}years"

    # ---- modes ----

    def generate_candidates(self) -> tuple[list[ExpressionCandidate], FieldCatalog]:
        self.load_history_learning()
        fields_df = self.top_fields(self.fetch_datafields())
        catalog = FieldCatalog.from_df(fields_df)
        validator = PreflightValidator(catalog, min_ts_corr_window=self.cfg.min_ts_corr_window)
        library_exact, library_skeletons, library_alpha_ids = self.fetch_library_fingerprints()
        if self.cfg.sync_platform_tried_before_simulate:
            self._merge_platform_tried(library_exact, library_alpha_ids)
        factory = ExpressionFactory(self.cfg, catalog, validator)
        if bool(getattr(self.cfg, "generate_exact_seen_from_tried", True)):
            generate_seen = set(self._tried_expressions)
            print(f"[generate] exact_seen=tried_only n={len(generate_seen)} (not full ledger n={len(self._history_seen_exact)})")
        else:
            generate_seen = self._history_seen_exact
        candidates = factory.generate(
            generate_seen,
            self._history_seen_skeleton,
            self._history_pools,
            library_skeletons,
            tried_exact=set(self._tried_expressions),
        )
        if self.cfg.near_pass_enabled and self._near_pass_records:
            phase4_records = self.phase4_mutate_near_pass_records(
                self._near_pass_records,
                validator,
                existing_expressions=self._tried_expressions | {c.expression for c in candidates},
            )
            if phase4_records:
                tree_candidates = [
                    ExpressionCandidate(
                        record["expression"],
                        record["meta"]["family"],
                        record["meta"]["source"],
                        float(record["metrics"].get("sharpe") or 0.0),
                    )
                    for record in phase4_records
                ]
                candidates = tree_candidates + candidates
            floor = max(1, int(self.cfg.min_candidates_floor))
            core_n = sum(1 for c in candidates if not (c.family or "").startswith("near_pass_variant"))
            template_core = sum(
                1
                for c in candidates
                if (c.family or "").startswith(("alpha_models_template", "formulaic_"))
            )
            min_core = max(1, int(getattr(self.cfg, "near_pass_min_core_candidates", 12)))
            skip_np_when_tpl = max(0, int(getattr(self.cfg, "near_pass_skip_when_template_core", 20)))
            shortfall = core_n < max(
                min_core, int(floor * float(self.cfg.near_pass_shortfall_ratio))
            )
            always_amp = bool(getattr(self.cfg, "near_pass_always_amplify", True))
            if core_n < min_core and not always_amp:
                print(
                    f"[near_pass] skipped: core_candidates={core_n} < {min_core} "
                    f"(fix template/formulaic generation first; near_pass cannot replace empty core pool)"
                )
            elif (
                not always_amp
                and template_core >= skip_np_when_tpl
                and not shortfall
            ):
                print(
                    f"[near_pass] skipped: template/formulaic core={template_core} >= {skip_np_when_tpl} "
                    f"(avoid flooding prescreen with near_pass variants)"
                )
            elif always_amp or not self.cfg.near_pass_only_when_short or shortfall:
                amplifier = NearPassAmplifier(self.cfg, catalog, validator)
                extra = amplifier.amplify(
                    self._near_pass_records, self._tried_expressions | {c.expression for c in candidates}
                )
                if extra:
                    existing = {c.expression for c in candidates}
                    deduped = [c for c in extra if c.expression not in existing]
                    amp_cap = max(0, int(getattr(self.cfg, "near_pass_amplify_cap", 480)))
                    if amp_cap and len(deduped) > amp_cap:
                        deduped = deduped[:amp_cap]
                    # Prepend so prescreen/allocator see near-pass before arch liquid flood.
                    candidates = deduped + candidates
                    print(
                        f"[near_pass] amplified +{len(deduped)} (prepended, cap={amp_cap or 'all'}) "
                        f"from {len(self._near_pass_records)} seeds"
                    )
            else:
                print(
                    f"[near_pass] skipped amplify: candidates={len(candidates)} >= "
                    f"floor*{self.cfg.near_pass_shortfall_ratio:.2f} (templates/fundamental first)"
                )
        clones = self._feedback_pass_clones(
            catalog,
            validator,
            self._tried_expressions | {c.expression for c in candidates},
        )
        if clones:
            existing = {c.expression for c in candidates}
            add = [c for c in clones if c.expression not in existing]
            if add:
                candidates = add + candidates
                print(f"[generate] feedback_clones +{len(add)} (prepended, cap={int(self.cfg.feedback_clone_max)})")
        # Phase 3: LLM grammar candidates from Research Memory hypotheses (opt-in).
        if getattr(self.cfg, "phase3_llm_grammar_enabled", False):
            llm_cands = self._phase235_llm_candidates(
                catalog,
                factory,
                existing_expressions=self._tried_expressions | {c.expression for c in candidates},
            )
            if llm_cands:
                existing = {c.expression for c in candidates}
                llm_cands = [c for c in llm_cands if c.expression not in existing]
                if llm_cands:
                    candidates = llm_cands + candidates
        # Phase 5: update SubmissionJudge priority scores in Research Memory (opt-in).
        if getattr(self.cfg, "phase5_judge_enabled", False):
            self._phase5_update_judge_scores()
        self._append_generated_registry(candidates)
        print(f"[generate] candidates={len(candidates)}")
        return candidates, catalog

    def run_generate(self) -> Any:
        self.ensure_authenticated(force=False)
        candidates, _ = self.generate_candidates()
        rows = [{"expression": c.expression, "family": c.family, "source": c.source, "score": c.score} for c in candidates[: self.cfg.budget]]
        out = self._path(f"{self.cfg.output_prefix}_candidates.csv")
        pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[generate] saved {out.name}")
        return pd.DataFrame(rows)

    def _order_payloads_for_prescreen(self, payloads: list[dict]) -> list[dict]:
        """Near-pass + templates + pass-first before arch explore (payload expand + prescreen)."""
        if not payloads:
            return payloads
        max_near = max(
            80,
            int(len(payloads) * float(getattr(self.cfg, "near_pass_max_family_share", 0.55))),
        )
        templates: list[dict] = []
        core: list[dict] = []
        near: list[dict] = []
        other: list[dict] = []
        for p in payloads:
            if not isinstance(p, dict):
                other.append(p)
                continue
            fam = str((p.get("meta") or {}).get("family") or "").lower()
            if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
                templates.append(p)
            elif fam.startswith("near_pass"):
                near.append(p)
            elif fam.startswith("pass_") or str((p.get("meta") or {}).get("source") or "").lower() == "pass_first":
                core.append(p)
            else:
                other.append(p)
        _fsp = float(getattr(self.cfg, "diversity_fast_signal_penalty", 0.0))
        rank = lambda p: _payload_fine_rank_key(p, self._family_pass_rates, self._family_quality_stats, _fsp)
        templates.sort(key=rank)
        core.sort(key=rank)
        other.sort(key=rank)
        near.sort(key=rank)
        if len(near) > max_near:
            print(f"[batch] near_pass payloads capped {len(near)} -> {max_near} (templates/fundamental first)")
            near = near[:max_near]
        return near + templates + core + other

    def _allocate_payload_budget(self, payloads: list[dict], run_cap: int) -> tuple[list[dict], dict[str, int]]:
        """v50.3: 当 prescreen 产出 < run_cap 时跳过配额，全部通过。

        v50.2 的多桶配额把 177 fine-selected 砍到 67。fine pass 已经做了
        intrabatch/shape/duplicate 多样性过滤，allocator 不需要再加一层。
        只保留 arch 家族上限（因为历史 pass rate ~0%）。
        """
        if run_cap <= 0 or not payloads:
            return [], {}
        min_batch = int(self.cfg.min_simulate_batch)
        run_cap = min(len(payloads), max(run_cap, min_batch))
        rates = getattr(self, "_family_pass_rates", None) or {}
        qstats = getattr(self, "_family_quality_stats", None) or {}
        low_yield_buckets = getattr(self, "_family_low_yield_buckets", {}) or {}
        payloads = sorted(payloads, key=lambda p: _payload_fine_rank_key(p, rates, qstats, float(getattr(self.cfg, "diversity_fast_signal_penalty", 0.0))))
        if str(getattr(self.cfg, "preset", "")).lower() == "diverse_exploration":
            return self._allocate_diverse_exploration_payload_budget(payloads, run_cap)
        if not _quality_diverse_enabled(self.cfg) and len(payloads) < min_batch:
            selected: list[dict] = []
            stats: Counter[str] = Counter()
            low_yield_budget = max(
                0,
                int(max(run_cap, min_batch) * float(getattr(self.cfg, "max_arch_explore_batch_share", 0.03))),
            )
            low_yield_seen = 0
            for p in payloads:
                fam = str((p.get("meta") or {}).get("family") or "").lower()
                src = str((p.get("meta") or {}).get("source") or "").lower()
                if _is_quality_simulate_family(fam, src):
                    selected.append(p)
                    stats["quality_passthrough"] += 1
                elif _is_low_yield_arch_family(fam) and low_yield_seen < low_yield_budget:
                    selected.append(p)
                    low_yield_seen += 1
                    stats["low_yield_underfill_included"] += 1
                if len(selected) >= run_cap:
                    break
            stats["quality_family_selected"] = sum(
                1
                for p in selected
                if _is_quality_simulate_family(
                    str((p.get("meta") or {}).get("family") or "").lower(),
                    str((p.get("meta") or {}).get("source") or "").lower(),
                )
            )
            stats["selected_total"] = len(selected)
            stats["pass_rate_underfill_passthrough"] = len(selected)
            if len(selected) < min_batch:
                print(
                    f"[allocator] pass_rate underfill selected={len(selected)} < min_simulate_batch={min_batch} "
                    "(kept quality candidates; capped low-yield arch)"
                )
            return selected, dict(stats)

        # v50.3: 当 prescreen 产出不够 fill run_cap 时，不做配额限制
        near_quota = min(
            sum(1 for p in payloads if str((p.get("meta") or {}).get("family") or "").lower().startswith("near_pass")),
            max(int(self.cfg.near_pass_batch_quota), int(run_cap * float(self.cfg.min_near_pass_batch_share))),
            run_cap,
        )
        remain = max(0, run_cap - near_quota)
        template_quota = min(
            len([p for p in payloads if str((p.get("meta") or {}).get("family") or "").lower().startswith(("alpha_models", "formulaic"))]),
            max(0, min(int(getattr(self.cfg, "alpha_models_batch_quota", 24)), int(run_cap * 0.08))),
            max(0, int(run_cap * float(self.cfg.min_template_batch_share))),
            remain,
        )
        remain = max(0, remain - template_quota)
        hybrid_quota = min(max(0, int(getattr(self.cfg, "pass_hybrid_batch_quota", 0))), remain)
        remain = max(0, remain - hybrid_quota)
        robust_quota = min(max(0, int(self.cfg.robust_batch_quota)), remain)
        remain = max(0, remain - robust_quota)
        pass_first_quota = min(
            sum(
                1
                for p in payloads
                if str((p.get("meta") or {}).get("family") or "").lower().startswith("pass_fundamental")
                or str((p.get("meta") or {}).get("source") or "").lower() == "pass_first"
            ),
            max(int(self.cfg.pass_first_batch_quota), int(run_cap * float(self.cfg.min_pass_quality_batch_share))),
            remain,
        )
        remain = max(0, remain - pass_first_quota)
        low_yield_arch_budget = min(
            remain,
            max(0, int(max(run_cap, min_batch) * float(getattr(self.cfg, "max_arch_explore_batch_share", 0.03)))),
        )
        arch_level_cap = min(max(0, int(self.cfg.max_arch_level_liquid_per_batch)), remain, low_yield_arch_budget)
        remain = max(0, remain - arch_level_cap)
        low_yield_arch_budget = max(0, low_yield_arch_budget - arch_level_cap)
        arch_delta_cap = min(max(0, int(self.cfg.max_arch_delta_liquid_per_batch)), remain, low_yield_arch_budget)
        remain = max(0, remain - arch_delta_cap)
        low_yield_arch_budget = max(0, low_yield_arch_budget - arch_delta_cap)
        arch_other_cap = min(max(0, int(self.cfg.max_arch_other_per_batch)), remain, low_yield_arch_budget)
        remain = max(0, remain - arch_other_cap)
        buckets: dict[str, list[dict]] = {
            "template": [],
            "near": [],
            "hybrid": [],
            "robust": [],
            "pass_first": [],
            "arch_level": [],
            "arch_delta": [],
            "arch_other": [],
            "other": [],
        }
        for p in payloads:
            fam = str((p.get("meta") or {}).get("family") or "").lower() if isinstance(p, dict) else ""
            src = str((p.get("meta") or {}).get("source") or "").lower() if isinstance(p, dict) else ""
            if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
                buckets["template"].append(p)
            elif fam.startswith("near_pass"):
                buckets["near"].append(p)
            elif fam.startswith("pass_fundamental_hybrid") or _is_priority_arch_quality_family(fam):
                buckets["hybrid"].append(p)
            elif fam.startswith("pass_fundamental_ts"):
                buckets["pass_first"].append(p)
            elif src == "pass_first" or fam.startswith("pass_pv") or fam.startswith("pass_fundamental"):
                buckets["pass_first"].append(p)
            elif fam.startswith("arch_level_liquid"):
                buckets["arch_level"].append(p)
            elif fam.startswith("arch_delta_liquid") or fam.startswith("arch_delta_ts"):
                buckets["arch_delta"].append(p)
            elif fam.startswith("arch_"):
                buckets["arch_other"].append(p)
            else:
                buckets["other"].append(p)

        selected: list[dict] = []
        stats: Counter[str] = Counter()
        ts_cap = max(0, int(self.cfg.pass_fundamental_ts_max_per_batch))
        ts_count = 0
        selected_bucket_counts: Counter[tuple[str, str, str, str, str, str]] = Counter()

        def take_from(group: str, n: int) -> None:
            nonlocal ts_count
            for p in buckets[group]:
                if len(selected) >= run_cap or n <= 0:
                    break
                fam = str((p.get("meta") or {}).get("family") or "").lower()
                bucket_key = _low_yield_bucket_key_for_payload(p)
                if bucket_key in low_yield_buckets and selected_bucket_counts[bucket_key] >= int(getattr(self.cfg, "low_yield_bucket_hard_cap", 2) or 2):
                    stats["low_yield_bucket_block"] += 1
                    continue
                if fam.startswith("pass_fundamental_ts") and ts_count >= ts_cap:
                    continue
                selected.append(p)
                selected_bucket_counts[bucket_key] += 1
                stats[group] += 1
                n -= 1
                if fam.startswith("pass_fundamental_ts"):
                    ts_count += 1

        def low_quality_family_cap(fam: str) -> int:
            if fam.startswith("alpha_models_template") or fam.startswith("formulaic_"):
                return max(0, int(getattr(self.cfg, "alpha_models_batch_quota", 24)))
            if fam.startswith("pass_fundamental_delta_liquid") or fam.startswith("pass_fundamental_liquid"):
                return max(4, int(run_cap * 0.04))
            if fam.startswith("pass_fundamental_delta"):
                return max(8, int(run_cap * 0.20))
            return run_cap

        def family_count(fam_prefix: str) -> int:
            return sum(
                1
                for p in selected
                if str((p.get("meta") or {}).get("family") or "").lower().startswith(fam_prefix)
            )

        def can_take_family(fam: str, *, allow_ts: bool = True) -> bool:
            if fam.startswith("pass_fundamental_ts") and (not allow_ts or ts_count >= ts_cap):
                return False
            if fam.startswith("alpha_models_template") or fam.startswith("formulaic_"):
                return stats["template"] < low_quality_family_cap(fam)
            if fam.startswith("pass_fundamental_delta_liquid") or fam.startswith("pass_fundamental_liquid"):
                return (
                    family_count("pass_fundamental_delta_liquid") + family_count("pass_fundamental_liquid")
                    < low_quality_family_cap(fam)
                )
            if fam.startswith("pass_fundamental_delta") and not fam.startswith("pass_fundamental_delta_liquid"):
                return family_count("pass_fundamental_delta") < low_quality_family_cap(fam)
            return True

        take_from("near", near_quota)
        take_from("template", template_quota)
        take_from("hybrid", hybrid_quota)
        take_from("robust", robust_quota)
        take_from("pass_first", pass_first_quota)
        take_from("arch_level", arch_level_cap)
        take_from("arch_delta", arch_delta_cap)
        take_from("arch_other", arch_other_cap)
        if len(selected) < run_cap:
            # Quality-first top-up: never flood with low-pass-rate arch families.
            fill_order = ("near", "pass_first", "hybrid", "template", "robust", "other")
            arch_fill_budget = max(0, int(run_cap * float(getattr(self.cfg, "max_arch_explore_batch_share", 0.10))))
            arch_filled = stats["arch_level"] + stats["arch_delta"] + stats["arch_other"]
            for group in fill_order:
                for p in buckets[group]:
                    if len(selected) >= run_cap:
                        break
                    if p in selected:
                        continue
                    fam = str((p.get("meta") or {}).get("family") or "").lower()
                    bucket_key = _low_yield_bucket_key_for_payload(p)
                    if bucket_key in low_yield_buckets and selected_bucket_counts[bucket_key] >= int(getattr(self.cfg, "low_yield_bucket_hard_cap", 2) or 2):
                        continue
                    if not can_take_family(fam):
                        continue
                    if fam.startswith("arch_"):
                        if arch_filled >= arch_fill_budget:
                            continue
                        arch_filled += 1
                    selected.append(p)
                    selected_bucket_counts[bucket_key] += 1
                    stats["fill"] += 1
                    if fam.startswith("pass_fundamental_ts"):
                        ts_count += 1
                    elif fam.startswith("arch_level_liquid"):
                        stats["arch_level"] += 1
                    elif fam.startswith("arch_delta_liquid") or fam.startswith("arch_delta_ts"):
                        stats["arch_delta"] += 1
                    elif fam.startswith("arch_"):
                        stats["arch_other"] += 1
                if len(selected) >= run_cap:
                    break
        stats["ts"] = ts_count
        if len(selected) < run_cap:
            before_topup = len(selected)
            selected = self._top_up_payloads_to_floor(
                selected,
                payloads,
                run_cap,
                payload_ok=lambda p: _is_quality_simulate_family(
                    str((p.get("meta") or {}).get("family") or "").lower(),
                    str((p.get("meta") or {}).get("source") or "").lower(),
                ),
            )
            added = len(selected) - before_topup
            if added:
                stats["quality_topup"] = added
                topup_fam = Counter(
                    str((p.get("meta") or {}).get("family") or "unknown").split(":")[0]
                    for p in selected[before_topup:]
                )
                print(f"[allocator] quality_topup +{added} families={dict(topup_fam.most_common(5))}")
        if len(selected) < min_batch:
            print(
                f"[allocator] WARN selected={len(selected)} < min_simulate_batch={min_batch} "
                f"(quality pool exhausted — not padding with arch_ts_rank/arch_regime)"
            )
        quality_payloads = [
            p for p in selected
            if _is_quality_simulate_family(
                str((p.get("meta") or {}).get("family") or "").lower(),
                str((p.get("meta") or {}).get("source") or "").lower(),
            )
        ]
        stats["quality_family_selected"] = len(quality_payloads)
        stats["low_yield_underfill_included"] = sum(
            1
            for p in selected
            if _is_low_yield_arch_family(str((p.get("meta") or {}).get("family") or "").lower())
        )
        stats["selected_total"] = len(selected)
        return selected, dict(stats)

    def _allocate_diverse_exploration_payload_budget(
        self, payloads: list[dict], run_cap: int
    ) -> tuple[list[dict], dict[str, int]]:
        """Bounded pilot allocator: shared archetype budget + no similarity relaxation."""
        def fam_of(p: dict) -> str:
            return str((p.get("meta") or {}).get("family") or "").lower()

        def src_of(p: dict) -> str:
            return str((p.get("meta") or {}).get("source") or "").lower()

        selected: list[dict] = []
        stats: Counter[str] = Counter()
        selected_ids: set[int] = set()

        def take(rows: Iterable[dict], limit: int, label: str) -> None:
            for p in rows:
                if len(selected) >= run_cap or stats[label] >= limit or id(p) in selected_ids:
                    continue
                selected.append(p)
                selected_ids.add(id(p))
                stats[label] += 1

        arch_rows = [p for p in payloads if fam_of(p).startswith("arch_")]
        arch_counts = getattr(self, "_family_simulation_counts", Counter()) or Counter()
        by_arch: dict[str, list[dict]] = defaultdict(list)
        for p in arch_rows:
            by_arch[fam_of(p)].append(p)
        arch_order = sorted(by_arch, key=lambda fam: (int(arch_counts.get(fam, 0)), fam))
        arch_quota = min(
            int(getattr(self.cfg, "arch_explore_batch_quota", 0) or 0),
            int(run_cap * float(getattr(self.cfg, "max_arch_explore_batch_share", 0.15))),
        )
        # Round-robin by least-sampled family instead of allowing the best-scored
        # archetype to consume the exploratory evidence budget.
        while stats["arch_explore"] < arch_quota:
            added = False
            for fam in arch_order:
                rows = by_arch[fam]
                candidate = next((p for p in rows if id(p) not in selected_ids), None)
                if candidate is None:
                    continue
                selected.append(candidate)
                selected_ids.add(id(candidate))
                stats["arch_explore"] += 1
                stats[f"archetype:{fam}"] += 1
                added = True
                if stats["arch_explore"] >= arch_quota:
                    break
            if not added:
                break

        near_rows = [p for p in payloads if fam_of(p).startswith("near_pass")]
        near_cap = min(
            len(near_rows),
            int(getattr(self.cfg, "near_pass_batch_quota", 0) or 0),
            int(run_cap * float(getattr(self.cfg, "min_near_pass_batch_share", 0.35))),
        )
        take(near_rows, near_cap, "near_pass")

        quality_rows = [
            p for p in payloads
            if not fam_of(p).startswith("near_pass")
            and not fam_of(p).startswith("arch_")
            and _is_quality_simulate_family(fam_of(p), src_of(p))
        ]
        quality_floor = min(
            len(quality_rows),
            math.ceil(run_cap * float(self.cfg.min_pass_quality_batch_share)),
        )
        take(quality_rows, quality_floor, "quality_base")

        for p in payloads:
            if len(selected) >= run_cap or id(p) in selected_ids:
                continue
            fam = fam_of(p)
            if fam.startswith("arch_"):
                continue
            if fam.startswith("near_pass") and stats["near_pass"] >= near_cap:
                continue
            if not _is_quality_simulate_family(fam, src_of(p)):
                continue
            selected.append(p)
            selected_ids.add(id(p))
            stats["quality_fill"] += 1
        stats["arch_explore_target"] = arch_quota
        stats["arch_explore_unused"] = max(0, arch_quota - stats["arch_explore"])
        stats["near_pass_target"] = near_cap
        stats["quality_base_target"] = quality_floor
        stats["selected_total"] = len(selected)
        stats["quality_family_selected"] = sum(
            1 for p in selected if _is_quality_simulate_family(fam_of(p), src_of(p))
        )
        return selected, dict(stats)

    def _batch_guard_allows_simulation(
        self,
        selected: list[dict],
        raw_family: Counter[str] | None = None,
    ) -> tuple[bool, list[str]]:
        n = len(selected)
        min_batch = max(1, int(getattr(self.cfg, "min_simulate_batch", 300) or 300))
        quality_diverse = _quality_diverse_enabled(self.cfg)
        # Near-pass share: underfilled quality batches should not deadlock on target min_batch.
        near_denom = max(n, 1) if n < min_batch else max(n, min_batch)
        # quality_diverse intentionally permits smaller batches after near-clone removal;
        # arch caps still use the target floor so 299/300 rounding does not flip decisions.
        mix_denom = max(n, 1) if quality_diverse else max(n, min_batch)
        arch_denom = max(n, min_batch)
        raw_family = raw_family or Counter()

        def fam_of(payload: dict) -> str:
            return str((payload.get("meta") or {}).get("family") or "").lower()

        def src_of(payload: dict) -> str:
            return str((payload.get("meta") or {}).get("source") or "").lower()

        near_n = sum(1 for p in selected if fam_of(p).startswith("near_pass_variant"))
        raw_near_n = sum(count for fam, count in raw_family.items() if str(fam).lower().startswith("near_pass"))
        low_yield_arch_n = sum(1 for p in selected if _is_low_yield_arch_family(fam_of(p)))
        quality_n = sum(1 for p in selected if _is_quality_simulate_family(fam_of(p), src_of(p)))

        violations: list[str] = []
        if n <= 0:
            violations.append("empty_batch")
        warnings: list[str] = []
        min_near = float(getattr(self.cfg, "min_near_pass_batch_share", 0.0) or 0.0)
        max_arch = float(getattr(self.cfg, "max_arch_explore_batch_share", 1.0) or 1.0)
        min_quality = float(getattr(self.cfg, "min_quality_batch_share", 0.0) or 0.0)
        raw_quality_n = sum(
            count
            for fam, count in raw_family.items()
            if _is_quality_simulate_family(str(fam), "")
            or str(fam).lower().startswith("near_pass")
        )
        # When upstream pool itself has zero quality/near_pass, blocking forever
        # just spins empty loop cycles. Allow underfill → warn + simulate pilot.
        allow_empty_quality = bool(
            quality_diverse
            and bool(getattr(self.cfg, "batch_guard_allow_underfill", False))
            and raw_quality_n <= 0
        )
        if raw_near_n > 0 and near_n == 0:
            warnings.append("near_pass_dropped_to_zero")
        if min_near > 0 and near_n / (max(n, 1) if quality_diverse else near_denom) + 1e-9 < min_near:
            warnings.append("near_pass_share_below_min")
        if max_arch >= 0 and low_yield_arch_n / arch_denom > max_arch + 1e-9:
            violations.append("low_yield_arch_share_above_cap")
        if min_quality > 0 and quality_n / mix_denom + 1e-9 < min_quality:
            if allow_empty_quality:
                warnings.append("quality_family_share_below_min")
            elif quality_diverse:
                violations.append("quality_family_share_below_min")
            elif quality_n <= 0:
                violations.append("quality_family_share_below_min")
            else:
                warnings.append("quality_family_share_below_min")
        if n > 0 and quality_n <= 0:
            if allow_empty_quality:
                warnings.append("no_quality_family_selected")
            else:
                violations.append("no_quality_family_selected")
        self._last_batch_guard_warnings = warnings
        return not violations, violations

    def _batch_guard_dataframe(self, selected: list[dict], violations: list[str]) -> Any:
        rows: list[dict[str, Any]] = []
        sample_payload = selected[0] if selected else {}
        sample_meta = sample_payload.get("meta") if isinstance(sample_payload.get("meta"), dict) else {}
        for reason in violations:
            rows.append(
                {
                    "index": 0,
                    "status": "skipped:batch_guard",
                    "reason": reason,
                    "expression": sample_payload.get("regular", ""),
                    "profile": sample_meta.get("profile", ""),
                    "family": sample_meta.get("family", ""),
                    "source": sample_meta.get("source", ""),
                }
            )
        if not rows:
            rows.append({"index": 0, "status": "skipped:batch_guard", "reason": "batch_guard"})
        return pd.DataFrame(rows)

    def _persist_guard_results(self, df: Any) -> None:
        out = self._path(f"{self.cfg.output_prefix}_results.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[batch] guard result saved={out.name} rows={len(df)}")

    def _sort_payloads_sim_priority(self, payloads: list[dict]) -> None:
        """Put higher-prior templates first within each bucket (allocator still applies quotas)."""
        payloads.sort(key=lambda p: _payload_fine_rank_key(p, self._family_pass_rates, self._family_quality_stats, float(getattr(self.cfg, "diversity_fast_signal_penalty", 0.0))))

    def _prescreen_template_bypass(self, payloads: list[dict]) -> list[dict]:
        """If coarse kills everything, let template/formulaic through on tried+toxic gates only."""
        screener = self._make_prescreen_screener()
        toxic_cap = float(self.cfg.prescreen_max_toxic_similarity)
        kept: list[dict] = []
        seen_expr: set[str] = set()
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            fam = str((payload.get("meta") or {}).get("family") or "").lower()
            if not (
                fam.startswith("alpha_models_template")
                or fam.startswith("formulaic_")
                or fam == "external_template"
            ):
                continue
            expr = _sig(payload.get("regular") or "")
            if not expr or expr in seen_expr:
                continue
            if self.cfg.prescreen_block_already_simulated:
                if _expression_identity(expr) in screener.tried_expression_ids:
                    continue
                payload_key = _payload_fingerprint(
                    expr, payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
                )
                if payload_key in screener.tried_payload_keys:
                    continue
            if screener.history_pools.max_similarity(expr, "toxic", early_exit_at=toxic_cap) >= toxic_cap:
                continue
            kept.append(payload)
            seen_expr.add(expr)
        return kept

    def _simulate_batch_target(self, available: int, hard_cap: int | None = None) -> int:
        """Batch size policy: at least min_simulate_batch; soft/unlimited unless capped."""
        floor = max(1, int(self.cfg.min_simulate_batch))
        soft = int(self.cfg.target_simulate_batch)
        safety = int(getattr(self.cfg, "max_simulate_batch_per_run", 0) or 0)
        if soft <= 0:
            n = max(floor, int(available))
        else:
            n = max(floor, min(int(available), soft))
        if safety > 0:
            n = min(n, safety)
        if hard_cap is not None:
            n = min(n, max(1, int(hard_cap)))
        return n

    def _top_up_payloads_to_floor(
        self,
        selected: list[dict],
        pool: list[dict],
        floor: int,
        *,
        family_ok: Callable[[str], bool] | None = None,
        payload_ok: Callable[[dict], bool] | None = None,
    ) -> list[dict]:
        if len(selected) >= floor:
            return selected
        seen_ids = {id(p) for p in selected}
        ordered = sorted(
            pool,
            key=lambda p: _payload_fine_rank_key(p, self._family_pass_rates, self._family_quality_stats, float(getattr(self.cfg, "diversity_fast_signal_penalty", 0.0))),
        )
        out = list(selected)
        for p in ordered:
            if len(out) >= floor:
                break
            if id(p) in seen_ids:
                continue
            meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
            fam = str(meta.get("family") or "").lower()
            if payload_ok is not None and not payload_ok(p):
                continue
            if family_ok is not None and not family_ok(fam):
                continue
            out.append(p)
            seen_ids.add(id(p))
        return out

    def _feedback_pass_clones(
        self, catalog: FieldCatalog, validator: PreflightValidator, tried: set[str]
    ) -> list[ExpressionCandidate]:
        """High-prior clones from passed / near-pass feedback (targets >=5%% batch pass rate)."""
        cap = max(0, int(getattr(self.cfg, "feedback_clone_max", 0)))
        if cap <= 0:
            return []
        amp = NearPassAmplifier(self.cfg, catalog, validator)
        out: list[ExpressionCandidate] = []
        seen: set[str] = set(tried)

        def add_expr(expr: str, score: float, fam: str = "pass_fundamental_clone") -> None:
            expr = _sig(expr)
            if not expr or expr in seen:
                return
            ok, _ = validator.validate(expr)
            if not ok:
                return
            seen.add(expr)
            out.append(ExpressionCandidate(expr, fam, "feedback_clone", score))

        for expr in sorted(self._passed_expressions, key=len)[:28]:
            add_expr(expr, 4.8, "pass_fundamental_passed")
            for v in amp._variants_for(expr)[:3]:
                add_expr(v, 4.6)
            if len(out) >= cap:
                return out[:cap]

        for rec in self._near_pass_records[:64]:
            expr = _sig(str(rec.get("expression") or ""))
            sh = float(rec.get("sharpe") or 0.0)
            ft = float(rec.get("fitness") or 0.0)
            if sh < 0.95 or sh + 1.05 * ft < 1.30:
                continue
            add_expr(expr, 4.2 + min(0.6, sh - 1.0))
            for v in amp._variants_for(expr)[:4]:
                add_expr(v, 4.0 + min(0.5, sh - 1.0))
            if len(out) >= cap:
                break
        return out[:cap]

    def _make_prescreen_screener(self) -> PreSimulationScreener:
        return PreSimulationScreener(
            self.cfg,
            tried_exact=self._tried_expressions,
            tried_payload_keys=self._tried_payload_keys,
            near_pass_expressions=self._near_pass_expression_set,
            failed_cluster=self._failed_cluster,
            history_pools=self._history_pools,
            top_field_lookup=self._top_field_in_expression,
            tried_metrics=self._tried_metrics,
            novelty_index=self._novelty_index,
            family_pass_rates=self._family_pass_rates,
            family_quality_stats=self._family_quality_stats,
        )

    def _prescreen_single_pass(
        self, payloads: list[dict], *, stage: str = "single"
    ) -> tuple[list[dict], Counter[str], list[tuple[str, str]]]:
        """One-pass prescreen with optional similarity relax (v40 behaviour)."""
        if not self.cfg.prescreen_enabled or not payloads:
            return payloads, Counter(), []
        min_t = self._simulate_batch_target(len(payloads), self.cfg.run_payload_cap)
        orig_ps = float(self.cfg.prescreen_max_history_similarity)
        orig_np = float(self.cfg.prescreen_near_pass_similarity)
        orig_ib = float(self.cfg.prescreen_intrabatch_similarity)
        relax = bool(self.cfg.prescreen_relax_to_hit_min_batch)
        if stage == "coarse":
            relax = relax and bool(self.cfg.prescreen_coarse_relax_to_fill)
        try:
            screener = self._make_prescreen_screener()
            kept, reasons, samples = screener.screen(payloads, stage=stage)
            best_family_drops = Counter(getattr(screener, "family_drop_counts", Counter()))

            def _pick_better(
                a: tuple[list[dict], Counter[str], list[tuple[str, str]]],
                b: tuple[list[dict], Counter[str], list[tuple[str, str]]],
            ) -> tuple[list[dict], Counter[str], list[tuple[str, str]]]:
                ka, _, _ = a
                kb, _, _ = b
                return a if len(ka) >= len(kb) else b

            best = (kept, reasons, samples)
            if (
                not relax
                or min_t <= 0
                or len(kept) >= min_t
                or len(kept) >= len(payloads)
            ):
                self._last_prescreen_family_drop_counts = best_family_drops
                return best[0], best[1], best[2]

            step = float(self.cfg.prescreen_similarity_relax_step)
            ceiling = float(self.cfg.prescreen_similarity_relax_ceiling)
            intra_step = float(self.cfg.prescreen_intrabatch_similarity_relax_step)
            intra_ceiling = float(self.cfg.prescreen_intrabatch_similarity_relax_ceiling)
            rounds = 0
            ps = orig_ps
            npv = orig_np
            ib = orig_ib
            label = "coarse" if stage == "coarse" else "prescreen"
            while len(best[0]) < min_t and (ps < ceiling - 1e-9 or ib < intra_ceiling - 1e-9):
                rounds += 1
                ps = min(ceiling, ps + step)
                npv = min(ceiling + 0.02, npv + step * 0.65)
                ib = min(intra_ceiling, ib + intra_step)
                self.cfg.prescreen_max_history_similarity = ps
                self.cfg.prescreen_near_pass_similarity = npv
                self.cfg.prescreen_intrabatch_similarity = ib
                screener = self._make_prescreen_screener()
                kept2, reasons2, samples2 = screener.screen(payloads, stage=stage)
                cand = (kept2, reasons2, samples2)
                old_best_len = len(best[0])
                best = _pick_better(best, cand)
                if len(best[0]) > old_best_len:
                    best_family_drops = Counter(getattr(screener, "family_drop_counts", Counter()))
                print(
                    f"[{label}] loosen round={rounds} "
                    f"prescreen_cap={self.cfg.prescreen_max_history_similarity:.3f} "
                    f"near_pass_cap={self.cfg.prescreen_near_pass_similarity:.3f} "
                    f"intra_cap={self.cfg.prescreen_intrabatch_similarity:.3f} "
                    f"kept_this_round={len(kept2)} best_kept={len(best[0])}/{len(payloads)} target={min_t}"
                )
                if rounds >= 48:
                    break
            self._last_prescreen_family_drop_counts = best_family_drops
            return best[0], best[1], best[2]
        finally:
            self.cfg.prescreen_max_history_similarity = orig_ps
            self.cfg.prescreen_near_pass_similarity = orig_np
            self.cfg.prescreen_intrabatch_similarity = orig_ib

    def _prescreen_two_stage(
        self, payloads: list[dict]
    ) -> tuple[list[dict], Counter[str], list[tuple[str, str]], int]:
        """Coarse expand → fine diverse select with fill-to-target (v41 + v42 fine relax)."""
        target = self._simulate_batch_target(len(payloads), self.cfg.run_payload_cap)
        strict_hist = float(self.cfg.prescreen_max_history_similarity)
        hist_ceiling = float(self.cfg.prescreen_similarity_relax_ceiling)
        coarse, coarse_reasons, coarse_samples = self._prescreen_single_pass(payloads, stage="coarse")
        if not coarse and payloads:
            bypass = self._prescreen_template_bypass(payloads)
            if bypass:
                coarse = bypass
                coarse_reasons = Counter({"template_bypass": len(bypass)})
                print(
                    f"[prescreen/coarse] template_bypass kept={len(bypass)} "
                    f"(tried/toxic only — weak/novelty history pools skipped for templates)"
                )
        print(
            f"[prescreen/coarse] in={len(payloads)} kept={len(coarse)} "
            f"target_simulate={target} strict_history_cap={strict_hist:.3f} hist_ceiling={hist_ceiling:.3f}"
        )
        family_drop_counts = Counter(getattr(self, "_last_prescreen_family_drop_counts", Counter()))
        screener = self._make_prescreen_screener()
        near_share = float(getattr(self.cfg, "min_near_pass_batch_share", 0.52))
        raw_near_count = sum(
            1
            for p in payloads
            if str((p.get("meta") or {}).get("family") or "").lower().startswith("near_pass_variant")
        )
        coarse_near_count = sum(
            1
            for p in coarse
            if str((p.get("meta") or {}).get("family") or "").lower().startswith("near_pass_variant")
        )
        if raw_near_count > 0 and coarse_near_count == 0:
            rescue_goal = min(raw_near_count, max(1, int(target * near_share + 0.999)))
            rescue, rescue_reasons = screener.select_near_pass_rescue(
                payloads,
                coarse,
                rescue_goal,
            )
            if rescue:
                coarse = rescue + coarse
                coarse_reasons.update(rescue_reasons)
                for reason, count in rescue_reasons.items():
                    family_drop_counts[("near_pass_variant", reason)] += count
                print(
                    f"[prescreen/coarse] near_pass_rescue +{len(rescue)}/{rescue_goal} "
                    f"raw_near={raw_near_count}"
                )
            else:
                coarse_reasons.update(rescue_reasons)
                for reason, count in rescue_reasons.items():
                    family_drop_counts[("near_pass_variant", reason)] += count
                print(
                    f"[prescreen/coarse] near_pass_rescue none raw_near={raw_near_count} "
                    f"reasons={dict(rescue_reasons.most_common(5))}"
                )
        effective_target = self._simulate_batch_target(len(coarse), self.cfg.run_payload_cap) if coarse else 0
        if effective_target < int(self.cfg.min_simulate_batch):
            print(
                f"[prescreen] WARN coarse_kept={len(coarse)} < floor={int(self.cfg.min_simulate_batch)}; "
                f"max simulate this run={effective_target}"
            )
        fine: list[dict] = []
        fine_reasons: Counter[str] = Counter()
        fine_samples: list[tuple[str, str]] = []
        near_goal = min(
            sum(1 for p in coarse if str((p.get("meta") or {}).get("family") or "").lower().startswith("near_pass")),
            max(0, int(effective_target * near_share + 0.999)),
        )
        near_pool = [
            p for p in coarse
            if str((p.get("meta") or {}).get("family") or "").lower().startswith("near_pass_variant")
        ]
        other_pool = [p for p in coarse if p not in near_pool]
        np_intra = float(getattr(self.cfg, "prescreen_near_pass_intrabatch_cap", 0.96))
        np_shape = int(getattr(self.cfg, "near_pass_fine_shape_quota", 14))

        if near_goal > 0 and near_pool:
            fine_near, r_near, s_near = screener.select_diverse_for_simulate(
                near_pool,
                near_goal,
                history_similarity_cap=strict_hist,
                intrabatch_cap_override=np_intra,
                shape_quota_override=np_shape,
            )
            fine.extend(fine_near)
            fine_reasons.update(r_near)
            fine_samples.extend(s_near)
            if len(fine_near) < near_goal and not _quality_diverse_enabled(self.cfg):
                picked_exprs = {_sig(p.get("regular") or "") for p in fine_near}
                retry_pool = [
                    p for p in near_pool
                    if _sig(p.get("regular") or "") and _sig(p.get("regular") or "") not in picked_exprs
                ]
                retry_need = near_goal - len(fine_near)
                retry_near, r_retry, s_retry = screener.select_diverse_for_simulate(
                    retry_pool,
                    retry_need,
                    history_similarity_cap=hist_ceiling,
                    intrabatch_cap_override=0.99,
                    shape_quota_override=-1,
                )
                if retry_near:
                    fine.extend(retry_near)
                    fine_reasons.update(r_retry)
                    fine_samples.extend(s_retry)
                    print(
                        f"[prescreen/fine] near_pass_retry +{len(retry_near)}/{retry_need} "
                        "intra_cap=0.99 shape_quota=off"
                    )
            print(
                f"[prescreen/fine] near_pass_stratum selected={sum(1 for p in fine if str((p.get('meta') or {}).get('family') or '').lower().startswith('near_pass_variant'))}/{near_goal} "
                f"intra_cap={np_intra:.2f} shape_quota={np_shape}"
            )

        remain = max(0, effective_target - len(fine))
        arch_cap = max(0, int(effective_target * float(getattr(self.cfg, "max_arch_explore_batch_share", 0.10))))
        if remain > 0 and other_pool:
            arch_pool = [
                p for p in other_pool
                if str((p.get("meta") or {}).get("family") or "").lower().startswith("arch_")
                and not _is_priority_arch_quality_family(
                    str((p.get("meta") or {}).get("family") or "").lower()
                )
            ]
            quality_pool = [p for p in other_pool if p not in arch_pool]
            if arch_pool and arch_cap > 0:
                fine_arch, r_arch, s_arch = screener.select_diverse_for_simulate(
                    arch_pool,
                    min(arch_cap, remain),
                    history_similarity_cap=hist_ceiling,
                    intrabatch_cap_override=0.88,
                )
                fine.extend(fine_arch)
                fine_reasons.update(r_arch)
                fine_samples.extend(s_arch)
                remain = max(0, effective_target - len(fine))
                print(f"[prescreen/fine] arch_explore_stratum selected={len(fine_arch)}/{arch_cap}")
            if remain > 0 and quality_pool:
                quality_pool = [
                    p for p in quality_pool
                    if _is_quality_simulate_family(
                        str((p.get("meta") or {}).get("family") or "").lower(),
                        str((p.get("meta") or {}).get("source") or "").lower(),
                    )
                ]
                fine_q, r_q, s_q = screener.select_diverse_for_simulate(
                    quality_pool,
                    remain,
                    history_similarity_cap=hist_ceiling,
                    intrabatch_cap_override=0.90,
                )
                fine.extend(fine_q)
                fine_reasons.update(r_q)
                fine_samples.extend(s_q)

        if bool(self.cfg.prescreen_fine_desperate_fill) and len(fine) < effective_target and coarse:
            need = effective_target - len(fine)
            topup = screener.select_coarse_topup(coarse, fine, need, history_similarity_cap=hist_ceiling)
            if topup:
                fine = fine + topup
                print(f"[prescreen/fine] coarse_topup +{len(topup)} total={len(fine)}/{effective_target}")

        salvage_stats: dict[str, int] = {}
        if bool(self.cfg.prescreen_fine_desperate_fill) and len(fine) < effective_target and coarse:
            need = effective_target - len(fine)
            salvage, salvage_stats = screener.select_salvage_topup(
                coarse,
                fine,
                need,
                target_n=effective_target,
                history_similarity_cap=hist_ceiling,
            )
            if salvage:
                fine = fine + salvage
                print(
                    f"[prescreen/fine] salvage_topup +{len(salvage)} total={len(fine)}/{effective_target} "
                    f"stats={salvage_stats}"
                )

        fill_cap = min(effective_target, len(coarse))
        if bool(getattr(self.cfg, "prescreen_fine_fill_to_target", True)) and len(fine) < fill_cap and coarse:
            need = fill_cap - len(fine)
            fill, fill_rejects = screener.select_coarse_throughput_fill(
                coarse,
                fine,
                fill_cap,
                history_similarity_cap=hist_ceiling,
            )
            if fill:
                fine = fine + fill
                fine_reasons.update(fill_rejects)
                print(
                    f"[prescreen/fine] throughput_fill +{len(fill)} total={len(fine)}/{fill_cap} "
                    f"(coarse pool={len(coarse)}) rejects={dict(fill_rejects.most_common(4))}"
                )

        reasons = Counter(coarse_reasons)
        reasons.update(fine_reasons)
        samples = list(coarse_samples) + list(fine_samples)
        near_selected = sum(
            1 for p in fine
            if str((p.get("meta") or {}).get("family") or "").lower().startswith("near_pass_variant")
        )
        quality_selected = sum(
            1 for p in fine
            if _is_quality_simulate_family(
                str((p.get("meta") or {}).get("family") or "").lower(),
                str((p.get("meta") or {}).get("source") or "").lower(),
            )
        )
        self._last_prescreen_diagnostic_stats = {
            "near_pass_coarse": len(near_pool),
            "near_pass_fine_goal": int(near_goal),
            "near_pass_fine_selected": int(near_selected),
            "quality_family_selected": int(quality_selected),
        }
        self._last_prescreen_family_drop_counts = family_drop_counts
        self._last_prescreen_diagnostic_stats.update(salvage_stats)
        print(
            f"[prescreen/fine] coarse={len(coarse)} selected={len(fine)}/{effective_target} "
            f"(requested_target={target}) fine_rejects={dict(fine_reasons.most_common(8))}"
        )
        return fine, reasons, samples, len(coarse)

    def _prescreen_until_target(
        self, payloads: list[dict]
    ) -> tuple[list[dict], Counter[str], list[tuple[str, str]]]:
        """Prescreen payloads for simulate.

        v41 default: two-stage (coarse quality gate → fine greedy diversity selection).
        v40 fallback: ``prescreen_two_stage=False`` single-pass sequential screen + relax.
        """
        if not self.cfg.prescreen_enabled or not payloads:
            self._last_prescreen_diagnostic_stats = {}
            self._last_prescreen_family_drop_counts = Counter()
            return payloads, Counter(), []
        if self.cfg.prescreen_two_stage:
            kept, reasons, samples, coarse_n = self._prescreen_two_stage(payloads)
            self._last_prescreen_coarse_count = coarse_n
            return kept, reasons, samples
        self._last_prescreen_coarse_count = 0
        self._last_prescreen_diagnostic_stats = {}
        self._last_prescreen_family_drop_counts = Counter()
        return self._prescreen_single_pass(payloads, stage="single")

    def _write_batch_diagnostics(
        self,
        *,
        candidates_count: int,
        raw_payloads_count: int,
        kept_count: int,
        selected_count: int,
        reasons: Counter[str],
        family_pre: Counter[str],
        family_post: Counter[str],
        family_selected: Counter[str],
        samples: list[tuple[str, str]],
        allocator_stats: dict[str, Any] | None,
        coarse_kept_count: int = 0,
        family_drop_counts: Counter[tuple[str, str]] | None = None,
        batch_guard: Counter[str] | None = None,
        selected_payloads: list[dict] | None = None,
    ) -> None:
        path = self._path(self.cfg.batch_diagnostics_filename)
        rows: list[dict[str, Any]] = []
        base = {
            "utc_iso": _utc(),
            "pipeline_version": self.cfg.pipeline_version,
            "preset": self.cfg.preset,
            "target_simulate_batch": int(self.cfg.target_simulate_batch),
            "min_simulate_batch": int(self.cfg.min_simulate_batch),
            "candidates": candidates_count,
            "raw_payloads": raw_payloads_count,
            "prescreen_coarse_kept": int(coarse_kept_count),
            "prescreen_kept": kept_count,
            "selected": selected_count,
            "novelty_strictness": self.cfg.novelty_strictness,
            "prescreen_similarity": float(self.cfg.prescreen_max_history_similarity),
            "intrabatch_similarity": float(self.cfg.prescreen_intrabatch_similarity),
        }
        if reasons:
            for reason, count in reasons.most_common(30):
                rows.append({**base, "kind": "drop_reason", "name": reason, "count": count, "sample": ""})
        else:
            rows.append({**base, "kind": "drop_reason", "name": "none", "count": 0, "sample": ""})
        for kind, counter in (("family_pre", family_pre), ("family_post", family_post), ("family_selected", family_selected)):
            for name, count in counter.most_common(20):
                rows.append({**base, "kind": kind, "name": name, "count": count, "sample": ""})
        for (family, reason), count in (family_drop_counts or Counter()).most_common(40):
            rows.append({**base, "kind": "family_drop", "name": f"{family}:{reason}", "count": count, "sample": ""})
        for reason, sample in samples[:20]:
            rows.append({**base, "kind": "drop_sample", "name": reason, "count": 1, "sample": sample})
        for name, value in (allocator_stats or {}).items():
            rows.append({**base, "kind": "allocator", "name": str(name), "count": value, "sample": ""})
        for reason, value in (batch_guard or Counter()).items():
            rows.append({**base, "kind": "batch_guard", "name": str(reason), "count": value, "sample": ""})
        behavior_diag = PreSimulationScreener._behavior_diagnostics(selected_payloads or [])
        for name, value in behavior_diag.items():
            rows.append({**base, "kind": "behavior", "name": str(name), "count": value, "sample": ""})
        if hasattr(self, "_history_pools"):
            risk_top = Counter(
                "|".join(sorted(toks)[:6])
                for toks in getattr(self._history_pools, "self_corr_risk", [])
                if toks
            ).most_common(5)
            rows.append(
                {
                    **base,
                    "kind": "behavior",
                    "name": "self_corr_risk_family_top",
                    "count": len(getattr(self._history_pools, "self_corr_risk", [])),
                    "sample": "; ".join(f"{name}:{count}" for name, count in risk_top)[:500],
                }
            )
        fields = [
            "utc_iso", "pipeline_version", "preset", "target_simulate_batch", "min_simulate_batch",
            "candidates", "raw_payloads", "prescreen_coarse_kept", "prescreen_kept", "selected",
            "novelty_strictness", "prescreen_similarity", "intrabatch_similarity",
            "kind", "name", "count", "sample",
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            try:
                with path.open("r", newline="", encoding="utf-8-sig") as f:
                    existing_rows = list(csv.DictReader(f))
                with path.open("r", newline="", encoding="utf-8-sig") as f:
                    existing_fields = next(csv.reader(f), [])
                if existing_fields and existing_fields != fields:
                    repaired_rows: list[dict[str, Any]] = []
                    for row in existing_rows:
                        repaired = {k: row.get(k, "") for k in fields}
                        if not repaired.get("prescreen_coarse_kept"):
                            repaired["prescreen_coarse_kept"] = "0"
                        repaired_rows.append(repaired)
                    with path.open("w", newline="", encoding="utf-8-sig") as f:
                        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                        w.writeheader()
                        w.writerows(repaired_rows)
                    print(f"[batch/diagnostics] repaired header -> {path.name}")
            except Exception as e:
                print(f"[batch/diagnostics] header repair skipped: {e}")
        new_file = not path.is_file()
        with path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerows(rows)
        print(
            f"[batch/diagnostics] candidates={candidates_count} raw_payloads={raw_payloads_count} "
            f"coarse_kept={coarse_kept_count} fine_kept={kept_count} selected={selected_count} -> {path.name}"
        )

    def run_full(self) -> Any:
        """Full pipeline: generate → expand payloads → prescreen → simulate *all* kept (unless ``run_payload_cap``).

        Batch policy (v41 defaults):
        - Expand **all** candidate setting-variants up to ``max_payload_expand_cap``.
        - Two-stage prescreen: coarse (quality + history/novelty) → fine (greedy diverse pick to ``min_simulate_batch``).
        - Simulate every fine-selected payload when ``run_payload_cap is None``.
        """
        minimum_floor = 60 if str(self.cfg.preset).lower() == "diverse_exploration" else 120
        self.cfg.min_simulate_batch = max(minimum_floor, int(self.cfg.min_simulate_batch))
        if int(self.cfg.target_simulate_batch) > 0:
            self.cfg.min_simulate_batch = min(
                int(self.cfg.min_simulate_batch),
                int(self.cfg.target_simulate_batch),
            )
        print(
            f"[batch] policy floor={int(self.cfg.min_simulate_batch)} "
            f"target_pass_rate>={float(self.cfg.target_platform_pass_rate):.0%} "
            f"near_pass_share>={float(self.cfg.min_near_pass_batch_share):.0%} "
            f"arch_explore_cap<={float(self.cfg.max_arch_explore_batch_share):.0%} "
            f"quality_topup=near_pass+pass_fundamental+template_only "
            f"diversity_mode={self.cfg.diversity_mode} "
            f"soft_cap={int(self.cfg.target_simulate_batch) or 'none'} "
            f"safety_cap={int(self.cfg.max_simulate_batch_per_run) or 'none'}"
        )
        self.ensure_authenticated(force=False)
        fb = self._feedback_path()
        cp = self._path(f"{self.cfg.output_prefix}_checkpoint.csv")
        print(
            f"[paths] failure_reasons+metrics -> {fb.resolve()} "
            f"| checkpoint -> {cp.resolve()} | script={_PIPELINE_SCRIPT} "
            f"| version={self.cfg.pipeline_version} "
            f"(v41 flow + v42 fine-fill; do not use v45)"
        )
        if cp.is_file():
            try:
                with cp.open("r", newline="", encoding="utf-8-sig") as f:
                    header = next(csv.reader(f), [])
                if header and "failure_reasons" not in header:
                    print(
                        "[warn] existing checkpoint has no failure_reasons column (older run). "
                        "Re-run v40 simulate to refresh; platform fail text is in alpha_submission_feedback.csv."
                    )
            except Exception:
                pass
        # v50.3: 清理上次运行遗留的 poll_only:not_checked 条目
        self._cleanup_stale_poll_only()
        # Prioritize previously pending self-correlation checks before launching new batch.
        if not self.cfg.batch_diagnostics_only and not self.cfg.recheck_skip_prebatch:
            try:
                print(
                    f"[recheck] pre-batch: max_items={int(self.cfg.recheck_prebatch_max_items)} "
                    f"per_alpha_timeout={float(self.cfg.recheck_prebatch_quick_timeout_seconds):.0f}s "
                    f"wall_budget={float(self.cfg.recheck_prebatch_wall_budget_seconds):.0f}s "
                    f"(then generate/sim; deep wait: --mode recheck)"
                )
                recheck_df = self.run_recheck_queue(
                    do_auth=False,
                    max_items=self.cfg.recheck_prebatch_max_items,
                    quick_timeout_seconds=self.cfg.recheck_prebatch_quick_timeout_seconds,
                    wall_budget_seconds=self.cfg.recheck_prebatch_wall_budget_seconds,
                )
                if isinstance(recheck_df, pd.DataFrame):
                    print(f"[recheck] pre-batch updated={len(recheck_df)}")
            except Exception as e:
                print(f"[recheck] pre-batch skipped: {e}")
        elif self.cfg.recheck_skip_prebatch:
            print("[recheck] pre-batch skipped (--no-prebatch-recheck)")
        else:
            print("[recheck] pre-batch skipped for --batch-diagnostics")
        candidates, _ = self.generate_candidates()
        explicit_cap = self.cfg.run_payload_cap is not None
        simulate_cap = max(1, int(self.cfg.run_payload_cap)) if explicit_cap else None
        # IMPORTANT: do not pass ``max(300, len(candidates)*3)`` into ``payloads_for`` — that caps payload
        # construction and stops after the first N variants, so later candidates never expand (classic "87 rows" bug).
        build_cap = min(
            max(
                int(self.cfg.min_simulate_batch) * 12,
                max(1, len(candidates)) * 48,
                100_000,
            ),
            int(self.cfg.max_payload_expand_cap),
        )
        payloads = ProfileSelector(self.cfg).payloads_for(candidates, max_payloads=build_cap)
        payloads = self._order_payloads_for_prescreen(payloads)
        raw_payloads_count = len(payloads)
        print(
            f"[batch] candidates={len(candidates)} payload_expand_cap={build_cap} "
            f"raw_payloads={raw_payloads_count} min_simulate_batch={int(self.cfg.min_simulate_batch)}"
        )
        if not explicit_cap and len(payloads) < int(self.cfg.min_simulate_batch):
            print(
                f"[batch] WARN raw_payloads={len(payloads)} < min_simulate_batch={int(self.cfg.min_simulate_batch)} "
                f"(candidate pool/settings variants too small after generation/dedup — raise budget or widen templates)"
            )
        family_pre = Counter(str((p.get("meta") or {}).get("family") or "") for p in payloads)
        print(f"[batch] pre-prescreen family_top={dict(family_pre.most_common(8))}")
        reasons: Counter[str] = Counter()
        samples: list[tuple[str, str]] = []
        kept_count = len(payloads)
        family_post: Counter[str] = Counter()
        alloc_stats: dict[str, Any] | None = None
        if self.cfg.prescreen_enabled and payloads:
            kept, reasons, samples = self._prescreen_until_target(payloads)
            dropped = len(payloads) - len(kept)
            kept_count = len(kept)
            coarse_n = int(getattr(self, "_last_prescreen_coarse_count", 0) or 0)
            if self.cfg.prescreen_two_stage and coarse_n:
                print(
                    f"[prescreen] in={len(payloads)} coarse_kept={coarse_n} fine_selected={len(kept)} "
                    f"dropped={dropped} reasons={dict(reasons.most_common(12))}"
                )
            else:
                print(f"[prescreen] in={len(payloads)} kept={len(kept)} dropped={dropped} reasons={dict(reasons)}")
            max_drop_samples = 2
            for i, (reason, sample) in enumerate(samples):
                if i >= max_drop_samples:
                    rest = len(samples) - max_drop_samples
                    if rest > 0:
                        print(f"[prescreen] … {rest} more dropped samples omitted (see reasons above)")
                    break
                print(f"[prescreen] dropped[{reason}] {sample}")
            family_post = Counter(str((p.get("meta") or {}).get("family") or "") for p in kept)
            print(f"[batch] post-prescreen family_top={dict(family_post.most_common(8))}")
            self._sort_payloads_sim_priority(kept)
            if not explicit_cap and len(kept) < int(self.cfg.min_simulate_batch):
                print(
                    f"[batch] WARN after prescreen kept={len(kept)} < target={int(self.cfg.min_simulate_batch)} "
                    f"(similarity / already_simulated / shape quotas still dominate)"
                )
            select_cap = simulate_cap if explicit_cap else len(kept)
            payloads, alloc_stats = self._allocate_payload_budget(kept, select_cap)
            print(f"[allocator] mode={'cap' if explicit_cap else 'all_eligible'} cap={select_cap} selected={len(payloads)} stats={dict(Counter(alloc_stats).most_common(10)) if isinstance(alloc_stats, dict) else alloc_stats}")
        else:
            select_cap = simulate_cap if explicit_cap else len(payloads)
            self._sort_payloads_sim_priority(payloads)
            payloads, alloc_stats = self._allocate_payload_budget(payloads, select_cap)
            family_post = Counter(str((p.get("meta") or {}).get("family") or "") for p in payloads)
            print(f"[allocator] mode={'cap' if explicit_cap else 'all_eligible'} cap={select_cap} selected={len(payloads)} stats={dict(Counter(alloc_stats).most_common(10)) if isinstance(alloc_stats, dict) else alloc_stats}")
        family_selected = Counter(str((p.get("meta") or {}).get("family") or "") for p in payloads)
        print(f"[batch] selected family_top={dict(family_selected.most_common(8))}")
        guard_ok, guard_violations = self._batch_guard_allows_simulation(payloads, family_pre)
        if payloads:
            near_n = sum(1 for p in payloads if str((p.get("meta") or {}).get("family") or "").lower().startswith("near_pass"))
            arch_n = sum(1 for p in payloads if str((p.get("meta") or {}).get("family") or "").lower().startswith("arch_"))
            near_share = near_n / len(payloads)
            arch_share = arch_n / len(payloads)
            min_near = float(self.cfg.min_near_pass_batch_share)
            max_arch = float(self.cfg.max_arch_explore_batch_share)
            if near_share + 1e-9 < min_near * 0.85:
                print(
                    f"[batch] WARN near_pass_share={near_share:.0%} < target {min_near:.0%} "
                    f"(arch={arch_share:.0%}) — check prescreen pool / near_pass seeds"
                )
            elif arch_share > max_arch + 0.05:
                print(f"[batch] WARN arch_share={arch_share:.0%} > cap {max_arch:.0%}")
        diagnostic_stats = dict(getattr(self, "_last_prescreen_diagnostic_stats", {}) or {})
        if isinstance(alloc_stats, dict):
            diagnostic_stats.update(alloc_stats)
        guard_warnings = list(getattr(self, "_last_batch_guard_warnings", []) or [])
        batch_guard_counts = Counter(guard_violations + guard_warnings)
        self._write_batch_diagnostics(
            candidates_count=len(candidates),
            raw_payloads_count=raw_payloads_count,
            kept_count=kept_count,
            selected_count=len(payloads),
            coarse_kept_count=int(getattr(self, "_last_prescreen_coarse_count", 0) or 0),
            reasons=reasons,
            family_pre=family_pre,
            family_post=family_post,
            family_selected=family_selected,
            samples=samples,
            allocator_stats=diagnostic_stats,
            family_drop_counts=Counter(getattr(self, "_last_prescreen_family_drop_counts", Counter())),
            batch_guard=batch_guard_counts,
            selected_payloads=payloads,
        )
        if self.cfg.batch_diagnostics_only:
            rows = []
            for i, p in enumerate(payloads, start=1):
                rows.append({
                    "index": i,
                    "expression": p.get("regular", ""),
                    "profile": (p.get("meta") or {}).get("profile", ""),
                    "family": (p.get("meta") or {}).get("family", ""),
                    "source": (p.get("meta") or {}).get("source", ""),
                })
            df = pd.DataFrame(rows)
            out = self._path(f"{self.cfg.output_prefix}_batch_diagnostic_selection.csv")
            df.to_csv(out, index=False, encoding="utf-8-sig")
            print(f"[batch/diagnostics] dry selection -> {out.name} rows={len(df)}; simulate skipped")
            return df
        if not guard_ok:
            guard_reasons = guard_violations + [w for w in guard_warnings if w not in guard_violations]
            print(f"[batch] GUARD skip simulate violations={guard_violations} warnings={guard_warnings}")
            df = self._batch_guard_dataframe(payloads, guard_reasons)
            self._persist_guard_results(df)
            return df
        if self.cfg.sync_platform_tried_before_simulate:
            print(
                f"[batch] platform_tried synced: {len(self._tried_expressions)} expressions, "
                f"{len(self._platform_alpha_ids)} alpha_ids — prescreen skips re-simulating these"
            )
        # Cool down after heavy data-field / 429 bursts before opening aiohttp connection pool.
        time.sleep(max(0.0, float(getattr(self.cfg, "pre_simulate_cooldown_seconds", 3.0))))
        result_df = self.run_batch_simulation(payloads)
        try:
            self.write_feedback_learning_summary()
        except Exception as e:
            print(f"[feedback] learning summary skipped: {e}")
        if self._near_pass_records:
            print(
                f"[hint] {len(self._near_pass_records)} near-pass/hopeful seeds loaded — "
                "near-pass first; generated history is near-clone gated. For SELF_CORRELATION pending: "
                "py -3 auto_alpha_pipeline_rebuilt_v50.py --mode recheck"
            )
        # Recheck again right after simulation to reduce pending backlog.
        if self.cfg.recheck_skip_postbatch:
            print("[recheck] post-batch skipped (recheck_skip_postbatch)")
        else:
            try:
                post_recheck_df = self.run_recheck_queue(
                    do_auth=False,
                    max_items=self.cfg.recheck_postbatch_max_items,
                    quick_timeout_seconds=float(
                        getattr(self.cfg, "recheck_postbatch_quick_timeout_seconds", 300.0) or 300.0
                    ),
                    wall_budget_seconds=float(
                        getattr(self.cfg, "recheck_postbatch_wall_budget_seconds", 900.0) or 900.0
                    ),
                )
                if isinstance(post_recheck_df, pd.DataFrame):
                    print(f"[recheck] post-batch updated={len(post_recheck_df)}")
            except Exception as e:
                print(f"[recheck] post-batch skipped: {e}")
        return result_df

    def run_batch_simulation(self, payloads: list[dict], *, force_sequential: bool = False) -> Any:
        self._simulate_snapshot_exprs = set(self._tried_expressions)
        self._simulate_snapshot_alpha_ids = set(self._platform_alpha_ids)
        workers = int(getattr(self.cfg, "max_concurrent_simulations", 0) or 0)
        if workers > 0 and not force_sequential:
            import asyncio

            from alpha_mining.simulate.async_batch import run_async_simulation_batch

            try:
                return asyncio.run(run_async_simulation_batch(self, payloads))
            except Exception as e:
                if _is_transient_connect_error(e):
                    print(
                        f"[simulate/async] connection failed ({e}); "
                        f"falling back to sequential simulate (use --preflight to test aiohttp first)"
                    )
                    return self.run_batch_simulation(payloads, force_sequential=True)
                raise
        from alpha_mining.simulate.async_batch import deduplicate_simulation_payloads
        rows = []
        unique_payloads = deduplicate_simulation_payloads(payloads)
        run_cap = len(unique_payloads) if self.cfg.run_payload_cap is None else max(1, min(int(self.cfg.run_payload_cap), len(unique_payloads)))
        run_payloads = unique_payloads[:run_cap]
        print(f"[simulate] START payloads={len(run_payloads)} cap={run_cap} budget={self.cfg.budget} (sequential)")
        for idx, payload in enumerate(run_payloads, start=1):
            expr = payload["regular"]
            profile = payload.get("meta", {}).get("profile", "?")
            print(f"[simulate {idx}/{len(run_payloads)}] ({profile}) {expr[:120]}")
            # v50.1: pre-simulation platform library dedup — 跳过与已有 alpha 高度重复的
            lib_sim = self._platform_library_similarity(expr, early_exit_at=0.85)
            if lib_sim < 0.85 and self._history_pools and len(self._history_pools.passed) > 0:
                lib_sim = self._history_pools.max_similarity(expr, "passed", early_exit_at=0.85)
            if lib_sim >= 0.85:
                print(f"[simulate {idx}] 跳过 — 与平台现有 alpha 高度相似 (sim={lib_sim:.2f})")
                row = _simulate_result_row(
                    index=idx, alpha_id=None, status="skipped:platform_similarity",
                    queue_status="not_queued:too_similar_to_platform", check_passed=False,
                    check_note=f"pre_sim_platform_similarity={lib_sim:.2f}",
                    expression=expr, profile=profile, merged={}, sim_json={},
                )
                rows.append(row)
                continue
            alpha_id, sim_json, status = self.submit_simulation(payload)
            check_passed: bool | None = None
            check_note = ""
            check_json: dict | None = None
            merged = sim_json
            if alpha_id:
                detail = self.fetch_alpha_detail(alpha_id)
                merged = _merge_json_dicts(sim_json, detail)
                sh_pre = _to_float(_metric_get(merged, "sharpe", "Sharpe"))
                fi_pre = _to_float(_metric_get(merged, "fitness", "Fitness"))
                check_wait: float | None = None
                check_wait = self._initial_simulate_check_wait(merged)
                if check_wait is not None and check_wait <= 0.0:
                    metrics = {
                        "sharpe": _to_float(_metric_get(merged, "sharpe", "Sharpe")),
                        "fitness": _to_float(_metric_get(merged, "fitness", "Fitness")),
                        "turnover": _to_float(_metric_get(merged, "turnover", "Turnover")),
                        "returns": _to_float(_metric_get(merged, "returns", "Returns")),
                        "drawdown": _to_float(_metric_get(merged, "drawdown", "Drawdown")),
                        "margin": _to_float(_metric_get(merged, "margin", "Margin")),
                    }
                    _gate_ok, gate_note = self._metric_gate(metrics)
                    check_passed, check_json, check_note = None, None, (
                        "skip_check:missing_core_metrics"
                        if gate_note == "missing_core_metrics"
                        else f"skip_check:{gate_note}"
                    )
                    print(f"[simulate {idx}] 跳过 check — {check_note} (sh={sh_pre} fi={fi_pre})")
                else:
                    check_passed, check_json, check_note = self.check_alpha(alpha_id, max_wait_seconds=check_wait)
                    merged = _merge_json_dicts(merged, check_json)

            # If signal quality is clearly poor, try one mirrored expression and keep better one.
            if self.cfg.enable_auto_invert_retry and status == "ok":
                sh = _to_float(_metric_get(merged, "sharpe"))
                fi = _to_float(_metric_get(merged, "fitness"))
                # Only mirror clearly wrong-sign signals; mild underperformance should not be inverted.
                should_retry = check_passed is not True and sh is not None and float(sh) < -0.15
                if should_retry:
                    inv_payload = {
                        "type": payload["type"],
                        "regular": self._invert_expression(expr),
                        "settings": dict(payload.get("settings") or {}),
                        "meta": {**dict(payload.get("meta") or {}), "profile": f"{profile}:invert_retry"},
                    }
                    inv_alpha_id, inv_sim_json, inv_status = self.submit_simulation(inv_payload)
                    inv_check_passed: bool | None = None
                    inv_check_note = ""
                    inv_check_json: dict | None = None
                    inv_merged = inv_sim_json
                    if inv_alpha_id:
                        inv_detail = self.fetch_alpha_detail(inv_alpha_id)
                        inv_merged = _merge_json_dicts(inv_sim_json, inv_detail)
                        inv_wait = self._initial_simulate_check_wait(inv_merged)
                        if inv_wait is not None and inv_wait <= 0.0:
                            inv_metrics = {
                                "sharpe": _to_float(_metric_get(inv_merged, "sharpe", "Sharpe")),
                                "fitness": _to_float(_metric_get(inv_merged, "fitness", "Fitness")),
                                "turnover": _to_float(_metric_get(inv_merged, "turnover", "Turnover")),
                                "returns": _to_float(_metric_get(inv_merged, "returns", "Returns")),
                                "drawdown": _to_float(_metric_get(inv_merged, "drawdown", "Drawdown")),
                                "margin": _to_float(_metric_get(inv_merged, "margin", "Margin")),
                            }
                            _inv_gate_ok, inv_gate_note = self._metric_gate(inv_metrics)
                            inv_check_passed, inv_check_json, inv_check_note = None, None, (
                                "skip_check:missing_core_metrics"
                                if inv_gate_note == "missing_core_metrics"
                                else f"skip_check:{inv_gate_note}"
                            )
                        else:
                            inv_check_passed, inv_check_json, inv_check_note = self.check_alpha(
                                inv_alpha_id,
                                max_wait_seconds=inv_wait,
                            )
                            inv_merged = _merge_json_dicts(inv_merged, inv_check_json)
                    base_score = (_to_float(_metric_get(merged, "sharpe")) or -999) + (_to_float(_metric_get(merged, "fitness")) or -999)
                    inv_score = (_to_float(_metric_get(inv_merged, "sharpe")) or -999) + (_to_float(_metric_get(inv_merged, "fitness")) or -999)
                    if inv_check_passed is True or inv_score > base_score:
                        payload = inv_payload
                        expr = inv_payload["regular"]
                        profile = inv_payload.get("meta", {}).get("profile", profile)
                        alpha_id, sim_json, status = inv_alpha_id, inv_sim_json, inv_status
                        check_passed, check_json, check_note = inv_check_passed, inv_check_json, inv_check_note
                        merged = inv_merged
                        print(f"[simulate {idx}] switched to inverted expression due to weak initial result")
            queue_status, _ = self.queue_decision(payload, alpha_id, merged, check_passed, check_note)
            self._append_feedback(
                payload,
                alpha_id,
                sim_json,
                status,
                check_passed,
                check_note,
                queue_status,
                check_json=check_json,
                merged_json=merged,
            )
            row = _simulate_result_row(
                index=idx,
                alpha_id=alpha_id,
                status=status,
                queue_status=queue_status,
                check_passed=check_passed,
                check_note=check_note,
                expression=expr,
                profile=profile,
                merged=merged,
                sim_json=sim_json,
            )
            rows.append(row)
            if idx % self.cfg.save_every_n == 0:
                pd.DataFrame(rows).to_csv(self._path(f"{self.cfg.output_prefix}_checkpoint.csv"), index=False, encoding="utf-8-sig")
            if status == "network_dns_error_batch_paused":
                print("[simulate] DNS unstable; stopping current batch early")
                break
        df = pd.DataFrame(rows)
        out = self._path(f"{self.cfg.output_prefix}_results.csv")
        df.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[simulate] DONE rows={len(df)} saved={out.name}")
        return df

    def run_submit_queue(self) -> Any:
        # The compatibility monolith cannot prove Dynamic Gate Registry
        # freshness at execution time. Live submission is therefore disabled;
        # use the vNext guarded queue. Dry-run remains entirely local.
        if not self.cfg.dry_run_submit:
            print("[submit] BLOCKED: legacy live submit disabled; use python -m alpha_mining submit execute")
            return pd.DataFrame([{"status": "blocked", "note": "legacy_live_submit_disabled"}])
        rows = self.queue.load()
        submitted_ids = self.queue.submitted_ids()
        valid = []
        for row in rows:
            alpha_id = str(row.get("alpha_id") or "").strip()
            if not alpha_id or alpha_id in submitted_ids:
                continue
            st = str(row.get("status") or "ready").lower()
            if st != "ready":
                continue
            checks = row.get("checks") if isinstance(row.get("checks"), list) else []
            statuses = {
                str(c.get("name") or "").upper(): str(c.get("result") or c.get("status") or "UNKNOWN").upper()
                for c in checks if isinstance(c, dict)
            }
            if not statuses or any(status != "PASS" for status in statuses.values()):
                continue
            if statuses.get("SELF_CORRELATION", "MISSING") != "PASS" or row.get("check_passed") is not True:
                continue
            similarity = _to_float(row.get("similarity_to_winners")) or 0.0
            if similarity >= self.cfg.max_queue_similarity:
                continue
            valid.append(row)
        if self.cfg.queue_prefer_low_similarity:
            valid.sort(
                key=lambda r: (
                    -(_to_float(((r.get("metrics") or {}).get("sharpe")) or -999)),
                    -(_to_float(((r.get("metrics") or {}).get("fitness")) or -999)),
                    (_to_float(r.get("similarity_to_winners")) or 0.0),
                )
            )
        print(f"[submit] queue={len(rows)} valid={len(valid)} dry_run={self.cfg.dry_run_submit}")
        results = []
        failures = 0
        for idx, row in enumerate(valid[: self.cfg.max_submit], start=1):
            alpha_id = str(row.get("alpha_id"))
            # IS ladder Sharpe pre-check (optional, adds N extra platform simulation calls)
            if getattr(self.cfg, "ladder_check_enabled", False) and not self.cfg.dry_run_submit:
                try:
                    ladder_ok, ladder_note = self.run_yearly_ladder_check(row)
                    if not ladder_ok:
                        print(f"[ladder-check {idx}] {alpha_id} rejected: {ladder_note}")
                        # Record in queue as needs_regen so RepairEngine can pick it up
                        self._append_hopeful_recheck_snapshot(
                            row, None, False, ladder_note, "not_queued:ladder_check_fail"
                        )
                        continue
                except Exception as _lc_exc:
                    print(f"[ladder-check] skipped: {type(_lc_exc).__name__}: {_lc_exc}")
            # Attach description before submitting (best-effort, never blocks submission)
            try:
                desc = self._get_description_for(
                    alpha_id,
                    str(row.get("expression") or ""),
                    family=str(row.get("family") or ""),
                    source=str(row.get("source") or ""),
                )
                if desc:
                    self.patch_alpha_description(alpha_id, desc)
            except Exception as _desc_exc:
                print(f"[description-patch] skipped: {type(_desc_exc).__name__}")
            ok, note = self.submit_alpha(alpha_id)
            status = "submitted" if ok and note != "dry_run" else note
            entry = {"utc_iso": _utc(), "alpha_id": alpha_id, "status": status, "note": note, "expression": row.get("expression")}
            self.queue.log_submission(entry)
            results.append(entry)
            print(f"[submit {idx}/{min(len(valid), self.cfg.max_submit)}] {alpha_id} {note}")
            if ok:
                failures = 0
            else:
                failures += 1
                if failures >= self.cfg.max_consecutive_submit_failures:
                    print("[submit] too many consecutive failures; stop")
                    break
            if idx % max(1, self.cfg.submit_batch_size) == 0:
                time.sleep(120 if not self.cfg.dry_run_submit else 1)
            else:
                time.sleep(30 if not self.cfg.dry_run_submit else 0.2)
        return pd.DataFrame(results)

    def _append_hopeful_recheck_snapshot(
        self,
        row: dict,
        detail: dict | None,
        check_passed: bool | None,
        note: str,
        queue_status: str,
    ) -> None:
        """Persist recheck progress to the hopeful JSONL so load() can see updated checks/status.

        v50.1: 马拉松模式 — 对高 Sharpe/Fitness 的 alpha，跟踪重试次数，
        最多重试 72 小时（每 2 小时一次），不因短期 SELF_CORRELATION 超时而放弃。
        """
        snap = dict(row)
        snap["rechecked_at"] = _utc()
        snap["last_recheck_note"] = str(note or "")
        snap["last_queue_status"] = str(queue_status or "")
        # v50.1: 追踪重试次数
        recheck_count = int(row.get("recheck_count") or 0) + 1
        snap["recheck_count"] = recheck_count
        first_seen = str(row.get("first_seen_at") or row.get("rechecked_at") or _utc())
        snap["first_seen_at"] = first_seen

        if isinstance(detail, dict):
            snap["checks"] = _extract_checks(detail)
            prev = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            newm = dict(prev)
            for key, getter in (
                ("sharpe", ("sharpe", "Sharpe")),
                ("fitness", ("fitness", "Fitness")),
                ("turnover", ("turnover", "Turnover")),
                ("returns", ("returns", "Returns")),
                ("drawdown", ("drawdown", "Drawdown")),
                ("margin", ("margin", "Margin")),
            ):
                v = _to_float(_metric_get(detail, *getter))
                if v is not None:
                    newm[key] = v
            snap["metrics"] = newm
        snap["check_passed"] = check_passed
        snap["check_note"] = str(note or "")
        detail_for_checks = detail if isinstance(detail, dict) else None
        self_corr_pending = (
            isinstance(note, str)
            and note.startswith("metric_pass:self_correlation_pending")
        ) or (
            _non_self_checks_all_pass(detail_for_checks)
            and _self_correlation_pending(detail_for_checks)
        )
        # v50.1: 马拉松判断 — 高Sharpe alpha 值得更长的等待
        metrics = snap.get("metrics") if isinstance(snap.get("metrics"), dict) else {}
        sh = _to_float(metrics.get("sharpe")) or 0.0
        fi = _to_float(metrics.get("fitness")) or 0.0
        marathon_worthy = sh >= 1.5 and fi >= 1.0
        max_marathon_retries = 36  # 36 * 2h = 72h
        if check_passed is True:
            snap["status"] = "ready"
        elif self_corr_pending:
            if marathon_worthy and recheck_count < max_marathon_retries:
                snap["status"] = "needs_recheck"
                if recheck_count == 1:
                    print(f"[recheck] marathon start: alpha_id={snap.get('alpha_id','?')[:12]} "
                          f"sharpe={sh:.2f} fitness={fi:.2f} (up to 72h retry)")
            elif not marathon_worthy:
                snap["status"] = "needs_recheck"  # 再给一次机会
            else:
                snap["status"] = "recheck_closed"  # 马拉松耗尽，放弃
                print(f"[recheck] marathon exhausted: alpha_id={snap.get('alpha_id','?')[:12]} "
                      f"after {recheck_count} retries")
        elif isinstance(note, str) and note.startswith("check_timeout"):
            snap["status"] = "needs_recheck"
        elif _hard_fail_checks(detail_for_checks):
            snap["status"] = "check_failed_stale"
        else:
            snap["status"] = "recheck_closed"
        self.queue.append(snap)

    @staticmethod
    def _recheck_priority_key(row: dict) -> tuple[int, float, float, float]:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        sharpe = _to_float(metrics.get("sharpe")) or -999.0
        fitness = _to_float(metrics.get("fitness")) or -999.0
        similarity = _to_float(row.get("similarity_to_winners")) or 0.0
        fam = str((row.get("meta") or {}).get("family") or "").lower() if isinstance(row.get("meta"), dict) else ""
        metric_pass = sharpe >= 1.24 and fitness >= 1.0
        near_pass = fam.startswith("near_pass")
        tier = 0 if (metric_pass and near_pass) else (1 if metric_pass else 2)
        return (tier, -sharpe, -fitness, similarity)

    @staticmethod
    def _feedback_cleanup_priority_key(row: dict) -> tuple[int, float, float]:
        sharpe = _to_float(row.get("Sharpe")) or -999.0
        fitness = _to_float(row.get("Fitness")) or -999.0
        metric_pass = str(row.get("metric_gate_pass") or "").lower() == "true"
        non_self_pass = str(row.get("platform_non_self_pass") or "").lower() == "true"
        tier = 0 if metric_pass else (1 if non_self_pass else 2)
        return (tier, -sharpe, -fitness)

    def _recheck_cooldown_remaining_seconds(
        self,
        row: dict,
        *,
        now: datetime | None = None,
    ) -> float:
        cooldown = float(getattr(self.cfg, "queue_recheck_seconds", 0) or 0.0)
        if cooldown <= 0:
            return 0.0
        anchor = _parse_utc_like(row.get("rechecked_at") or row.get("queued_at"))
        if anchor is None:
            return 0.0
        current = now or datetime.now(timezone.utc)
        age = max(0.0, (current - anchor).total_seconds())
        return max(0.0, cooldown - age)

    def run_recheck_queue(
        self,
        *,
        do_auth: bool = True,
        max_items: int | None = None,
        quick_timeout_seconds: float | None = None,
        wall_budget_seconds: float | None = None,
        bypass_cooldown: bool = False,
    ) -> Any:
        if do_auth:
            self.ensure_authenticated(force=False)
        rows = self.queue.load()
        results = []
        pending_rows: list[dict] = []
        cooling_rows = 0
        now = datetime.now(timezone.utc)
        for row in rows:
            if row.get("status") != "needs_recheck":
                continue
            if not bypass_cooldown and self._recheck_cooldown_remaining_seconds(row, now=now) > 0:
                cooling_rows += 1
                continue
            pending_rows.append(row)
        pending_rows.sort(key=self._recheck_priority_key)
        limit = len(pending_rows) if max_items is None else max(0, int(max_items))
        processing = pending_rows if max_items is None else pending_rows[:limit]
        print(f"[recheck] pending={len(pending_rows)} cooling={cooling_rows} processing={len(processing)}")
        t_wall0 = time.time()
        for idx, row in enumerate(processing, start=1):
            if wall_budget_seconds is not None and (time.time() - t_wall0) >= float(wall_budget_seconds):
                print(
                    f"[recheck] wall budget {float(wall_budget_seconds):.0f}s reached "
                    f"after {idx - 1}/{len(processing)}; stopping early (rest stay pending for post-batch / --mode recheck)"
                )
                break
            if row.get("status") != "needs_recheck":
                continue
            alpha_id = str(row.get("alpha_id") or "").strip()
            if not alpha_id:
                continue
            check_passed, detail, note = self.check_alpha(
                alpha_id,
                max_wait_seconds=(
                    float(quick_timeout_seconds)
                    if quick_timeout_seconds is not None
                    else float(self.cfg.max_check_poll_seconds)
                ),
                heartbeat_label=f"{idx}/{len(processing)}",
            )
            payload = {"regular": row.get("expression"), "settings": row.get("settings") or {}, "meta": row.get("meta") or {}}
            merged = detail if isinstance(detail, dict) else {}
            if not _extract_checks(merged) and isinstance(row.get("checks"), list):
                merged = _merge_json_dicts(merged or {}, {"is": {"checks": row["checks"]}})
            mx = row.get("metrics")
            if isinstance(mx, dict) and isinstance(merged, dict):
                iso = dict(merged.get("is") or {}) if isinstance(merged.get("is"), dict) else {}
                for k, alt in (("sharpe", "Sharpe"), ("fitness", "Fitness"), ("turnover", "Turnover"), ("returns", "Returns"), ("drawdown", "Drawdown"), ("margin", "Margin")):
                    if mx.get(k) is not None and _metric_get(merged, k, alt) is None:
                        iso[k] = mx[k]
                if iso:
                    merged = _merge_json_dicts(merged, {"is": iso})
            queue_status, _qentry = self.queue_decision(payload, alpha_id, merged, check_passed, note)
            sh = _metric_get(merged, "sharpe", "Sharpe")
            fi = _metric_get(merged, "fitness", "Fitness")
            print(
                f"[recheck {idx}/{len(processing)}] {alpha_id[:12]} "
                f"queue={queue_status} sh={sh} fi={fi} note={(note or '')[:56]}"
            )
            results.append({"alpha_id": alpha_id, "check_passed": check_passed, "note": note, "queue_status": queue_status})
            # Always persist: previously we only wrote when qentry was None, so promoted "ready" never updated JSONL.
            self._append_hopeful_recheck_snapshot(row, detail, check_passed, note, queue_status)
        return pd.DataFrame(results)

    def run_continuous(self) -> None:
        while True:
            try:
                self.run_full()
                self.run_submit_queue()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"[continuous] error: {e}")
            time.sleep(6 * 3600)

    def run_preflight(self) -> int:
        """Verify sync + aiohttp can reach the API before a large simulate batch."""
        self.ensure_authenticated(force=False)
        print("[preflight] sync authentication OK")
        import asyncio

        from alpha_mining.simulate.async_batch import probe_async_connection

        asyncio.run(probe_async_connection(self.cfg))
        print("[preflight] aiohttp authentication OK")
        return 0

    def run(self) -> Any:
        mode = self.cfg.mode.lower()
        if mode == "preflight":
            return self.run_preflight()
        if mode == "generate":
            return self.run_generate()
        if mode in ("full", "simulate"):
            return self.run_full()
        if mode == "submit":
            return self.run_submit_queue()
        if mode == "recheck":
            if bool(getattr(self.cfg, "recheck_deep", False)):
                return self.run_recheck_queue(quick_timeout_seconds=None, bypass_cooldown=True)
            max_items = int(getattr(self.cfg, "recheck_standalone_max_items", 20) or 0)
            wall = float(getattr(self.cfg, "recheck_standalone_wall_budget_seconds", 1800.0) or 0.0)
            quick = float(getattr(self.cfg, "recheck_standalone_quick_timeout_seconds", 600.0) or 600.0)
            return self.run_recheck_queue(
                max_items=max_items if max_items > 0 else None,
                wall_budget_seconds=wall if wall > 0 else None,
                quick_timeout_seconds=quick,
            )
        if mode == "continuous":
            return self.run_continuous()
        raise ValueError(f"unknown mode: {self.cfg.mode}")


# ---------------------------- CLI ----------------------------

def _run_offline_smoke() -> int:
    """Fast local checks without calling the platform API."""
    errors: list[str] = []
    factory_cfg = PipelineConfig(username="smoke", password="smoke")
    cat = FieldCatalog.from_df(pd.DataFrame({"id": ["fnd6_test_ebit", "cap", "volume", "adv20", "close", "vwap"]}))
    validator = PreflightValidator(cat, min_ts_corr_window=factory_cfg.min_ts_corr_window)
    factory = ExpressionFactory(factory_cfg, cat, validator)

    ok, note = factory._quality_gate(
        "group_neutralize(ts_zscore(fnd6_test_ebit/cap, 126), subindustry)"
    )
    if not ok:
        errors.append(f"quality_gate expected pass got {note}")

    ok2, note2 = factory._quality_gate(
        "group_neutralize(ts_corr(rank(receivable/cap), rank(inventory/cap), 10), subindustry)"
    )
    if ok2:
        errors.append(f"quality_gate expected reject short ts_corr got pass ({note2})")

    screener = PreSimulationScreener(
        factory_cfg,
        tried_exact=set(),
        tried_payload_keys=set(),
        near_pass_expressions=set(),
        failed_cluster={},
        history_pools=HistorySimilarityPools(),
        top_field_lookup=lambda _e: None,
        tried_metrics={"bad_expr": {"sharpe": -0.5}},
    )
    kept, reasons, _ = screener.screen(
        [
            {"regular": "bad_expr", "settings": {}, "meta": {"family": "pass_pv"}},
            {
                "regular": "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),subindustry)",
                "settings": {},
                "meta": {"family": "pass_pv"},
            },
            {
                "regular": "group_neutralize(ts_corr(rank(close),rank(volume),63),sector)",
                "settings": {},
                "meta": {"family": "pass_pv"},
            },
        ]
    )
    if "negative_sharpe_history" not in reasons:
        errors.append(f"prescreen expected negative_sharpe_history in {dict(reasons)}")
    if len(kept) != 2:
        errors.append(f"prescreen expected 2 kept rows got {len(kept)} reasons={dict(reasons)}")

    near_dup_payloads = [
        {
            "regular": "group_rank(fnd6_test_ebit/cap,subindustry)-0.5",
            "settings": {},
            "meta": {"family": "pass_pv", "candidate_score": 2.0},
        },
        {
            "regular": "group_rank(fnd6_test_ebit/cap,subindustry)-0.5*0.99",
            "settings": {},
            "meta": {"family": "pass_pv", "candidate_score": 1.0},
        },
        {
            "regular": "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),subindustry)",
            "settings": {},
            "meta": {"family": "pass_fundamental_ts", "candidate_score": 3.0},
        },
    ]
    coarse_kept, coarse_reasons, _ = screener.screen(near_dup_payloads, stage="coarse")
    if len(coarse_kept) < 2:
        errors.append(f"coarse prescreen expected >=2 kept got {len(coarse_kept)} reasons={dict(coarse_reasons)}")
    picked, fine_reasons, _ = screener.select_diverse_for_simulate(coarse_kept, 2)
    if len(picked) < 2:
        errors.append(f"fine diverse select expected 2 got {len(picked)} reasons={dict(fine_reasons)}")

    generated_pool = HistorySimilarityPools()
    generated_pool.append_tokens("group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),subindustry)", "generated")
    generated_screener = PreSimulationScreener(
        factory_cfg,
        tried_exact=set(),
        tried_payload_keys=set(),
        near_pass_expressions=set(),
        failed_cluster={},
        history_pools=generated_pool,
        top_field_lookup=lambda _e: None,
        tried_metrics={},
    )
    _kept_g, reasons_g, _ = generated_screener.screen(
        [
            {
                "regular": "group_neutralize(ts_zscore(fnd6_test_ebit/cap,126),subindustry)",
                "settings": {},
                "meta": {"family": "pass_pv"},
            }
        ]
    )
    if not any(k.startswith("generated_near_clone") for k in reasons_g):
        errors.append(f"generated near-clone gate expected rejection in {dict(reasons_g)}")

    novelty = NoveltyIndex()
    novelty.add("group_neutralize(ts_zscore(x/cap,126),subindustry)")
    novelty_reason = novelty.reject_reason(
        "group_neutralize(ts_zscore(x/cap,252),subindustry)",
        strictness="strict",
    )
    if novelty_reason != "novelty_exact_normalized":
        errors.append(f"novelty expected exact_normalized got {novelty_reason!r}")

    import alpha_mining.simulate.async_batch as _async_batch  # noqa: F401 — import smoke
    if not hasattr(_async_batch, "_build_ssl_connector"):
        errors.append("async_batch missing _build_ssl_connector")

    if errors:
        for e in errors:
            print(f"[smoke] FAIL {e}")
        return 1
    print("[smoke] OK offline checks passed")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=f"WorldQuant Alpha Pipeline {PIPELINE_VERSION}")
    p.add_argument(
        "--mode",
        choices=["full", "generate", "simulate", "submit", "continuous", "recheck", "preflight"],
        default="full",
    )
    p.add_argument(
        "--smoke",
        action="store_true",
        help="Run offline smoke checks (imports, quality gate, prescreen helpers) and exit.",
    )
    p.add_argument("--preset", choices=["conservative", "pv", "fundamental", "mixed", "challenge", "diverse_exploration"], default="mixed")
    p.add_argument("--budget", type=int, default=300, help="Generation target floor; default 300.")
    p.add_argument("--run-payload-cap", type=int, default=None, help="Optional hard cap on payloads simulated this run. Default: simulate *all* prescreen-kept payloads (no limit).")
    p.add_argument(
        "--min-simulate-batch",
        type=int,
        default=None,
        metavar="N",
        help="Minimum payloads to simulate per batch (floor). Default 200.",
    )
    p.add_argument(
        "--target-simulate-batch",
        type=int,
        default=None,
        metavar="N",
        help="Soft max per batch (0 = no soft cap, only min floor + safety cap). Default 0.",
    )
    p.add_argument(
        "--max-simulate-batch",
        type=int,
        default=None,
        metavar="N",
        help="Safety cap on simulate count per run (default 500 from config).",
    )
    p.add_argument(
        "--novelty-strictness",
        choices=["balanced", "strict", "paranoid"],
        default="strict",
        help="Novelty/self-correlation guard strength. Default strict.",
    )
    p.add_argument(
        "--diversity-mode",
        choices=["quality_diverse", "pass_rate"],
        default=None,
        help="Override the preset diversity mode. Omit to preserve the preset policy.",
    )
    p.add_argument(
        "--diversity-fast-signal-penalty",
        type=float,
        default=None,
        metavar="PENALTY",
        help="Down-weight fast price/volume signals in fine-rank. 0=off (default). "
             "Use setup_from_history.py to compute the recommended value from history.",
    )
    p.add_argument(
        "--ladder-check-enabled",
        action="store_true",
        default=False,
        help="Enable IS ladder Sharpe pre-check (per-year consistency check). "
             "Uses local daily-return series when available; falls back to extra platform calls.",
    )
    p.add_argument(
        "--exploration-region-share",
        type=float,
        default=None,
        metavar="SHARE",
        help="Fraction of payloads to route to non-primary region/universe (0.0=off, 0.2=20%%). "
             "Only active when > 0.",
    )
    p.add_argument(
        "--behavior-similarity-cap",
        type=float,
        default=0.78,
        help="Behavior fingerprint similarity cap for quality_diverse mode. Default 0.78.",
    )
    p.add_argument(
        "--near-pass-variants-per-seed",
        type=int,
        default=2,
        help="Max near-pass structural variants per seed. Default 2.",
    )
    p.add_argument(
        "--max-behavior-per-seed",
        type=int,
        default=1,
        help="Max emitted behavior variants per near-pass seed in quality_diverse mode. Default 1.",
    )
    p.add_argument(
        "--analyze-recent",
        type=int,
        default=None,
        metavar="N",
        help="Fetch last N platform alphas, compute pass rates + pattern buckets, write alpha_recent_platform_analysis.csv and exit.",
    )
    p.add_argument(
        "--feedback-diagnostics",
        action="store_true",
        help="Analyze feedback/result CSV files and exit without platform calls.",
    )
    p.add_argument(
        "--batch-diagnostics",
        action="store_true",
        help="Generate, prescreen, write batch diagnostics, then skip simulate.",
    )
    p.add_argument(
        "--no-prescreen-relax",
        action="store_true",
        help="Do not relax prescreen similarity when kept count is below --min-simulate-batch.",
    )
    p.add_argument(
        "--prebatch-recheck",
        action="store_true",
        help="Run bounded pre-batch recheck before generate/simulate. Default is off to protect throughput.",
    )
    p.add_argument(
        "--no-prebatch-recheck",
        action="store_true",
        help="Skip pre-batch recheck before generate/simulate (post-batch may still run).",
    )
    p.add_argument(
        "--no-postbatch-recheck",
        action="store_true",
        help="Skip bounded post-batch recheck after simulate.",
    )
    p.add_argument(
        "--recheck-postbatch-max-items",
        type=int,
        default=None,
        metavar="N",
        help="Max needs_recheck rows after each simulate batch (default 12).",
    )
    p.add_argument(
        "--recheck-postbatch-wall-budget-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Wall time cap for post-batch recheck (default 900).",
    )
    p.add_argument(
        "--recheck-max-items",
        type=int,
        default=None,
        metavar="N",
        help="Cap items processed in --mode recheck (default 20; 0 = no cap). Ignored with --recheck-deep.",
    )
    p.add_argument(
        "--recheck-wall-budget-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop --mode recheck after this many seconds (default 1800). 0 = no cap. Ignored with --recheck-deep.",
    )
    p.add_argument(
        "--recheck-quick-timeout-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Per-alpha check timeout in --mode recheck (default 600). Ignored with --recheck-deep.",
    )
    p.add_argument(
        "--recheck-deep",
        action="store_true",
        help="Unbounded recheck with full self-correlation wait (can run for days; not for outer loop).",
    )
    p.add_argument(
        "--single-stage-prescreen",
        action="store_true",
        help="Use v40 one-pass prescreen (no coarse→fine diverse selection).",
    )
    p.add_argument(
        "--max-payload-expand-cap",
        type=int,
        default=None,
        metavar="N",
        help="Hard ceiling when expanding candidates→payloads before prescreen (default from config).",
    )
    p.add_argument("--universe", default="TOP3000")
    p.add_argument("--delay", type=int, default=1)
    p.add_argument("--dataset-ids", default="", help="Comma-separated dataset ids. Empty means auto.")
    p.add_argument("--max-submit", type=int, default=20)
    p.add_argument("--execute-submit", action="store_true", help="Actually submit queued alphas. Default is dry-run.")
    p.add_argument("--dry-run-submit", action="store_true", help="Keep submit mode in dry-run mode.")
    p.add_argument("--queue-min-sharpe", type=float, default=1.25)
    p.add_argument("--queue-min-fitness", type=float, default=1.0)
    p.add_argument(
        "--simulate-check-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Initial platform-check wait per simulated alpha before deferring to needs_recheck. Default 90.",
    )
    p.add_argument(
        "--simulate-quality-check-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Initial platform-check wait for metric-pass alphas. Default 600.",
    )
    p.add_argument("--no-prescreen", action="store_true", help="Disable pre-simulate screening layer")
    p.add_argument("--prescreen-similarity", type=float, default=None, help="Override prescreen Jaccard threshold vs history token pool (default ~0.64)")
    p.add_argument(
        "--skip-registry-similarity",
        action="store_true",
        help="Do not load alpha_generated_expressions*.csv into the prescreen similarity pool.",
    )
    p.add_argument(
        "--similarity-history-cap",
        type=int,
        default=None,
        metavar="N",
        help="Max token-rows kept for Jaccard prescreen (reservoir sample; default from config).",
    )
    p.add_argument("--history-similarity", type=float, default=None, help="Override candidate-stage max Jaccard vs history (default ~0.72)")
    p.add_argument("--no-near-pass", action="store_true", help="Disable near-pass variant amplification")
    p.add_argument("--https-proxy", default=None)
    p.add_argument("--no-ipv4", action="store_true")
    p.add_argument("--tls-no-verify", action="store_true")
    p.add_argument(
        "--auth-state-file",
        default=None,
        metavar="PATH",
        help="DPAPI-protected shared authentication state (default .wq_auth_state.json).",
    )
    p.add_argument(
        "--auth-cooldown-seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Minimum successful-login reuse window (default 1500 / 25 minutes).",
    )
    p.add_argument(
        "--auth-daily-cap",
        type=int,
        default=None,
        metavar="N",
        help="UTC real-login safety cap; only 1..5 accepted (default 5).",
    )
    p.add_argument(
        "--auth-retries",
        type=int,
        default=None,
        help="Authentication attempts per trigger; only 1..2 accepted (default 2).",
    )
    p.add_argument(
        "--sequential-sim",
        action="store_true",
        help="Disable async simulate (use legacy sequential HTTP for each alpha).",
    )
    p.add_argument(
        "--concurrent-sim",
        type=int,
        default=None,
        metavar="N",
        help="Max concurrent simulate workers (default 16; reduce if you see 429).",
    )
    p.add_argument(
        "--concurrent-submit",
        type=int,
        default=None,
        metavar="N",
        help="Max concurrent POST /simulations requests (default 2; polling still uses --concurrent-sim).",
    )
    p.add_argument(
        "--sqlite-runs",
        default=None,
        metavar="PATH",
        help="SQLite audit/idempotency store (default: research_memory.sqlite outside observation mode).",
    )
    p.add_argument(
        "--submission-observe",
        action="store_true",
        help="Record local submission observations and description drafts; never writes or submits WorldQuant alphas.",
    )
    p.add_argument(
        "--submission-observe-description-limit",
        type=int,
        default=20,
        metavar="N",
        help="Maximum ready-candidate description drafts per process when --submission-observe is enabled.",
    )
    p.add_argument(
        "--no-phase2-llm",
        action="store_true",
        help="Disable Phase 2 LLM hypothesis generation (default: enabled).",
    )
    p.add_argument(
        "--no-phase3-llm",
        action="store_true",
        help="Disable Phase 3 LLM grammar expression generation (default: enabled).",
    )
    p.add_argument(
        "--phase23-hypotheses",
        type=int,
        default=None,
        metavar="N",
        help="Max hypotheses to drive LLM expression generation per call (default: 3).",
    )
    return p.parse_args()


def main() -> int:
    _load_env_file()
    args = parse_args()
    if args.smoke:
        return _run_offline_smoke()
    from alpha_mining.factory.control import FactoryControl

    legacy_read_only = bool(args.submission_observe or getattr(args, "analyze_recent", None) or args.mode == "preflight")
    if not legacy_read_only:
        state = FactoryControl("research_memory.sqlite").status()
        if state.hard_stop:
            print(f"[legacy-v50] BLOCKED: vNext factory hard stop ({state.reason})")
            return 2
    if args.submission_observe and not args.sqlite_runs:
        print("[error] --submission-observe requires --sqlite-runs PATH")
        return 2
    if getattr(args, "analyze_recent", None):
        username, password = _credentials()
        if not username or not password:
            print("[error] Missing WQ_USERNAME / WQ_PASSWORD in environment or .env")
            return 2
        cfg = PipelineConfig(
            username=username,
            password=password,
            mode="analyze-recent",
            analyze_recent_workers=6,
        )
        pipeline = WorldQuantAlphaPipeline(cfg)
        try:
            result = pipeline.run_analyze_recent(max(1, int(args.analyze_recent)))
            if isinstance(result, pd.DataFrame):
                print(f"[done] analyze_recent_rows={len(result)}")
            return 0
        except Exception as e:
            import traceback
            print(f"[fatal] {e}")
            traceback.print_exc()
            return 1
    if args.feedback_diagnostics:
        feedback_floor = 60 if args.preset == "diverse_exploration" else 120
        cfg = PipelineConfig(
            username="offline",
            password="offline",
            mode="feedback-diagnostics",
            preset=args.preset,
            target_simulate_batch=max(0, int(args.target_simulate_batch or 180)),
            min_simulate_batch=max(feedback_floor, int(args.min_simulate_batch or 180)),
            novelty_strictness=args.novelty_strictness,
            diversity_mode=args.diversity_mode or "quality_diverse",
            behavior_similarity_cap=float(args.behavior_similarity_cap),
            near_pass_max_variants_per_seed=max(0, int(args.near_pass_variants_per_seed)),
            max_behavior_per_seed=max(1, int(args.max_behavior_per_seed)),
            feedback_diagnostics_only=True,
        )
        pipeline = WorldQuantAlphaPipeline(cfg)
        result = pipeline.run_feedback_diagnostics()
        if isinstance(result, pd.DataFrame):
            print(f"[done] feedback_diagnostics_rows={len(result)}")
        return 0
    username, password = _credentials()
    if not username or not password:
        print("[error] Missing WQ_USERNAME / WQ_PASSWORD in environment or .env")
        return 2
    dataset_ids = tuple(x.strip() for x in args.dataset_ids.split(",") if x.strip()) or None
    pilot = args.preset == "diverse_exploration"
    config_floor = 60 if pilot else 120
    cfg = PipelineConfig(
        username=username,
        password=password,
        mode=args.mode,
        preset=args.preset,
        budget=max(120, int(args.budget)),
        run_payload_cap=(max(1, int(args.run_payload_cap)) if args.run_payload_cap is not None else None),
        target_simulate_batch=max(0, int(args.target_simulate_batch or (60 if pilot else 180))),
        min_simulate_batch=max(config_floor, int(args.min_simulate_batch or (60 if pilot else 180))),
        novelty_strictness=args.novelty_strictness,
        diversity_mode=args.diversity_mode or ("quality_diverse" if pilot else "pass_rate"),
        behavior_similarity_cap=float(args.behavior_similarity_cap),
        near_pass_max_variants_per_seed=max(0, int(args.near_pass_variants_per_seed)),
        max_behavior_per_seed=max(1, int(args.max_behavior_per_seed)),
        batch_diagnostics_only=bool(args.batch_diagnostics),


        universe=args.universe,
        delay=args.delay,
        dataset_ids=dataset_ids,
        max_submit=max(1, int(args.max_submit)),
        dry_run_submit=not bool(args.execute_submit),
        queue_min_sharpe=float(args.queue_min_sharpe),
        queue_min_fitness=float(args.queue_min_fitness),
        https_proxy=args.https_proxy,
        force_ipv4=not args.no_ipv4,
        tls_verify=not args.tls_no_verify,
        auth_state_file=str(args.auth_state_file or PipelineConfig.auth_state_file),
        auth_cooldown_seconds=(
            max(0.0, float(args.auth_cooldown_seconds))
            if args.auth_cooldown_seconds is not None
            else PipelineConfig.auth_cooldown_seconds
        ),
        auth_daily_cap=(
            max(1, min(5, int(args.auth_daily_cap)))
            if args.auth_daily_cap is not None
            else PipelineConfig.auth_daily_cap
        ),
        auth_max_retries=(
            max(1, min(2, int(args.auth_retries)))
            if args.auth_retries is not None
            else PipelineConfig.auth_max_retries
        ),
        max_concurrent_simulations=(
            0
            if args.sequential_sim
            else (max(0, int(args.concurrent_sim)) if args.concurrent_sim is not None else 6)
        ),
        max_concurrent_simulation_posts=(
            max(1, int(args.concurrent_submit)) if args.concurrent_submit is not None else 1
        ),
        simulate_check_poll_seconds=(
            max(1.0, float(args.simulate_check_seconds))
            if args.simulate_check_seconds is not None
            else PipelineConfig.simulate_check_poll_seconds
        ),
        simulate_quality_check_poll_seconds=(
            max(1.0, float(args.simulate_quality_check_seconds))
            if args.simulate_quality_check_seconds is not None
            else PipelineConfig.simulate_quality_check_poll_seconds
        ),
        sqlite_runs_path=(
            str(args.sqlite_runs).strip()
            if args.sqlite_runs
            else "research_memory.sqlite"
        ),
        submission_observe_enabled=bool(args.submission_observe),
        submission_observe_description_limit=max(0, int(args.submission_observe_description_limit)),
    )
    if args.dry_run_submit:
        cfg.dry_run_submit = True
    if args.no_prescreen:
        cfg.prescreen_enabled = False
    if args.prescreen_similarity is not None:
        cfg.prescreen_max_history_similarity = float(args.prescreen_similarity)
    if args.history_similarity is not None:
        cfg.max_history_similarity = float(args.history_similarity)
    if args.skip_registry_similarity:
        cfg.include_generated_registry_in_similarity = False
    if args.min_simulate_batch is not None:
        cfg.min_simulate_batch = max(config_floor, int(args.min_simulate_batch))
    if args.target_simulate_batch is not None:
        cfg.target_simulate_batch = max(0, int(args.target_simulate_batch))
    if getattr(args, "max_simulate_batch", None) is not None:
        cfg.max_simulate_batch_per_run = max(0, int(args.max_simulate_batch))
    if args.no_prescreen_relax:
        cfg.prescreen_relax_to_hit_min_batch = False
    if args.single_stage_prescreen:
        cfg.prescreen_two_stage = False
    if getattr(args, "prebatch_recheck", False):
        cfg.recheck_skip_prebatch = False
    if getattr(args, "no_prebatch_recheck", False):
        cfg.recheck_skip_prebatch = True
    if getattr(args, "no_postbatch_recheck", False):
        cfg.recheck_skip_postbatch = True
    if getattr(args, "recheck_postbatch_max_items", None) is not None:
        cfg.recheck_postbatch_max_items = max(0, int(args.recheck_postbatch_max_items))
    if getattr(args, "recheck_postbatch_wall_budget_seconds", None) is not None:
        cfg.recheck_postbatch_wall_budget_seconds = max(0.0, float(args.recheck_postbatch_wall_budget_seconds))
    if getattr(args, "recheck_deep", False):
        cfg.recheck_deep = True
    if getattr(args, "recheck_max_items", None) is not None:
        cfg.recheck_standalone_max_items = max(0, int(args.recheck_max_items))
    if getattr(args, "recheck_wall_budget_seconds", None) is not None:
        cfg.recheck_standalone_wall_budget_seconds = max(0.0, float(args.recheck_wall_budget_seconds))
    if getattr(args, "recheck_quick_timeout_seconds", None) is not None:
        cfg.recheck_standalone_quick_timeout_seconds = max(1.0, float(args.recheck_quick_timeout_seconds))
    if args.max_payload_expand_cap is not None:
        cfg.max_payload_expand_cap = max(10_000, int(args.max_payload_expand_cap))
    if args.no_near_pass:
        cfg.near_pass_enabled = False
    if cfg.diversity_mode == "pass_rate":
        cfg.prescreen_fine_desperate_fill = True
        cfg.max_behavior_per_batch = 10_000
        cfg.max_operator_field_per_batch = 10_000
    if getattr(args, "diversity_fast_signal_penalty", None) is not None:
        cfg.diversity_fast_signal_penalty = max(0.0, float(args.diversity_fast_signal_penalty))
    if getattr(args, "ladder_check_enabled", False):
        cfg.ladder_check_enabled = True
    if getattr(args, "exploration_region_share", None) is not None:
        cfg.exploration_region_share = max(0.0, min(1.0, float(args.exploration_region_share)))
    if getattr(args, "no_phase2_llm", False):
        cfg.phase2_llm_enabled = False
    if getattr(args, "no_phase3_llm", False):
        cfg.phase3_llm_grammar_enabled = False
    if getattr(args, "phase23_hypotheses", None) is not None:
        cfg.phase23_hypotheses_per_call = max(1, int(args.phase23_hypotheses))

    pipeline = WorldQuantAlphaPipeline(cfg)
    try:
        result = pipeline.run()
        if isinstance(result, pd.DataFrame):
            print(f"[done] rows={len(result)}")
        return 0
    except KeyboardInterrupt:
        print("[stop] interrupted")
        return 130
    except Exception as e:
        import traceback
        print(f"[fatal] {e}")
        traceback.print_exc()
        return _exit_code_for_fatal(e)


if __name__ == "__main__":
    raise SystemExit(main())
