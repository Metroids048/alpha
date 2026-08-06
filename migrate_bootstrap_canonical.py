import sqlite3
from pathlib import Path

canonical_db = Path('数据/本地运行产物/数据库/research_memory.sqlite')
legacy_db = Path('research_memory.sqlite')

print("=== 迁移bootstrap种子到canonical数据库 ===")

# 读取legacy的bootstrap种子
with sqlite3.connect(legacy_db) as legacy_conn:
    cursor = legacy_conn.execute("PRAGMA table_info(candidate_outcomes)")
    cols = [row[1] for row in cursor]
    print(f"Legacy列: {cols}")

    cursor = legacy_conn.execute("SELECT * FROM candidate_outcomes WHERE outcome='PASS'")
    bootstrap_rows = cursor.fetchall()
    print(f"Legacy bootstrap种子: {len(bootstrap_rows)} 行")

# 写入canonical数据库
if bootstrap_rows:
    with sqlite3.connect(canonical_db) as canonical_conn:
        # 确保表结构一致
        canonical_conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_outcomes (
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
                observed_at TEXT NOT NULL,
                quality_status TEXT,
                quality_reasons_json TEXT,
                self_correlation TEXT,
                prod_correlation TEXT,
                knowledge_refs_json TEXT,
                parent_candidate_id TEXT,
                repair_action TEXT,
                operator_topology TEXT,
                region TEXT,
                universe_name TEXT,
                delay TEXT,
                knowledge_usage_mode TEXT,
                context_refs_json TEXT,
                knowledge_context_hash TEXT,
                degraded INTEGER,
                expression TEXT
            )
        """)

        # 用legacy的列名构建INSERT
        with sqlite3.connect(legacy_db) as legacy_conn:
            cursor = legacy_conn.execute("PRAGMA table_info(candidate_outcomes)")
            legacy_cols = [row[1] for row in cursor]

            # 读取数据
            cursor = legacy_conn.execute(f"SELECT {','.join(legacy_cols)} FROM candidate_outcomes WHERE outcome='PASS'")
            rows = cursor.fetchall()

        # 插入到canonical
        placeholders = ','.join(['?' for _ in legacy_cols])
        for row in rows:
            canonical_conn.execute(
                f"INSERT OR IGNORE INTO candidate_outcomes ({','.join(legacy_cols)}) VALUES ({placeholders})",
                row
            )
        canonical_conn.commit()

        count = canonical_conn.execute("SELECT COUNT(*) FROM candidate_outcomes WHERE outcome='PASS'").fetchone()[0]
        print(f"Canonical PASS记录: {count} 行")

    # 清空legacy的candidate_outcomes
    with sqlite3.connect(legacy_db) as legacy_conn:
        legacy_conn.execute("DELETE FROM candidate_outcomes")
        legacy_conn.commit()
        count = legacy_conn.execute("SELECT COUNT(*) FROM candidate_outcomes").fetchone()[0]
        print(f"Legacy清空后: {count} 行")

print("\n✅ 迁移完成，dual-ledger冲突应已解决")
