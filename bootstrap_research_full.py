#!/usr/bin/env python3
"""Bootstrap complete research memory: topics, hypotheses, and data mappings."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

print("🔧 Bootstrapping research configuration...")

# 1. Install seed topics
print("\n📚 Step 1: Installing seed topics from seed_topics.yaml...")
from alpha_mining.knowledge.ontology import install_seed_topics
db_path = "alpha_state.sqlite3"
installed_count = install_seed_topics(db_path)
print(f"✅ Installed {installed_count} research topics")

# 2. Check if hypotheses exist
print("\n🧪 Step 2: Checking hypotheses...")
import sqlite3
con = sqlite3.connect(db_path)
cursor = con.cursor()
cursor.execute("SELECT COUNT(*) FROM hypotheses")
hyp_count = cursor.fetchone()[0]
print(f"📊 Current hypotheses: {hyp_count}")

# 3. Check data mappings
print("\n🗺️  Step 3: Checking data mappings...")
cursor.execute("SELECT COUNT(*) FROM data_mappings")
mappings_count = cursor.fetchone()[0]
print(f"📊 Current data mappings: {mappings_count}")

# 4. Generate initial hypotheses if missing
if hyp_count == 0:
    print("\n🔬 Step 4: Generating initial hypotheses...")
    cursor.execute("SELECT topic_id, topic_name_en, data_category FROM research_topics WHERE active=1 LIMIT 5")
    topics = cursor.fetchall()

    from alpha_mining.knowledge.ontology import ALPHA_FAMILIES

    hypothesis_count = 0
    for topic_id, topic_name, data_category in topics:
        for family in ALPHA_FAMILIES[:3]:  # A, B, C families
            hypothesis_id = f"{topic_id}_{family.family_id}_21d"
            statement_cn = f"通过{family.name}模式测试{topic_name}"
            statement_en = f"Test {topic_name} via {family.name} pattern"
            mechanism = f"Using {family.pattern} with {family.rationale}"
            cursor.execute("""
                INSERT OR IGNORE INTO hypotheses
                (hypothesis_id, topic_id, statement_cn, statement_en, mechanism, horizon, status, created_at, llm_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 'bootstrap')
            """, (
                hypothesis_id,
                topic_id,
                statement_cn,
                statement_en,
                mechanism,
                "21d",
                "active"
            ))
            hypothesis_count += 1

            # Add data_mappings for this hypothesis
            if data_category == "fundamental":
                fields = [("analyst10", "eps_mean"), ("analyst11", "sales_mean")]
            elif data_category == "price":
                fields = [("pv1", "close"), ("pv1", "volume")]
            else:
                fields = [("analyst10", "eps_mean")]

            for dataset_id, data_field in fields:
                mapping_id = f"{hypothesis_id}_{dataset_id}_{data_field}"
                cursor.execute("""
                    INSERT OR IGNORE INTO data_mappings
                    (mapping_id, hypothesis_id, data_field, dataset_id, selected_by, created_at)
                    VALUES (?, ?, ?, ?, 'bootstrap', datetime('now'))
                """, (mapping_id, hypothesis_id, data_field, dataset_id))

    con.commit()
    cursor.execute("SELECT COUNT(*) FROM hypotheses")
    new_hyp_count = cursor.fetchone()[0]
    print(f"✅ Generated {new_hyp_count} initial hypotheses")

    cursor.execute("SELECT COUNT(*) FROM data_mappings")
    new_mappings_count = cursor.fetchone()[0]
    print(f"✅ Generated {new_mappings_count} data mappings")

con.close()

print("\n" + "="*60)
print("✅ Research configuration bootstrap complete!")
print("="*60)
print("\n📊 Final status:")
con = sqlite3.connect(db_path)
cursor = con.cursor()
cursor.execute("SELECT COUNT(*) FROM research_topics WHERE active=1")
print(f"  Active topics: {cursor.fetchone()[0]}")
cursor.execute("SELECT COUNT(*) FROM hypotheses WHERE status='active'")
print(f"  Active hypotheses: {cursor.fetchone()[0]}")
cursor.execute("SELECT COUNT(*) FROM data_mappings")
print(f"  Data mappings: {cursor.fetchone()[0]}")
con.close()

print("\n🚀 Pipeline can now generate alphas!")
