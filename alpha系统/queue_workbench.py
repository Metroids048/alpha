"""Thin PyQt client for the alpha_mining candidate workflow service."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class QueueTableModel(QAbstractTableModel):
    headers = ("Candidate", "State", "Alpha ID", "Sharpe", "Fitness", "Turnover", "Description", "Error")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows = []

    def set_items(self, items) -> None:
        self.beginResetModel()
        self.rows = list(items)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        item = self.rows[index.row()]
        metrics = item.metrics
        values = (item.candidate_id, item.state, item.alpha_id, metrics.get("sharpe", ""), metrics.get("fitness", ""), metrics.get("turnover", ""), item.description_status, item.last_error)
        return str(values[index.column()])

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return None


class _PrepareThread(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self.service = service

    def run(self) -> None:
        try:
            self.completed.emit(self.service.prepare_once())
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class QueueWorkbench(QWidget):
    """Queue view with filter, prepare/resume and batch freeze actions."""

    def __init__(self, database: str | Path | None = None, parent=None, service=None) -> None:
        super().__init__(parent)
        from alpha_mining.factory.operator_service import CandidateWorkflowService

        self.service = service or CandidateWorkflowService(database or ROOT / "数据" / "本地运行产物" / "数据库" / "research_memory.sqlite")
        self.model = QueueTableModel(self)
        self.filter = QComboBox()
        self.filter.addItem("All", "")
        for state in ("PENDING_SIMULATION", "SIMULATING", "WAITING_CHECKS", "NEAR_PASS", "READY_TO_SUBMIT", "DESCRIPTION_VALIDATED", "AWAITING_BATCH_CONFIRMATION", "SUBMITTED", "FAR_FAIL"):
            self.filter.addItem(state, state)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.prepare_button = QPushButton("开始/恢复模拟")
        self.prepare_button.clicked.connect(self.prepare)
        self.tune_button = QPushButton("NEAR_PASS 调参")
        self.tune_button.clicked.connect(self.tune)
        self.batch_button = QPushButton("冻结批次")
        self.batch_button.clicked.connect(self.freeze_batch)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.filter)
        toolbar.addWidget(self.prepare_button)
        toolbar.addWidget(self.tune_button)
        toolbar.addWidget(self.batch_button)
        toolbar.addWidget(self.refresh_button)
        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)
        self._thread = None
        self.refresh()

    def refresh(self) -> None:
        value = self.filter.currentData()
        states = [value] if value else None
        self.model.set_items(self.service.list_items(states=states))

    def prepare(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        self.prepare_button.setEnabled(False)
        self._thread = _PrepareThread(self.service, self)
        self._thread.completed.connect(self._prepared)
        self._thread.failed.connect(self._failed)
        self._thread.finished.connect(lambda: self.prepare_button.setEnabled(True))
        self._thread.start()

    def _prepared(self, _summary) -> None:
        self.refresh()

    def _failed(self, message: str) -> None:
        QMessageBox.warning(self, "工作流错误", message)

    def _selected_ids(self) -> list[str]:
        return [self.model.rows[index.row()].candidate_id for index in self.table.selectionModel().selectedRows()]

    def tune(self) -> None:
        for candidate_id in self._selected_ids():
            self.service.retry_item(candidate_id)
        self.refresh()

    def freeze_batch(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "批次", "请选择候选")
            return
        try:
            batch = self.service.submit_batch(ids, execute=False)
        except Exception as exc:
            QMessageBox.warning(self, "批次", str(exc))
            return
        QMessageBox.information(self, "批次已冻结", f"{batch.batch_id}\n{len(batch.candidate_ids)} candidates")
        self.refresh()
