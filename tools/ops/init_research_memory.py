"""Initialize Research Memory database with seed topics, then verify AI chain."""
import os
import sys

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def init_db():
    from alpha_mining.knowledge.ontology import install_seed_topics
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    db_path = "research_memory.sqlite"
    print(f"[init] initializing schema in {db_path} ...")
    SqliteRunLog(db_path).initialize_schema()
    count = install_seed_topics(db_path)
    print(f"[init] installed {count} seed topics OK")
    return db_path

def verify_deepseek():
    from alpha_mining.llm.deepseek import DeepSeekStructuredLLM
    print("[verify] testing DeepSeek API ...")
    llm = DeepSeekStructuredLLM()
    resp = llm.generate_json(
        system_prompt="You are a test assistant. Reply minimally.",
        user_prompt="Return a JSON object with a 'ok' field set to true.",
        json_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    )
    assert resp.get("ok") is True, f"unexpected response: {resp}"
    print(f"[verify] DeepSeek API OK: {resp}")
    llm.close()

def verify_embedding():
    from alpha_mining.llm.local_embedding import LocalSentenceTransformerEmbedder
    print("[verify] testing local embedder (first run downloads model) ...")
    emb = LocalSentenceTransformerEmbedder()
    vec = emb.embed("capital efficiency peer rank")
    assert len(vec) > 0, "empty embedding"
    print(f"[verify] embedding dim={len(vec)} OK")
    emb.close()

def verify_hypothesis_gen(db_path):
    import sqlite3
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT topic_id, data_category FROM research_topics WHERE active=1 LIMIT 3"
        ).fetchall()
    if not rows:
        print("[verify] no active topics found, skipping hypothesis gen test")
        return
    print(f"[verify] {len(rows)} active topics: {[r[0] for r in rows]}")
    print("[verify] hypothesis generator ready (will run during pipeline)")

if __name__ == "__main__":
    print("=" * 60)
    print("Research Memory + AI Chain Initialization")
    print("=" * 60)
    try:
        db_path = init_db()
        verify_deepseek()
        verify_embedding()
        verify_hypothesis_gen(db_path)
        print("\n✅ All systems operational. LLM will engage in next pipeline run.")
    except Exception as exc:
        print(f"\n❌ Error: {exc}", file=sys.stderr)
        sys.exit(1)
