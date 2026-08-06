#!/usr/bin/env python3
"""初始化基础表数据"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

database = Path("数据/本地运行产物/数据库/research_memory.sqlite")
conn = sqlite3.connect(str(database))
c = conn.cursor()

now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

# 1. 初始化research_topics
topics = [
    ('momentum', '动量', 'Momentum', 'momentum', 'price', 'Momentum and trend following', 'bootstrap'),
    ('mean_reversion', '均值回归', 'Mean Reversion', 'mean_reversion', 'price', 'Mean reversion strategies', 'bootstrap'),
    ('volume', '成交量', 'Volume', 'volume', 'volume', 'Volume-based patterns', 'bootstrap'),
    ('correlation', '相关性', 'Correlation', 'correlation', 'cross_sectional', 'Cross-sectional relationships', 'bootstrap'),
    ('volatility', '波动率', 'Volatility', 'volatility', 'price', 'Volatility-based signals', 'bootstrap'),
]

print("初始化 research_topics...")
for topic_id, name_cn, name_en, category, data_cat, description, source in topics:
    c.execute('''
        INSERT OR REPLACE INTO research_topics (
            topic_id, topic_name_cn, topic_name_en, category, data_category,
            description, source, active, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (topic_id, name_cn, name_en, category, data_cat, description, source, 1, now))
    print(f"  ✓ {topic_id}: {name_cn} / {name_en}")

# 2. 初始化hypotheses
hypotheses = [
    ('hyp_momentum_vwap', 'momentum', 'VWAP动量信号', 'VWAP momentum signal', 'vwap_delta', 'short', 'active'),
    ('hyp_reversion_zscore', 'mean_reversion', 'Z分数均值回归', 'Z-score mean reversion', 'zscore', 'short', 'active'),
    ('hyp_volume_rank', 'volume', '成交量排序信号', 'Volume ranking signal', 'volume_rank', 'short', 'active'),
    ('hyp_corr_price_vol', 'correlation', '价量相关性', 'Price-volume correlation', 'price_volume_corr', 'short', 'active'),
    ('hyp_volatility_spike', 'volatility', '波动率突破检测', 'Volatility spike detection', 'volatility_breakout', 'short', 'active'),
]

print("\n初始化 hypotheses...")
for hyp_id, topic_id, statement_cn, statement_en, mechanism, horizon, status in hypotheses:
    c.execute('''
        INSERT OR REPLACE INTO hypotheses (
            hypothesis_id, topic_id, statement_cn, statement_en, mechanism, horizon, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (hyp_id, topic_id, statement_cn, statement_en, mechanism, horizon, status, now))
    print(f"  ✓ {hyp_id}: {statement_cn}")

# 3. 初始化data_mappings
mappings = [
    ('hyp_momentum_vwap', 'vwap', 'TOP3000'),
    ('hyp_momentum_vwap', 'close', 'TOP3000'),
    ('hyp_reversion_zscore', 'close', 'TOP3000'),
    ('hyp_reversion_zscore', 'returns', 'TOP3000'),
    ('hyp_volume_rank', 'volume', 'TOP3000'),
    ('hyp_corr_price_vol', 'high', 'TOP3000'),
    ('hyp_corr_price_vol', 'volume', 'TOP3000'),
    ('hyp_volatility_spike', 'close', 'TOP3000'),
    ('hyp_volatility_spike', 'high', 'TOP3000'),
    ('hyp_volatility_spike', 'low', 'TOP3000'),
]

print("\n初始化 data_mappings...")
for hyp_id, data_field, dataset_id in mappings:
    c.execute('''
        INSERT OR REPLACE INTO data_mappings (hypothesis_id, data_field, dataset_id, created_at)
        VALUES (?, ?, ?, ?)
    ''', (hyp_id, data_field, dataset_id, now))
    print(f"  ✓ {hyp_id} -> {data_field} ({dataset_id})")

conn.commit()

# 验证
print("\n" + "="*50)
print("验证数据:")
c.execute("SELECT COUNT(*) FROM research_topics WHERE active=1")
print(f"  research_topics (active): {c.fetchone()[0]}")

c.execute("SELECT COUNT(*) FROM hypotheses WHERE status='active'")
print(f"  hypotheses (active): {c.fetchone()[0]}")

c.execute("SELECT COUNT(*) FROM data_mappings")
print(f"  data_mappings: {c.fetchone()[0]}")

# 测试完整查询
print("\n测试research_specs查询:")
c.execute("""
    SELECT h.hypothesis_id, t.topic_id, t.category,
           h.mechanism, h.horizon, m.data_field, m.dataset_id
    FROM hypotheses h
    JOIN research_topics t ON t.topic_id=h.topic_id
    JOIN data_mappings m ON m.hypothesis_id=h.hypothesis_id
    WHERE COALESCE(h.status,'active')='active' AND COALESCE(t.active,1)=1
    LIMIT 5
""")
specs = c.fetchall()
print(f"  查询结果数量: {len(specs)}")
for spec in specs:
    print(f"    {spec[0]}: {spec[2]}/{spec[3]} -> {spec[5]}")

conn.close()
print("\n✓ 初始化完成！")
