"""
One-shot AI setup: init Research Memory DB + verify DeepSeek + verify embedding.
Run with: .venv\Scripts\python setup_ai.py
"""
import os, sys, sqlite3
from pathlib import Path

# ── load .env ──────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).parent
DB = ROOT / "research_memory.sqlite"

# ── step 1: init schema + seed topics ──────────────────────────────────────
print("[1/3] Initializing Research Memory DB ...")
sys.path.insert(0, str(ROOT))

from alpha_mining.storage.sqlite_store import SqliteRunLog
from alpha_mining.knowledge.ontology import install_seed_topics

SqliteRunLog(DB).initialize_schema()
n = install_seed_topics(DB)
print(f"      ✓ {n} seed topics installed in {DB}")

# quick sanity
with sqlite3.connect(DB) as con:
    rows = con.execute(
        "SELECT topic_id, data_category FROM research_topics WHERE active=1"
    ).fetchall()
print(f"      ✓ {len(rows)} active topics: {[r[0] for r in rows[:5]]}...")

# ── step 2: verify DeepSeek API ────────────────────────────────────────────
print("[2/3] Verifying DeepSeek API ...")
from alpha_mining.llm.deepseek import DeepSeekStructuredLLM

llm = DeepSeekStructuredLLM()
resp = llm.generate_json(
    system_prompt="You are a minimal test assistant.",
    user_prompt='Return {"ok": true}.',
    json_schema={
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    },
)
assert resp.get("ok") is True, f"Bad response: {resp}"
print(f"      ✓ DeepSeek API responding (model={llm.model_id})")
llm.close()

# ── step 3: verify local embedding ────────────────────────────────────────
print("[3/3] Verifying local sentence-transformer embedding ...")
from alpha_mining.llm.local_embedding import LocalSentenceTransformerEmbedder

emb = LocalSentenceTransformerEmbedder()
vec = emb.embed("capital efficiency peer rank alpha")
assert len(vec) > 100, f"embedding too short: {len(vec)}"
print(f"      ✓ Embedding dim={len(vec)}")
emb.close()

print()
print("=" * 60)
print("✅  ALL DONE — LLM fully wired into alpha generation pipeline.")
print("   phase2_llm_enabled   = True  (generates new hypotheses)")
print("   phase3_llm_grammar   = True  (DeepSeek writes expressions)")
print("   historical alphas     → fed as positive examples to prompts")
print("=" * 60)
