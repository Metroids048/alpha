from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


def test_queue_lock_blocks_a_second_writer_and_recovers_stale_lock(tmp_path: Path) -> None:
    from alpha_mining.storage.csv_queue import CandidateCsvQueue, QueueLockedError

    queue = CandidateCsvQueue(tmp_path / "候选Alpha.csv", tmp_path / "处理事件.csv", stale_lock_seconds=1)
    with queue.writer():
        with pytest.raises(QueueLockedError):
            with queue.writer():
                pass

    queue.lock_path.write_text(json.dumps({"pid": os.getpid(), "created_at": "stale"}), encoding="utf-8")
    old = time.time() - 10
    os.utime(queue.lock_path, (old, old))
    with queue.writer():
        assert queue.lock_path.exists()
    assert not queue.lock_path.exists()


def test_queue_uses_atomic_replace_and_appends_events(tmp_path: Path, monkeypatch) -> None:
    from alpha_mining.storage.csv_queue import CandidateCsvQueue

    queue = CandidateCsvQueue(tmp_path / "候选Alpha.csv", tmp_path / "处理事件.csv")
    replacements: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def tracked_replace(source, target):
        replacements.append((Path(source), Path(target)))
        return original_replace(source, target)

    monkeypatch.setattr(os, "replace", tracked_replace)
    row = queue.empty_candidate()
    row.update(
        candidate_id="candidate-1",
        expression="rank(fixture_close)",
        canonical_signature="{}",
        queue_status="GENERATED",
    )
    with queue.writer():
        queue.upsert(row)
        queue.transition("candidate-1", "QUEUED")

    assert len(replacements) == 2
    assert all(source.name.endswith(".tmp") for source, _ in replacements)
    events = queue.read_events()
    assert [event["new_status"] for event in events] == ["GENERATED", "QUEUED"]
