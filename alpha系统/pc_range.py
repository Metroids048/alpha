#!/usr/bin/env python3
"""
PC Range Estimator — 独立可调试版

利用 PnL 曲线相关性传递性预估 prod corr 范围：
  若 corr(A,B) = p1, corr(B,prod) = p2，则
  corr(A,prod) ∈ [p1·p2 − √((1−p1²)(1−p2²)),  p1·p2 + √((1−p1²)(1−p2²))]

用法: 直接改下方 ═══ CONFIG ═══ 区的参数，然后 python pc_range.py
"""

import os
import sys
import csv
import json
import math
import numpy as np


# ──────────────────────────────────────────────
#  Path Constants
# ──────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ALPHAS_DB_PATH = os.path.join(_SCRIPT_DIR, 'alphas_db.json')
_PC_CACHE_PATH = os.path.join(_SCRIPT_DIR, 'pc_cache.json')
_PNL_SUBMITTED_DIR = os.path.join(_SCRIPT_DIR, 'pnl_csv_submitted')
_PNL_UNSUBMITTED_DIR = os.path.join(_SCRIPT_DIR, 'pnl_csv_unsubmitted')


# ══════════════════════════════════════════════
#  ═══ CONFIG — 改这里直接调试 ═══
# ══════════════════════════════════════════════
THRESHOLD      = 0.3           # 最小 |inter corr| 才纳入估算
VERBOSE        = True          # 打印每个 peer alpha 的详情


# ──────────────────────────────────────────────
#  Inter-Correlation Math (ProdMemo corrWorker.js 算法)
# ──────────────────────────────────────────────

def normalize_pnl(dates, cum_pnl):
    """Filter invalid records, normalize dates to YYYY-MM-DD, sort."""
    result = []
    for d, v in zip(dates, cum_pnl):
        if not d:
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue
        result.append((str(d)[:10], val))
    result.sort(key=lambda x: x[0])
    return result


def calendar_window_start(records, years=4):
    """Last record year minus `years` + 1, January 1."""
    if not records:
        return None
    last_year = int(records[-1][0][:4])
    return f'{last_year - years + 1}-01-01'


def calculate_returns(records, start_date):
    """Cumulative PnL → daily returns (direct diff, no fill)."""
    returns = {}
    previous = None
    for date_str, value in records:
        if previous is not None and date_str >= start_date:
            returns[date_str] = value - previous
        previous = value
    return returns


def calculate_forward_filled_returns(records, dates, start_date):
    """Cumulative PnL forward-filled on global calendar → daily returns."""
    returns = {}
    record_index = 0
    current_value = None
    previous_value = None
    for date_str in dates:
        while record_index < len(records) and records[record_index][0] <= date_str:
            current_value = records[record_index][1]
            record_index += 1
        if current_value is None:
            continue
        if previous_value is not None and date_str >= start_date:
            returns[date_str] = current_value - previous_value
        previous_value = current_value
    return returns


def pearson(target_returns, peer_returns):
    """Pearson correlation with sequential accumulation.
    Returns (value, count) or None if insufficient data."""
    count = 0
    sum_x = sum_y = sum_xx = sum_yy = sum_xy = 0.0
    for date_str, x in target_returns.items():
        if date_str not in peer_returns:
            continue
        y = peer_returns[date_str]
        count += 1
        sum_x += x
        sum_y += y
        sum_xx += x * x
        sum_yy += y * y
        sum_xy += x * y
    if count < 2:
        return None
    covariance = count * sum_xy - sum_x * sum_y
    variance_x = count * sum_xx - sum_x * sum_x
    variance_y = count * sum_yy - sum_y * sum_y
    denominator = math.sqrt(variance_x * variance_y)
    if not math.isfinite(denominator) or denominator == 0:
        return None
    value = covariance / denominator
    if not math.isfinite(value):
        return None
    return value, count


def calc_inter_corr(my_dates, my_pnl, peer_dates, peer_pnl):
    """计算两个 alpha 之间的 inter correlation。

    Args:
        my_dates: target alpha 的日期列表
        my_pnl: target alpha 的累积 PnL 列表
        peer_dates: peer alpha 的日期列表
        peer_pnl: peer alpha 的累积 PnL 列表

    Returns:
        float | None: inter correlation 值
    """
    my_records = normalize_pnl(my_dates, my_pnl)
    peer_records = normalize_pnl(peer_dates, peer_pnl)
    if len(my_records) < 2 or len(peer_records) < 2:
        return None

    start1 = calendar_window_start(my_records)
    start2 = calendar_window_start(peer_records)
    start_date = max(start1, start2)

    date_set = set()
    for r in my_records:
        date_set.add(r[0])
    for r in peer_records:
        date_set.add(r[0])
    global_dates = sorted(date_set)

    target_returns = calculate_returns(my_records, start_date)
    peer_returns = calculate_forward_filled_returns(peer_records, global_dates, start_date)

    result = pearson(target_returns, peer_returns)
    return result[0] if result is not None else None


# ──────────────────────────────────────────────
#  Local Data Access
# ──────────────────────────────────────────────

def load_alphas_db():
    """Load alphas_db.json → dict {alpha_id: alpha_data}."""
    if not os.path.exists(_ALPHAS_DB_PATH):
        return {}
    try:
        with open(_ALPHAS_DB_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {a['id']: a for a in data if a.get('id')}
    except Exception as e:
        print(f"Warning: failed to read {_ALPHAS_DB_PATH}: {e}")
    return {}


def load_pc_cache():
    """Load prod corr cache → dict {alpha_id: {"max": float, "min": float}}."""
    if not os.path.exists(_PC_CACHE_PATH):
        return {}
    try:
        with open(_PC_CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def infer_region_from_pnl_path(alpha_id):
    """Infer alpha region from PnL CSV subdirectory structure."""
    for base_dir in (_PNL_SUBMITTED_DIR, _PNL_UNSUBMITTED_DIR):
        if not os.path.exists(base_dir):
            continue
        for entry in os.listdir(base_dir):
            subdir = os.path.join(base_dir, entry)
            if os.path.isdir(subdir):
                if os.path.exists(os.path.join(subdir, f'{alpha_id}.csv')):
                    return entry
    return ''


def read_pnl_csv(filepath):
    """Read PnL records from a single CSV file."""
    records = []
    try:
        with open(filepath, 'r', newline='') as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                if len(row) < 2:
                    continue
                try:
                    date_str = str(row[0])[:10]
                    value = float(row[1])
                    if math.isfinite(value):
                        records.append((date_str, value))
                except (TypeError, ValueError):
                    continue
    except Exception:
        return None
    records.sort(key=lambda x: x[0])
    return records if len(records) >= 2 else None


def load_pnl_from_csv(alpha_id, region=None, db=None):
    """Load PnL data from CSV (submitted first, then unsubmitted).
    db: optional alphas_db dict to avoid redundant JSON reads."""
    if db is None:
        db = load_alphas_db()
    if region is None:
        alpha_data = db.get(alpha_id)
        region = alpha_data.get('settings', {}).get('region') if alpha_data else None
    for submitted in (True, False):
        base = _PNL_SUBMITTED_DIR if submitted else _PNL_UNSUBMITTED_DIR
        if region:
            filepath = os.path.join(base, region, f'{alpha_id}.csv')
            if os.path.exists(filepath):
                records = read_pnl_csv(filepath)
                if records:
                    return records
        # Fallback: old flat path
        filepath = os.path.join(base, f'{alpha_id}.csv')
        if os.path.exists(filepath):
            records = read_pnl_csv(filepath)
            if records:
                return records
    return None


# ──────────────────────────────────────────────
#  PC Range Core Logic
# ──────────────────────────────────────────────

def estimate_pc_range(alpha_id, my_region=None, my_dates=None, my_pnl=None,
                      corr_threshold=0.3, verbose=False):
    """Estimate prod corr range using PnL correlation transitivity.
    Uses numpy for vectorized Pearson computation.

    Args:
        alpha_id: target alpha ID
        my_region: target alpha region (auto-detected if None)
        my_dates: target alpha date list (loaded from CSV if None)
        my_pnl: target alpha cumulative PnL list (loaded from CSV if None)
        corr_threshold: minimum |inter corr| to use an estimate (default 0.3)
        verbose: print progress details

    Returns:
        dict with keys:
            range_max, range_min: estimated bounds
            used_count: number of correlated alphas used
            my_known_pc: known prod corr of target alpha (or None)
            estimates: list of (low, high, other_id, inter_corr, other_pc) tuples
        or raises Exception on error
    """
    db = load_alphas_db()
    pc_cache = load_pc_cache()

    # Auto-detect region
    if not my_region:
        alpha_db = db.get(alpha_id, {})
        my_region = alpha_db.get('settings', {}).get('region', '')
    if not my_region:
        raise Exception(f"Cannot determine region for alpha {alpha_id}")

    # Auto-load PnL
    if my_dates is None or my_pnl is None:
        records = load_pnl_from_csv(alpha_id, my_region, db=db)
        if not records or len(records) < 10:
            raise Exception(f"No PnL data for alpha {alpha_id} (region={my_region})")
        my_dates = [str(r[0])[:10] for r in records]
        my_pnl = [r[1] for r in records]

    if len(my_dates) != len(my_pnl) or len(my_dates) < 10:
        raise Exception("PnL data too short for PC Range estimation")

    # Current alpha's known PC
    my_known_pc = None
    my_alpha_db = db.get(alpha_id, {})
    my_is_pc = my_alpha_db.get('is', {}).get('prodCorrelation')
    if my_is_pc is not None:
        my_known_pc = abs(my_is_pc)
    else:
        my_cached = pc_cache.get(alpha_id)
        if my_cached and my_cached.get("max") is not None:
            my_known_pc = abs(my_cached["max"])

    # Collect same-region alphas with PC data
    region_pc_alphas = {}
    for other_id, other_alpha in db.items():
        if other_id == alpha_id:
            continue
        other_region = other_alpha.get('settings', {}).get('region', '')
        if other_region != my_region:
            continue
        pc = other_alpha.get('is', {}).get('prodCorrelation')
        if pc is not None:
            region_pc_alphas[other_id] = abs(pc)

    # Supplement from pc_cache
    for other_id, pc_vals in pc_cache.items():
        if other_id == alpha_id:
            continue
        if other_id in region_pc_alphas:
            continue
        pc_max = pc_vals.get("max")
        if pc_max is None:
            continue
        other_alpha = db.get(other_id, {})
        other_region = other_alpha.get('settings', {}).get('region', '')
        if not other_region:
            other_region = infer_region_from_pnl_path(other_id)
        if other_region != my_region:
            continue
        region_pc_alphas[other_id] = abs(pc_max)

    if not region_pc_alphas:
        raise Exception(f"No PC data for region {my_region}. Run Download first.")

    if verbose:
        print(f"Region: {my_region}")
        print(f"Known PC: {my_known_pc}")
        print(f"PC alphas in region: {len(region_pc_alphas)}")

    # ── Pre-compute target returns as numpy array ──
    my_records = normalize_pnl(my_dates, my_pnl)
    my_start_date = calendar_window_start(my_records)
    target_returns = calculate_returns(my_records, my_start_date)

    # Build global dates from target's returns (only dates in the 4-year window)
    active_dates = sorted(target_returns.keys())
    date_to_idx = {d: i for i, d in enumerate(active_dates)}
    n_dates = len(active_dates)

    target_arr = np.full(n_dates, np.nan)
    for d, v in target_returns.items():
        idx = date_to_idx.get(d)
        if idx is not None:
            target_arr[idx] = v
    target_valid = ~np.isnan(target_arr)

    # Vectorized Pearson
    def _vec_pearson(p_arr):
        overlap = target_valid & ~np.isnan(p_arr)
        count = np.sum(overlap)
        if count < 2:
            return None
        x = target_arr[overlap]
        y = p_arr[overlap]
        x_c = x - x.mean()
        y_c = y - y.mean()
        denom = np.sqrt(np.dot(x_c, x_c) * np.dot(y_c, y_c))
        if denom == 0:
            return None
        return float(np.dot(x_c, y_c) / denom)

    # ── Iterate over peers, pre-compute returns arrays ──
    # Load all peer PnLs first, compute returns arrays
    peer_data = {}  # other_id -> (other_pc, returns_arr or None)
    for other_id, other_pc in region_pc_alphas.items():
        if other_pc == 0:
            continue
        other_records = load_pnl_from_csv(other_id, my_region, db=db)
        if not other_records or len(other_records) < 10:
            peer_data[other_id] = (other_pc, None)
            continue
        other_norm = normalize_pnl(
            [str(r[0])[:10] for r in other_records],
            [r[1] for r in other_records]
        )
        other_start = calendar_window_start(other_norm)
        pool_start = max(my_start_date, other_start) if (my_start_date and other_start) else my_start_date
        other_returns = calculate_forward_filled_returns(other_norm, active_dates, pool_start)
        arr = np.full(n_dates, np.nan)
        for d, v in other_returns.items():
            idx = date_to_idx.get(d)
            if idx is not None:
                arr[idx] = v
        peer_data[other_id] = (other_pc, arr)

    # Compute correlations
    estimates = []
    used_count = 0
    skipped_no_pnl = 0
    skipped_low_corr = 0

    for i, (other_id, (other_pc, p_arr)) in enumerate(peer_data.items()):
        if p_arr is None:
            skipped_no_pnl += 1
            continue

        corr = _vec_pearson(p_arr)
        if corr is None:
            continue

        abs_corr = abs(corr)
        if abs_corr < corr_threshold:
            skipped_low_corr += 1
            continue

        # calc_corr_range: [p1*p2 - sqrt((1-p1²)(1-p2²)), p1*p2 + sqrt((1-p1²)(1-p2²))]
        p1 = abs_corr
        p2 = other_pc
        x = p1 * p2
        y = ((1 - p1 ** 2) * (1 - p2 ** 2)) ** 0.5
        low = x - y
        high = x + y
        estimates.append((low, high, other_id, corr, other_pc))
        used_count += 1

        if verbose:
            print(f"  [{i+1}/{len(peer_data)}] {other_id}: "
                  f"inter_corr={corr:+.4f}, other_pc={other_pc:.4f} → "
                  f"range=[{low:.4f}, {high:.4f}]")

    if not estimates:
        if my_known_pc is not None:
            return {
                'range_max': my_known_pc,
                'range_min': my_known_pc,
                'used_count': 0,
                'my_known_pc': my_known_pc,
                'estimates': [],
            }
        raise Exception(
            f"No sufficiently correlated PnL curves "
            f"({len(region_pc_alphas)} PC alphas in {my_region}, "
            f"{skipped_no_pnl} no PnL, {skipped_low_corr} low corr)"
        )

    # range_min: max of all lows (tightest lower bound)
    # range_max: max of all highs (tightest upper bound)
    range_min = max(e[0] for e in estimates)
    range_max = max(e[1] for e in estimates)

    if my_known_pc is not None:
        # Known PC is exact — it provides a floor for range_min
        # (any estimate low < known_pc is a loose bound) and a
        # ceiling that range_max must cover.
        range_min = max(range_min, my_known_pc)
        range_max = max(range_max, my_known_pc)

    return {
        'range_max': range_max,
        'range_min': range_min,
        'used_count': used_count,
        'my_known_pc': my_known_pc,
        'estimates': estimates,
    }


# ──────────────────────────────────────────────
#  Direct Run — 改顶部 CONFIG 后直接 python pc_range.py
# ──────────────────────────────────────────────

if __name__ == '__main__':
    alpha_id = 'KP9rkGXE'
    # alpha_id = 'RR8Aq89e'
    if not alpha_id:
        print("Error: set ALPHA_ID at the top of pc_range.py")
        sys.exit(1)

    REGION = 'USA'
    try:
        result = estimate_pc_range(
            alpha_id,
            my_region = REGION,
            corr_threshold=THRESHOLD,
            verbose=VERBOSE,
        )

        print("\n" + "=" * 60)
        print(f"  Alpha:  {alpha_id}")
        print(f"  PC Range: [{result['range_min']:.4f}, {result['range_max']:.4f}]")
        print(f"  Used:   {result['used_count']} correlated alphas")
        if result['my_known_pc'] is not None:
            print(f"  Known PC: {result['my_known_pc']:.4f}")
        print("=" * 60)

        if VERBOSE and result['estimates']:
            print("\nTop estimates (sorted by |inter corr|):")
            sorted_est = sorted(result['estimates'], key=lambda e: -abs(e[3]))
            for low, high, oid, corr, opc in sorted_est[:20]:
                print(f"  {oid}: corr={corr:+.4f}  other_pc={opc:.4f}  → [{low:.4f}, {high:.4f}]")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
