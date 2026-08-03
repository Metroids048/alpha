"""Network-free candidate generation workflow.

Service imports are deliberately lazy: shared validation imports the metadata
types, while the service imports that validator.  Eager package imports would
therefore create a circular import before either contract is available.
"""


def __getattr__(name: str):
    if name in {"OfflineGenerationSummary", "run_offline_generation"}:
        from .service import OfflineGenerationSummary, run_offline_generation

        return {
            "OfflineGenerationSummary": OfflineGenerationSummary,
            "run_offline_generation": run_offline_generation,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["OfflineGenerationSummary", "run_offline_generation"]
