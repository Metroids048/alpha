
from __future__ import annotations

import argparse
import ast
import csv
import glob
import json
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
PIPELINE_VERSION = "v46"
UNIFIED_FEEDBACK_CSV = "alpha_submission_feedback.csv"
UNIFIED_REGISTRY_CSV = "alpha_generated_expressions.csv"
UNIFIED_HOPEFUL_JSONL = "hopeful_alphas.jsonl"
UNIFIED_SUBMISSION_JSONL = "submission_results.jsonl"
UNIFIED_OUTPUT_PREFIX = "alpha_pipeline"
FEEDBACK_DIAGNOSTICS_CSV = "alpha_feedback_diagnostics.csv"
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
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "python-dateutil"],
            stdout=sys.stdout,
            stderr=sys.stderr,
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


def _sig(expr: str) -> str:
    return re.sub(r"\s+", " ", str(expr or "").strip())


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
    s = _sig(expr).lower()
    s = re.sub(r"\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b", "#", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\*#(?:\.#*)?", "*#", s)
    s = re.sub(r"\+#(?:\.#*)?", "+#", s)
    s = re.sub(r"-#(?:\.#*)?", "-#", s)
    return s


def _field_signature(expr: str, max_fields: int = 4) -> str:
    fields = sorted(_expression_fields(expr))[:max_fields]
    return "|".join(fields) if fields else "-"


def _structure_signature(expr: str) -> str:
    funcs = _extract_functions(expr)
    func_part = ">".join(funcs[:10]) if funcs else "raw"
    return f"{func_part}::{_field_signature(expr)}::{_operator_skeleton(expr)}"


def _token_jaccard(a: set[str], b: set[str]) -> float:
    den = len(a | b)
    return (len(a | b) / den) if den else 0.0


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

    def append_tokens(self, expr: str, tier: str) -> None:
        toks = _expr_token_set(expr)
        if not toks:
            return
        bucket = {
            "toxic": self.toxic,
            "weak_fail": self.weak_fail,
            "near_pass": self.near_pass,
            "passed": self.passed,
        }.get(tier, self.weak_fail)
        bucket.append(toks)


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
        _sig(expr).lower(),
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
    return {
        "index": index,
        "alpha_id": alpha_id or "",
        "status": status,
        "queue_status": queue_status,
        "check_passed": check_passed,
        "check_note": check_note,
        "expression": expression,
        "profile": profile,
        "sharpe": _to_float(_metric_get(merged, "sharpe", "Sharpe")),
        "fitness": _to_float(_metric_get(merged, "fitness", "Fitness")),
        "turnover": _to_float(_metric_get(merged, "turnover", "Turnover")),
        "returns": _to_float(_metric_get(merged, "returns", "Returns")),
        "drawdown": _to_float(_metric_get(merged, "drawdown", "Drawdown")),
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


def _hard_fail_checks(detail: dict | None) -> list[str]:
    hard = []
    for c in _extract_checks(detail):
        name = str(c.get("name") or "").upper()
        result = str(c.get("result") or c.get("status") or "").upper()
        if result in ("FAIL", "FAILED", "ERROR", "REJECTED"):
            hard.append(name or "UNKNOWN_CHECK")
    return hard


def _is_dns_error(exc: BaseException | str) -> bool:
    s = str(exc).lower()
    return any(x in s for x in ("nameresolutionerror", "getaddrinfo failed", "temporary failure in name resolution", "gaierror", "getaddrinfo"))


def _is_transient_connect_error(exc: BaseException) -> bool:
    if _is_dns_error(exc):
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
            "ssl",
            "broken pipe",
            "semaphore timeout",
        )
    )


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
    target_simulate_batch: int = 300
    # ---- batch simulate policy (durable defaults; do not tie payload expansion to this) ----
    # Target: after prescreen, send *all* kept payloads to simulate when `run_payload_cap` is None.
    # If kept count is still below this floor, progressively relax prescreen similarity (bounded).
    min_simulate_batch: int = 300
    prescreen_relax_to_hit_min_batch: bool = True
    # When loosening prescreen: **raise** these Jaccard caps (higher = allow more similar past alphas through).
    prescreen_similarity_relax_step: float = 0.03
    prescreen_similarity_relax_ceiling: float = 0.88
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
    dedup_against_library_skeleton: bool = False
    success_blacklist_similarity_threshold: float = 0.88
    max_family_share: float = 0.14
    # Token Jaccard vs *all* historically simulated / generated expressions (self-corr proxy).
    # With 4500+ generated rows, defaults must be stricter than v34 early defaults.
    max_history_similarity: float = 0.88
    # Toxic pool: multi-fail / negative / far-below-threshold alphas — must not resemble these.
    # Toxic block only near-clones of clearly bad alphas (negative / catastrophic fails).
    max_toxic_history_similarity: float = 0.85
    template_max_history_similarity: float = 0.90
    formulaic_max_history_similarity: float = 0.78
    prescreen_max_toxic_similarity: float = 0.85
    template_skip_toxic_similarity: bool = True
    prescreen_skip_toxic_for_near_pass: bool = True
    prescreen_skip_weak_for_template: bool = True
    prescreen_skip_weak_for_near_pass: bool = True
    prescreen_skip_novelty_for_template: bool = True
    near_pass_min_core_candidates: int = 12
    near_pass_skip_when_template_core: int = 20
    # Generate: block exact re-run of platform-tried exprs only (not whole feedback ledger).
    generate_exact_seen_from_tried: bool = True
    generate_use_toxic_similarity: bool = False
    template_skip_weak_similarity: bool = True
    generate_template_rescue: bool = True
    similarity_toxic_max_rows: int = 12000
    similarity_weak_max_rows: int = 8000
    similarity_near_pass_max_rows: int = 2500
    formulaic_primitives_enabled: bool = True
    template_skip_history_skeleton: bool = True
    timeout: int = 45
    connect_timeout: int = 90
    submit_timeout: int = 120
    max_poll_seconds_per_alpha: int = 720
    # Platform SELF_CORRELATION can stay PENDING for many minutes under load; 20min is often too short.
    max_check_poll_seconds: float = 2700.0
    check_poll_interval_seconds: float = 3.0
    # Self-correlation checks can take many minutes; short quick rechecks falsely
    # leave alphas stuck in needs_recheck forever.
    recheck_quick_timeout_seconds: float = 900.0
    # Pre-batch recheck runs *before* generate/simulate and is strictly sequential (one alpha at a time).
    # A long per-alpha timeout × many items blocks the whole pipeline for an hour+ — keep this short and
    # cap total wall time; use ``--mode recheck`` (no wall cap) for a deep self-corr drain.
    recheck_prebatch_quick_timeout_seconds: float = 60.0
    recheck_prebatch_max_items: int = 20
    # Stop pre-batch recheck after this many seconds total (remaining pending stay for post-batch / manual recheck).
    recheck_prebatch_wall_budget_seconds: float = 240.0
    recheck_postbatch_max_items: int = 28
    # If simulation progress polling hits the wall, extend once (handles slow queues / SSL hiccups).
    simulation_poll_retry_extend_seconds: int = 420
    recheck_heartbeat_every_polls: int = 5
    submit_sleep: float = 0.75
    page_sleep: float = 0.15
    pre_simulate_cooldown_seconds: float = 3.0
    poll_fallback_sleep: float = 0.42
    poll_error_sleep: float = 2.0
    adaptive_base_sleep: float = 2.0
    adaptive_max_sleep: float = 90.0
    adaptive_backoff_factor: float = 2.0
    adaptive_recover_factor: float = 0.85
    hard_cooldown_429_count: int = 5
    hard_cooldown_seconds: float = 180.0
    submit_429_min_sleep: float = 25.0
    dns_error_pause_count: int = 3
    dns_error_pause_seconds: float = 180.0
    max_retries: int = 5
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
    alpha_models_enabled: bool = True
    alpha_models_instances_per_template: int = 6
    alpha_models_max_templates: int = 64
    alpha_models_candidate_score: float = 2.85
    alpha_models_batch_quota: int = 120
    near_pass_max_family_share: float = 0.10
    near_pass_only_when_short: bool = True
    near_pass_shortfall_ratio: float = 0.45
    alpha50_filename: str = "alpha50.csv"
    save_every_n: int = 20
    enable_auto_invert_retry: bool = True
    pass_first_mode: bool = True
    exploration_ratio: float = 0.05
    # v39 experiment families (regime_shift / pair / ts_corr spreads) systematically hurt IS Sharpe.
    enable_explore_families: bool = False
    max_operator_depth: int = 3
    max_nested_functions: int = 8
    # Log analysis: bare `group_rank(f/cap,subindustry)-0.5` → LOW_SHARPE; hybrids pass far more often.
    max_pass_fundamental_level_fields: int = 28
    # Extra poll budget when Sharpe/Fitness already PASS but SELF_CORRELATION is PENDING.
    check_self_correlation_extra_seconds: float = 3600.0
    max_queue_similarity: float = 0.84
    queue_min_returns: float = -0.05
    queue_max_drawdown: float = 0.30
    queue_min_margin: float = -0.05
    queue_prefer_low_similarity: bool = True
    min_candidates_floor: int = 300
    fallback_disable_library_skeleton_dedup: bool = True
    fallback_disable_history_skeleton_dedup: bool = True
    block_history_skeleton_always: bool = False
    block_generated_registry_exact: bool = False
    # Pre-simulate screening
    prescreen_enabled: bool = True
    prescreen_max_nesting_depth: int = 6
    prescreen_max_function_calls: int = 9
    prescreen_max_history_similarity: float = 0.85
    prescreen_near_pass_similarity: float = 0.90
    # Additional in-batch diversity guard: keep newly kept payloads from collapsing
    # into one near-duplicate cluster while still allowing batch-size relaxation.
    prescreen_intrabatch_similarity: float = 0.80
    prescreen_intrabatch_similarity_relax_step: float = 0.03
    prescreen_intrabatch_similarity_relax_ceiling: float = 0.78
    # Two-stage prescreen (v41): coarse keeps quality/history gates but skips batch-local quotas;
    # fine pass greedily picks a diverse simulate set up to min_simulate_batch.
    prescreen_two_stage: bool = True
    prescreen_coarse_skip_intrabatch: bool = True
    prescreen_coarse_skip_shape_quota: bool = True
    prescreen_coarse_relax_to_fill: bool = True
    prescreen_fine_fill_to_target: bool = True
    prescreen_fine_history_relax_step: float = 0.04
    prescreen_fine_intrabatch_relax_ceiling: float = 0.92
    prescreen_fine_desperate_fill: bool = True
    recheck_skip_prebatch: bool = False
    recheck_skip_postbatch: bool = False
    novelty_enabled: bool = True
    novelty_strictness: str = "balanced"
    novelty_index_filename: str = NOVELTY_INDEX_JSON
    batch_diagnostics_filename: str = BATCH_DIAGNOSTICS_CSV
    feedback_diagnostics_filename: str = FEEDBACK_DIAGNOSTICS_CSV
    batch_diagnostics_only: bool = False
    feedback_diagnostics_only: bool = False
    # Append every expression from generated registries into the prescreen similarity pool
    # (in addition to rows read from alpha_submission_feedback*.csv).
    include_generated_registry_in_similarity: bool = True
    # Cap token-sets kept for Jaccard comparisons (random reservoir) to bound CPU.
    similarity_history_max_token_rows: int = 14000
    prescreen_block_already_simulated: bool = True
    prescreen_skip_low_sharpe_cluster: bool = True
    prescreen_cluster_max_avg_sharpe: float = 0.42
    prescreen_cluster_min_samples: int = 3
    prescreen_allow_near_pass_settings_retry: bool = True
    prescreen_skip_negative_sharpe: bool = True
    prescreen_negative_sharpe_floor: float = 0.30
    # Near-pass amplifier (variants around historical near-pass alphas)
    near_pass_enabled: bool = True
    near_pass_min_sharpe: float = 0.75
    # Seeds use score ≈ sharpe + 1.05*fitness; require both marginally strong to avoid amplifying junk.
    near_pass_seed_min_composite: float = 1.38
    near_pass_max_variants_per_seed: int = 6
    near_pass_max_seeds: int = 32
    near_pass_primary_decay: int = 1
    # Payload allocation for one simulation batch
    near_pass_batch_quota: int = 40
    # Proven hybrid (fundamental + price leg) — highest pass rate in historical hopeful queue.
    pass_hybrid_batch_quota: int = 100
    # Prefer high-prior templates so batches are not dominated by weak explores.
    robust_batch_quota: int = 0
    pass_first_batch_quota: int = 150
    delta_liquid_batch_quota: int = 40
    explore_batch_quota: int = 0
    pass_fundamental_ts_max_per_batch: int = 50
    max_same_shape_per_run: int = 8
    # Async simulate (aiohttp): 0 = legacy sequential requests; 10–15 recommended for stability.
    max_concurrent_simulations: int = 16
    # POST /simulations is rate-limited; 2 is a practical default—drop to 1 if you see 429 bursts.
    max_concurrent_simulation_posts: int = 2
    sqlite_runs_path: str | None = None

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
        for _, row in df.iterrows():
            fid = str(row.get("id", "")).strip()
            if not fid:
                continue
            ds = str(row.get("_ds", "")).lower()
            by_ds[ds].append(fid)
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


def is_inverse_field(field_name: str) -> bool:
    low = str(field_name or "").lower()
    inverse_tokens = (
        "debt", "liabilit", "expense", "payable", "inventory", 
        "accrual", "tax", "borrow", "risk", "short", "loss"
    )
    return any(t in low for t in inverse_tokens)

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
        # Sparse / gated signals tend to fail CONCENTRATED_WEIGHT and LOW_SUB_UNIVERSE_SHARPE more often.
        # Keep them out of the default generator pool.
        if any(x in low for x in ("trade_when(", "if_else(", "bucket(")):
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

class ExpressionFactory:
    def __init__(self, cfg: PipelineConfig, catalog: FieldCatalog, validator: PreflightValidator):
        self.cfg = cfg
        self.catalog = catalog
        self.validator = validator
        self.rng = random.Random(time.time_ns() & 0xFFFFFFFF)

    def generate(
        self,
        history_seen: set[str],
        history_skeletons: set[str],
        history_pools: HistorySimilarityPools,
        library_skeletons: set[str],
        *,
        tried_exact: set[str] | None = None,
    ) -> list[ExpressionCandidate]:
        """Build a submission-first candidate pool.

        The goal here is not maximal novelty for its own sake; it is to keep the
        generator biased toward expressions that are structurally stable,
        sufficiently different from prior failures, and more likely to clear the
        platform's Sharpe/Fitness/Sub-universe gates.
        """
        raw: list[ExpressionCandidate] = []
        if self.cfg.pass_first_mode:
            # Proven templates only — avoid regime_shift / pair / ts_corr / bare rank floods (negative Sharpe).
            raw.extend(self._pass_first_hybrid_family())
            raw.extend(self._pass_first_fundamental_family())
            raw.extend(self._pass_first_pv_family())
            raw.extend(self._robust_pv_family_smoothed())
            if self.cfg.alpha_models_enabled:
                raw.extend(self._alpha_models_template_family())
            if self.cfg.formulaic_primitives_enabled:
                raw.extend(self._formulaic_primitives_family())

            if self.cfg.enable_explore_families:
                explore: list[ExpressionCandidate] = []
                explore.extend(self._broad_fundamental_family())
                explore.extend(self._broad_pv_family())
                explore.extend(self._alpha50_template_family())
                explore_budget = max(
                    1,
                    int(max(1, self.cfg.budget * self.cfg.candidate_multiplier) * self.cfg.exploration_ratio),
                )
                self.rng.shuffle(explore)
                raw.extend(explore[:explore_budget])
        else:
            raw.extend(self._robust_fundamental_family())
            raw.extend(self._regime_shift_family())
            raw.extend(self._robust_analyst_family())
            raw.extend(self._liquidity_family())
            raw.extend(self._cross_hybrid_family())
            raw.extend(self._robust_pv_family())
            raw.extend(self._hybrid_stability_family())
            raw.extend(self._broad_fundamental_family())
            raw.extend(self._broad_pv_family())
            raw.extend(self._quality_family())
            raw.extend(self._volatility_family())
            raw.extend(self._pair_family())
            raw.extend(self._alpha50_template_family())
            raw.extend(self._external_template_family())
            raw.extend(self._tanay_style_family())

        tried = tried_exact if tried_exact is not None else history_seen
        out, reject_counts = self._screen_candidates(
            raw,
            history_seen,
            history_skeletons,
            history_pools,
            library_skeletons,
            tried_exact=tried,
            use_library_skeleton_dedup=self.cfg.dedup_against_library_skeleton,
        )
        floor = max(1, min(int(self.cfg.min_candidates_floor), max(1, int(self.cfg.budget * self.cfg.candidate_multiplier))))
        if len(out) < floor and self.cfg.fallback_disable_library_skeleton_dedup and self.cfg.dedup_against_library_skeleton:
            print(f"[generate] fallback triggered: candidates={len(out)} < floor={floor}; disable library skeleton dedup once")
            out, reject_counts = self._screen_candidates(
                raw,
                history_seen,
                history_skeletons,
                history_pools,
                library_skeletons,
                tried_exact=tried,
                use_library_skeleton_dedup=False,
            )
        if len(out) < floor and self.cfg.fallback_disable_history_skeleton_dedup and self.cfg.block_history_skeleton_always:
            print(f"[generate] fallback triggered: candidates={len(out)} < floor={floor}; disable history skeleton dedup once (prescreen novelty still active)")
            out, reject_counts = self._screen_candidates(
                raw,
                history_seen,
                set(),
                history_pools,
                library_skeletons,
                tried_exact=tried,
                use_library_skeleton_dedup=False,
                skip_history_skeleton=True,
            )
        if len(out) < max(12, floor // 25) and bool(getattr(self.cfg, "generate_template_rescue", True)):
            rescued, rescue_rejects = self._rescue_template_candidates(raw, tried)
            if rescued:
                print(f"[generate] template_rescue kept={len(rescued)} (bypass history similarity; prescreen still applies)")
                out = rescued + [c for c in out if c.expression not in {x.expression for x in rescued}]
                reject_counts.update(rescue_rejects)

        if reject_counts:
            top_rejects = ", ".join(f"{k}:{v}" for k, v in reject_counts.most_common(8))
            print(f"[generate] rejects {top_rejects}")

        out.sort(key=self._candidate_priority_key)
        capped = self._apply_family_quota(out, max(1, int(self.cfg.budget * self.cfg.candidate_multiplier)))
        return capped[: max(self.cfg.budget * self.cfg.candidate_multiplier, self.cfg.budget)]

    def _rescue_template_candidates(
        self,
        raw: list[ExpressionCandidate],
        tried_exact: set[str],
    ) -> tuple[list[ExpressionCandidate], Counter[str]]:
        """Last resort: keep template/formulaic rows that pass syntax gates only."""
        out: list[ExpressionCandidate] = []
        reject_counts: Counter[str] = Counter()
        seen: set[str] = set()
        for c in raw:
            fam_low = (c.family or "").lower()
            if not (
                fam_low.startswith("alpha_models_template")
                or fam_low.startswith("formulaic_")
                or fam_low == "external_template"
            ):
                continue
            expr = _sig(c.expression)
            if not expr or expr in seen or expr in tried_exact:
                if expr in tried_exact:
                    reject_counts["rescue_already_tried"] += 1
                continue
            ok, note = self._submission_quality_gate(expr, c.family, c.source)
            if not ok:
                reject_counts[f"rescue_{note}"] += 1
                continue
            ok, note = self.validator.validate(expr)
            if not ok:
                reject_counts[f"rescue_{note}"] += 1
                continue
            c.expression = expr
            c.score = c.score + self._score_expression(c)
            seen.add(expr)
            out.append(c)
        return out, reject_counts

    def _screen_candidates(
        self,
        raw: list[ExpressionCandidate],
        history_seen: set[str],
        history_skeletons: set[str],
        history_pools: HistorySimilarityPools,
        library_skeletons: set[str],
        *,
        tried_exact: set[str] | None = None,
        use_library_skeleton_dedup: bool,
        skip_history_skeleton: bool = False,
    ) -> tuple[list[ExpressionCandidate], Counter[str]]:
        out: list[ExpressionCandidate] = []
        seen: set[str] = set()
        shape_counts: Counter[str] = Counter()
        reject_counts: Counter[str] = Counter()
        tried = tried_exact if tried_exact is not None else history_seen
        use_toxic_gen = bool(getattr(self.cfg, "generate_use_toxic_similarity", False))
        for c in raw:
            expr = _sig(c.expression)
            if not expr:
                reject_counts["empty"] += 1
                continue
            if expr in seen:
                reject_counts["duplicate_in_run"] += 1
                continue
            fam_low = (c.family or "").lower()
            template_like = (
                fam_low.startswith("alpha_models_template")
                or fam_low.startswith("formulaic_")
                or fam_low == "external_template"
            )
            if expr in tried:
                reject_counts["already_tried_exact"] += 1
                continue
            if not template_like and expr in history_seen:
                reject_counts["history_seen"] += 1
                continue
            skel = _skel(expr)
            if (
                self.cfg.block_history_skeleton_always
                and not skip_history_skeleton
                and not template_like
                and skel in history_skeletons
            ):
                reject_counts["history_skeleton_seen"] += 1
                continue
            if use_library_skeleton_dedup and not template_like and skel in library_skeletons:
                reject_counts["library_skeleton_seen"] += 1
                continue
            if use_toxic_gen and not (
                template_like and bool(getattr(self.cfg, "template_skip_toxic_similarity", True))
            ):
                toxic_cap = float(self.cfg.max_toxic_history_similarity)
                toxic_sim = history_pools.max_similarity(expr, "toxic", early_exit_at=toxic_cap)
                if toxic_sim >= toxic_cap:
                    reject_counts["toxic_history_similarity"] += 1
                    continue
            skip_weak = template_like and bool(getattr(self.cfg, "template_skip_weak_similarity", True))
            if not skip_weak:
                if template_like:
                    weak_cap = float(self.cfg.template_max_history_similarity)
                else:
                    weak_cap = float(self.cfg.max_history_similarity)
                weak_sim = history_pools.max_similarity(expr, "weak_fail", early_exit_at=weak_cap)
                if weak_sim >= weak_cap:
                    reject_counts["weak_history_similarity"] += 1
                    continue
                if not fam_low.startswith("near_pass_variant") and not template_like:
                    near_cap = max(weak_cap, float(self.cfg.prescreen_near_pass_similarity))
                    near_sim = history_pools.max_similarity(expr, "near_pass", early_exit_at=near_cap)
                    if near_sim >= near_cap:
                        reject_counts["near_pass_history_similarity"] += 1
                        continue
            ok, note = self._submission_quality_gate(expr, c.family, c.source)
            if not ok:
                reject_counts[note] += 1
                continue
            ok, note = self.validator.validate(expr)
            if not ok:
                reject_counts[note] += 1
                continue
            shape = _shape_key(expr)
            if shape_counts[shape] >= int(self.cfg.max_same_shape_per_run):
                reject_counts["shape_quota"] += 1
                continue
            projected_score = c.score + self._score_expression(c)
            min_proj = 2.20 if template_like else 2.35
            if projected_score < min_proj:
                reject_counts["low_projected_score"] += 1
                continue
            if c.source not in ("pass_first", "robust") and c.family.startswith(
                ("regime_shift", "pair_", "quality_", "volatility_", "analyst_condition")
            ):
                reject_counts["non_pass_first_family"] += 1
                continue
            c.expression = expr
            c.score = projected_score
            seen.add(expr)
            shape_counts[shape] += 1
            out.append(c)
        return out, reject_counts

    def _submission_quality_gate(self, expr: str, family: str, source: str) -> tuple[bool, str]:
        low = _sig(expr).lower()
        if not low:
            return False, "empty"

        # Require at least one stabilizing operator or cross-sectional normalizer.
        if not any(tok in low for tok in ("ts_mean(", "ts_zscore(", "ts_rank(", "ts_decay_linear(", "group_neutralize(", "group_rank(", "group_mean(")):
            return False, "insufficient_structure"

        # Reject raw price-ratio noise unless it is clearly smoothed or liquidity-filtered.
        if "zscore(vwap/close)" in low and not any(tok in low for tok in ("ts_decay_linear(", "ts_rank(", "ts_mean(")):
            return False, "raw_vwap_noise"
        if "vwap/close" in low and not any(tok in low for tok in ("ts_decay_linear(", "ts_rank(", "ts_mean(", "group_neutralize(", "group_rank(")):
            return False, "raw_price_ratio"

        # Cross-sectional neutralization is required (bare rank/ts_rank on levels is unstable).
        if not any(tok in low for tok in ("group_neutralize(", "group_rank(")):
            return False, "missing_cross_section"

        if re.search(r"(?<!group_neutralize\()\bts_rank\s*\(", low) and "group_neutralize(" not in low:
            return False, "bare_ts_rank"

        if "ts_corr(" in low and low.count("rank(") >= 2:
            return False, "balance_sheet_pair_corr"

        if re.search(r"(?<!group_neutralize\()\brank\s*\(\s*ts_mean\s*\(", low):
            return False, "bare_level_rank"

        for fid in re.findall(r"\b[a-z_][a-z0-9_]*\b", low):
            if is_weak_fundamental_field(fid):
                return False, "weak_fundamental_field"

        # Short ts_rank on fundamentals tends to be noisy / negative Sharpe in IS.
        for match in re.finditer(r"ts_rank\(([^,]+),\s*(\d+)\)", low):
            arg = match.group(1)
            if int(match.group(2)) < 63 and not any(x in arg for x in ("close", "vwap", "volume", "returns", "adv20", "high", "low")):
                return False, "short_ts_rank"

        # Short-horizon deltas are the main source of noisy / high-turnover outputs.
        for match in re.finditer(r"ts_delta\(([^,]+),\s*(\d+)\)", low):
            arg = match.group(1)
            w = int(match.group(2))
            if w < 10 and not any(tok in arg for tok in ("close", "vwap", "high", "low", "volume", "returns")):
                if not any(tok in low for tok in ("adv20", "ts_decay_linear(", "ts_mean(")):
                    return False, "short_delta_noise"

        # Correlations on very short horizons tend to be unstable in this pipeline.
        corr_windows = [int(x) for x in re.findall(r"ts_corr\([^,]+,\s*[^,]+,\s*(\d+)\)", low)]
        if corr_windows and min(corr_windows) < 21:
            return False, "short_corr_noise"

        # Small helper to keep very noisy PV reversals out of the pool.
        if family.startswith("pass_pv") and "ts_delta(close," in low:
            m = re.search(r"ts_delta\([^,]+,\s*(\d+)\)", low)
            if m and int(m.group(1)) < 21 and "adv20" not in low:
                return False, "weak_pv_reversal"

        return True, "ok"

    def _add(self, out: list[ExpressionCandidate], expr: str, family: str, source: str, score: float = 0.0) -> None:
        out.append(ExpressionCandidate(_sig(expr), family, source, score))

    def _fundamental_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        groups = ("subindustry", "industry", "sector")
        windows = (10, 21, 42, 63, 126)
        for i, f in enumerate(self.catalog.fund[:180]):
            if is_bad_field_name(f) or f.lower() == "cap":
                continue
            g = groups[i % len(groups)]
            self._add(out, f"rank({f}/cap)", "fundamental_rank", "core", field_quality_score(f))
            self._add(out, f"-rank({f}/cap)", "fundamental_rank", "core", field_quality_score(f) + 0.1)
            self._add(out, f"group_neutralize(rank({f}/cap), {g})", "fundamental_neut", "core", field_quality_score(f) + 0.2)
            for w in windows[:4]:
                self._add(out, f"group_neutralize(rank(ts_mean({f}/cap, {w})), {g})", "fundamental_mean", "core", field_quality_score(f) + 0.3)
                self._add(out, f"group_neutralize(rank(ts_delta({f}, {w})/cap), {g})", "fundamental_delta", "core", field_quality_score(f) + 0.2)
            for w in (21, 63, 126):
                self._add(out, f"group_neutralize(ts_zscore({f}/cap, {w}), {g})", "fundamental_z", "core", field_quality_score(f) + 0.1)
        return out

    def _pass_first_hybrid_family(self) -> list[ExpressionCandidate]:
        """Fundamental level + slow price-reversion leg (historically clears LOW_SHARPE/FITNESS)."""
        out: list[ExpressionCandidate] = []
        close = self._pv_var("close")
        priority_tokens = (
            "capex_to_total", "operating_expense", "operating_income", "total_assets",
            "ebitda", "gross_profit", "sales", "revenue", "cashflow", "bookvalue",
            "anl4_ebitda", "assets_curr",
        )
        ranked: list[tuple[int, float, str]] = []
        for f in self.catalog.fund[:320]:
            if is_bad_field_name(f) or is_weak_fundamental_field(f) or f.lower() == "cap":
                continue
            low = f.lower()
            pri = any(t in low for t in priority_tokens)
            ranked.append((int(pri), field_quality_score(f), f))
        ranked.sort(key=lambda x: (-x[0], -x[1]))
        for idx, (_, base_sc, f) in enumerate(ranked[:55]):
            base = 3.15 + base_sc
            if is_inverse_field(f):
                lvl = f"(0.5 - group_rank({f}/cap, subindustry))"
                lvl_delta = f"(0.5 - group_rank(ts_delta({f}, 126)/cap, subindustry))"
            else:
                lvl = f"(group_rank({f}/cap, subindustry) - 0.5)"
                lvl_delta = f"(group_rank(ts_delta({f}, 126)/cap, subindustry) - 0.5)"

            for w in (2, 5):
                for mix in (0.20, 0.25, 0.30):
                    leg = f"(-rank(ts_delta({close}, {w})))"
                    self._add(
                        out,
                        f"{lvl} + {leg}*{mix}",
                        "pass_fundamental_hybrid",
                        "pass_first",
                        base,
                    )
                    self._add(
                        out,
                        f"{lvl} * {1.0 - mix} + {leg}*{mix}",
                        "pass_fundamental_hybrid",
                        "pass_first",
                        base - 0.04,
                    )
            if idx < 28:
                for w in (2, 5):
                    for mix in (0.20, 0.25):
                        leg = f"(-rank(ts_delta({close}, {w})))"
                        self._add(
                            out,
                            f"{lvl_delta} + {leg}*{mix}",
                            "pass_fundamental_hybrid_delta",
                            "pass_first",
                            base + 0.12,
                        )
        return out

    def _pass_first_fundamental_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        vol = self._pv_var("volume")
        adv = self._pv_var("adv20")
        windows = (21, 42, 63, 126)
        fund_fields = [
            f
            for f in self.catalog.fund[:220]
            if not is_bad_field_name(f) and f.lower() != "cap" and not is_weak_fundamental_field(f)
        ]
        fund_fields.sort(key=field_quality_score, reverse=True)
        cap_level = max(0, int(self.cfg.max_pass_fundamental_level_fields))
        for idx, f in enumerate(fund_fields):
            base = field_quality_score(f)
            if is_inverse_field(f):
                lvl = f"(0.5 - group_rank({f}/cap, subindustry))"
                lvl_delta = f"(0.5 - group_rank(ts_delta({f}, {{w}})/cap, subindustry))"
                lvl_ts = f"group_neutralize(0.5 - ts_rank({f}/cap, 126), subindustry)"
            else:
                lvl = f"(group_rank({f}/cap, subindustry) - 0.5)"
                lvl_delta = f"(group_rank(ts_delta({f}, {{w}})/cap, subindustry) - 0.5)"
                lvl_ts = f"group_neutralize(ts_rank({f}/cap, 126) - 0.5, subindustry)"

            if idx < cap_level:
                self._add(out, lvl, "pass_fundamental_level", "pass_first", 2.05 + base)
            
            self._add(out, f"{lvl} * rank({vol}/(1+{adv}))", "pass_fundamental_liquid", "pass_first", 2.5 + base)
            self._add(out, f"{lvl} * rank(ts_mean({vol},21)/{adv})", "pass_fundamental_liquid", "pass_first", 2.48 + base)
            
            for w in windows:
                dw = lvl_delta.format(w=w)
                self._add(out, dw, "pass_fundamental_delta", "pass_first", 2.08 + base)
                if idx < 90:
                    self._add(out, f"{dw} * rank({vol}/{adv})", "pass_fundamental_delta_liquid", "pass_first", 2.2 + base)
                    
            if idx < 60:
                self._add(out, lvl_ts, "pass_fundamental_ts", "pass_first", 1.85 + base)
        return out

    def _analyst_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        fields = list(dict.fromkeys(self.catalog.analyst + self.catalog.model))[:120]
        groups = ("subindustry", "industry", "sector")
        for i, f in enumerate(fields):
            if is_bad_field_name(f):
                continue
            g = groups[i % len(groups)]
            self._add(out, f"group_neutralize(rank({f}), {g})", "analyst_rank", "core", 0.8 + field_quality_score(f))
            self._add(out, f"group_neutralize(ts_zscore({f}, 63), {g})", "analyst_z", "core", 0.7)
            if not self.cfg.pass_first_mode:
                self._add(out, f"trade_when(ts_delta({f}, 20)>0, ts_rank({f}/close, 60), -1)", "analyst_condition", "tanay", 1.2)
                self._add(out, f"trade_when(ts_delta({f}, 20)<=0, -rank(ts_delta(close, 5)), -1)", "analyst_condition", "tanay", 1.0)
        return out

    def _pv_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        close = self._pv_var("close")
        volume = self._pv_var("volume")
        returns = self._pv_var("returns")
        vwap = self._pv_var("vwap")
        adv = self._pv_var("adv20")
        for g in ("subindustry", "industry", "sector"):
            self._add(out, f"group_neutralize(rank({volume}/ts_mean({volume}, 60)) * rank(ts_mean({close}, 5)/{close}), {g})", "pv_reversal", "tanay", 1.8)
            self._add(out, f"group_neutralize(-rank(ts_delta({close}, 5)) * rank({volume}/{adv}), {g})", "pv_reversal", "core", 1.4)
            self._add(out, f"group_neutralize(-ts_corr({volume}, abs({returns}), 20), {g})", "pv_corr", "core", 1.0)
            self._add(out, f"group_neutralize(ts_corr(rank({volume}), rank({close}), 14), {g})", "pv_corr", "core", 0.9)
            self._add(out, f"group_neutralize(rank(({close}-{vwap})/{vwap}), {g})", "pv_vwap", "core", 0.7)
        for w in (5, 10, 21, 42):
            self._add(out, f"-ts_rank(ts_delta({close}, 5), {w})", "pv_reversal", "core", 0.6)
            self._add(out, f"group_neutralize(rank(ts_mean({returns}, {w})), subindustry)", "pv_return", "core", 0.4)
        return out

    def _quality_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        quality_tokens = ("margin", "profit", "income", "cash", "asset", "debt", "equity", "roe", "roa", "earn")
        fields = [f for f in self.catalog.fund[:260] if not is_bad_field_name(f) and any(t in f.lower() for t in quality_tokens)]
        groups = self._group_cycle(broad=True)
        for i, f in enumerate(fields[:140]):
            g = groups[i % len(groups)]
            score = 1.35 + field_quality_score(f)
            self._add(out, f"group_neutralize(ts_zscore({f}/cap, 126), {g})", "quality_zscore", "diverse", score)
            self._add(out, f"group_neutralize(ts_rank({f}/cap, 126)-0.5, {g})", "quality_level", "diverse", score + 0.10)
            self._add(out, f"group_neutralize(ts_zscore(ts_delta({f}, 126)/cap, 126), {g})", "quality_price_spread", "diverse", score + 0.05)
        return out

    def _volatility_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        returns = self._pv_var("returns")
        volume = self._pv_var("volume")
        close = self._pv_var("close")
        groups = self._group_cycle(broad=True)
        for i, g in enumerate(groups):
            score = 0.95 - 0.05 * i
            self._add(out, f"group_neutralize(-rank(ts_std_dev({returns}, 126)), {g})", "volatility_reversal", "diverse", score)
            self._add(out, f"group_neutralize(-rank(ts_corr(abs({returns}), {volume}, 126)), {g})", "volume_volatility_corr", "diverse", score + 0.05)
            self._add(out, f"group_neutralize(rank(ts_mean(({close}-ts_mean({close}, 21))/{close}, 126)), {g})", "range_volatility", "diverse", score + 0.08)
        return out

    def _volume_price_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        close = self._pv_var("close")
        vwap = self._pv_var("vwap")
        volume = self._pv_var("volume")
        adv = self._pv_var("adv20")
        returns = self._pv_var("returns")
        for g in ("subindustry", "industry", "sector"):
            for w in (10, 14, 21, 42):
                self._add(out, f"group_neutralize(rank(ts_delta({volume}, {w})/{adv}) * -rank(ts_delta({close}, {w})), {g})", "volume_price_reversal", "diverse", 1.25)
                self._add(out, f"group_neutralize(rank(({vwap}-{close})/{close}) * rank(ts_mean({volume}, {w})/{adv}), {g})", "vwap_volume", "diverse", 1.1)
                self._add(out, f"group_neutralize(-rank(ts_corr(rank({returns}), rank({volume}), {w})), {g})", "return_volume_corr", "diverse", 0.95)
        return out

    def _pass_first_pv_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        vwap = self._pv_var("vwap")
        close = self._pv_var("close")
        for g in ("subindustry", "industry", "sector"):
            self._add(out, f"group_rank({vwap}/{close}, {g})-0.5", "pass_pv_vwap", "pass_first", 2.0)
            self._add(out, f"group_neutralize(ts_rank({vwap}/{close}, 126)-0.5, {g})", "pass_pv_vwap", "pass_first", 1.95)
            self._add(out, f"group_neutralize(ts_decay_linear(zscore({vwap}/{close}), 42), {g})", "pass_pv_vwap", "pass_first", 1.90)
        return out

    def _robust_pv_family_smoothed(self) -> list[ExpressionCandidate]:
        """Smoothed PV only (no short reversal legs that often flip negative)."""
        out: list[ExpressionCandidate] = []
        vwap = self._pv_var("vwap")
        close = self._pv_var("close")
        for g in self._group_cycle(broad=True):
            self._add(
                out,
                f"group_neutralize(ts_decay_linear(zscore({vwap}/{close}), 42), {g})",
                "pass_pv_vwap",
                "pass_first",
                1.88,
            )
        return out

    def _pair_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        pool = [f for f in self.catalog.fund[:120] if not is_bad_field_name(f) and f.lower() != "cap"]
        pool = self._priority_fields((pool,), 60, ("cash", "income", "profit", "debt", "asset", "margin", "ebit", "roe", "roa"))
        self.rng.shuffle(pool)
        emitted = 0
        for i, f1 in enumerate(pool):
            for f2 in pool[i + 1 : i + 4]:
                if emitted >= max(1, self.cfg.pair_limit // 2):
                    return out
                g = ("market", "sector", "industry", "subindustry")[emitted % 4]
                self._add(out, f"group_neutralize(ts_zscore({f1}/cap, 126) - ts_zscore({f2}/cap, 126), {g})", "pair_spread", "core", 1.05 + field_quality_score(f1) + field_quality_score(f2))
                self._add(out, f"group_neutralize(ts_rank(ts_zscore({f1}/cap, 126) - ts_zscore({f2}/cap, 126), 126)-0.5, {g})", "pair_corr", "core", 0.95)
                emitted += 1
        return out

    def _alpha50_template_family(self) -> list[ExpressionCandidate]:
        path = Path(__file__).resolve().parent / self.cfg.alpha50_filename
        if not path.is_file():
            return []
        out: list[ExpressionCandidate] = []
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    formula = _sig(row.get("formula") or "")
                    mapped = self._map_formula_fields(formula)
                    if mapped:
                        score = (_to_float(row.get("Sharpe")) or 0) + (_to_float(row.get("Fitness")) or 0)
                        self._add(out, mapped, "alpha50_seed", "alpha50", score)
        except Exception as e:
            print(f"[alpha50] load failed: {e}")
        return out

    def _alpha_models_template_family(self) -> list[ExpressionCandidate]:
        """Instantiate Alpha Models.csv templates with multiple field bindings each."""
        path = Path(__file__).resolve().parent / self.cfg.alpha_models_filename
        if not path.is_file():
            print(f"[templates] missing {path.name}")
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
            for _ in range(per_t * 3):
                if got >= per_t:
                    break
                mapped = self._map_placeholders(skel)
                if not mapped or mapped in seen:
                    continue
                seen.add(mapped)
                self._add(out, mapped, "alpha_models_template", "Alpha Models.csv", score)
                got += 1
        print(
            f"[templates] {path.name} skeletons={len(skeletons)} "
            f"instantiated={len(out)} ({per_t} bindings/template max)"
        )
        return out

    def _formulaic_primitives_family(self) -> list[ExpressionCandidate]:
        """PV decay / correlation / rank motifs inspired by 101 Formulaic Alphas (SSRN), not literal copies."""
        out: list[ExpressionCandidate] = []
        g = "subindustry"
        vwap = self._pv_var("vwap")
        close = self._pv_var("close")
        high = self._pv_var("high")
        low = self._pv_var("low")
        volume = self._pv_var("volume")
        adv = self._pv_var("adv20")
        returns = "returns"
        vol60 = f"ts_mean({volume}, 60)"
        score_base = 2.75
        patterns: list[tuple[str, str, float]] = [
            (
                f"group_neutralize(rank(ts_decay_linear(ts_corr({vwap}, {volume}, 20), 10)), {g})",
                "formulaic_decay_corr",
                score_base + 0.12,
            ),
            (
                f"group_neutralize(-ts_rank(ts_delta({vwap}, 1), 10), {g})",
                "formulaic_mean_revert",
                score_base + 0.08,
            ),
            (
                f"group_neutralize(ts_rank(ts_delta({close}, 5), 63), {g})",
                "formulaic_momentum",
                score_base + 0.10,
            ),
            (
                f"group_neutralize(rank(ts_decay_linear({vwap} - ts_mean({vwap}, 20), 8)), {g})",
                "formulaic_vwap_distance",
                score_base + 0.06,
            ),
            (
                f"group_neutralize(-ts_decay_linear(zscore({vwap}/{close}), 21), {g})",
                "formulaic_vwap_mr",
                score_base + 0.14,
            ),
            (
                f"group_neutralize(rank(ts_corr({close}, {vol60}, 20)), {g})",
                "formulaic_price_liq_corr",
                score_base + 0.05,
            ),
            (
                f"group_neutralize(ts_rank(ts_corr(rank({returns}), rank({volume}), 14), 42), {g})",
                "formulaic_ret_vol_rank",
                score_base + 0.07,
            ),
            (
                f"group_neutralize(rank(({high} - {low}) / (0.001 + {close})), {g}) * -1",
                "formulaic_range_mr",
                score_base + 0.04,
            ),
        ]
        fund = [f for f in (self.catalog.fund + self.catalog.analyst) if not is_bad_field_name(f)]
        for f in self.rng.sample(fund, min(12, len(fund))) if fund else []:
            patterns.append(
                (
                    f"group_neutralize(ts_rank(ts_corr({f}, {adv}, 60), 126), industry)",
                    "formulaic_fund_corr",
                    score_base + 0.09,
                )
            )
            patterns.append(
                (
                    f"group_neutralize(-ts_zscore(ts_delta({f}, 21), 126) * rank({vol60}/{adv}), {g})",
                    "formulaic_fund_mr",
                    score_base + 0.11,
                )
            )
        seen: set[str] = set()
        for expr, family, score in patterns:
            expr = _sig(expr)
            if not expr or expr in seen:
                continue
            seen.add(expr)
            self._add(out, expr, family, "formulaic101", score)
        print(f"[formulaic] primitives instantiated={len(out)} (SSRN-inspired motifs)")
        return out

    def _external_template_family(self) -> list[ExpressionCandidate]:
        return self._alpha_models_template_family()

    def _tanay_style_family(self) -> list[ExpressionCandidate]:
        """Analyst momentum without trade_when / gating (preflight blocks sparse gates for submission hygiene)."""
        out: list[ExpressionCandidate] = []
        eps = self.catalog.best((self.catalog.analyst, self.catalog.fund), ("eps", "estimate", "earn"))
        eps_rev = self.catalog.best((self.catalog.analyst, self.catalog.model), ("revision", "eps", "rank"))
        if eps and eps_rev:
            self._add(
                out,
                f"group_neutralize(rank(ts_delta({eps_rev}, 10)) * rank({eps}/close), subindustry)",
                "analyst_dense",
                "tanay",
                1.35,
            )
            self._add(
                out,
                f"group_rank(ts_delta({eps_rev}, 5)/adv20, subindustry)-0.5",
                "analyst_dense",
                "tanay",
                1.15,
            )
        return out

    def _pv_var(self, name: str) -> str:
        low_map = {f.lower(): f for f in self.catalog.pv}
        return low_map.get(name, name)

    def _map_placeholders(self, expr: str) -> str | None:
        expr = _sig(expr)
        fund = self.catalog.fund + self.catalog.other
        pools = {
            "x": fund + self.catalog.analyst,
            "y": fund + self.catalog.model,
            "a": self.catalog.fund + self.catalog.analyst,
            "b": self.catalog.fund + self.catalog.model,
            "c": fund,
            "f1": self.catalog.fund + self.catalog.analyst,
            "f2": self.catalog.fund + self.catalog.model,
            "f3": fund + self.catalog.other,
            "y1": self.catalog.fund + self.catalog.model,
            "y2": fund + self.catalog.analyst,
            "analyst": self.catalog.analyst + self.catalog.model,
            "sentiment": self.catalog.sent + self.catalog.analyst,
        }
        for ph in sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", expr))):
            pool = [f for f in pools.get(ph, []) if not is_bad_field_name(f)]
            if not pool:
                return None
            expr = expr.replace("{" + ph + "}", self.rng.choice(pool))
        return expr

    def _map_formula_fields(self, formula: str) -> str | None:
        if not formula:
            return None
        low = formula.lower()
        if any(re.search(rf"\b{fn}\s*\(", low) for fn in BLOCKED_FUNCTIONS):
            return None
        mapped = formula
        ids = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", low))
        allowed = FUNCTIONS | GROUPS | BASE_VARS | {"d", "a", "b", "c", "range", "rettype"}
        for ident in sorted(ids, key=len, reverse=True):
            if ident in allowed or ident in {x.lower() for x in self.catalog.ids}:
                continue
            repl = self._field_replacement_for(ident)
            if not repl:
                return None
            mapped = re.sub(rf"\b{re.escape(ident)}\b", repl, mapped, flags=re.I)
        return mapped

    def _field_replacement_for(self, ident: str) -> str | None:
        tokens = tuple(t for t in re.split(r"[_\W]+", ident.lower()) if len(t) >= 3)
        if ident.startswith(("mdf_", "fnd", "fn_", "fund")):
            return self.catalog.best((self.catalog.fund, self.catalog.other), tokens)
        if ident.startswith(("fam_", "est_", "analyst")):
            return self.catalog.best((self.catalog.analyst, self.catalog.model, self.catalog.fund), tokens)
        return self.catalog.best((self.catalog.fund, self.catalog.analyst, self.catalog.other), tokens)

    def _score_expression(self, c: ExpressionCandidate) -> float:
        score = c.score
        low = c.expression.lower()

        # Strongly prefer slow, stable, cross-sectional structures.
        if "ts_mean(" in low:
            score += 0.90
        if "ts_zscore(" in low:
            score += 0.80
        if "ts_rank(" in low:
            score += 0.55
        if "ts_decay_linear(" in low:
            score += 0.45
        if "group_neutralize(" in low:
            score += 0.40
        if "group_rank(" in low:
            score += 0.22

        # Penalize raw PV ratios and short-horizon reversal noise.
        if "zscore(vwap/close)" in low:
            score -= 1.60
        if "vwap/close" in low and not any(tok in low for tok in ("ts_decay_linear(", "ts_rank(", "ts_mean(", "group_neutralize(", "group_rank(")):
            score -= 0.95
        for match in re.finditer(r"ts_delta\(([^,]+),\s*(\d+)\)", low):
            arg = match.group(1)
            try:
                ww = int(match.group(2))
            except Exception:
                continue
            if ww < 10:
                if any(x in arg for x in ("close", "vwap", "high", "low", "volume", "returns")):
                    score -= 0.30
                else:
                    score -= 1.10
            elif ww < 21:
                score -= 0.45
            elif ww >= 126:
                score += 0.45
            elif ww >= 63:
                score += 0.20

        ranks = [int(x) for x in re.findall(r"ts_rank\([^,]+,\s*(\d+)\)", low)]
        if ranks:
            if max(ranks) >= 126:
                score += 0.85
            elif max(ranks) >= 63:
                score += 0.45
            if min(ranks) < 10:
                score -= 0.25

        deltas = [int(x) for x in re.findall(r"ts_mean\([^,]+,\s*(\d+)\)", low)]
        if deltas and max(deltas) >= 126:
            score += 0.35

        # Reward liquidity smoothed exposure.
        vol_sym = str(self._pv_var("volume")).lower()
        adv_sym = str(self._pv_var("adv20")).lower()
        if vol_sym in low and adv_sym in low:
            score += 0.70
        if "rank(ts_mean(volume,63)/adv20)" in low or "rank(ts_mean(volume,126)/adv20)" in low:
            score += 0.95

        # Broader neutralization tends to generalize better.
        if ", market)" in low:
            score += 0.40
        elif ", sector)" in low:
            score += 0.24
        elif ", industry)" in low:
            score += 0.10
        elif ", subindustry)" in low:
            score -= 0.04

        if c.source == "pass_first":
            score += 1.20
        if c.source == "robust":
            score += 1.00
        if c.source == "hybrid":
            score += 0.60
        if c.family.startswith("pass_fundamental"):
            score += 0.85
        if c.family.startswith("pass_pv"):
            score += 0.30
        if c.family.startswith("regime_shift"):
            score += 0.75
        if c.family.startswith("quality_"):
            score += 0.35
        if c.family.startswith("volatility_"):
            score -= 0.20
        if c.family.startswith("pair_"):
            score += 0.20

        # Penalize over-processing and deep expression trees.
        score -= 0.28 * self._nesting_depth(low)
        score -= 0.14 * max(0, self._function_calls(low) - self.cfg.max_nested_functions)

        ids = re.findall(r"\b[a-z_][a-z0-9_]*\b", low)
        for fid in ids:
            score += min(field_quality_score(fid) * 0.20, 0.50)
        return score

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
        return len(re.findall(r"\b[a-z_][a-z0-9_]*\s*\(", expr))


    def _priority_fields(self, pools: Iterable[list[str]], limit: int, tokens: tuple[str, ...] = ()) -> list[str]:
        fields: list[str] = []
        for pool in pools:
            fields.extend(pool)
        deduped = list(
            dict.fromkeys(
                [f for f in fields if f and not is_bad_field_name(f) and not is_weak_fundamental_field(f)]
            )
        )
        if not deduped:
            return []

        def score_field(f: str) -> float:
            s = field_quality_score(f)
            low = f.lower()
            if tokens and any(t in low for t in tokens):
                s += 1.0
            if any(x in low for x in ("capex", "ebitda", "operating", "income", "profit", "cash", "asset", "debt", "margin", "roe", "roa", "eps")):
                s += 0.35
            if low.startswith(("fnd6_", "fn_", "mdf_", "fundamental")):
                s += 0.20
            return s

        deduped.sort(key=score_field, reverse=True)
        return deduped[: max(1, limit)]

    def _group_cycle(self, *, broad: bool = False) -> tuple[str, ...]:
        return ("market", "sector", "industry", "subindustry") if broad else ("sector", "industry", "subindustry", "market")

    def _robust_fundamental_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        fields = self._priority_fields((self.catalog.fund, self.catalog.model), 160, ("capex", "ebitda", "income", "profit", "asset", "cash", "debt", "margin", "operating", "roe", "roa"))
        groups = self._group_cycle(broad=True)
        volume = self._pv_var("volume")
        adv = self._pv_var("adv20")
        for i, f in enumerate(fields):
            if f.lower() == "cap":
                continue
            g = groups[i % len(groups)]
            base = f"{f}/cap"
            score = 2.0 + field_quality_score(f)
            if is_inverse_field(f):
                z_expr = f"-ts_zscore({base}, 126)"
                lvl_expr = f"-ts_mean({base}, 126)"
                delta_expr = f"(0.5 - group_rank(ts_delta({f}, {{w}})/cap, {g}))"
            else:
                z_expr = f"ts_zscore({base}, 126)"
                lvl_expr = f"ts_mean({base}, 126)"
                delta_expr = f"(group_rank(ts_delta({f}, {{w}})/cap, {g}) - 0.5)"

            if i < 120:
                self._add(out, f"group_neutralize({z_expr}, {g})", "pass_fundamental_ts", "robust", score)
                self._add(out, f"group_neutralize({lvl_expr}, {g})", "pass_fundamental_level", "robust", score + 0.05)
            if i < 90:
                self._add(out, delta_expr.format(w=126), "pass_fundamental_delta", "robust", score + 0.20)
                self._add(out, delta_expr.format(w=252), "pass_fundamental_delta", "robust", score + 0.30)
            if i < 70:
                self._add(out, f"{delta_expr.format(w=252)} * rank(ts_mean({volume}, 63)/{adv})", "pass_fundamental_delta_liquid", "robust", score + 0.45)
                self._add(out, f"group_neutralize(rank(ts_mean({volume}, 63)/{adv}) * {z_expr}, {g})", "pass_fundamental_liquid", "robust", score + 0.35)
        return out

    def _broad_fundamental_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        fields = self._priority_fields((self.catalog.fund, self.catalog.other), 120, ("sales", "revenue", "income", "profit", "ebit", "cashflow", "assets", "debt", "equity", "book", "margin", "capex"))
        groups = self._group_cycle(broad=False)
        for i, f in enumerate(fields):
            if f.lower() == "cap":
                continue
            g = groups[i % len(groups)]
            base = f"{f}/cap"
            score = 1.0 + field_quality_score(f)
            self._add(out, f"group_neutralize(rank(ts_mean({base}, 126)), {g})", "pass_fundamental_level", "explore", score)
            self._add(out, f"group_neutralize(ts_rank({base}, 126)-0.5, {g})", "pass_fundamental_ts", "explore", score + 0.05)
            if i < 40:
                self._add(out, f"group_neutralize(ts_zscore({base}, 126), {g})", "pass_fundamental_ts", "explore", score + 0.10)
        return out

    def _robust_analyst_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        fields = self._priority_fields((self.catalog.analyst, self.catalog.model), 100, ("estimate", "revision", "forecast", "target", "rating", "eps", "guidance"))
        groups = self._group_cycle(broad=True)
        for i, f in enumerate(fields):
            if is_bad_field_name(f):
                continue
            g = groups[i % len(groups)]
            score = 1.0 + field_quality_score(f)
            self._add(out, f"group_neutralize(ts_zscore({f}, 126), {g})", "analyst_dense", "robust", score)
            self._add(out, f"group_neutralize(ts_rank({f}, 63)-0.5, {g})", "analyst_dense", "robust", score + 0.05)
            if i < 60:
                self._add(out, f"group_neutralize(rank(ts_delta({f}, 63)), {g})", "analyst_dense", "robust", score + 0.10)
        return out

    def _robust_pv_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        close = self._pv_var("close")
        volume = self._pv_var("volume")
        vwap = self._pv_var("vwap")
        returns = self._pv_var("returns")
        adv = self._pv_var("adv20")
        groups = self._group_cycle(broad=True)
        for i, g in enumerate(groups):
            score = 1.35 + (0.15 * (len(groups) - i))
            self._add(out, f"group_neutralize(ts_decay_linear(zscore({vwap}/{close}), 42), {g})", "pass_pv_vwap", "robust", score)
            self._add(out, f"group_neutralize(ts_rank({vwap}/{close}, 126)-0.5, {g})", "pass_pv_vwap", "robust", score + 0.10)
            self._add(out, f"group_neutralize(rank(ts_mean({volume}, 63)/{adv}) * -rank(ts_delta({close}, 42)), {g})", "pass_pv", "robust", score + 0.05)
            self._add(out, f"group_neutralize(rank(ts_mean({volume}, 126)/{adv}) * -rank(ts_corr(abs({returns}), {volume}, 63)), {g})", "pass_pv", "robust", score + 0.08)
        return out

    def _broad_pv_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        close = self._pv_var("close")
        volume = self._pv_var("volume")
        vwap = self._pv_var("vwap")
        adv = self._pv_var("adv20")
        returns = self._pv_var("returns")
        groups = self._group_cycle(broad=False)
        for i, g in enumerate(groups):
            score = 0.9 + 0.08 * i
            self._add(out, f"group_neutralize(ts_rank({vwap}/{close}, 126)-0.5, {g})", "pass_pv_vwap", "explore", score)
            self._add(out, f"group_neutralize(ts_decay_linear(zscore({vwap}/{close}), 21), {g})", "pass_pv_vwap", "explore", score + 0.05)
            self._add(out, f"group_neutralize(rank(ts_mean({volume}, 126)/{adv}) * -rank(ts_delta({close}, 63)), {g})", "pass_pv", "explore", score + 0.10)
            self._add(out, f"group_neutralize(-rank(ts_corr(rank({returns}), rank({volume}), 126)), {g})", "pass_pv", "explore", score + 0.08)
        return out

    def _liquidity_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        volume = self._pv_var("volume")
        adv = self._pv_var("adv20")
        close = self._pv_var("close")
        groups = self._group_cycle(broad=True)
        for i, g in enumerate(groups):
            self._add(out, f"group_neutralize(rank(ts_mean({volume}, 21)/{adv}), {g})", "pass_pv", "explore", 0.95 - 0.05 * i)
            self._add(out, f"group_neutralize(rank(ts_mean({volume}, 63)/{adv}), {g})", "pass_pv", "explore", 1.00 - 0.05 * i)
            self._add(out, f"group_neutralize(rank(ts_mean({volume}, 126)/{adv}) * -rank(ts_delta({close}, 63)), {g})", "pass_pv", "explore", 1.05 - 0.05 * i)
        return out

    def _hybrid_stability_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        fields = self._priority_fields((self.catalog.fund, self.catalog.model, self.catalog.analyst), 90, ("capex", "ebitda", "income", "profit", "asset", "debt", "margin", "estimate", "revision"))
        volume = self._pv_var("volume")
        adv = self._pv_var("adv20")
        groups = self._group_cycle(broad=True)
        for i, f in enumerate(fields[:60]):
            if f.lower() == "cap":
                continue
            g = groups[i % len(groups)]
            base = f"{f}/cap"
            score = 1.6 + field_quality_score(f)
            self._add(out, f"(group_rank(ts_delta({f}, 252)/cap, {g})-0.5)*rank(ts_mean({volume}, 63)/{adv})", "pass_fundamental_delta_liquid", "hybrid", score)
            self._add(out, f"group_neutralize(ts_zscore({base}, 126) * rank(ts_mean({volume}, 126)/{adv}), {g})", "pass_fundamental_liquid", "hybrid", score + 0.10)
            self._add(out, f"group_neutralize(rank(ts_delta({f}, 126)/cap) + 0.25*rank(ts_mean({volume}, 63)/{adv}), {g})", "pass_fundamental_liquid", "hybrid", score + 0.05)
        return out

    def _regime_shift_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        fields = self._priority_fields((self.catalog.fund, self.catalog.model, self.catalog.analyst), 100, ("ebitda", "cash", "income", "profit", "margin", "roe", "roa", "estimate", "revision", "forecast"))
        volume = self._pv_var("volume")
        adv = self._pv_var("adv20")
        groups = self._group_cycle(broad=True)
        for i, f in enumerate(fields[:70]):
            if f.lower() == "cap":
                continue
            g = groups[i % len(groups)]
            base = f"{f}/cap"
            score = 1.75 + field_quality_score(f)
            self._add(out, f"group_neutralize(ts_zscore({base}, 126) - ts_zscore({base}, 252), {g})", "regime_shift", "submission", score)
            self._add(out, f"group_neutralize(ts_rank(ts_delta({f}, 252)/cap, 126)-0.5, {g})", "regime_shift", "submission", score + 0.10)
            self._add(out, f"group_neutralize(rank(ts_mean({volume}, 126)/{adv}) * (ts_zscore({base}, 126) - ts_zscore({base}, 252)), {g})", "regime_shift_liquid", "submission", score + 0.20)
        return out

    def _cross_hybrid_family(self) -> list[ExpressionCandidate]:
        out: list[ExpressionCandidate] = []
        close = self._pv_var("close")
        vwap = self._pv_var("vwap")
        volume = self._pv_var("volume")
        adv = self._pv_var("adv20")
        fields = self._priority_fields((self.catalog.fund, self.catalog.analyst, self.catalog.model), 70, ("ebitda", "cash", "income", "profit", "estimate", "revision", "forecast", "margin"))
        groups = self._group_cycle(broad=False)
        for i, f in enumerate(fields[:50]):
            if f.lower() == "cap":
                continue
            g = groups[i % len(groups)]
            base = f"{f}/cap"
            score = 1.45 + field_quality_score(f)
            self._add(out, f"group_neutralize(ts_zscore({base}, 126) * rank(ts_mean({volume}, 126)/{adv}), {g})", "pass_fundamental_liquid", "hybrid", score)
            self._add(out, f"group_neutralize(ts_rank({base}, 126)-0.5 + 0.25*ts_decay_linear(zscore({vwap}/{close}), 21), {g})", "pass_fundamental_ts", "hybrid", score + 0.05)
            self._add(out, f"group_neutralize(ts_zscore({base}, 252) - ts_zscore({base}, 126), {g})", "regime_shift", "hybrid", score + 0.10)
        return out

    @staticmethod
    def _candidate_priority_key(c: ExpressionCandidate) -> tuple[int, float, int]:
        fam = (c.family or "").lower()
        if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
            tier = 0
        elif fam.startswith("pass_fundamental_hybrid"):
            tier = 1
        elif fam.startswith("pass_fundamental") or fam.startswith("pass_pv"):
            tier = 1
        elif fam.startswith("near_pass_variant"):
            tier = 8
        else:
            tier = 4
        return (tier, -float(c.score or 0.0), len(c.expression or ""))

    def _apply_family_quota(self, candidates: list[ExpressionCandidate], budget: int) -> list[ExpressionCandidate]:
        max_per_family = max(1, int(max(1, budget) * self.cfg.max_family_share))
        near_cap = max(1, int(max(1, budget) * float(getattr(self.cfg, "near_pass_max_family_share", 0.10))))
        template_cap = max(64, int(max(1, budget) * 0.22))
        family_caps: dict[str, int] = {
            "near_pass_variant": near_cap,
            "alpha_models_template": template_cap,
        }
        formulaic_cap = max(48, template_cap // 2)
        counts: Counter[str] = Counter()
        out: list[ExpressionCandidate] = []
        for c in candidates:
            if c.family.startswith("formulaic_"):
                cap = formulaic_cap
            else:
                cap = family_caps.get(c.family, max_per_family)
            if counts[c.family] >= cap:
                continue
            out.append(c)
            counts[c.family] += 1
            if len(out) >= budget:
                break
        if len(out) < budget:
            seen = {c.expression for c in out}
            for c in candidates:
                if c.expression in seen:
                    continue
                out.append(c)
                if len(out) >= budget:
                    break
        return out


# ---------------------------- profiles ----------------------------

class ProfileSelector:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.alpha50_profiles = self._load_alpha50_profiles()

    def payloads_for(self, candidates: list[ExpressionCandidate], max_payloads: int) -> list[dict]:
        payloads: list[dict] = []
        seen: set[tuple[str, str]] = set()
        pending_variants: list[tuple[ExpressionCandidate, int, str, dict]] = []
        for c in candidates:
            variants = self._variants_for(c)
            if not variants:
                continue
            name, settings = variants[0]
            added = self._append_payload(payloads, seen, c, 0, name, settings)
            if len(payloads) >= max_payloads:
                return payloads
            if added:
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
            "neutralization": "SUBINDUSTRY",
            "truncation": 0.08,
            "pasteurization": "ON",
            "unitHandling": "VERIFY",
            "nanHandling": "ON",
            "language": "FASTEXPR",
            "visualization": False,
        }

    def _variants_for(self, c: ExpressionCandidate) -> list[tuple[str, dict]]:
        low = c.expression.lower()
        base = self._base()
        if ", market)" in low:
            neutral = "MARKET"
        elif ", sector)" in low:
            neutral = "SECTOR"
        elif ", industry)" in low:
            neutral = "INDUSTRY"
        else:
            neutral = "SUBINDUSTRY"
        base["neutralization"] = neutral
        if c.family.startswith("pass_fundamental_hybrid"):
            base.update({"decay": 1, "truncation": 0.045, "nanHandling": "ON"})
        elif c.family.startswith("pass_fundamental_level"):
            base.update({"decay": 8, "truncation": 0.03, "nanHandling": "ON"})
        elif c.family.startswith("pass_fundamental_liquid") or c.family.startswith("pass_fundamental_delta_liquid"):
            base.update({"decay": 5, "truncation": 0.035, "nanHandling": "ON"})
        elif c.family.startswith("pass_fundamental_delta"):
            base.update({"decay": 6, "truncation": 0.035, "nanHandling": "ON"})
        elif c.family.startswith("pass_fundamental_ts"):
            base.update({"decay": 10, "truncation": 0.03, "nanHandling": "ON"})
        elif c.family.startswith("pass_fundamental"):
            base.update({"decay": 6, "truncation": 0.035, "nanHandling": "ON"})
        elif c.family.startswith("pass_pv"):
            # Use slower smoothing than the old PV defaults to reduce turnover and weight spikes.
            base.update({"decay": 6, "truncation": 0.035, "nanHandling": "ON"})
        elif c.family.startswith("pv"):
            # PV signals can be spiky; use stronger truncation to reduce weight concentration
            base.update({"decay": 1, "truncation": 0.06, "nanHandling": "ON"})
        elif "condition" in c.family:
            base.update({"decay": 3, "truncation": 0.08, "nanHandling": "ON"})
        elif "mean" in c.family or "z" in c.family:
            base.update({"decay": 10, "truncation": 0.04, "nanHandling": "ON"})
        elif c.family.startswith("near_pass_variant"):
            base.update({"decay": max(4, int(self.cfg.near_pass_primary_decay)), "truncation": 0.035, "nanHandling": "ON"})
        elif c.family.startswith("alpha_models_template") or c.family.startswith("formulaic_"):
            base.update({"decay": 6, "truncation": 0.04, "nanHandling": "ON"})
        elif "analyst_dense" in c.family:
            base.update({"decay": 5, "truncation": 0.055, "nanHandling": "ON"})
        elif c.family.startswith("regime_shift") or c.source in ("robust", "hybrid", "submission"):
            base.update({"decay": 8, "truncation": 0.035, "nanHandling": "ON"})
        else:
            base.update({"decay": 6, "truncation": 0.04, "nanHandling": "ON"})
        variants = [(f"{c.family}:primary", dict(base))]
        if c.family.startswith("near_pass_variant"):
            for decay, trunc in ((5, 0.045), (8, 0.04)):
                rescue = dict(base)
                rescue.update({"decay": decay, "truncation": trunc, "nanHandling": "ON"})
                variants.append((f"{c.family}:rescue_d{decay}_t{trunc}", rescue))
            return variants
        if c.family.startswith("alpha_models_template") or c.family.startswith("formulaic_"):
            for decay, trunc in ((4, 0.035), (8, 0.045), (10, 0.03)):
                alt = dict(base)
                alt.update({"decay": decay, "truncation": trunc, "nanHandling": "ON"})
                variants.append((f"{c.family}:d{decay}_t{trunc}", alt))
            if c.score >= 2.0:
                variants.append((f"{c.family}:alpha50_prior", self._alpha50_like(base, c)))
            return variants
        if c.score >= 2.0:
            variants.append((f"{c.family}:alpha50_prior", self._alpha50_like(base, c)))
        if c.score >= 3.0 and len(variants) < 3:
            alt = dict(base)
            alt["decay"] = 1 if int(base.get("decay", 0)) != 1 else 10
            alt["truncation"] = 0.01 if float(base.get("truncation", 0.04)) >= 0.02 else 0.03
            variants.append((f"{c.family}:alt", alt))
        if c.family.startswith("pass_pv") and len(variants) < 3:
            conservative = dict(base)
            conservative["decay"] = max(8, int(base.get("decay", 4)) + 4)
            conservative["truncation"] = min(0.035, float(base.get("truncation", 0.045)))
            variants.append((f"{c.family}:conservative", conservative))
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
        for rec in records:
            expr = _sig(rec.get("expression") or "")
            if not expr:
                continue
            sh = float(rec.get("sharpe") or 0.0)
            base_score = 2.5 + (sh - self.cfg.near_pass_min_sharpe)
            variants = self._variants_for(expr)
            picked = 0
            for v in variants:
                if picked >= int(self.cfg.near_pass_max_variants_per_seed):
                    break
                if v in tried_exact or v == expr:
                    continue
                ok, _ = self.validator.validate(v)
                if not ok:
                    continue
                self._emit(out, v, "near_pass_variant", "near_pass", base_score)
                picked += 1
        return out

    def _emit(self, out: list[ExpressionCandidate], expr: str, family: str, source: str, score: float) -> None:
        out.append(ExpressionCandidate(_sig(expr), family, source, score))

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
            if v and v != s:
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
                add(f"({s})*rank(ts_mean(volume, {w})/(1+adv20))")

        # For fundamental-style seeds, add a smoother long-window sibling.
        if "/cap" in low or "cap)" in low:
            for w in (63, 126, 252):
                add(f"group_neutralize(ts_zscore(({s}), {w}), subindustry)")
                add(f"group_neutralize(ts_mean(({s}), {w}), sector)")

        # Blend with a slow price-reversion leg (winning pattern in hopeful queue).
        if "vwap" not in low and "ts_delta(close" not in low:
            for w in (2, 5):
                add(f"({s})+(-rank(ts_delta({close}, {w})))*0.25")
                add(f"({s})*0.75+(-rank(ts_delta({close}, {w})))*0.25")
            for w in (21, 42):
                add(f"({s})*0.8+(-rank(ts_delta({close}, {w})))*0.2")
                add(f"({s})*0.7+rank(ts_mean(volume,63)/adv20)*0.3")

        # Sign flip as fallback.
        if low.startswith("-(") and low.endswith(")"):
            add(s[2:-1])
        elif low.startswith("-"):
            add(s[1:])
        else:
            add(f"-({s})")

        seen: set[str] = set()
        unique: list[str] = []
        for v in results:
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
    ) -> None:
        self.cfg = cfg
        self.tried_exact = tried_exact
        self.tried_payload_keys = tried_payload_keys
        self.near_pass_expressions = near_pass_expressions
        self.failed_cluster = failed_cluster
        self.history_pools = history_pools
        self.top_field_lookup = top_field_lookup
        self.tried_metrics = tried_metrics or {}
        self.novelty_index = novelty_index

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
        dropped_samples: list[tuple[str, str]] = []
        max_depth = int(self.cfg.prescreen_max_nesting_depth)
        max_fcalls = int(self.cfg.prescreen_max_function_calls)
        cluster_threshold = float(self.cfg.prescreen_cluster_max_avg_sharpe)
        cluster_min_samples = int(self.cfg.prescreen_cluster_min_samples)
        kept_tokens: list[set[str]] = []
        kept_exprs: set[str] = set()
        kept_structures: Counter[str] = Counter()

        for payload in payloads:
            expr = _sig(payload.get("regular") or "")
            if not expr:
                reject_counts["empty"] += 1
                continue
            if self.cfg.prescreen_block_already_simulated:
                payload_key = _payload_fingerprint(expr, payload.get("settings") if isinstance(payload, dict) else {})
                if payload_key in self.tried_payload_keys:
                    reject_counts["already_simulated_payload"] += 1
                    self._record_drop(dropped_samples, "already_simulated_payload", expr)
                    continue
                if expr in self.tried_exact:
                    can_retry_settings = (
                        self.cfg.prescreen_allow_near_pass_settings_retry
                        and expr in self.near_pass_expressions
                    )
                    if not can_retry_settings:
                        reject_counts["already_simulated_expr"] += 1
                        self._record_drop(dropped_samples, "already_simulated_expr", expr)
                        continue
            if self.cfg.prescreen_skip_negative_sharpe:
                hist = self.tried_metrics.get(expr)
                if isinstance(hist, dict):
                    sh = hist.get("sharpe")
                    if sh is not None and float(sh) < float(self.cfg.prescreen_negative_sharpe_floor):
                        reject_counts["negative_sharpe_history"] += 1
                        self._record_drop(dropped_samples, "negative_sharpe_history", expr)
                        continue
            if self._nesting_depth(expr) > max_depth:
                reject_counts["too_deep_nesting"] += 1
                self._record_drop(dropped_samples, "too_deep_nesting", expr)
                continue
            if self._function_calls(expr) > max_fcalls:
                reject_counts["too_many_operators"] += 1
                self._record_drop(dropped_samples, "too_many_operators", expr)
                continue
            fam = str((payload.get("meta") or {}).get("family") or "").lower()
            skip_toxic = bool(getattr(self.cfg, "prescreen_skip_toxic_for_near_pass", True)) and (
                fam.startswith("near_pass_variant")
                or fam.startswith("alpha_models_template")
                or fam.startswith("formulaic_")
            )
            if not skip_toxic:
                toxic_cap = float(self.cfg.prescreen_max_toxic_similarity)
                toxic_sim = self.history_pools.max_similarity(expr, "toxic", early_exit_at=toxic_cap)
                if toxic_sim >= toxic_cap:
                    reject_counts[f"toxic_history>={toxic_cap:.2f}"] += 1
                    self._record_drop(dropped_samples, "toxic_history", expr)
                    continue
            template_like = fam.startswith("alpha_models_template") or fam.startswith("formulaic_")
            near_pass_fam = fam.startswith("near_pass_variant")
            skip_weak = (
                (template_like and bool(getattr(self.cfg, "prescreen_skip_weak_for_template", True)))
                or (near_pass_fam and bool(getattr(self.cfg, "prescreen_skip_weak_for_near_pass", True)))
            )
            if not skip_weak:
                max_sim = self._sim_threshold_for(payload)
                weak_sim = self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=max_sim)
                if weak_sim >= max_sim:
                    reject_counts[f"high_self_corr>={max_sim:.2f}"] += 1
                    self._record_drop(dropped_samples, "high_self_corr", expr)
                    continue
                if not near_pass_fam:
                    near_cap = max(max_sim, float(self.cfg.prescreen_near_pass_similarity) - 0.04)
                    near_sim = self.history_pools.max_similarity(expr, "near_pass", early_exit_at=near_cap)
                    if near_sim >= near_cap:
                        reject_counts[f"near_pass_history>={near_cap:.2f}"] += 1
                        self._record_drop(dropped_samples, "near_pass_history", expr)
                        continue
            if self.cfg.novelty_enabled and self.novelty_index is not None:
                novelty_strict = self.cfg.novelty_strictness
                if template_like and bool(getattr(self.cfg, "prescreen_skip_novelty_for_template", True)):
                    novelty_strict = "balanced"
                reason = self.novelty_index.reject_reason(expr, strictness=novelty_strict)
                if reason:
                    reject_counts[reason] += 1
                    self._record_drop(dropped_samples, reason, expr)
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
                    reject_counts[f"novelty_intrabatch_shape_quota>{same_shape_limit}"] += 1
                    self._record_drop(dropped_samples, "novelty_intrabatch_shape_quota", expr)
                    continue
            if not skip_intrabatch:
                intra_max = self._intrabatch_threshold_for(payload)
                intra_sim = self._max_token_similarity(toks, kept_tokens, early_exit_at=intra_max)
                if intra_sim >= intra_max:
                    reject_counts[f"high_intrabatch_self_corr>={intra_max:.2f}"] += 1
                    self._record_drop(dropped_samples, "high_intrabatch_self_corr", expr)
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
                        reject_counts["low_sharpe_cluster"] += 1
                        self._record_drop(dropped_samples, "low_sharpe_cluster", expr)
                        continue
            kept.append(payload)
            kept_exprs.add(expr)
            kept_tokens.append(toks)
            kept_structures[struct] += 1
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
        """Per-family weak-pool cap; None = skip weak-fail pool (templates / near-pass)."""
        fam = str((payload.get("meta") or {}).get("family") or "").lower()
        if fam.startswith("near_pass_variant") and bool(
            getattr(self.cfg, "prescreen_skip_weak_for_near_pass", True)
        ):
            return None
        if (
            fam.startswith("alpha_models_template")
            or fam.startswith("formulaic_")
            or fam == "external_template"
        ) and bool(getattr(self.cfg, "prescreen_skip_weak_for_template", True)):
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
        if fam.startswith("near_pass_variant"):
            return max(base, float(self.cfg.prescreen_near_pass_similarity))
        return base

    def _intrabatch_threshold_for(self, payload: dict) -> float:
        """Keep the current batch from collapsing into one near-duplicate cluster."""
        base = float(self.cfg.prescreen_intrabatch_similarity)
        fam = str((payload.get("meta") or {}).get("family") or "").lower() if isinstance(payload, dict) else ""
        if fam.startswith("near_pass_variant"):
            return max(base, min(float(self.cfg.prescreen_near_pass_similarity), base + 0.10))
        return base

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
        key_fn = rank_key or self._fine_rank_key
        ordered = sorted(payloads, key=key_fn)
        selected: list[dict] = []
        selected_tokens: list[set[str]] = []
        shape_counts: Counter[str] = Counter()
        seen_expr: set[str] = set()

        for payload in ordered:
            if len(selected) >= target_n:
                break
            expr = _sig(payload.get("regular") or "")
            if not expr:
                reject_counts["fine_empty"] += 1
                continue
            if expr in seen_expr:
                reject_counts["fine_duplicate_expr"] += 1
                self._record_drop(dropped_samples, "fine_duplicate_expr", expr)
                continue
            struct = _structure_signature(expr)
            if self.cfg.novelty_enabled and shape_limit >= 0 and shape_counts[struct] >= shape_limit:
                reject_counts[f"fine_shape_quota>{shape_limit}"] += 1
                self._record_drop(dropped_samples, "fine_shape_quota", expr)
                continue
            toks = set(re.findall(r"[a-z_]+|\d+", expr.lower()))
            if intrabatch_cap_override is not None:
                intra_max = float(intrabatch_cap_override)
            else:
                intra_max = self._intrabatch_threshold_for(payload)
            intra_sim = self._max_token_similarity(toks, selected_tokens, early_exit_at=intra_max)
            if intra_sim >= intra_max:
                reject_counts[f"fine_intrabatch>={intra_max:.2f}"] += 1
                self._record_drop(dropped_samples, "fine_intrabatch", expr)
                continue
            weak_cap = self._weak_history_cap_for(payload, hist_cap)
            if weak_cap is not None:
                hist_sim = self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=weak_cap)
                if hist_sim >= weak_cap:
                    reject_counts[f"fine_history>={weak_cap:.2f}"] += 1
                    self._record_drop(dropped_samples, "fine_history", expr)
                    continue
            selected.append(payload)
            seen_expr.add(expr)
            selected_tokens.append(toks)
            shape_counts[struct] += 1

        return selected, reject_counts, dropped_samples

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
        seen = {_sig(p.get("regular") or "") for p in already}
        seen.discard("")
        out: list[dict] = []
        for payload in sorted(coarse, key=self._fine_rank_key_bulk):
            if len(out) >= need:
                break
            expr = _sig(payload.get("regular") or "")
            if not expr or expr in seen:
                continue
            cap = self._weak_history_cap_for(payload, history_similarity_cap)
            if cap is not None:
                if self.history_pools.max_similarity(expr, "weak_fail", early_exit_at=cap) >= cap:
                    continue
            out.append(payload)
            seen.add(expr)
        return out

    def _fine_rank_key_bulk(self, payload: dict) -> tuple[int, float, int]:
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        fam = str(meta.get("family") or "").lower()
        src = str(meta.get("source") or "").lower()
        sc = float(meta.get("candidate_score") or 0.0)
        variant = int(meta.get("variant") or 0)
        if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
            tier = 0
        elif fam.startswith("near_pass_variant"):
            tier = 7
        elif fam.startswith("pass_fundamental_hybrid"):
            tier = 1
        elif src in ("robust", "hybrid", "submission") or fam.startswith("regime_shift"):
            tier = 2
        elif src == "pass_first" or fam.startswith("pass_pv") or fam.startswith("pass_fundamental"):
            tier = 1
        elif "liquid" in fam or "delta" in fam:
            tier = 2
        elif fam.startswith("pass_fundamental_ts"):
            tier = 3
        else:
            tier = 4
        return (tier, -sc, variant)

    def _fine_rank_key(self, payload: dict) -> tuple[int, float, int]:
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        fam = str(meta.get("family") or "").lower()
        src = str(meta.get("source") or "").lower()
        sc = float(meta.get("candidate_score") or 0.0)
        variant = int(meta.get("variant") or 0)
        if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
            tier = 0
        elif fam.startswith("pass_fundamental_hybrid"):
            tier = 1
        elif src == "pass_first" or fam.startswith("pass_pv") or fam.startswith("pass_fundamental"):
            tier = 1
        elif src in ("robust", "hybrid", "submission") or fam.startswith("regime_shift"):
            tier = 2
        elif "liquid" in fam or "delta" in fam:
            tier = 2
        elif fam.startswith("pass_fundamental_ts"):
            tier = 3
        elif fam.startswith("near_pass_variant"):
            tier = 8
        else:
            tier = 4
        return (tier, -sc, variant)


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
        self._simulate_snapshot_exprs: set[str] = set()
        self._simulate_snapshot_alpha_ids: set[str] = set()
        self._failed_expressions: set[str] = set()
        self._passed_expressions: set[str] = set()
        self._tried_metrics: dict[str, dict[str, float | None]] = {}
        self._failed_cluster: dict[tuple[str, str], list[float]] = {}
        self._near_pass_records: list[dict[str, Any]] = []
        self._near_pass_expression_set: set[str] = set()
        self._novelty_index = NoveltyIndex()
        self._last_prescreen_coarse_count: int = 0
        self._feedback_blocked_operators: set[str] = set()
        self._feedback_blocked_variables: set[str] = set()
        ctx = ssl.create_default_context()
        if not cfg.tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if cfg.force_ipv4:
            try:
                import urllib3.util.connection as _u3
                _u3.allowed_gai_family = lambda: socket.AF_INET  # type: ignore
            except Exception:
                pass
        self.sess = requests.Session()
        self.sess.trust_env = True
        self.sess.auth = HTTPBasicAuth(cfg.username, cfg.password)
        self.sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, */*",
            "Content-Type": "application/json",
            "Origin": "https://platform.worldquantbrain.com",
        })
        self.sess.mount("https://", _TLSAdapter(ctx))
        proxy = (str(cfg.https_proxy).strip() if cfg.https_proxy else "") or os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", "")
        if proxy:
            self.sess.proxies["https"] = proxy
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
            "Sharpe": _to_float(_metric_get(result_json, "sharpe", "Sharpe")),
            "Fitness": _to_float(_metric_get(result_json, "fitness", "Fitness")),
            "Turnover": _to_float(_metric_get(result_json, "turnover", "Turnover")),
            "Returns": _to_float(_metric_get(result_json, "returns", "Returns")),
            "Drawdown": _to_float(_metric_get(result_json, "drawdown", "Drawdown")),
            "Margin": _to_float(_metric_get(result_json, "margin", "Margin")),
            "Failure Reasons": _failure_reason_for_ledger(result_json, sim_json=sim_json, status=status, check_note=check_note),
            "platform_simulation_json": _json_compact(sim_json),
            "platform_check_json": _json_compact(check_json),
        }
        new_file = not path.is_file()
        with path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(FEEDBACK_FIELDS), extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerow(row)
        if new_file:
            print(f"[feedback] created ledger -> {path.resolve()}")

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

    def _metric_gate(self, metrics: dict[str, float | None]) -> tuple[bool, str]:
        sharpe = metrics.get("sharpe")
        fitness = metrics.get("fitness")
        turnover = metrics.get("turnover")
        returns = metrics.get("returns")
        drawdown = metrics.get("drawdown")
        margin = metrics.get("margin")
        if sharpe is None or fitness is None or turnover is None:
            return False, "missing_core_metrics"
        if sharpe < self.cfg.min_sharpe_threshold:
            return False, "sharpe_below_threshold"
        if fitness < self.cfg.min_fitness_threshold:
            return False, "fitness_below_threshold"
        if turnover < self.cfg.min_turnover_threshold or turnover > self.cfg.max_turnover_threshold:
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
        """Add token Jaccard rows from `alpha_generated_expressions*.csv` (and the v34 registry).

        This directly targets platform SELF_CORRELATION failures caused by repeating
        near-identical templates across thousands of historical generations.
        """
        if not self.cfg.include_generated_registry_in_similarity:
            return
        seen: set[str] = set()
        acc: list[str] = []
        for expr in self._generated_registry_exact:
            e = _sig(str(expr))
            if e and e not in seen:
                seen.add(e)
                acc.append(e)
        base_dir = Path(__file__).resolve().parent
        for path in sorted(glob.glob(str(base_dir / "alpha_generated_expressions*.csv"))):
            try:
                with open(path, "r", newline="", encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        e = _sig(row.get("expression") or "")
                        if e and e not in seen:
                            seen.add(e)
                            acc.append(e)
            except Exception as ex:
                print(f"[history] generated expressions read failed {Path(path).name}: {ex}")
        added = 0
        for e in acc:
            self._novelty_index.add(e)
            metrics = self._tried_metrics.get(e) if isinstance(self._tried_metrics, dict) else None
            sh = metrics.get("sharpe") if isinstance(metrics, dict) else None
            fit = metrics.get("fitness") if isinstance(metrics, dict) else None
            tier = _history_quality_tier(
                sharpe=sh if sh is not None else None,
                fitness=fit if fit is not None else None,
                check_passed=e in self._passed_expressions,
                feedback_text="",
                near_pass_min_sharpe=float(self.cfg.near_pass_min_sharpe),
                near_pass_seed_min_composite=float(self.cfg.near_pass_seed_min_composite),
            )
            self._history_pools.append_tokens(e, tier if tier != "weak_fail" or sh is not None else "weak_fail")
            added += 1
        print(f"[history] similarity_pool +generated_csv token_rows~={added} unique_expr={len(acc)}")
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
            f"near={len(self._history_pools.near_pass)} passed={len(self._history_pools.passed)}"
        )

    # ---- auth / http ----

    def authenticate(self) -> None:
        resp = self._retry("POST", f"{self._BASE}/authentication")
        print(f"[auth] OK status={resp.status_code} user={_mask(self.cfg.username)}")

    def _timeout(self) -> tuple[float, float]:
        return (float(self.cfg.connect_timeout), float(self.cfg.timeout))

    def _sess_request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        with self._sess_lock:
            return self.sess.request(method, url, **kwargs)

    def _retry(self, method: str, url: str, *, params: dict | None = None, json_body: dict | None = None, timeout: tuple | None = None) -> requests.Response:
        to = timeout or self._timeout()
        for attempt in range(self.cfg.max_retries + 1):
            try:
                resp = self._sess_request(method, url, params=params, json=json_body, timeout=to)
                self._consecutive_dns_errors = 0
                if resp.status_code in (429, 500, 502, 503, 504):
                    if attempt == self.cfg.max_retries:
                        resp.raise_for_status()
                    wait = float(resp.headers.get("Retry-After") or min(2 ** attempt, 30))
                    self._retry_transient_hits += 1
                    now = time.time()
                    tail = url.rsplit("/", 1)[-1]
                    if len(tail) > 52:
                        tail = tail[:49] + "..."
                    window = 14.0
                    should_log = (now - self._retry_transient_log_ts >= window) or (attempt == self.cfg.max_retries)
                    if should_log:
                        n = self._retry_transient_hits
                        sfx = " (last attempt)" if attempt == self.cfg.max_retries else ""
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
                if _is_dns_error(e):
                    self._consecutive_dns_errors += 1
                    if self._consecutive_dns_errors >= self.cfg.dns_error_pause_count:
                        print(f"[network] DNS errors={self._consecutive_dns_errors}; pause {self.cfg.dns_error_pause_seconds:.0f}s")
                        time.sleep(self.cfg.dns_error_pause_seconds)
                        self._consecutive_dns_errors = 0
                if attempt == self.cfg.max_retries:
                    raise
                time.sleep(min(2 ** attempt, 30))
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

    def _dataset_ids(self) -> list[str]:
        if self.cfg.dataset_ids:
            return list(self.cfg.dataset_ids)
        url = f"{self._BASE}/data-sets"
        out: list[str] = []
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

        return sorted(out, key=priority)[: self.cfg.dataset_auto_max] or ["fundamental6", "fundamental65", "analyst14", "analyst15", "model262", "pv1"]

    def fetch_datafields(self) -> Any:
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
            first = self._retry("GET", base, params=params).json()
            total = int(first.get("count", 0))
            rows = list(first.get("results", []))
            for offset in range(50, total, 50):
                params["offset"] = offset
                rows.extend(self._retry("GET", base, params=params).json().get("results", []))
                time.sleep(self.cfg.page_sleep)
            print(f"[fields] {did}: {len(rows)}")
            for r in rows:
                row = dict(r) if isinstance(r, dict) else {"id": str(r)}
                row["_ds"] = did
                all_rows.append(row)
        df = pd.DataFrame(all_rows)
        if df.empty:
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
        return df

    def top_fields(self, df: Any) -> Any:
        ranked = df.copy()
        for col in ("coverage", "dateCoverage", "userCount"):
            if col not in ranked.columns:
                ranked[col] = 0.0
        user = pd.to_numeric(ranked["userCount"], errors="coerce").fillna(0)
        ranked["_score"] = (
            pd.to_numeric(ranked["coverage"], errors="coerce").fillna(0) * 0.35
            + pd.to_numeric(ranked["dateCoverage"], errors="coerce").fillna(0) * 0.35
            + user / (user.max() + 1) * 0.15
            + ranked["id"].astype(str).map(field_quality_score) * 0.15
        )
        ranked = ranked.sort_values("_score", ascending=False)
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
        self._novelty_index = NoveltyIndex()
        self._feedback_blocked_operators = set()
        self._feedback_blocked_variables = set()
        near_pass_buf: dict[str, dict[str, Any]] = {}
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
                            sharpe = _to_float(row.get("Sharpe"))
                            fitness = _to_float(row.get("Fitness"))
                            turnover = _to_float(row.get("Turnover"))
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
        self._near_pass_expression_set = {str(r.get("expression") or "") for r in self._near_pass_records}
        if hopeful_added:
            print(f"[history] hopeful_jsonl near_pass seeds +{hopeful_added}")
        # Important: v34 dedup is based on v34 registry only. We still learn from older ledgers,
        # but we do NOT block generation just because an expression existed in older runs/tools.
        print(
            f"[history] registry_seen={len(self._history_seen_exact)} "
            f"registry_skeletons={len(self._history_seen_skeleton)} "
            f"similarity_tiers toxic={len(self._history_pools.toxic)} weak={len(self._history_pools.weak_fail)} "
            f"near_pass={len(self._history_pools.near_pass)} passed={len(self._history_pools.passed)} "
            f"(ingest {dict(tier_counts)}) "
            f"tried={len(self._tried_expressions)} payloads={len(self._tried_payload_keys)} failed={len(self._failed_expressions)} "
            f"passed={len(self._passed_expressions)} near_pass_seeds={len(self._near_pass_records)} "
            f"feedback_blocked_ops={len(self._feedback_blocked_operators)} feedback_unknown_vars={len(self._feedback_blocked_variables)}"
        )

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
        """Treat platform library as already simulated for prescreen + generation dedup."""
        if not expressions and not alpha_ids:
            return
        before = len(self._tried_expressions)
        self._tried_expressions.update(expressions)
        self._history_seen_exact.update(expressions)
        for expr in expressions:
            self._history_seen_skeleton.add(_skel(expr))
        self._platform_alpha_ids.update(alpha_ids)
        added = len(self._tried_expressions) - before
        print(
            f"[history] platform_sync expressions={len(expressions)} alpha_ids={len(alpha_ids)} "
            f"tried+={added} tried_total={len(self._tried_expressions)}"
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
                        self.authenticate()
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
                    self.authenticate()
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

    def check_alpha(
        self,
        alpha_id: str,
        *,
        max_wait_seconds: float | None = None,
        heartbeat_label: str = "",
    ) -> tuple[bool | None, dict | None, str]:
        wait_s = float(self.cfg.max_check_poll_seconds) if max_wait_seconds is None else float(max_wait_seconds)
        deadline = time.time() + max(1.0, wait_s)
        metric_pass_deadline: float | None = None
        last_note = "pending"
        polls = 0
        last_detail: dict | None = None
        while time.time() < deadline:
            polls += 1
            detail = self.fetch_alpha_detail(alpha_id)
            if not isinstance(detail, dict):
                if polls % max(1, int(self.cfg.recheck_heartbeat_every_polls)) == 0 and heartbeat_label:
                    print(f"[recheck] waiting {heartbeat_label} alpha_id={alpha_id} polls={polls}")
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
                if metric_pass_deadline is None:
                    extra = float(getattr(self.cfg, "check_self_correlation_extra_seconds", 0.0) or 0.0)
                    if extra > 0 and max_wait_seconds is None:
                        metric_pass_deadline = time.time() + extra
                        deadline = max(deadline, metric_pass_deadline)
                        if heartbeat_label:
                            print(
                                f"[recheck] {heartbeat_label} alpha_id={alpha_id} "
                                f"metric checks PASS — extended self_correlation poll +{extra:.0f}s"
                            )
            is_data = detail.get("is") if isinstance(detail.get("is"), dict) else {}
            if str(is_data.get("check_passed") or "").lower() in ("true", "1", "yes"):
                return True, detail, "check_passed"
            if polls % max(1, int(self.cfg.recheck_heartbeat_every_polls)) == 0 and heartbeat_label:
                print(f"[recheck] waiting {heartbeat_label} alpha_id={alpha_id} note={last_note} polls={polls}")
            time.sleep(self.cfg.check_poll_interval_seconds)
        if last_detail and _non_self_checks_all_pass(last_detail) and _self_correlation_pending(last_detail):
            return None, last_detail, "metric_pass:self_correlation_pending"
        return None, last_detail, f"check_timeout:{last_note}"

    def queue_decision(self, payload: dict, alpha_id: str | None, result_json: dict | None, check_passed: bool | None, check_note: str) -> tuple[str, dict | None]:
        if not alpha_id or not isinstance(result_json, dict):
            return "not_queued", None
        hard = _hard_fail_checks(result_json)
        if hard:
            return "not_queued:hard_fail", None
        sharpe = _to_float(_metric_get(result_json, "sharpe", "Sharpe"))
        fitness = _to_float(_metric_get(result_json, "fitness", "Fitness"))
        turnover = _to_float(_metric_get(result_json, "turnover", "Turnover"))
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
        gate_ok, gate_note = self._metric_gate(metrics)
        if check_passed is True or (non_self_all_pass and self_pending):
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
            if template_core >= skip_np_when_tpl:
                print(
                    f"[near_pass] skipped: template/formulaic core={template_core} >= {skip_np_when_tpl} "
                    f"(avoid flooding prescreen with near_pass variants)"
                )
            elif core_n < min_core:
                print(
                    f"[near_pass] skipped: core_candidates={core_n} < {min_core} "
                    f"(fix template/formulaic generation first; near_pass cannot replace empty core pool)"
                )
            elif template_core >= skip_np_when_tpl:
                pass
            elif not self.cfg.near_pass_only_when_short or shortfall:
                amplifier = NearPassAmplifier(self.cfg, catalog, validator)
                extra = amplifier.amplify(
                    self._near_pass_records, self._tried_expressions | {c.expression for c in candidates}
                )
                if extra:
                    existing = {c.expression for c in candidates}
                    deduped = [c for c in extra if c.expression not in existing]
                    cap = max(0, floor - len(candidates))
                    if cap and len(deduped) > cap:
                        deduped = deduped[:cap]
                    candidates = candidates + deduped
                    print(
                        f"[near_pass] amplified +{len(deduped)} (appended, cap={cap or 'all'}) "
                        f"from {len(self._near_pass_records)} seeds"
                    )
            else:
                print(
                    f"[near_pass] skipped amplify: candidates={len(candidates)} >= "
                    f"floor*{self.cfg.near_pass_shortfall_ratio:.2f} (templates/fundamental first)"
                )
        self._append_generated_registry(candidates)
        print(f"[generate] candidates={len(candidates)}")
        return candidates, catalog

    def run_generate(self) -> Any:
        self.authenticate()
        candidates, _ = self.generate_candidates()
        rows = [{"expression": c.expression, "family": c.family, "source": c.source, "score": c.score} for c in candidates[: self.cfg.budget]]
        out = self._path(f"{self.cfg.output_prefix}_candidates.csv")
        pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
        print(f"[generate] saved {out.name}")
        return pd.DataFrame(rows)

    def _order_payloads_for_prescreen(self, payloads: list[dict]) -> list[dict]:
        """Alpha Models + pass-first first; near-pass variants last (payload expand + prescreen)."""
        if not payloads:
            return payloads
        max_near = max(30, int(len(payloads) * float(getattr(self.cfg, "near_pass_max_family_share", 0.10)) * 2))
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
            elif fam.startswith("near_pass_variant"):
                near.append(p)
            elif fam.startswith("pass_") or str((p.get("meta") or {}).get("source") or "").lower() == "pass_first":
                core.append(p)
            else:
                other.append(p)
        rank = lambda p: PreSimulationScreener._fine_rank_key_bulk(None, p)  # type: ignore[arg-type]
        templates.sort(key=rank)
        core.sort(key=rank)
        other.sort(key=rank)
        near.sort(key=rank)
        if len(near) > max_near:
            print(f"[batch] near_pass payloads capped {len(near)} -> {max_near} (templates/fundamental first)")
            near = near[:max_near]
        return templates + core + other + near

    def _allocate_payload_budget(self, payloads: list[dict], run_cap: int) -> tuple[list[dict], dict[str, int]]:
        """Allocate one batch by family quotas to avoid low-value family domination."""
        if run_cap <= 0 or not payloads:
            return [], {}
        if run_cap >= len(payloads):
            stats = Counter(str((p.get("meta") or {}).get("family") or "unknown") for p in payloads if isinstance(p, dict))
            stats["selected_total"] = len(payloads)
            return payloads, dict(stats)

        template_quota = min(max(0, int(getattr(self.cfg, "alpha_models_batch_quota", 80))), run_cap)
        remain = max(0, run_cap - template_quota)
        near_quota = min(max(0, int(self.cfg.near_pass_batch_quota)), remain)
        remain = max(0, remain - near_quota)
        hybrid_quota = min(max(0, int(getattr(self.cfg, "pass_hybrid_batch_quota", 0))), remain)
        remain = max(0, remain - hybrid_quota)
        robust_quota = min(max(0, int(self.cfg.robust_batch_quota)), remain)
        remain = max(0, remain - robust_quota)
        pass_first_quota = min(max(0, int(self.cfg.pass_first_batch_quota)), remain)
        remain = max(0, remain - pass_first_quota)
        delta_quota = min(max(0, int(self.cfg.delta_liquid_batch_quota)), remain)
        remain = max(0, remain - delta_quota)
        explore_quota = min(max(0, int(self.cfg.explore_batch_quota)), remain)
        remain = max(0, remain - explore_quota)

        buckets: dict[str, list[dict]] = {
            "template": [], "near": [], "hybrid": [], "robust": [], "pass_first": [], "delta": [], "explore": [], "other": [],
        }
        for p in payloads:
            fam = str((p.get("meta") or {}).get("family") or "").lower() if isinstance(p, dict) else ""
            src = str((p.get("meta") or {}).get("source") or "").lower() if isinstance(p, dict) else ""
            if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
                buckets["template"].append(p)
            elif fam.startswith("near_pass_variant"):
                buckets["near"].append(p)
            elif fam.startswith("pass_fundamental_hybrid"):
                buckets["hybrid"].append(p)
            elif fam.startswith("pass_fundamental_ts"):
                buckets["explore"].append(p)
            elif src == "pass_first" or fam.startswith("pass_pv") or fam.startswith("pass_fundamental"):
                buckets["pass_first"].append(p)
            elif "liquid" in fam or ("delta" in fam and "fundamental" in fam):
                buckets["delta"].append(p)
            else:
                buckets["other"].append(p)

        selected: list[dict] = []
        stats: Counter[str] = Counter()
        ts_cap = max(0, int(self.cfg.pass_fundamental_ts_max_per_batch))
        ts_count = 0

        def take_from(group: str, n: int) -> None:
            nonlocal ts_count
            for p in buckets[group]:
                if len(selected) >= run_cap or n <= 0:
                    break
                fam = str((p.get("meta") or {}).get("family") or "").lower()
                if fam.startswith("pass_fundamental_ts") and ts_count >= ts_cap:
                    continue
                selected.append(p)
                stats[group] += 1
                n -= 1
                if fam.startswith("pass_fundamental_ts"):
                    ts_count += 1

        take_from("template", template_quota)
        take_from("near", near_quota)
        take_from("hybrid", hybrid_quota)
        take_from("robust", robust_quota)
        take_from("pass_first", pass_first_quota)
        take_from("delta", delta_quota)
        take_from("explore", explore_quota)
        if len(selected) < run_cap:
            # fill from remaining pool by original order
            for group in ("template", "near", "hybrid", "robust", "pass_first", "delta", "explore", "other"):
                for p in buckets[group]:
                    if len(selected) >= run_cap:
                        break
                    if p in selected:
                        continue
                    fam = str((p.get("meta") or {}).get("family") or "").lower()
                    if fam.startswith("pass_fundamental_ts") and ts_count >= ts_cap:
                        continue
                    selected.append(p)
                    stats["fill"] += 1
                    if fam.startswith("pass_fundamental_ts"):
                        ts_count += 1
                if len(selected) >= run_cap:
                    break
        stats["ts"] = ts_count
        stats["selected_total"] = len(selected)
        return selected, dict(stats)

    @staticmethod
    def _sort_payloads_sim_priority(payloads: list[dict]) -> None:
        """Put higher-prior templates first within each bucket (allocator still applies quotas)."""

        def key(p: dict) -> tuple[int, float]:
            meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
            fam = str(meta.get("family") or "").lower()
            src = str(meta.get("source") or "").lower()
            sc = float(meta.get("candidate_score") or 0.0)
            if fam.startswith("alpha_models_template") or fam == "external_template" or fam.startswith("formulaic_"):
                tier = 0
            elif fam.startswith("pass_fundamental_hybrid"):
                tier = 1
            elif src == "pass_first" or fam.startswith("pass_pv") or fam.startswith("pass_fundamental"):
                tier = 1
            elif src in ("robust", "hybrid", "submission") or fam.startswith("regime_shift"):
                tier = 2
            elif "liquid" in fam or "delta" in fam:
                tier = 2
            elif fam.startswith("pass_fundamental_ts"):
                tier = 3
            elif fam.startswith("near_pass_variant"):
                tier = 8
            else:
                tier = 4
            return (tier, -sc)

        payloads.sort(key=key)

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
                if expr in screener.tried_exact:
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
        )

    def _prescreen_single_pass(
        self, payloads: list[dict], *, stage: str = "single"
    ) -> tuple[list[dict], Counter[str], list[tuple[str, str]]]:
        """One-pass prescreen with optional similarity relax (v40 behaviour)."""
        if not self.cfg.prescreen_enabled or not payloads:
            return payloads, Counter(), []
        min_t = max(0, int(self.cfg.min_simulate_batch))
        orig_ps = float(self.cfg.prescreen_max_history_similarity)
        orig_np = float(self.cfg.prescreen_near_pass_similarity)
        orig_ib = float(self.cfg.prescreen_intrabatch_similarity)
        relax = bool(self.cfg.prescreen_relax_to_hit_min_batch)
        if stage == "coarse":
            relax = relax and bool(self.cfg.prescreen_coarse_relax_to_fill)
        try:
            screener = self._make_prescreen_screener()
            kept, reasons, samples = screener.screen(payloads, stage=stage)

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
                best = _pick_better(best, cand)
                print(
                    f"[{label}] loosen round={rounds} "
                    f"prescreen_cap={self.cfg.prescreen_max_history_similarity:.3f} "
                    f"near_pass_cap={self.cfg.prescreen_near_pass_similarity:.3f} "
                    f"intra_cap={self.cfg.prescreen_intrabatch_similarity:.3f} "
                    f"kept_this_round={len(kept2)} best_kept={len(best[0])}/{len(payloads)} target={min_t}"
                )
                if rounds >= 48:
                    break
            return best[0], best[1], best[2]
        finally:
            self.cfg.prescreen_max_history_similarity = orig_ps
            self.cfg.prescreen_near_pass_similarity = orig_np
            self.cfg.prescreen_intrabatch_similarity = orig_ib

    def _prescreen_two_stage(
        self, payloads: list[dict]
    ) -> tuple[list[dict], Counter[str], list[tuple[str, str]], int]:
        """Coarse expand → fine diverse select with fill-to-target (v41 + v42 fine relax)."""
        target = max(0, int(self.cfg.min_simulate_batch))
        if self.cfg.run_payload_cap is not None:
            target = min(target, max(1, int(self.cfg.run_payload_cap)))
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
        effective_target = min(target, len(coarse)) if coarse else 0
        if effective_target < target:
            print(
                f"[prescreen] WARN coarse_kept={len(coarse)} < target={target}; "
                f"max simulate this run={effective_target}"
            )
        screener = self._make_prescreen_screener()
        fine: list[dict] = []
        fine_reasons: Counter[str] = Counter()
        fine_samples: list[tuple[str, str]] = []
        orig_ib = float(self.cfg.prescreen_intrabatch_similarity)
        intra_step = float(self.cfg.prescreen_intrabatch_similarity_relax_step)
        intra_ceiling = float(
            getattr(self.cfg, "prescreen_fine_intrabatch_relax_ceiling", None)
            or self.cfg.prescreen_intrabatch_similarity_relax_ceiling
        )
        hist_step = float(self.cfg.prescreen_fine_history_relax_step)
        fill_enabled = bool(self.cfg.prescreen_fine_fill_to_target)
        ib = orig_ib
        if fill_enabled and effective_target >= 30:
            ib = max(ib, float(self.cfg.prescreen_near_pass_similarity))
        hist_cap = strict_hist
        fine_round = 0
        max_rounds = 32

        while fine_round < max_rounds and len(fine) < effective_target:
            fine_round += 1
            use_bulk_rank = hist_cap > strict_hist + 0.02
            intra_override = ib if (fill_enabled and (fine_round > 1 or effective_target >= 20)) else None
            cand, reasons_round, samples_round = screener.select_diverse_for_simulate(
                coarse,
                effective_target,
                history_similarity_cap=hist_cap,
                intrabatch_cap_override=intra_override,
                rank_key=screener._fine_rank_key_bulk if use_bulk_rank else screener._fine_rank_key,
            )
            if len(cand) > len(fine):
                fine = cand
            fine_reasons = reasons_round
            fine_samples = samples_round
            print(
                f"[prescreen/fine] round={fine_round} hist_cap={hist_cap:.3f} intra_cap={ib:.3f} "
                f"selected={len(fine)}/{effective_target}"
                f"{' bulk_rank' if use_bulk_rank else ''}"
            )
            if len(fine) >= effective_target:
                break
            if not fill_enabled:
                break
            progressed = False
            if hist_cap < hist_ceiling - 1e-9:
                hist_cap = min(hist_ceiling, hist_cap + hist_step)
                progressed = True
            if ib < intra_ceiling - 1e-9:
                ib = min(intra_ceiling, ib + intra_step)
                progressed = True
            if not progressed:
                break

        if (
            bool(self.cfg.prescreen_fine_desperate_fill)
            and fill_enabled
            and len(fine) < effective_target
            and coarse
        ):
            shape_desperate = max(
                8,
                int(_novelty_profile(self.cfg.novelty_strictness)["same_shape_per_batch"]) + 4,
                (effective_target + 9) // 10,
            )
            cand, reasons_round, samples_round = screener.select_diverse_for_simulate(
                coarse,
                effective_target,
                history_similarity_cap=hist_ceiling,
                intrabatch_cap_override=intra_ceiling,
                shape_quota_override=shape_desperate,
                rank_key=screener._fine_rank_key_bulk,
            )
            if len(cand) > len(fine):
                fine = cand
                fine_reasons = reasons_round
                fine_samples = samples_round
            print(
                f"[prescreen/fine] desperate_fill hist={hist_ceiling:.3f} intra={intra_ceiling:.3f} "
                f"selected={len(fine)}/{effective_target}"
            )

        if fill_enabled and len(fine) < effective_target and coarse:
            need = effective_target - len(fine)
            topup = screener.select_coarse_topup(coarse, fine, need, history_similarity_cap=hist_ceiling)
            if topup:
                fine = fine + topup
                print(
                    f"[prescreen/fine] coarse_topup +{len(topup)} total={len(fine)}/{effective_target}"
                )

        reasons = Counter(coarse_reasons)
        reasons.update(fine_reasons)
        samples = list(coarse_samples) + list(fine_samples)
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
            return payloads, Counter(), []
        if self.cfg.prescreen_two_stage:
            kept, reasons, samples, coarse_n = self._prescreen_two_stage(payloads)
            self._last_prescreen_coarse_count = coarse_n
            return kept, reasons, samples
        self._last_prescreen_coarse_count = 0
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
    ) -> None:
        path = self._path(self.cfg.batch_diagnostics_filename)
        rows: list[dict[str, Any]] = []
        base = {
            "utc_iso": _utc(),
            "pipeline_version": self.cfg.pipeline_version,
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
        for reason, sample in samples[:20]:
            rows.append({**base, "kind": "drop_sample", "name": reason, "count": 1, "sample": sample})
        for name, value in (allocator_stats or {}).items():
            rows.append({**base, "kind": "allocator", "name": str(name), "count": value, "sample": ""})
        fields = [
            "utc_iso", "pipeline_version", "target_simulate_batch", "min_simulate_batch",
            "candidates", "raw_payloads", "prescreen_coarse_kept", "prescreen_kept", "selected",
            "novelty_strictness", "prescreen_similarity", "intrabatch_similarity",
            "kind", "name", "count", "sample",
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
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
        self.cfg.min_simulate_batch = max(int(self.cfg.min_simulate_batch), int(self.cfg.target_simulate_batch))
        self.authenticate()
        fb = self._feedback_path()
        cp = self._path(f"{self.cfg.output_prefix}_checkpoint.csv")
        print(
            f"[paths] failure_reasons+metrics -> {fb.resolve()} "
            f"| checkpoint -> {cp.resolve()} | version={self.cfg.pipeline_version} "
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
            max_drop_samples = 5
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
            allocator_stats=alloc_stats,
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
        if self.cfg.sync_platform_tried_before_simulate:
            print(
                f"[batch] platform_tried synced: {len(self._tried_expressions)} expressions, "
                f"{len(self._platform_alpha_ids)} alpha_ids — prescreen skips re-simulating these"
            )
        # Cool down after heavy data-field / 429 bursts before opening aiohttp connection pool.
        time.sleep(max(0.0, float(getattr(self.cfg, "pre_simulate_cooldown_seconds", 3.0))))
        result_df = self.run_batch_simulation(payloads)
        if self._near_pass_records:
            print(
                f"[hint] {len(self._near_pass_records)} near-pass/hopeful seeds loaded — "
                "templates/formulaic first; toxic-history blocked. For SELF_CORRELATION pending: "
                "py -3 auto_alpha_pipeline_rebuilt_v40.py --mode recheck"
            )
        # Recheck again right after simulation to reduce pending backlog.
        if self.cfg.recheck_skip_postbatch:
            print("[recheck] post-batch skipped (recheck_skip_postbatch)")
        else:
            try:
                post_recheck_df = self.run_recheck_queue(
                    do_auth=False,
                    max_items=self.cfg.recheck_postbatch_max_items,
                    quick_timeout_seconds=min(120.0, float(self.cfg.recheck_quick_timeout_seconds)),
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
        rows = []
        run_cap = len(payloads) if self.cfg.run_payload_cap is None else max(1, min(int(self.cfg.run_payload_cap), len(payloads)))
        run_payloads = payloads[:run_cap]
        print(f"[simulate] START payloads={len(run_payloads)} cap={run_cap} budget={self.cfg.budget} (sequential)")
        for idx, payload in enumerate(run_payloads, start=1):
            expr = payload["regular"]
            profile = payload.get("meta", {}).get("profile", "?")
            print(f"[simulate {idx}/{len(run_payloads)}] ({profile}) {expr[:120]}")
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
                if sh_pre is not None and fi_pre is not None and float(sh_pre) < -0.25 and float(fi_pre) < 0:
                    check_wait = min(120.0, float(self.cfg.max_check_poll_seconds))
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
                        inv_check_passed, inv_check_json, inv_check_note = self.check_alpha(inv_alpha_id)
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
        self.authenticate()
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
            if any(str(c.get("result") or "").upper() == "FAIL" for c in checks if isinstance(c, dict)):
                continue
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            if (_to_float(metrics.get("sharpe")) or -999) < self.cfg.queue_min_sharpe:
                continue
            if (_to_float(metrics.get("fitness")) or -999) < self.cfg.queue_min_fitness:
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
        """Persist recheck progress to the hopeful JSONL so load() can see updated checks/status."""
        snap = dict(row)
        snap["rechecked_at"] = _utc()
        snap["last_recheck_note"] = str(note or "")
        snap["last_queue_status"] = str(queue_status or "")
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
        if check_passed is True:
            snap["status"] = "ready"
        elif isinstance(note, str) and note.startswith("check_timeout"):
            snap["status"] = "needs_recheck"
        elif _hard_fail_checks(detail if isinstance(detail, dict) else None):
            snap["status"] = "check_failed_stale"
        else:
            snap["status"] = "recheck_closed"
        self.queue.append(snap)

    def run_recheck_queue(
        self,
        *,
        do_auth: bool = True,
        max_items: int | None = None,
        quick_timeout_seconds: float | None = None,
        wall_budget_seconds: float | None = None,
    ) -> Any:
        if do_auth:
            self.authenticate()
        rows = self.queue.load()
        results = []
        pending_rows = [row for row in rows if row.get("status") == "needs_recheck"]
        limit = len(pending_rows) if max_items is None else max(0, int(max_items))
        processing = pending_rows if max_items is None else pending_rows[:limit]
        print(f"[recheck] pending={len(pending_rows)} processing={len(processing)}")
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
            print(f"[recheck {idx}/{len(processing)}] alpha_id={alpha_id}")
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
        self.authenticate()
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
            return self.run_recheck_queue(quick_timeout_seconds=None)
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

    ok, note = factory._submission_quality_gate(
        "group_neutralize(ts_zscore(fnd6_test_ebit/cap, 126), subindustry)",
        "pass_fundamental_ts",
        "robust",
    )
    if not ok:
        errors.append(f"quality_gate expected pass got {note}")

    ok2, note2 = factory._submission_quality_gate(
        "group_neutralize(ts_corr(rank(receivable/cap), rank(inventory/cap), 21), subindustry)",
        "explore",
        "explore",
    )
    if ok2:
        errors.append(f"quality_gate expected reject pair_corr got pass ({note2})")

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
            {"regular": "group_neutralize(ts_zscore(x/cap,126),subindustry)", "settings": {}, "meta": {"family": "pass_pv"}},
            {"regular": "group_neutralize(ts_zscore(x/cap,126),subindustry)*0.99", "settings": {}, "meta": {"family": "pass_pv"}},
        ]
    )
    if "negative_sharpe_history" not in reasons:
        errors.append(f"prescreen expected negative_sharpe_history in {dict(reasons)}")
    if not any(k.startswith("high_intrabatch_self_corr") for k in reasons):
        errors.append(f"prescreen expected high_intrabatch_self_corr in {dict(reasons)}")

    near_dup_payloads = [
        {"regular": "group_rank(x/cap,subindustry)-0.5", "settings": {}, "meta": {"family": "pass_pv", "candidate_score": 2.0}},
        {"regular": "group_rank(x/cap,subindustry)-0.5*0.99", "settings": {}, "meta": {"family": "pass_pv", "candidate_score": 1.0}},
        {"regular": "group_neutralize(ts_zscore(y/cap,126),subindustry)", "settings": {}, "meta": {"family": "pass_fundamental_ts", "candidate_score": 3.0}},
    ]
    coarse_kept, coarse_reasons, _ = screener.screen(near_dup_payloads, stage="coarse")
    if len(coarse_kept) < 2:
        errors.append(f"coarse prescreen expected >=2 kept got {len(coarse_kept)} reasons={dict(coarse_reasons)}")
    picked, fine_reasons, _ = screener.select_diverse_for_simulate(coarse_kept, 2)
    if len(picked) < 2:
        errors.append(f"fine diverse select expected 2 got {len(picked)} reasons={dict(fine_reasons)}")

    novelty = NoveltyIndex()
    novelty.add("group_neutralize(ts_zscore(x/cap,126),subindustry)")
    novelty_screener = PreSimulationScreener(
        factory_cfg,
        tried_exact=set(),
        tried_payload_keys=set(),
        near_pass_expressions=set(),
        failed_cluster={},
        history_pools=HistorySimilarityPools(),
        top_field_lookup=lambda _e: None,
        tried_metrics={},
        novelty_index=novelty,
    )
    _kept2, reasons2, _ = novelty_screener.screen(
        [
            {"regular": "group_neutralize(ts_zscore(x/cap,252),subindustry)", "settings": {}, "meta": {"family": "pass_pv"}},
            {"regular": "group_neutralize(ts_zscore(x/cap,126),subindustry)*0.99", "settings": {}, "meta": {"family": "pass_pv"}},
        ]
    )
    if not any(k.startswith("novelty_") for k in reasons2):
        errors.append(f"novelty gate expected structural/field rejection in {dict(reasons2)}")

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
    p.add_argument("--preset", choices=["conservative", "pv", "fundamental", "mixed", "challenge"], default="mixed")
    p.add_argument("--budget", type=int, default=300, help="Generation target floor; default 300.")
    p.add_argument("--run-payload-cap", type=int, default=None, help="Optional hard cap on payloads simulated this run. Default: simulate *all* prescreen-kept payloads (no limit).")
    p.add_argument(
        "--min-simulate-batch",
        type=int,
        default=None,
        metavar="N",
        help="Try to keep at least N payloads after prescreen (may relax prescreen similarity toward floor). Default 300.",
    )
    p.add_argument(
        "--target-simulate-batch",
        type=int,
        default=300,
        metavar="N",
        help="Target selected payload count for one simulate batch. Default 300.",
    )
    p.add_argument(
        "--novelty-strictness",
        choices=["balanced", "strict", "paranoid"],
        default="strict",
        help="Novelty/self-correlation guard strength. Default strict.",
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
        "--no-prebatch-recheck",
        action="store_true",
        help="Skip inline pre/post recheck in full mode (use --mode recheck separately).",
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
        help="Optional SQLite file to append simulation rows (in addition to CSV feedback).",
    )
    return p.parse_args()


def main() -> int:
    print("[blocked] archived generated pipeline cannot write; use python -m alpha_mining")
    return 2
    _load_env_file()
    args = parse_args()
    if args.smoke:
        return _run_offline_smoke()
    if args.feedback_diagnostics:
        cfg = PipelineConfig(
            username="offline",
            password="offline",
            mode="feedback-diagnostics",
            target_simulate_batch=max(1, int(args.target_simulate_batch)),
            min_simulate_batch=max(1, int(args.target_simulate_batch)),
            novelty_strictness=args.novelty_strictness,
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
    cfg = PipelineConfig(
        username=username,
        password=password,
        mode=args.mode,
        preset=args.preset,
        budget=max(300, int(args.budget)),
        run_payload_cap=(max(1, int(args.run_payload_cap)) if args.run_payload_cap is not None else None),
        target_simulate_batch=max(1, int(args.target_simulate_batch)),
        min_simulate_batch=max(1, int(args.target_simulate_batch)),
        novelty_strictness=args.novelty_strictness,
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
        max_concurrent_simulations=(
            0
            if args.sequential_sim
            else (max(0, int(args.concurrent_sim)) if args.concurrent_sim is not None else 16)
        ),
        max_concurrent_simulation_posts=(
            max(1, int(args.concurrent_submit)) if args.concurrent_submit is not None else 2
        ),
        sqlite_runs_path=(str(args.sqlite_runs).strip() or None) if args.sqlite_runs else None,
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
        cfg.min_simulate_batch = max(1, int(args.min_simulate_batch))
        cfg.target_simulate_batch = max(1, int(args.min_simulate_batch))
    if args.no_prescreen_relax:
        cfg.prescreen_relax_to_hit_min_batch = False
    if args.single_stage_prescreen:
        cfg.prescreen_two_stage = False
    if getattr(args, "no_prebatch_recheck", False):
        cfg.recheck_skip_prebatch = True
        cfg.recheck_skip_postbatch = True
    if args.max_payload_expand_cap is not None:
        cfg.max_payload_expand_cap = max(10_000, int(args.max_payload_expand_cap))
    if args.no_near_pass:
        cfg.near_pass_enabled = False

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
        print(f"[fatal] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
