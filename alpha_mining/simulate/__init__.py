"""Simulation backends (sync in monolith; async high-concurrency here)."""

from alpha_mining.simulate.async_batch import run_async_simulation_batch

__all__ = ["run_async_simulation_batch"]
