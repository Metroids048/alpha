from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PyQt5") is None,
    reason="optional GUI dependencies are not installed",
)


def test_queue_model_filters_and_displays_authoritative_items(qtbot, tmp_path: Path) -> None:
    from alpha_mining.factory.operator_service import CandidateWorkflowService
    from alpha系统.queue_workbench import QueueWorkbench

    service = CandidateWorkflowService(tmp_path / "research.sqlite", gateway=object())
    service.store.upsert_candidate({"candidate_id": "candidate-1", "request_hash": "request-1", "expression": "rank(fixture_close)"})
    widget = QueueWorkbench(service=service)
    qtbot.addWidget(widget)
    assert widget.model.rowCount() == 1
    widget.filter.setCurrentIndex(widget.filter.findData("PENDING_SIMULATION"))
    assert widget.model.rowCount() == 1
