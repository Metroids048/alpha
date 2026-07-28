"""Test if run_pipeline_cycle.py loads .env correctly"""
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "run_pipeline_cycle.py", "--help"],
    capture_output=True,
    text=True,
    timeout=10
)

print(f"Exit code: {result.returncode}")
print(f"Stdout preview: {result.stdout[:200]}")
if result.stderr:
    print(f"Stderr preview: {result.stderr[:200]}")
