"""Shared, fail-closed contract for WorldQuant simulation settings.

The contract deliberately consumes a locally synchronized capability snapshot.
Neither generation nor the network gateway guesses platform enum values.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_REQUIRED_KEYS = (
    "alpha_type",
    "region",
    "universe",
    "delay",
    "decay",
    "neutralization",
    "truncation",
    "language",
)
# alpha_type is validated like the rest, but it is a property of the simulation
# payload (its outer "type"), not of "settings".  The endpoint refuses it there:
#   POST /simulations -> 400 {"settings":{"alphaType":["Unexpected property."]}}
_PAYLOAD_ONLY_KEYS = frozenset({"alpha_type"})


@dataclass(frozen=True)
class SimulationSettingsContract:
    """Canonicalize settings only against a synchronized platform schema."""

    defaults: dict[str, Any]
    allowed_values: dict[str, tuple[Any, ...]]
    context: dict[str, Any]
    source: Path

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        max_age_hours: float = 24.0,
        now: float | None = None,
    ) -> "SimulationSettingsContract":
        source = Path(path)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"simulation settings schema missing: {source}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"simulation settings schema unreadable: {source}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != "simulation-settings-v1":
            raise ValueError("simulation settings schema has an unsupported version")
        fetched_at = raw.get("fetched_at")
        try:
            age_hours = ((time.time() if now is None else now) - float(fetched_at)) / 3600
        except (TypeError, ValueError) as exc:
            raise ValueError("simulation settings schema has invalid fetched_at") from exc
        if age_hours < -1 or age_hours > float(max_age_hours):
            raise ValueError("simulation settings schema is stale")
        defaults = raw.get("defaults")
        allowed = raw.get("allowed_values")
        context = raw.get("context")
        if not isinstance(defaults, dict) or not isinstance(allowed, dict) or not isinstance(context, dict):
            raise ValueError("simulation settings schema has invalid sections")
        missing = [key for key in _REQUIRED_KEYS if key not in defaults or not isinstance(allowed.get(key), list)]
        if missing:
            raise ValueError("simulation settings schema missing keys: " + ", ".join(missing))
        normalized_allowed = {key: tuple(values) for key, values in allowed.items()}
        if any(not normalized_allowed[key] for key in _REQUIRED_KEYS):
            raise ValueError("simulation settings schema has an empty allowed value set")
        return cls(dict(defaults), normalized_allowed, dict(context), source)

    def prepare(self, settings: dict[str, Any] | None) -> dict[str, Any]:
        """Canonicalize what belongs in the request's ``settings`` object.

        Keys the schema enumerates are canonicalized against it.  Keys it does
        not enumerate are passed through untouched: the synced schema carries no
        ``instrumentType``, while the endpoint requires one, so dropping unknown
        keys would make every simulation a 400.
        """

        source = settings if isinstance(settings, dict) else {}
        merged = {**self.defaults, **source}
        prepared = {key: value for key, value in source.items() if key not in _PAYLOAD_ONLY_KEYS}
        for key in _REQUIRED_KEYS:
            value = self._canonical_value(key, merged[key])
            if key not in _PAYLOAD_ONLY_KEYS:
                prepared[key] = value
        for key in ("region", "universe", "delay"):
            if key in self.context and prepared[key] != self._canonical_value(key, self.context[key]):
                raise ValueError(f"{key} does not match the synchronized schema context")
        return prepared

    def alpha_type(self, settings: dict[str, Any] | None = None) -> Any:
        """The payload's ``type``, canonicalized against the same schema."""

        source = settings if isinstance(settings, dict) else {}
        return self._canonical_value("alpha_type", {**self.defaults, **source}["alpha_type"])

    def _canonical_value(self, key: str, value: Any) -> Any:
        allowed = self.allowed_values[key]
        if isinstance(value, str):
            matches = [item for item in allowed if isinstance(item, str) and item.casefold() == value.strip().casefold()]
        elif isinstance(value, bool):
            matches = []
        elif isinstance(value, int):
            matches = [item for item in allowed if isinstance(item, int) and not isinstance(item, bool) and item == value]
        elif isinstance(value, float):
            matches = [item for item in allowed if isinstance(item, (int, float)) and not isinstance(item, bool) and float(item) == value]
        else:
            matches = []
        if len(matches) != 1:
            raise ValueError(f"{key} is not a unique platform-allowed value")
        return matches[0]
