"""One complete platform Check admission per behavior cluster per round."""

from __future__ import annotations


class BehaviorRoundQuota:
    def __init__(self) -> None:
        self._admitted: set[str] = set()

    def admit_full_check(self, cluster_id: str) -> bool:
        key = str(cluster_id or "").strip()
        if not key or key in self._admitted:
            return False
        self._admitted.add(key)
        return True
