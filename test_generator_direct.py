#!/usr/bin/env python3
"""Test generator directly to see why it's not generating candidates."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alpha_mining.factory.orchestrator import FactoryOrchestrator
from alpha_mining.simulate.async_batch import AsyncBatchSimulator

db_path = Path("alpha_state.sqlite3")

# Create a mock simulator (we won't actually simulate)
simulator = AsyncBatchSimulator(db_path=db_path)

orchestrator = FactoryOrchestrator(
    database=db_path,
    simulation=simulator
)

print("🔍 Testing generator directly...\n")

# Get research specs
specs = orchestrator._research_specs(batch_size=5)
print(f"📋 Research specs: {len(specs)}")
if specs:
    print(f"   First spec: {specs[0]}")

print("\n🎲 Trying to generate candidates...")
try:
    result = orchestrator._generate_batch(target=3)
    print(f"✅ Generated: {result}")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
