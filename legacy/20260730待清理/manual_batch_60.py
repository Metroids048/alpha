#!/usr/bin/env python3
"""Test why batch_size=60 fails but batch_size=3 works."""

import sys
sys.path.insert(0, ".")

from alpha_mining.factory.orchestrator import FactoryOrchestrator
from alpha_mining.platform.gateway import PlatformGateway

DB = "alpha_state.sqlite3"

print("\n🧪 Testing batch_size=60 (same as pipeline)...")
gateway = PlatformGateway(
    state_path="auth_state.sqlite3",
    database=DB,
    lock_path="platform.lock",
    min_interval=1.0,
)

orchestrator = FactoryOrchestrator(DB, gateway)

try:
    result = orchestrator.run_simulate(batch_size=60)
    print(f"✅ Result: {result}")
    print(f"   Generated: {result.generated}")
    print(f"   Simulated: {result.simulated}")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
