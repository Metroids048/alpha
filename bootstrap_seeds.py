"""Bootstrap high-quality seed expressions for cold-start feedback loop."""

BOOTSTRAP_SEEDS = [
    # 动量反转 - 经典因子
    {
        "expression": "group_neutralize(ts_zscore(ts_delta(close, 21) / ts_std_dev(close, 21), 252), industry)",
        "sharpe": 1.8,
        "fitness": 0.65,
        "turnover": 0.12,
        "outcome": "PASS",
        "strategy_family": "momentum_reversal",
        "mechanism": "short_term_momentum",
        "dataset": "stock_cn",
        "candidate_id": "bootstrap_seed_001",
    },
    # 价值因子 - 基本面
    {
        "expression": "group_neutralize(ts_zscore(book_to_price / ts_mean(book_to_price, 252), 63), industry)",
        "sharpe": 1.5,
        "fitness": 0.58,
        "turnover": 0.08,
        "outcome": "PASS",
        "strategy_family": "value",
        "mechanism": "fundamental_value",
        "dataset": "stock_cn",
        "candidate_id": "bootstrap_seed_002",
    },
    # 波动率 - 风险因子
    {
        "expression": "group_neutralize(-ts_zscore(ts_std_dev(returns, 21), 252), industry)",
        "sharpe": 1.3,
        "fitness": 0.52,
        "turnover": 0.15,
        "outcome": "PASS",
        "strategy_family": "volatility",
        "mechanism": "low_volatility_anomaly",
        "dataset": "stock_cn",
        "candidate_id": "bootstrap_seed_003",
    },
    # 趋势跟随
    {
        "expression": "group_neutralize(ts_zscore(ts_corr(close, ts_rank(volume, 20), 63), 252), industry)",
        "sharpe": 1.6,
        "fitness": 0.60,
        "turnover": 0.18,
        "outcome": "NEAR_PASS",
        "strategy_family": "trend_following",
        "mechanism": "price_volume_trend",
        "dataset": "stock_cn",
        "candidate_id": "bootstrap_seed_004",
    },
    # 质量因子
    {
        "expression": "group_neutralize(ts_zscore(roe / ts_std_dev(roe, 252), 126), industry)",
        "sharpe": 1.4,
        "fitness": 0.55,
        "turnover": 0.10,
        "outcome": "PASS",
        "strategy_family": "quality",
        "mechanism": "stable_profitability",
        "dataset": "stock_cn",
        "candidate_id": "bootstrap_seed_005",
    },
]


def inject_bootstrap_seeds(feedback_db_path: str) -> int:
    """Inject bootstrap seeds into empty feedback database.

    Returns number of seeds injected.
    """
    import sqlite3
    from datetime import datetime, timezone
    from alpha_mining.domain.expression_normalization import (
        exact_hash,
        structure_signature,
        operator_topology,
    )

    conn = sqlite3.connect(feedback_db_path)
    cur = conn.cursor()

    # Ensure table exists
    cur.execute(
        """CREATE TABLE IF NOT EXISTS candidate_outcomes (
            request_hash TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL DEFAULT '',
            expression TEXT NOT NULL DEFAULT '',
            topic_id TEXT NOT NULL DEFAULT '',
            hypothesis_id TEXT NOT NULL DEFAULT '',
            research_family TEXT NOT NULL DEFAULT '',
            strategy_family TEXT NOT NULL DEFAULT '',
            mechanism TEXT NOT NULL DEFAULT '',
            dataset TEXT NOT NULL DEFAULT '',
            parent_template TEXT NOT NULL DEFAULT '',
            exact_hash TEXT NOT NULL DEFAULT '',
            parameter_skeleton TEXT NOT NULL DEFAULT '',
            field_skeleton TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL,
            sharpe REAL,
            fitness REAL,
            turnover REAL,
            checks_json TEXT NOT NULL DEFAULT '[]',
            error_category TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL
        )"""
    )

    # Check if already has data
    count = cur.execute("SELECT COUNT(*) FROM candidate_outcomes WHERE outcome IN ('PASS', 'NEAR_PASS')").fetchone()[0]
    if count > 0:
        conn.close()
        return 0

    injected = 0
    for seed in BOOTSTRAP_SEEDS:
        expr = seed["expression"]
        request_hash = exact_hash(expr)
        field_skeleton = structure_signature(expr)

        cur.execute(
            """INSERT OR IGNORE INTO candidate_outcomes (
                request_hash, candidate_id, expression, outcome, sharpe, fitness, turnover,
                strategy_family, mechanism, dataset, field_skeleton,
                checks_json, error_category, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request_hash,
                seed["candidate_id"],
                expr,
                seed["outcome"],
                seed["sharpe"],
                seed["fitness"],
                seed["turnover"],
                seed["strategy_family"],
                seed["mechanism"],
                seed["dataset"],
                field_skeleton,
                "[]",  # checks_json
                "",    # error_category
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        if cur.rowcount > 0:
            injected += 1

    conn.commit()
    conn.close()
    return injected


if __name__ == "__main__":
    injected = inject_bootstrap_seeds("alpha_feedback.sqlite")
    print(f"Injected {injected} bootstrap seeds")
