#!/usr/bin/env python3
"""Test generator directly to see why it's not generating candidates."""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dataclasses import dataclass
from alpha_mining.factory.orchestrator import FactoryOrchestrator

@dataclass(frozen=True)
class MockSimResult:
    alpha_id: str = "mock-123"
    status: str = "COMPLETE"
    metrics: dict = None
    checks: list = None
    raw: dict = None

    def __post_init__(self):
        object.__setattr__(self, 'metrics', self.metrics or {})
        object.__setattr__(self, 'checks', self.checks or [])
        object.__setattr__(self, 'raw', self.raw or {})

class MockSimulator:
    def simulate(self, *, expression: str, settings: dict, alpha_type: str = "REGULAR"):
        return MockSimResult()

db_path = Path("alpha_state.sqlite3")
simulator = MockSimulator()

orchestrator = FactoryOrchestrator(database=db_path, simulation=simulator)

print("🔍 Testing generator directly...\n")

# Get research specs
specs = orchestrator._research_specs()
print(f"📋 Research specs: {len(specs)}")
if specs:
    print(f"   First spec: {specs[0]}")
else:
    print(f"   Deferral reason: {orchestrator._generation_deferral_reason}")

print("\n🎲 Trying to run a cycle...")
try:
    result = orchestrator.run_simulate(batch_size=3)
    print(f"✅ Cycle result: {result}")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
