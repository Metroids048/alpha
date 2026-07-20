#!/usr/bin/env python3
"""Single pipeline cycle entry: optional resilient async patch, then delegate to v50 CLI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _strip_resilient_flag(argv: list[str]) -> tuple[list[str], bool]:
    resilient = False
    out: list[str] = []
    for arg in argv:
        if arg == "--resilient-async":
            resilient = True
            continue
        out.append(arg)
    env_on = os.environ.get("ALPHA_RESILIENT_ASYNC", "").strip().lower() in ("1", "true", "yes")
    return out, resilient or env_on


def main() -> int:
    argv, use_resilient = _strip_resilient_flag(sys.argv[1:])
    sys.argv = [sys.argv[0], *argv]
    if use_resilient:
        from alpha_mining.simulate.resilient_async import apply_patch

        apply_patch()
    import auto_alpha_pipeline_rebuilt_v50 as pipeline

    return int(pipeline.main())


if __name__ == "__main__":
    raise SystemExit(main())
