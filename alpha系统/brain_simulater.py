# fetch_aid_arr = [
# ]
fetch_aid_arr = None


DEFAULT_EMAIL = ""
DEFAULT_PASSWORD = ""


alpha_name = None
need_complete = True
show_auto_fill = False
CREDENTIALS_FILE = "brain_credentials.json"


from common_config import *
from pc_range import estimate_pc_range
from queue_workbench import QueueWorkbench

import re
import os
import sys
import csv
import json
import time
import math
import copy
import pytz
import glob
import base64
import shutil
import asyncio
import inspect
import requests
import winsound
import webbrowser
import tempfile
import subprocess
import ctypes
import ctypes.wintypes
import threading
import importlib.util
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep
from datetime import datetime as dt_datetime, datetime, timezone, timedelta
from itertools import groupby, product

import matplotlib
matplotlib.use('Qt5Agg')
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'SimSun', 'KaiTi', 'FangSong', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.ticker as mticker

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFormLayout, QLabel, QLineEdit, QTextEdit, QComboBox, QDoubleSpinBox,
    QSpinBox, QPushButton, QProgressBar, QGroupBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QSplitter, QMessageBox, QCheckBox,
    QStatusBar, QTabBar, QScrollArea, QSizePolicy, QSpacerItem,
    QLayout, QMenu, QListView, QAbstractItemView, QDialog, QSystemTrayIcon,
    QShortcut
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint, QRect, QSize, QStringListModel, QSortFilterProxyModel, QMimeData, QByteArray, QFileSystemWatcher
from PyQt5.QtCore import pyqtSignal as _sig
from PyQt5.QtGui import QPainter, QPixmap, QPen, QDrag, QFont, QColor, QKeySequence, QTextCharFormat, QSyntaxHighlighter, QTextCursor, QTextOption, QIcon


# ──────────────────────────────────────────────
#  Theme / Style Constants (Catppuccin Mocha)
# ──────────────────────────────────────────────
STYLES = {
    'dialog':       "QDialog { background-color: #1e1e2e; }",
    'input':        "QLineEdit { background: #313244; color: #cdd6f4; font-size: 11pt; border: 1px solid #45475a; border-radius: 4px; padding: 6px; }"
                    "QLineEdit:focus { border: 1px solid #89b4fa; }",
    'input_small':  "QLineEdit { background: #313244; color: #cdd6f4; font-size: 10pt; border: 1px solid #45475a; border-radius: 4px; padding: 4px; }"
                    "QLineEdit:focus { border: 1px solid #89b4fa; }",
    'btn':          "QPushButton { background: #45475a; color: #cdd6f4; font-size: 10pt; border: 1px solid #585b70; border-radius: 4px; padding: 6px 16px; }"
                    "QPushButton:hover { background: #585b70; }",
    'btn_danger':   "QPushButton { background: #f38ba8; color: #1e1e2e; font-size: 10pt; font-weight: bold; border: 1px solid #f38ba8; border-radius: 4px; padding: 6px 16px; }"
                    "QPushButton:hover { background: #eba0ac; }",
    'btn_success':  "QPushButton { background: #a6e3a1; color: #1e1e2e; font-size: 10pt; font-weight: bold; border: 1px solid #a6e3a1; border-radius: 4px; padding: 6px 16px; }"
                    "QPushButton:hover { background: #94e2d5; }",
    'combo':        "QComboBox { background: #313244; color: #cdd6f4; font-size: 10pt; border: 1px solid #45475a; border-radius: 4px; padding: 4px 8px; }"
                    "QComboBox::drop-down { border: none; }"
                    "QComboBox QAbstractItemView { background: #313244; color: #cdd6f4; selection-background-color: #45475a; }",
    'label_dim':    "color: #a6adc8; font-size: 10pt;",
    'label_preview': "color: #f9e2af; font-size: 9pt; padding: 2px;",
}


# ──────────────────────────────────────────────
#  Draggable Tab Bar
# ──────────────────────────────────────────────
class DraggableTabBar(QTabBar):
    """QTabBar that supports drag-and-drop reordering of tabs."""

    tabDropped = pyqtSignal(int, int)  # from_index, to_index
    _logs = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMovable(False)  # Disable built-in move, use custom drag
        self._from_index = -1
        self._drop_index = -1
        self._drag_start_pos = None
        self._is_dragging = False
        self._log("DraggableTabBar initialized")

    @classmethod
    def _log(cls, msg):
        cls._logs.append(msg)
        print(msg, flush=True)

    @classmethod
    def get_logs(cls):
        return "\n".join(cls._logs[-50:])

    def mousePressEvent(self, event):
        self._log(f"mousePressEvent: button={event.button()}, pos={event.pos()}, tabAt={self.tabAt(event.pos())}")
        if event.button() == Qt.LeftButton:
            self._from_index = self.tabAt(event.pos())
            self._drag_start_pos = event.pos()
            self._log(f"mousePressEvent (left): from_index={self._from_index}")
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Check if we should start a drag
        if (event.buttons() & Qt.LeftButton and self._from_index >= 0
            and self._drag_start_pos is not None and not self._is_dragging):
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            self._log(f"mouseMove: dist={dist}, threshold={QApplication.startDragDistance()}")
            if dist >= QApplication.startDragDistance():
                self._is_dragging = True
                # Start drag operation
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData("application/x-tabindex", QByteArray.number(self._from_index))
                drag.setMimeData(mime)

                # Create drag pixmap
                tab_rect = self.tabRect(self._from_index)
                pixmap = QPixmap(tab_rect.size())
                pixmap.fill(self.palette().window().color())
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setBrush(QColor("#45475a"))
                painter.setPen(QPen(QColor("#89b4fa"), 2))
                painter.drawRoundedRect(pixmap.rect().adjusted(2, 2, -2, -2), 4, 4)
                tab_text = self.tabText(self._from_index)
                painter.setPen(QColor("#cdd6f4"))
                font = self.font()
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(pixmap.rect(), Qt.AlignCenter, tab_text)
                painter.end()
                drag.setPixmap(pixmap)
                drag.setHotSpot(event.pos() - tab_rect.topLeft())

                # Reset drop index before drag
                self._drop_index = self._from_index
                self._log(f"Starting drag: from_index={self._from_index}")
                # Execute drag - blocks until drop
                result = drag.exec_(Qt.MoveAction)
                self._log(f"Drag completed: result={result}, drop_index={self._drop_index}")
                return

        # Update drop position during drag (for visual feedback)
        if self._is_dragging:
            new_drop = self.tabAt(event.pos())
            if new_drop != self._drop_index:
                self._drop_index = new_drop
                self._log(f"dragMove: drop_index={self._drop_index}")
            self.update()  # Trigger repaint

        super().mouseMoveEvent(event)

    def dragMoveEvent(self, event):
        drop_idx = self.tabAt(event.pos())
        if drop_idx >= 0 and drop_idx != self._drop_index:
            self._drop_index = drop_idx
            self._log(f"dragMoveEvent: drop_index={self._drop_index}")
        self.update()  # Trigger repaint for visual feedback
        event.acceptProposedAction()

    def dropEvent(self, event):
        self._log(f"dropEvent called, mimeData valid={event.mimeData().hasFormat('application/x-tabindex')}")
        if event.mimeData().hasFormat("application/x-tabindex"):
            data = event.mimeData().data("application/x-tabindex")
            from_index = int(data.data().decode())
            to_index = self._drop_index
            self._log(f"dropEvent: from={from_index}, to={to_index}")
            if from_index >= 0 and to_index >= 0 and from_index != to_index:
                self._log(f"emitting tabDropped({from_index}, {to_index})")
                self.tabDropped.emit(from_index, to_index)
            else:
                self._log(f"skipping emit: from_index={from_index}, to_index={to_index}")
        event.acceptProposedAction()

    def mouseReleaseEvent(self, event):
        # Emit tabDropped signal if we were dragging and have valid indices
        if self._is_dragging and self._from_index >= 0 and self._drop_index >= 0 and self._from_index != self._drop_index:
            self._log(f"mouseReleaseEvent: emitting tabDropped({self._from_index}, {self._drop_index})")
            self.tabDropped.emit(self._from_index, self._drop_index)
        self._from_index = -1
        self._drop_index = -1
        self._drag_start_pos = None
        self._is_dragging = False
        self.update()
        super().mouseReleaseEvent(event)


def _load_config_value(name, fallback):
    """Load a value from common_config.py at runtime (works even when frozen as exe)."""
    try:
        if getattr(sys, 'frozen', False):
            config_path = os.path.join(os.path.dirname(sys.executable), 'common_config.py')
        else:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'common_config.py')
        if os.path.exists(config_path):
            spec = importlib.util.spec_from_file_location("common_config_runtime", config_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, name):
                return getattr(mod, name)
    except Exception:
        pass
    return fallback  # fallback to imported value


def _save_config_value(name, value):
    """Persist a single `name = value` line into common_config.py so it survives
    restarts. Silently no-ops if the file is missing or not writable (e.g. frozen
    exe with no companion config)."""
    try:
        if getattr(sys, 'frozen', False):
            config_path = os.path.join(os.path.dirname(sys.executable), 'common_config.py')
        else:
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'common_config.py')
        if not os.path.exists(config_path):
            return False
        with open(config_path, 'r', encoding='utf-8') as f:
            src = f.read()
        pattern = re.compile(rf'^({re.escape(name)}\s*=\s*).*$',
                             re.MULTILINE)
        new_line = f'{name} = {repr(value)}'
        if pattern.search(src):
            new_src = pattern.sub(lambda m: m.group(1) + repr(value), src, count=1)
        else:
            new_src = src.rstrip() + '\n' + new_line + '\n'
        if new_src == src:
            return True
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(new_src)
        return True
    except Exception:
        return False


SYSTEM_LANGUAGE = _load_config_value('SYSTEM_LANGUAGE', SYSTEM_LANGUAGE)
use_local_corr = _load_config_value('use_local_corr', use_local_corr)
# When True (and use_local_corr), the local self/ppc pool excludes peers
# submitted within the last _LC_PARITY_FRESH_DAYS, to mirror the platform's
# self-corr snapshot (newly-submitted alphas aren't in the platform pool until
# its batch refresh catches up).
strict_platform_parity = _load_config_value('strict_platform_parity', strict_platform_parity)


def T(text):
    """Translate text based on the global `SYSTEM_LANGUAGE` variable.
    If SYSTEM_LANGUAGE == 'Chinese', return Chinese translation.
    Otherwise, return the original English text.
    """
    if SYSTEM_LANGUAGE == 'Chinese':
        return TRANSLATIONS.get(text, text)
        
    return text


def get_tune_score(alpha_data):
    """Score function for tune ranking. Can be overridden via Edit Score button."""
    if _custom_tune_score_fn is not None:
        try:
            return _custom_tune_score_fn(alpha_data)
        except Exception:
            pass  # fallback to default on error

    fn = _load_config_value('default_get_tune_score', None)
    if fn is not None:
        try:
            return fn(alpha_data)
        except Exception:
            pass

    # ultimate fallback (hardcoded)
    IS = alpha_data.get("is", {})
    fitness = abs(IS.get("fitness", 0))
    margin = abs(IS.get("margin", 0))
    return fitness * 10 + margin
    

_custom_tune_score_fn = None  # set by _edit_tune_score dialog


# ──────────────────────────────────────────────
#  Generic Numeric Value Parser
# ──────────────────────────────────────────────
def parse_numeric_values(text):
    """Parse numeric input text into a list of float values.

    Supports:
      - Single number: "10" → [10.0]
      - Comma-separated: "1,2,3" → [1.0, 2.0, 3.0]
      - Range: "0:100:10" → [0, 10, 20, ..., 100]
      - Range with exclusions: "0:100:10:[20,50]" → exclude 20 and 50
    Returns list of float, or None on parse error.
    """
    if not text:
        return None
    # Try range format first: start:end:step[:[excludes]]
    range_m = re.match(r'^(-?\d+\.?\d*):(-?\d+\.?\d*):(-?\d+\.?\d*)(?::\[([^\]]+)\])?$', text)
    if range_m:
        start = float(range_m.group(1))
        end = float(range_m.group(2))
        step = float(range_m.group(3))
        excludes = None
        if range_m.group(4):
            excludes = set()
            for x in range_m.group(4).split(','):
                try:
                    excludes.add(int(x.strip()) if '.' not in x.strip() else float(x.strip()))
                except ValueError:
                    pass
        if step == 0:
            return [start]
        vals = []
        v = start
        while (step > 0 and v <= end + step * 1e-9) or (step < 0 and v >= end + step * 1e-9):
            iv = int(v) if v == int(v) else v
            if excludes is None or iv not in excludes:
                vals.append(float(iv))
            v += step
        return vals if vals else None
    # Split by comma
    parts = [p.strip() for p in text.split(',') if p.strip()]
    if not parts:
        return None
    try:
        return [float(p) for p in parts]
    except ValueError:
        return None


# ──────────────────────────────────────────────
#  Alpha Expression Syntax Highlighter
# ──────────────────────────────────────────────
DEFAULT_LOOKBACK = 0


def _load_operators_from_json():
    """从 operators.json 加载 REGULAR scope 的 operator 名称集合。文件不存在则返回 None。"""
    ops_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'operators.json')
    if not os.path.exists(ops_path):
        return None
    try:
        with open(ops_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {op['name'] for op in data
                    if isinstance(op, dict) and 'name' in op and 'REGULAR' in op.get('scope', [])}
    except Exception:
        pass
    return None


def _get_regular_scope_operators():
    """从 operators.json 获取 scope 包含 REGULAR 的 operator 名称集合。"""
    ops_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'operators.json')
    if not os.path.exists(ops_path):
        return None
    try:
        with open(ops_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return {op['name'] for op in data
                    if isinstance(op, dict) and 'name' in op and 'REGULAR' in op.get('scope', [])}
    except Exception:
        pass
    return None


# 先尝试从 operators.json 加载，否则用硬编码 fallback
_LOADED_OPS = _load_operators_from_json()

_BRAIN_OPERATORS = _LOADED_OPS if _LOADED_OPS is not None else {
    # Arithmetic
    "abs", "add", "arc_tan", "densify", "divide", "floor", "inverse", "log",
    "max", "min", "multiply", "nan_out", "pasteurize", "power", "purify",
    "reverse", "round", "s_log_1p", "sigmoid", "sign", "signed_power", "sqrt",
    "subtract", "tanh", "to_nan",
    # Cross Sectional
    "multi_regression", "normalize", "quantile", "rank",
    "rank_gmean_amean_diff", "regression_neut", "regression_proj", "scale",
    "scale_down", "truncate", "vector_neut", "vector_proj", "winsorize",
    "zscore",
    # Group
    "group_backfill", "group_cartesian_product", "group_count", "group_extra",
    "group_max", "group_mean", "group_median", "group_min",
    "group_multi_regression", "group_neutralize", "group_normalize",
    "group_rank", "group_scale", "group_std_dev", "group_sum",
    "group_vector_proj", "group_zscore",
    # Logical
    "and", "equal", "greater", "greater_equal", "if_else", "is_finite",
    "is_nan", "is_not_nan", "less", "less_equal", "not", "not_equal", "or",
    # Special
    "inst_pnl",
    # Time Series
    "days_from_last_change", "hump", "hump_decay", "inst_tvr", "jump_decay",
    "kth_element", "last_diff_value", "ts_arg_max", "ts_arg_min", "ts_av_diff",
    "ts_backfill", "ts_co_kurtosis", "ts_co_skewness", "ts_corr",
    "ts_count_nans", "ts_covariance", "ts_decay_exp_window", "ts_decay_linear",
    "ts_delay", "ts_delta", "ts_delta_limit", "ts_entropy", "ts_ir",
    "ts_kurtosis", "ts_max", "ts_max_diff", "ts_mean", "ts_median", "ts_min",
    "ts_min_diff", "ts_min_max_cps", "ts_min_max_diff", "ts_moment",
    "ts_poly_regression", "ts_product", "ts_quantile", "ts_rank",
    "ts_regression", "ts_returns", "ts_scale", "ts_skewness", "ts_std_dev",
    "ts_step", "ts_sum", "ts_target_tvr_decay", "ts_target_tvr_delta_limit",
    "ts_target_tvr_hump", "ts_theilsen", "ts_weighted_decay", "ts_zscore",
    # Transformational
    "bucket", "left_tail", "right_tail", "tail", "trade_when",
    # Vector
    "vec_avg", "vec_count", "vec_kurtosis", "vec_max", "vec_min", "vec_range",
    "vec_skewness", "vec_stddev", "vec_sum",
}

_BRAIN_CONSTANTS = {
    "true", "false", "nan", "inf",
}

_BRAIN_KEYWORDS = {
    "filter", "driver", "gaussian", "cauchy", "uniform", "sigma",
    "rate", "scale", "longscale", "shortscale", "useStd", "limit",
    "range", "buckets", "skipBoth", "NaNGroup",
    "lookback", "dense", "constant", "lag", "rettype",
    "lower", "upper", "newval", "std", "k", "ignore",
    "mode", "nlength", "p", "f",
    "hump", "lambda_min", "lambda_max", "target_tvr",
    "nth", "ignoreNan", "threshold", "precise", "percentage",
}


class AlphaExprHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._formats = {}

        # Operators — orange (#FFB757)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#FFB757"))
        fmt.setFontWeight(QFont.Bold)
        self._formats["operator"] = fmt

        # Datafields — light blue
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#91CBFF"))
        self._formats["datafield"] = fmt

        # Constants (true, false, nan, inf) — mauve
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#cba6f7"))
        self._formats["constant"] = fmt

        # Named parameters — peach
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#fab387"))
        self._formats["keyword"] = fmt

        # Numbers — green
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#a6e3a1"))
        self._formats["number"] = fmt

        # Strings (quoted) — yellow
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#f9e2af"))
        self._formats["string"] = fmt

        # Parentheses / brackets — overlay2 (dim)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#7f849c"))
        self._formats["bracket"] = fmt

    def highlightBlock(self, text: str):
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]

            # Skip whitespace
            if ch.isspace():
                i += 1
                continue

            # String literal
            if ch in ('"', "'"):
                quote = ch
                j = i + 1
                while j < n and text[j] != quote:
                    if text[j] == '\\':
                        j += 1
                    j += 1
                j = min(j + 1, n)
                self.setFormat(i, j - i, self._formats["string"])
                i = j
                continue

            # Number
            if ch.isdigit() or (ch == '.' and i + 1 < n and text[i + 1].isdigit()):
                j = i
                has_dot = False
                while j < n and (text[j].isdigit() or (text[j] == '.' and not has_dot)):
                    if text[j] == '.':
                        has_dot = True
                    j += 1
                # Handle negative exponent like 1e-5
                if j < n and text[j] in ('e', 'E'):
                    j += 1
                    if j < n and text[j] in ('+', '-'):
                        j += 1
                    while j < n and text[j].isdigit():
                        j += 1
                self.setFormat(i, j - i, self._formats["number"])
                i = j
                continue

            # Identifier (operator, constant, keyword, or datafield)
            if ch.isalpha() or ch == '_':
                j = i
                while j < n and (text[j].isalnum() or text[j] == '_'):
                    j += 1
                word = text[i:j]
                if word in _BRAIN_OPERATORS:
                    self.setFormat(i, j - i, self._formats["operator"])
                elif word in _BRAIN_CONSTANTS:
                    self.setFormat(i, j - i, self._formats["constant"])
                elif word in _BRAIN_KEYWORDS:
                    self.setFormat(i, j - i, self._formats["keyword"])
                else:
                    self.setFormat(i, j - i, self._formats["datafield"])
                i = j
                continue

            # Brackets / parentheses
            if ch in '()[]{}':
                self.setFormat(i, 1, self._formats["bracket"])
                i += 1
                continue

            # Operators: + - * / ^ = < > ! & |
            if ch in '+-*/^=<>!&|':
                i += 1
                continue

            # Comma, semicolon, colon
            if ch in ',;:':
                i += 1
                continue

            # Anything else
            i += 1


# ──────────────────────────────────────────────
#  Tab Completion for Alpha Expression
# ──────────────────────────────────────────────
_COMPLETION_WORDS = sorted(set(
    list(_BRAIN_OPERATORS) + list(_BRAIN_CONSTANTS) + list(_BRAIN_KEYWORDS)
))


def _refresh_operators():
    """重新从 operators.json 加载 operators 并刷新全局变量。"""
    global _BRAIN_OPERATORS, _COMPLETION_WORDS
    ops = _load_operators_from_json()
    if ops is not None:
        _BRAIN_OPERATORS = ops
        _COMPLETION_WORDS = sorted(set(
            list(_BRAIN_OPERATORS) + list(_BRAIN_CONSTANTS) + list(_BRAIN_KEYWORDS)
        ))
        print(f"Operators refreshed from JSON: {len(_BRAIN_OPERATORS)} operators", flush=True)


class _OperatorsDownloadWorker(QThread):
    """异步下载 operators.json。"""
    finished = pyqtSignal()

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        try:
            self.client.ensure_auth()
            resp = self.client.session.get("https://api.worldquantbrain.com/operators")
            resp.raise_for_status()
            operators = resp.json()
            out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'operators.json')
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(operators, f, indent=2, ensure_ascii=False)
            print(f"Auto-downloaded {len(operators)} operators to operators.json", flush=True)
        except Exception as e:
            print(f"Auto-download operators failed: {e}", flush=True)
        self.finished.emit()


class CompletionPopup(QListView):
    """Popup list for Tab-completion in the alpha expression editor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QListView {
                background: #313244; color: #cdd6f4; border: 1px solid #585b70;
                font-family: Consolas, monospace; font-size: 11pt;
                selection-background-color: #45475a; selection-color: #89b4fa;
            }
        """)
        self._model = QStringListModel(_COMPLETION_WORDS, self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setModel(self._proxy)
        self.clicked.connect(self._on_clicked)
        self._text_edit = None
        self._prefix_start = 0

    def show_completions(self, text_edit, prefix, prefix_start, rect):
        self._text_edit = text_edit
        self._prefix_start = prefix_start
        self._proxy.setFilterFixedString(prefix)
        if self._proxy.rowCount() == 0:
            self.hide()
            return
        self.setCurrentIndex(self._proxy.index(0, 0))
        self.setFixedWidth(280)
        self.setFixedHeight(min(self.sizeHintForRow(0) * min(self._proxy.rowCount(), 10) + 4, 300))
        pos = text_edit.viewport().mapToGlobal(rect.bottomLeft() + QPoint(0, 4))
        self.move(pos)
        self.show()
        self.raise_()

    def _on_clicked(self, index):
        self._insert_completion(index.data())
        self.hide()

    def _insert_completion(self, text):
        if self._text_edit is None:
            return
        cursor = self._text_edit.textCursor()
        cursor.setPosition(self._prefix_start, QTextCursor.KeepAnchor)
        cursor.insertText(text)
        self._text_edit.setTextCursor(cursor)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
            index = self.currentIndex()
            if index.isValid():
                self._insert_completion(index.data())
            self.hide()
            return
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown,
                           Qt.Key_Home, Qt.Key_End):
            return super().keyPressEvent(event)
        # Forward printable keys to the text edit
        if event.text() and not event.modifiers():
            self.hide()
            QApplication.sendEvent(self._text_edit, event)
            return
        super().keyPressEvent(event)


# ──────────────────────────────────────────────
#  Region → Universe / Neutralization mapping (from BRAIN platform)
# ──────────────────────────────────────────────
REGION_OPTIONS = dict()
for region in REGION_ARR:
    REGION_OPTIONS[region] = {
        "universes": UNIVERSE_DICT[region],
        "skip_universes": [],
        "neutralizations": NEUTRALIZATION_DICT[region],
    }

# REGION_OPTIONS['USA']['skip_universes'] = ["TOP1000", "TOP500", "TOP200"]
REGION_OPTIONS['EUR']['skip_universes'] = ["TOP800", "TOP400"]



# ──────────────────────────────────────────────
#  API Client (synchronous, for QThread usage)
# ──────────────────────────────────────────────
class BrainClient:
    BASE_URL = "https://api.worldquantbrain.com/"

    def __init__(self):
        self.session = requests.Session()
        self.session.timeout = 60
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.email = ""
        self.password = ""

    def authenticate(self, email: str, password: str) -> dict:
        self.email = email
        self.password = password
        self.session.cookies.clear()

        credentials = f"{email}:{password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        headers = {'Authorization': f'Basic {encoded}'}

        resp = self.session.post(f"{self.BASE_URL}authentication", headers=headers)

        if resp.status_code == 201:
            return {"status": "ok", "message": "Login successful"}
        elif resp.status_code == 401:
            www_auth = resp.headers.get("WWW-Authenticate", "")
            if www_auth == "persona":
                return {"status": "biometric", "message": "Biometric auth required", "url": resp.headers.get("Location", "")}
            return {"status": "error", "message": "Incorrect email or password"}
        else:
            return {"status": "error", "message": f"Auth failed (HTTP {resp.status_code})"}

    def is_authenticated(self) -> bool:
        try:
            resp = self.session.get(f"{self.BASE_URL}authentication")
            return resp.status_code == 200
        except Exception:
            return False

    def ensure_auth(self):
        if not self.is_authenticated():
            if self.email and self.password:
                result = self.authenticate(self.email, self.password)
                if result["status"] != "ok":
                    raise Exception("Re-authentication failed")
            else:
                raise Exception("Not authenticated")

    def get_platform_options(self) -> dict:
        self.ensure_auth()
        resp = self.session.options(f"{self.BASE_URL}simulations")
        resp.raise_for_status()
        return resp.json()

    def create_simulation(self, expression: str, settings: dict, progress_cb=None, cancel_event=None, location_cb=None) -> dict:
        self.ensure_auth()

        payload = {
            "type": "REGULAR",
            "settings": settings,
            "regular": expression,
        }

        resp = self.session.post(f"{self.BASE_URL}simulations", json=payload)
        if resp.status_code == 401:
            self.ensure_auth()
            resp = self.session.post(f"{self.BASE_URL}simulations", json=payload)
        if resp.status_code not in (200, 201):
            try:
                err = resp.json()
                raise Exception(f"Simulation creation failed: {err}")
            except json.JSONDecodeError:
                raise Exception(f"Simulation creation failed (HTTP {resp.status_code}): {resp.text[:500]}")

        location = resp.headers.get('Location', '')
        if not location:
            raise Exception("No Location header in simulation response")

        if location_cb:
            location_cb(location)

        start = time.time()
        poll_count = 0
        while True:
            if cancel_event and cancel_event.is_set():
                raise Exception("Simulation cancelled by user")

            poll_count += 1
            sim_resp = self.session.get(location)

            if sim_resp.status_code == 401:
                self.ensure_auth()
                continue

            if sim_resp.status_code >= 400:
                try:
                    err_data = sim_resp.json()
                    err_msg = err_data.get("error", err_data.get("message", str(err_data)))
                except Exception:
                    err_msg = sim_resp.text[:500]
                raise Exception(f"Simulation error: {err_msg}")

            retry_after = sim_resp.headers.get("Retry-After", "0")
            elapsed = time.time() - start

            # Parse progress from API response
            progress_pct = None
            try:
                sim_json = sim_resp.json()
                progress_val = sim_json.get("progress")
                if progress_val is not None:
                    # progress can be 0.0~1.0 or 0~100
                    if isinstance(progress_val, (int, float)):
                        progress_pct = progress_val if progress_val <= 1.0 else progress_val / 100.0
            except Exception:
                pass

            if progress_cb:
                status = f"Polling... (attempt {poll_count}, {elapsed:.0f}s elapsed)"
                progress_cb(elapsed, status, progress_pct)

            if retry_after == "0" or not retry_after:
                break

            sleep(float(retry_after))

        sim_data = sim_resp.json()
        alpha_id = sim_data.get("alpha")
        if not alpha_id:
            error = sim_data.get("error") or sim_data.get("message")
            if error:
                raise Exception(f"Simulation failed: {error}")
            raise Exception(f"No alpha ID in simulation result: {json.dumps(sim_data)[:500]}")

        if progress_cb:
            progress_cb(elapsed, "Fetching alpha details...", -1.0)

        self.ensure_auth()
        alpha_resp = self.session.get(f"{self.BASE_URL}alphas/{alpha_id}")
        if alpha_resp.status_code == 401:
            self.ensure_auth()
            alpha_resp = self.session.get(f"{self.BASE_URL}alphas/{alpha_id}")
        alpha_resp.raise_for_status()
        try:
            alpha_data = alpha_resp.json()
        except Exception:
            return {"error": {"error": "Failed to parse alpha details"}}

        if progress_cb:
            progress_cb(elapsed, "Fetching PnL data...", -1.0)

        pnl_data = self._get_pnl(alpha_id)
        yearly_data = self._get_yearly_stats(alpha_id)

        return {
            "alpha": alpha_data,
            "pnl": pnl_data,
            "yearly": yearly_data,
        }

    def _fetch_correlation(self, alpha_id: str, corr_type: str, max_retries=3, retry_delay=5):
        self.ensure_auth()
        # url = f"https://api.worldquantbrain.com/alphas/{alpha_id}/correlations/power-pool"
        url = f"{self.BASE_URL}alphas/{alpha_id}/correlations/{corr_type}"
        for attempt in range(max_retries):
            resp = self.session.get(url)
            if resp.status_code == 401:
                self.ensure_auth()
                resp = self.session.get(url)
            if resp.status_code == 404:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return {"max": None, "min": None}
            if resp.status_code >= 400:
                try:
                    err = resp.json()
                    raise Exception(f"Correlation error: {err}")
                except Exception:
                    raise Exception(f"Correlation error (HTTP {resp.status_code})")
            try:
                return resp.json()
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return {"max": None, "min": None}
        return {"max": None, "min": None}

    def get_self_correlation(self, alpha_id: str) -> dict:
        return self._fetch_correlation(alpha_id, "self")

    def get_ppc_correlation(self, alpha_id: str) -> dict:
        return self._fetch_correlation(alpha_id, "power-pool")

    def get_prod_correlation(self, alpha_id: str) -> dict:
        return self._fetch_correlation(alpha_id, "prod")

    def get_tags(self) -> list:
        self.ensure_auth()
        all_tags = []
        url = f"{self.BASE_URL}tags?limit=100"
        while url:
            resp = self.session.get(url)
            if resp.status_code == 401:
                self.ensure_auth()
                resp = self.session.get(url)
            if resp.status_code != 200:
                break
            try:
                data = resp.json()
            except Exception:
                break
            results = data.get("results", [])
            all_tags.extend(results)
            url = data.get("next")
            if url and url.startswith("http://"):
                url = url.replace("http://", "https://")
                
        return all_tags

    def add_alpha_to_tag(self, tag_id: str, tag_name: str, alpha_id: str) -> dict:
        """Add alpha to an existing list via PATCH /tags/{id}."""
        self.ensure_auth()
        data = {"op": "add", "name": tag_name, "alphas": [alpha_id]}
        resp = self.session.patch(f"{self.BASE_URL}tags/{tag_id}", json=data)
        if resp.status_code == 401:
            self.ensure_auth()
            resp = self.session.patch(f"{self.BASE_URL}tags/{tag_id}", json=data)
        if resp.status_code not in (200, 201):
            raise Exception(f"Add to list failed (HTTP {resp.status_code})")
        return {"status": "ok"}

    def create_tag(self, name: str, alpha_id: str = None) -> dict:
        self.ensure_auth()
        url = f"{self.BASE_URL}tags"
        payload = {"type": "LIST", "name": name}
        if alpha_id:
            payload["alphas"] = [alpha_id]
        resp = self.session.post(url, json=payload)
        if resp.status_code == 401:
            self.ensure_auth()
            resp = self.session.post(url, json=payload)
        if resp.status_code not in (200, 201):
            try:
                err = resp.json()
                raise Exception(f"Create list failed: {err}")
            except Exception:
                if resp.status_code >= 400:
                    raise Exception(f"Create list failed (HTTP {resp.status_code})")
        return resp.json()

    def get_today_simulated_count(self) -> int:
        self.ensure_auth()
        est = timezone(timedelta(hours=-4))
        today = dt_datetime.now(est).strftime("%Y-%m-%d")
        url = f"{self.BASE_URL}users/self/activities/simulations?date%3E={today}"
        try:
            resp = self.session.get(url)
            if resp.status_code == 401:
                self.ensure_auth()
                resp = self.session.get(url)
            resp.raise_for_status()
            data = resp.json()
            records = data.get("records", {})
            if isinstance(records, dict):
                inner = records.get("records", [])
                if inner and len(inner) > 0 and len(inner[-1]) > 1:
                    return int(inner[-1][1])
            return 0
        except Exception:
            return 0

    def get_user_id(self) -> str:
        self.ensure_auth()
        url = f"{self.BASE_URL}users/self"
        try:
            resp = self.session.get(url)
            if resp.status_code == 401:
                self.ensure_auth()
                resp = self.session.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data.get("id", "") or data.get("userId", "") or ""
        except Exception:
            return ""

    def _get_pnl(self, alpha_id: str) -> list:
        url = f"{self.BASE_URL}alphas/{alpha_id}/recordsets/pnl"
        for attempt in range(5):
            self.ensure_auth()
            resp = self.session.get(url)
            if resp.status_code == 401:
                self.ensure_auth()
                continue
            if resp.status_code == 404:
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code != 200:
                time.sleep(2)
                continue
            try:
                data = resp.json()
            except Exception:
                time.sleep(2)
                continue
            records = data.get('records', [])
            if not records:
                time.sleep(3 * (attempt + 1))
                continue
            def _norm_date(v):
                s = str(v)
                if len(s) == 8 and s.isdigit():
                    return f"{s[:4]}-{s[4:6]}-{s[6:]}"
                return s
            dates = [_norm_date(r[0]) for r in records]
            pnl = [float(r[1]) for r in records]
            if len(records[0]) >= 4:
                risk = [float(r[2]) for r in records]
                invest = [float(r[3]) for r in records]
            elif len(records[0]) == 3:
                risk = []
                invest = [float(r[2]) for r in records]
            else:
                risk = []
                invest = []
            # GLB sub-region PnL: columns 4,5,6 are AMER, APAC, EMEA
            sub_regions = {}
            if len(records[0]) >= 7:
                try:
                    sub_regions["AMER"] = [float(r[4]) for r in records]
                    sub_regions["APAC"] = [float(r[5]) for r in records]
                    sub_regions["EMEA"] = [float(r[6]) for r in records]
                except (ValueError, IndexError):
                    pass
            return dates, pnl, risk, invest, sub_regions
        return []

    def _get_yearly_stats(self, alpha_id: str) -> list:
        self.ensure_auth()
        for attempt in range(5):
            resp = self.session.get(f"{self.BASE_URL}alphas/{alpha_id}/recordsets/yearly-stats")
            if resp.status_code == 401:
                self.ensure_auth()
                continue
            if resp.status_code != 200:
                time.sleep(2)
                continue
            try:
                return resp.json()
            except Exception:
                time.sleep(2)
                continue
        return []


# ──────────────────────────────────────────────
#  Simulation Worker Thread
# ──────────────────────────────────────────────
class SimulationWorker(QThread):
    progress = pyqtSignal(float, str, float)  # elapsed, status, progress_pct (0.0~1.0 or -1 if unknown)
    sim_id_ready = pyqtSignal(str)  # simulation ID extracted from Location header
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, client: BrainClient, expression: str, settings: dict):
        super().__init__()
        self.client = client
        self.expression = expression
        self.settings = settings
        self._cancel_event = threading.Event()
        self._sim_location = None

    def cancel(self):
        self._cancel_event.set()
        if self._sim_location:
            try:
                self.client.session.delete(self._sim_location)
            except Exception:
                pass

    def run(self):
        try:
            result = self.client.create_simulation(
                self.expression,
                self.settings,
                progress_cb=lambda elapsed, status, pct: self.progress.emit(elapsed, status, pct if pct is not None else -1.0),
                cancel_event=self._cancel_event,
                location_cb=self._on_location,
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _on_location(self, loc):
        self._sim_location = loc
        # Extract sim ID from location like "/simulations/XXXX"
        sim_id = loc.rsplit('/', 1)[-1] if '/' in loc else loc
        self.sim_id_ready.emit(sim_id)


# ──────────────────────────────────────────────
#  Base Auto-Tune Worker
# ──────────────────────────────────────────────
class BaseTuneWorker(QThread):
    """Base class for auto-tune workers with shared batch execution logic."""
    progress = _sig(int, int, object, str)   # current_idx, total, param_val, status
    sim_id_ready = _sig(str)
    finished = _sig(dict, object)
    error = _sig(str)
    active_count = _sig(int)

    def __init__(self, client: BrainClient, expression: str, settings: dict,
                 max_concurrent=8, is_glb=False):
        super().__init__()
        self.client = client
        self.expression = expression
        self.settings = dict(settings)
        self._cancel_event = threading.Event()
        self._slot_cost = 2 if is_glb else 1
        self._max_concurrent = max(1, max_concurrent // self._slot_cost)

    def cancel(self):
        self._cancel_event.set()

    def _run_one(self, param_val):
        """Run a single simulation. Subclasses must override."""
        raise NotImplementedError

    def _format_progress(self, phase_label, param_val, completed, total, score):
        """Format the progress status string. Subclasses may override."""
        return f"{phase_label} {param_val} ({completed}/{total}) score={score:.2f}"

    def _run_batch(self, param_list, phase_label):
        """Run a batch of param values with bounded concurrency.
        Returns list of (param, result, score) for completed sims."""
        results = []
        completed = 0
        active = 0
        total = len(param_list)
        lock = threading.Lock()

        def worker(p):
            nonlocal completed, active
            if self._cancel_event.is_set():
                with lock:
                    active -= 1
                    self.active_count.emit(max(0, active))
                return
            try:
                result, score = self._run_one(p)
                with lock:
                    completed += 1
                    active -= 1
                    results.append((p, result, score))
                    self.progress.emit(completed, total, p,
                        self._format_progress(phase_label, p, completed, total, score))
                    self.active_count.emit(max(0, active))
            except Exception:
                with lock:
                    completed += 1
                    active -= 1
                    self.active_count.emit(max(0, active))

        sem = threading.Semaphore(self._max_concurrent)

        def limited_worker(p):
            sem.acquire()
            try:
                worker(p)
            finally:
                sem.release()

        threads = []
        for p in param_list:
            if self._cancel_event.is_set():
                break
            with lock:
                active += 1
                self.active_count.emit(active)
            t = threading.Thread(target=limited_worker, args=(p,))
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        return results


# ──────────────────────────────────────────────
#  Auto-Tune Worker for Universe / Neutral
# ──────────────────────────────────────────────
class AutoTuneWorker(BaseTuneWorker):
    """Try multiple parameter values in parallel, return the result with highest get_tune_score()."""

    def __init__(self, client: BrainClient, expression: str, settings: dict,
                 param_name: str, param_values: list, max_concurrent=8, is_glb=False):
        super().__init__(client, expression, settings, max_concurrent, is_glb)
        self._param_name = param_name
        self._param_values = param_values

    def _run_one(self, param_val):
        """Run a single simulation with the given param value, return (result_dict, score)."""
        s = dict(self.settings)
        s[self._param_name] = param_val
        result = self.client.create_simulation(
            self.expression, s,
            cancel_event=self._cancel_event,
        )
        alpha = result.get("alpha", {})
        score = get_tune_score(alpha)
        return result, score

    def _format_progress(self, phase_label, param_val, completed, total, score):
        return f"{phase_label} {self._param_name}={param_val} ({completed}/{total}) score={score:.2f}"

    def run(self):
        try:
            results = self._run_batch(self._param_values, "Auto-tune")

            if not results:
                raise Exception("All auto-tune simulations failed")

            best_param = max(results, key=lambda x: x[2])[0]
            best_result = max(results, key=lambda x: x[2])[1]

            self.finished.emit(best_result, best_param)

        except Exception as e:
            if self._cancel_event.is_set():
                return
            self.error.emit(str(e))


# ──────────────────────────────────────────────
#  Decay Auto-Tune Worker Thread
# ──────────────────────────────────────────────
class DecayAutoTuneWorker(BaseTuneWorker):
    """Try multiple decay values in parallel, return the result with highest get_tune_score()."""

    def __init__(self, client: BrainClient, expression: str, settings: dict,
                 decay_values=None, max_concurrent=8, is_glb=False):
        super().__init__(client, expression, settings, max_concurrent, is_glb)
        self._decay_values = decay_values or _load_config_value('COARSE_DECAYS', COARSE_DECAYS)

    def _run_one(self, decay_val):
        """Run a single simulation with the given decay, return (result_dict, score)."""
        s = dict(self.settings)
        s["decay"] = int(decay_val)
        result = self.client.create_simulation(
            self.expression, s,
            cancel_event=self._cancel_event,
        )
        alpha = result.get("alpha", {})
        score = get_tune_score(alpha)
        return result, score

    def _format_progress(self, phase_label, param_val, completed, total, score):
        return f"{phase_label} decay={int(param_val)} ({completed}/{total}) score={score:.2f}"

    def run(self):
        try:
            # Phase 1: coarse search
            coarse_results = self._run_batch(self._decay_values, "Coarse")

            if not coarse_results:
                raise Exception("All coarse decay simulations failed")

            best_decay = max(coarse_results, key=lambda x: x[2])[0]
            best_score = max(coarse_results, key=lambda x: x[2])[2]
            best_result = max(coarse_results, key=lambda x: x[2])[1]

            # Phase 2: fine-tune around best coarse decay (integers only)
            fine_decays = []
            center = int(best_decay)
            for offset in range(-2, 3):
                v = center + offset
                if v < 0 or v == int(best_decay):
                    continue
                if v not in [int(x) for x in self._decay_values]:
                    fine_decays.append(v)

            if fine_decays and not self._cancel_event.is_set():
                fine_results = self._run_batch(fine_decays, "Fine")
                for d, result, score in fine_results:
                    if score > best_score:
                        best_score = score
                        best_result = result
                        best_decay = d

            self.finished.emit(best_result, best_decay)

        except Exception as e:
            if self._cancel_event.is_set():
                return
            self.error.emit(str(e))


# ──────────────────────────────────────────────
#  Expression Auto-Tune Worker Thread
# ──────────────────────────────────────────────
class ExpressionAutoTuneWorker(BaseTuneWorker):
    """Try multiple expression variants by replacing <?> or <?g>, return the result with highest get_tune_score().
    For prefix patterns like group_value<?g>, replaces group_value<?g> → value.
    """

    def __init__(self, client: BrainClient, expression: str, settings: dict,
                 expr_values=None, max_concurrent=8, is_glb=False, region="USA"):
        super().__init__(client, expression, settings, max_concurrent, is_glb)
        self._expr_values = expr_values or _load_config_value('DEFAULT_VALUES', DEFAULT_VALUES)
        self._region = region
        self._placeholder = "<?>"

    def _get_glossary_values(self):
        """Get WHITE_LIST values, skipping 'country' if region doesn't support it."""
        values = list(WHITE_LIST)
        region_neutrals = NEUTRALIZATION_DICT.get(self._region, [])
        if "COUNTRY" not in region_neutrals:
            values = [v for v in values if v != "country"]
        return values

    def _build_expression(self, val, placeholder="<?>"):
        """Replace placeholder with val in expression."""
        return self.expression.replace(placeholder, str(val), 1)

    def _run_one(self, val):
        """Run a single simulation with the given expr value, return (result_dict, score)."""
        expr = self._build_expression(val, self._placeholder)
        result = self.client.create_simulation(
            expr, self.settings,
            cancel_event=self._cancel_event,
        )
        alpha = result.get("alpha", {})
        score = get_tune_score(alpha)
        return result, score

    def _format_progress(self, phase_label, param_val, completed, total, score):
        return f"{phase_label} expr={self._placeholder}→{param_val} ({completed}/{total}) score={score:.2f}"

    def run(self):
        try:
            # Check which placeholder is used: <prefix?g> or <?g> or <?>
            m_glossary = re.search(r'<(?:(\w+)\s*)?\?g(?:=[^>]*)?>', self.expression)
            m_plain = re.search(r'<\?(?:=[^>]*)?>', self.expression) if not m_glossary else None
            if m_glossary:
                val_list = self._get_glossary_values()
                self._placeholder = m_glossary.group(0)
                prefill = m_glossary.group(1)
                if prefill:
                    val_list = [v for v in val_list if v != prefill]
            elif m_plain:
                val_list = self._expr_values
                self._placeholder = m_plain.group(0)
            else:
                raise Exception("No <?>, <?g>, or <prefix?g> placeholder found in expression")

            results = self._run_batch(val_list, "Auto-tune")

            if not results:
                raise Exception("All expression auto-tune simulations failed")

            best_val = max(results, key=lambda x: x[2])[0]
            best_result = max(results, key=lambda x: x[2])[1]

            best_expr = self._build_expression(best_val, self._placeholder)
            self.finished.emit(best_result, best_expr)

        except Exception as e:
            if self._cancel_event.is_set():
                return
            self.error.emit(str(e))


# ──────────────────────────────────────────────
#  Correlation Worker Thread
# ──────────────────────────────────────────────
class CorrelationWorker(QThread):
    finished = pyqtSignal(str, dict)  # corr_type, data
    error = pyqtSignal(str, str)  # corr_type, error_msg

    def __init__(self, client: BrainClient, alpha_id: str, corr_type: str):
        super().__init__()
        self.client = client
        self.alpha_id = alpha_id
        self.corr_type = corr_type

    def run(self):
        try:
            if self.corr_type == "self":
                data = self.client.get_self_correlation(self.alpha_id)
            elif self.corr_type == "ppc":
                data = self.client.get_ppc_correlation(self.alpha_id)
            elif self.corr_type == "prod":
                data = self.client.get_prod_correlation(self.alpha_id)
            elif self.corr_type == "checks":
                self.client.ensure_auth()
                resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{self.alpha_id}")
                if resp.status_code == 401:
                    self.client.ensure_auth()
                    resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{self.alpha_id}")
                resp.raise_for_status()
                data = resp.json()
            else:
                data = {}
            self.finished.emit(self.corr_type, data)
        except Exception as e:
            self.error.emit(self.corr_type, str(e))


# ──────────────────────────────────────────────
#  Download Submitted Alphas Worker
#  (内嵌 local_corr.py 逻辑，使用 BrainClient.session)
# ──────────────────────────────────────────────

# ── 常量（与 local_corr.py / ProdMemo corrWorker.js 一致）──
_LC_YEARS = 4
_LC_RA = 'REGULAR:REGULAR'
_LC_PPA = 'POWER_POOL:POWER_POOL_ELIGIBLE'
_LC_ALPHA_PAGE_LIMIT = 100
_LC_PNL_BATCH_SIZE = 100
_LC_PNL_CONCURRENCY = 3
# When strict_platform_parity is True, exclude peers submitted within this
# many days from the self/ppc pool — mirrors the platform's batch-refresh lag
# (newly-submitted alphas don't appear in other alphas' self-corr snapshots
# until the platform recomputes them).
_LC_PARITY_FRESH_DAYS = 30
_LC_PNL_RETRY_DELAYS = [1, 2, 4]

# 路径
_LC_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LC_OLD_PNL_DIR = os.path.join(_LC_SCRIPT_DIR, 'pnl_csv')           # 旧目录，启动时自动迁移
_LC_OUTPUT_DIR = os.path.join(_LC_SCRIPT_DIR, 'pnl_csv_submitted')  # 已提交 alpha 的 PnL CSV
_LC_UNSUBMITTED_DIR = os.path.join(_LC_SCRIPT_DIR, 'pnl_csv_unsubmitted')  # 未提交 alpha 的 PnL CSV
_LC_ALPHAS_DB_PATH = os.path.join(_LC_SCRIPT_DIR, 'alphas_db.json')
_LC_PC_CACHE_PATH = os.path.join(_LC_SCRIPT_DIR, 'pc_cache.json')

# 符号运算符 → operator name 映射
_SYM_TO_OP = {
    '+': 'add', '-': 'subtract', '*': 'multiply', '/': 'divide',
    '**': 'power', '^': 'power', '1/': 'inverse',
    '&&': 'and', '||': 'or', '!': 'not',
    '>': 'greater', '>=': 'greater_equal',
    '<': 'less', '<=': 'less_equal',
    '==': 'equal', '!=': 'not_equal',
    '?:': 'if_else',
}
_SYM_PATTERNS = sorted(_SYM_TO_OP.keys(), key=len, reverse=True)

# 一元 - 前面的字符：运算符、左括号、逗号、分号、等号、行首等
_UNARY_MINUS_PREV = set('+-*/(^<>=!&|,;=([{}?:~%')


def _extract_fields_from_python(code):
    """从 PYTHON alpha 代码中提取 datafield 名称。
    Python alpha 中 datafield 通过 @alpha(data=[...]) 装饰器声明，
    例如 @alpha(data=["returns", "close", "est_12m_eps_raisednum_4wks"], store=[])
    也可能通过 brain.get_data_frame('field') 访问。
    返回 dict: {field_name: count}
    """
    wl = set(WHITE_LIST)
    fields = {}
    # Match @alpha(data=[...]) or @alpha( data=[...] ) — extract the list content
    # The data list can span multiple lines
    # Strategy: find @alpha( then find data=[ then collect strings until ]
    for m in re.finditer(r'@alpha\s*\(', code):
        start = m.end()
        # Find 'data=' after @alpha(
        rest = code[start:]
        dm = re.search(r'data\s*=\s*\[', rest)
        if not dm:
            continue
        list_start = start + dm.end()
        # Find the matching ]
        depth = 1
        pos = list_start
        while pos < len(code) and depth > 0:
            if code[pos] == '[':
                depth += 1
            elif code[pos] == ']':
                depth -= 1
            pos += 1
        list_content = code[list_start:pos - 1]
        # Extract all string literals from the list
        for s in re.findall(r'["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']', list_content):
            if s not in wl:
                fields[s] = fields.get(s, 0) + 1
    # Also match brain.get_data_frame('field') or brain.get_data_frame("field")
    for m in re.finditer(r'brain\.get_data_frame\s*\(\s*["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']\s*\)', code):
        name = m.group(1)
        if name not in wl:
            fields[name] = fields.get(name, 0) + 1
    return fields


def _extract_ops_from_code(code):
    """从 FASTEXPR 代码中提取所有 operator 及其使用次数（含符号运算符）。
    - 一元 - (如 -a, rank(x)-a, -ts_mean(...)) 计为 reverse
    - 二元 - (如 a - b, close - open) 计为 subtract
    判断逻辑：
      1. 前一个非空白字符属于运算符/括号/逗号等 → 一元
      2. 行首 → 一元
      3. - 后面紧跟字母（函数名/变量名）→ 一元（FASTEXPR 中 a -func 不可能是减法）
      4. 其余 → 二元 subtract
    返回 dict: {op_name: count}
    """
    ops_count = {}

    # 提取函数调用: word(
    found = re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', code)
    for op in found:
        ops_count[op] = ops_count.get(op, 0) + 1

    # 提取符号运算符（逐字符扫描，长符号优先匹配）
    i = 0
    n = len(code)
    while i < n:
        matched = False
        for sym in _SYM_PATTERNS:
            slen = len(sym)
            if i + slen <= n and code[i:i+slen] == sym:
                if sym == '-':
                    # 判断一元还是二元
                    # 1) 前一个非空白字符
                    j = i - 1
                    while j >= 0 and code[j] in ' \t':
                        j -= 1
                    prev_is_unary = (j < 0 or code[j] in _UNARY_MINUS_PREV)

                    # 2) - 后面紧跟字母 → 一元 (如 -ts_co_kurtosis, -close)
                    #    FASTEXPR 中 a -func(...) 语法上不可能是减法
                    k = i + 1
                    next_is_alpha = (k < n and code[k].isalpha())

                    # 3) 算术运算符后跟 -数字 → 负数字面量，不算 reverse
                    #    如 / -1, * -2, + -3 等
                    next_is_digit = (k < n and code[k].isdigit())
                    prev_is_arith = (j >= 0 and code[j] in '+-*/')
                    if prev_is_arith and next_is_digit:
                        i += slen
                        matched = True
                        break

                    if prev_is_unary or next_is_alpha:
                        op_name = 'reverse'
                    else:
                        op_name = 'subtract'
                else:
                    op_name = _SYM_TO_OP[sym]
                ops_count[op_name] = ops_count.get(op_name, 0) + 1
                i += slen
                matched = True
                break
        if not matched:
            i += 1

    return ops_count


# ─── ProdMemo corrWorker.js 算法（inter corr / PC Range 共用）───

def _ic_normalize_pnl(dates, cum_pnl):
    """Filter invalid records, normalize dates to YYYY-MM-DD, sort."""
    result = []
    for d, v in zip(dates, cum_pnl):
        if not d:
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue
        result.append((str(d)[:10], val))
    result.sort(key=lambda x: x[0])
    return result


def _ic_calendar_window_start(records):
    """Last record year minus YEARS + 1, January 1."""
    if not records:
        return None
    last_year = int(records[-1][0][:4])
    return f'{last_year - 4 + 1}-01-01'


def _ic_calculate_returns(records, start_date):
    """Cumulative PnL → daily returns (direct diff, no fill)."""
    returns = {}
    previous = None
    for date_str, value in records:
        if previous is not None and date_str >= start_date:
            returns[date_str] = value - previous
        previous = value
    return returns


def _ic_calculate_forward_filled_returns(records, dates, start_date):
    """Cumulative PnL forward-filled on global calendar → daily returns."""
    returns = {}
    record_index = 0
    current_value = None
    previous_value = None
    for date_str in dates:
        while record_index < len(records) and records[record_index][0] <= date_str:
            current_value = records[record_index][1]
            record_index += 1
        if current_value is None:
            continue
        if previous_value is not None and date_str >= start_date:
            returns[date_str] = current_value - previous_value
        previous_value = current_value
    return returns


def _ic_pearson(target_returns, peer_returns):
    """Pearson correlation with sequential accumulation (matches JS pearson()).
    Returns (value, count) or None if insufficient data."""
    count = 0
    sum_x = sum_y = sum_xx = sum_yy = sum_xy = 0.0
    for date_str, x in target_returns.items():
        if date_str not in peer_returns:
            continue
        y = peer_returns[date_str]
        count += 1
        sum_x += x
        sum_y += y
        sum_xx += x * x
        sum_yy += y * y
        sum_xy += x * y
    if count < 2:
        return None
    covariance = count * sum_xy - sum_x * sum_y
    variance_x = count * sum_xx - sum_x * sum_x
    variance_y = count * sum_yy - sum_y * sum_y
    denominator = math.sqrt(variance_x * variance_y)
    if not math.isfinite(denominator) or denominator == 0:
        return None
    value = covariance / denominator
    if not math.isfinite(value):
        return None
    return value, count


def _ic_calc_inter_corr(my_dates, my_pnl, peer_dates, peer_pnl):
    """计算两个 alpha 之间的 inter correlation（ProdMemo corrWorker.js 算法）。

    Args:
        my_dates: target alpha 的日期列表
        my_pnl: target alpha 的累积 PnL 列表
        peer_dates: peer alpha 的日期列表
        peer_pnl: peer alpha 的累积 PnL 列表

    Returns:
        float | None: inter correlation 值
    """
    my_records = _ic_normalize_pnl(my_dates, my_pnl)
    peer_records = _ic_normalize_pnl(peer_dates, peer_pnl)
    if len(my_records) < 2 or len(peer_records) < 2:
        return None

    start1 = _ic_calendar_window_start(my_records)
    start2 = _ic_calendar_window_start(peer_records)
    start_date = max(start1, start2)

    date_set = set()
    for r in my_records:
        date_set.add(r[0])
    for r in peer_records:
        date_set.add(r[0])
    global_dates = sorted(date_set)

    target_returns = _ic_calculate_returns(my_records, start_date)
    peer_returns = _ic_calculate_forward_filled_returns(peer_records, global_dates, start_date)

    result = _ic_pearson(target_returns, peer_returns)
    return result[0] if result is not None else None


def _lc_load_pc_cache():
    """加载 prod corr 缓存。返回 dict: {alpha_id: {"max": float, "min": float}}"""
    if not os.path.exists(_LC_PC_CACHE_PATH):
        return {}
    try:
        with open(_LC_PC_CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _lc_save_pc_cache(cache):
    """保存 prod corr 缓存。"""
    with open(_LC_PC_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)


def _lc_get_cached_pc(alpha_id):
    """获取单个 alpha 的 cached prod corr。返回 {"max": float, "min": float} 或 None。"""
    cache = _lc_load_pc_cache()
    return cache.get(alpha_id)


def _lc_set_cached_pc(alpha_id, max_c, min_c):
    """缓存单个 alpha 的 prod corr。"""
    cache = _lc_load_pc_cache()
    cache[alpha_id] = {"max": max_c, "min": min_c}
    _lc_save_pc_cache(cache)


def _calc_drawdown(cum_pnl):
    """从累计 PnL 序列计算最大回撤（BRAIN 格式：非负比例值）。
    BRAIN 平台 drawdown = max(peak - trough) / 10_000_000，
    其中 10M 是模拟的初始资金基数。
    """
    if not cum_pnl or len(cum_pnl) < 2:
        return 0.0
    peak = cum_pnl[0]
    max_dd = 0.0
    for v in cum_pnl:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    return max_dd / 10_000_000


# ── 已提交 Alpha 元数据本地缓存 ──

def _lc_load_local_alphas_db():
    if not os.path.exists(_LC_ALPHAS_DB_PATH):
        return {}
    try:
        with open(_LC_ALPHAS_DB_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {a['id']: a for a in data if a.get('id')}
    except Exception as e:
        print(f"Warning: failed to read {_LC_ALPHAS_DB_PATH}: {e}")
    return {}


def _lc_save_local_alphas_db(alphas_dict):
    with open(_LC_ALPHAS_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(alphas_dict, f, ensure_ascii=False)


def _lc_fetch_alpha_page(sess, offset, limit=_LC_ALPHA_PAGE_LIMIT):
    url = (
        f"https://api.worldquantbrain.com/users/self/alphas"
        f"?limit={limit}&offset={offset}&order=-dateSubmitted&hidden=false"
        f"&status!=UNSUBMITTED%1FIS-FAIL"
    )
    resp = sess.get(url, headers={'accept': 'application/json;version=4.0'})
    if resp.status_code != 200:
        raise Exception(f"Alpha list HTTP {resp.status_code}")
    data = resp.json()
    if not isinstance(data.get('results'), list) or 'count' not in data:
        raise Exception("Invalid Alpha list response")
    return data


def _lc_fetch_all_submitted_alphas(sess, need_download=False, log_cb=None):
    """获取所有已提交 Alpha 元数据。need_download=True 时增量同步。log_cb 用于进度回调。"""
    local_db = _lc_load_local_alphas_db()

    if not need_download:
        return list(local_db.values())

    local_count = len(local_db)

    try:
        first_page = _lc_fetch_alpha_page(sess, 0, limit=1)
    except Exception:
        first_page = _lc_fetch_alpha_page(sess, 0, limit=1)

    remote_count = first_page['count']
    remote_results = first_page.get('results', [])

    if local_count == remote_count and remote_results:
        newest_id = remote_results[0].get('id')
        if newest_id and newest_id in local_db:
            msg = f"Alpha metadata up to date: {local_count} alphas"
            print(msg); log_cb and log_cb(msg)
            return list(local_db.values())

    # If local count < remote count (e.g. user deleted an alpha from alphas_db.json),
    # we must scan ALL pages — early stop would miss the deleted alpha.
    # Track how many we still need so we can stop early once all are found.
    must_scan_all = (local_count < remote_count)
    missing_needed = (remote_count - local_count) if must_scan_all else 0

    new_alphas = []
    local_ids = set(local_db.keys())
    total_pages = max(1, math.ceil(remote_count / _LC_ALPHA_PAGE_LIMIT))
    stop_paging = False
    failed_offsets = []

    for page_num in range(1, total_pages + 1):
        offset = (page_num - 1) * _LC_ALPHA_PAGE_LIMIT
        try:
            page = _lc_fetch_alpha_page(sess, offset)
        except Exception as e:
            failed_offsets.append(offset)
            continue

        submitted = [
            alpha for alpha in page['results']
            if alpha.get('id') and alpha.get('dateSubmitted') and alpha.get('status') != 'UNSUBMITTED'
        ]
        for alpha in submitted:
            aid = alpha['id']
            if aid in local_ids:
                if not must_scan_all:
                    stop_paging = True
                    break
                # When must_scan_all, skip already-known alphas but keep paging
                continue
            new_alphas.append(alpha)
            local_ids.add(aid)
            if must_scan_all:
                missing_needed -= 1

        msg = f"Alpha page {page_num}/{total_pages}: +{len(new_alphas)} new, total={local_count + len(new_alphas)}"
        print(msg); log_cb and log_cb(msg)

        if stop_paging:
            break
        # All missing alphas found — no need to scan further
        if must_scan_all and missing_needed <= 0:
            msg = f"All {len(new_alphas)} missing alpha(s) found, stopping scan"
            print(msg); log_cb and log_cb(msg)
            break

    # 重试失败页
    for retry_round in range(3):
        if not failed_offsets:
            break
        time.sleep(2 ** retry_round)
        still_failed = []
        for offset in list(failed_offsets):
            try:
                page = _lc_fetch_alpha_page(sess, offset)
                submitted = [
                    alpha for alpha in page['results']
                    if alpha.get('id') and alpha.get('dateSubmitted') and alpha.get('status') != 'UNSUBMITTED'
                ]
                for alpha in submitted:
                    aid = alpha['id']
                    if aid not in local_ids:
                        new_alphas.append(alpha)
                        local_ids.add(aid)
            except Exception:
                still_failed.append(offset)
        failed_offsets = still_failed

    if failed_offsets:
        msg = f"WARNING: {len(failed_offsets)} Alpha page(s) still failed"
        print(msg); log_cb and log_cb(msg)

    for alpha in new_alphas:
        local_db[alpha['id']] = alpha

    # Only remove stale entries if we scanned ALL pages (no early stop)
    # and had no failures — otherwise we might delete alphas we didn't see
    if not stop_paging and not failed_offsets:
        remote_ids = set(local_ids)
        stale_ids = set(local_db.keys()) - remote_ids
        for sid in stale_ids:
            del local_db[sid]

    _lc_save_local_alphas_db(local_db)
    return list(local_db.values())


# ── 单个 Alpha 详情（用于 IS alpha）──

def _lc_fetch_alpha_detail(sess, alpha_id):
    url = f'https://api.worldquantbrain.com/alphas/{alpha_id}'
    try:
        resp = sess.get(url, headers={'accept': 'application/json;version=4.0'})
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def _lc_fetch_pnl_for_alpha(sess, alpha_id, max_retries=5):
    """获取单个 alpha 的 PnL（用于 IS alpha，不缓存到 CSV）。"""
    url = f'https://api.worldquantbrain.com/alphas/{alpha_id}/recordsets/pnl'
    for attempt in range(max_retries):
        try:
            resp = sess.get(url, headers={'accept': 'application/json;version=2.0'})
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return None

        status = resp.status_code
        if status in (401, 403):
            raise Exception(f"PnL auth failed: HTTP {status}")
        if status in (202, 204, 429):
            time.sleep(2 * (attempt + 1))
            continue
        if status != 200:
            return None

        body = resp.text
        if not body or not body.strip():
            time.sleep(3 * (attempt + 1))
            continue
        try:
            data = json.loads(body)
        except Exception:
            time.sleep(3 * (attempt + 1))
            continue
        records = data.get('records', [])
        if not records:
            time.sleep(3 * (attempt + 1))
            continue

        result = []
        for r in records:
            if not isinstance(r, (list, tuple)) or len(r) < 2 or not r[0]:
                continue
            try:
                val = float(r[1])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(val):
                continue
            date_str = str(r[0])[:10]
            result.append((date_str, val))
        result.sort(key=lambda x: x[0])
        return result if len(result) >= 2 else None

    return None


# ── PnL CSV 缓存 ──

def _lc_request_pnl_once(sess, alpha_id):
    url = f'https://api.worldquantbrain.com/alphas/{alpha_id}/recordsets/pnl'
    try:
        resp = sess.get(url, headers={'accept': 'application/json;version=2.0'})
    except Exception:
        return False, None

    status = resp.status_code
    if status in (401, 403):
        raise Exception(f"PnL auth failed: HTTP {status}")
    if status in (202, 204, 429):
        return False, None
    if status != 200:
        return False, None

    try:
        data = resp.json()
        if data and isinstance(data.get('records'), list) and len(data['records']) > 0:
            return True, data
        return False, None
    except Exception:
        return False, None


def _lc_infer_region_from_pnl_path(alpha_id):
    """从 PnL CSV 文件路径推断 alpha 的 region（扫描 region 子目录）。"""
    for base_dir in (_LC_OUTPUT_DIR, _LC_UNSUBMITTED_DIR):
        if not os.path.exists(base_dir):
            continue
        for entry in os.listdir(base_dir):
            subdir = os.path.join(base_dir, entry)
            if os.path.isdir(subdir):
                if os.path.exists(os.path.join(subdir, f'{alpha_id}.csv')):
                    return entry
    return ''


def _lc_pnl_path(alpha_id, region=None, submitted=True):
    """返回 PnL CSV 文件路径（按 region 子目录组织）。"""
    base = _LC_OUTPUT_DIR if submitted else _LC_UNSUBMITTED_DIR
    if region:
        return os.path.join(base, region, f'{alpha_id}.csv')
    # Fallback: 无 region 时查找旧路径或 new path
    return os.path.join(base, f'{alpha_id}.csv')


def _lc_save_pnl_to_csv(alpha_id, pnl_data, region=None):
    records = pnl_data.get('records', [])
    if not records:
        return False
    region_dir = os.path.join(_LC_OUTPUT_DIR, region) if region else _LC_OUTPUT_DIR
    os.makedirs(region_dir, exist_ok=True)
    filepath = os.path.join(region_dir, f'{alpha_id}.csv')
    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['date', 'cum_pnl'])
        for record in records:
            if not isinstance(record, (list, tuple)) or len(record) < 2:
                continue
            date_str = str(record[0])[:10]
            try:
                value = float(record[1])
            except (TypeError, ValueError):
                continue
            writer.writerow([date_str, value])
    return True


def _lc_is_pnl_downloaded(alpha_id, region=None):
    """检查 PnL CSV 是否已下载（在 submitted 或 unsubmitted 目录中）。"""
    db = _lc_load_local_alphas_db()
    if region is None:
        alpha_data = db.get(alpha_id)
        region = alpha_data.get('settings', {}).get('region') if alpha_data else None
    for submitted in (True, False):
        base = _LC_OUTPUT_DIR if submitted else _LC_UNSUBMITTED_DIR
        if region:
            filepath = os.path.join(base, region, f'{alpha_id}.csv')
            if os.path.exists(filepath) and os.path.getsize(filepath) > 20:
                return True
        # Fallback: 旧扁平路径
        filepath = os.path.join(base, f'{alpha_id}.csv')
        if os.path.exists(filepath) and os.path.getsize(filepath) > 20:
            return True
    return False


def _lc_load_pnl_from_csv(alpha_id, region=None, db=None):
    """从 CSV 加载 PnL 数据（先查 submitted，再查 unsubmitted）。
    db: 可选的 alphas_db dict，避免重复加载。"""
    if db is None:
        db = _lc_load_local_alphas_db()
    if region is None:
        alpha_data = db.get(alpha_id)
        region = alpha_data.get('settings', {}).get('region') if alpha_data else None
    for submitted in (True, False):
        base = _LC_OUTPUT_DIR if submitted else _LC_UNSUBMITTED_DIR
        # 优先查 region 子目录
        if region:
            filepath = os.path.join(base, region, f'{alpha_id}.csv')
            if os.path.exists(filepath):
                records = _lc_read_pnl_csv(filepath)
                if records:
                    return records
        # Fallback: 旧扁平路径
        filepath = os.path.join(base, f'{alpha_id}.csv')
        if os.path.exists(filepath):
            records = _lc_read_pnl_csv(filepath)
            if records:
                return records
    return None


def _lc_read_pnl_csv(filepath):
    """从单个 CSV 文件读取 PnL 记录。"""
    records = []
    try:
        with open(filepath, 'r', newline='') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) < 2:
                    continue
                try:
                    date_str = str(row[0])[:10]
                    value = float(row[1])
                    if math.isfinite(value):
                        records.append((date_str, value))
                except (TypeError, ValueError):
                    continue
    except Exception:
        return None
    records.sort(key=lambda x: x[0])
    return records if len(records) >= 2 else None


def _lc_save_unsubmitted_pnl(alpha_id, dates, pnl_values, region=None):
    """将未提交 alpha 的 PnL 保存到 pnl_csv_unsubmitted/{region}/ 目录。
    dates: 日期字符串列表, pnl_values: 累计 PnL 值列表。
    """
    if not dates or not pnl_values or len(dates) != len(pnl_values):
        return False
    region_dir = os.path.join(_LC_UNSUBMITTED_DIR, region) if region else _LC_UNSUBMITTED_DIR
    os.makedirs(region_dir, exist_ok=True)
    filepath = os.path.join(region_dir, f'{alpha_id}.csv')
    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['date', 'cum_pnl'])
        for d, v in zip(dates, pnl_values):
            writer.writerow([str(d)[:10], v])
    return True


def _lc_migrate_submitted_pnls(db):
    """将 pnl_csv_unsubmitted 中已提交的 alpha 的 PnL CSV 移动到 pnl_csv_submitted（按 region）。
    db: alphas_db.json 的内容 (dict of alpha_id -> alpha_data)。
    返回移动的文件数量。
    """
    if not os.path.exists(_LC_UNSUBMITTED_DIR):
        return 0
    moved = 0
    # 遍历 unsubmitted 目录（含 region 子目录和旧扁平文件）
    for root, dirs, files in os.walk(_LC_UNSUBMITTED_DIR):
        for fname in files:
            if not fname.endswith('.csv'):
                continue
            alpha_id = fname[:-4]
            alpha_data = db.get(alpha_id) if isinstance(db, dict) else None
            if alpha_data and alpha_data.get('dateSubmitted'):
                src = os.path.join(root, fname)
                region = alpha_data.get('settings', {}).get('region')
                region_dir = os.path.join(_LC_OUTPUT_DIR, region) if region else _LC_OUTPUT_DIR
                os.makedirs(region_dir, exist_ok=True)
                dst = os.path.join(region_dir, fname)
                try:
                    shutil.move(src, dst)
                    moved += 1
                except Exception:
                    pass
    return moved


def _lc_migrate_old_pnl_dir():
    """启动时迁移旧的 pnl_csv/ 目录为 pnl_csv_submitted/。"""
    if os.path.exists(_LC_OLD_PNL_DIR) and not os.path.exists(_LC_OUTPUT_DIR):
        try:
            shutil.move(_LC_OLD_PNL_DIR, _LC_OUTPUT_DIR)
        except Exception:
            pass


def _lc_migrate_pnl_to_region_dirs():
    """将 pnl_csv_submitted/ 和 pnl_csv_unsubmitted/ 中的扁平 CSV 文件迁移到 region 子目录。"""
    db = _lc_load_local_alphas_db()
    for base_dir in (_LC_OUTPUT_DIR, _LC_UNSUBMITTED_DIR):
        if not os.path.exists(base_dir):
            continue
        for fname in os.listdir(base_dir):
            if not fname.endswith('.csv'):
                continue
            alpha_id = fname[:-4]
            alpha_data = db.get(alpha_id) if isinstance(db, dict) else None
            region = alpha_data.get('settings', {}).get('region') if alpha_data else None
            if not region:
                continue  # 无 region 信息，暂不迁移
            src = os.path.join(base_dir, fname)
            if os.path.isdir(src):
                continue  # 已经是子目录
            region_dir = os.path.join(base_dir, region)
            os.makedirs(region_dir, exist_ok=True)
            dst = os.path.join(region_dir, fname)
            if not os.path.exists(dst):
                try:
                    shutil.move(src, dst)
                except Exception:
                    pass


def _lc_fetch_all_pnls(sess, alpha_ids, log_cb=None):
    """批量获取并保存 PnL。log_cb 用于进度回调。"""
    # 预加载 region 映射
    db = _lc_load_local_alphas_db()
    saved_ids = set()
    failed_ids = set()
    total_batches = math.ceil(len(alpha_ids) / _LC_PNL_BATCH_SIZE)

    for batch_start in range(0, len(alpha_ids), _LC_PNL_BATCH_SIZE):
        batch = alpha_ids[batch_start:batch_start + _LC_PNL_BATCH_SIZE]
        batch_num = batch_start // _LC_PNL_BATCH_SIZE + 1
        pending = list(batch)

        # Warm-up round
        with ThreadPoolExecutor(max_workers=_LC_PNL_CONCURRENCY) as executor:
            future_to_id = {}
            for alpha_id in pending:
                future = executor.submit(_lc_request_pnl_once, sess, alpha_id)
                future_to_id[future] = alpha_id
            still_pending = []
            for future in as_completed(future_to_id):
                alpha_id = future_to_id[future]
                try:
                    ready, data = future.result()
                    if ready:
                        try:
                            region = db.get(alpha_id, {}).get('settings', {}).get('region')
                            _lc_save_pnl_to_csv(alpha_id, data, region=region)
                            saved_ids.add(alpha_id)
                        except Exception:
                            failed_ids.add(alpha_id)
                    else:
                        still_pending.append(alpha_id)
                except Exception:
                    still_pending.append(alpha_id)
            pending = still_pending

        # Retry rounds
        for retry_delay in _LC_PNL_RETRY_DELAYS:
            if not pending:
                break
            time.sleep(retry_delay)
            with ThreadPoolExecutor(max_workers=_LC_PNL_CONCURRENCY) as executor:
                future_to_id = {}
                for alpha_id in pending:
                    future = executor.submit(_lc_request_pnl_once, sess, alpha_id)
                    future_to_id[future] = alpha_id
                still_pending = []
                for future in as_completed(future_to_id):
                    alpha_id = future_to_id[future]
                    try:
                        ready, data = future.result()
                        if ready:
                            try:
                                _lc_save_pnl_to_csv(alpha_id, data, region=db.get(alpha_id, {}).get('settings', {}).get('region'))
                                saved_ids.add(alpha_id)
                            except Exception:
                                failed_ids.add(alpha_id)
                        else:
                            still_pending.append(alpha_id)
                    except Exception:
                        still_pending.append(alpha_id)
                pending = still_pending

        for alpha_id in pending:
            failed_ids.add(alpha_id)

        msg = f"PnL batch {batch_num}/{total_batches}: saved={len(saved_ids)}, failed={len(failed_ids)}"
        print(msg); log_cb and log_cb(msg)

    return saved_ids, failed_ids


# ── 收益率计算（复用 _ic_* 函数）──
# _lc_calendar_window_start, _lc_calculate_returns, _lc_calculate_forward_filled_returns, _lc_pearson
# 已统一为 _ic_* 版本（_ic_pearson 现在返回 (value, count)）

# ── 池选择 ──

def _lc_is_type(alpha, classification):
    classifications = alpha.get('classifications', [])
    if not classifications:
        return False
    for item in classifications:
        if isinstance(item, dict):
            if item.get('id') == classification:
                return True
        elif isinstance(item, str):
            if item == classification:
                return True

    return False


def _lc_is_ra(alpha):
    # Platform admits an alpha to the SELF pool by alpha *type* == 'REGULAR',
    # NOT by a 'REGULAR:REGULAR' classification tag — most regular alphas have
    # empty classifications, so the tag check alone wrongly excludes them and
    # drops the local self-corr max far below the platform value.
    if alpha.get('type') == 'REGULAR':
        return True
    return _lc_is_type(alpha, _LC_RA)

    
def _lc_is_ppa(alpha):
    return _lc_is_type(alpha, _LC_PPA)


def _lc_select_pool(all_alphas, pnl_available_ids, alpha_id, region, corr_type):
    pool = []
    for alpha in all_alphas:
        if alpha['id'] == alpha_id:
            continue
        settings = alpha.get('settings', {})
        if not settings or settings.get('region') != region:
            continue
        if alpha['id'] not in pnl_available_ids:
            continue
        # ── strict_platform_parity: exclude freshly-submitted SELF peers ──
        # The platform's self-corr snapshot is batch-refreshed; alphas
        # submitted within the last _LC_PARITY_FRESH_DAYS haven't been
        # incorporated yet, so we skip them to match the platform output.
        # PPC pool is NOT filtered — it already matches the platform exactly.
        if strict_platform_parity and corr_type == 'SELF':
            ds = alpha.get('dateSubmitted', '')
            if ds:
                try:
                    sub_dt = datetime.fromisoformat(ds.replace('Z', '+00:00'))
                    if sub_dt.tzinfo is None:
                        sub_dt = sub_dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - sub_dt).days < _LC_PARITY_FRESH_DAYS:
                        continue
                except Exception:
                    pass  # unparseable date → keep in pool
        if corr_type == 'SELF':
            if _lc_is_ra(alpha):
                pool.append(alpha)
        elif corr_type == 'PPA':
            # Power-pool alphas are themselves REGULAR by type, so we must NOT
            # exclude RA here — that would empty the PPC pool entirely.
            if _lc_is_ppa(alpha):
                pool.append(alpha)
    return pool


# ── 核心计算 ──

def _lc_round_official(value):
    return round(value + 1e-10, 4)


def _lc_calculate_corr_for_alpha(target_alpha, all_alphas, pnl_cache, pnl_available_ids):
    """计算单个 alpha 的 Self Corr 和 PPC。返回 dict。
    使用 numpy 向量化加速 Pearson 计算。"""
    alpha_id = target_alpha['id']
    region = target_alpha.get('settings', {}).get('region')
    if not region:
        return None

    target_records = pnl_cache.get(alpha_id)
    if target_records is None or len(target_records) < 2:
        return None

    target_start_date = _ic_calendar_window_start(target_records)
    if target_start_date is None:
        return None

    target_returns = _ic_calculate_returns(target_records, target_start_date)

    # 全局日期列表
    date_set = set()
    for records in pnl_cache.values():
        if records:
            for date_str, _ in records:
                date_set.add(date_str)
    global_dates = sorted(date_set)

    if global_dates:
        pool_start_date = f'{int(global_dates[-1][:4]) - _LC_YEARS + 1}-01-01'
    else:
        pool_start_date = target_start_date

    # Build date → index mapping for fast array alignment
    # Only include dates >= pool_start_date that appear in target_returns
    active_dates = sorted(d for d in target_returns if d >= pool_start_date)
    date_to_idx = {d: i for i, d in enumerate(active_dates)}
    n_dates = len(active_dates)

    # Target returns as numpy array (NaN for missing dates)
    target_arr = np.full(n_dates, np.nan)
    for d, v in target_returns.items():
        idx = date_to_idx.get(d)
        if idx is not None:
            target_arr[idx] = v

    # Valid mask for target (non-NaN dates)
    target_valid = ~np.isnan(target_arr)

    # Pre-compute peer returns arrays for all same-region alphas with PnL
    # Cache by peer_id so SELF and PPA pools share the work
    peer_arrays = {}  # peer_id -> np.array of same length as active_dates
    for alpha in all_alphas:
        aid = alpha['id']
        if aid == alpha_id:
            continue
        if alpha.get('settings', {}).get('region') != region:
            continue
        if aid not in pnl_available_ids:
            continue
        peer_records = pnl_cache.get(aid)
        if peer_records is None:
            continue
        peer_returns = _ic_calculate_forward_filled_returns(peer_records, global_dates, pool_start_date)
        arr = np.full(n_dates, np.nan)
        for d, v in peer_returns.items():
            idx = date_to_idx.get(d)
            if idx is not None:
                arr[idx] = v
        peer_arrays[aid] = arr

    # Vectorized Pearson correlation
    def _vec_pearson(t_arr, t_valid, p_arr):
        """Compute Pearson correlation between two arrays, only on overlapping non-NaN dates."""
        overlap = t_valid & ~np.isnan(p_arr)
        count = np.sum(overlap)
        if count < 2:
            return None
        x = t_arr[overlap]
        y = p_arr[overlap]
        # Pearson via mean-centered dot product
        x_mean = x.mean()
        y_mean = y.mean()
        x_c = x - x_mean
        y_c = y - y_mean
        denom = np.sqrt(np.dot(x_c, x_c) * np.dot(y_c, y_c))
        if denom == 0:
            return None
        return np.dot(x_c, y_c) / denom

    result = {
        'alpha_id': alpha_id,
        'region': region,
        'self_corr_max': None, 'self_corr_min': None,
        'self_corr_count': 0, 'self_pool_size': 0,
        'ppa_corr_max': None, 'ppa_corr_min': None,
        'ppa_corr_count': 0, 'ppa_pool_size': 0,
    }

    for corr_type in ['SELF', 'PPA']:
        pool = _lc_select_pool(all_alphas, pnl_available_ids, alpha_id, region, corr_type)
        pool_size = len(pool)

        if corr_type == 'SELF':
            result['self_pool_size'] = pool_size
        else:
            result['ppa_pool_size'] = pool_size

        if pool_size == 0:
            continue

        correlations = []
        peer_corr = []  # (peer_alpha, corr_value) — kept for top/bottom-5 records
        for peer_alpha in pool:
            peer_id = peer_alpha['id']
            p_arr = peer_arrays.get(peer_id)
            if p_arr is None:
                continue
            corr_value = _vec_pearson(target_arr, target_valid, p_arr)
            if corr_value is not None:
                correlations.append(float(corr_value))
                peer_corr.append((peer_alpha, float(corr_value)))

        if not correlations:
            continue

        corr_max = _lc_round_official(max(correlations))
        corr_min = _lc_round_official(min(correlations))

        # Top 5 most-correlated & bottom 5 least-correlated (for the expand panel)
        def _to_rec(pa, cv):
            isd = pa.get('is') or {}
            return {
                'id': pa.get('id'),
                'correlation': _lc_round_official(cv),
                'sharpe': isd.get('sharpe'),
                'returns': isd.get('returns'),
                'turnover': isd.get('turnover'),
                'fitness': isd.get('fitness'),
                'margin': isd.get('margin'),
            }
        sorted_pc = sorted(peer_corr, key=lambda x: x[1], reverse=True)
        top5 = [_to_rec(p, c) for p, c in sorted_pc[:5]]
        bottom5 = [_to_rec(p, c) for p, c in sorted_pc[-5:][::-1]]

        if corr_type == 'SELF':
            result['self_corr_max'] = corr_max
            result['self_corr_min'] = corr_min
            result['self_corr_count'] = len(correlations)
            result['self_corr_top'] = top5
            result['self_corr_bottom'] = bottom5
        else:
            result['ppa_corr_max'] = corr_max
            result['ppa_corr_min'] = corr_min
            result['ppa_corr_count'] = len(correlations)
            result['ppa_corr_top'] = top5
            result['ppa_corr_bottom'] = bottom5

    return result


# ── Worker 类 ──

class DownloadSubmittedAlphasWorker(QThread):
    """Worker thread to sync submitted alpha metadata and download missing PnL CSVs."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(int, int, int)  # total, downloaded, failed

    def __init__(self, client: BrainClient):
        super().__init__()
        self.client = client

    def run(self):
        sess = self.client.session
        self.client.ensure_auth()

        def _log(msg):
            print(msg, flush=True)
            self.progress.emit(msg)

        # 增量同步已提交 Alpha 元数据
        _log("Fetching submitted alpha metadata...")
        try:
            all_alphas = _lc_fetch_all_submitted_alphas(sess, need_download=True, log_cb=_log)
        except Exception as e:
            _log(f"Failed to fetch alphas: {e}")
            self.finished.emit(0, 0, 0)
            return

        if not all_alphas:
            _log("No submitted alphas found.")
            self.finished.emit(0, 0, 0)
            return

        # 将 pnl_csv_unsubmitted 中已提交的 alpha 移动到 pnl_csv_submitted
        db = _lc_load_local_alphas_db()
        moved = _lc_migrate_submitted_pnls(db)
        if moved > 0:
            _log(f"Moved {moved} PnL CSV(s) from unsubmitted to submitted")

        _log(f"Found {len(all_alphas)} submitted alphas, checking PnL cache...")
        os.makedirs(_LC_OUTPUT_DIR, exist_ok=True)

        existing_count = 0
        missing_ids = []
        for alpha in all_alphas:
            aid = alpha['id']
            if _lc_is_pnl_downloaded(aid):
                existing_count += 1
            else:
                missing_ids.append(aid)

        _log(f"PnL cache: {existing_count} existing, {len(missing_ids)} missing")

        if not missing_ids:
            _log("All PnLs already downloaded.")
            self.finished.emit(len(all_alphas), 0, 0)
            return

        _log(f"Downloading PnL for {len(missing_ids)} alphas...")
        start_time = time.time()
        saved_ids, failed_ids = _lc_fetch_all_pnls(sess, missing_ids, log_cb=_log)
        elapsed = time.time() - start_time

        _log(
            f"Done in {elapsed:.1f}s: {len(saved_ids)} downloaded, {len(failed_ids)} failed, "
            f"{existing_count} already cached"
        )
        self.finished.emit(len(all_alphas), len(saved_ids), len(failed_ids))


class LocalCorrelationWorker(QThread):
    """Compute Self Corr and PPC locally (内嵌 local_corr.py 算法)."""
    finished = pyqtSignal(str, dict)  # corr_type, data  (same format as CorrelationWorker)
    error = pyqtSignal(str, str)      # corr_type, error_msg
    progress = pyqtSignal(str)        # status message

    def __init__(self, client: BrainClient, alpha_id: str, corr_type: str):
        super().__init__()
        self.client = client
        self.alpha_id = alpha_id
        self.corr_type = corr_type  # "self" or "ppc"

    def run(self):
        try:
            sess = self.client.session
            self.client.ensure_auth()

            def _log(msg):
                print(msg, flush=True)
                self.progress.emit(msg)

            # 从本地缓存加载已提交 Alpha 元数据
            _log(f"[{self.corr_type}] Loading alpha metadata...")
            all_alphas = _lc_fetch_all_submitted_alphas(sess, need_download=False)

            if not all_alphas:
                self.error.emit(self.corr_type, "No submitted alphas in local cache. Run Download first.")
                return

            # 一次性加载 db，避免每个 alpha 重复读 alphas_db.json
            db = _lc_load_local_alphas_db()

            # 加载 PnL 数据
            _log(f"[{self.corr_type}] Loading PnL data...")
            os.makedirs(_LC_OUTPUT_DIR, exist_ok=True)
            pnl_cache = {}
            pnl_available_ids = set()
            for alpha in all_alphas:
                aid = alpha['id']
                region = alpha.get('settings', {}).get('region')
                records = _lc_load_pnl_from_csv(aid, region=region, db=db)
                if records is not None:
                    pnl_cache[aid] = records
                    pnl_available_ids.add(aid)
            _log(f"[{self.corr_type}] Loaded PnL for {len(pnl_available_ids)}/{len(all_alphas)} alphas")

            # 确定 target alpha
            target_alpha = None
            for a in all_alphas:
                if a['id'] == self.alpha_id:
                    target_alpha = a
                    break

            if target_alpha is None:
                # IS alpha：从 API 获取元数据和 PnL
                _log(f"[{self.corr_type}] Fetching alpha detail for {self.alpha_id}...")
                target_alpha = _lc_fetch_alpha_detail(sess, self.alpha_id)
                if target_alpha is None:
                    self.error.emit(self.corr_type, f"Failed to fetch alpha detail for {self.alpha_id}")
                    return

                _log(f"[{self.corr_type}] Fetching PnL for {self.alpha_id}...")
                target_pnl = _lc_fetch_pnl_for_alpha(sess, self.alpha_id)
                if target_pnl is None:
                    self.error.emit(self.corr_type, f"Failed to fetch PnL for {self.alpha_id}")
                    return

                pnl_cache[self.alpha_id] = target_pnl
                pnl_available_ids.add(self.alpha_id)

            # 计算
            _log(f"[{self.corr_type}] Calculating correlation for {self.alpha_id}...")
            result = _lc_calculate_corr_for_alpha(target_alpha, all_alphas, pnl_cache, pnl_available_ids)

            if result is None:
                self.error.emit(self.corr_type, "No correlation result")
                return

            # 映射到 API 格式
            if self.corr_type == "self":
                max_c = result.get('self_corr_max')
                min_c = result.get('self_corr_min')
                top = result.get('self_corr_top') or []
                bottom = result.get('self_corr_bottom') or []
                _log(f"[self] self_corr_max={max_c}  self_corr_min={min_c}  pool_size={result.get('self_pool_size', 0)}")
            else:  # ppc
                max_c = result.get('ppa_corr_max')
                min_c = result.get('ppa_corr_min')
                top = result.get('ppa_corr_top') or []
                bottom = result.get('ppa_corr_bottom') or []
                _log(f"[ppc] ppa_corr_max={max_c}  ppa_corr_min={min_c}  pool_size={result.get('ppa_pool_size', 0)}")

            # When the target alpha's sharpe < 0 (and we're in local mode — this
            # worker only runs under use_local_corr), surface the 5 LEAST-correlated
            # peers instead of the most-correlated, so the user can find alphas to
            # diversify against a losing alpha.
            target_sharpe = (target_alpha.get('is') or {}).get('sharpe')
            least = (target_sharpe is not None) and (target_sharpe < 0)
            records = bottom if least else top

            self.finished.emit(self.corr_type, {
                "max": max_c, "min": min_c,
                "records": records, "least": least,
            })

        except Exception as e:
            self.error.emit(self.corr_type, str(e))


# ──────────────────────────────────────────────
#  Matplotlib PnL Canvas
# ──────────────────────────────────────────────
class PnlCanvas(FigureCanvas):
    def __init__(self, parent=None):
        self.fig = Figure(figsize=(10, 4), dpi=100, facecolor='#FFFFFF')
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self._nav_toolbar = NavigationToolbar(self, parent)
        self._nav_toolbar.setStyleSheet("""
            QToolBar { background: #313244; border: none; spacing: 3px; padding: 2px; }
            QToolButton { background: #45475a; color: #cdd6f4; border: 1px solid #585b70;
                          border-radius: 3px; padding: 2px 4px; min-width: 20px; }
            QToolButton:hover { background: #585b70; }
        """)
        self._pnl_data = None
        self._draw_empty()

    def _fmt_axis(self, x, pos):
        if abs(x) >= 1e6:
            return f"{x/1e6:.1f}M"
        elif abs(x) >= 1e3:
            return f"{x/1e3:.1f}K"
        else:
            return f"{x:.0f}"

    def _draw_empty(self):
        self._pnl_lines = []
        self._pnl_visible = []
        self.ax.clear()
        self.ax.set_facecolor('#FFFFFF')
        self.ax.set_title(T("PnL Curve"), fontsize=12, color='#313244')
        self.ax.set_ylabel(T("Cumulative PnL"), color='#6c7086')
        self.ax.tick_params(colors='#6c7086')
        self.ax.yaxis.set_major_formatter(mticker.FuncFormatter(self._fmt_axis))
        self.ax.grid(True, alpha=0.3, color='#e0e0e0')
        for spine in self.ax.spines.values():
            spine.set_color('#e0e0e0')
        self.fig.tight_layout()
        self.draw()

    def _on_legend_pick(self, event):
        """Toggle visibility of a PnL line when its legend label is clicked."""
        if not hasattr(self, '_pnl_lines') or not self._pnl_lines:
            return
        # Determine which legend entry was clicked
        legend = self.ax.get_legend()
        if legend is None:
            return
        legend_texts = legend.get_texts()
        clicked_idx = None
        for i, txt in enumerate(legend_texts):
            if event.artist == txt:
                clicked_idx = i
                break
        if clicked_idx is None or clicked_idx >= len(self._pnl_lines):
            return
        # Toggle visibility
        self._pnl_visible[clicked_idx] = not self._pnl_visible[clicked_idx]
        visible = self._pnl_visible[clicked_idx]
        for line in self._pnl_lines[clicked_idx]:
            line.set_visible(visible)
        # Update legend text style: strikethrough-ish for hidden lines
        legend_texts[clicked_idx].set_alpha(1.0 if visible else 0.3)
        self.draw()

    def plot_pnl(self, pnl_data, os_start_date=None):
        self._pnl_data = pnl_data
        self.ax.clear()
        self.ax.set_facecolor('#FFFFFF')

        if not pnl_data or not pnl_data[0]:
            self.ax.set_xticks([])
        else:
            # Unpack pnl_data — may have 4 (standard) or 5 (GLB with sub_regions) elements
            if len(pnl_data) >= 5:
                dates, pnl, risk, invest, sub_regions = pnl_data[0], pnl_data[1], pnl_data[2], pnl_data[3], pnl_data[4]
            else:
                dates, pnl, risk, invest = pnl_data[0], pnl_data[1], pnl_data[2], pnl_data[3]
                sub_regions = {}

            # Track line visibility state for legend click toggle
            self._pnl_lines = []
            self._pnl_visible = []

            # Sub-region colors for GLB
            sub_region_colors = {"AMER": "#7972D8", "APAC": "#D68D83", "EMEA": "#CA76C3"}

            # PnL - blue, with OS portion in green if applicable
            if os_start_date:
                split_idx = None
                for i, d in enumerate(dates):
                    if d >= os_start_date:
                        split_idx = i
                        break
                if split_idx is not None and split_idx > 0:
                    # IS portion (blue) with overlap point for continuity
                    line_is = self.ax.plot(range(split_idx + 1), pnl[:split_idx + 1],
                                color='#59CDD5', linewidth=1.2, label=T('IS PnL'))[0]
                    # OS portion (green) from split point
                    line_os = self.ax.plot(range(split_idx, len(pnl)), pnl[split_idx:],
                                color='#47B74A', linewidth=1.2, label=T('OS PnL'))[0]
                    # Vertical line at OS start
                    self.ax.axvline(x=split_idx, color='#f9e2af', linewidth=0.8,
                                   linestyle='--', alpha=0.7)
                    # IS and OS toggle together as "PnL group"
                    self._pnl_lines.append([line_is, line_os])
                    self._pnl_visible.append(True)
                elif split_idx == 0:
                    # All OS
                    line = self.ax.plot(range(len(pnl)), pnl, color='#47B74A', linewidth=1.2, label=T('OS PnL'))[0]
                    self._pnl_lines.append([line])
                    self._pnl_visible.append(True)
                else:
                    # No split found, draw all as IS
                    line = self.ax.plot(range(len(pnl)), pnl, color='#59CDD5', linewidth=1.2, label=T('PnL'))[0]
                    self._pnl_lines.append([line])
                    self._pnl_visible.append(True)
            else:
                line = self.ax.plot(range(len(pnl)), pnl, color='#59CDD5', linewidth=1.2, label=T('PnL'))[0]
                self._pnl_lines.append([line])
                self._pnl_visible.append(True)
            # Risk Neutralized PnL - green
            if risk and any(v != 0 for v in risk):
                line = self.ax.plot(range(len(risk)), risk, color='#60CA68', linewidth=1.0, label=T('Risk Neutralized PnL'))[0]
                self._pnl_lines.append([line])
                self._pnl_visible.append(True)
            # Investability Constrained PnL - yellow-green
            if invest and any(v != 0 for v in invest):
                line = self.ax.plot(range(len(invest)), invest, color='#ACD147', linewidth=1.0, label=T('Investability Constrained PnL'))[0]
                self._pnl_lines.append([line])
                self._pnl_visible.append(True)
            # GLB Sub-region PnL
            for region_name in ["AMER", "APAC", "EMEA"]:
                region_pnl = sub_regions.get(region_name)
                if region_pnl and any(v != 0 for v in region_pnl):
                    line = self.ax.plot(range(len(region_pnl)), region_pnl,
                                color=sub_region_colors[region_name], linewidth=0.8,
                                linestyle='--', label=f'{region_name}{T(" PnL")}')[0]
                    self._pnl_lines.append([line])
                    self._pnl_visible.append(True)

            # X-axis: show every year start
            year_starts = []
            seen_years = set()
            for i, d in enumerate(dates):
                try:
                    year = d[:4]
                    if year not in seen_years:
                        year_starts.append(i)
                        seen_years.add(year)
                except (ValueError, IndexError):
                    pass

            self.ax.set_xticks(year_starts)
            self.ax.set_xticklabels(sorted(seen_years), rotation=0, fontsize=9)
            self.ax.set_xlim(0, len(dates) - 1)

            # Legend below chart — make it clickable to toggle line visibility
            n_lines = len(self._pnl_lines)
            ncol = min(n_lines, 4) if n_lines > 3 else 3
            legend = self.ax.legend(fontsize=8, loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=ncol, frameon=False)
            for text_item in legend.get_texts():
                text_item.set_picker(True)  # Make legend text pickable
            self.fig.canvas.mpl_connect('pick_event', self._on_legend_pick)

        self.ax.set_title(T("PnL Curve"), fontsize=12, color='#313244')
        self.ax.set_ylabel(T("Cumulative PnL"), color='#6c7086')
        self.ax.tick_params(colors='#6c7086')
        self.ax.yaxis.set_major_formatter(mticker.FuncFormatter(self._fmt_axis))
        self.ax.grid(True, alpha=0.3, color='#e0e0e0')
        for spine in self.ax.spines.values():
            spine.set_color('#e0e0e0')
        self.fig.tight_layout()
        self.draw()


# ──────────────────────────────────────────────
#  Flow Layout (auto-wrapping tags)
# ──────────────────────────────────────────────
class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._spacing = spacing
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, dry_run):
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0
        avail_width = rect.width() - margins.left() - margins.right()

        for item in self._items:
            widget = item.widget()
            if widget is None:
                continue
            sz = widget.sizeHint()
            next_x = x + sz.width() + self._spacing
            if next_x - self._spacing > rect.x() + margins.left() + avail_width and line_height > 0:
                x = rect.x() + margins.left()
                y = y + line_height + self._spacing
                next_x = x + sz.width() + self._spacing
                line_height = 0
            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), sz))
            x = next_x
            line_height = max(line_height, sz.height())

        return y + line_height - rect.y() + margins.bottom()


# ──────────────────────────────────────────────
#  Add to List Dialog
# ──────────────────────────────────────────────
class ListDialog(QWidget):
    done = pyqtSignal()

    def __init__(self, client, alpha_id, parent=None):
        super().__init__(parent)
        self.client = client
        self.alpha_id = alpha_id
        self.setWindowTitle(T("Add to List"))
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setMinimumWidth(400)
        self.setStyleSheet("background: #1e1e2e; color: #cdd6f4;")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Alpha ID display
        id_label = QLabel(f"{T('Alpha: ')}{alpha_id}")
        id_label.setStyleSheet("color: #89b4fa; font-size: 12pt; font-weight: bold;")
        layout.addWidget(id_label)

        # List name row
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(T("List:"))
        label.setStyleSheet("color: #a6adc8; font-size: 11pt;")
        label.setFixedWidth(40)
        row.addWidget(label)

        self.list_combo = QComboBox()
        self.list_combo.setEditable(True)
        self.list_combo.setStyleSheet("""
            QComboBox {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 6px; padding: 4px 8px; font-size: 11pt; min-width: 250px;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox::down-arrow { image: none; border-left: 4px solid transparent;
                                     border-right: 4px solid transparent;
                                     border-top: 6px solid #a6adc8; margin-right: 6px; }
            QComboBox QAbstractItemView { background: #313244; color: #cdd6f4;
                                          selection-background-color: #45475a; border: 1px solid #45475a; }
        """)
        row.addWidget(self.list_combo)
        layout.addLayout(row)

        # Hint
        hint = QLabel(T("Select existing list or type new name to create"))
        hint.setStyleSheet("color: #6c7086; font-size: 9pt;")
        layout.addWidget(hint)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton(T("Cancel"))
        cancel_btn.setStyleSheet("""
            QPushButton { background: #45475a; color: #cdd6f4; font-size: 11pt;
                          font-weight: bold; border-radius: 6px; padding: 6px 20px; border: none; }
            QPushButton:hover { background: #585b70; }
        """)
        cancel_btn.clicked.connect(self.close)
        btn_row.addWidget(cancel_btn)

        self.add_btn = QPushButton(T("Add"))
        self.add_btn.setStyleSheet("""
            QPushButton { background: #89b4fa; color: #1e1e2e; font-size: 11pt;
                          font-weight: bold; border-radius: 6px; padding: 6px 20px; border: none; }
            QPushButton:hover { background: #74c7ec; }
            QPushButton:disabled { background: #45475a; color: #6c7086; }
        """)
        self.add_btn.clicked.connect(self._on_add)
        self.add_btn.setEnabled(False)
        btn_row.addWidget(self.add_btn)

        layout.addLayout(btn_row)

        # Status
        self.status_label = QLabel(T("Loading lists..."))
        self.status_label.setStyleSheet("color: #6c7086; font-size: 9pt;")
        layout.addWidget(self.status_label)

        # Fetch lists in background
        self._tags = []
        self._fetch_tags()

    def _fetch_tags(self):
        class TagWorker(QThread):
            finished = _sig(list)
            error = _sig(str)

            def __init__(self, client):
                super().__init__()
                self.client = client

            def run(self):
                try:
                    tags = self.client.get_tags()
                    self.finished.emit(tags)
                except Exception as e:
                    self.error.emit(str(e))

        self._worker = TagWorker(self.client)
        self._worker.finished.connect(self._on_tags_loaded)
        self._worker.error.connect(lambda msg: self.status_label.setText(f"{T('Error: ')}{msg}"))
        self._worker.start()

    def _on_tags_loaded(self, tags):
        self._tags = tags
        self.list_combo.clear()
        for tag in tags:
            name = tag.get("name", "")
            count = len(tag.get("alphas", []))
            self.list_combo.addItem(f"{name} ({count})")
        self.add_btn.setEnabled(True)
        self.status_label.setText(f"{T('Loaded ')}{len(tags)}{T(' lists')}")

    def _on_add(self):
        text = self.list_combo.currentText().strip()
        if not text:
            return

        # Extract name from "name (count)" format or use raw text
        name = text
        if " (" in text and text.endswith(")"):
            name = text[:text.rfind(" (")]

        self.add_btn.setEnabled(False)
        self.status_label.setText(f"{T('Adding to ')}'{name}'{T('...')}")
        self.status_label.setStyleSheet("color: #f9e2af; font-size: 9pt;")

        class AddWorker(QThread):
            finished = _sig(str)
            error = _sig(str)

            def __init__(self, client, alpha_id, tags, name):
                super().__init__()
                self.client = client
                self.alpha_id = alpha_id
                self.tags = tags
                self.name = name

            def run(self):
                try:
                    # Find existing tag by name
                    existing_tag = None
                    for tag in self.tags:
                        if tag.get("name") == self.name:
                            existing_tag = tag
                            break

                    if existing_tag:
                        # Check if alpha already in this list
                        existing_alphas = existing_tag.get("alphas", [])
                        if self.alpha_id in existing_alphas:
                            self.finished.emit(f"{T('Already in list ')}'{self.name}'")
                            return
                        tag_id = existing_tag.get("id")
                        if not tag_id:
                            self.error.emit(f"{T('List ')}'{self.name}'{T(' has no ID')}")
                            return
                        self.client.add_alpha_to_tag(tag_id, self.name, self.alpha_id)
                        self.finished.emit(f"{T('Added to list ')}'{self.name}'")
                    else:
                        new_tag = self.client.create_tag(self.name, self.alpha_id)
                        tag_id = new_tag.get("id")
                        if tag_id:
                            self.finished.emit(f"{T('Created list ')}'{self.name}'{T(' and added alpha')}")
                        else:
                            self.error.emit(T("Created list but got no ID back"))
                except Exception as e:
                    self.error.emit(str(e))

        self._add_worker = AddWorker(self.client, self.alpha_id, self._tags, name)
        self._add_worker.finished.connect(self._on_add_done)
        self._add_worker.error.connect(self._on_add_error)
        self._add_worker.start()

    def _on_add_done(self, msg):
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: #a6e3a1; font-size: 9pt;")
        self.add_btn.setEnabled(True)
        self.done.emit()

    def _on_add_error(self, msg):
        self.status_label.setText(f"{T('Error: ')}{msg}")
        self.status_label.setStyleSheet("color: #f38ba8; font-size: 9pt;")
        self.add_btn.setEnabled(True)


# ──────────────────────────────────────────────
#  Pinned Metrics Window (always-on-top screenshot)
# ──────────────────────────────────────────────
class _PinnedMetricsWindow(QWidget):
    """Frameless always-on-top window showing a pinned screenshot of Key Metrics."""

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # no taskbar entry
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet("background: #1e1e2e;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Close button row
        close_row = QHBoxLayout()
        close_row.setContentsMargins(4, 2, 4, 0)
        close_row.addStretch()

        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(20, 20)
        self._close_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #f38ba8; font-size: 10pt; font-weight: bold;
                border: none; border-radius: 10px;
            }
            QPushButton:hover { background: #f38ba8; color: #1e1e2e; }
        """)
        self._close_btn.clicked.connect(self.close)
        close_row.addWidget(self._close_btn)
        layout.addLayout(close_row)

        # Screenshot image
        lbl = QLabel()
        lbl.setPixmap(pixmap)
        lbl.setStyleSheet("background: transparent;")
        layout.addWidget(lbl)

        self.setFixedSize(pixmap.size() + QSize(8, 24))
        self._drag_pos = None

        # Esc to close
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


#  Single Simulate Tab
# ──────────────────────────────────────────────
class SimulateTab(QWidget):
    clone_requested = pyqtSignal()  # emitted when "+" button clicked
    tab_title_update = pyqtSignal(str)  # emitted to update tab title
    running_state_changed = pyqtSignal()  # emitted when simulation starts/stops
    sim_finished = pyqtSignal(object)  # emitted when sim finishes: self

    # Loading animation frames
    LOADING_FRAMES = ["⏳", "⌛", "⏳", "⌛"]

    # Regions available from pyramid-alphas API (updated at login)
    _available_regions = list(REGION_OPTIONS.keys())

    # Tab states
    STATE_IDLE = "idle"
    STATE_QUEUED = "queued"
    STATE_RUNNING = "running"
    STATE_DONE_UNVIEWED = "done_unviewed"
    STATE_DONE_VIEWED = "done_viewed"

    MAX_CONCURRENT = 8

    def __init__(self, client: BrainClient, parent=None):
        super().__init__(parent)
        self.client = client
        self.worker = None
        self._alpha_id = None
        self._state = self.STATE_IDLE
        self._tab_base_name = T("Simulate")
        self._batch_id = None
        self._batch_expr = None
        self._simulated_universes = set()
        self._simulated_neutrals = set()
        self._simulated_decay = set()
        self._current_progress_pct = 0
        self._current_elapsed = 0
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(6)

        # ── Left Panel: Expression + Settings ──
        left_panel = QWidget()
        left_panel.setMinimumWidth(380)
        self._left_layout = QVBoxLayout(left_panel)
        self._left_layout.setSpacing(6)

        self.expr_toggle_btn = QPushButton(T("Alpha Expression"))
        self.expr_toggle_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 16pt; font-weight: bold;
                border: 1px solid #45475a; border-radius: 6px; padding: 4px 12px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.expr_toggle_btn.setCheckable(True)
        self.expr_toggle_btn.setChecked(True)

        self.expr_group = QGroupBox()
        expr_layout = QVBoxLayout(self.expr_group)

        # Copy / Import buttons row (top center)
        expr_btn_row = QHBoxLayout()
        expr_btn_row.addStretch()
        self._copy_expr_btn = QPushButton(T("Copy"))
        self._copy_expr_btn.setFixedHeight(24)
        self._copy_expr_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #cdd6f4; font-size: 10pt; font-weight: bold;
                border: 1px solid #585b70; border-radius: 4px; padding: 2px 14px;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self._copy_expr_btn.setToolTip(T("Copy expression to clipboard"))
        self._copy_expr_btn.clicked.connect(self._copy_expr)
        expr_btn_row.addWidget(self._copy_expr_btn)

        self._import_expr_btn = QPushButton(T("Import"))
        self._import_expr_btn.setFixedHeight(24)
        self._import_expr_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #cdd6f4; font-size: 10pt; font-weight: bold;
                border: 1px solid #585b70; border-radius: 4px; padding: 2px 14px;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self._import_expr_btn.setToolTip(T("Import expression from clipboard"))
        self._import_expr_btn.clicked.connect(self._import_expr)
        expr_btn_row.addWidget(self._import_expr_btn)
        expr_btn_row.addStretch()
        expr_layout.addLayout(expr_btn_row)

        expr_input_row = QHBoxLayout()
        self.expr_input = QTextEdit("1")
        self.expr_input.setStyleSheet("""
            QTextEdit { font-size: 18pt; }
        """)
        self.expr_input.setLineWrapMode(QTextEdit.WidgetWidth)
        self.expr_input.setMinimumHeight(120)
        self._completion_popup = CompletionPopup(self)
        self.expr_input.installEventFilter(self)
        self.expr_input.textChanged.connect(self._update_completion)
        self._expr_highlighter = AlphaExprHighlighter(self.expr_input.document())
        expr_input_row.addWidget(self.expr_input)

        # Right-side buttons column
        expr_btn_col = QVBoxLayout()
        expr_btn_col.setSpacing(2)

        self.expr_fullscreen_btn = QPushButton("⛶")
        self.expr_fullscreen_btn.setFixedSize(28, 28)
        self.expr_fullscreen_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cdd6f4; font-size: 12pt;
                border: 1px solid #45475a; border-radius: 4px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.expr_fullscreen_btn.setToolTip(T("Full screen"))
        self.expr_fullscreen_btn.clicked.connect(self._toggle_expr_fullscreen)
        expr_btn_col.addWidget(self.expr_fullscreen_btn)

        # VSCode edit button
        self._vscode_btn = QPushButton("📝")
        self._vscode_btn.setFixedSize(28, 28)
        self._vscode_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 12pt;
                border: 1px solid #45475a; border-radius: 4px;
            }
            QPushButton:hover { background: #45475a; border: 1px solid #89b4fa; }
        """)
        self._vscode_btn.setToolTip(T("Edit in VSCode"))
        self._vscode_btn.clicked.connect(self._open_in_vscode)
        expr_btn_col.addWidget(self._vscode_btn)
        expr_btn_col.addStretch()

        expr_input_row.addLayout(expr_btn_col)

        # Setup temp file and file watcher for VSCode sync
        self._vscode_temp_dir = tempfile.mkdtemp(prefix="brain_expr_")
        self._vscode_temp_file = os.path.join(self._vscode_temp_dir, "expression.txt")
        with open(self._vscode_temp_file, 'w', encoding='utf-8') as f:
            f.write("1")
        self._vscode_watcher = QFileSystemWatcher()
        self._vscode_watcher.fileChanged.connect(self._on_vscode_file_changed)
        self._vscode_watcher.addPath(self._vscode_temp_file)
        self._vscode_syncing = False  # guard against recursive updates
        # Sync expr_input → temp file whenever text changes (from setPlainText calls)
        self.expr_input.textChanged.connect(self._sync_expr_to_tempfile)

        expr_layout.addLayout(expr_input_row)
        self.expr_toggle_btn.toggled.connect(self.expr_group.setVisible)
        self._left_layout.addWidget(self.expr_toggle_btn)
        self._left_layout.addWidget(self.expr_group)

        self.settings_toggle_btn = QPushButton(T("Simulation Settings"))
        self.settings_toggle_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 16pt; font-weight: bold;
                border: 1px solid #45475a; border-radius: 6px; padding: 4px 12px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.settings_toggle_btn.setCheckable(True)
        self.settings_toggle_btn.setChecked(True)

        self.settings_group = QGroupBox()
        settings_outer = QVBoxLayout(self.settings_group)
        settings_outer.setSpacing(2)
        settings_outer.setContentsMargins(4, 4, 4, 4)

        # Copy / Import buttons row (above settings grid)
        settings_btn_row = QHBoxLayout()
        settings_btn_row.addStretch()
        self._copy_settings_btn = QPushButton(T("Copy"))
        self._copy_settings_btn.setFixedHeight(24)
        self._copy_settings_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #cdd6f4; font-size: 10pt; font-weight: bold;
                border: 1px solid #585b70; border-radius: 4px; padding: 2px 14px;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self._copy_settings_btn.setToolTip(T("Copy current settings to clipboard"))
        self._copy_settings_btn.clicked.connect(self._copy_settings)
        settings_btn_row.addWidget(self._copy_settings_btn)

        self._import_settings_btn = QPushButton(T("Import"))
        self._import_settings_btn.setFixedHeight(24)
        self._import_settings_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #cdd6f4; font-size: 10pt; font-weight: bold;
                border: 1px solid #585b70; border-radius: 4px; padding: 2px 14px;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self._import_settings_btn.setToolTip(T("Import settings from clipboard"))
        self._import_settings_btn.clicked.connect(self._import_settings)
        settings_btn_row.addWidget(self._import_settings_btn)
        settings_btn_row.addStretch()
        settings_outer.addLayout(settings_btn_row)

        # Settings in two rows, each row sized by settings_size_cfg stretch factors
        settings_row1 = QHBoxLayout()
        settings_row1.setSpacing(4)
        settings_row1.setContentsMargins(0, 0, 0, 0)
        settings_row2 = QHBoxLayout()
        settings_row2.setSpacing(4)
        settings_row2.setContentsMargins(0, 0, 0, 0)
        settings_outer.addLayout(settings_row1)
        settings_outer.addLayout(settings_row2)

        def make_setting_col(label_text, widget, stretch=1, row=None):
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #a6adc8; font-size: 10pt; font-weight: bold; background: transparent; border: none;")
            lbl.setAlignment(Qt.AlignCenter)
            col.addWidget(lbl)
            col.addWidget(widget)
            container = QWidget()
            container.setLayout(col)
            container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row.addWidget(container, stretch)
            return container

        self.region_combo = QComboBox()
        self.region_combo.addItems(SimulateTab._available_regions)
        self.region_combo.setCurrentText("USA")
        self.region_combo.currentTextChanged.connect(self._on_region_changed)

        self.universe_combo = QComboBox()
        self.universe_combo.addItems(["?"] + REGION_OPTIONS["USA"]["universes"])
        self.universe_combo.setCurrentText("TOP3000")

        self.universe_edit_check = QCheckBox("✏")
        self.universe_edit_check.setFixedSize(26, 26)
        self.universe_edit_check.setToolTip(T("Toggle edit mode to type custom values (e.g. ?1 for sequential tune)"))
        self.universe_edit_check.setStyleSheet("""
            QCheckBox {
                background-color: transparent; color: #a6adc8; font-size: 12px;
                font-weight: bold; border: none; padding: 0px; spacing: 0px;
            }
            QCheckBox::indicator { width: 14px; height: 14px; }
            QCheckBox::indicator:checked { background-color: #cba6f7; border: 1px solid #cba6f7; }
            QCheckBox::indicator:unchecked { background-color: #313244; border: 1px solid #45475a; }
        """)
        self.universe_edit_check.toggled.connect(lambda checked: self._toggle_combo_edit(self.universe_combo, checked))

        self.universe_traverse_btn = QPushButton("⟳")
        self.universe_traverse_btn.setFixedSize(26, 26)
        self.universe_traverse_btn.setToolTip(T("Traverse all universes for this region"))
        self.universe_traverse_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #89b4fa; font-size: 12pt; font-weight: bold;
                border-radius: 4px; border: none;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self.universe_traverse_btn.clicked.connect(self._traverse_universes)

        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 1)
        self.delay_spin.setValue(1)

        self.decay_input = QLineEdit("0")
        self.decay_input.setFixedHeight(28)
        self.decay_input.setAlignment(Qt.AlignCenter)
        self.decay_input.setToolTip(T("Decay value. Single number (e.g. 10), comma-separated (e.g. 1,2,3) for traversal, ? for auto-tune, ?1 for sequential tune, 10?1 to fix at 10 before tune group 1."))
        self.decay_input.setStyleSheet("""
            QLineEdit {
                background: #181825; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 4px; padding: 2px 4px; font-size: 11pt; font-weight: bold;
            }
            QLineEdit:focus { border: 1px solid #89b4fa; }
        """)
        self.decay_input.textChanged.connect(self._on_decay_text_changed)

        # Step up / step down buttons (like QDoubleSpinBox arrows)
        self.decay_step_up_btn = QPushButton("▲")
        self.decay_step_up_btn.setFixedSize(18, 13)
        self.decay_step_up_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 2px; font-size: 7pt; font-weight: bold; padding: 0px;
            }
            QPushButton:hover { background: #45475a; }
            QPushButton:disabled { color: #45475a; background: #181825; }
        """)
        self.decay_step_up_btn.clicked.connect(self._step_decay_up)

        self.decay_step_down_btn = QPushButton("▼")
        self.decay_step_down_btn.setFixedSize(18, 13)
        self.decay_step_down_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 2px; font-size: 7pt; font-weight: bold; padding: 0px;
            }
            QPushButton:hover { background: #45475a; }
            QPushButton:disabled { color: #45475a; background: #181825; }
        """)
        self.decay_step_down_btn.clicked.connect(self._step_decay_down)

        self.decay_traverse_btn = QPushButton("⟳")
        self.decay_traverse_btn.setFixedSize(26, 26)
        self.decay_traverse_btn.setToolTip(T("Traverse decay values from input (e.g. 1,2,3) or default [0,10,15,21,42,63,126,252,512]"))
        self.decay_traverse_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #89b4fa; font-size: 12pt; font-weight: bold;
                border-radius: 4px; border: none;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self.decay_traverse_btn.clicked.connect(self._traverse_decay)

        self._simulated_decay = set()

        self.neutral_combo = QComboBox()
        self.neutral_combo.addItems(["?"] + REGION_OPTIONS["USA"]["neutralizations"])
        self.neutral_combo.setCurrentText("INDUSTRY")

        self.neutral_edit_check = QCheckBox("✏")
        self.neutral_edit_check.setFixedSize(26, 26)
        self.neutral_edit_check.setToolTip(T("Toggle edit mode to type custom values (e.g. ?1 for sequential tune)"))
        self.neutral_edit_check.setStyleSheet("""
            QCheckBox {
                background-color: transparent; color: #a6adc8; font-size: 12px;
                font-weight: bold; border: none; padding: 0px; spacing: 0px;
            }
            QCheckBox::indicator { width: 14px; height: 14px; }
            QCheckBox::indicator:checked { background-color: #cba6f7; border: 1px solid #cba6f7; }
            QCheckBox::indicator:unchecked { background-color: #313244; border: 1px solid #45475a; }
        """)
        self.neutral_edit_check.toggled.connect(lambda checked: self._toggle_combo_edit(self.neutral_combo, checked))

        self.neutral_traverse_btn = QPushButton("⟳")
        self.neutral_traverse_btn.setFixedSize(26, 26)
        self.neutral_traverse_btn.setToolTip(T("Traverse all neutralizations for this region"))
        self.neutral_traverse_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #89b4fa; font-size: 12pt; font-weight: bold;
                border-radius: 4px; border: none;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self.neutral_traverse_btn.clicked.connect(self._traverse_neutrals)

        self.truncation_input = QLineEdit("0.08")
        self.truncation_input.setFixedHeight(28)
        self.truncation_input.setAlignment(Qt.AlignCenter)
        self.truncation_input.setToolTip(T("Truncation value. Single number (e.g. 0.08), comma-separated (e.g. 0.01,0.05,0.1) for traversal, ? for auto-tune, ?2 for sequential tune, 0.08?2 to fix at 0.08 before tune group 2."))
        self.truncation_input.setStyleSheet("""
            QLineEdit {
                background: #181825; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 4px; padding: 2px 4px; font-size: 11pt; font-weight: bold;
            }
            QLineEdit:focus { border: 1px solid #89b4fa; }
        """)
        self.truncation_input.textChanged.connect(self._on_truncation_text_changed)

        # Step up / step down buttons (like QDoubleSpinBox arrows)
        self.truncation_step_up_btn = QPushButton("▲")
        self.truncation_step_up_btn.setFixedSize(18, 13)
        self.truncation_step_up_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 2px; font-size: 7pt; font-weight: bold; padding: 0px;
            }
            QPushButton:hover { background: #45475a; }
            QPushButton:disabled { color: #45475a; background: #181825; }
        """)
        self.truncation_step_up_btn.clicked.connect(self._step_truncation_up)

        self.truncation_step_down_btn = QPushButton("▼")
        self.truncation_step_down_btn.setFixedSize(18, 13)
        self.truncation_step_down_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 2px; font-size: 7pt; font-weight: bold; padding: 0px;
            }
            QPushButton:hover { background: #45475a; }
            QPushButton:disabled { color: #45475a; background: #181825; }
        """)
        self.truncation_step_down_btn.clicked.connect(self._step_truncation_down)

        self.truncation_traverse_btn = QPushButton("⟳")
        self.truncation_traverse_btn.setFixedSize(26, 26)
        self.truncation_traverse_btn.setToolTip(T("Traverse truncation values from input (e.g. 0.01,0.05,0.1) or default [0.001,0.005,0.01,0.03,0.05,0.1]"))
        self.truncation_traverse_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #89b4fa; font-size: 12pt; font-weight: bold;
                border-radius: 4px; border: none;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self.truncation_traverse_btn.clicked.connect(self._traverse_truncation)

        self._simulated_truncation = set()

        self.pasteur_combo = QComboBox()
        self.pasteur_combo.addItems(["ON", "OFF"])
        self.pasteur_combo.setCurrentText("ON")

        self.nan_combo = QComboBox()
        self.nan_combo.addItems(["OFF", "ON"])
        self.nan_combo.setCurrentText("ON")

        self.max_trade_combo = QComboBox()
        self.max_trade_combo.addItems(["OFF", "ON"])
        self.max_trade_combo.setCurrentText("OFF")

        self.max_position_combo = QComboBox()
        self.max_position_combo.addItems(["OFF", "ON"])
        self.max_position_combo.setCurrentText("OFF")

        self.language_combo = QComboBox()
        self.language_combo.addItems(["FASTEXPR", "PYTHON"])

        self.lookback_spin = QSpinBox()
        self.lookback_spin.setRange(0, 1024)
        self.lookback_spin.setValue(DEFAULT_LOOKBACK)

        # Region
        make_setting_col(T("Region"), self.region_combo, settings_size_cfg['Region'], row=settings_row1)

        # Universe combo + traverse button
        universe_col = QVBoxLayout()
        universe_col.setSpacing(2)
        universe_lbl = QLabel(T("Universe"))
        universe_lbl.setStyleSheet("color: #a6adc8; font-size: 10pt; font-weight: bold; background: transparent; border: none;")
        universe_lbl.setAlignment(Qt.AlignCenter)
        universe_row = QHBoxLayout()
        universe_row.setSpacing(2)
        universe_row.addWidget(self.universe_combo)
        universe_row.addWidget(self.universe_edit_check)
        universe_row.addWidget(self.universe_traverse_btn)
        universe_col.addWidget(universe_lbl)
        universe_col.addLayout(universe_row)
        universe_container = QWidget()
        universe_container.setLayout(universe_col)
        universe_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        settings_row1.addWidget(universe_container, settings_size_cfg['Universe'])

        # Delay
        make_setting_col(T("Delay"), self.delay_spin, settings_size_cfg['Delay'], row=settings_row1)

        # Decay combo + traverse button
        decay_col = QVBoxLayout()
        decay_col.setSpacing(2)
        decay_lbl = QLabel(T("Decay"))
        decay_lbl.setStyleSheet("color: #a6adc8; font-size: 10pt; font-weight: bold; background: transparent; border: none;")
        decay_lbl.setAlignment(Qt.AlignCenter)
        decay_row = QHBoxLayout()
        decay_row.setSpacing(2)
        decay_row.addWidget(self.decay_input)
        # Step up/down buttons stacked vertically
        decay_step_col = QVBoxLayout()
        decay_step_col.setSpacing(0)
        decay_step_col.setContentsMargins(0, 0, 0, 0)
        decay_step_col.addWidget(self.decay_step_up_btn)
        decay_step_col.addWidget(self.decay_step_down_btn)
        decay_row.addLayout(decay_step_col)
        decay_row.addWidget(self.decay_traverse_btn)
        decay_col.addWidget(decay_lbl)
        decay_col.addLayout(decay_row)
        decay_container = QWidget()
        decay_container.setLayout(decay_col)
        decay_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        settings_row1.addWidget(decay_container, settings_size_cfg['Decay'])

        # Neutral combo + traverse button
        neutral_col = QVBoxLayout()
        neutral_col.setSpacing(2)
        neutral_lbl = QLabel(T("Neutral"))
        neutral_lbl.setStyleSheet("color: #a6adc8; font-size: 10pt; font-weight: bold; background: transparent; border: none;")
        neutral_lbl.setAlignment(Qt.AlignCenter)
        neutral_row = QHBoxLayout()
        neutral_row.setSpacing(2)
        neutral_row.addWidget(self.neutral_combo)
        neutral_row.addWidget(self.neutral_edit_check)
        neutral_row.addWidget(self.neutral_traverse_btn)
        neutral_col.addWidget(neutral_lbl)
        neutral_col.addLayout(neutral_row)
        neutral_container = QWidget()
        neutral_container.setLayout(neutral_col)
        neutral_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        settings_row1.addWidget(neutral_container, settings_size_cfg['Neutral'])

        # Truncation with traverse button
        trunc_col = QVBoxLayout()
        trunc_col.setSpacing(2)
        trunc_lbl = QLabel(T("Truncation"))
        trunc_lbl.setStyleSheet("color: #a6adc8; font-size: 10pt; font-weight: bold; background: transparent; border: none;")
        trunc_lbl.setAlignment(Qt.AlignCenter)
        trunc_row = QHBoxLayout()
        trunc_row.setSpacing(2)
        trunc_row.addWidget(self.truncation_input)
        # Step up/down buttons stacked vertically
        trunc_step_col = QVBoxLayout()
        trunc_step_col.setSpacing(0)
        trunc_step_col.setContentsMargins(0, 0, 0, 0)
        trunc_step_col.addWidget(self.truncation_step_up_btn)
        trunc_step_col.addWidget(self.truncation_step_down_btn)
        trunc_row.addLayout(trunc_step_col)
        trunc_row.addWidget(self.truncation_traverse_btn)
        trunc_col.addWidget(trunc_lbl)
        trunc_col.addLayout(trunc_row)
        trunc_container = QWidget()
        trunc_container.setLayout(trunc_col)
        trunc_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        settings_row2.addWidget(trunc_container, settings_size_cfg['Truncation'])

        # Other settings (row 2)
        for label, w, key in [(T("Pasteur"), self.pasteur_combo, 'Pasteur'),
                         (T("NaN"), self.nan_combo, 'NaN'),
                         (T("Max Trade"), self.max_trade_combo, 'Max Trade'),
                         (T("Max Pos"), self.max_position_combo, 'Max Pos'),
                         (T("Language"), self.language_combo, 'Language'),
                         (T("Lookback"), self.lookback_spin, 'Lookback')]:
            make_setting_col(label, w, settings_size_cfg[key], row=settings_row2)

        self._left_layout.addWidget(self.settings_toggle_btn)
        self._left_layout.addWidget(self.settings_group)
        self.settings_toggle_btn.toggled.connect(self.settings_group.setVisible)

        # Simulate button + progress
        btn_layout = QHBoxLayout()
        self.sim_btn = QPushButton(T("Simulate"))
        self.sim_btn.setFixedHeight(40)
        self.sim_btn.setStyleSheet("""
            QPushButton {
                background-color: #47B74A; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none;
            }
            QPushButton:hover { background-color: #5CC85F; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)

        self.fill8_btn = QPushButton(T("Fill"))
        self.fill8_btn.setFixedHeight(40)
        self.fill8_btn.setStyleSheet("""
            QPushButton {
                background-color: #f9e2af; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none;
            }
            QPushButton:hover { background-color: #fab387; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.fill8_btn.clicked.connect(self._fill_8)

        self.fill_count_spin = QSpinBox()
        self.fill_count_spin.setRange(1, 32)
        self.fill_count_spin.setValue(8)
        self.fill_count_spin.setFixedHeight(40)
        self.fill_count_spin.setFixedWidth(50)
        self.fill_count_spin.setToolTip(T("Fill to N slots (GLB=2 slots/tab, others=1 slot/tab)"))
        self.fill_count_spin.setStyleSheet("""
            QSpinBox {
                background: #181825; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 6px; font-size: 14px; font-weight: bold;
            }
        """)

        self.sim_btn.clicked.connect(self._on_simulate)
        btn_layout.addWidget(self.sim_btn)

        self.tune_btn = QPushButton(T("Tune"))
        self.tune_btn.setFixedHeight(40)
        self.tune_btn.setStyleSheet("""
            QPushButton {
                background-color: #cba6f7; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none;
            }
            QPushButton:hover { background-color: #b4befe; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.tune_btn.setToolTip(T("Auto-tune: use ?/?N in settings or <?N>/<?gN>/<prefix?gN> in expression. ?N = sequential; ? = cartesian. <?g> = glossary; <sector?g> = glossary with default."))
        self.tune_btn.clicked.connect(self._on_tune)
        btn_layout.addWidget(self.tune_btn)

        # Edit get_tune_score button (below Tune button)
        self._edit_score_btn = QPushButton(T("Edit Score"))
        self._edit_score_btn.setFixedHeight(22)
        self._edit_score_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._edit_score_btn.setStyleSheet("""
            QPushButton {
                background-color: #313244; color: #a6adc8; font-size: 10px;
                font-weight: bold; border-radius: 4px; border: 1px solid #45475a;
                padding: 0px 4px;
            }
            QPushButton:hover { background-color: #45475a; color: #cba6f7; }
        """)
        self._edit_score_btn.setToolTip(T("Customize get_tune_score function"))
        self._edit_score_btn.clicked.connect(self._edit_tune_score)
        btn_layout.addWidget(self._edit_score_btn)
        self._custom_tune_score_code = None  # None = use default

        # Tune Expand toggle (below Tune button)
        self.tune_expand_check = QCheckBox(T("Expand"))
        self.tune_expand_check.setFixedHeight(20)
        self.tune_expand_check.setStyleSheet("""
            QCheckBox {
                background-color: transparent; color: #a6adc8; font-size: 10px;
                font-weight: bold; border: none; padding: 0px; spacing: 2px;
            }
            QCheckBox::indicator { width: 12px; height: 12px; }
            QCheckBox::indicator:checked { background-color: #cba6f7; border: 1px solid #cba6f7; }
            QCheckBox::indicator:unchecked { background-color: #313244; border: 1px solid #45475a; }
        """)
        self.tune_expand_check.setToolTip(T("When checked, tune will open all variants in separate tabs, keep them open, and move the best result to the leftmost tab"))
        self.tune_expand_check.setChecked(True)  # Default checked
        btn_layout.addWidget(self.tune_expand_check)

        btn_layout.addWidget(self.fill8_btn)
        btn_layout.addWidget(self.fill_count_spin)

        # Auto Fill toggle
        self.auto_fill_check = QCheckBox(T("Auto Fill"))
        self.auto_fill_check.setFixedHeight(40)
        self.auto_fill_check.setStyleSheet("""
            QCheckBox {
                background-color: #313244; color: #a6e3a1; font-size: 12px;
                font-weight: bold; border-radius: 6px; border: 1px solid #45475a;
                padding: 0 10px; spacing: 6px;
            }
            QCheckBox:hover { background-color: #45475a; }
        """)
        self.auto_fill_check.setToolTip(T("Auto-fill running simulations to 8 when progress < 50%"))
        btn_layout.addWidget(self.auto_fill_check)

        self.cancel_btn = QPushButton(T("Cancel"))
        self.cancel_btn.setFixedHeight(40)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.cancel_btn)

        self.clone_btn = QPushButton("+")
        self.clone_btn.setFixedHeight(40)
        self.clone_btn.setFixedWidth(40)
        self.clone_btn.setToolTip(T("Duplicate this tab with current expression & settings"))
        self.clone_btn.setStyleSheet("""
            QPushButton {
                background-color: #a6e3a1; color: #1e1e2e; font-size: 18px;
                font-weight: bold; border-radius: 6px; border: none;
            }
            QPushButton:hover { background-color: #94e2d5; }
        """)
        self.clone_btn.clicked.connect(self.clone_requested.emit)
        btn_layout.addWidget(self.clone_btn)

        self._left_layout.addLayout(btn_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self._left_layout.addWidget(self.progress_bar)

        # Copy All / Import All buttons row
        all_btn_row = QHBoxLayout()
        all_btn_row.addStretch()
        self._copy_all_btn = QPushButton(T("Copy All"))
        self._copy_all_btn.setFixedHeight(24)
        self._copy_all_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #cdd6f4; font-size: 10pt; font-weight: bold;
                border: 1px solid #585b70; border-radius: 4px; padding: 2px 14px;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self._copy_all_btn.setToolTip(T("Copy expression + settings to clipboard"))
        self._copy_all_btn.clicked.connect(self._copy_all)
        all_btn_row.addWidget(self._copy_all_btn)

        self._import_all_btn = QPushButton(T("Import All"))
        self._import_all_btn.setFixedHeight(24)
        self._import_all_btn.setStyleSheet("""
            QPushButton {
                background: #45475a; color: #cdd6f4; font-size: 10pt; font-weight: bold;
                border: 1px solid #585b70; border-radius: 4px; padding: 2px 14px;
            }
            QPushButton:hover { background: #585b70; }
        """)
        self._import_all_btn.setToolTip(T("Import expression + settings from clipboard"))
        self._import_all_btn.clicked.connect(self._import_all)
        all_btn_row.addWidget(self._import_all_btn)
        all_btn_row.addStretch()
        self._left_layout.addLayout(all_btn_row)

        self._sim_id_row = QHBoxLayout()
        self._sim_id_label = QLineEdit("")
        self._sim_id_label.setReadOnly(True)
        self._sim_id_label.setVisible(False)
        self._sim_id_label.setFixedHeight(20)
        self._sim_id_label.setStyleSheet("""
            QLineEdit {
                background: #181825; color: #89b4fa; border: 1px solid #45475a;
                border-radius: 3px; padding: 1px 4px; font-size: 9pt;
                selection-background-color: #45475a; selection-color: #89b4fa;
            }
        """)
        self._sim_id_row.addWidget(self._sim_id_label)

        self._sim_url_btn = QPushButton("🔗")
        self._sim_url_btn.setFixedSize(20, 20)
        self._sim_url_btn.setVisible(False)
        self._sim_url_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cdd6f4; font-size: 10pt;
                border: 1px solid #45475a; border-radius: 3px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self._sim_url_btn.setToolTip(T("Open simulation URL"))
        self._sim_url_btn.clicked.connect(self._open_sim_url)
        self._sim_id_row.addWidget(self._sim_url_btn)
        self._sim_id_row.addStretch()

        self._left_layout.addLayout(self._sim_id_row)

        # Status / error area: QTextEdit for copyability + Copy Error button
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        self.status_label = QTextEdit("")
        self.status_label.setReadOnly(True)
        self.status_label.setMaximumHeight(60)
        self.status_label.setLineWrapMode(QTextEdit.WidgetWidth)
        self.status_label.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.status_label.setStyleSheet("""
            QTextEdit {
                background: #1e1e2e; color: #cdd6f4; border: none;
                font-size: 11pt; padding: 2px 4px;
            }
        """)
        self._copy_error_btn = QPushButton("📋")
        self._copy_error_btn.setFixedSize(26, 26)
        self._copy_error_btn.setToolTip(T("Copy error message"))
        self._copy_error_btn.setVisible(False)
        self._copy_error_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #f38ba8; border: 1px solid #45475a;
                border-radius: 4px; font-size: 11pt;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self._copy_error_btn.clicked.connect(self._copy_error_to_clipboard)
        status_row.addWidget(self.status_label)
        status_row.addWidget(self._copy_error_btn)
        self._left_layout.addLayout(status_row)

        self._left_stretch = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self._left_layout.addItem(self._left_stretch)

        self._left_layout.addStretch()

        # ── Right Panel: Results (scrollable) ──
        self.right_scroll = QScrollArea()
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setStyleSheet("""
            QScrollArea { background: #1e1e2e; border: none; }
        """)
        right_content = QWidget()
        right_content.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._right_layout = QVBoxLayout(right_content)
        self._right_layout.setSpacing(6)
        self._right_layout.setAlignment(Qt.AlignTop)

        # ── Classification Bar ──
        self._classif_toggle_btn = QPushButton(T("Classifications"))
        self._classif_toggle_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 11pt; font-weight: bold;
                border: 1px solid #45475a; border-radius: 6px; padding: 4px 12px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self._classif_toggle_btn.setCheckable(True)
        self._classif_toggle_btn.setChecked(True)

        # Toggle + →list in one row
        classif_top_row = QHBoxLayout()
        classif_top_row.addStretch()
        classif_top_row.addWidget(self._classif_toggle_btn)
        classif_top_row.addStretch()
        # Pin Key Metrics button (before Open in browser)
        self._pin_metrics_btn = QPushButton("📌")
        self._pin_metrics_btn.setFixedSize(32, 32)
        self._pin_metrics_btn.setToolTip(T("Pin Key Metrics to desktop"))
        self._pin_metrics_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #f9e2af; font-size: 14pt;
                border: 1px solid #45475a; border-radius: 6px;
            }
            QPushButton:hover { background: #45475a; border: 1px solid #f9e2af; }
        """)
        self._pin_metrics_btn.clicked.connect(self._pin_metrics_screenshot)
        self._pin_metrics_btn.setVisible(False)
        classif_top_row.addWidget(self._pin_metrics_btn)

        self._open_url_btn = QPushButton("🔗")
        self._open_url_btn.setFixedHeight(24)
        self._open_url_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #a6adc8; font-size: 10pt;
                border: 1px solid #45475a; border-radius: 4px; padding: 2px 8px;
            }
            QPushButton:hover { background: #45475a; color: #cdd6f4; }
        """)
        self._open_url_btn.setToolTip(T("Open in browser"))
        self._open_url_btn.clicked.connect(self._open_alpha_url)
        classif_top_row.addWidget(self._open_url_btn)

        self._classif_list_btn = QPushButton("→list")
        self._classif_list_btn.setFixedHeight(24)
        self._classif_list_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #a6adc8; font-size: 10pt;
                border: 1px solid #45475a; border-radius: 4px; padding: 2px 8px;
            }
            QPushButton:hover { background: #45475a; color: #cdd6f4; }
        """)
        self._classif_list_btn.clicked.connect(self._open_list_dialog)
        classif_top_row.addWidget(self._classif_list_btn)

        self._classif_toggle_btn.setVisible(False)
        self._classif_list_btn.setVisible(False)
        self._open_url_btn.setVisible(False)
        self._right_layout.addLayout(classif_top_row)

        self._classif_group = QWidget()
        classif_vbox = QVBoxLayout(self._classif_group)
        classif_vbox.setContentsMargins(0, 0, 0, 0)
        classif_vbox.setSpacing(4)
        # Tags area: FlowLayout for auto-wrapping
        self._classif_flow = FlowLayout()
        self._classif_flow.setContentsMargins(0, 0, 0, 0)
        self._classif_flow.setSpacing(6)
        classif_vbox.addLayout(self._classif_flow)
        # Pyramid themes row
        self._pyramid_flow = FlowLayout()
        self._pyramid_flow.setContentsMargins(0, 0, 0, 0)
        self._pyramid_flow.setSpacing(6)
        classif_vbox.addLayout(self._pyramid_flow)
        self._classif_toggle_btn.toggled.connect(self._classif_group.setVisible)
        self._classif_group.setVisible(False)
        self._right_layout.addWidget(self._classif_group)

        # spacing between classifications and metrics
        self._right_layout.addSpacing(16)

        # ── Key Metrics Bar ──
        self._metrics_grid = QWidget()
        grid = QGridLayout(self._metrics_grid)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(45)
        grid.setVerticalSpacing(16)

        self._key_labels = {}
        self._rn_labels = {}
        self._ic_labels = {}
        self._section_corr_btns = {}  # {"RN": QPushButton, "IC": QPushButton, "AMER": QPushButton, ...}

        metric_names = ["Sharpe", "Turnover", "Fitness", "Returns", "Drawdown", "Margin"]
        metric_display = [T("Sharpe"), T("Turnover"), T("Fitness"), T("Returns"), T("Drawdown"), T("Margin")]

        rows = [
            (self._key_labels, "#89b4fa", None, None),
            (self._rn_labels, "#60CA68", T("Risk Neutralized"), "RN"),
            (self._ic_labels, "#ACD147", T("Investability Constrained"), "IC"),
        ]

        self._rn_title = None
        self._rn_row_start = -1
        self._ic_title = None
        self._ic_row_start = -1
        grid_row = 0

        for row_labels, val_color, title_text, corr_key in rows:
            if title_text:
                title = QLabel(title_text)
                title.setStyleSheet(f"color: {val_color}; font-size: 12pt; font-weight: bold; background: transparent; border: none;")
                title.setAlignment(Qt.AlignCenter)
                title.setVisible(False)
                grid.addWidget(title, grid_row, 0, 1, 6)
                if corr_key == "RN":
                    self._rn_title = title
                else:
                    self._ic_title = title
                grid_row += 1

            row_start = grid_row
            if corr_key == "RN":
                self._rn_row_start = row_start
            elif corr_key == "IC":
                self._ic_row_start = row_start

            for col, (name, display) in enumerate(zip(metric_names, metric_display)):
                card = QWidget()
                card.setStyleSheet("background: #313244; border-radius: 6px;")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(8, 4, 8, 4)
                card_layout.setSpacing(0)
                name_lbl = QLabel(display)
                name_lbl.setStyleSheet("color: #a6adc8; font-size: 16pt; font-weight: bold; background: transparent; border: none;")
                name_lbl.setAlignment(Qt.AlignCenter)
                val_lbl = QLabel("--")
                val_lbl.setStyleSheet(f"color: {val_color}; font-size: 20pt; font-weight: bold; background: transparent; border: none;")
                val_lbl.setAlignment(Qt.AlignCenter)
                card_layout.addWidget(name_lbl)
                card_layout.addWidget(val_lbl)
                grid.addWidget(card, grid_row, col)
                row_labels[name] = val_lbl
                grid.setColumnStretch(col, 1)

            # Corr button for RN/IC rows
            if corr_key:
                corr_btn = QPushButton(T("Corr"))
                corr_btn.setFixedSize(40, 40)
                corr_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #313244; color: {val_color}; font-size: 10pt; font-weight: bold;
                        border: 1px solid #45475a; border-radius: 6px;
                    }}
                    QPushButton:hover {{ background: #45475a; border: 1px solid {val_color}; }}
                """)
                corr_btn.setToolTip(T("Show Self Corr & PPC"))
                corr_btn.setVisible(False)
                grid.addWidget(corr_btn, grid_row, 6)
                self._section_corr_btns[corr_key] = corr_btn

            grid_row += 1

        # ── GLB Sub-Region rows (AMER / APAC / EMEA) ──
        self._sub_region_labels = {}   # {"AMER": {metric: QLabel, ...}, ...}
        self._sub_region_titles = {}   # {"AMER": QLabel, ...}
        self._sub_region_row_start = {}  # {"AMER": grid_row, ...}
        sub_region_info = [
            ("AMER", "#7972D8"),
            ("APAC", "#D68D83"),
            ("EMEA", "#CA76C3"),
        ]
        for sr_name, sr_color in sub_region_info:
            # Title row
            sr_title = QLabel(sr_name)
            sr_title.setStyleSheet(f"color: {sr_color}; font-size: 12pt; font-weight: bold; background: transparent; border: none;")
            sr_title.setAlignment(Qt.AlignCenter)
            sr_title.setVisible(False)
            grid.addWidget(sr_title, grid_row, 0, 1, 6)
            self._sub_region_titles[sr_name] = sr_title
            grid_row += 1

            # Metric cards row
            self._sub_region_row_start[sr_name] = grid_row
            self._sub_region_labels[sr_name] = {}
            for col, name in enumerate(metric_names):
                card = QWidget()
                card.setStyleSheet("background: #313244; border-radius: 6px;")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(8, 4, 8, 4)
                card_layout.setSpacing(0)
                name_lbl = QLabel(name)
                name_lbl.setStyleSheet("color: #a6adc8; font-size: 16pt; font-weight: bold; background: transparent; border: none;")
                name_lbl.setAlignment(Qt.AlignCenter)
                val_lbl = QLabel("--")
                val_lbl.setStyleSheet(f"color: {sr_color}; font-size: 20pt; font-weight: bold; background: transparent; border: none;")
                val_lbl.setAlignment(Qt.AlignCenter)
                card_layout.addWidget(name_lbl)
                card_layout.addWidget(val_lbl)
                grid.addWidget(card, grid_row, col)
                self._sub_region_labels[sr_name][name] = val_lbl

            # Corr button for sub-region rows
            corr_btn = QPushButton(T("Corr"))
            corr_btn.setFixedSize(40, 40)
            corr_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #313244; color: {sr_color}; font-size: 10pt; font-weight: bold;
                    border: 1px solid #45475a; border-radius: 6px;
                }}
                QPushButton:hover {{ background: #45475a; border: 1px solid {sr_color}; }}
            """)
            corr_btn.setToolTip(f"{T('Show Self Corr & PPC for ')}{sr_name}")
            corr_btn.setVisible(False)
            grid.addWidget(corr_btn, grid_row, 6)
            self._section_corr_btns[sr_name] = corr_btn

            grid_row += 1

        # Hide RN/IC/sub-region rows initially
        if self._rn_title: self._rn_title.setVisible(False)
        if self._ic_title: self._ic_title.setVisible(False)
        self._set_grid_row_visible(grid, self._rn_row_start, False)
        self._set_grid_row_visible(grid, self._ic_row_start, False)
        for sr_name in self._sub_region_row_start:
            self._sub_region_titles[sr_name].setVisible(False)
            self._set_grid_row_visible(grid, self._sub_region_row_start[sr_name], False)

        # Connect Corr buttons
        for corr_key, btn in self._section_corr_btns.items():
            btn.clicked.connect(lambda checked, k=corr_key: self._show_section_corr(k))

        metrics_bar_row = QHBoxLayout()
        metrics_bar_row.setSpacing(6)
        metrics_bar_row.addWidget(self._metrics_grid, 0, Qt.AlignHCenter)
        metrics_bar_row.addStretch()
        self._right_layout.addLayout(metrics_bar_row)

        self._right_layout.addSpacing(20)

        metrics_toggle_btn = QPushButton(T("Performance Metrics"))
        metrics_toggle_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 11pt; font-weight: bold;
                border: 1px solid #45475a; border-radius: 6px; padding: 4px 12px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        metrics_toggle_btn.setCheckable(True)
        metrics_toggle_btn.setChecked(False)

        metrics_group = QGroupBox(T("Performance Metrics"))
        metrics_layout = QVBoxLayout(metrics_group)
        self.metrics_table = QTableWidget(0, 2)
        self.metrics_table.setHorizontalHeaderLabels([T("Metric"), T("Value")])
        self.metrics_table.horizontalHeader().setStretchLastSection(True)
        self.metrics_table.setColumnWidth(0, 180)
        self.metrics_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.metrics_table.setAlternatingRowColors(True)
        self.metrics_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.metrics_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.metrics_table.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        metrics_layout.addWidget(self.metrics_table)
        metrics_group.setVisible(False)

        metrics_toggle_btn.toggled.connect(metrics_group.setVisible)
        self._right_layout.addWidget(metrics_toggle_btn, 0, Qt.AlignHCenter)
        self._right_layout.addWidget(metrics_group)

        yearly_group = QGroupBox(T("Yearly Statistics"))
        yearly_layout = QVBoxLayout(yearly_group)
        self.yearly_table = QTableWidget(0, 2)
        self.yearly_table.setHorizontalHeaderLabels([T("Year"), T("Stats")])
        self.yearly_table.horizontalHeader().setStretchLastSection(True)
        self.yearly_table.setColumnWidth(0, 80)
        self.yearly_table.verticalHeader().setVisible(False)
        self.yearly_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.yearly_table.setAlternatingRowColors(True)
        self.yearly_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.yearly_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.yearly_table.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        yearly_layout.addWidget(self.yearly_table)
        self._right_layout.addWidget(yearly_group)

        # ── Correlation section ──
        self.corr_widget = QWidget()
        corr_main_layout = QVBoxLayout(self.corr_widget)
        corr_main_layout.setSpacing(10)
        corr_main_layout.setContentsMargins(0, 0, 0, 0)

        # All Corr button row
        all_corr_row = QHBoxLayout()
        all_corr_row.setSpacing(15)
        self.all_corr_btn = QPushButton(T("All Corr"))
        self.all_corr_btn.setFixedSize(120, 40)
        self.all_corr_btn.setStyleSheet("""
            QPushButton {
                background-color: #f5c2e7; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none; padding: 2px 10px;
            }
            QPushButton:hover { background-color: #eba0d0; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.all_corr_btn.clicked.connect(self._fetch_all_correlations)
        self.all_corr_loading = QLabel("")
        self.all_corr_loading.setStyleSheet("color: #f5c2e7; font-size: 16pt;")
        all_corr_row.addWidget(self.all_corr_btn)
        all_corr_row.addWidget(self.all_corr_loading)
        all_corr_row.addStretch()
        corr_main_layout.addLayout(all_corr_row)

        # Self Corr row
        self_corr_row = QHBoxLayout()
        self_corr_row.setSpacing(15)
        self.self_corr_btn = QPushButton(T("Local SC") if use_local_corr else T("Self Corr"))
        self.self_corr_btn.setFixedSize(120, 40)
        self.self_corr_btn.setStyleSheet("""
            QPushButton {
                background-color: #cba6f7; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none; padding: 2px 10px;
            }
            QPushButton:hover { background-color: #b4befe; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.self_corr_btn.clicked.connect(lambda: self._fetch_correlation("self"))
        self.self_corr_label = QLabel(T("max: --  min: --"))
        self.self_corr_label.setStyleSheet("color: #cba6f7; font-size: 20pt; font-weight: bold;")
        self.self_corr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.self_corr_loading = QLabel("")
        self.self_corr_loading.setStyleSheet("color: #cba6f7; font-size: 16pt;")
        self_corr_row.addWidget(self.self_corr_btn)
        self_corr_row.addWidget(self.self_corr_label)
        self_corr_row.addWidget(self.self_corr_loading)
        self.self_corr_expand_btn = QPushButton("▸")
        self.self_corr_expand_btn.setCheckable(True)
        self.self_corr_expand_btn.setFixedSize(24, 24)
        self.self_corr_expand_btn.setEnabled(False)
        self.self_corr_expand_btn.setToolTip(T("Show top correlated alphas"))
        self.self_corr_expand_btn.setStyleSheet("""
            QPushButton {
                background-color: #313244; color: #cba6f7; font-size: 12pt;
                font-weight: bold; border: 1px solid #45475a; border-radius: 4px;
            }
            QPushButton:hover { background-color: #45475a; }
            QPushButton:disabled { color: #585b70; }
        """)
        self.self_corr_expand_btn.toggled.connect(
            lambda c: self._toggle_corr_details("self", c))
        self_corr_row.addWidget(self.self_corr_expand_btn)
        self_corr_row.addStretch()
        corr_main_layout.addLayout(self_corr_row)

        # Expandable panel listing the (up to) 5 most/least-correlated alphas
        self.self_corr_details = QTableWidget(0, 7)
        self.self_corr_details.setHorizontalHeaderLabels(
            ["ID", "Corr", "Sharpe", "Fitness", "Turnover", "Returns", "Margin"])
        self.self_corr_details.horizontalHeader().setStretchLastSection(True)
        self.self_corr_details.setColumnWidth(0, 100)  # ID
        self.self_corr_details.setColumnWidth(1, 80)   # Corr
        self.self_corr_details.setColumnWidth(2, 70)   # Sharpe
        self.self_corr_details.setColumnWidth(3, 70)   # Fitness
        self.self_corr_details.setColumnWidth(4, 85)   # Turnover
        self.self_corr_details.setColumnWidth(5, 80)   # Returns
        self.self_corr_details.verticalHeader().setVisible(False)
        self.self_corr_details.setEditTriggers(QTableWidget.NoEditTriggers)
        self.self_corr_details.setAlternatingRowColors(True)
        self.self_corr_details.setSelectionBehavior(QTableWidget.SelectRows)
        self.self_corr_details.cellClicked.connect(
            lambda row, col: self._on_corr_id_clicked(self.self_corr_details, row, col))
        self.self_corr_details.setVisible(False)
        corr_main_layout.addWidget(self.self_corr_details)

        # PPC row
        ppc_row = QHBoxLayout()
        ppc_row.setSpacing(15)
        self.ppc_btn = QPushButton(T("Local PPC") if use_local_corr else T("PPC"))
        self.ppc_btn.setFixedSize(120, 40)
        self.ppc_btn.setStyleSheet("""
            QPushButton {
                background-color: #f9e2af; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none; padding: 2px 10px;
            }
            QPushButton:hover { background-color: #fab387; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.ppc_btn.clicked.connect(lambda: self._fetch_correlation("ppc"))
        self.ppc_label = QLabel(T("max: --  min: --"))
        self.ppc_label.setStyleSheet("color: #f9e2af; font-size: 20pt; font-weight: bold;")
        self.ppc_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ppc_loading = QLabel("")
        self.ppc_loading.setStyleSheet("color: #f9e2af; font-size: 16pt;")
        ppc_row.addWidget(self.ppc_btn)
        ppc_row.addWidget(self.ppc_label)
        ppc_row.addWidget(self.ppc_loading)
        self.ppc_expand_btn = QPushButton("▸")
        self.ppc_expand_btn.setCheckable(True)
        self.ppc_expand_btn.setFixedSize(24, 24)
        self.ppc_expand_btn.setEnabled(False)
        self.ppc_expand_btn.setToolTip(T("Show top correlated alphas"))
        self.ppc_expand_btn.setStyleSheet("""
            QPushButton {
                background-color: #313244; color: #f9e2af; font-size: 12pt;
                font-weight: bold; border: 1px solid #45475a; border-radius: 4px;
            }
            QPushButton:hover { background-color: #45475a; }
            QPushButton:disabled { color: #585b70; }
        """)
        self.ppc_expand_btn.toggled.connect(
            lambda c: self._toggle_corr_details("ppc", c))
        ppc_row.addWidget(self.ppc_expand_btn)
        ppc_row.addStretch()
        corr_main_layout.addLayout(ppc_row)

        # Expandable panel listing the (up to) 5 most/least-correlated alphas
        self.ppc_details = QTableWidget(0, 7)
        self.ppc_details.setHorizontalHeaderLabels(
            ["ID", "Corr", "Sharpe", "Fitness", "Turnover", "Returns", "Margin"])
        self.ppc_details.horizontalHeader().setStretchLastSection(True)
        self.ppc_details.setColumnWidth(0, 100)  # ID
        self.ppc_details.setColumnWidth(1, 80)   # Corr
        self.ppc_details.setColumnWidth(2, 70)   # Sharpe
        self.ppc_details.setColumnWidth(3, 70)   # Fitness
        self.ppc_details.setColumnWidth(4, 85)   # Turnover
        self.ppc_details.setColumnWidth(5, 80)   # Returns
        self.ppc_details.verticalHeader().setVisible(False)
        self.ppc_details.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ppc_details.setAlternatingRowColors(True)
        self.ppc_details.setSelectionBehavior(QTableWidget.SelectRows)
        self.ppc_details.cellClicked.connect(
            lambda row, col: self._on_corr_id_clicked(self.ppc_details, row, col))
        self.ppc_details.setVisible(False)
        corr_main_layout.addWidget(self.ppc_details)

        # Prod Corr row
        prod_corr_row = QHBoxLayout()
        prod_corr_row.setSpacing(15)
        self.prod_corr_btn = QPushButton(T("Prod Corr"))
        self.prod_corr_btn.setFixedSize(120, 40)
        self.prod_corr_btn.setStyleSheet("""
            QPushButton {
                background-color: #94e2d5; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none; padding: 2px 10px;
            }
            QPushButton:hover { background-color: #89dceb; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.prod_corr_btn.clicked.connect(lambda: self._fetch_correlation("prod"))
        self.prod_corr_label = QLabel(T("max: --  min: --"))
        self.prod_corr_label.setStyleSheet("color: #94e2d5; font-size: 20pt; font-weight: bold;")
        self.prod_corr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.prod_corr_loading = QLabel("")
        self.prod_corr_loading.setStyleSheet("color: #94e2d5; font-size: 16pt;")
        prod_corr_row.addWidget(self.prod_corr_btn)
        prod_corr_row.addWidget(self.prod_corr_label)
        prod_corr_row.addWidget(self.prod_corr_loading)
        prod_corr_row.addStretch()
        corr_main_layout.addLayout(prod_corr_row)

        # Cached PC row
        cached_pc_row = QHBoxLayout()
        cached_pc_row.setSpacing(15)
        self.cached_pc_btn = QPushButton(T("Cached PC"))
        self.cached_pc_btn.setFixedSize(120, 40)
        self.cached_pc_btn.setStyleSheet("""
            QPushButton {
                background-color: #a6e3a1; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none; padding: 2px 10px;
            }
            QPushButton:hover { background-color: #94e2d5; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.cached_pc_btn.clicked.connect(lambda: self._fetch_correlation("prod"))
        self.cached_pc_label = QLabel(T("max: --  min: --"))
        self.cached_pc_label.setStyleSheet("color: #a6e3a1; font-size: 20pt; font-weight: bold;")
        self.cached_pc_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cached_pc_row.addWidget(self.cached_pc_btn)
        cached_pc_row.addWidget(self.cached_pc_label)
        cached_pc_row.addStretch()
        corr_main_layout.addLayout(cached_pc_row)

        # PC Range row (estimate via PnL correlation transitivity)
        pc_range_row = QHBoxLayout()
        pc_range_row.setSpacing(15)
        self.pc_range_btn = QPushButton(T("PC Range"))
        self.pc_range_btn.setFixedSize(120, 40)
        self.pc_range_btn.setStyleSheet("""
            QPushButton {
                background-color: #cba6f7; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none; padding: 2px 10px;
            }
            QPushButton:hover { background-color: #b4befe; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.pc_range_btn.clicked.connect(self._estimate_pc_range)
        self.pc_range_label = QLabel(T("min: --"))
        self.pc_range_label.setStyleSheet("color: #cba6f7; font-size: 20pt; font-weight: bold;")
        self.pc_range_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pc_range_loading = QLabel("")
        self.pc_range_loading.setStyleSheet("color: #cba6f7; font-size: 16pt;")
        pc_range_row.addWidget(self.pc_range_btn)
        pc_range_row.addWidget(self.pc_range_label)
        pc_range_row.addWidget(self.pc_range_loading)
        pc_range_row.addStretch()
        corr_main_layout.addLayout(pc_range_row)

        # Inter Corr row
        inter_corr_row = QHBoxLayout()
        inter_corr_row.setSpacing(8)
        self.inter_corr_btn = QPushButton(T("Inter Corr"))
        self.inter_corr_btn.setFixedSize(120, 40)
        self.inter_corr_btn.setStyleSheet("""
            QPushButton {
                background-color: #89b4fa; color: #1e1e2e; font-size: 14px;
                font-weight: bold; border-radius: 6px; border: none; padding: 2px 10px;
            }
            QPushButton:hover { background-color: #74c7ec; }
            QPushButton:disabled { background-color: #45475a; color: #6c7086; }
        """)
        self.inter_corr_btn.clicked.connect(self._fetch_inter_correlation)
        self.inter_corr_input = QLineEdit()
        self.inter_corr_input.setPlaceholderText(T("Alpha ID"))
        self.inter_corr_input.setFixedHeight(32)
        self.inter_corr_input.setFixedWidth(120)
        self.inter_corr_input.setStyleSheet("""
            QLineEdit {
                background: #181825; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 4px; padding: 2px 8px; font-size: 11pt; font-weight: bold;
            }
            QLineEdit:focus { border: 1px solid #89b4fa; }
        """)
        self.inter_corr_input.returnPressed.connect(self._fetch_inter_correlation)
        self.inter_corr_label = QLabel("--")
        self.inter_corr_label.setStyleSheet("color: #89b4fa; font-size: 20pt; font-weight: bold;")
        self.inter_corr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.inter_corr_loading = QLabel("")
        self.inter_corr_loading.setStyleSheet("color: #89b4fa; font-size: 16pt;")
        inter_corr_row.addWidget(self.inter_corr_btn)
        inter_corr_row.addWidget(self.inter_corr_input)
        inter_corr_row.addWidget(self.inter_corr_label)
        inter_corr_row.addWidget(self.inter_corr_loading)
        inter_corr_row.addStretch()
        corr_main_layout.addLayout(inter_corr_row)

        self._right_layout.addWidget(self.corr_widget)

        # PnL Curve with copy button (all in one widget for easy move between panels)
        self.pnl_content_widget = QWidget()
        pnl_main_layout = QVBoxLayout(self.pnl_content_widget)
        pnl_main_layout.setContentsMargins(0, 0, 0, 0)
        pnl_main_layout.setSpacing(0)

        self.pnl_canvas = PnlCanvas()
        self.pnl_canvas.setMinimumHeight(300)

        # Add Copy PnL button after the navigation toolbar (after Save the figure)
        self.pnl_copy_btn = QPushButton(T("Copy PnL"))
        self.pnl_copy_btn.setFixedSize(80, 24)
        self.pnl_copy_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #a6e3a1; font-size: 9pt; font-weight: bold;
                border: 1px solid #45475a; border-radius: 4px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.pnl_copy_btn.setToolTip(T("Copy PnL data to clipboard"))
        self.pnl_copy_btn.clicked.connect(self._copy_pnl_to_clipboard)
        # Insert into the toolbar's layout after the last action
        toolbar = self.pnl_canvas._nav_toolbar
        toolbar.addWidget(self.pnl_copy_btn)

        pnl_main_layout.addWidget(self.pnl_canvas._nav_toolbar)
        pnl_main_layout.addWidget(self.pnl_canvas)

        self._right_layout.addWidget(self.pnl_content_widget)

        # ── Submission Checks section ──
        # ── PASS toggle ──
        self.checks_pass_btn = QPushButton(T("PASS"))
        self.checks_pass_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #4CAF50; font-size: 14pt; font-weight: bold;
                border: 1px solid #4CAF50; border-radius: 6px; padding: 6px 20px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.checks_pass_btn.setCheckable(True)
        self.checks_pass_btn.setChecked(False)

        self.checks_pass_group = QWidget()
        pass_layout = QVBoxLayout(self.checks_pass_group)
        pass_layout.setContentsMargins(0, 0, 0, 0)
        pass_layout.setSpacing(0)
        self.checks_pass_list = QTextEdit()
        self.checks_pass_list.setReadOnly(True)
        self.checks_pass_list.setLineWrapMode(QTextEdit.WidgetWidth)
        self.checks_pass_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.checks_pass_list.setStyleSheet("""
            QTextEdit { background: #1e1e2e; color: #a6e3a1; font-size: 20px;
                     border: 1px solid #4CAF50; border-radius: 6px; padding: 2px 6px; }
        """)
        self.checks_pass_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.checks_pass_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        pass_layout.addWidget(self.checks_pass_list)
        self.checks_pass_group.setVisible(False)
        self.checks_pass_btn.toggled.connect(self.checks_pass_group.setVisible)

        # ── WARNING toggle ──
        self.checks_warn_btn = QPushButton(T("WARNING"))
        self.checks_warn_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #f9a825; font-size: 14pt; font-weight: bold;
                border: 1px solid #f9a825; border-radius: 6px; padding: 6px 20px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.checks_warn_btn.setCheckable(True)
        self.checks_warn_btn.setChecked(False)

        self.checks_warn_group = QWidget()
        warn_layout = QVBoxLayout(self.checks_warn_group)
        warn_layout.setContentsMargins(0, 0, 0, 0)
        warn_layout.setSpacing(0)
        self.checks_warn_list = QTextEdit()
        self.checks_warn_list.setReadOnly(True)
        self.checks_warn_list.setLineWrapMode(QTextEdit.WidgetWidth)
        self.checks_warn_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.checks_warn_list.setStyleSheet("""
            QTextEdit { background: #1e1e2e; color: #f9e2af; font-size: 20px;
                     border: 1px solid #f9a825; border-radius: 6px; padding: 2px 6px; }
        """)
        self.checks_warn_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.checks_warn_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        warn_layout.addWidget(self.checks_warn_list)
        self.checks_warn_group.setVisible(False)
        self.checks_warn_btn.toggled.connect(self.checks_warn_group.setVisible)

        # ── FAIL toggle ──
        self.checks_fail_btn = QPushButton(T("FAIL"))
        self.checks_fail_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #f38ba8; font-size: 14pt; font-weight: bold;
                border: 1px solid #f38ba8; border-radius: 6px; padding: 6px 20px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.checks_fail_btn.setCheckable(True)
        self.checks_fail_btn.setChecked(False)

        self.checks_fail_group = QWidget()
        fail_layout = QVBoxLayout(self.checks_fail_group)
        fail_layout.setContentsMargins(0, 0, 0, 0)
        fail_layout.setSpacing(0)
        self.checks_fail_list = QTextEdit()
        self.checks_fail_list.setReadOnly(True)
        self.checks_fail_list.setLineWrapMode(QTextEdit.WidgetWidth)
        self.checks_fail_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.checks_fail_list.setStyleSheet("""
            QTextEdit { background: #1e1e2e; color: #f38ba8; font-size: 20px;
                     border: 1px solid #f38ba8; border-radius: 6px; padding: 2px 6px; }
        """)
        self.checks_fail_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.checks_fail_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fail_layout.addWidget(self.checks_fail_list)
        self.checks_fail_group.setVisible(False)
        self.checks_fail_btn.toggled.connect(self.checks_fail_group.setVisible)

        # ── PENDING toggle ──
        self.checks_pending_btn = QPushButton(T("PENDING"))
        self.checks_pending_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 14pt; font-weight: bold;
                border: 1px solid #89b4fa; border-radius: 6px; padding: 6px 20px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.checks_pending_btn.setCheckable(True)
        self.checks_pending_btn.setChecked(False)

        self.checks_pending_group = QWidget()
        pending_layout = QVBoxLayout(self.checks_pending_group)
        pending_layout.setContentsMargins(0, 0, 0, 0)
        pending_layout.setSpacing(0)
        self.checks_pending_list = QTextEdit()
        self.checks_pending_list.setReadOnly(True)
        self.checks_pending_list.setLineWrapMode(QTextEdit.WidgetWidth)
        self.checks_pending_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.checks_pending_list.setStyleSheet("""
            QTextEdit { background: #1e1e2e; color: #89b4fa; font-size: 20px;
                     border: 1px solid #89b4fa; border-radius: 6px; padding: 2px 6px; }
        """)
        self.checks_pending_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.checks_pending_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        pending_layout.addWidget(self.checks_pending_list)
        self.checks_pending_group.setVisible(False)
        self.checks_pending_btn.toggled.connect(self.checks_pending_group.setVisible)

        self._right_layout.addWidget(self.checks_pass_btn, 0, Qt.AlignHCenter)
        self._right_layout.addWidget(self.checks_pass_group)
        self._right_layout.addWidget(self.checks_fail_btn, 0, Qt.AlignHCenter)
        self._right_layout.addWidget(self.checks_fail_group)
        self._right_layout.addWidget(self.checks_warn_btn, 0, Qt.AlignHCenter)
        self._right_layout.addWidget(self.checks_warn_group)
        self._right_layout.addWidget(self.checks_pending_btn, 0, Qt.AlignHCenter)
        self._right_layout.addWidget(self.checks_pending_group)

        # ── Properties section ──
        self.props_btn = QPushButton(T("Properties"))
        self.props_btn.setCheckable(True)
        self.props_btn.setChecked(False)
        self.props_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cba6f7; font-size: 14pt; font-weight: bold;
                border: 1px solid #cba6f7; border-radius: 6px; padding: 6px 20px;
            }
            QPushButton:hover { background: #45475a; }
        """)

        self.props_group = QWidget()
        props_layout = QVBoxLayout(self.props_group)
        props_layout.setContentsMargins(0, 0, 0, 0)
        props_layout.setSpacing(4)

        # Name
        props_form = QFormLayout()
        props_form.setSpacing(4)
        props_form.setLabelAlignment(Qt.AlignRight)
        label_style = "color: #cdd6f4; font-size: 12pt;"
        name_label = QLabel(T("Name"))
        name_label.setStyleSheet(label_style)
        tags_label = QLabel(T("Tags"))
        tags_label.setStyleSheet(label_style)
        desc_label = QLabel(T("Desc"))
        desc_label.setStyleSheet(label_style)
        color_label = QLabel(T("Color"))
        color_label.setStyleSheet(label_style)
        self.prop_name = QLineEdit()
        self.prop_name.setStyleSheet("QLineEdit { background: #313244; color: #cdd6f4; font-size: 12pt; border: 1px solid #585b70; border-radius: 4px; padding: 2px 6px; }")
        props_form.addRow(name_label, self.prop_name)

        self.prop_tags = QLineEdit()
        self.prop_tags.setStyleSheet("QLineEdit { background: #313244; color: #cdd6f4; font-size: 12pt; border: 1px solid #585b70; border-radius: 4px; padding: 2px 6px; }")
        self.prop_tags.setPlaceholderText(T("comma separated"))
        props_form.addRow(tags_label, self.prop_tags)

        # Color selector: None, red, yellow, green, blue, purple
        self._color_options = [
            (None,  "#585b70", T("None")),
            ("RED",    "#f38ba8", "Red"),
            ("YELLOW", "#f9e2af", "Yellow"),
            ("GREEN",  "#a6e3a1", "Green"),
            ("BLUE",   "#89b4fa", "Blue"),
            ("PURPLE", "#cba6f7", "Purple"),
        ]
        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        self._color_btns = []
        for color_val, hex_color, tooltip_text in self._color_options:
            btn = QPushButton()
            btn.setFixedSize(24, 24)
            btn.setToolTip(tooltip_text)
            btn.setCheckable(True)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {hex_color}; border: 2px solid #45475a;
                    border-radius: 12px;
                }}
                QPushButton:checked {{ border: 2px solid #cdd6f4; }}
                QPushButton:hover {{ border: 2px solid #cdd6f4; }}
            """)
            btn._color_val = color_val
            btn.clicked.connect(lambda checked, b=btn: self._on_color_btn_clicked(b))
            color_row.addWidget(btn)
            self._color_btns.append(btn)
        # Default: None selected
        self._color_btns[0].setChecked(True)
        self._selected_color = None
        props_form.addRow(color_label, color_row)

        self.prop_desc = QTextEdit()
        self.prop_desc.setMaximumHeight(60)
        self.prop_desc.setStyleSheet("QTextEdit { background: #313244; color: #cdd6f4; font-size: 12pt; border: 1px solid #585b70; border-radius: 4px; padding: 2px 6px; }")
        props_form.addRow(desc_label, self.prop_desc)

        props_layout.addLayout(props_form)

        # AI Write Desc + Update buttons in one row
        props_btn_row = QHBoxLayout()
        props_btn_row.setSpacing(8)

        self.prop_write_desc_btn = QPushButton(T("AI Write Desc"))
        self.prop_write_desc_btn.setStyleSheet("""
            QPushButton {
                background: #89b4fa; color: #1e1e2e; font-size: 12pt; font-weight: bold;
                border: none; border-radius: 6px; padding: 4px 16px;
            }
            QPushButton:hover { background: #74c7ec; }
            QPushButton:disabled { background: #45475a; color: #6c7086; }
        """)
        self.prop_write_desc_btn.setToolTip(T("Auto-generate description using /write_desc"))
        self.prop_write_desc_btn.clicked.connect(self._on_write_desc)

        self.prop_submit_btn = QPushButton(T("Update"))
        self.prop_submit_btn.setStyleSheet("""
            QPushButton {
                background: #cba6f7; color: #1e1e2e; font-size: 12pt; font-weight: bold;
                border: none; border-radius: 6px; padding: 4px 16px;
            }
            QPushButton:hover { background: #b4befe; }
        """)
        self.prop_submit_btn.clicked.connect(self._submit_properties)

        props_btn_row.addWidget(self.prop_write_desc_btn)
        props_btn_row.addWidget(self.prop_submit_btn)
        props_btn_row.addStretch()
        props_layout.addLayout(props_btn_row)

        self.props_group.setVisible(False)
        self.props_btn.toggled.connect(self.props_group.setVisible)

        self._right_layout.addWidget(self.props_btn, 0, Qt.AlignHCenter)
        self._right_layout.addWidget(self.props_group)

        # Submit / Check buttons row
        _submit_row = QHBoxLayout()
        _submit_row.addStretch()

        self.check_btn = QPushButton(T("Check"))
        self.check_btn.setStyleSheet("""
            QPushButton {
                background: #353E4F; color: #cdd6f4; font-size: 14pt; font-weight: bold;
                border: none; border-radius: 6px; padding: 8px 24px;
            }
            QPushButton:hover { background: #45475a; }
            QPushButton:disabled { background: #45475a; color: #6c7086; }
        """)
        self.check_btn.setToolTip(T("Check if alpha can be submitted"))
        self.check_btn.clicked.connect(self._on_check_alpha)
        _submit_row.addWidget(self.check_btn)

        self.submit_btn = QPushButton(T("Submit"))
        self.submit_btn.setStyleSheet("""
            QPushButton {
                background: #47B74A; color: #1e1e2e; font-size: 14pt; font-weight: bold;
                border: none; border-radius: 6px; padding: 8px 24px;
            }
            QPushButton:hover { background: #3da845; }
            QPushButton:disabled { background: #45475a; color: #6c7086; }
        """)
        self.submit_btn.setToolTip(T("Submit alpha for production"))
        self.submit_btn.clicked.connect(self._on_submit_alpha)
        _submit_row.addWidget(self.submit_btn)

        _submit_row.addStretch()
        self._right_layout.addLayout(_submit_row)


        self._right_layout.addStretch()
        self.right_scroll.setWidget(right_content)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.right_scroll)
        self._splitter = splitter
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([550, 550])
        layout.addWidget(splitter)

        # Init region-dependent combos (skip _on_region_changed during init)
        self._region_init_mode = True
        self._on_region_changed(self.region_combo.currentText())

        # Initial mode
        self._update_mode()

        # End init mode
        self._region_init_mode = False

    def _toggle_expr_fullscreen(self):
        if hasattr(self, '_expr_fullscreen_widget') and self._expr_fullscreen_widget is not None:
            w = self._expr_fullscreen_widget
            text = w.findChild(QTextEdit).toPlainText().replace(';\n', ';')
            self.expr_input.setPlainText(text)
            w.close()
            self._expr_fullscreen_widget = None
            return

        fs_widget = QWidget()
        fs_widget.setWindowTitle(T("Alpha Expression - Full Screen"))
        fs_widget.setWindowFlags(Qt.Window)
        fs_widget.setStyleSheet("background: #1e1e2e;")
        fs_layout = QVBoxLayout(fs_widget)
        fs_layout.setContentsMargins(4, 4, 4, 4)

        def _format_expr(text):
            return text.replace(';', ';\n').replace(';\n ', ';\n')

        fs_input = QTextEdit()
        fs_input.setPlainText(_format_expr(self.expr_input.toPlainText()))
        fs_input.setStyleSheet("QTextEdit { font-size: 18pt; background: #1e1e2e; color: #cdd6f4; border: none; }")
        fs_input.setLineWrapMode(QTextEdit.WidgetWidth)
        fs_input.setWordWrapMode(QTextOption.WrapAnywhere)
        fs_input.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        _fs_opt = fs_input.document().defaultTextOption()
        _fs_opt.setWrapMode(QTextOption.WrapAnywhere)
        fs_input.document().setDefaultTextOption(_fs_opt)
        fs_layout.addWidget(fs_input)

        btn_row = QHBoxLayout()
        restore_btn = QPushButton(T("Restore (Esc)"))
        restore_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 11pt; font-weight: bold;
                border: 1px solid #45475a; border-radius: 6px; padding: 4px 16px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        restore_btn.clicked.connect(self._toggle_expr_fullscreen)
        btn_row.addStretch()
        btn_row.addWidget(restore_btn)
        btn_row.addStretch()
        fs_layout.addLayout(btn_row)

        fs_widget.keyPressEvent = lambda e: (
            self._toggle_expr_fullscreen() if e.key() == Qt.Key_Escape else None
        )
        fs_widget.showMaximized()

        self._expr_fullscreen_widget = fs_widget
        self._expr_fs_input = fs_input

        def _unformat_expr(text):
            return text.replace(';\n', ';')

        def _sync_back():
            if hasattr(self, '_expr_fs_input') and self._expr_fs_input is not None:
                self.expr_input.setPlainText(_unformat_expr(self._expr_fs_input.toPlainText()))
        fs_input.textChanged.connect(_sync_back)

    def _open_in_vscode(self):
        """Open expression in VSCode for editing."""
        # Write current expression to temp file
        expr = self.expr_input.toPlainText()
        self._vscode_syncing = True
        with open(self._vscode_temp_file, 'w', encoding='utf-8') as f:
            f.write(expr)
        self._vscode_syncing = False
        # Re-add watch (file may have been removed and recreated by VSCode save)
        if self._vscode_temp_file not in self._vscode_watcher.files():
            self._vscode_watcher.addPath(self._vscode_temp_file)
        # Open in VSCode
        subprocess.Popen(f'code --wait "{self._vscode_temp_file}"', shell=True)

    def _sync_expr_to_tempfile(self):
        """Sync expr_input text to temp file (called on textChanged)."""
        if self._vscode_syncing:
            return
        expr = self.expr_input.toPlainText()
        self._vscode_syncing = True
        try:
            with open(self._vscode_temp_file, 'w', encoding='utf-8') as f:
                f.write(expr)
            # Re-add watch if it was removed
            if self._vscode_temp_file not in self._vscode_watcher.files():
                self._vscode_watcher.addPath(self._vscode_temp_file)
        except Exception:
            pass
        self._vscode_syncing = False

    def _on_vscode_file_changed(self, path, _retries=0):
        """Called when VSCode saves the temp file — sync back to expr_input."""
        if self._vscode_syncing:
            return
        # Re-add watch immediately (VSCode atomic save may remove and recreate the file)
        if path not in self._vscode_watcher.files():
            self._vscode_watcher.addPath(path)
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                new_text = f.read()
        except Exception:
            # File might still be being written; retry after short delay
            if _retries < 5:
                QTimer.singleShot(200, lambda: self._on_vscode_file_changed(path, _retries + 1))
            return
        if new_text != self.expr_input.toPlainText():
            self._vscode_syncing = True
            self.expr_input.setPlainText(new_text)
            self._vscode_syncing = False

    def _update_completion(self):
        """Auto-update completion popup as user types."""
        # Clear Ctrl+D multi-selection on text change
        if hasattr(self, '_ctrl_d_search') and self._ctrl_d_search:
            self._ctrl_d_search = None
            self._ctrl_d_selections = []
            self.expr_input.setExtraSelections([])
        if not need_complete:
            return
        popup = self._completion_popup
        if self._state != self.STATE_IDLE:
            popup.hide()
            return
        cursor = self.expr_input.textCursor()
        text = self.expr_input.toPlainText()
        pos = cursor.position()
        # Find start of current word (alphanumeric + underscore)
        start = pos
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] == '_'):
            start -= 1
        prefix = text[start:pos]
        if len(prefix) >= 2:
            rect = self.expr_input.cursorRect(cursor)
            popup.show_completions(self.expr_input, prefix, start, rect)
        else:
            popup.hide()

    def eventFilter(self, obj, event):
        if obj is self.expr_input and event.type() == event.KeyPress:
            mods = event.modifiers()
            key = event.key()
            cursor = self.expr_input.textCursor()

            # Smart auto-close brackets: ( [ { <  ── also handles ) ] } > skip-over & pair-delete
            _OPEN_TO_CLOSE = {Qt.Key_ParenLeft: ')', Qt.Key_BracketLeft: ']',
                              Qt.Key_BraceLeft: '}', Qt.Key_Less: '>'}
            _OPEN_CHARS = {Qt.Key_ParenLeft: '(', Qt.Key_BracketLeft: '[',
                           Qt.Key_BraceLeft: '{', Qt.Key_Less: '<'}
            _CLOSE_TO_CHAR = {Qt.Key_ParenRight: ')', Qt.Key_BracketRight: ']',
                              Qt.Key_BraceRight: '}', Qt.Key_Greater: '>'}
            _PAIR_MAP = {'(': ')', '[': ']', '{': '}', '<': '>'}
            # Only exclude Ctrl/Alt/Meta — Shift must be allowed (since ( { < > etc. need Shift)
            _pair_mods = Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier
            _ctrl_d_active = bool(getattr(self, '_ctrl_d_search', None))
            if not (mods & _pair_mods) and not _ctrl_d_active:
                # Open bracket → auto-close (or wrap current selection)
                if key in _OPEN_TO_CLOSE:
                    if cursor.hasSelection():
                        sel = cursor.selectedText()
                        cursor.insertText(_OPEN_CHARS[key] + sel + _OPEN_TO_CLOSE[key])
                        start = cursor.position() - len(sel) - 1
                        cursor.setPosition(start, QTextCursor.MoveAnchor)
                        cursor.setPosition(start + len(sel), QTextCursor.KeepAnchor)
                        self.expr_input.setTextCursor(cursor)
                    else:
                        cursor.insertText(_OPEN_CHARS[key] + _OPEN_TO_CLOSE[key])
                        cursor.movePosition(QTextCursor.Left)
                        self.expr_input.setTextCursor(cursor)
                    return True
                # Close bracket → skip over if the next char already matches (no double-insert)
                if key in _CLOSE_TO_CHAR and not cursor.hasSelection():
                    pos = cursor.position()
                    text = self.expr_input.toPlainText()
                    ch = _CLOSE_TO_CHAR[key]
                    if pos < len(text) and text[pos] == ch:
                        cursor.movePosition(QTextCursor.Right)
                        self.expr_input.setTextCursor(cursor)
                        return True
                    # otherwise fall through and let Qt insert the close char normally
                # Backspace → delete the whole pair when cursor sits between matching open/close
                if key == Qt.Key_Backspace and not cursor.hasSelection():
                    pos = cursor.position()
                    text = self.expr_input.toPlainText()
                    if pos > 0 and pos < len(text):
                        prev, nxt = text[pos - 1], text[pos]
                        if prev in _PAIR_MAP and _PAIR_MAP[prev] == nxt:
                            cursor.beginEditBlock()
                            cursor.deleteChar()                 # delete close
                            cursor.movePosition(QTextCursor.Left)
                            cursor.deleteChar()                 # delete open
                            cursor.endEditBlock()
                            self.expr_input.setTextCursor(cursor)
                            return True

            # Completion popup navigation
            popup = self._completion_popup
            if need_complete and popup.isVisible():
                if key == Qt.Key_Escape:
                    popup.hide()
                    return True
                if key == Qt.Key_Up:
                    idx = popup.currentIndex()
                    new_row = idx.row() - 1 if idx.isValid() else popup._proxy.rowCount() - 1
                    new_idx = popup._proxy.index(new_row, 0)
                    if new_idx.isValid():
                        popup.setCurrentIndex(new_idx)
                    return True
                if key == Qt.Key_Down:
                    idx = popup.currentIndex()
                    new_row = idx.row() + 1 if idx.isValid() else 0
                    if new_row >= popup._proxy.rowCount():
                        new_row = 0
                    new_idx = popup._proxy.index(new_row, 0)
                    if new_idx.isValid():
                        popup.setCurrentIndex(new_idx)
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter):
                    index = popup.currentIndex()
                    if index.isValid():
                        popup._insert_completion(index.data())
                    popup.hide()
                    return True

            # Escape: clear Ctrl+D multi-selection
            if key == Qt.Key_Escape and not mods:
                if hasattr(self, '_ctrl_d_search') and self._ctrl_d_search:
                    self._ctrl_d_search = None
                    self._ctrl_d_selections = []
                    self.expr_input.setExtraSelections([])
                    return True

            # Tab: confirm completion if popup visible, otherwise ignore
            if key == Qt.Key_Tab and not mods:
                if need_complete and popup.isVisible():
                    index = popup.currentIndex()
                    if index.isValid():
                        popup._insert_completion(index.data())
                    popup.hide()
                    return True
                return True  # swallow Tab always

            # Ctrl+D: Add selection to next Find match (VSCode style)
            if mods & Qt.ControlModifier and key == Qt.Key_D and not (mods & Qt.ShiftModifier):
                doc = self.expr_input.document()
                text = self.expr_input.toPlainText()
                # If we already have an active Ctrl+D search, find next occurrence
                if hasattr(self, '_ctrl_d_search') and self._ctrl_d_search:
                    search_text = self._ctrl_d_search
                    # Start searching from after the last selection
                    last_cursor = self.expr_input.textCursor()
                    search_from = last_cursor.selectionEnd()
                    # Find next occurrence
                    find_cursor = doc.find(search_text, search_from)
                    if find_cursor.isNull():
                        # Wrap around: search from beginning
                        find_cursor = doc.find(search_text, 0)
                    if not find_cursor.isNull() and find_cursor.position() != last_cursor.position():
                        # Add as extra selection
                        es = QTextEdit.ExtraSelection()
                        es.cursor = find_cursor
                        es.format.setBackground(QColor("#264f78"))
                        es.format.setForeground(QColor("#ffffff"))
                        self._ctrl_d_selections.append(es)
                        self.expr_input.setExtraSelections(self._ctrl_d_selections)
                        self.expr_input.setTextCursor(find_cursor)
                else:
                    # First Ctrl+D: select the word under cursor
                    cursor.select(QTextCursor.WordUnderCursor)
                    selected = cursor.selectedText()
                    if selected:
                        self._ctrl_d_search = selected
                        self._ctrl_d_selections = []
                        es = QTextEdit.ExtraSelection()
                        es.cursor = cursor
                        es.format.setBackground(QColor("#264f78"))
                        es.format.setForeground(QColor("#ffffff"))
                        self._ctrl_d_selections.append(es)
                        self.expr_input.setExtraSelections(self._ctrl_d_selections)
                        self.expr_input.setTextCursor(cursor)
                return True

            # Multi-edit: when Ctrl+D selections are active, typing replaces all selected occurrences
            if hasattr(self, '_ctrl_d_search') and self._ctrl_d_search:
                # Get the text to insert
                insert_text = None
                if key == Qt.Key_Backspace and not mods:
                    insert_text = ""  # delete all selections
                elif key == Qt.Key_Delete and not mods:
                    insert_text = ""
                elif not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
                    ch = event.text()
                    if ch and ch.isprintable():
                        insert_text = ch
                if insert_text is not None:
                    # Replace all selected occurrences using QTextCursor (preserves undo history)
                    positions = []
                    for es in self._ctrl_d_selections:
                        c = es.cursor
                        positions.append((c.selectionStart(), c.selectionEnd()))
                    positions.sort(reverse=True)  # reverse so earlier positions don't shift
                    # Clear Ctrl+D state
                    self._ctrl_d_search = None
                    self._ctrl_d_selections = []
                    self.expr_input.setExtraSelections([])
                    # Use a single edit block so Ctrl+Z undoes all replacements at once
                    cursor = self.expr_input.textCursor()
                    cursor.beginEditBlock()
                    for start, end in positions:
                        cursor.setPosition(start)
                        cursor.setPosition(end, QTextCursor.KeepAnchor)
                        cursor.insertText(insert_text)
                    cursor.endEditBlock()
                    # Position cursor at the first replacement point
                    if positions:
                        first_start = positions[-1][0]  # last in sorted-reverse = first position
                        cursor.setPosition(first_start + len(insert_text))
                        self.expr_input.setTextCursor(cursor)
                    return True

            # Ctrl+Shift+K: delete current line
            if mods & Qt.ControlModifier and mods & Qt.ShiftModifier and key == Qt.Key_K:
                cursor.beginEditBlock()
                cursor.select(QTextCursor.BlockUnderCursor)
                cursor.removeSelectedText()
                # remove trailing newline if not last line
                if not cursor.atEnd():
                    cursor.deleteChar()
                cursor.endEditBlock()
                return True

            # Alt+Up: move line up
            if mods & Qt.AltModifier and key == Qt.Key_Up:
                cursor.beginEditBlock()
                block = cursor.block()
                if block.blockNumber() > 0:
                    prev_block = block.previous()
                    line_text = block.text()
                    prev_text = prev_block.text()
                    # select previous line
                    cursor.select(QTextCursor.BlockUnderCursor)
                    cursor.removeSelectedText()
                    if not cursor.atEnd():
                        cursor.deleteChar()
                    # move to start of current (now previous) line
                    cursor.movePosition(QTextCursor.StartOfBlock)
                    cursor.insertText(line_text + '\n')
                    cursor.movePosition(QTextCursor.Up)
                    cursor.movePosition(QTextCursor.StartOfBlock)
                cursor.endEditBlock()
                return True

            # Alt+Down: move line down
            if mods & Qt.AltModifier and key == Qt.Key_Down:
                cursor.beginEditBlock()
                block = cursor.block()
                next_block = block.next()
                if next_block.isValid():
                    line_text = block.text()
                    next_text = next_block.text()
                    # select current line
                    cursor.select(QTextCursor.BlockUnderCursor)
                    cursor.removeSelectedText()
                    if not cursor.atEnd():
                        cursor.deleteChar()
                    # insert after next line
                    cursor.movePosition(QTextCursor.EndOfBlock)
                    cursor.insertText('\n' + line_text)
                cursor.endEditBlock()
                return True

            # Ctrl+Shift+Up: copy line up
            if mods & Qt.ControlModifier and mods & Qt.ShiftModifier and key == Qt.Key_Up:
                cursor.beginEditBlock()
                block = cursor.block()
                line_text = block.text()
                cursor.movePosition(QTextCursor.EndOfBlock)
                cursor.insertText('\n' + line_text)
                cursor.endEditBlock()
                return True

            # Ctrl+Shift+Down: copy line down
            if mods & Qt.ControlModifier and mods & Qt.ShiftModifier and key == Qt.Key_Down:
                cursor.beginEditBlock()
                block = cursor.block()
                line_text = block.text()
                cursor.movePosition(QTextCursor.StartOfBlock)
                cursor.insertText(line_text + '\n')
                cursor.endEditBlock()
                return True

            # Ctrl+/: toggle line comment
            if mods & Qt.ControlModifier and key == Qt.Key_Slash:
                cursor.beginEditBlock()
                block = cursor.block()
                line_text = block.text()
                if line_text.lstrip().startswith('#'):
                    # uncomment
                    new_text = line_text.replace('#', '', 1)
                    cursor.select(QTextCursor.BlockUnderCursor)
                    cursor.removeSelectedText()
                    cursor.insertText(new_text)
                else:
                    # comment
                    cursor.movePosition(QTextCursor.StartOfBlock)
                    cursor.insertText('#')
                cursor.endEditBlock()
                return True

            # Ctrl+Enter: End then Enter (new line at end of current line)
            if mods & Qt.ControlModifier and not (mods & Qt.ShiftModifier) and (key == Qt.Key_Return or key == Qt.Key_Enter):
                cursor.movePosition(QTextCursor.EndOfBlock)
                cursor.insertText('\n')
                self.expr_input.setTextCursor(cursor)
                return True

            # Ctrl+Shift+Enter: Home then Enter then Up (new line above current line)
            if mods & Qt.ControlModifier and mods & Qt.ShiftModifier and (key == Qt.Key_Return or key == Qt.Key_Enter):
                cursor.movePosition(QTextCursor.StartOfBlock)
                cursor.insertText('\n')
                cursor.movePosition(QTextCursor.Up)
                self.expr_input.setTextCursor(cursor)
                return True

        return super().eventFilter(obj, event)

    def _update_mode(self):
        """Toggle between edit mode and view mode based on current state."""
        is_view = self._state in (self.STATE_DONE_UNVIEWED, self.STATE_DONE_VIEWED)
        is_idle = self._state == self.STATE_IDLE
        is_running = self._state == self.STATE_RUNNING
        is_queued = self._state == self.STATE_QUEUED
        busy = is_running or is_queued

        # Alpha Expression: stretch to fill in idle, compact otherwise
        if is_idle:
            self.expr_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            self.expr_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self._left_stretch.changeSize(0, 0, QSizePolicy.Minimum, QSizePolicy.Minimum)
        else:
            self.expr_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            self.expr_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self._left_stretch.changeSize(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.expr_group.layout().activate()

        # Collapse groups in view mode
        if is_view:
            self.expr_toggle_btn.setChecked(True)
            self.settings_toggle_btn.setChecked(True)
        else:
            self.expr_toggle_btn.setChecked(True)
            self.settings_toggle_btn.setChecked(True)

        # Edit-mode widgets (visible when idle/running)
        self.sim_btn.setVisible(not is_view and not is_running)
        self.tune_btn.setVisible(not is_view and not is_running)
        self._edit_score_btn.setVisible(not is_view and not is_running)
        self.fill8_btn.setVisible(not is_view and not is_running)
        self.fill_count_spin.setVisible(not is_view and not is_running)
        self.tune_expand_check.setVisible(not is_view)
        self.auto_fill_check.setVisible(not is_view and show_auto_fill)
        self.cancel_btn.setVisible(is_running or is_queued)
        self.progress_bar.setVisible(is_running)
        self.status_label.setVisible(not is_view)
        self.checks_pass_btn.setVisible(is_view and bool(self.checks_pass_list.toPlainText()))
        self.checks_warn_btn.setVisible(is_view and bool(self.checks_warn_list.toPlainText()))
        self.checks_fail_btn.setVisible(is_view and bool(self.checks_fail_list.toPlainText()))
        self.checks_pending_btn.setVisible(is_view and bool(self.checks_pending_list.toPlainText()))
        self._classif_toggle_btn.setVisible(is_view)
        self._classif_list_btn.setVisible(is_view)
        self._open_url_btn.setVisible(is_view)
        self.props_btn.setVisible(is_view)
        self.submit_btn.setVisible(is_view)
        if hasattr(self, 'check_btn'):
            self.check_btn.setVisible(is_view)
        self._pin_metrics_btn.setVisible(is_view)
        if hasattr(self, 'pnl_copy_btn'):
            self.pnl_copy_btn.setVisible(is_view)
        if not is_view:
            self.checks_pass_group.setVisible(False)
            self.checks_warn_group.setVisible(False)
            self.checks_fail_group.setVisible(False)
            self.checks_pending_group.setVisible(False)
            self.props_group.setVisible(False)

        # View-mode widgets (visible when done)
        self.corr_widget.setVisible(is_view)
        self.right_scroll.setVisible(is_view)
        self.clone_btn.setVisible(not is_view)

        if is_queued:
            self.cancel_btn.setText(T("Dequeue"))
        else:
            self.cancel_btn.setText(T("Cancel"))

        # Move PnL Curve to left panel in view mode
        if is_view:
            self._right_layout.removeWidget(self.pnl_content_widget)
            self._left_layout.insertWidget(self._left_layout.indexOf(self._left_stretch), self.pnl_content_widget)
        else:
            self._left_layout.removeWidget(self.pnl_content_widget)
            self._right_layout.insertWidget(0, self.pnl_content_widget)

        # Apply splitter sizes after layout is settled
        QTimer.singleShot(0, self._apply_splitter)

        # On Windows with showMaximized(), the first transition to view mode
        # causes a layout recalculation that pushes the status bar off-screen.
        # Force a window state toggle to recalculate the maximized geometry.
        if is_view:
            main_win = self.window()
            if isinstance(main_win, MainWindow) and not main_win._first_done_layout_fixed and main_win.isMaximized():
                main_win._first_done_layout_fixed = True
                QTimer.singleShot(300, lambda: (main_win.showNormal(), main_win.showMaximized()))

    def _apply_splitter(self):
        """Apply splitter size ratios after the layout is settled."""
        total = self._splitter.width()
        if total <= 0:
            # Widget not yet shown, retry after a short delay
            QTimer.singleShot(50, self._apply_splitter)
            return
        left = round(total / 1.95)
        self._splitter.setSizes([left, total - left])

    def _emit_title(self, suffix=""):
        title = self._tab_base_name
        if suffix:
            title = f"{title} {suffix}"
        self.tab_title_update.emit(title)

    def _copy_all(self):
        """Copy expression + settings to clipboard as JSON."""
        data = {
            "expression": self.expr_input.toPlainText(),
            "settings": {
                "region": self.region_combo.currentText(),
                "universe": self.universe_combo.currentText(),
                "delay": self.delay_spin.value(),
                "decay": self._get_decay_single(),
                "neutralization": self.neutral_combo.currentText(),
                "truncation": self._get_truncation_single(),
                "pasteurization": self.pasteur_combo.currentText(),
                "nanHandling": self.nan_combo.currentText(),
                "maxTrade": self.max_trade_combo.currentText(),
                "maxPosition": self.max_position_combo.currentText(),
                "language": self.language_combo.currentText(),
                "lookback": self.lookback_spin.value(),
            }
        }
        clipboard = QApplication.clipboard()
        clipboard.setText(json.dumps(data, indent=2))
        self._copy_all_btn.setText(T("Copied!"))
        QTimer.singleShot(1500, lambda: self._copy_all_btn.setText(T("Copy All")))

    def _import_all(self):
        """Import expression + settings from clipboard JSON."""
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
        if not text:
            QMessageBox.warning(self, T("Import"), T("Clipboard is empty."))
            return
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            QMessageBox.warning(self, T("Import"), T("Clipboard does not contain valid JSON."))
            return

        # Import expression
        if "expression" in data:
            self.expr_input.setPlainText(data["expression"])

        # Import settings
        settings = data.get("settings", {})
        if "region" in settings:
            self.region_combo.setCurrentText(settings["region"])
        if "universe" in settings:
            self.universe_combo.setCurrentText(settings["universe"])
        if "delay" in settings:
            self.delay_spin.setValue(int(settings["delay"]))
        if "decay" in settings:
            self._set_decay_value(float(settings["decay"]))
        if "neutralization" in settings:
            self.neutral_combo.setCurrentText(settings["neutralization"])
        if "truncation" in settings:
            self._set_truncation_value(float(settings["truncation"]))
        if "pasteurization" in settings:
            self.pasteur_combo.setCurrentText(settings["pasteurization"])
        if "nanHandling" in settings:
            self.nan_combo.setCurrentText(settings["nanHandling"])
        if "maxTrade" in settings:
            self.max_trade_combo.setCurrentText(settings["maxTrade"])
        if "maxPosition" in settings:
            self.max_position_combo.setCurrentText(settings["maxPosition"])
        if "language" in settings:
            self.language_combo.setCurrentText(settings["language"])
        if "lookback" in settings:
            self.lookback_spin.setValue(int(settings["lookback"]))

        self._import_all_btn.setText(T("Imported!"))
        QTimer.singleShot(1500, lambda: self._import_all_btn.setText(T("Import All")))

    def _copy_expr(self):
        """Copy current expression to clipboard."""
        text = self.expr_input.toPlainText()
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self._copy_expr_btn.setText(T("Copied!"))
        QTimer.singleShot(1500, lambda: self._copy_expr_btn.setText(T("Copy")))

    def _import_expr(self):
        """Import expression from clipboard."""
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
        if not text:
            QMessageBox.warning(self, T("Import"), T("Clipboard is empty."))
            return
        self.expr_input.setPlainText(text)
        self._import_expr_btn.setText(T("Imported!"))
        QTimer.singleShot(1500, lambda: self._import_expr_btn.setText(T("Import")))

    def _copy_settings(self):
        """Copy current simulation settings to clipboard as JSON."""
        settings = {
            "region": self.region_combo.currentText(),
            "universe": self.universe_combo.currentText(),
            "delay": self.delay_spin.value(),
            "decay": self._get_decay_single(),
            "neutralization": self.neutral_combo.currentText(),
            "truncation": self._get_truncation_single(),
            "pasteurization": self.pasteur_combo.currentText(),
            "nanHandling": self.nan_combo.currentText(),
            "maxTrade": self.max_trade_combo.currentText(),
            "maxPosition": self.max_position_combo.currentText(),
            "language": self.language_combo.currentText(),
            "lookback": self.lookback_spin.value(),
        }
        clipboard = QApplication.clipboard()
        clipboard.setText(json.dumps(settings, indent=2))
        self._copy_settings_btn.setText(T("Copied!"))
        QTimer.singleShot(1500, lambda: self._copy_settings_btn.setText(T("Copy")))

    def _import_settings(self):
        """Import simulation settings from clipboard JSON."""
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
        if not text:
            QMessageBox.warning(self, T("Import"), T("Clipboard is empty."))
            return
        try:
            settings = json.loads(text)
        except json.JSONDecodeError:
            QMessageBox.warning(self, T("Import"), T("Clipboard does not contain valid JSON."))
            return

        if "region" in settings:
            self.region_combo.setCurrentText(settings["region"])
        if "universe" in settings:
            self.universe_combo.setCurrentText(settings["universe"])
        if "delay" in settings:
            self.delay_spin.setValue(int(settings["delay"]))
        if "decay" in settings:
            self._set_decay_value(float(settings["decay"]))
        if "neutralization" in settings:
            self.neutral_combo.setCurrentText(settings["neutralization"])
        if "truncation" in settings:
            self._set_truncation_value(float(settings["truncation"]))
        if "pasteurization" in settings:
            self.pasteur_combo.setCurrentText(settings["pasteurization"])
        if "nanHandling" in settings:
            self.nan_combo.setCurrentText(settings["nanHandling"])
        if "maxTrade" in settings:
            self.max_trade_combo.setCurrentText(settings["maxTrade"])
        if "maxPosition" in settings:
            self.max_position_combo.setCurrentText(settings["maxPosition"])
        if "language" in settings:
            self.language_combo.setCurrentText(settings["language"])
        if "lookback" in settings:
            self.lookback_spin.setValue(int(settings["lookback"]))

        self._import_settings_btn.setText(T("Imported!"))
        QTimer.singleShot(1500, lambda: self._import_settings_btn.setText(T("Import")))

    def _toggle_combo_edit(self, combo, checked):
        """Toggle a QComboBox between dropdown-only and editable mode."""
        combo.setEditable(checked)
        if checked:
            combo.setInsertPolicy(QComboBox.NoInsert)

    def _on_region_changed(self, region: str):
        # Skip if in initialization mode
        if getattr(self, '_region_init_mode', False):
            return

        opts = REGION_OPTIONS.get(region, {})
        universes = opts.get("universes", [])
        neutrals = opts.get("neutralizations", ["NONE"])

        prev_universe = self.universe_combo.currentText()
        self.universe_combo.blockSignals(True)
        self.universe_combo.clear()
        self.universe_combo.addItems(["?"] + universes)
        if prev_universe in (["?"] + universes):
            self.universe_combo.setCurrentText(prev_universe)
        self.universe_combo.blockSignals(False)

        prev_neutral = self.neutral_combo.currentText()
        self.neutral_combo.blockSignals(True)
        self.neutral_combo.clear()
        self.neutral_combo.addItems(["?"] + neutrals)
        if prev_neutral in (["?"] + neutrals):
            self.neutral_combo.setCurrentText(prev_neutral)
        self.neutral_combo.blockSignals(False)

    # ── Simulate ──
    def _fill_8(self):
        main_window = self.window()
        if not isinstance(main_window, MainWindow):
            return
        target_slots = self.fill_count_spin.value()  # target in slots (not tabs)
        main_window._next_batch_id += 1
        batch_id = main_window._next_batch_id
        batch_expr = self.expr_input.toPlainText().strip()
        current_region = self.region_combo.currentText()
        current_is_glb = current_region == "GLB"
        current_slot_cost = 2 if current_is_glb else 1

        # Calculate current slot usage
        def calc_running_weight():
            weight = 0
            for i in range(main_window.tab_widget.count()):
                tab = main_window.tab_widget.widget(i)
                if isinstance(tab, SimulateTab) and tab.is_running():
                    tab_region = getattr(tab, '_current_region', None)
                    if tab_region == "GLB":
                        weight += 2
                    else:
                        weight += 1
            return weight

        # Assign batch to current tab
        self._batch_id = batch_id
        self._batch_expr = batch_expr
        self.set_tab_base_name(f"S{batch_id}")
        # Simulate current tab if not already running
        if not self.is_running():
            self._on_simulate()

        # Then fill up to target slots, checking before each addition
        while True:
            current_weight = calc_running_weight()
            # Check if we can add more without exceeding target
            if current_weight >= target_slots:
                break
            if current_is_glb and current_weight + current_slot_cost > target_slots:
                QMessageBox.information(
                    self, T("Cannot Fill"),
                    f"{T('Current region is GLB (2 slots/tab). Adding another GLB would reach ')}{current_weight + current_slot_cost}{T(' slots, exceeding target of ')}{target_slots}."
                )
                break
            # Add new tab
            main_window._add_tab(self.get_state(), batch_id=batch_id, batch_expr=batch_expr)
            new_tab = main_window.tab_widget.widget(main_window.tab_widget.count() - 1)
            if isinstance(new_tab, SimulateTab):
                new_tab._on_simulate()

    # ── Generic traverse helper ──

    def _traverse_values(self, targets, set_value_fn, simulated_set, param_label):
        """Generic traverse: open new tabs for each target value and simulate.

        Args:
            targets: list of values to traverse
            set_value_fn: callable(tab, value) to set the value on a tab
            simulated_set: the set tracking already-simulated values (e.g. self._simulated_decay)
            param_label: label for status messages (e.g. "decay values")
        """
        is_done = self._state in (self.STATE_DONE_VIEWED, self.STATE_DONE_UNVIEWED)
        if is_done:
            targets = [v for v in targets if v not in simulated_set]
        else:
            targets = list(targets)
        if not targets:
            self.status_label.setVisible(True)
            self.status_label.setText(f"{T('All ')}{param_label}{T(' already simulated')}")
            self.status_label.setStyleSheet("color: #a6e3a1;")
            return
        main_window = self.window()
        if not isinstance(main_window, MainWindow):
            return

        # Save original expression before any _on_simulate changes it
        original_expr = self.expr_input.toPlainText()

        # Collect all new tabs first, then simulate them all
        tabs_to_simulate = []

        if is_done:
            # Keep current tab intact, open new tabs for all targets
            for v in targets:
                main_window._add_tab(self.get_state())
                new_tab = main_window.tab_widget.widget(main_window.tab_widget.count() - 1)
                if isinstance(new_tab, SimulateTab):
                    new_tab.expr_input.setPlainText(original_expr)
                    set_value_fn(new_tab, v)
                    simulated_set.add(v)
                    tabs_to_simulate.append(new_tab)
        else:
            # First target: current tab
            set_value_fn(self, targets[0])
            simulated_set.add(targets[0])
            # Remaining targets: open new tabs
            for v in targets[1:]:
                main_window._add_tab(self.get_state())
                new_tab = main_window.tab_widget.widget(main_window.tab_widget.count() - 1)
                if isinstance(new_tab, SimulateTab):
                    new_tab.expr_input.setPlainText(original_expr)
                    set_value_fn(new_tab, v)
                    simulated_set.add(v)
                    tabs_to_simulate.append(new_tab)
            # Simulate current tab first (may trigger expression traverse)
            self._on_simulate()

        # Simulate all new tabs
        for t in tabs_to_simulate:
            QTimer.singleShot(100, lambda t=t: t._on_simulate())

    # ── Specific traverse methods ──

    def _traverse_universes(self):
        region = self.region_combo.currentText()
        opts = REGION_OPTIONS.get(region, {})
        all_universes = opts.get("universes", [])
        skip = opts.get("skip_universes", [])
        all_universes = [u for u in all_universes if u not in skip]
        if not all_universes:
            return
        self._traverse_values(
            all_universes,
            lambda tab, u: tab.universe_combo.setCurrentText(u),
            self._simulated_universes,
            "universes",
        )

    def _traverse_neutrals(self):
        region = self.region_combo.currentText()
        opts = REGION_OPTIONS.get(region, {})
        all_neutrals = [n for n in opts.get("neutralizations", ["NONE"]) if n not in ("NONE", "SLOW_AND_FAST")]
        if not all_neutrals:
            return
        self._traverse_values(
            all_neutrals,
            lambda tab, n: tab.neutral_combo.setCurrentText(n),
            self._simulated_neutrals,
            "neutralizations",
        )

    # ── Generic numeric input helpers ──

    @staticmethod
    def _set_input_value(widget, value):
        """Set a numeric input widget's text. Accepts a number or a list of numbers."""
        if isinstance(value, (list, tuple)):
            parts = []
            for v in value:
                if isinstance(v, float) and v == int(v):
                    parts.append(str(int(v)))
                else:
                    parts.append(str(v))
            widget.setText(",".join(parts))
        elif isinstance(value, str):
            widget.setText(value)
        elif isinstance(value, float) and value == int(value):
            widget.setText(str(int(value)))
        else:
            widget.setText(str(value))

    @staticmethod
    def _get_input_single(widget, parse_fn, default=0.0):
        """Get a single value (first from parsed list) from a numeric input widget."""
        values = parse_fn()
        if values:
            return values[0]
        try:
            return float(widget.text().strip())
        except (ValueError, AttributeError):
            return default

    def _step_value(self, widget, delta, min_val, max_val, set_fn):
        """Step a numeric input value by delta, clamped to [min_val, max_val]."""
        try:
            val = float(widget.text().strip())
            val = val + delta
            if min_val is not None:
                val = max(min_val, val)
            if max_val is not None:
                val = min(max_val, val)
            set_fn(val)
        except (ValueError, AttributeError):
            pass

    def _on_numeric_text_changed(self, text, up_btn, down_btn):
        """Enable/disable step buttons based on whether input is a single number."""
        is_single = bool(text.strip()) and ',' not in text.strip()
        up_btn.setEnabled(is_single)
        down_btn.setEnabled(is_single)

    # ── Decay input handling ──

    def _on_decay_text_changed(self, text):
        """Enable/disable step buttons based on whether input is a single number."""
        self._on_numeric_text_changed(text, self.decay_step_up_btn, self.decay_step_down_btn)

    def _step_decay_up(self):
        """Increment decay value by 1."""
        self._step_value(self.decay_input, 1, 0, None, self._set_decay_value)

    def _step_decay_down(self):
        """Decrement decay value by 1."""
        self._step_value(self.decay_input, -1, 0, None, self._set_decay_value)

    def _parse_decay_values(self):
        """Parse decay input text into a list of float values. Delegates to parse_numeric_values()."""
        return parse_numeric_values(self.decay_input.text().strip())

    def _get_decay_single(self):
        """Get a single decay value (first value from input) for simulation settings."""
        return self._get_input_single(self.decay_input, self._parse_decay_values, 0.0)

    def _set_decay_value(self, value):
        """Set decay input. Accepts a number or a list of numbers."""
        self._set_input_value(self.decay_input, value)

    def _traverse_decay(self):
        """Traverse decay values from input (e.g. 1,2,3) or default [0,10,15,21,42,63,126,252,512]"""
        parsed = self._parse_decay_values()
        if parsed and len(parsed) > 1:
            decay_values = parsed
        else:
            decay_values = _load_config_value('COARSE_DECAYS', COARSE_DECAYS)
        self._traverse_values(
            decay_values,
            lambda tab, d: tab._set_decay_value(d),
            self._simulated_decay,
            "decay values",
        )

    # ── Truncation input handling ──

    def _on_truncation_text_changed(self, text):
        """Enable/disable step buttons based on whether input is a single number."""
        self._on_numeric_text_changed(text, self.truncation_step_up_btn, self.truncation_step_down_btn)

    def _step_truncation_up(self):
        """Increment truncation value by 0.01."""
        try:
            val = float(self.truncation_input.text().strip())
            val = min(1, round(val + 0.01, 4))
            self._set_truncation_value(val)
        except (ValueError, AttributeError):
            pass

    def _step_truncation_down(self):
        """Decrement truncation value by 0.01."""
        try:
            val = float(self.truncation_input.text().strip())
            val = max(0, round(val - 0.01, 4))
            self._set_truncation_value(val)
        except (ValueError, AttributeError):
            pass

    def _parse_truncation_values(self):
        """Parse truncation input text into a list of float values. Delegates to parse_numeric_values()."""
        return parse_numeric_values(self.truncation_input.text().strip())

    def _get_truncation_single(self):
        """Get a single truncation value (first value from input) for simulation settings."""
        return self._get_input_single(self.truncation_input, self._parse_truncation_values, 0.0)

    def _set_truncation_value(self, value):
        """Set truncation input. Accepts a number or a list of numbers."""
        self._set_input_value(self.truncation_input, value)

    def _traverse_truncation(self):
        """Traverse truncation values from input (e.g. 0.01,0.05,0.1) or default [0.001,0.005,0.01,0.03,0.05,0.1]"""
        parsed = self._parse_truncation_values()
        if parsed and len(parsed) > 1:
            truncation_values = parsed
        else:
            truncation_values = _load_config_value('DEFAULT_TRUNCS', DEFAULT_TRUNCS)
        self._traverse_values(
            truncation_values,
            lambda tab, t: tab._set_truncation_value(t),
            self._simulated_truncation,
            "truncation values",
        )

    def _parse_expr_traverse_values(self, expression):
        """Parse <...> placeholders in expression and return list of replacement values.

        Supports:
          - <1,2,3> → ["1", "2", "3"]
          - <5> → ["5"]
          - <0:9:1> → ["0", "1", "2", ..., "9"]  (start:end:step range)
          - <0:9:1:[2,5]> → ["0", "1", "3", "4", "6", ..., "9"]  (range with exclusions)
          - Multiple placeholders: <1,2> and <a,b> → cartesian product
        Returns (expressions_list, error_msg).
        """
        # Find all <...> groups
        groups = re.findall(r'<([^>]+)>', expression)
        if not groups:
            return None, "No <...> placeholder found in expression. Use e.g. ts_delay(returns, <1,2,3>)"

        # Parse each group into a list of string values
        parsed_groups = []
        for g in groups:
            # Try range format: start:end:step  or  start:end:step:[excludes]
            range_m = re.match(r'^(-?\d+\.?\d*):(-?\d+\.?\d*):(-?\d+\.?\d*)(?::\[([^\]]+)\])?$', g)
            if range_m:
                start = float(range_m.group(1))
                end = float(range_m.group(2))
                step = float(range_m.group(3))
                excludes = None
                if range_m.group(4):
                    excludes = set()
                    for x in range_m.group(4).split(','):
                        try:
                            excludes.add(int(x.strip()) if '.' not in x.strip() else float(x.strip()))
                        except ValueError:
                            pass
                parts = []
                if step == 0:
                    iv = int(start) if start == int(start) else start
                    parts.append(str(iv))
                else:
                    v = start
                    while (step > 0 and v <= end + step * 1e-9) or (step < 0 and v >= end + step * 1e-9):
                        iv = int(v) if v == int(v) else v
                        if excludes is None or iv not in excludes:
                            parts.append(str(iv))
                        v += step
                if not parts:
                    return None, f"Range <{g}> produced no values"
                parsed_groups.append(parts)
            else:
                # Comma-separated values
                parts = [p.strip() for p in g.split(',') if p.strip()]
                if not parts:
                    return None, f"Empty placeholder <> in expression"
                parsed_groups.append(parts)

        # Build cartesian product of all groups
        combos = list(product(*parsed_groups))

        # Generate one expression per combo
        expressions = []
        for combo in combos:
            expr = expression
            for g_text, replacement in zip(groups, combo):
                # Replace first occurrence of <g_text> with replacement
                expr = expr.replace(f'<{g_text}>', replacement, 1)
            expressions.append(expr)

        return expressions, None

    def _refresh_language(self):
        """Refresh all visible text in this tab after a language switch."""
        self._tab_base_name = T("Simulate")
        self.expr_toggle_btn.setText(T("Alpha Expression"))
        self._copy_expr_btn.setText(T("Copy"))
        self._import_expr_btn.setText(T("Import"))
        self.settings_toggle_btn.setText(T("Simulation Settings"))
        self._copy_settings_btn.setText(T("Copy"))
        self._import_settings_btn.setText(T("Import"))
        self.sim_btn.setText(T("Simulate"))
        self.fill8_btn.setText(T("Fill"))
        self._refresh_corr_button_labels()
        # Refresh tab title
        main_win = self.window()
        if main_win and hasattr(main_win, 'tab_widget'):
            idx = main_win.tab_widget.indexOf(self)
            if idx >= 0:
                main_win.tab_widget.setTabText(idx, self._tab_base_name)

    def _refresh_corr_button_labels(self):
        """Refresh Self Corr / PPC button labels to reflect use_local_corr."""
        self.self_corr_btn.setText(T("Local SC") if use_local_corr else T("Self Corr"))
        self.ppc_btn.setText(T("Local PPC") if use_local_corr else T("PPC"))

    def _on_simulate(self):
        expression = self.expr_input.toPlainText().strip()
        if not expression:
            QMessageBox.warning(self, T("Input Error"), T("Please enter an alpha expression."))
            return

        # Check if any setting or expression has ? placeholder — must use Tune instead
        # Skip this check when running as part of a tune expand (placeholders are applied before sim)
        if not getattr(self, '_tune_expand', False):
            for check_text, check_name in [
                (self.universe_combo.currentText(), "Universe"),
                (self.neutral_combo.currentText(), "Neutral"),
                (self.truncation_input.text().strip(), "Truncation"),
                (self.decay_input.text().strip(), "Decay"),
            ]:
                if re.match(r'^\?\d*(?:=.+)?$', check_text):
                    QMessageBox.information(self, T("Tune Required"), f"{check_name}{T(' is set to ')}'{check_text}'{T('. Please click Tune to auto-select the best value.')}")
                    return
            if re.search(r'<(?:\w+\s*)?\?(g)?\d+(?:=[^>]*)?>', expression) or re.search(r'<(?:\w+\s*)?\?(g)?(?!\d)(?:=[^>]*)?>', expression):
                QMessageBox.information(self, T("Tune Required"), T("Expression contains tune placeholder. Please click Tune to auto-select the best value."))
                return

        # Auto-detect multi-value decay/truncation and traverse instead of single sim
        decay_parsed = self._parse_decay_values()
        truncation_parsed = self._parse_truncation_values()
        has_multi_decay = decay_parsed is not None and len(decay_parsed) > 1
        has_multi_truncation = truncation_parsed is not None and len(truncation_parsed) > 1

        # Auto-detect multi-value universe (e.g. "TOP2000,TOP3000")
        universe_text = self.universe_combo.currentText().strip()
        multi_universes = [u.strip() for u in universe_text.split(',') if u.strip()] if ',' in universe_text else []

        if has_multi_decay and has_multi_truncation:
            # Both have multiple values: traverse decay first (each new tab keeps
            # the multi-value truncation list, so when it calls _on_simulate it
            # will auto-trigger truncation traversal → full cartesian product)
            self._traverse_decay()
            return
        elif has_multi_decay:
            self._traverse_decay()
            return
        elif has_multi_truncation:
            self._traverse_truncation()
            return
        elif len(multi_universes) > 1:
            # Traverse comma-separated universes
            self._traverse_values(
                multi_universes,
                lambda tab, u: tab.universe_combo.setCurrentText(u),
                self._simulated_universes,
                "universes",
            )
            return

        # Auto-detect <...> placeholders and traverse
        expressions, error = self._parse_expr_traverse_values(expression)
        if expressions and len(expressions) > 1:
            main_window = self.window()
            if isinstance(main_window, MainWindow):
                # Remaining variants: open new tabs
                for expr in expressions[1:]:
                    main_window._add_tab(self.get_state())
                    new_tab = main_window.tab_widget.widget(main_window.tab_widget.count() - 1)
                    if isinstance(new_tab, SimulateTab):
                        new_tab.expr_input.setPlainText(expr)
                        QTimer.singleShot(100, lambda t=new_tab: t._on_simulate())
                # First variant: set current tab expression, then simulate below
                self.expr_input.setPlainText(expressions[0])
                expression = expressions[0]  # Use replaced expression for current tab
                # Continue below with the normal _on_simulate logic

        if not self.client.is_authenticated():
            QMessageBox.warning(self, T("Not Authenticated"), T("Please login first."))
            return

        main_window = self.window()
        if isinstance(main_window, MainWindow):
            current_region = self.region_combo.currentText()
            is_glb = current_region == "GLB"

            # Calculate weighted running count: GLB = 2, others = 1
            running_weight = 0
            glb_running = 0
            for i in range(main_window.tab_widget.count()):
                tab = main_window.tab_widget.widget(i)
                if isinstance(tab, SimulateTab) and tab._state == SimulateTab.STATE_RUNNING:
                    tab_region = getattr(tab, '_current_region', None)
                    if tab_region == "GLB":
                        running_weight += 2
                        glb_running += 1
                    else:
                        running_weight += 1

            # Check limits: GLB max 4, total weight per user setting
            max_weight = main_window._get_max_running_weight()
            if is_glb:
                if glb_running >= 4 or running_weight >= max_weight:
                    language = self.language_combo.currentText()
                    self._queued_settings = {
                        "expression": expression,
                        "settings": {
                            "instrumentType": "EQUITY",
                            "region": self.region_combo.currentText(),
                            "universe": self.universe_combo.currentText(),
                            "delay": self.delay_spin.value(),
                            "decay": self._get_decay_single(),
                            "neutralization": self.neutral_combo.currentText(),
                            "truncation": self._get_truncation_single(),
                            "pasteurization": self.pasteur_combo.currentText(),
                            "language": language,
                            "lookback": self.lookback_spin.value(),
                            "visualization": False,
                            "testPeriod": "P0Y0M",
                            "maxTrade": self.max_trade_combo.currentText(),
                            "maxPosition": self.max_position_combo.currentText(),
                        },
                    }
                    if language == "FASTEXPR":
                        self._queued_settings["settings"]["unitHandling"] = "VERIFY"
                        self._queued_settings["settings"]["nanHandling"] = self.nan_combo.currentText()
                    self._state = self.STATE_QUEUED
                    self._emit_title(f"⏳ Q{glb_running - 4 + 1}" if glb_running >= 4 else f"⏳ Q{running_weight - max_weight + 1}")
                    self.running_state_changed.emit()
                    self.status_label.setText(T("Queued — waiting for slot..."))
                    self.status_label.setStyleSheet("color: #f9e2af;")
                    self.sim_btn.setEnabled(False)
                    self.tune_btn.setEnabled(False)
                    self._edit_score_btn.setEnabled(False)
                    self.cancel_btn.setEnabled(True)
                    self._update_mode()
                    main_window._enqueue(self)
                    return
            else:
                if running_weight >= max_weight:
                    language = self.language_combo.currentText()
                    self._queued_settings = {
                        "expression": expression,
                        "settings": {
                            "instrumentType": "EQUITY",
                            "region": self.region_combo.currentText(),
                            "universe": self.universe_combo.currentText(),
                            "delay": self.delay_spin.value(),
                            "decay": self._get_decay_single(),
                            "neutralization": self.neutral_combo.currentText(),
                            "truncation": self._get_truncation_single(),
                            "pasteurization": self.pasteur_combo.currentText(),
                            "language": language,
                            "lookback": self.lookback_spin.value(),
                            "visualization": False,
                            "testPeriod": "P0Y0M",
                            "maxTrade": self.max_trade_combo.currentText(),
                            "maxPosition": self.max_position_combo.currentText(),
                        },
                    }
                    if language == "FASTEXPR":
                        self._queued_settings["settings"]["unitHandling"] = "VERIFY"
                        self._queued_settings["settings"]["nanHandling"] = self.nan_combo.currentText()
                    self._state = self.STATE_QUEUED
                    self._emit_title(f"⏳ Q{running_weight - max_weight + 1}")
                    self.running_state_changed.emit()
                    self.status_label.setText(T("Queued — waiting for slot..."))
                    self.status_label.setStyleSheet("color: #f9e2af;")
                    self.sim_btn.setEnabled(False)
                    self.tune_btn.setEnabled(False)
                    self._edit_score_btn.setEnabled(False)
                    self.cancel_btn.setEnabled(True)
                    self._update_mode()
                    main_window._enqueue(self)
                    return

        # Set current region for tracking
        self._current_region = self.region_combo.currentText()

        language = self.language_combo.currentText()
        # PYTHON language should not include unitHandling and nanHandling
        settings = {
            "instrumentType": "EQUITY",
            "region": self.region_combo.currentText(),
            "universe": self.universe_combo.currentText(),
            "delay": self.delay_spin.value(),
            "decay": self._get_decay_single(),
            "neutralization": self.neutral_combo.currentText(),
            "truncation": self._get_truncation_single(),
            "pasteurization": self.pasteur_combo.currentText(),
            "language": language,
            "lookback": self.lookback_spin.value(),
            "visualization": False,
            "testPeriod": "P0Y0M",
            "maxTrade": self.max_trade_combo.currentText(),
            "maxPosition": self.max_position_combo.currentText(),
        }
        # Only add unitHandling and nanHandling for FASTEXPR language
        if language == "FASTEXPR":
            settings["unitHandling"] = "VERIFY"
            settings["nanHandling"] = self.nan_combo.currentText()

        self.metrics_table.setRowCount(0)
        self.yearly_table.setRowCount(0)
        self.pnl_canvas._draw_empty()

        self.sim_btn.setEnabled(False)
        self.tune_btn.setEnabled(False)
        self._edit_score_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(T("Starting simulation..."))

        self._state = self.STATE_RUNNING
        self._emit_title("⏳ 0s")
        self.running_state_changed.emit()
        self._update_mode()

        self.worker = SimulationWorker(self.client, expression, settings)
        self.worker.progress.connect(self._on_progress)
        self.worker.sim_id_ready.connect(self._on_sim_id_ready)
        self.worker.finished.connect(self._on_result)
        self.worker.error.connect(self._on_error)
        self.worker.start()
        self._current_sim_expression = expression
        self._current_sim_settings = settings

        # Start auto-fill timer if enabled
        if hasattr(self, 'auto_fill_check') and self.auto_fill_check.isChecked():
            self._start_auto_fill_timer()

    def _edit_tune_score(self):
        """Open dialog to edit custom get_tune_score function."""
        # Dynamically get default code from common_config.py
        fn = _load_config_value('default_get_tune_score', None)
        if fn is not None:
            try:
                src = inspect.getsource(fn).strip()
                # Replace function name to match what exec expects
                default_code = src.replace('def default_get_tune_score', 'def get_tune_score', 1)
            except Exception:
                default_code = (
                    "def get_tune_score(alpha_data):\n"
                    "    IS = alpha_data.get('is', {})\n"
                    "    fitness = abs(IS.get('fitness', 0))\n"
                    "    margin = abs(IS.get('margin', 0))\n"
                    "    return fitness * 1000000 + margin"
                )
        else:
            default_code = (
                "def get_tune_score(alpha_data):\n"
                "    IS = alpha_data.get('is', {})\n"
                "    fitness = abs(IS.get('fitness', 0))\n"
                "    margin = abs(IS.get('margin', 0))\n"
                "    return fitness * 1000000 + margin"
            )
        current_code = self._custom_tune_score_code if self._custom_tune_score_code else default_code

        dlg = QDialog(self)
        dlg.setWindowTitle(T("Edit get_tune_score"))
        dlg.setMinimumSize(550, 400)
        dlg.setStyleSheet("QDialog { background-color: #1e1e2e; }")

        layout = QVBoxLayout(dlg)

        info = QLabel(T("Define a get_tune_score(alpha_data) → float function.\n"
                       "alpha_data contains 'is' dict with keys like fitness, sharpe, turnover, etc.\n"
                       "Leave as default to reset."))
        info.setStyleSheet("color: #a6adc8; font-size: 20px; padding: 4px;")
        layout.addWidget(info)

        editor = QTextEdit()
        editor.setPlainText(current_code)
        editor.setStyleSheet("""
            QTextEdit {
                background-color: #313244; color: #cdd6f4; font-family: Consolas, monospace;
                font-size: 20px; border: 1px solid #45475a; border-radius: 4px; padding: 6px;
            }
        """)
        layout.addWidget(editor)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        reset_btn = QPushButton(T("Reset to Default"))
        reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #45475a; color: #a6adc8; font-size: 20px;
                border-radius: 4px; padding: 6px 14px; border: none;
            }
            QPushButton:hover { background-color: #585b70; }
        """)
        reset_btn.clicked.connect(lambda: editor.setPlainText(default_code))
        btn_row.addWidget(reset_btn)

        cancel_btn = QPushButton(T("Cancel"))
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #45475a; color: #a6adc8; font-size: 20px;
                border-radius: 4px; padding: 6px 14px; border: none;
            }
            QPushButton:hover { background-color: #585b70; }
        """)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton(T("Apply"))
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #cba6f7; color: #1e1e2e; font-size: 20px;
                font-weight: bold; border-radius: 4px; padding: 6px 14px; border: none;
            }
            QPushButton:hover { background-color: #b4befe; }
        """)
        ok_btn.setDefault(True)

        def _apply():
            code = editor.toPlainText().strip()
            if not code:
                dlg.reject()
                return
            # Validate: try to compile and extract function
            try:
                local_ns = {}
                exec(code, {}, local_ns)
                fn = local_ns.get("get_tune_score")
                if fn is None or not callable(fn):
                    QMessageBox.warning(dlg, T("Error"), T("Code must define a get_tune_score function."))
                    return
                # Quick test with dummy data (include 'checks' for default_get_tune_score)
                test_result = fn({"is": {"fitness": 1.0, "margin": 0.5, "checks": [
                    {"name": "CONCENTRATED_WEIGHT", "result": "PASS"},
                    {"name": "LOW_SUB_UNIVERSE_SHARPE", "result": "PASS"},
                ]}})
                if not isinstance(test_result, (int, float)):
                    QMessageBox.warning(dlg, T("Error"), T("get_tune_score must return a number."))
                    return
            except Exception as e:
                QMessageBox.warning(dlg, T("Code Error"), str(e))
                return
            # Apply
            global _custom_tune_score_fn
            _custom_tune_score_fn = fn
            self._custom_tune_score_code = code
            self._edit_score_btn.setStyleSheet("""
                QPushButton {
                    background-color: #313244; color: #cba6f7; font-size: 10px;
                    font-weight: bold; border-radius: 4px; border: 1px solid #cba6f7;
                    padding: 0px 6px;
                }
                QPushButton:hover { background-color: #45475a; }
            """)
            dlg.accept()

        ok_btn.clicked.connect(_apply)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        dlg.exec_()

    def _on_tune(self):
        """Tune button handler: supports ?N sequential tune, ? cartesian tune, <?N>/<?gN>/<prefix?gN> expression tune."""
        expression = self.expr_input.toPlainText().strip()
        if not expression:
            QMessageBox.warning(self, T("Input Error"), T("Please enter an alpha expression."))
            return

        # Check if user has set any tune placeholders (?N, ?, ?=..., <?>, <?g>, <prefix?gN>, <?=...>, <?g=...>)
        has_numbered = bool(re.search(r'<(?:\w+\s*)?\?(g)?\d+(?:=[^>]*)?>', expression))
        has_unnumbered_expr = bool(re.search(r'<(?:\w+\s*)?\?(g)?(?!\d)(?:=[^>]*)?>', expression))
        has_settings_placeholder = any(
            re.match(r'^\?\d*(?:=.+)?$', t) for t in [
                self.universe_combo.currentText(),
                self.neutral_combo.currentText(),
                self.truncation_input.text().strip(),
                self.decay_input.text().strip(),
            ]
        )

        # If any placeholders exist or expand is checked, use expand mode
        expand_checked = getattr(self, 'tune_expand_check', None) and self.tune_expand_check.isChecked()
        if has_numbered or has_unnumbered_expr or has_settings_placeholder or expand_checked:
            self._on_tune_expand()
            return

        # No placeholders, no expand: default decay tune
        self.decay_input.setText("?")
        self._auto_tune_decay(expression)

    def _parse_tune_slots(self):
        """Parse all tune placeholders (?N) from expression and settings.

        Supports custom value ranges:
          - Settings: ?=1,2,3  ?1=1,2,3
          - Expression: <?=a,b,c>  <?1=a,b,c>  <?g=x,y>  <?g1=x,y>  <sector?g>  <industry?g2=x,y>

        Two mutually exclusive modes:
          - Cartesian product: all unnumbered ? (order=0)
          - Sequential: all numbered ?N (order=N), executed in order

        Mixing numbered and unnumbered is not allowed.

        Returns a list of (order, slot_type, key, values) sorted by order.
        """
        expression = self.expr_input.toPlainText().strip()
        region = self.region_combo.currentText()
        opts = REGION_OPTIONS.get(region, {})

        numbered_slots = []   # ?N slots
        unnumbered_slots = [] # ? slots

        def _parse_custom_values(val_str, is_numeric=True):
            """Parse comma-separated or range values. Returns list of str or list of numbers.

            Supports:
              - Comma-separated: "1,2,3" → [1, 2, 3]
              - Range: "0:100:10" → [0, 10, 20, ..., 100]  (start:end:step)
              - Range with exclusions: "0:100:10:[20,50]" → exclude 20 and 50
            """
            val_str = val_str.strip()

            def _expand_range(start, end, step, excludes=None):
                if step == 0:
                    return [start]
                vals = []
                v = start
                while (step > 0 and v <= end + step * 1e-9) or (step < 0 and v >= end + step * 1e-9):
                    iv = int(v) if v == int(v) else v
                    if excludes is None or iv not in excludes:
                        vals.append(iv)
                    v += step
                return vals

            def _parse_excludes(s):
                """Parse exclusion list like [20,50] → {20, 50}"""
                m = re.match(r'^\[([^\]]+)\]$', s)
                if not m:
                    return None
                items = [x.strip() for x in m.group(1).split(',')]
                result = set()
                for x in items:
                    try:
                        result.add(int(x) if '.' not in x else float(x))
                    except ValueError:
                        pass
                return result

            # Try range format: start:end:step  or  start:end:step:[excludes]
            if is_numeric:
                range_m = re.match(r'^(-?\d+\.?\d*):(-?\d+\.?\d*):(-?\d+\.?\d*)(?::\[([^\]]+)\])?$', val_str)
                if range_m:
                    start = float(range_m.group(1))
                    end = float(range_m.group(2))
                    step = float(range_m.group(3))
                    excludes = _parse_excludes(range_m.group(4)) if range_m.group(4) else None
                    return _expand_range(start, end, step, excludes)
            # Comma-separated
            items = [v.strip() for v in val_str.split(',') if v.strip()]
            if not is_numeric:
                return items
            result = []
            for v in items:
                # Each item can also be a range
                range_m = re.match(r'^(-?\d+\.?\d*):(-?\d+\.?\d*):(-?\d+\.?\d*)(?::\[([^\]]+)\])?$', v)
                if range_m:
                    start = float(range_m.group(1))
                    end = float(range_m.group(2))
                    step = float(range_m.group(3))
                    excludes = _parse_excludes(range_m.group(4)) if range_m.group(4) else None
                    result.extend(_expand_range(start, end, step, excludes))
                    continue
                try:
                    result.append(int(v) if '.' not in v else float(v))
                except ValueError:
                    result.append(v)  # keep as string if not a number
            return result

        # --- Expression placeholders ---
        # Numbered with optional custom and optional prefix default:
        #   <?1>  <?1=a,b,c>  <?g2>  <?g2=x,y>  <sector?g1>  <industry?g2=x,y>
        for m in re.finditer(r'<(?:(\w+)\s*)?\?(g)?(\d+)(?:=([^>]*))?>', expression):
            prefill_val = m.group(1) or None  # prefix default value (e.g. "sector" in <sector?g1>)
            is_glossary = m.group(2) == 'g'
            order = int(m.group(3))
            custom = m.group(4)
            placeholder = m.group(0)
            if custom is not None:
                vals = _parse_custom_values(custom, is_numeric=not is_glossary)
            elif is_glossary:
                vals = list(WHITE_LIST)
                region_neutrals = NEUTRALIZATION_DICT.get(region, [])
                if "COUNTRY" not in region_neutrals:
                    vals = [v for v in vals if v != "country"]
            else:
                vals = _load_config_value('DEFAULT_VALUES', DEFAULT_VALUES)
            # Exclude prefill_val from tune values (already simulated as default)
            if prefill_val is not None:
                if is_glossary:
                    vals = [v for v in vals if v != prefill_val]
                else:
                    try:
                        pv = int(prefill_val) if '.' not in prefill_val else float(prefill_val)
                        vals = [v for v in vals if v != pv]
                    except ValueError:
                        vals = [v for v in vals if v != prefill_val]
            numbered_slots.append((order, 'expression', placeholder, vals, prefill_val))

        # Unnumbered with optional custom and optional prefix default:
        #   <?>  <?=a,b,c>  <?g>  <?g=x,y>  <sector?g>  <industry?g=market,country>
        for m in re.finditer(r'<(?:(\w+)\s*)?\?(g)?(?!\d)(?:=([^>]*))?>', expression):
            prefill_val = m.group(1) or None
            is_glossary = m.group(2) == 'g'
            custom = m.group(3)
            placeholder = m.group(0)
            if custom is not None:
                vals = _parse_custom_values(custom, is_numeric=not is_glossary)
            elif is_glossary:
                vals = list(WHITE_LIST)
                region_neutrals = NEUTRALIZATION_DICT.get(region, [])
                if "COUNTRY" not in region_neutrals:
                    vals = [v for v in vals if v != "country"]
            else:
                vals = _load_config_value('DEFAULT_VALUES', DEFAULT_VALUES)
            # Exclude prefill_val from tune values
            if prefill_val is not None:
                if is_glossary:
                    vals = [v for v in vals if v != prefill_val]
                else:
                    try:
                        pv = int(prefill_val) if '.' not in prefill_val else float(prefill_val)
                        vals = [v for v in vals if v != pv]
                    except ValueError:
                        vals = [v for v in vals if v != prefill_val]
            unnumbered_slots.append((0, 'expression', placeholder, vals, prefill_val))

        # --- Expression range placeholders: <start:end:step> or <start:end:step:[excludes]> ---
        # e.g. <0:100:10> → [0, 10, 20, ..., 100]
        # e.g. <0:100:10:[20,50]> → [0, 10, 30, 40, 60, ..., 100]
        for m in re.finditer(r'<(-?\d+\.?\d*):(-?\d+\.?\d*):(-?\d+\.?\d*)(?::\[([^\]]+)\])?>', expression):
            placeholder = m.group(0)
            # Skip if this position was already matched by <?...> or <?g...>
            if any(s[2] == placeholder for s in numbered_slots) or any(s[2] == placeholder for s in unnumbered_slots):
                continue
            start = float(m.group(1))
            end = float(m.group(2))
            step = float(m.group(3))
            excludes = None
            if m.group(4):
                excludes = set()
                for x in m.group(4).split(','):
                    try:
                        excludes.add(int(x.strip()) if '.' not in x.strip() else float(x.strip()))
                    except ValueError:
                        pass
            if step == 0:
                vals = [int(start) if start == int(start) else start]
            else:
                vals = []
                v = start
                while (step > 0 and v <= end + step * 1e-9) or (step < 0 and v >= end + step * 1e-9):
                    iv = int(v) if v == int(v) else v
                    if excludes is None or iv not in excludes:
                        vals.append(iv)
                    v += step
            unnumbered_slots.append((0, 'expression', placeholder, vals, None))

        # --- Settings placeholders ---
        settings_map = {
            'decay': ('decay', lambda: _load_config_value('COARSE_DECAYS', COARSE_DECAYS)),
            'universe': ('universe', lambda: [u for u in opts.get("universes", []) if u not in opts.get("skip_universes", [])]),
            'neutralization': ('neutralization', lambda: [n for n in opts.get("neutralizations", ["NONE"]) if n != "NONE"]),
            'truncation': ('truncation', lambda: _load_config_value('DEFAULT_TRUNCS', DEFAULT_TRUNCS)),
        }

        for key, (settings_key, get_values) in settings_map.items():
            if key == 'decay':
                text = self.decay_input.text().strip()
            elif key == 'truncation':
                text = self.truncation_input.text().strip()
            elif key == 'universe':
                text = self.universe_combo.currentText().strip()
            elif key == 'neutralization':
                text = self.neutral_combo.currentText().strip()
            else:
                continue

            # Numbered with optional custom: ?1  ?1=1,2,3  or  value?1  value?1=1,2,3
            m = re.match(r'^([^\?]+)\?(\d+)(?:=(.+))?$', text)
            if m:
                prefill_raw = m.group(1).strip()
                order = int(m.group(2))
                custom = m.group(3)
                vals = _parse_custom_values(custom) if custom is not None else get_values()
                # Parse prefill value
                try:
                    prefill_val = int(prefill_raw) if '.' not in prefill_raw else float(prefill_raw)
                except ValueError:
                    prefill_val = prefill_raw
                # Remove prefill_val from tune values (already simulated)
                vals = [v for v in vals if v != prefill_val]
                numbered_slots.append((order, 'settings', settings_key, vals, prefill_val))
            else:
                m = re.match(r'^\?(\d+)(?:=(.+))?$', text)
                if m:
                    order = int(m.group(1))
                    custom = m.group(2)
                    vals = _parse_custom_values(custom) if custom is not None else get_values()
                    numbered_slots.append((order, 'settings', settings_key, vals, None))
                # Unnumbered with optional custom: ?  ?=1,2,3
                elif re.match(r'^\?(?:=(.+))?$', text):
                    m2 = re.match(r'^\?(?:=(.+))?$', text)
                    custom = m2.group(1)
                    vals = _parse_custom_values(custom) if custom is not None else get_values()
                    unnumbered_slots.append((0, 'settings', settings_key, vals, None))

        # Mixing check: if both numbered and unnumbered exist, that's an error
        if numbered_slots and unnumbered_slots:
            QMessageBox.warning(self, T("Tune Error"),
                "Cannot mix numbered (?1, ?2, ...) and unnumbered (?) placeholders.\n"
                "Use all numbered for sequential tune, or all unnumbered for cartesian product.")
            return None

        # If nothing found, default to decay cartesian tune
        if not numbered_slots and not unnumbered_slots:
            self.decay_input.setText("?")
            unnumbered_slots.append((0, 'settings', 'decay', _load_config_value('COARSE_DECAYS', COARSE_DECAYS), None))

        # Sort by order
        slots = (numbered_slots if numbered_slots else unnumbered_slots)
        slots.sort(key=lambda x: x[0])

        return slots

    def _apply_tune_value(self, slot_type, key, value, expression=None, prefill_val=None):
        """Apply a single tune value to this tab. Returns updated expression if applicable.
        For expression slots, key is the full placeholder (e.g. <sector?g2>),
        prefill_val is the default value inside the placeholder (e.g. sector).
        The placeholder is replaced entirely with the new value.
        """
        if slot_type == 'expression':
            if expression is not None:
                # key is the full placeholder like <sector?g2>, replace with value
                return expression.replace(key, str(value), 1)
        elif slot_type == 'settings':
            if key == 'decay':
                self._set_decay_value(value)
            elif key == 'universe':
                self.universe_combo.setCurrentText(str(value))
            elif key == 'neutralization':
                self.neutral_combo.setCurrentText(str(value))
            elif key == 'truncation':
                self._set_truncation_value(value)
        return expression

    def _on_tune_expand(self):
        """Tune with expand mode: supports sequential (?1,?2,...) and cartesian (?) tune."""
        main_window = self.window()
        if not isinstance(main_window, MainWindow):
            return

        slots = self._parse_tune_slots()
        if not slots:
            return  # _parse_tune_slots already showed error dialog

        # Group slots by order: same order = cartesian product, different order = sequential
        ordered_groups = []
        for order, group in groupby(slots, key=lambda x: x[0]):
            ordered_groups.append((order, list(group)))

        # Assign a tune batch id for visual grouping (same color for same tune chain)
        main_window._next_batch_id += 1
        tune_batch_id = main_window._next_batch_id

        # Start the sequential tune chain from the first group
        self._tune_chain = {
            'groups': ordered_groups,
            'current_group_idx': 0,
            'best_score': -float('inf'),
            'best_tab': None,
            'best_expressions': {},  # order -> best expression after that stage
            'best_settings': {},     # order -> {key: value} after that stage
            'tune_batch_id': tune_batch_id,
        }
        self._run_tune_group()

    def _run_tune_group(self):
        """Run one group of tune slots (cartesian product of all slots in this group)."""
        chain = self._tune_chain
        group_idx = chain['current_group_idx']
        if group_idx >= len(chain['groups']):
            # All groups done — move best tab to leftmost
            self._finalize_tune_chain()
            return

        order, group = chain['groups'][group_idx]

        # Collect prefill values from all slots (including future groups)
        # These values should be applied as fixed settings before this group runs
        prefill_settings = {}
        all_slots = [slot for _, grp in chain['groups'] for slot in grp]
        for slot_order, slot_type, key, vals, prefill_val in all_slots:
            if prefill_val is not None and slot_type == 'settings':
                prefill_settings[key] = prefill_val

        # Build the cartesian product of all slots in this group
        value_lists = [slot[3] for slot in group]  # list of value lists
        combos = list(product(*value_lists))

        expression = self.expr_input.toPlainText().strip()
        # Apply best values from previous groups
        for prev_order, prev_expr in chain['best_expressions'].items():
            expression = prev_expr
        for prev_order, prev_settings in chain['best_settings'].items():
            for key, val in prev_settings.items():
                self._apply_tune_value('settings', key, val, expression)

        main_window = self.window()
        original_state = self.get_state()

        # Reset tracking for this group
        chain['group_best_score'] = -float('inf')
        chain['group_best_tab'] = None
        chain['group_best_combo'] = None
        chain['group_tabs'] = []
        chain['group_total'] = len(combos)
        chain['group_finished'] = 0

        # Create tabs for each combo
        for i, combo in enumerate(combos):
            if i == 0:
                tab = self
            else:
                main_window._add_tab(original_state)
                tab = main_window.tab_widget.widget(main_window.tab_widget.count() - 1)

            if isinstance(tab, SimulateTab):
                tab._tune_expand = True
                tab._tune_group_idx = group_idx
                tab._tune_batch_id = chain['tune_batch_id']

                # Apply prefill values (fixed values from value?n syntax)
                tab_expr = expression
                for pf_key, pf_val in prefill_settings.items():
                    tab._apply_tune_value('settings', pf_key, pf_val, tab_expr)

                # Apply all values in this combo and build label
                tune_labels = []
                for j, (slot_order, slot_type, key, vals, prefill_val) in enumerate(group):
                    val = combo[j]
                    tab_expr = tab._apply_tune_value(slot_type, key, val, tab_expr, prefill_val)
                    # Build short label for tab title
                    if slot_type == 'settings':
                        if key == 'decay':
                            tune_labels.append(f"d={val}")
                        elif key == 'truncation':
                            tune_labels.append(f"t={val}")
                        elif key == 'universe':
                            tune_labels.append(f"u={val}")
                        elif key == 'neutralization':
                            tune_labels.append(f"n={val}")
                    elif slot_type == 'expression':
                        tune_labels.append(f"e={val}")

                # Set tab base name to show tune info
                group_label = f"G{group_idx + 1}"
                combo_label = ",".join(tune_labels) if tune_labels else f"#{i + 1}"
                tab.set_tab_base_name(f"T{chain['tune_batch_id']} {group_label} {combo_label}")

                if tab_expr is not None:
                    tab.expr_input.setPlainText(tab_expr)

                # Expand settings
                tab.settings_toggle_btn.setChecked(True)
                tab.settings_group.setVisible(True)

                # Connect sim_finished
                tab.sim_finished.connect(lambda t=tab: self._on_tune_group_finished(t))

                chain['group_tabs'].append(tab)

        # Start simulations
        for i, tab in enumerate(chain['group_tabs']):
            if i == 0:
                tab._on_simulate()
            else:
                QTimer.singleShot(i * 200, lambda t=tab: t._on_simulate())

    def _on_tune_group_finished(self, tab):
        """Called when a tab in the current tune group finishes."""
        if not getattr(tab, '_tune_expand', False):
            return

        chain = getattr(self, '_tune_chain', None)
        if chain is None:
            return

        main_window = self.window()
        if not isinstance(main_window, MainWindow):
            return

        # Get score
        alpha = getattr(tab, '_last_alpha', None)
        score = get_tune_score(alpha) if alpha else -float('inf')
        chain['group_finished'] += 1

        if score > chain['group_best_score']:
            chain['group_best_score'] = score
            chain['group_best_tab'] = tab
            # Save the expression and settings from this tab as best for this group
            chain['best_expressions'][chain['current_group_idx']] = tab.expr_input.toPlainText().strip()
            best_settings = {}
            # Extract the specific settings values that were tuned in this group
            group = chain['groups'][chain['current_group_idx']][1]
            for slot_order, slot_type, key, vals, prefill_val in group:
                if slot_type == 'settings':
                    if key == 'decay':
                        best_settings[key] = tab.decay_input.text().strip()
                    elif key == 'truncation':
                        best_settings[key] = tab.truncation_input.text().strip()
                    elif key == 'universe':
                        best_settings[key] = tab.universe_combo.currentText()
                    elif key == 'neutralization':
                        best_settings[key] = tab.neutral_combo.currentText()
            chain['best_settings'][chain['current_group_idx']] = best_settings

        # Track global best
        if score > chain['best_score']:
            chain['best_score'] = score
            chain['best_tab'] = tab

        # Check if all tabs in this group are done
        all_done = True
        for t in chain['group_tabs']:
            if isinstance(t, SimulateTab) and t._state in (t.STATE_RUNNING, t.STATE_QUEUED):
                all_done = False
                break

        if all_done:
            # Move best tab of this group to leftmost, then start next group
            best_tab = chain['group_best_tab']
            if best_tab:
                best_idx = main_window.tab_widget.indexOf(best_tab)
                if best_idx > 0:
                    title = main_window.tab_widget.tabText(best_idx)
                    main_window.tab_widget.removeTab(best_idx)
                    main_window.tab_widget.insertTab(0, best_tab, title)
                    main_window.tab_widget.setCurrentIndex(0)

            # Move to next group
            chain['current_group_idx'] += 1
            # Apply best values to self (the initiator tab) before running next group
            if chain['current_group_idx'] < len(chain['groups']):
                best_expr = chain['best_expressions'].get(chain['current_group_idx'] - 1)
                if best_expr:
                    self.expr_input.setPlainText(best_expr)
                best_settings = chain['best_settings'].get(chain['current_group_idx'] - 1, {})
                for key, val in best_settings.items():
                    self._apply_tune_value('settings', key, val)
                QTimer.singleShot(500, self._run_tune_group)

    def _finalize_tune_chain(self):
        """All tune groups done — move the overall best tab to leftmost."""
        chain = getattr(self, '_tune_chain', None)
        if chain is None:
            return
        best_tab = chain.get('best_tab')
        if best_tab:
            main_window = self.window()
            if isinstance(main_window, MainWindow):
                best_idx = main_window.tab_widget.indexOf(best_tab)
                if best_idx > 0:
                    title = main_window.tab_widget.tabText(best_idx)
                    main_window.tab_widget.removeTab(best_idx)
                    main_window.tab_widget.insertTab(0, best_tab, title)
                    main_window.tab_widget.setCurrentIndex(0)
        # Cleanup
        self._tune_chain = None

    def _on_tune_expand_finished(self, tab):
        """Called when a tune expand tab finishes simulation."""
        if not getattr(tab, '_tune_expand', False):
            return

        main_window = self.window()
        if not isinstance(main_window, MainWindow):
            return

        # Get score from the alpha data saved in _on_result
        alpha = getattr(tab, '_last_alpha', None)
        score = get_tune_score(alpha) if alpha else -float('inf')
        print(f"[TuneExpand] Tab finished: tune_value={getattr(tab, '_tune_value', '?')}, score={score:.4f}, best_so_far={self._tune_best_score:.4f}", flush=True)

        # Use the initiating tab (self) as the parent to track best score
        if score > self._tune_best_score:
            self._tune_best_score = score
            self._tune_best_tab = tab
            print(f"[TuneExpand] New best! score={score:.4f}, tune_value={getattr(tab, '_tune_value', '?')}", flush=True)

        # Check if all tabs in this tune group are done
        all_done = True
        running_count = 0
        for i in range(main_window.tab_widget.count()):
            t = main_window.tab_widget.widget(i)
            if isinstance(t, SimulateTab) and getattr(t, '_tune_expand', False) and getattr(t, '_tune_type', None) == self._tune_type:
                if t._state in (t.STATE_RUNNING, t.STATE_QUEUED):
                    all_done = False
                    running_count += 1

        print(f"[TuneExpand] all_done={all_done}, running={running_count}, best_tab={self._tune_best_tab is not None}", flush=True)

        if all_done and self._tune_best_tab:
            # Move best tab to leftmost position
            best_tab = self._tune_best_tab
            best_idx = main_window.tab_widget.indexOf(best_tab)
            print(f"[TuneExpand] Moving best tab (score={self._tune_best_score:.4f}) from idx={best_idx} to 0", flush=True)
            if best_idx > 0:
                title = main_window.tab_widget.tabText(best_idx)
                main_window.tab_widget.removeTab(best_idx)
                main_window.tab_widget.insertTab(0, best_tab, title)
                main_window.tab_widget.setCurrentIndex(0)

    def _auto_tune_param(self, param_name):
        """Auto-tune universe or neutralization to maximize get_tune_score() when combo is '?'."""
        expression = self.expr_input.toPlainText().strip()
        if not self.client.is_authenticated():
            QMessageBox.warning(self, T("Not Authenticated"), T("Please login first."))
            return

        region = self.region_combo.currentText()
        opts = REGION_OPTIONS.get(region, {})

        if param_name == "universe":
            all_values = opts.get("universes", [])
            skip = opts.get("skip_universes", [])
            param_values = [u for u in all_values if u not in skip]
            if not param_values:
                QMessageBox.warning(self, T("No Options"), T("No valid universes to tune."))
                return
        elif param_name == "neutralization":
            all_values = opts.get("neutralizations", ["NONE"])
            # Skip NONE for neutralization tuning
            param_values = [n for n in all_values if n != "NONE"]
            if not param_values:
                QMessageBox.warning(self, T("No Options"), T("No valid neutralizations to tune."))
                return
        elif param_name == "truncation":
            # Default truncation search grid
            param_values = _load_config_value('DEFAULT_TRUNCS', DEFAULT_TRUNCS)
        else:
            return

        main_window = self.window()
        is_glb = region == "GLB"
        max_weight = 8
        if isinstance(main_window, MainWindow):
            max_weight = main_window._get_max_running_weight()

        self._current_region = region
        language = self.language_combo.currentText()
        settings = {
            "instrumentType": "EQUITY",
            "region": region,
            "universe": self.universe_combo.currentText(),
            "delay": self.delay_spin.value(),
            "decay": self._get_decay_single(),
            "neutralization": self.neutral_combo.currentText(),
            "truncation": self._get_truncation_single(),
            "pasteurization": self.pasteur_combo.currentText(),
            "language": language,
            "lookback": self.lookback_spin.value(),
            "visualization": False,
            "testPeriod": "P0Y0M",
            "maxTrade": self.max_trade_combo.currentText(),
            "maxPosition": self.max_position_combo.currentText(),
        }
        if language == "FASTEXPR":
            settings["unitHandling"] = "VERIFY"
            settings["nanHandling"] = self.nan_combo.currentText()

        self.metrics_table.setRowCount(0)
        self.yearly_table.setRowCount(0)
        self.pnl_canvas._draw_empty()
        self.sim_btn.setEnabled(False)
        self.tune_btn.setEnabled(False)
        self._edit_score_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(f"{T('Auto-tuning ')}{param_name}{T('...')}")
        self.status_label.setStyleSheet("color: #cba6f7;")

        self._state = self.STATE_RUNNING
        self._tune_active_count = 0
        self._emit_title("⏳ 🔍")
        self.running_state_changed.emit()
        self._update_mode()

        self._param_tune_worker = AutoTuneWorker(
            self.client, expression, settings, param_name, param_values,
            max_concurrent=max_weight, is_glb=is_glb)
        self._param_tune_worker.progress.connect(self._on_param_tune_progress)
        self._param_tune_worker.finished.connect(self._on_param_tune_result)
        self._param_tune_worker.error.connect(self._on_error)
        self._param_tune_worker.active_count.connect(self._on_tune_active_count)
        self._param_tune_worker.start()

    def _on_param_tune_progress(self, idx, total, param_val, status):
        self.status_label.setText(status)
        self._emit_title(f"⏳ 🔍 {param_val}")

    def _on_param_tune_result(self, result, best_param):
        """Called when auto-tune finishes with the best result."""
        self._tune_active_count = 0
        # Update combo/input to the best value found
        if self.universe_combo.currentText() == "?":
            self.universe_combo.setCurrentText(best_param)
        elif self.neutral_combo.currentText() == "?":
            self.neutral_combo.setCurrentText(best_param)
        elif self.truncation_input.text().strip() == "?":
            self._set_truncation_value(best_param)
        # Treat as a normal simulation result
        self._on_result(result)

    def _auto_tune_decay(self, expression):
        """Auto-tune decay to maximize get_tune_score() when decay input is '?'."""
        if not self.client.is_authenticated():
            QMessageBox.warning(self, T("Not Authenticated"), T("Please login first."))
            return

        main_window = self.window()
        is_glb = self.region_combo.currentText() == "GLB"
        max_weight = 8
        if isinstance(main_window, MainWindow):
            max_weight = main_window._get_max_running_weight()

        self._current_region = self.region_combo.currentText()
        language = self.language_combo.currentText()
        settings = {
            "instrumentType": "EQUITY",
            "region": self.region_combo.currentText(),
            "universe": self.universe_combo.currentText(),
            "delay": self.delay_spin.value(),
            "decay": 0,  # placeholder, worker will override
            "neutralization": self.neutral_combo.currentText(),
            "truncation": self._get_truncation_single(),
            "pasteurization": self.pasteur_combo.currentText(),
            "language": language,
            "lookback": self.lookback_spin.value(),
            "visualization": False,
            "testPeriod": "P0Y0M",
            "maxTrade": self.max_trade_combo.currentText(),
            "maxPosition": self.max_position_combo.currentText(),
        }
        if language == "FASTEXPR":
            settings["unitHandling"] = "VERIFY"
            settings["nanHandling"] = self.nan_combo.currentText()

        self.metrics_table.setRowCount(0)
        self.yearly_table.setRowCount(0)
        self.pnl_canvas._draw_empty()
        self.sim_btn.setEnabled(False)
        self.tune_btn.setEnabled(False)
        self._edit_score_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(T("Auto-tuning decay..."))
        self.status_label.setStyleSheet("color: #cba6f7;")

        self._state = self.STATE_RUNNING
        self._tune_active_count = 0
        self._emit_title("⏳ 🔍")
        self.running_state_changed.emit()
        self._update_mode()

        self._decay_tune_worker = DecayAutoTuneWorker(
            self.client, expression, settings,
            max_concurrent=max_weight, is_glb=is_glb)
        self._decay_tune_worker.progress.connect(self._on_decay_tune_progress)
        self._decay_tune_worker.finished.connect(self._on_decay_tune_result)
        self._decay_tune_worker.error.connect(self._on_error)
        self._decay_tune_worker.active_count.connect(self._on_tune_active_count)
        self._decay_tune_worker.start()

    def _on_decay_tune_progress(self, idx, total, decay_val, status):
        self.status_label.setText(status)
        self._emit_title(f"⏳ 🔍 d={int(decay_val)}")

    def _on_tune_active_count(self, count):
        """Update tune active count and notify MainWindow to refresh Running display."""
        self._tune_active_count = count
        main_window = self.window()
        if isinstance(main_window, MainWindow):
            main_window._update_running_count()

    def _on_decay_tune_result(self, result, best_decay):
        """Called when auto-tune finishes with the best result."""
        self._tune_active_count = 0
        # Update decay input to the best value found
        self._set_decay_value(best_decay)
        # Treat as a normal simulation result
        self._on_result(result)

    def _auto_tune_expression(self, expression):
        """Auto-tune expression by replacing <?> with numeric values."""
        if not self.client.is_authenticated():
            QMessageBox.warning(self, T("Not Authenticated"), T("Please login first."))
            return

        main_window = self.window()
        is_glb = self.region_combo.currentText() == "GLB"
        max_weight = 8
        if isinstance(main_window, MainWindow):
            max_weight = main_window._get_max_running_weight()

        self._current_region = self.region_combo.currentText()
        language = self.language_combo.currentText()
        settings = {
            "instrumentType": "EQUITY",
            "region": self.region_combo.currentText(),
            "universe": self.universe_combo.currentText(),
            "delay": self.delay_spin.value(),
            "decay": self._get_decay_single(),
            "neutralization": self.neutral_combo.currentText(),
            "truncation": self._get_truncation_single(),
            "pasteurization": self.pasteur_combo.currentText(),
            "language": language,
            "lookback": self.lookback_spin.value(),
            "visualization": False,
            "testPeriod": "P0Y0M",
            "maxTrade": self.max_trade_combo.currentText(),
            "maxPosition": self.max_position_combo.currentText(),
        }
        if language == "FASTEXPR":
            settings["unitHandling"] = "VERIFY"
            settings["nanHandling"] = self.nan_combo.currentText()

        self.metrics_table.setRowCount(0)
        self.yearly_table.setRowCount(0)
        self.pnl_canvas._draw_empty()
        self.sim_btn.setEnabled(False)
        self.tune_btn.setEnabled(False)
        self._edit_score_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(T("Auto-tuning expression..."))
        self.status_label.setStyleSheet("color: #cba6f7;")

        self._state = self.STATE_RUNNING
        self._tune_active_count = 0
        self._emit_title("⏳ 🔍")
        self.running_state_changed.emit()
        self._update_mode()

        self._expr_tune_worker = ExpressionAutoTuneWorker(
            self.client, expression, settings,
            max_concurrent=max_weight, is_glb=is_glb, region=self.region_combo.currentText())
        self._expr_tune_worker.progress.connect(self._on_expr_tune_progress)
        self._expr_tune_worker.finished.connect(self._on_expr_tune_result)
        self._expr_tune_worker.error.connect(self._on_error)
        self._expr_tune_worker.active_count.connect(self._on_tune_active_count)
        self._expr_tune_worker.start()

    def _on_expr_tune_progress(self, idx, total, expr_val, status):
        self.status_label.setText(status)
        self._emit_title(f"⏳ 🔍 expr={expr_val}")

    def _on_expr_tune_result(self, result, best_expr):
        """Called when expression auto-tune finishes with the best result."""
        self._tune_active_count = 0
        # Update expression to the best one found
        self.expr_input.setPlainText(best_expr)
        # Treat as a normal simulation result
        self._on_result(result)

    def _start_auto_fill_timer(self):
        """Start timer to check if auto-fill is needed."""
        if not hasattr(self, '_auto_fill_timer'):
            self._auto_fill_timer = QTimer(self)
            self._auto_fill_timer.timeout.connect(self._check_auto_fill)
        self._auto_fill_timer.start(2000)  # Check every 2 seconds

    def _stop_auto_fill_timer(self):
        """Stop the auto-fill timer."""
        if hasattr(self, '_auto_fill_timer') and self._auto_fill_timer.isActive():
            self._auto_fill_timer.stop()

    def _check_auto_fill(self):
        """Check if auto-fill is needed and fill slowest tabs."""
        main_window = self.window()
        if not isinstance(main_window, MainWindow):
            return

        # Calculate current weighted slots
        running_weight = 0
        running_tabs = []
        for i in range(main_window.tab_widget.count()):
            tab = main_window.tab_widget.widget(i)
            if isinstance(tab, SimulateTab) and tab._state == SimulateTab.STATE_RUNNING:
                running_tabs.append(tab)
                tab_region = getattr(tab, '_current_region', None)
                if tab_region == "GLB":
                    running_weight += 2
                else:
                    running_weight += 1

        max_weight = main_window._get_max_running_weight()
        if running_weight >= max_weight:
            return  # Already at max slots

        # Find tabs with progress < 50%
        low_progress_tabs = []
        for tab in running_tabs:
            progress = getattr(tab, '_current_progress_pct', 0)
            elapsed = getattr(tab, '_current_elapsed', 0)
            if progress < 50:
                low_progress_tabs.append((tab, progress, elapsed))

        if not low_progress_tabs:
            return

        # Sort by progress (ascending), then by elapsed time (ascending)
        low_progress_tabs.sort(key=lambda x: (x[1], x[2]))

        # Get current tab's region for slot cost calculation
        current_region = self.region_combo.currentText()
        slot_cost = 2 if current_region == "GLB" else 1

        # Fill up to max weight
        for tab_to_clone in low_progress_tabs:
            if running_weight >= max_weight:
                break
            main_window._add_tab(tab_to_clone.get_state())
            new_tab = main_window.tab_widget.widget(main_window.tab_widget.count() - 1)
            if isinstance(new_tab, SimulateTab):
                QTimer.singleShot(100, new_tab._on_simulate)
            running_weight += slot_cost

    def _on_sim_id_ready(self, sim_id):
        self._sim_id_label.setText(f"{T('Sim: ')}{sim_id}")
        self._sim_id_label.setVisible(True)
        self._sim_url_btn.setVisible(True)

    def _start_queued(self):
        """Start the queued simulation."""
        if self._state != self.STATE_QUEUED or not self._queued_settings:
            return
        expression = self._queued_settings["expression"]
        settings = self._queued_settings["settings"]
        self._queued_settings = None

        self.metrics_table.setRowCount(0)
        self.yearly_table.setRowCount(0)
        self.pnl_canvas._draw_empty()

        self.sim_btn.setEnabled(False)
        self.tune_btn.setEnabled(False)
        self._edit_score_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText(T("Starting simulation..."))
        self.status_label.setStyleSheet("")

        self._state = self.STATE_RUNNING
        self._emit_title("⏳ 0s")
        self.running_state_changed.emit()
        self._update_mode()

        self.worker = SimulationWorker(self.client, expression, settings)
        self.worker.progress.connect(self._on_progress)
        self.worker.sim_id_ready.connect(self._on_sim_id_ready)
        self.worker.finished.connect(self._on_result)
        self.worker.error.connect(self._on_error)
        self.worker.start()
        self._current_sim_expression = expression
        self._current_sim_settings = settings

    def _dequeue_cancel(self):
        """Cancel a queued simulation."""
        self._queued_settings = None
        self._reset_ui()
        self._state = self.STATE_IDLE
        self._emit_title()
        self.running_state_changed.emit()
        self.status_label.setText(T("Cancelled (queued)"))
        self._update_mode()
        main_window = self.window()
        if isinstance(main_window, MainWindow):
            main_window._dequeue(self)

    def _on_cancel(self):
        if self._state == self.STATE_QUEUED:
            self._dequeue_cancel()
            return
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.quit()
            self.worker.wait(3000)
        if hasattr(self, '_decay_tune_worker') and self._decay_tune_worker and self._decay_tune_worker.isRunning():
            self._decay_tune_worker.cancel()
            self._decay_tune_worker.quit()
            self._decay_tune_worker.wait(3000)
        if hasattr(self, '_param_tune_worker') and self._param_tune_worker and self._param_tune_worker.isRunning():
            self._param_tune_worker.cancel()
            self._param_tune_worker.quit()
            self._param_tune_worker.wait(3000)
        if hasattr(self, '_expr_tune_worker') and self._expr_tune_worker and self._expr_tune_worker.isRunning():
            self._expr_tune_worker.cancel()
            self._expr_tune_worker.quit()
            self._expr_tune_worker.wait(3000)
        self._stop_auto_fill_timer()
        self._reset_ui()
        self._tune_active_count = 0
        self._sim_id_label.setVisible(False)
        self._sim_url_btn.setVisible(False)
        self._state = self.STATE_IDLE
        self._emit_title()
        self.running_state_changed.emit()
        self.status_label.setText(T("Cancelled"))
        self._update_mode()
        # Restore edit-mode widgets
        self.expr_input.setReadOnly(False)
        self._vscode_btn.setEnabled(True)
        for combo in [self.region_combo, self.universe_combo, self.neutral_combo]:
            combo.setEnabled(True)
        self.delay_spin.setEnabled(True)
        self.decay_input.setEnabled(True)
        self.truncation_input.setEnabled(True)
        self.pasteur_combo.setEnabled(True)
        self.nan_combo.setEnabled(True)
        self.max_trade_combo.setEnabled(True)
        self.max_position_combo.setEnabled(True)
        self.language_combo.setEnabled(True)
        self.lookback_spin.setEnabled(True)

    def _on_progress(self, elapsed, status, progress_pct):
        self.status_label.setText(f"[{elapsed:.0f}s] {status}")
        # Store progress for auto-fill
        self._current_elapsed = elapsed
        if progress_pct >= 0:
            pct_int = int(progress_pct * 100)
            self._current_progress_pct = pct_int
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(pct_int)
            self._emit_title(f"⏳ {pct_int}% {elapsed:.0f}s")
        else:
            self._current_progress_pct = 0
            self.progress_bar.setRange(0, 0)
            self._emit_title(f"⏳ {elapsed:.0f}s")
        # Auto-retry: if simulation exceeds 9 minutes, cancel and restart
        if elapsed >= 540 and self._state == self.STATE_RUNNING:
            self._auto_retry_sim()

    def _auto_retry_sim(self):
        """Cancel the current simulation (timed out at 9 min) and restart it."""
        if self._state != self.STATE_RUNNING:
            return
        # Cancel current worker
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.quit()
            self.worker.wait(3000)
        self._stop_auto_fill_timer()
        # Restart with saved expression and settings
        expression = getattr(self, '_current_sim_expression', None)
        settings = getattr(self, '_current_sim_settings', None)
        if not expression or not settings:
            self._reset_ui()
            self._state = self.STATE_IDLE
            self._emit_title()
            self.running_state_changed.emit()
            self.sim_finished.emit(self)
            self._set_status(f"{T('Error: ')}timeout (9 min), no saved settings to retry", "#f38ba8")
            self._update_mode()
            return
        self.metrics_table.setRowCount(0)
        self.yearly_table.setRowCount(0)
        self.pnl_canvas._draw_empty()
        self.status_label.setText(T("Starting simulation..."))
        self.status_label.setStyleSheet("color: #f9e2af;")
        self._state = self.STATE_RUNNING
        self._emit_title("⏳ 0s ↻")
        self.running_state_changed.emit()
        self._update_mode()
        self.worker = SimulationWorker(self.client, expression, settings)
        self.worker.progress.connect(self._on_progress)
        self.worker.sim_id_ready.connect(self._on_sim_id_ready)
        self.worker.finished.connect(self._on_result)
        self.worker.error.connect(self._on_error)
        self.worker.start()
        if hasattr(self, 'auto_fill_check') and self.auto_fill_check.isChecked():
            self._start_auto_fill_timer()

    def _on_error(self, msg):
        if self._state != self.STATE_RUNNING:
            return
        self._stop_auto_fill_timer()
        self._reset_ui()
        self._tune_active_count = 0
        self._sim_id_label.setVisible(False)
        self._sim_url_btn.setVisible(False)
        self._state = self.STATE_IDLE
        self._emit_title()
        self.running_state_changed.emit()
        self.sim_finished.emit(self)
        self._set_status(f"{T('Error: ')}{msg}", "#f38ba8")
        self._update_mode()
        # Restore edit-mode widgets
        self.expr_input.setReadOnly(False)
        self._vscode_btn.setEnabled(True)
        for combo in [self.region_combo, self.universe_combo, self.neutral_combo]:
            combo.setEnabled(True)
        self.delay_spin.setEnabled(True)
        self.decay_input.setEnabled(True)
        self.truncation_input.setEnabled(True)
        self.pasteur_combo.setEnabled(True)
        self.nan_combo.setEnabled(True)
        self.max_trade_combo.setEnabled(True)
        self.max_position_combo.setEnabled(True)
        self.language_combo.setEnabled(True)
        self.lookback_spin.setEnabled(True)
        if "cancelled by user" not in msg.lower():
            err_box = QMessageBox(self)
            err_box.setIcon(QMessageBox.Critical)
            err_box.setWindowTitle(T("Simulation Error"))
            err_box.setText(T("Simulation failed"))
            err_box.setDetailedText(msg)
            err_box.setStyleSheet("""
                QMessageBox { background: #313244; color: #cdd6f4; }
                QLabel { color: #cdd6f4; }
                QPushButton { background: #45475a; color: #cdd6f4; border-radius: 4px; padding: 4px 16px; }
                QPushButton:hover { background: #585b70; }
                QTextEdit { background: #181825; color: #f38ba8; font-family: Consolas; font-size: 10pt; }
            """)
            err_box.exec_()

    def _set_status(self, text, color="#cdd6f4"):
        """设置 status_label 文本和颜色，错误时显示复制按钮。"""
        self.status_label.setText(text)
        is_error = color == "#f38ba8"
        self._copy_error_btn.setVisible(is_error and bool(text))
        self.status_label.setStyleSheet(f"""
            QTextEdit {{
                background: #1e1e2e; color: {color}; border: none;
                font-size: 11pt; padding: 2px 4px;
            }}
        """)

    def _copy_error_to_clipboard(self):
        """复制 status_label 中的错误信息到剪贴板。"""
        text = self.status_label.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def _on_result(self, result: dict):
        if self._state != self.STATE_RUNNING:
            return
        self._stop_auto_fill_timer()
        self._reset_ui()
        self._sim_id_label.setVisible(False)
        self._sim_url_btn.setVisible(False)
        # Record simulated universe/neutral before state changes
        self._simulated_universes.add(self.universe_combo.currentText())
        self._simulated_neutrals.add(self.neutral_combo.currentText())
        main_window = self.window()
        is_current = isinstance(main_window, MainWindow) and main_window.tab_widget.currentWidget() is self
        if is_current:
            self._state = self.STATE_DONE_VIEWED
            self._emit_title()
        else:
            self._state = self.STATE_DONE_UNVIEWED
            self._emit_title("\U0001f7e2")
        self.running_state_changed.emit()

        alpha = result.get("alpha", {})
        self._alpha_id = alpha.get("id")
        self._last_alpha = alpha  # Save for tune_expand score tracking (must be before sim_finished.emit)

        # 加载 cached prod corr
        if self._alpha_id:
            cached = _lc_get_cached_pc(self._alpha_id)
            if cached and cached.get("max") is not None:
                self.cached_pc_label.setText(f"max: {cached['max']:.4f}  min: {cached['min']:.4f}")

        # Auto-set alpha name in background thread if alpha_name is configured
        if alpha_name and self._alpha_id:
            class SetNameWorker(QThread):
                def __init__(self, client, alpha_id, name):
                    super().__init__()
                    self.client = client
                    self.alpha_id = alpha_id
                    self.name = name

                def run(self):
                    try:
                        self.client.ensure_auth()
                        resp = self.client.session.patch(
                            f"{self.client.BASE_URL}alphas/{self.alpha_id}",
                            json={"name": self.name}
                        )
                        if resp.status_code == 401:
                            self.client.authenticate(self.client.email, self.client.password)
                            resp = self.client.session.patch(
                                f"{self.client.BASE_URL}alphas/{self.alpha_id}",
                                json={"name": self.name}
                            )
                        resp.raise_for_status()
                    except Exception:
                        pass  # Silently ignore name-setting failures
            self._set_name_worker = SetNameWorker(self.client, self._alpha_id, alpha_name)
            self._set_name_worker.start()

        self.sim_finished.emit(self)
        self._update_mode()

        # Close tabs to the right with the same alpha id (skip if tune_expand is checked)
        if self._alpha_id and isinstance(main_window, MainWindow):
            # Check if this tab is in tune_expand mode
            if not getattr(self, '_tune_expand', False):
                my_idx = main_window.tab_widget.indexOf(self)
                tabs_to_close = []
                for i in range(my_idx + 1, main_window.tab_widget.count()):
                    tab = main_window.tab_widget.widget(i)
                    if isinstance(tab, SimulateTab) and getattr(tab, '_alpha_id', None) == self._alpha_id:
                        tabs_to_close.append(tab)
                for tab in tabs_to_close:
                    QTimer.singleShot(0, lambda t=tab: main_window._close_tab_widget(t))
        pnl = result.get("pnl", [])
        yearly = result.get("yearly", [])

        self._display_metrics(alpha)
        self._display_classifications(alpha)
        self._display_yearly(yearly)
        # Determine OS start date for PnL coloring
        os_start_date = None
        os_data = alpha.get("os")
        if os_data and isinstance(os_data, dict) and os_data.get("startDate"):
            os_start_date = os_data["startDate"][:10]
        self.pnl_canvas.plot_pnl(pnl, os_start_date=os_start_date)

        # Populate expression and settings from simulation result
        expr = alpha.get("regular", "")
        if isinstance(expr, dict):
            expr = expr.get("code", json.dumps(expr))
        self.expr_input.setPlainText(expr)
        settings = alpha.get("settings", {})
        self.region_combo.setCurrentText(settings.get("region", "USA"))
        self._on_region_changed(self.region_combo.currentText())
        self.universe_combo.setCurrentText(settings.get("universe", ""))
        self.delay_spin.setValue(settings.get("delay", 1))
        self._set_decay_value(settings.get("decay", 0))
        self.neutral_combo.setCurrentText(settings.get("neutralization", "NONE"))
        self._set_truncation_value(settings.get("truncation", 0))
        self.pasteur_combo.setCurrentText(settings.get("pasteurization", "ON"))
        self.nan_combo.setCurrentText(settings.get("nanHandling", "OFF"))
        self.max_trade_combo.setCurrentText(settings.get("maxTrade", "OFF"))
        self.max_position_combo.setCurrentText(settings.get("maxPosition", "OFF"))
        self.language_combo.setCurrentText(settings.get("language", "FASTEXPR"))
        self.lookback_spin.setValue(settings.get("lookback", DEFAULT_LOOKBACK))

        # Display checks from simulation result directly
        self._display_checks(alpha)
        self._display_properties(alpha)

        # Make expression and settings read-only in view mode
        self.expr_input.setReadOnly(True)
        self._vscode_btn.setEnabled(False)
        for combo in [self.region_combo, self.universe_combo, self.neutral_combo]:
            combo.setEnabled(False)
        self.delay_spin.setEnabled(False)
        self.decay_input.setEnabled(False)
        self.truncation_input.setEnabled(False)
        self.pasteur_combo.setEnabled(False)
        self.nan_combo.setEnabled(False)
        self.max_trade_combo.setEnabled(False)
        self.max_position_combo.setEnabled(False)
        self.language_combo.setEnabled(False)
        self.lookback_spin.setEnabled(False)

        alpha_id = alpha.get("id", "?")
        sharpe = alpha.get("is", {}).get("sharpe", "N/A")
        self._set_status(f"{T('Done')}{T('! ')}{T('Alpha ID')}: {alpha_id}, {T('Sharpe')}: {sharpe}", "#a6e3a1")

    def _fmt_yearly_val(self, col_name, val):
        if not isinstance(val, (int, float)):
            return str(val)
        if col_name == "year":
            return str(int(val))
        if col_name in ("turnover", "returns", "drawdown"):
            return f"{val * 100:.2f}%"
        if col_name == "margin":
            return f"{val * 10000:.2f}"
        if col_name in ("longCount", "shortCount"):
            return str(int(val))
        return f"{val:.2f}"

    @staticmethod
    def _set_grid_row_visible(grid, row, visible):
        for col in range(grid.columnCount()):
            item = grid.itemAtPosition(row, col)
            if item and item.widget():
                item.widget().setVisible(visible)

    def _display_classifications(self, alpha):
        # Clear old tags
        while self._classif_flow.count():
            item = self._classif_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        while self._pyramid_flow.count():
            item = self._pyramid_flow.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        classifications = alpha.get("classifications", [])
        if classifications:
            for c in classifications:
                name = c.get("name", "")
                if not name:
                    continue
                tag = QLabel(name)
                tag.setStyleSheet("""
                    QLabel {
                        background: #B9E3FD; color: #1e1e2e; font-size: 18pt; font-weight: bold;
                        border-radius: 4px; padding: 4px 18px;
                    }
                """)
                self._classif_flow.addWidget(tag)
            self._classif_toggle_btn.setVisible(True)
            self._classif_list_btn.setVisible(True)
            self._open_url_btn.setVisible(True)
            self._classif_group.setVisible(self._classif_toggle_btn.isChecked())

        # Extract pyramid themes from checks (works for both IS and OS)
        pyramids = []
        checks = alpha.get("is", {}).get("checks", [])
        for check in checks:
            if check.get("name") == "MATCHES_PYRAMID":
                pyramids = check.get("pyramids", [])
                break
        # Fallback to top-level pyramidThemes/pyramids (OS alphas)
        if not pyramids:
            pt = alpha.get("pyramidThemes")
            if pt and isinstance(pt, dict):
                pyramids = pt.get("pyramids", [])
        if not pyramids:
            pyramids = alpha.get("pyramids") or []

        if pyramids:
            # Get pyramid alpha counts from cached activities data
            pyramid_counts = {}
            main_win = self.window()
            if isinstance(main_win, MainWindow):
                pyramid_counts = getattr(main_win, '_pyramid_alpha_counts', {})

            for p in pyramids:
                name = p.get("name", "")
                multiplier = p.get("multiplier", 1)
                if not name:
                    continue
                # Look up alphaCount for this pyramid (try exact match, then case-insensitive)
                count = pyramid_counts.get(name, None)
                if count is None:
                    name_lower = name.lower()
                    for k, v in pyramid_counts.items():
                        if k.lower() == name_lower:
                            count = v
                            break
                count_str = f"{count}" if count is not None else "0"
                label_text = f"{count_str} 🔺 {name} ×{multiplier}" if multiplier != 1 else f"{count_str} 🔺 {name}"
                btn = QPushButton(label_text)
                btn.setStyleSheet("""
                    QPushButton {
                        background: #313244; color: #fab387; font-size: 16pt;
                        border: 1px solid #fab387; border-radius: 4px; padding: 4px 16px;
                    }
                    QPushButton:hover { background: #45475a; }
                """)
                btn.setToolTip(f"{T('Add alpha to list ')}'{name}'")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda checked, n=name, b=btn: self._add_to_pyramid_list(n, b))
                self._pyramid_flow.addWidget(btn)
            self._classif_toggle_btn.setVisible(True)
            self._classif_list_btn.setVisible(True)
            self._open_url_btn.setVisible(True)
            self._classif_group.setVisible(self._classif_toggle_btn.isChecked())

        # Show toggle even if no classifications (but may have pyramids)
        if not classifications and not pyramids:
            self._classif_toggle_btn.setVisible(True)
            self._classif_list_btn.setVisible(True)
            self._open_url_btn.setVisible(True)
            self._classif_group.setVisible(self._classif_toggle_btn.isChecked())

    def _add_to_pyramid_list(self, list_name: str, btn: QPushButton):
        """Add current alpha to a list named after the pyramid theme."""
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("Run a simulation first to get an Alpha ID."))
            return

        # Disable button while adding
        btn.setEnabled(False)
        btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #6c7086; font-size: 16pt;
                border: 1px solid #585b70; border-radius: 4px; padding: 4px 16px;
            }
        """)

        class PyramidAddWorker(QThread):
            finished = _sig(str, bool)
            error = _sig(str)

            def __init__(self, client, alpha_id, list_name):
                super().__init__()
                self.client = client
                self.alpha_id = alpha_id
                self.list_name = list_name

            def run(self):
                try:
                    # Fetch all tags to find existing list
                    tags = self.client.get_tags()
                    existing_tag = None
                    for tag in tags:
                        if tag.get("name") == self.list_name:
                            existing_tag = tag
                            break

                    if existing_tag:
                        # Check if alpha already in this list
                        existing_alphas = existing_tag.get("alphas", [])
                        if self.alpha_id in existing_alphas:
                            self.finished.emit(f"Already in list '{self.list_name}'", False)
                            return
                        tag_id = existing_tag.get("id")
                        if not tag_id:
                            self.error.emit(f"List '{self.list_name}' has no ID")
                            return
                        self.client.add_alpha_to_tag(tag_id, self.list_name, self.alpha_id)
                        self.finished.emit(f"Added to list '{self.list_name}'", True)
                    else:
                        new_tag = self.client.create_tag(self.list_name, self.alpha_id)
                        tag_id = new_tag.get("id")
                        if tag_id:
                            self.finished.emit(f"Created list '{self.list_name}' and added alpha", True)
                        else:
                            self.error.emit("Created list but got no ID back")
                except Exception as e:
                    self.error.emit(str(e))

        self.status_label.setText(f"{T('Adding to list ')}'{list_name}'...")
        self.status_label.setStyleSheet("color: #f9e2af;")

        self._pyramid_add_worker = PyramidAddWorker(self.client, self._alpha_id, list_name)
        self._pyramid_add_worker.finished.connect(lambda msg, ok: self._on_pyramid_add_done(msg, ok, btn))
        self._pyramid_add_worker.error.connect(lambda msg: self._on_pyramid_add_error(msg, btn))
        self._pyramid_add_worker.start()

    def _on_pyramid_add_done(self, msg, ok, btn):
        self.status_label.setText(msg)
        if ok:
            self.status_label.setStyleSheet("color: #a6e3a1;")
            btn.setStyleSheet("""
                QPushButton {
                    background: #2a3a2a; color: #a6e3a1; font-size: 16pt;
                    border: 1px solid #a6e3a1; border-radius: 4px; padding: 4px 16px;
                }
                QPushButton:hover { background: #3a4a3a; }
            """)
            btn.setToolTip(msg)
            winsound.Beep(800, 200)
        else:
            self.status_label.setStyleSheet("color: #f9e2af;")
            btn.setStyleSheet("""
                QPushButton {
                    background: #313244; color: #fab387; font-size: 16pt;
                    border: 1px solid #fab387; border-radius: 4px; padding: 4px 16px;
                }
                QPushButton:hover { background: #45475a; }
            """)
            btn.setEnabled(True)

    def _on_pyramid_add_error(self, msg, btn):
        self._set_status(f"{T('Error: ')}{msg}", "#f38ba8")
        btn.setStyleSheet("""
            QPushButton {
                background: #3a2a2a; color: #f38ba8; font-size: 16pt;
                border: 1px solid #f38ba8; border-radius: 4px; padding: 4px 16px;
            }
            QPushButton:hover { background: #4a3a3a; }
        """)
        btn.setToolTip(f"Error: {msg}")
        winsound.Beep(300, 400)

    # ── Pin Key Metrics to desktop ──────────────────────────────────
    def _pin_metrics_screenshot(self):
        """Screenshot the Key Metrics Bar and pin it as a frameless always-on-top window."""
        widget = self._metrics_grid
        if widget is None:
            return

        pixmap = widget.grab()

        pin_win = _PinnedMetricsWindow(pixmap, parent=self)
        # Position directly below the Key Metrics Bar
        bar_rect = widget.mapToGlobal(widget.rect().topLeft())
        pin_win.move(bar_rect.x() - 4, bar_rect.y() + widget.height() + 4)
        pin_win.show()
        if not hasattr(self, '_pinned_windows'):
            self._pinned_windows = []
        self._pinned_windows.append(pin_win)
        pin_win.destroyed.connect(lambda: self._pinned_windows.remove(pin_win)
                                  if pin_win in self._pinned_windows else None)

    def _compute_diversity_metrics(self, alpha):
        """Compute Operators per Alpha, Operators used, Fields per Alpha, Fields used
        showing current → next (±diff) if this alpha were submitted.
        - Operators: only FASTEXPR alphas contribute.
        - Fields: both FASTEXPR and PYTHON alphas contribute.
          For PYTHON alphas, fields are extracted from alpha('field') / alpha["field"] references
          (excluding WHITE_LIST).
        Returns list of (label, value_str) tuples for the metrics table.
        """

        code = alpha.get("regular", {}).get("code", "")
        if not code:
            return []
        settings = alpha.get("settings", {})
        lang = settings.get("language", "FASTEXPR")
        if lang not in ("FASTEXPR", "PYTHON"):
            return []

        this_is_fastexpr = (lang == "FASTEXPR")

        # ── Compute this alpha's contribution ──
        this_ops = _extract_ops_from_code(code) if this_is_fastexpr else {}
        this_ops.pop("ts_backfill", None)
        this_ops.pop("group_backfill", None)
        regular_ops = _get_regular_scope_operators()
        if regular_ops is not None:
            this_ops = {op: cnt for op, cnt in this_ops.items() if op in regular_ops}

        # Build known identifiers for FASTEXPR datafield extraction
        wl = set(WHITE_LIST)
        known = set(_BRAIN_OPERATORS) | _BRAIN_CONSTANTS | _BRAIN_KEYWORDS | {"returns"} | wl

        this_fields = {}
        if this_is_fastexpr:
            assigned = set(re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*=', code))
            all_ids = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', code)
            for ident in all_ids:
                if ident not in known and ident not in assigned:
                    this_fields[ident] = this_fields.get(ident, 0) + 1
        else:
            # PYTHON alpha: extract fields from alpha('field') / alpha["field"] references
            this_fields = _extract_fields_from_python(code)

        # ── Load current quarter stats from alphas_db.json ──
        et_now = dt_datetime.now(timezone(timedelta(hours=-5)))
        quarter = (et_now.month - 1) // 3 + 1
        year = et_now.year
        q_start = dt_datetime(year, (quarter - 1) * 3 + 1, 1)
        q_end = dt_datetime(year, quarter * 3, 1) + timedelta(days=32)
        q_end = q_end.replace(day=1)

        cur_ops_count = {}
        cur_fields_count = {}
        cur_ops_alpha_count = 0      # FASTEXPR alphas only (for Operators per Alpha)
        cur_fields_alpha_count = 0   # FASTEXPR + PYTHON alphas (for Fields per Alpha)
        cur_sum_unique_ops = 0       # sum of unique ops per alpha (for Operators per Alpha)
        cur_sum_unique_fields = 0    # sum of unique fields per alpha (for Fields per Alpha)
        alpha_id = alpha.get("id")

        if os.path.exists(_LC_ALPHAS_DB_PATH):
            try:
                with open(_LC_ALPHAS_DB_PATH, "r", encoding="utf-8") as f:
                    db = json.load(f)
                for a in (db.values() if isinstance(db, dict) else db):
                    s = a.get("settings", {})
                    a_lang = s.get("language", "FASTEXPR")
                    ds = a.get("dateSubmitted", "")
                    if not ds:
                        continue
                    try:
                        sub_dt = dt_datetime.strptime(ds[:10], "%Y-%m-%d")
                    except ValueError:
                        continue
                    if sub_dt < q_start or sub_dt >= q_end:
                        continue
                    c = a.get("regular", {}).get("code", "")
                    if not c:
                        continue
                    if a_lang == "FASTEXPR":
                        cur_ops_alpha_count += 1
                        a_ops = _extract_ops_from_code(c)
                        a_ops.pop("ts_backfill", None)
                        a_ops.pop("group_backfill", None)
                        if regular_ops is not None:
                            a_ops = {op: cnt for op, cnt in a_ops.items() if op in regular_ops}
                        cur_sum_unique_ops += len(a_ops)
                        for op, cnt in a_ops.items():
                            cur_ops_count[op] = cur_ops_count.get(op, 0) + cnt
                        # FASTEXPR datafields
                        cur_fields_alpha_count += 1
                        a_known = known.copy()
                        a_assigned = set(re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*=', c))
                        a_fields = set()
                        for ident in re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', c):
                            if ident not in a_known and ident not in a_assigned:
                                a_fields.add(ident)
                                cur_fields_count[ident] = cur_fields_count.get(ident, 0) + 1
                        cur_sum_unique_fields += len(a_fields)
                    elif a_lang == "PYTHON":
                        # PYTHON alpha contributes only fields, not operators
                        cur_fields_alpha_count += 1
                        py_fields = _extract_fields_from_python(c)
                        cur_sum_unique_fields += len(py_fields)
                        for f, cnt in py_fields.items():
                            cur_fields_count[f] = cur_fields_count.get(f, 0) + cnt
            except Exception:
                pass

        # Check if this alpha is already in current quarter's submitted alphas
        already_submitted = False
        if alpha_id:
            try:
                with open(_LC_ALPHAS_DB_PATH, "r", encoding="utf-8") as f:
                    db = json.load(f)
                for a in (db.values() if isinstance(db, dict) else db):
                    if a.get("id") == alpha_id:
                        already_submitted = True
                        break
            except Exception:
                pass

        # this alpha's unique ops/fields count (already filtered)
        this_unique_ops = len(this_ops)
        this_unique_fields = len(this_fields)

        if already_submitted:
            # Current already includes this alpha
            next_ops_alpha_count = cur_ops_alpha_count
            next_fields_alpha_count = cur_fields_alpha_count
            next_sum_unique_ops = cur_sum_unique_ops
            next_sum_unique_fields = cur_sum_unique_fields
            next_ops_count = cur_ops_count
            next_fields_count = cur_fields_count
        else:
            next_ops_alpha_count = cur_ops_alpha_count + (1 if this_is_fastexpr else 0)
            next_fields_alpha_count = cur_fields_alpha_count + 1
            next_sum_unique_ops = cur_sum_unique_ops + (this_unique_ops if this_is_fastexpr else 0)
            next_sum_unique_fields = cur_sum_unique_fields + this_unique_fields
            next_ops_count = dict(cur_ops_count)
            for op, cnt in this_ops.items():
                next_ops_count[op] = next_ops_count.get(op, 0) + cnt
            next_fields_count = dict(cur_fields_count)
            for f, cnt in this_fields.items():
                next_fields_count[f] = next_fields_count.get(f, 0) + cnt

        # Current values
        cur_total_ops = sum(cur_ops_count.values())
        cur_unique_ops = len(cur_ops_count)
        cur_ops_per_alpha = cur_sum_unique_ops / cur_ops_alpha_count if cur_ops_alpha_count > 0 else 0
        cur_total_fields = sum(cur_fields_count.values())
        cur_unique_fields = len(cur_fields_count)
        cur_fields_per_alpha = cur_sum_unique_fields / cur_fields_alpha_count if cur_fields_alpha_count > 0 else 0

        # Next values
        next_total_ops = sum(next_ops_count.values())
        next_unique_ops = len(next_ops_count)
        next_ops_per_alpha = next_sum_unique_ops / next_ops_alpha_count if next_ops_alpha_count > 0 else 0
        next_total_fields = sum(next_fields_count.values())
        next_unique_fields = len(next_fields_count)
        next_fields_per_alpha = next_sum_unique_fields / next_fields_alpha_count if next_fields_alpha_count > 0 else 0

        def _fmt_div(cur, nxt):
            diff = nxt - cur
            sign = "+" if diff > 0 else ""
            return f"{cur:.2f} → {nxt:.2f} ({sign}{diff:.2f})" if cur_ops_alpha_count > 0 else f"{nxt:.-2f}"

        def _fmt_div_int(cur, nxt):
            diff = nxt - cur
            sign = "+" if diff > 0 else ""
            return f"{cur} → {nxt} ({sign}{diff})" if cur_ops_alpha_count > 0 else f"{nxt}"

        # New ops/fields introduced by this alpha (sorted alphabetically)
        new_ops = sorted(set(next_ops_count) - set(cur_ops_count))
        new_fields = sorted(set(next_fields_count) - set(cur_fields_count))

        result = [
            ("", ""),
            (T("── Submitted ──"), ""),
            (T("Operators per Alpha"), _fmt_div(cur_ops_per_alpha, next_ops_per_alpha)),
            (T("Operators used"), _fmt_div_int(cur_unique_ops, next_unique_ops)),
        ]
        if new_ops:
            result.append((T("Operators used"), ", ".join(new_ops)))
        result += [
            (T("Fields per Alpha"), _fmt_div(cur_fields_per_alpha, next_fields_per_alpha)),
            (T("Fields used"), _fmt_div_int(cur_unique_fields, next_unique_fields)),
        ]
        if new_fields:
            result.append((T("Fields used"), ", ".join(new_fields)))
        return result

    def _show_section_corr(self, section_key):
        """Show Self Corr & PPC for a specific PnL section (RN/IC/AMER/APAC/EMEA).
        Uses local correlation calculation (same as LocalCorrelationWorker).
        """
        alpha_id = getattr(self, '_alpha_id', None)
        if not alpha_id:
            return

        # Get the PnL data for this section
        pnl_data = getattr(self.pnl_canvas, '_pnl_data', None)
        if not pnl_data or not pnl_data[0]:
            QMessageBox.information(self, T("Corr"), T("No PnL data available."))
            return

        dates = pnl_data[0]
        # Determine which PnL series to use
        if section_key == "RN":
            if len(pnl_data) < 3 or not pnl_data[2]:
                QMessageBox.information(self, T("Corr"), T("No Risk Neutralized PnL data."))
                return
            section_pnl = pnl_data[2]
            section_label = T("Risk Neutralized")
        elif section_key == "IC":
            if len(pnl_data) < 4 or not pnl_data[3]:
                QMessageBox.information(self, T("Corr"), T("No Investability Constrained PnL data."))
                return
            section_pnl = pnl_data[3]
            section_label = T("Investability Constrained")
        else:  # AMER/APAC/EMEA
            if len(pnl_data) < 5 or not isinstance(pnl_data[4], dict):
                QMessageBox.information(self, T("Corr"), f"{T('No ')}{section_key}{T(' PnL data.')}")
                return
            section_pnl = pnl_data[4].get(section_key)
            if not section_pnl:
                QMessageBox.information(self, T("Corr"), f"{T('No ')}{section_key}{T(' PnL data.')}")
                return
            section_label = section_key

        # Build target_records in the format expected by _lc_calculate_corr_for_alpha
        target_records = list(zip([str(d)[:10] for d in dates], section_pnl))

        # Launch a worker thread to compute correlation
        class SectionCorrWorker(QThread):
            finished = pyqtSignal(dict)  # result dict
            error = pyqtSignal(str)
            progress = pyqtSignal(str)

            def __init__(self, client, alpha_id, target_records, section_key):
                super().__init__()
                self.client = client
                self.alpha_id = alpha_id
                self.target_records = target_records
                self.section_key = section_key

            def run(self):
                try:
                    sess = self.client.session
                    self.client.ensure_auth()

                    def _log(msg):
                        print(msg, flush=True)
                        self.progress.emit(msg)

                    _log(f"[{self.section_key}-corr] Loading alpha metadata...")
                    all_alphas = _lc_fetch_all_submitted_alphas(sess, need_download=False)
                    if not all_alphas:
                        self.error.emit("No submitted alphas in local cache. Run Download first.")
                        return

                    _log(f"[{self.section_key}-corr] Loading PnL data...")
                    os.makedirs(_LC_OUTPUT_DIR, exist_ok=True)
                    _corr_db = _lc_load_local_alphas_db()
                    pnl_cache = {}
                    pnl_available_ids = set()
                    for alpha in all_alphas:
                        aid = alpha['id']
                        region = alpha.get('settings', {}).get('region')
                        records = _lc_load_pnl_from_csv(aid, region=region, db=_corr_db)
                        if records is not None:
                            pnl_cache[aid] = records
                            pnl_available_ids.add(aid)
                    _log(f"[{self.section_key}-corr] Loaded PnL for {len(pnl_available_ids)}/{len(all_alphas)} alphas")

                    # Find target alpha in pool
                    target_alpha = None
                    for a in all_alphas:
                        if a['id'] == self.alpha_id:
                            target_alpha = a
                            break

                    if target_alpha is None:
                        _log(f"[{self.section_key}-corr] Fetching alpha detail for {self.alpha_id}...")
                        target_alpha = _lc_fetch_alpha_detail(sess, self.alpha_id)
                        if target_alpha is None:
                            self.error.emit(f"Failed to fetch alpha detail for {self.alpha_id}")
                            return

                    # Use the section PnL as the target's PnL
                    pnl_cache[self.alpha_id] = self.target_records
                    pnl_available_ids.add(self.alpha_id)

                    _log(f"[{self.section_key}-corr] Calculating correlation...")
                    result = _lc_calculate_corr_for_alpha(target_alpha, all_alphas, pnl_cache, pnl_available_ids)
                    if result is None:
                        self.error.emit("No correlation result")
                        return

                    self.finished.emit(result)

                except Exception as e:
                    self.error.emit(str(e))

        worker = SectionCorrWorker(self.client, alpha_id, target_records, section_key)
        # Show loading
        btn = self._section_corr_btns.get(section_key)
        if btn:
            btn.setText("...")
            btn.setEnabled(False)

        def _on_finished(result):
            if btn:
                btn.setText(T("Corr"))
                btn.setEnabled(True)
            # Show result in a popup
            dlg = QDialog(self)
            dlg.setWindowTitle(f"{T('Corr')}{T(' — ')}{section_label}")
            dlg.setMinimumSize(300, 200)
            layout = QVBoxLayout(dlg)

            sc_max = result.get('self_corr_max')
            sc_min = result.get('self_corr_min')
            sc_count = result.get('self_corr_count', 0)
            pool_size = result.get('self_pool_size', 0)
            ppc_max = result.get('ppa_corr_max')
            ppc_min = result.get('ppa_corr_min')
            ppc_count = result.get('ppa_corr_count', 0)
            ppc_pool_size = result.get('ppa_pool_size', 0)

            info = QGridLayout()
            info.setSpacing(8)
            row = 0
            info.addWidget(QLabel(T("Self Corr")), row, 0)
            sc_text = f"{T('max: ')}{sc_max:.4f}{T('  min: ')}{sc_min:.4f}" if sc_max is not None else "N/A"
            info.addWidget(QLabel(sc_text), row, 1)
            row += 1
            info.addWidget(QLabel(T("Self Pool")), row, 0)
            info.addWidget(QLabel(f"{sc_count}/{pool_size}"), row, 1)
            row += 1
            info.addWidget(QLabel(T("PPC")), row, 0)
            ppc_text = f"{T('max: ')}{ppc_max:.4f}{T('  min: ')}{ppc_min:.4f}" if ppc_max is not None else "N/A"
            info.addWidget(QLabel(ppc_text), row, 1)
            row += 1
            info.addWidget(QLabel(T("PPC Pool")), row, 0)
            info.addWidget(QLabel(f"{ppc_count}/{ppc_pool_size}"), row, 1)

            for r in range(info.rowCount()):
                for c in range(info.columnCount()):
                    item = info.itemAtPosition(r, c)
                    if item and item.widget():
                        item.widget().setStyleSheet("color: #cdd6f4; font-size: 14pt;")

            layout.addLayout(info)
            layout.addStretch()
            dlg.exec_()

        def _on_error(msg):
            if btn:
                btn.setText(T("Corr"))
                btn.setEnabled(True)
            QMessageBox.warning(self, T("Corr Error"), msg)

        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.start()
        self._section_corr_worker = worker  # prevent GC

    def _display_metrics(self, alpha: dict):
        is_data = alpha.get("is", {})
        os_data = alpha.get("os", {})

        # Key metrics bar
        sharpe = is_data.get("sharpe")
        turnover = is_data.get("turnover")
        fitness = is_data.get("fitness")
        returns = is_data.get("returns")
        drawdown = is_data.get("drawdown")
        margin = is_data.get("margin")

        def fmt(v, pct=False, bp=False):
            if v is None:
                return "--"
            if pct:
                return f"{v * 100:.2f}%"
            if bp:
                return f"{v * 10000:.2f}‱"
            return f"{v:.2f}"

        self._key_labels["Sharpe"].setText(f"{fmt(sharpe)}")
        self._key_labels["Turnover"].setText(f"{fmt(turnover, pct=True)}")
        self._key_labels["Fitness"].setText(f"{fmt(fitness)}")
        self._key_labels["Returns"].setText(f"{fmt(returns, pct=True)}")
        self._key_labels["Drawdown"].setText(f"{fmt(drawdown, pct=True)}")
        self._key_labels["Margin"].setText(f"{fmt(margin, bp=True)}")

        # Risk Neutralized metrics
        rn_data = is_data.get("riskNeutralized")
        if rn_data:
            self._rn_labels["Sharpe"].setText(fmt(rn_data.get("sharpe")))
            self._rn_labels["Turnover"].setText(fmt(rn_data.get("turnover"), pct=True))
            self._rn_labels["Fitness"].setText(fmt(rn_data.get("fitness")))
            self._rn_labels["Returns"].setText(fmt(rn_data.get("returns"), pct=True))
            self._rn_labels["Drawdown"].setText(fmt(rn_data.get("drawdown"), pct=True))
            self._rn_labels["Margin"].setText(fmt(rn_data.get("margin"), bp=True))
            self._set_grid_row_visible(self._metrics_grid.layout(), self._rn_row_start, True)
        else:
            self._set_grid_row_visible(self._metrics_grid.layout(), self._rn_row_start, False)

        # Investability Constrained metrics
        ic_data = is_data.get("investabilityConstrained")
        if ic_data:
            self._ic_labels["Sharpe"].setText(fmt(ic_data.get("sharpe")))
            self._ic_labels["Turnover"].setText(fmt(ic_data.get("turnover"), pct=True))
            self._ic_labels["Fitness"].setText(fmt(ic_data.get("fitness")))
            self._ic_labels["Returns"].setText(fmt(ic_data.get("returns"), pct=True))
            self._ic_labels["Drawdown"].setText(fmt(ic_data.get("drawdown"), pct=True))
            self._ic_labels["Margin"].setText(fmt(ic_data.get("margin"), bp=True))
            self._set_grid_row_visible(self._metrics_grid.layout(), self._ic_row_start, True)
        else:
            self._set_grid_row_visible(self._metrics_grid.layout(), self._ic_row_start, False)

        # GLB sub-region metrics (AMER, APAC, EMEA)
        sub_region_map = {"glbAmer": "AMER", "glbApac": "APAC", "glbEmea": "EMEA"}
        for key, display_name in sub_region_map.items():
            sub_data = is_data.get(key)
            grid = self._metrics_grid.layout()
            if sub_data and isinstance(sub_data, dict):
                labels = self._sub_region_labels[display_name]
                labels["Sharpe"].setText(fmt(sub_data.get("sharpe")))
                labels["Turnover"].setText(fmt(sub_data.get("turnover"), pct=True))
                labels["Fitness"].setText(fmt(sub_data.get("fitness")))
                labels["Returns"].setText(fmt(sub_data.get("returns"), pct=True))
                labels["Drawdown"].setText(fmt(sub_data.get("drawdown"), pct=True))
                labels["Margin"].setText(fmt(sub_data.get("margin"), bp=True))
                self._sub_region_titles[display_name].setVisible(True)
                self._set_grid_row_visible(grid, self._sub_region_row_start[display_name], True)
            else:
                self._sub_region_titles[display_name].setVisible(False)
                self._set_grid_row_visible(grid, self._sub_region_row_start[display_name], False)

        metrics = [
            (T("Alpha ID"), alpha.get("id", "N/A")),
            (T("Status"), alpha.get("status", "N/A")),
            (T("Date Created"), alpha.get("dateCreated", "N/A")),
            ("", ""),
            (T("── IS Metrics ──"), ""),
        ]

        is_fields = [
            (T("Long Count"), "longCount"),
            (T("Short Count"), "shortCount"),
            (T("Beta"), "beta"),
            (T("Total Orders"), "totalOrders"),
            (T("Weight Correlation"), "weightCorr"),
        ]

        for label, key in is_fields:
            val = is_data.get(key)
            if val is not None:
                if isinstance(val, float):
                    metrics.append((label, f"{val:.6f}"))
                else:
                    metrics.append((label, str(val)))

        regular_data = alpha.get("regular", {})
        op_count = regular_data.get("operatorCount")
        if op_count is not None:
            metrics.append((T("Operator Count"), str(op_count)))

        # IS PnL
        is_pnl = is_data.get("pnl")
        if is_pnl is not None:
            pnl_val = float(is_pnl)
            pnl_m = pnl_val / 1e6
            metrics.append((T("IS PnL"), f"{int(pnl_val)}({pnl_m:.2f}M)"))

        # ── Diversity metrics: current → next (±diff) ──
        div_lines = self._compute_diversity_metrics(alpha)
        metrics.extend(div_lines)

        if os_data:
            metrics.append(("", ""))
            metrics.append((T("── OS Metrics ──"), ""))
            for label, key in is_fields:
                val = os_data.get(key)
                if val is not None:
                    if isinstance(val, float):
                        metrics.append((label, f"{val:.6f}"))
                    else:
                        metrics.append((label, str(val)))

        self.metrics_table.setRowCount(len(metrics))
        for i, (name, value) in enumerate(metrics):
            name_item = QTableWidgetItem(name)
            self.metrics_table.setItem(i, 0, name_item)
            if name == T("Alpha ID"):
                # Use a widget with value label + copy button
                cell_widget = QWidget()
                cell_layout = QHBoxLayout(cell_widget)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                cell_layout.setSpacing(4)
                val_label = QLabel(str(value))
                val_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                val_label.setStyleSheet("color: #cdd6f4; background: transparent; border: none;")
                copy_btn = QPushButton("📋")
                copy_btn.setFixedSize(24, 24)
                copy_btn.setToolTip(T("Copy Alpha ID"))
                copy_btn.setStyleSheet("""
                    QPushButton {
                        background: #313244; color: #cdd6f4; font-size: 10pt;
                        border: 1px solid #45475a; border-radius: 3px;
                    }
                    QPushButton:hover { background: #45475a; }
                """)
                def _copy_aid(_checked, v=str(value), btn=copy_btn):
                    QApplication.clipboard().setText(v)
                    btn.setText("✓")
                    QTimer.singleShot(1500, lambda: btn.setText("📋"))
                copy_btn.clicked.connect(_copy_aid)
                cell_layout.addWidget(val_label)
                cell_layout.addWidget(copy_btn)
                cell_layout.addStretch()
                self.metrics_table.setCellWidget(i, 1, cell_widget)
            else:
                value_item = QTableWidgetItem(str(value))
                if name.startswith("──"):
                    name_item.setFont(QFont("", -1, QFont.Bold))
                    value_item.setFont(QFont("", -1, QFont.Bold))
                self.metrics_table.setItem(i, 1, value_item)
        self.metrics_table.resizeRowsToContents()
        h = self.metrics_table.horizontalHeader().height() + 4
        for r in range(self.metrics_table.rowCount()):
            h += self.metrics_table.rowHeight(r)
        self.metrics_table.setFixedHeight(h)

    def _display_yearly(self, yearly_data):
        records = []
        col_names = []
        if isinstance(yearly_data, dict) and "records" in yearly_data:
            records = yearly_data["records"]
            schema = yearly_data.get("schema", {})
            col_names = [p["name"] for p in schema.get("properties", [])]
        elif isinstance(yearly_data, list):
            records = yearly_data

        if not records:
            self.yearly_table.setRowCount(1)
            self.yearly_table.setItem(0, 0, QTableWidgetItem("—"))
            self.yearly_table.setItem(0, 1, QTableWidgetItem("No yearly data"))
            self.yearly_table.setFixedHeight(60)
            return

        key_cols = ["year", "sharpe", "fitness", "turnover", "returns", "drawdown", "margin", "longCount", "shortCount"]
        col_indices = {}
        for col_name in key_cols:
            if col_name in col_names:
                col_indices[col_name] = col_names.index(col_name)

        display_cols = [c for c in key_cols if c in col_indices]
        display_labels = {"longCount": "Long", "shortCount": "Short"}.get
        self.yearly_table.setColumnCount(len(display_cols))
        self.yearly_table.setHorizontalHeaderLabels([display_labels(c, c) for c in display_cols])
        self.yearly_table.setRowCount(len(records))

        for i, entry in enumerate(records):
            if isinstance(entry, (list, tuple)):
                for j, col_name in enumerate(display_cols):
                    idx = col_indices[col_name]
                    val = entry[idx] if idx < len(entry) else ""
                    text = self._fmt_yearly_val(col_name, val)
                    self.yearly_table.setItem(i, j, QTableWidgetItem(text))
            else:
                for j, col_name in enumerate(display_cols):
                    val = entry.get(col_name, "")
                    text = self._fmt_yearly_val(col_name, val)
                    self.yearly_table.setItem(i, j, QTableWidgetItem(text))

        self.yearly_table.resizeRowsToContents()
        h = self.yearly_table.horizontalHeader().height() + 4
        for r in range(self.yearly_table.rowCount()):
            h += self.yearly_table.rowHeight(r)
        self.yearly_table.setFixedHeight(h)

    def _fetch_checks_async(self, alpha_id):
        """Fetch checks data asynchronously after simulation."""
        if not alpha_id:
            return
        worker = CorrelationWorker(self.client, alpha_id, "checks")
        worker.finished.connect(lambda ct, data: self._display_checks(data))
        worker.start()

    def _display_checks(self, checks_data):
        """Display submission checks from alpha data."""
        checks = checks_data.get("is", {}).get("checks", [])
        if not checks:
            self.checks_pass_list.clear()
            self.checks_warn_list.clear()
            self.checks_fail_list.clear()
            self.checks_pending_list.clear()
            self.checks_pass_btn.setVisible(False)
            self.checks_warn_btn.setVisible(False)
            self.checks_fail_btn.setVisible(False)
            self.checks_pending_btn.setVisible(False)
            self.checks_pass_group.setVisible(False)
            self.checks_warn_group.setVisible(False)
            self.checks_fail_group.setVisible(False)
            self.checks_pending_group.setVisible(False)
            return

        pass_items = []
        warn_items = []
        fail_items = []
        pending_items = []

        for check in checks:
            name = check.get("name", "")
            result = check.get("result", "")
            value = check.get("value")
            limit = check.get("limit")
            message = check.get("message", "")

            desc = self._format_check_desc(name, value, limit, message, check)

            if result == "PASS":
                pass_items.append(f'<span style="color:#4CAF50">●</span> {desc}')
            elif result == "FAIL":
                fail_items.append(f'<span style="color:#f38ba8">●</span> {desc}')
            elif result in ("PENDING", "PENDING_NOT_STARTED"):
                pending_items.append(f'<span style="color:#89b4fa">●</span> {desc}')
            else:
                warn_items.append(f'<span style="color:#f9a825">●</span> {desc}')

        self.checks_pass_list.setHtml("<br>".join(pass_items) if pass_items else "")
        self.checks_warn_list.setHtml("<br>".join(warn_items) if warn_items else "")
        self.checks_fail_list.setHtml("<br>".join(fail_items) if fail_items else "")
        self.checks_pending_list.setHtml("<br>".join(pending_items) if pending_items else "")

        # Auto-resize QTextEdit to fit content
        for w in (self.checks_pass_list, self.checks_warn_list,
                  self.checks_fail_list, self.checks_pending_list):
            doc = w.document()
            doc.setTextWidth(w.viewport().width())
            h = int(doc.size().height()) + 4
            w.setFixedHeight(max(h, 10))

        self.checks_pass_btn.setText(f"{T('PASS')} ({len(pass_items)})")
        self.checks_pass_btn.setVisible(bool(pass_items))
        self.checks_pass_group.setVisible(bool(pass_items))
        self.checks_pass_btn.setChecked(bool(pass_items))
        self.checks_warn_btn.setText(f"{T('WARNING')} ({len(warn_items)})")
        self.checks_warn_btn.setVisible(bool(warn_items))
        self.checks_warn_group.setVisible(bool(warn_items))
        self.checks_warn_btn.setChecked(bool(warn_items))
        self.checks_fail_btn.setText(f"{T('FAIL')} ({len(fail_items)})")
        self.checks_fail_btn.setVisible(bool(fail_items))
        self.checks_fail_group.setVisible(bool(fail_items))
        self.checks_fail_btn.setChecked(bool(fail_items))
        self.checks_pending_btn.setText(f"{T('PENDING')} ({len(pending_items)})")
        self.checks_pending_btn.setVisible(bool(pending_items))
        self.checks_pending_group.setVisible(False)
        self.checks_pending_btn.setChecked(False)

        # Refresh tab style so the tab text turns red when there are FAILs
        main_window = self.window()
        if isinstance(main_window, MainWindow):
            idx = main_window.tab_widget.indexOf(self)
            if idx >= 0:
                main_window._update_tab_style(idx, self)

        # Disable Submit/Check buttons if there are any FAIL checks
        has_fail = len(fail_items) > 0
        if hasattr(self, 'submit_btn'):
            self.submit_btn.setEnabled(not has_fail)
        if hasattr(self, 'check_btn'):
            self.check_btn.setEnabled(not has_fail)

    def _format_check_desc(self, name, value, limit, message, check):
        """Format a check item into a human-readable description."""
        # Special PENDING descriptions
        result = check.get("result", "")
        if result in ("PENDING", "PENDING_NOT_STARTED"):
            return self._format_pending_desc(name, value, check)

        # Special WARNING/FAIL descriptions
        if name == "CONCENTRATED_WEIGHT" and value is not None and limit is not None:
            v_str = self._fmt_check_val(name, value)
            l_str = self._fmt_check_val(name, limit)
            date = check.get("date", "")
            date_str = f" on {date}" if date else ""
            cmp = "above" if (isinstance(value, (int, float)) and value >= limit) else "below"
            return f"Weight concentration {v_str} is {cmp} cutoff of {l_str}{date_str}."

        if name == "HT_ORTHOGONAL_RAM_NEUTRALIZATION" and value is not None and limit is not None:
            return f"Orthogonal High Turnover: Neutralization of {value} does not match Orthogonal High Turnover neutralization of {limit}."

        if name == "MATCHES_THEMES":
            themes = check.get("themes", [])
            if themes:
                parts = [f"{t['name']} — {t.get('multiplier', 1)}" for t in themes]
                return f"These themes do not match with the following multipliers: {'; '.join(parts)}."
            return "Theme matches"

        if name == "MATCHES_PYRAMID":
            pyramids = check.get("pyramids", [])
            effective = check.get("effective", 1)
            if pyramids:
                parts = [f"{p['name']} matches with multiplier of {p.get('multiplier', 1)}" for p in pyramids]
                return f"Pyramid theme {', '.join(parts)}. Effective pyramid count for Genius is {effective}."
            return "Pyramid theme matches"

        if name == "MATCHES_CLASSIFICATION" and isinstance(value, list):
            return f"Classification {' '.join(value)} matches"

        if name == "OSMOSIS_ALLOCATION":
            return "Daily Osmosis Rank not generated."

        if name == "HT_AFTER_COST_SHARPE" and value is not None and limit is not None:
            v_str = self._fmt_check_val(name, value)
            l_str = self._fmt_check_val(name, limit)
            cmp = "below" if (isinstance(value, (int, float)) and value < limit) else "above"
            return f"After Cost High Turnover: After cost Sharpe of {v_str} is {cmp} cutoff of {l_str}."

        if message:
            return message

        if value is not None and limit is not None:
            v_str = self._fmt_check_val(name, value)
            l_str = self._fmt_check_val(name, limit)
            if name.startswith("LOW_") or name == "CONCENTRATED_WEIGHT":
                cmp = "above" if (isinstance(value, (int, float)) and value >= limit) else "below"
            else:
                cmp = "below" if (isinstance(value, (int, float)) and value < limit) else "above"
            label = self._check_label(name)
            return f"{label} of {v_str} is {cmp} cutoff of {l_str}."
        if value is not None:
            return f"{self._check_label(name)}: {self._fmt_check_val(name, value)}"
        return self._check_label(name)

    def _format_pending_desc(self, name, value, check):
        """Format a PENDING check description."""
        pending_labels = {
            "SELF_CORRELATION": T("Self-correlation"),
            "DATA_DIVERSITY": T("Data overuse"),
            "PROD_CORRELATION": T("Production correlation"),
            "REGULAR_SUBMISSION": T("Alpha submissions quota"),
            "POWER_POOL_CORRELATION": T("Power Pool correlation"),
        }
        if name == "MATCHES_THEMES":
            themes = check.get("themes", [])
            if themes:
                parts = [f"Theme {t['name']} with multiplier of {t.get('multiplier', 1)}" for t in themes]
                return f"{' and '.join(parts)} will be processed when checking submission."
            return "Theme check pending."
        label = pending_labels.get(name, self._check_label(name))
        return f"{label} check pending."

    def _fmt_check_val(self, name, v):
        """Format a check value, converting turnover ratios to percentages."""
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        if isinstance(v, (int, float)):
            is_pct = any(k in name for k in ["TURNOVER", "TURNOVER", "CONCENTRATED"])
            if is_pct and abs(v) <= 1:
                return f"{v * 100:.2f}%"
            if abs(v) < 10:
                return f"{v:.2f}"
            return f"{v:.2f}"
        return str(v)

    def _check_label(self, name):
        """Convert check name to human-readable label."""
        labels = {
            "LOW_SHARPE": T("Sharpe"),
            "LOW_FITNESS": T("Fitness"),
            "LOW_TURNOVER": T("Turnover"),
            "HIGH_TURNOVER": T("Turnover"),
            "CONCENTRATED_WEIGHT": T("Concentrated weight"),
            "LOW_SUB_UNIVERSE_SHARPE": T("Sub-universe Sharpe"),
            "REVERSION_COMPONENT": T("Reversion component"),
            "SELF_CORRELATION": T("Self correlation"),
            "DATA_DIVERSITY": T("Data diversity"),
            "PROD_CORRELATION": T("Prod correlation"),
            "REGULAR_SUBMISSION": T("Regular submission"),
            "HT_TURNOVER": T("High Turnover: Turnover"),
            "HT_HIGH_TURNOVER_RETURNS_RATIO": T("High Turnover: High Turnover returns ratio"),
            "HT_PNL_REALIZATION_HORIZON": T("High Turnover: Pnl realization"),
            "HT_LIQUID_TOP500_TOP200_SHARPE_RATIO": T("Liquid High Turnover: TOP500 and TOP200 Sharpe ratio"),
            "HT_LIQUID_TOP200_SHARPE": T("Liquid High Turnover: TOP200 Sharpe"),
            "HT_AFTER_COST_SHARPE": T("High Turnover: After cost Sharpe"),
            "HT_INVESTABLE_MAX_TRADE_SHARPE": T("Investable High Turnover: Max Trade Sharpe"),
            "HT_INVESTABLE_MAX_TRADE_TURNOVER": T("Investable High Turnover: Max Trade turnover"),
            "HT_INVESTABLE_MAX_POSITION_SHARPE": T("Investable High Turnover: Max Position Sharpe"),
            "HT_INVESTABLE_MAX_POSITION_TURNOVER": T("Investable High Turnover: Max Position turnover"),
            "HT_ORTHOGONAL_RAM_NEUTRALIZATION": T("High Turnover: Orthogonal RAM neutralization"),
            "LOW_2Y_SHARPE": T("2 year Sharpe"),
            "MATCHES_CLASSIFICATION": T("Classification High Turnover"),
            "MATCHES_PYRAMID": T("Pyramid theme"),
            "MATCHES_THEMES": T("Theme"),
            "POWER_POOL_CORRELATION": T("Power pool correlation"),
            "OSMOSIS_ALLOCATION": T("Osmosis allocation"),
        }
        return labels.get(name, name.replace("_", " ").title())

    def _display_properties(self, alpha_data):
        name = alpha_data.get("name") or ""
        tags = alpha_data.get("tags") or []
        desc = alpha_data.get("description")
        if isinstance(desc, dict):
            desc = desc.get("regular", "")
        elif desc is None:
            # Try regular.description
            regular = alpha_data.get("regular")
            if isinstance(regular, dict):
                desc = regular.get("description", "")
            else:
                desc = ""
        self.prop_name.setText(str(name))
        self.prop_tags.setText(", ".join(tags) if isinstance(tags, list) else str(tags))
        self.prop_desc.setPlainText(str(desc))
        # Set color button from alpha data
        alpha_color = alpha_data.get("color")
        self._selected_color = alpha_color
        for btn in self._color_btns:
            btn.setChecked(btn._color_val == alpha_color)
        self.props_btn.setVisible(True)

    def _on_color_btn_clicked(self, clicked_btn):
        for btn in self._color_btns:
            btn.setChecked(btn is clicked_btn)
        self._selected_color = clicked_btn._color_val

    def _on_write_desc(self):
        """Run /write_desc skill for the current alpha via Claude Agent SDK."""
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("Run a simulation first to get an Alpha ID."))
            return

        self.prop_write_desc_btn.setEnabled(False)
        self.prop_write_desc_btn.setText(T("Writing..."))

        # Timer to show elapsed seconds on the button while waiting
        self._write_desc_start = datetime.now()
        self._write_desc_timer = QTimer(self)
        self._write_desc_timer.timeout.connect(self._update_write_desc_elapsed)
        self._write_desc_timer.start(1000)

        class WriteDescWorker(QThread):
            finished = _sig(str)   # generated description text
            error = _sig(str)

            def __init__(self, alpha_id):
                super().__init__()
                self.alpha_id = alpha_id

            def run(self):
                import traceback

                # Capture stderr from the CLI subprocess so we can show the
                # real error instead of "Failed to authenticate. API Error: 403".
                stderr_lines: list[str] = []

                def _on_stderr(line: str) -> None:
                    stderr_lines.append(line)

                # Snapshot env vars for diagnostics.
                api_key_val = os.environ.get("ANTHROPIC_API_KEY", "")
                auth_token_val = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
                base_url_val = os.environ.get("ANTHROPIC_BASE_URL", "")

                def _env_summary() -> str:
                    return (
                        f"ANTHROPIC_API_KEY={'set (' + api_key_val[:7] + '...)' if api_key_val else 'unset'}, "
                        f"ANTHROPIC_AUTH_TOKEN={'set' if auth_token_val else 'unset'}, "
                        f"ANTHROPIC_BASE_URL={base_url_val or 'unset (defaults to api.anthropic.com)'}"
                    )

                try:
                    # If only ANTHROPIC_AUTH_TOKEN is set, copy it to
                    # ANTHROPIC_API_KEY so the CLI picks it up.
                    if not api_key_val and auth_token_val:
                        os.environ["ANTHROPIC_API_KEY"] = auth_token_val
                        api_key_val = auth_token_val

                    if not api_key_val:
                        self.error.emit(
                            "Claude authentication not configured.\n\n"
                            "Set ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) in your environment, "
                            "or run `claude login` first.\n\n"
                            "If you launched brain_simulater.py from a terminal that already has "
                            "Claude auth, run it from that same terminal.\n\n"
                            f"[debug] {_env_summary()}"
                        )
                        return

                    from claude_agent_sdk import (
                        ClaudeAgentOptions,
                        ClaudeSDKClient,
                        AssistantMessage,
                        TextBlock,
                    )

                    options = ClaudeAgentOptions(stderr=_on_stderr)

                    async def _run():
                        result_text = ""
                        async with ClaudeSDKClient(options=options) as client:
                            await client.query(f"/write_desc {self.alpha_id}")
                            async for message in client.receive_response():
                                if isinstance(message, AssistantMessage):
                                    for block in message.content:
                                        if isinstance(block, TextBlock):
                                            result_text += block.text
                        return result_text.strip()

                    # Run the async coroutine in a new event loop
                    loop = asyncio.new_event_loop()
                    try:
                        desc = loop.run_until_complete(_run())
                    finally:
                        loop.close()

                    if desc:
                        self.finished.emit(desc)
                    else:
                        stderr_blob = "\n".join(stderr_lines).strip()
                        msg = "No description generated."
                        if stderr_blob:
                            msg += f"\n\n[CLI stderr]\n{stderr_blob}"
                        msg += f"\n\n[debug] {_env_summary()}"
                        self.error.emit(msg)

                except ImportError:
                    self.error.emit("claude_agent_sdk not installed. Run: pip install claude-agent-sdk")
                except Exception as e:
                    tb = traceback.format_exc()
                    stderr_blob = "\n".join(stderr_lines).strip()
                    msg = f"{type(e).__name__}: {e}"
                    if stderr_blob:
                        msg += f"\n\n[CLI stderr]\n{stderr_blob}"
                    msg += f"\n\n[debug] {_env_summary()}"
                    # Log full traceback to file for diagnosis.
                    try:
                        log_dir = os.path.join(os.path.expanduser("~"), ".claude", "logs")
                        os.makedirs(log_dir, exist_ok=True)
                        with open(os.path.join(log_dir, "write_desc.log"), "a", encoding="utf-8") as f:
                            f.write(f"\n--- {datetime.now().isoformat()} ---\n")
                            f.write(f"[env] {_env_summary()}\n")
                            f.write(f"[stderr]\n{stderr_blob or '(empty)'}\n")
                            f.write(f"[exception]\n{tb}\n")
                    except Exception:
                        pass
                    self.error.emit(msg)

        self._write_desc_worker = WriteDescWorker(self._alpha_id)
        self._write_desc_worker.finished.connect(self._on_write_desc_finished)
        self._write_desc_worker.error.connect(self._on_write_desc_error)
        self._write_desc_worker.start()

    def _update_write_desc_elapsed(self):
        elapsed = int((datetime.now() - self._write_desc_start).total_seconds())
        self.prop_write_desc_btn.setText(f"{T('Writing...')} {elapsed}s")

    def _on_write_desc_finished(self, desc_text):
        if hasattr(self, '_write_desc_timer'):
            self._write_desc_timer.stop()
        self.prop_write_desc_btn.setEnabled(True)
        self.prop_write_desc_btn.setText(T("AI Write Desc"))
        self.prop_desc.setPlainText(desc_text)
        self.status_label.setText(T("Description generated — click Update to save"))
        self.status_label.setStyleSheet("color: #89b4fa; font-weight: bold;")
        winsound.Beep(500, 500)

    def _on_write_desc_error(self, msg):
        if hasattr(self, '_write_desc_timer'):
            self._write_desc_timer.stop()
        self.prop_write_desc_btn.setEnabled(True)
        self.prop_write_desc_btn.setText(T("AI Write Desc"))
        QMessageBox.warning(self, T("AI Write Desc Error"), msg)

    def _submit_properties(self):
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("No alpha ID available."))
            return
        name = self.prop_name.text().strip()
        tags_text = self.prop_tags.text().strip()
        tags = [t.strip() for t in tags_text.split(",") if t.strip()] if tags_text else []
        desc = self.prop_desc.toPlainText().strip()

        # API rejects empty strings: "" → 400 "This field may not be blank."
        # Use None to clear a field, or omit the key to leave it unchanged.
        payload = {}
        if name:
            payload["name"] = name
        else:
            payload["name"] = None
        payload["tags"] = tags
        payload["regular"] = {"description": desc if desc else None}
        payload["color"] = self._selected_color

        try:
            self.client.ensure_auth()
            resp = self.client.session.patch(
                f"{self.client.BASE_URL}alphas/{self._alpha_id}",
                json=payload
            )
            if resp.status_code == 401:
                self.client.authenticate(self.client.email, self.client.password)
                resp = self.client.session.patch(
                    f"{self.client.BASE_URL}alphas/{self._alpha_id}",
                    json=payload
                )
            if resp.status_code >= 400:
                try:
                    err = resp.json()
                except Exception:
                    err = resp.text[:500]
                QMessageBox.critical(self, T("Error"), f"{T('Failed to update properties')} ({resp.status_code}):\n{err}")
                return
            QMessageBox.information(self, T("Success"), T("Properties updated."))
        except Exception as e:
            QMessageBox.critical(self, T("Error"), f"{T('Failed to update properties')}: {e}")

    def _on_check_alpha(self):
        """Check if current alpha can be submitted."""
        alpha_id = getattr(self, '_alpha_id', None)
        if not alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("No alpha ID available."))
            return

        self.check_btn.setEnabled(False)
        self.check_btn.setText(T("Checking..."))
        self.status_label.setText(T("Checking..."))
        self.status_label.setStyleSheet("color: #f9e2af; font-weight: bold;")

        # Hit /alphas/{id}/check to trigger and retrieve submission checks
        class CheckWorker(QThread):
            finished = _sig(dict)
            error = _sig(str)
            def __init__(self, client, alpha_id):
                super().__init__()
                self.client = client
                self.alpha_id = alpha_id
            def run(self):
                try:
                    self.client.ensure_auth()
                    url = f"{self.client.BASE_URL}alphas/{self.alpha_id}/check"
                    res = self.client.session.get(url)
                    while "retry-after" in res.headers:
                        sleep_time = max(float(res.headers["Retry-After"]), 3)
                        sleep(sleep_time)
                        res = self.client.session.get(url)
                    res.raise_for_status()
                    data = res.json()
                    # Response shape: {"is": {"checks": [...]}}
                    # Wrap so _display_checks can find is.checks
                    if "is" not in data and "checks" in data:
                        data = {"is": data}
                    self.finished.emit(data)
                except Exception as e:
                    self.error.emit(str(e))

        self._check_worker = CheckWorker(self.client, alpha_id)
        self._check_worker.finished.connect(self._on_check_data_ready)
        self._check_worker.error.connect(self._on_check_error)
        self._check_worker.start()

    def _on_check_data_ready(self, data):
        """Called when check data arrives from /alphas/{id}/check."""
        self.check_btn.setEnabled(True)
        self.check_btn.setText(T("Check"))

        checks = data.get("is", {}).get("checks", [])
        try:
            self._display_checks(data)
            # Force-expand PASS and WARNING sections so user sees results
            if self.checks_pass_btn.isVisible():
                self.checks_pass_btn.setChecked(True)
            if self.checks_warn_btn.isVisible():
                self.checks_warn_btn.setChecked(True)
            if self.checks_fail_btn.isVisible():
                self.checks_fail_btn.setChecked(True)
            from PyQt5.QtWidgets import QApplication
            QApplication.processEvents()
            # Scroll to the check buttons so user sees them
            target = self.checks_warn_btn if self.checks_warn_btn.isVisible() else self.checks_pass_btn
            if target.isVisible():
                self.right_scroll.ensureWidgetVisible(target)
        except Exception as e:
            print(f"[Check] _display_checks ERROR: {e}")
            import traceback; traceback.print_exc()

        has_fail = any(c.get("result") == "FAIL" for c in checks)
        has_warn = any(c.get("result") == "WARNING" for c in checks)
        if has_fail:
            winsound.Beep(300, 400)
            self.status_label.setText(T("Check complete: has FAIL"))
            self.status_label.setStyleSheet("color: #f38ba8; font-weight: bold;")
        elif has_warn:
            winsound.Beep(600, 200)
            self.status_label.setText(T("Check complete: has WARNING"))
            self.status_label.setStyleSheet("color: #f9e2af; font-weight: bold;")
        else:
            winsound.Beep(800, 200)
            self.status_label.setText(T("Check complete: all PASS"))
            self.status_label.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        self.window().statusBar().showMessage(self.status_label.toPlainText(), 8000)

    def _on_check_error(self, error_msg):
        self.check_btn.setEnabled(True)
        self.check_btn.setText(T("Check"))
        winsound.Beep(300, 400)
        self.status_label.setText(f"{T('Check error: ')}{error_msg}")
        self.status_label.setStyleSheet("color: #f38ba8; font-weight: bold;")

    def _on_submit_alpha(self):
        """Submit current alpha for production (with retry loop in background thread)."""
        alpha_id = getattr(self, '_alpha_id', None)
        if not alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("No alpha ID available."))
            return

        self.submit_btn.setEnabled(False)
        self.submit_btn.setText(T("Submitting..."))

        class SubmitWorker(QThread):
            finished = _sig(int, str)   # status_code, message
            progress = _sig(str)        # status message

            def __init__(self, client, alpha_id):
                super().__init__()
                self.client = client
                self.alpha_id = alpha_id

            def run(self):
                TOTAL_RETRY = 100000
                retry = 0
                while retry < TOTAL_RETRY:
                    try:
                        self.client.ensure_auth()
                        url = f"{self.client.BASE_URL}alphas/{self.alpha_id}/submit"
                        res = self.client.session.post(url)

                        if res.status_code == 400 and "The plain HTTP request was sent to HTTPS port" in res.text:
                            self.progress.emit(T("Submitting..."))
                            res.headers["Retry-After"] = "1.0"

                        # Poll with retry-after
                        while "retry-after" in res.headers:
                            sleep_time = max(float(res.headers["Retry-After"]), 3)
                            # Countdown: show each second
                            for remaining in range(int(sleep_time), 0, -1):
                                self.progress.emit(f"{T('Waiting ')}{remaining}s...")
                                sleep(1)
                            res = self.client.session.get(url)

                        if res.status_code == 429:
                            self.progress.emit(T("Rate limited, waiting 60s..."))
                            sleep(60)
                            retry += 1
                            continue
                        if res.status_code == 401:
                            self.client.authenticate(self.client.email, self.client.password)
                            retry += 1
                            continue
                        if res.status_code == 404:
                            retry += 1
                            continue
                        if res.status_code // 100 == 5:
                            self.progress.emit(T("Server error, retrying in 5s..."))
                            sleep(5)
                            retry += 1
                            continue
                        if res.status_code == 403:
                            fail_checks = []
                            try:
                                checks = res.json().get("is", {}).get("checks", [])
                                fail_checks = [x for x in checks if x.get("result") == "FAIL"]
                            except Exception:
                                pass
                            for info in fail_checks:
                                if info.get("name") == "ALREADY_SUBMITTED":
                                    self.finished.emit(200, T("Already submitted"))
                                    return
                            if any(x.get("name") in ["REGULAR_SUBMISSION", "SUPER_SUBMISSION"] for x in fail_checks):
                                self.finished.emit(403, T("Submission limit exceeded"))
                                return
                            fail_names = ", ".join(x.get("name", "") for x in fail_checks)
                            self.finished.emit(403, f"{T('Submit failed: ')}{fail_names}")
                            return
                        if res.status_code == 200:
                            self.finished.emit(200, T("Submit success!"))
                            return
                        if res.status_code // 100 != 2:
                            self.finished.emit(res.status_code, f"{T('Unexpected status: ')}{res.status_code}")
                            return
                        self.finished.emit(res.status_code, T("Done"))
                        return
                    except Exception as e:
                        self.progress.emit(f"{T('Error: ')}{e}, retrying...")
                        sleep(10)
                        retry += 1
                        continue
                self.finished.emit(0, T("Max retries exceeded"))

        self._submit_worker = SubmitWorker(self.client, alpha_id)
        self._submit_worker.progress.connect(self._on_submit_progress)
        self._submit_worker.finished.connect(self._on_submit_finished)
        self._submit_worker.start()

    def _on_submit_progress(self, msg):
        print(msg, flush=True)
        self.submit_btn.setText(msg)
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: #f9e2af; font-weight: bold;")

    def _on_submit_finished(self, status_code, message):
        self.submit_btn.setEnabled(True)
        self.submit_btn.setText(T("Submit"))
        if status_code == 200:
            winsound.Beep(800, 200)
            self.status_label.setText(message)
            self.status_label.setStyleSheet("color: #a6e3a1; font-weight: bold;")
            self.window().statusBar().showMessage(message, 8000)
        else:
            winsound.Beep(300, 400)
            self.status_label.setText(message)
            self.status_label.setStyleSheet("color: #f38ba8; font-weight: bold;")
            self.window().statusBar().showMessage(message, 8000)

    def _estimate_pc_range(self):
        """利用 PnL 曲线相关性传递性预估 prod corr 范围（同 region，含已提交 alpha）。"""
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("Run a simulation first to get an Alpha ID."))
            return

        # 获取当前 alpha 的 region
        alpha_data = getattr(self, '_last_alpha', None)
        if alpha_data:
            my_region = alpha_data.get('settings', {}).get('region', '')
        else:
            my_region = ''
        if not my_region:
            self.pc_range_label.setText(T("min: --"))
            self.status_label.setText(T("Cannot determine region for PC Range"))
            self.status_label.setStyleSheet("color: #f38ba8;")
            return

        # 获取当前 alpha 的 PnL 数据
        pnl_data = getattr(self.pnl_canvas, '_pnl_data', None)
        if not pnl_data or len(pnl_data) < 2 or not pnl_data[0] or not pnl_data[1]:
            self.pc_range_label.setText(T("min: --"))
            self.status_label.setText(T("No PnL data available for PC Range estimation"))
            self.status_label.setStyleSheet("color: #f38ba8;")
            return

        my_dates = [str(d)[:10] for d in pnl_data[0]]
        my_pnl = list(pnl_data[1])
        if len(my_dates) != len(my_pnl) or len(my_dates) < 10:
            self.pc_range_label.setText(T("min: --"))
            self.status_label.setText(T("PnL data too short for PC Range estimation"))
            self.status_label.setStyleSheet("color: #f38ba8;")
            return

        # Start loading animation
        self.pc_range_btn.setEnabled(False)
        self.pc_range_loading._frame_idx = 0
        self.pc_range_loading._timer = QTimer()
        self.pc_range_loading._timer.timeout.connect(
            lambda: self._update_loading_animation(self.pc_range_loading)
        )
        self.pc_range_loading._timer.start(300)

        class PCRangeWorker(QThread):
            """Worker that delegates to pc_range.estimate_pc_range()."""
            finished = _sig(float, float, int, float)  # range_max, range_min, used_count, my_known_pc
            error = _sig(str)

            def __init__(self, alpha_id, my_region, my_dates, my_pnl):
                super().__init__()
                self.alpha_id = alpha_id
                self.my_region = my_region
                self.my_dates = my_dates
                self.my_pnl = my_pnl

            def run(self):
                try:
                    result = estimate_pc_range(
                        self.alpha_id,
                        my_region=self.my_region,
                        my_dates=self.my_dates,
                        my_pnl=self.my_pnl,
                    )
                    my_known_pc = result['my_known_pc'] if result['my_known_pc'] is not None else -1
                    self.finished.emit(
                        result['range_max'], result['range_min'],
                        result['used_count'], my_known_pc
                    )
                except Exception as e:
                    self.error.emit(str(e))

        self._pc_range_worker = PCRangeWorker(self._alpha_id, my_region, my_dates, my_pnl)
        self._pc_range_worker.finished.connect(self._on_pc_range_finished)
        self._pc_range_worker.error.connect(self._on_pc_range_error)
        self._pc_range_worker.start()

    def _on_pc_range_finished(self, range_max, range_min, used_count, my_known_pc):
        self._stop_loading_animation(self.pc_range_loading)
        self.pc_range_btn.setEnabled(True)
        alpha_data = getattr(self, '_last_alpha', None)
        my_region = alpha_data.get('settings', {}).get('region', '') if alpha_data else ''
        self.pc_range_label.setText(f"{T('min: ')}{range_min:.4f}")
        if used_count > 0:
            self.status_label.setText(f"{T('PC Range ')}({my_region}): {used_count} {T('correlated alphas')}")
        else:
            self.status_label.setText(f"{T('PC Range ')}({my_region}): {T('known value, no correlated estimates')}")
        self.status_label.setStyleSheet("color: #cba6f7; font-weight: bold;")

    def _on_pc_range_error(self, msg):
        self._stop_loading_animation(self.pc_range_loading)
        self.pc_range_btn.setEnabled(True)
        self.pc_range_label.setText(T("min: --"))
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color: #f9e2af;")

    def _fetch_correlation(self, corr_type: str):
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("Run a simulation first to get an Alpha ID."))
            return

        # UI elements mapping
        btn_map = {"self": self.self_corr_btn, "ppc": self.ppc_btn, "prod": self.prod_corr_btn}
        label_map = {"self": self.self_corr_label, "ppc": self.ppc_label, "prod": self.prod_corr_label}
        loading_map = {"self": self.self_corr_loading, "ppc": self.ppc_loading, "prod": self.prod_corr_loading}
        btn_text_map = {"self": T("Self Corr"), "ppc": T("PPC"), "prod": T("Prod Corr")}

        btn = btn_map.get(corr_type)
        label = label_map.get(corr_type)
        loading = loading_map.get(corr_type)
        btn_text = btn_text_map.get(corr_type)

        # Disable button and start loading animation
        if btn:
            btn.setEnabled(False)
        if loading:
            loading._frame_idx = 0
            loading._timer = QTimer()
            loading._timer.timeout.connect(lambda: self._update_loading_animation(loading))
            loading._timer.start(300)  # Update every 300ms

        # Use local_corr for Self Corr and PPC when use_local_corr=True
        if use_local_corr and corr_type in ("self", "ppc"):
            worker = LocalCorrelationWorker(self.client, self._alpha_id, corr_type)
        else:
            worker = CorrelationWorker(self.client, self._alpha_id, corr_type)

        worker.finished.connect(lambda ct, data: self._on_corr_finished(ct, data, btn, label, loading, btn_text))
        worker.error.connect(lambda ct, msg: self._on_corr_error(ct, msg, btn, label, loading, btn_text))
        worker.start()
        self._corr_worker = worker

    def _update_loading_animation(self, loading):
        if hasattr(loading, '_frame_idx'):
            loading._frame_idx = (loading._frame_idx + 1) % len(self.LOADING_FRAMES)
            loading.setText(self.LOADING_FRAMES[loading._frame_idx])

    def _stop_loading_animation(self, loading):
        if hasattr(loading, '_timer') and loading._timer:
            loading._timer.stop()
            loading._timer = None
        loading.setText("")

    def _fetch_all_correlations(self):
        """Fetch Self, PPC, and Prod correlations simultaneously."""
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("Run a simulation first to get an Alpha ID."))
            return

        # Disable the All Corr button and start loading animation
        self.all_corr_btn.setEnabled(False)
        self.all_corr_loading._frame_idx = 0
        self.all_corr_loading._timer = QTimer()
        self.all_corr_loading._timer.timeout.connect(
            lambda: self._update_loading_animation(self.all_corr_loading)
        )
        self.all_corr_loading._timer.start(300)

        # Also disable individual buttons to avoid conflicts
        for b in (self.self_corr_btn, self.ppc_btn, self.prod_corr_btn):
            b.setEnabled(False)

        # Reset retry counts
        if not hasattr(self, '_corr_retry_count'):
            self._corr_retry_count = {}
        for ct in ("self", "ppc", "prod"):
            self._corr_retry_count[ct] = 0

        # Fetch all three correlations in parallel
        self._all_corr_workers = []
        for corr_type in ("self", "ppc", "prod"):
            # Use local_corr for Self Corr and PPC when use_local_corr=True
            if use_local_corr and corr_type in ("self", "ppc"):
                worker = LocalCorrelationWorker(self.client, self._alpha_id, corr_type)
            else:
                worker = CorrelationWorker(self.client, self._alpha_id, corr_type)
            btn_map = {"self": self.self_corr_btn, "ppc": self.ppc_btn, "prod": self.prod_corr_btn}
            label_map = {"self": self.self_corr_label, "ppc": self.ppc_label, "prod": self.prod_corr_label}
            loading_map = {"self": self.self_corr_loading, "ppc": self.ppc_loading, "prod": self.prod_corr_loading}
            btn_text_map = {"self": T("Self Corr"), "ppc": T("PPC"), "prod": T("Prod Corr")}
            btn = btn_map[corr_type]
            label = label_map[corr_type]
            loading = loading_map[corr_type]
            btn_text = btn_text_map[corr_type]
            worker.finished.connect(
                lambda ct, data, b=btn, l=label, ld=loading, bt=btn_text: self._on_corr_finished(ct, data, b, l, ld, bt)
            )
            worker.error.connect(
                lambda ct, msg, b=btn, l=label, ld=loading, bt=btn_text: self._on_corr_error(ct, msg, b, l, ld, bt)
            )
            worker.start()
            self._all_corr_workers.append(worker)

    def _on_corr_finished(self, corr_type, data, btn, label, loading, btn_text):
        self._stop_loading_animation(loading)
        # Stop the All Corr loading animation if all individual ones are done
        self._maybe_stop_all_corr_loading()
        if isinstance(data, dict):
            # API may return nested structure with "records" key
            max_c = data.get("max")
            min_c = data.get("min")
            if max_c is not None and min_c is not None:
                label.setText(f"max: {max_c:.4f}  min: {min_c:.4f}")
                # 缓存 prod corr
                if corr_type == "prod" and self._alpha_id:
                    _lc_set_cached_pc(self._alpha_id, max_c, min_c)
                    self.cached_pc_label.setText(f"max: {max_c:.4f}  min: {min_c:.4f}")
                    # 若此 alpha 未提交，将 PnL curve 保存到 pnl_csv_unsubmitted/
                    alpha_data = getattr(self, '_last_alpha', None)
                    is_submitted = bool(alpha_data and alpha_data.get('dateSubmitted')) if alpha_data else False
                    if not is_submitted:
                        pnl_data = getattr(self.pnl_canvas, '_pnl_data', None)
                        if pnl_data and len(pnl_data) > 1 and pnl_data[0] and pnl_data[1]:
                            region = alpha_data.get('settings', {}).get('region') if alpha_data else None
                            _lc_save_unsubmitted_pnl(self._alpha_id, pnl_data[0], pnl_data[1], region=region)
                # Success - reset retry count
                if hasattr(self, '_corr_retry_count'):
                    self._corr_retry_count[corr_type] = 0
                # Populate the expandable "top/least correlated alphas" panel
                self._apply_corr_records(corr_type, data)
            else:
                # Retry if not available
                self._retry_fetch_corr(corr_type, btn, label, loading, btn_text)
        else:
            # Retry if not available
            self._retry_fetch_corr(corr_type, btn, label, loading, btn_text)
        if btn:
            btn.setEnabled(True)

    def _apply_corr_records(self, corr_type, data):
        """Populate (or clear) the expandable correlated-alphas panel for self/ppc."""
        expand_map = {"self": self.self_corr_expand_btn, "ppc": self.ppc_expand_btn}
        details_map = {"self": self.self_corr_details, "ppc": self.ppc_details}
        expand = expand_map.get(corr_type)
        details = details_map.get(corr_type)
        if expand is None or details is None:
            return  # prod corr has no expand panel
        records = None
        least = False
        if isinstance(data, dict):
            records = data.get("records")
            least = bool(data.get("least", False))
        norm = self._normalize_corr_records(records)
        if norm:
            self._populate_corr_table(details, norm)
            expand.setEnabled(True)
            expand.setToolTip(T("Least correlated alphas") if least else T("Most correlated alphas"))
        else:
            details.setRowCount(0)
            expand.setEnabled(False)
            if expand.isChecked():
                expand.setChecked(False)  # collapse (toggled signal hides details)

    def _populate_corr_table(self, table, recs):
        """Fill QTableWidget with correlation records."""
        table.setRowCount(len(recs))
        for i, r in enumerate(recs):
            rid = r.get('id') or '?'
            corr = r.get('correlation')
            sh = r.get('sharpe')
            ret = r.get('returns')
            turn = r.get('turnover')
            fit = r.get('fitness')
            marg = r.get('margin')
            corr_s = f"{corr:.4f}" if isinstance(corr, (int, float)) else "--"
            sh_s = f"{sh:.2f}" if isinstance(sh, (int, float)) else "--"
            fit_s = f"{fit:.2f}" if isinstance(fit, (int, float)) else "--"
            turn_s = f"{turn * 100:.2f}%" if isinstance(turn, (int, float)) else "--"
            ret_s = f"{ret * 100:.2f}%" if isinstance(ret, (int, float)) else "--"
            marg_s = f"{marg * 10000:.2f}‰" if isinstance(marg, (int, float)) else "--"
            id_item = QTableWidgetItem(rid)
            id_item.setForeground(QColor("#89b4fa"))
            id_item.setData(Qt.UserRole, rid)  # store alpha id for click handler
            table.setItem(i, 0, id_item)
            table.setItem(i, 1, QTableWidgetItem(corr_s))
            table.setItem(i, 2, QTableWidgetItem(sh_s))
            table.setItem(i, 3, QTableWidgetItem(fit_s))
            table.setItem(i, 4, QTableWidgetItem(turn_s))
            table.setItem(i, 5, QTableWidgetItem(ret_s))
            table.setItem(i, 6, QTableWidgetItem(marg_s))
        table.resizeRowsToContents()
        h = table.horizontalHeader().height() + 4
        for r in range(table.rowCount()):
            h += table.rowHeight(r)
        table.setFixedHeight(h)

    def _on_corr_id_clicked(self, table, row, col):
        """Handle click on alpha ID in correlation table."""
        if col != 0:
            return
        item = table.item(row, 0)
        if item:
            aid = item.data(Qt.UserRole)
            if aid:
                main_window = self.window()
                if hasattr(main_window, '_fetch_alpha_by_id'):
                    main_window._fetch_alpha_by_id([aid])

    def _toggle_corr_details(self, corr_type, checked):
        details_map = {"self": self.self_corr_details, "ppc": self.ppc_details}
        expand_map = {"self": self.self_corr_expand_btn, "ppc": self.ppc_expand_btn}
        details = details_map.get(corr_type)
        expand = expand_map.get(corr_type)
        if details:
            details.setVisible(checked)
        if expand:
            expand.setText("▾" if checked else "▸")

    def _normalize_corr_records(self, recs):
        """Normalize API list-of-lists and local list-of-dicts to a list of dicts."""
        out = []
        if not recs:
            return out
        for r in recs:
            if isinstance(r, dict):
                out.append(r)
            elif isinstance(r, list):
                # [id, name, instrumentType, region, universe, correlation,
                #  sharpe, returns, turnover, fitness, margin]
                out.append({
                    'id': r[0] if len(r) > 0 else None,
                    'correlation': r[5] if len(r) > 5 else None,
                    'sharpe': r[6] if len(r) > 6 else None,
                    'returns': r[7] if len(r) > 7 else None,
                    'turnover': r[8] if len(r) > 8 else None,
                    'fitness': r[9] if len(r) > 9 else None,
                    'margin': r[10] if len(r) > 10 else None,
                })
        return out

    def _retry_fetch_corr(self, corr_type, btn, label, loading, btn_text):
        """Auto-retry fetching correlation if not available."""
        self._apply_corr_records(corr_type, None)  # collapse/clear the panel
        if not hasattr(self, '_corr_retry_count'):
            self._corr_retry_count = {}
        retry_count = self._corr_retry_count.get(corr_type, 0)

        if retry_count < 3:
            self._corr_retry_count[corr_type] = retry_count + 1
            label.setText(f"{T('Not available (retry ')}{retry_count + 1}/3)...")
            QTimer.singleShot(2000, lambda: self._fetch_correlation(corr_type))
        else:
            label.setText(T("Not available"))
            self._corr_retry_count[corr_type] = 0

    def _on_corr_error(self, corr_type, msg, btn, label, loading, btn_text):
        self._stop_loading_animation(loading)
        # Stop the All Corr loading animation if all individual ones are done
        self._maybe_stop_all_corr_loading()
        if "404" in msg:
            # Auto-retry on 404
            self._retry_fetch_corr(corr_type, btn, label, loading, btn_text)
        else:
            label.setText(T("Error"))
        if btn:
            btn.setEnabled(True)

    def _maybe_stop_all_corr_loading(self):
        """Stop the All Corr loading animation and re-enable buttons once all
        individual correlation workers have finished (no active retry timers)."""
        if not hasattr(self, '_all_corr_workers'):
            return
        # If any individual loading timer is still running, wait for it
        for loading in (self.self_corr_loading, self.ppc_loading, self.prod_corr_loading):
            if getattr(loading, '_timer', None) is not None:
                return
        # Stop the All Corr loading animation
        self._stop_loading_animation(self.all_corr_loading)
        self.all_corr_btn.setEnabled(True)
        for b in (self.self_corr_btn, self.ppc_btn, self.prod_corr_btn):
            b.setEnabled(True)

    def _open_sim_url(self):
        sim_id = self._sim_id_label.text().replace(T("Sim: "), "")
        if sim_id:
            webbrowser.open(f"https://api.worldquantbrain.com/simulations/{sim_id}")

    def _fetch_inter_correlation(self):
        """Fetch inter-correlation between current alpha and another alpha by PnL.

        Uses ProdMemo corrWorker.js algorithm (identical to corr.py):
        1. Normalize cumulative PnL records
        2. target: calculateReturns (direct diff, no fill)
        3. peer: calculateForwardFilledReturns (forward-fill cum PnL, then re-diff)
        4. Pearson on overlapping dates (sequential accumulation, calculator formula)
        """
        other_id = self.inter_corr_input.text().strip()
        if not other_id:
            QMessageBox.warning(self, T("Input Error"), T("Please enter an Alpha ID for Inter Corr."))
            return
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("Run a simulation first to get an Alpha ID."))
            return
        if other_id == self._alpha_id:
            self.inter_corr_label.setText("1.0")
            return

        self.inter_corr_btn.setEnabled(False)
        self.inter_corr_label.setText("--")
        self.inter_corr_loading._frame_idx = 0
        self.inter_corr_loading._timer = QTimer()
        self.inter_corr_loading._timer.timeout.connect(
            lambda: self._update_loading_animation(self.inter_corr_loading)
        )
        self.inter_corr_loading._timer.start(300)

        class InterCorrWorker(QThread):
            finished = pyqtSignal(float)
            error = pyqtSignal(str)

            def __init__(self, client, alpha_id_1, alpha_id_2):
                super().__init__()
                self.client = client
                self.alpha_id_1 = alpha_id_1
                self.alpha_id_2 = alpha_id_2

            def run(self):
                try:
                    # Fetch PnL for both alphas
                    pnl_1 = self.client._get_pnl(self.alpha_id_1)
                    if not pnl_1:
                        self.error.emit(f"No PnL data for {self.alpha_id_1}")
                        return
                    pnl_2 = self.client._get_pnl(self.alpha_id_2)
                    if not pnl_2:
                        self.error.emit(f"No PnL data for {self.alpha_id_2}")
                        return

                    dates_1, cum_pnl_1 = pnl_1[0], pnl_1[1]
                    dates_2, cum_pnl_2 = pnl_2[0], pnl_2[1]

                    # Flip cumulative PnL if IS sharpe < 0
                    try:
                        self.client.ensure_auth()
                        resp1 = self.client.session.get(f"{self.client.BASE_URL}alphas/{self.alpha_id_1}")
                        if resp1.status_code == 200:
                            a1 = resp1.json()
                            if a1.get("is", {}).get("sharpe", 0) < 0:
                                cum_pnl_1 = [-v for v in cum_pnl_1]
                        resp2 = self.client.session.get(f"{self.client.BASE_URL}alphas/{self.alpha_id_2}")
                        if resp2.status_code == 200:
                            a2 = resp2.json()
                            if a2.get("is", {}).get("sharpe", 0) < 0:
                                cum_pnl_2 = [-v for v in cum_pnl_2]
                    except Exception:
                        pass

                    corr = _ic_calc_inter_corr(
                        [str(d)[:10] for d in dates_1], cum_pnl_1,
                        [str(d)[:10] for d in dates_2], cum_pnl_2
                    )
                    if corr is None:
                        self.error.emit("Correlation could not be computed")
                        return

                    print(corr)
                    self.finished.emit(corr)
                except Exception as e:
                    self.error.emit(str(e))

        self._inter_corr_worker = InterCorrWorker(self.client, self._alpha_id, other_id)
        self._inter_corr_worker.finished.connect(self._on_inter_corr_finished)
        self._inter_corr_worker.error.connect(self._on_inter_corr_error)
        self._inter_corr_worker.start()

    def _on_inter_corr_finished(self, corr):
        self._stop_loading_animation(self.inter_corr_loading)
        self.inter_corr_label.setText(f"{corr:.4f}")
        self.inter_corr_btn.setEnabled(True)

    def _on_inter_corr_error(self, msg):
        self._stop_loading_animation(self.inter_corr_loading)
        self.inter_corr_label.setText(T("Error"))
        self.inter_corr_btn.setEnabled(True)

    def _open_alpha_url(self):
        if self._alpha_id:
            webbrowser.open(f"https://platform.worldquantbrain.com/alpha/{self._alpha_id}")

    def _copy_pnl_to_clipboard(self):
        """Copy raw PnL JSON data to clipboard."""
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Data"), T("Run a simulation first to get PnL data."))
            return

        # Fetch PnL data from API
        try:
            self.client.ensure_auth()
            url = f"{self.client.BASE_URL}alphas/{self._alpha_id}/recordsets/pnl"
            resp = self.client.session.get(url)
            if resp.status_code == 401:
                self.client.ensure_auth()
                resp = self.client.session.get(url)
            resp.raise_for_status()
            data = resp.json()

            # Copy raw JSON directly
            json_text = json.dumps(data, indent=2)
            clipboard = QApplication.clipboard()
            clipboard.setText(json_text)
            records = data.get('records', [])
            QMessageBox.information(self, T("Copied"), f"{T('Copied')} {T('raw PnL data')} ({len(records)} {T('records')}) {T('to clipboard')}.")
        except Exception as e:
            QMessageBox.critical(self, T("Error"), f"{T('Failed to copy PnL data')}: {str(e)}")

    def _open_list_dialog(self):
        if not self._alpha_id:
            QMessageBox.warning(self, T("No Alpha"), T("Run a simulation first to get an Alpha ID."))
            return
        dlg = ListDialog(self.client, self._alpha_id, self)
        dlg.show()

    def _refetch_alpha(self):
        """Refetch current alpha data and refresh display (F5)."""
        if self._state not in (self.STATE_DONE_UNVIEWED, self.STATE_DONE_VIEWED):
            return
        alpha_id = getattr(self, '_alpha_id', None)
        if not alpha_id:
            self._set_status(T("No alpha ID to refetch"), "#f38ba8")
            return
        # Run in a background thread to avoid blocking UI
        class RefetchWorker(QThread):
            finished = _sig(dict, object, object)  # alpha_data, pnl_data, yearly_data
            error = _sig(str)

            def __init__(self, client, alpha_id):
                super().__init__()
                self.client = client
                self.alpha_id = alpha_id

            def run(self):
                try:
                    self.client.ensure_auth()
                    resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{self.alpha_id}")
                    if resp.status_code == 401:
                        self.client.authenticate(self.client.email, self.client.password)
                        resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{self.alpha_id}")
                    resp.raise_for_status()
                    alpha_data = resp.json()
                    pnl_data = self.client._get_pnl(self.alpha_id)
                    yearly_data = self.client._get_yearly_stats(self.alpha_id)
                    self.finished.emit(alpha_data, pnl_data, yearly_data)
                except Exception as e:
                    self.error.emit(str(e))

        self._refetch_worker = RefetchWorker(self.client, alpha_id)
        self._refetch_worker.finished.connect(self._on_refetch_result)
        self._refetch_worker.error.connect(lambda msg: self.status_label.setText(f"{T('Refetch error: ')}{msg}"))
        self._refetch_worker.start()
        self.status_label.setText(T("Refetching..."))
        self.status_label.setStyleSheet("color: #f9e2af;")

    def _on_refetch_result(self, alpha_data, pnl_data, yearly_data):
        """Handle refetched alpha data."""
        alpha = alpha_data
        self._last_alpha = alpha

        self._display_metrics(alpha)
        self._display_classifications(alpha)
        self._display_yearly(yearly_data)
        os_start_date = None
        os_data = alpha.get("os")
        if os_data and isinstance(os_data, dict) and os_data.get("startDate"):
            os_start_date = os_data["startDate"][:10]
        self.pnl_canvas.plot_pnl(pnl_data, os_start_date=os_start_date)
        self._display_checks(alpha)
        self._display_properties(alpha)

        self.status_label.setText(f"{T('Refetched Alpha: ')}{self._alpha_id}")
        self.status_label.setStyleSheet("color: #a6e3a1; font-weight: bold;")

    def _fetch_single_alpha(self, alpha_id):
        try:
            self.status_label.setText(f"{T('Fetching alpha ')}{alpha_id}...")
            self.status_label.setStyleSheet("color: #f9e2af;")

            self.client.ensure_auth()
            alpha_resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{alpha_id}")
            if alpha_resp.status_code == 401:
                self.client.authenticate(self.client.email, self.client.password)
                alpha_resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{alpha_id}")
            alpha_resp.raise_for_status()
            if not alpha_resp.text.strip():
                raise Exception("Empty response from server (session may have expired)")
            alpha_data = alpha_resp.json()
            self._alpha_id = alpha_id
            self._last_alpha = alpha_data

            # 加载 cached prod corr
            cached = _lc_get_cached_pc(alpha_id)
            if cached and cached.get("max") is not None:
                self.cached_pc_label.setText(f"max: {cached['max']:.4f}  min: {cached['min']:.4f}")

            pnl_data = self.client._get_pnl(alpha_id)
            yearly_data = self.client._get_yearly_stats(alpha_id)

            self._display_metrics(alpha_data)
            self._display_classifications(alpha_data)
            self._display_yearly(yearly_data)
            # Determine OS start date for PnL coloring
            os_start_date = None
            os_data = alpha_data.get("os")
            if os_data and isinstance(os_data, dict) and os_data.get("startDate"):
                os_start_date = os_data["startDate"][:10]
            self.pnl_canvas.plot_pnl(pnl_data, os_start_date=os_start_date)
            self._display_checks(alpha_data)
            self._display_properties(alpha_data)

            # Populate expression and settings from fetched alpha
            expr = alpha_data.get("regular", "")
            if isinstance(expr, dict):
                expr = expr.get("code", json.dumps(expr))
            self.expr_input.setPlainText(expr)
            settings = alpha_data.get("settings", {})
            self.region_combo.setCurrentText(settings.get("region", "USA"))
            self._on_region_changed(self.region_combo.currentText())
            self.universe_combo.setCurrentText(settings.get("universe", ""))
            self.delay_spin.setValue(settings.get("delay", 1))
            self._set_decay_value(settings.get("decay", 0))
            self.neutral_combo.setCurrentText(settings.get("neutralization", "NONE"))
            self._set_truncation_value(settings.get("truncation", 0))
            self.pasteur_combo.setCurrentText(settings.get("pasteurization", "ON"))
            self.nan_combo.setCurrentText(settings.get("nanHandling", "OFF"))
            self.max_trade_combo.setCurrentText(settings.get("maxTrade", "OFF"))
            self.max_position_combo.setCurrentText(settings.get("maxPosition", "OFF"))
            self.language_combo.setCurrentText(settings.get("language", "FASTEXPR"))
            self.lookback_spin.setValue(settings.get("lookback", DEFAULT_LOOKBACK))

            self._state = self.STATE_DONE_VIEWED
            self._simulated_universes.add(settings.get("universe", ""))
            self._simulated_neutrals.add(settings.get("neutralization", "NONE"))
            # Make expression and settings read-only in view mode
            self.expr_input.setReadOnly(True)
            self._vscode_btn.setEnabled(False)
            for combo in [self.region_combo, self.universe_combo, self.neutral_combo]:
                combo.setEnabled(False)
            self.delay_spin.setEnabled(False)
            self.decay_input.setEnabled(False)
            self.truncation_input.setEnabled(False)
            self.pasteur_combo.setEnabled(False)
            self.nan_combo.setEnabled(False)
            self.max_trade_combo.setEnabled(False)
            self.max_position_combo.setEnabled(False)
            self.language_combo.setEnabled(False)
            self.lookback_spin.setEnabled(False)
            self._emit_title()
            self._update_mode()

            self.status_label.setText(f"{T('Fetched Alpha: ')}{alpha_id}")
            self.status_label.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        except Exception as e:
            self._set_status(f"{T('Fetch error: ')}{e}", "#f38ba8")
            QMessageBox.critical(self, T("Fetch Error"), str(e))

    def _fetch_single_alpha_async(self, alpha_id):
        """Fetch alpha data in a background thread (non-blocking UI)."""
        self.status_label.setText(f"{T('Fetching alpha ')}{alpha_id}...")
        self.status_label.setStyleSheet("color: #f9e2af;")

        class FetchWorker(QThread):
            finished = _sig(dict, object, object)  # alpha_data, pnl_data, yearly_data
            error = _sig(str)

            def __init__(self, client, aid):
                super().__init__()
                self.client = client
                self.alpha_id = aid

            def run(self):
                try:
                    self.client.ensure_auth()
                    resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{self.alpha_id}")
                    if resp.status_code == 401:
                        self.client.authenticate(self.client.email, self.client.password)
                        resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{self.alpha_id}")
                    resp.raise_for_status()
                    if not resp.text.strip():
                        raise Exception("Empty response from server (session may have expired)")
                    alpha_data = resp.json()
                    pnl_data = self.client._get_pnl(self.alpha_id)
                    yearly_data = self.client._get_yearly_stats(self.alpha_id)
                    self.finished.emit(alpha_data, pnl_data, yearly_data)
                except Exception as e:
                    self.error.emit(str(e))

        self._fetch_worker = FetchWorker(self.client, alpha_id)
        self._fetch_worker.finished.connect(self._on_fetch_async_result)
        self._fetch_worker.error.connect(lambda msg: self._set_status(f"{T('Fetch error: ')}{msg}", "#f38ba8"))
        self._fetch_worker.start()

    def _on_fetch_async_result(self, alpha_data, pnl_data, yearly_data):
        """Handle async-fetched alpha data on the UI thread."""
        alpha_id = alpha_data.get("id", self._alpha_id or "?")
        self._alpha_id = alpha_id
        self._last_alpha = alpha_data

        # Load cached prod corr
        cached = _lc_get_cached_pc(alpha_id)
        if cached and cached.get("max") is not None:
            self.cached_pc_label.setText(f"max: {cached['max']:.4f}  min: {cached['min']:.4f}")

        self._display_metrics(alpha_data)
        self._display_classifications(alpha_data)
        self._display_yearly(yearly_data)
        os_start_date = None
        os_data = alpha_data.get("os")
        if os_data and isinstance(os_data, dict) and os_data.get("startDate"):
            os_start_date = os_data["startDate"][:10]
        self.pnl_canvas.plot_pnl(pnl_data, os_start_date=os_start_date)
        self._display_checks(alpha_data)
        self._display_properties(alpha_data)

        # Populate expression and settings from fetched alpha
        expr = alpha_data.get("regular", "")
        if isinstance(expr, dict):
            expr = expr.get("code", json.dumps(expr))
        self.expr_input.setPlainText(expr)
        settings = alpha_data.get("settings", {})
        self.region_combo.setCurrentText(settings.get("region", "USA"))
        self._on_region_changed(self.region_combo.currentText())
        self.universe_combo.setCurrentText(settings.get("universe", ""))
        self.delay_spin.setValue(settings.get("delay", 1))
        self._set_decay_value(settings.get("decay", 0))
        self.neutral_combo.setCurrentText(settings.get("neutralization", "NONE"))
        self._set_truncation_value(settings.get("truncation", 0))
        self.pasteur_combo.setCurrentText(settings.get("pasteurization", "ON"))
        self.nan_combo.setCurrentText(settings.get("nanHandling", "OFF"))
        self.max_trade_combo.setCurrentText(settings.get("maxTrade", "OFF"))
        self.max_position_combo.setCurrentText(settings.get("maxPosition", "OFF"))
        self.language_combo.setCurrentText(settings.get("language", "FASTEXPR"))
        self.lookback_spin.setValue(settings.get("lookback", DEFAULT_LOOKBACK))

        self._state = self.STATE_DONE_VIEWED
        self._simulated_universes.add(settings.get("universe", ""))
        self._simulated_neutrals.add(settings.get("neutralization", "NONE"))
        # Make expression and settings read-only in view mode
        self.expr_input.setReadOnly(True)
        self._vscode_btn.setEnabled(False)
        for combo in [self.region_combo, self.universe_combo, self.neutral_combo]:
            combo.setEnabled(False)
        self.delay_spin.setEnabled(False)
        self.decay_input.setEnabled(False)
        self.truncation_input.setEnabled(False)
        self.pasteur_combo.setEnabled(False)
        self.nan_combo.setEnabled(False)
        self.max_trade_combo.setEnabled(False)
        self.max_position_combo.setEnabled(False)
        self.language_combo.setEnabled(False)
        self.lookback_spin.setEnabled(False)
        self._emit_title()
        self._update_mode()

        self.status_label.setText(f"{T('Fetched Alpha: ')}{alpha_id}")
        self.status_label.setStyleSheet("color: #a6e3a1; font-weight: bold;")

    def _reset_ui(self):
        self.sim_btn.setEnabled(True)
        self.tune_btn.setEnabled(True)
        self._edit_score_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_label.setStyleSheet("")

    def is_running(self) -> bool:
        return self._state == self.STATE_RUNNING

    def set_tab_base_name(self, name: str):
        self._tab_base_name = name
        self._emit_title(self._current_title_suffix())

    def _current_title_suffix(self) -> str:
        if self._state == self.STATE_RUNNING:
            return "⏳"
        elif self._state == self.STATE_DONE_UNVIEWED:
            return "\U0001f7e2"
        return ""

    def mark_viewed(self):
        if self._state == self.STATE_DONE_UNVIEWED:
            self._state = self.STATE_DONE_VIEWED
            self._emit_title()
            self._update_mode()

    def get_state(self) -> dict:
        return {
            "expression": self.expr_input.toPlainText(),
            "region": self.region_combo.currentText(),
            "universe": self.universe_combo.currentText(),
            "delay": self.delay_spin.value(),
            "decay": self.decay_input.text().strip(),
            "neutralization": self.neutral_combo.currentText(),
            "truncation": self.truncation_input.text().strip(),
            "pasteurization": self.pasteur_combo.currentText(),
            "nanHandling": self.nan_combo.currentText(),
            "unitHandling": "VERIFY",
            "maxTrade": self.max_trade_combo.currentText(),
            "maxPosition": self.max_position_combo.currentText(),
            "language": self.language_combo.currentText(),
            "lookback": self.lookback_spin.value(),
        }

    def set_state(self, state: dict):
        self.expr_input.setPlainText(state.get("expression", "1"))
        self.region_combo.setCurrentText(state.get("region", "USA"))
        self._on_region_changed(self.region_combo.currentText())
        self.universe_combo.setCurrentText(state.get("universe", ""))
        self.delay_spin.setValue(state.get("delay", 1))
        self._set_decay_value(state.get("decay", 0))
        self.neutral_combo.setCurrentText(state.get("neutralization", "NONE"))
        self._set_truncation_value(state.get("truncation", 0))
        self.pasteur_combo.setCurrentText(state.get("pasteurization", "ON"))
        self.nan_combo.setCurrentText(state.get("nanHandling", "OFF"))
        self.max_trade_combo.setCurrentText(state.get("maxTrade", "OFF"))
        self.max_position_combo.setCurrentText(state.get("maxPosition", "OFF"))
        self.language_combo.setCurrentText(state.get("language", "FASTEXPR"))
        self.lookback_spin.setValue(state.get("lookback", DEFAULT_LOOKBACK))
        # Reset simulated decay and truncation sets when loading state
        self._simulated_decay = set()
        self._simulated_truncation = set()


# ──────────────────────────────────────────────
#  Custom Title Bar (for frameless window)
# ──────────────────────────────────────────────
class CustomTitleBar(QWidget):
    """Custom title bar with icon, title, Settings button, and window control buttons."""

    def __init__(self, parent_window):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self._drag_pos = None
        self.setFixedHeight(32)
        self.setStyleSheet("background-color: #1e1e2e;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(6)

        # Icon
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'brain.ico')
        if os.path.exists(ico_path):
            icon_label = QLabel()
            icon_pixmap = QIcon(ico_path).pixmap(16, 16)
            icon_label.setPixmap(icon_pixmap)
            icon_label.setFixedSize(16, 16)
            layout.addWidget(icon_label)

        # Title
        self.title_label = QLabel(T("BRAIN Alpha Simulater"))
        self.title_label.setStyleSheet("color: #cdd6f4; font-size: 10pt; font-weight: bold;")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # ── Help button (?) ──
        self.help_btn = QPushButton("?")
        self.help_btn.setFixedSize(40, 28)
        self.help_btn.setToolTip(T("Help"))
        self.help_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #89b4fa; font-size: 14pt; font-weight: bold;
                border: none; padding: 0px;
            }
            QPushButton:hover { background: #313244; border-radius: 4px; }
        """)
        self._help_menu = QMenu(self.help_btn)
        self._help_menu.setStyleSheet("""
            QMenu {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                padding: 4px; font-size: 10pt;
            }
            QMenu::item { padding: 4px 20px; border-radius: 3px; }
            QMenu::item:selected { background: #45475a; color: #89b4fa; }
        """)
        self._help_menu.addAction(T("Shortcuts"), parent_window._show_shortcuts)
        self.help_btn.clicked.connect(self._show_help_menu)

        layout.addWidget(self.help_btn)

        # ── Settings button (⚙) ──
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(40, 28)
        self.settings_btn.setToolTip(T("Settings"))
        self.settings_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #f9e2af; font-size: 14pt; font-weight: bold;
                border: none; padding: 0px;
            }
            QPushButton:hover { background: #313244; border-radius: 4px; }
        """)
        self._settings_menu = QMenu(self.settings_btn)
        self._settings_menu.setStyleSheet("""
            QMenu {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                padding: 4px; font-size: 10pt;
            }
            QMenu::item { padding: 4px 20px; border-radius: 3px; }
            QMenu::item:selected { background: #45475a; color: #89b4fa; }
        """)

        lang_menu = self._settings_menu.addMenu(T("Language"))
        lang_menu.setStyleSheet("""
            QMenu {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                padding: 4px; font-size: 10pt;
            }
            QMenu::item { padding: 4px 20px; border-radius: 3px; }
            QMenu::item:selected { background: #45475a; color: #89b4fa; }
        """)
        self.act_en = lang_menu.addAction("English")
        self.act_en.setCheckable(True)
        self.act_zh = lang_menu.addAction("中文")
        self.act_zh.setCheckable(True)
        if SYSTEM_LANGUAGE == 'Chinese':
            self.act_zh.setChecked(True)
        else:
            self.act_en.setChecked(True)
        self.act_en.triggered.connect(lambda: parent_window._switch_language('English'))
        self.act_zh.triggered.connect(lambda: parent_window._switch_language('Chinese'))

        # Use Local Corr toggle (below Language)
        self.act_local_corr = self._settings_menu.addAction(T("Use Local Corr"))
        self.act_local_corr.setCheckable(True)
        self.act_local_corr.setChecked(bool(use_local_corr))
        self.act_local_corr.setToolTip(T("Compute Self Corr / PPC locally from cached PnL instead of the platform API"))
        self.act_local_corr.triggered.connect(lambda checked: parent_window._toggle_use_local_corr(checked))

        # Strict Platform Parity toggle (below Use Local Corr)
        self.act_strict_parity = self._settings_menu.addAction(T("Corr Strict Platform Parity"))
        self.act_strict_parity.setCheckable(True)
        self.act_strict_parity.setChecked(bool(strict_platform_parity))
        self.act_strict_parity.setEnabled(bool(use_local_corr))
        self.act_strict_parity.setToolTip(T("Exclude freshly-submitted peers from local self/ppc pool to match platform snapshot"))
        self.act_strict_parity.triggered.connect(lambda checked: parent_window._toggle_strict_platform_parity(checked))

        self.settings_btn.clicked.connect(self._show_settings_menu)
        layout.addWidget(self.settings_btn)

        # Separator between Settings and window controls
        sep = QLabel("|")
        sep.setStyleSheet("color: #45475a; font-size: 10pt; margin: 0 2px;")
        layout.addWidget(sep)

        # ── Minimize button ──
        self.min_btn = QPushButton("─")
        self.min_btn.setFixedSize(46, 28)
        self.min_btn.setToolTip(T("Minimize"))
        self.min_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #a6adc8; font-size: 10pt; font-weight: bold;
                border: none; border-radius: 0px;
            }
            QPushButton:hover { background: #313244; color: #cdd6f4; }
        """)
        self.min_btn.clicked.connect(self._on_minimize)
        layout.addWidget(self.min_btn)

        # ── Maximize/Restore button ──
        self.max_btn = QPushButton("□")
        self.max_btn.setFixedSize(46, 28)
        self.max_btn.setToolTip(T("Maximize"))
        self.max_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #a6adc8; font-size: 10pt; font-weight: bold;
                border: none; border-radius: 0px;
            }
            QPushButton:hover { background: #313244; color: #cdd6f4; }
        """)
        self.max_btn.clicked.connect(self._on_maximize)
        layout.addWidget(self.max_btn)

        # ── Close button ──
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(46, 28)
        self.close_btn.setToolTip(T("Quit"))
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #a6adc8; font-size: 14pt; font-weight: bold;
                border: none; border-radius: 0px;
            }
            QPushButton:hover { background: #e64553; color: #1e1e2e; }
        """)
        self.close_btn.clicked.connect(self._on_close)
        layout.addWidget(self.close_btn)

        # Initial state sync
        self._update_max_btn()

    def _show_settings_menu(self):
        """Show the settings menu below the button."""
        btn = self.settings_btn
        pos = btn.mapToGlobal(QPoint(0, btn.height()))
        self._settings_menu.popup(pos)

    def _show_help_menu(self):
        """Show the help menu below the button."""
        btn = self.help_btn
        pos = btn.mapToGlobal(QPoint(0, btn.height()))
        self._help_menu.popup(pos)

    def _on_minimize(self):
        self.parent_window.showMinimized()

    def _on_maximize(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
        else:
            self.parent_window.showMaximized()
        self._update_max_btn()

    def _on_close(self):
        self.parent_window.close()

    def _update_max_btn(self):
        if self.parent_window.isMaximized():
            self.max_btn.setText("❐")
            self.max_btn.setToolTip(T("Restore"))
        else:
            self.max_btn.setText("□")
            self.max_btn.setToolTip(T("Maximize"))

    def update_language_checks(self, lang):
        """Update the check marks in the Language submenu."""
        self.act_en.setChecked(lang == 'English')
        self.act_zh.setChecked(lang == 'Chinese')

    # ── Drag to move ──
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.parent_window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            # If maximized, first restore and reposition so cursor stays proportional
            if self.parent_window.isMaximized():
                # Calculate proportional position
                screen = self.parent_window.screen().availableGeometry()
                ratio = event.globalPos().x() / screen.width()
                self.parent_window.showNormal()
                self._update_max_btn()
                # Move so cursor is at same proportional position on the restored window
                new_x = event.globalPos().x() - int(self.parent_window.width() * ratio)
                self.parent_window.move(new_x, event.globalPos().y() - self._drag_pos.y())
                self._drag_pos = event.globalPos() - self.parent_window.frameGeometry().topLeft()
            else:
                self.parent_window.move(event.globalPos() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_maximize()
        super().mouseDoubleClickEvent(event)


# ──────────────────────────────────────────────
#  Main Window (multi-tab)
# ──────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.client = BrainClient()
        self.tab_counter = 0
        self._next_batch_id = 0
        self._sim_queue = []
        self._first_done_layout_fixed = False
        # 启动时迁移旧的 pnl_csv/ → pnl_csv_submitted/
        _lc_migrate_old_pnl_dir()
        # 启动时迁移扁平 CSV 到 region 子目录
        _lc_migrate_pnl_to_region_dirs()
        self._init_ui()

        # ── System tray ──
        self._tray_icon = QSystemTrayIcon(self)
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'brain.ico')
        if os.path.exists(ico_path):
            app_icon = QIcon(ico_path)
        else:
            app_icon = QApplication.windowIcon()
            if app_icon.isNull():
                pixmap = QPixmap(16, 16)
                pixmap.fill(QColor('#89b4fa'))
                app_icon = QIcon(pixmap)
        self._tray_icon.setIcon(app_icon)
        self._tray_icon.setToolTip(T("BRAIN Alpha Simulater"))

        tray_menu = QMenu()
        show_action = tray_menu.addAction(T("Show"))
        show_action.triggered.connect(self._tray_show)
        quit_action = tray_menu.addAction(T("Quit"))
        quit_action.triggered.connect(QApplication.quit)
        self._tray_icon.setContextMenu(tray_menu)
        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()

        # Timer to check and correct slot count every 30 seconds
        self._slot_check_timer = QTimer(self)
        self._slot_check_timer.timeout.connect(self._on_slot_check_timer)
        self._slot_check_timer.start(1000)  # 1 second interval for countdown

        # Timer to auto-refresh Today Simulated count every 10 minutes
        self._today_sim_refresh_timer = QTimer(self)
        self._today_sim_refresh_timer.timeout.connect(self._fetch_today_sim_count)
        self._today_sim_refresh_timer.start(600000)  # 10 minutes

    def nativeEvent(self, eventType, message):
        """Handle WM_NCHITTEST for frameless window resize on Windows."""
        if eventType == b'windows_generic_MSG':
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                # Get cursor position (screen coordinates)
                x = msg.lParam & 0xFFFF
                y = (msg.lParam >> 16) & 0xFFFF
                # Convert to signed for multi-monitor
                if x > 32767: x -= 65536
                if y > 32767: y -= 65536
                rect = self.frameGeometry()
                # Border widths for resize detection
                border = 6
                # Determine hit zone
                on_left   = x >= rect.left() and x < rect.left() + border
                on_right  = x < rect.right()  and x >= rect.right() - border
                on_top    = y >= rect.top()   and y < rect.top() + border
                on_bottom = y < rect.bottom()  and y >= rect.bottom() - border
                on_top_left    = on_top and on_left
                on_top_right   = on_top and on_right
                on_bottom_left = on_bottom and on_left
                on_bottom_right= on_bottom and on_right
                if on_top_left:     result = 0xD  # HTTOPLEFT
                elif on_top_right:  result = 0xE  # HTTOPRIGHT
                elif on_bottom_left:result = 0x10 # HTBOTTOMLEFT
                elif on_bottom_right:result= 0x11 # HTBOTTOMRIGHT
                elif on_left:       result = 0xA  # HTLEFT
                elif on_right:      result = 0xB  # HTRIGHT
                elif on_top:        result = 0xC  # HTTOP
                elif on_bottom:     result = 0xF  # HTBOTTOM
                else:
                    # Check if in title bar area
                    title_bar_h = self._title_bar.height() if hasattr(self, '_title_bar') else 32
                    if y >= rect.top() and y < rect.top() + title_bar_h and x >= rect.left() and x < rect.right():
                        # Check if not on a button
                        child = self.childAt(self.mapFromGlobal(QPoint(x, y)))
                        if child and isinstance(child, QPushButton):
                            result = 0x1  # HTCLIENT — let button handle click
                        else:
                            result = 0x2  # HTCAPTION — drag to move
                    else:
                        result = 0x1  # HTCLIENT
                if result != 0x1:
                    return True, int(result)
        return super().nativeEvent(eventType, message)

    def changeEvent(self, event):
        """Handle window state changes: minimize-to-tray, title bar sync."""
        if event.type() == event.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized:
                QTimer.singleShot(0, self.hide)
                self._tray_icon.showMessage(
                    T("BRAIN Alpha Simulater"),
                    T("Minimized to tray. Click to restore."),
                    QSystemTrayIcon.Information,
                    2000
                )
                event.accept()
                return
            # Sync title bar maximize/restore button
            if hasattr(self, '_title_bar'):
                self._title_bar._update_max_btn()
        super().changeEvent(event)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._tray_show()

    def _tray_show(self):
        self.showMaximized()
        self.activateWindow()
        self.raise_()

    def _update_et_clock(self):
        """Update the Eastern Time clock label every second.
        Auto-detects DST: EDT (UTC-4) in summer, EST (UTC-5) in winter.
        """
        utc_now = dt_datetime.now(timezone.utc)
        # US DST: 2nd Sunday of March 02:00 EST to 1st Sunday of November 02:00 EST
        year = utc_now.year
        # 2nd Sunday of March
        mar1 = dt_datetime(year, 3, 1, tzinfo=timezone.utc)
        mar_sun = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)  # 2nd Sunday
        dst_start = mar_sun.replace(hour=7)  # 02:00 EST = 07:00 UTC
        # 1st Sunday of November
        nov1 = dt_datetime(year, 11, 1, tzinfo=timezone.utc)
        nov_sun = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)  # 1st Sunday
        dst_end = nov_sun.replace(hour=6)  # 02:00 EST (falls back) = 06:00 UTC
        offset = timedelta(hours=-4) if dst_start <= utc_now < dst_end else timedelta(hours=-5)
        et_now = utc_now + offset
        # self._et_clock_label.setText(f"EST {et_now.strftime('%H:%M:%S')}")
        self._et_clock_label.setText(f"{T('EST')} {et_now.strftime('%H:%M:%S')}")

    def _get_max_running_weight(self) -> int:
        """Return the user-configured maximum running weight (default 8)."""
        try:
            return int(self._max_running_weight_spin.value())
        except Exception:
            return 8

    def _on_max_weight_changed(self, new_value):
        """When max weight changes, sync Fill spin and drain queue."""
        # Update all tabs' fill_count_spin to match
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab):
                tab.fill_count_spin.setValue(new_value)
        self._drain_queue()

    def _load_credentials(self):
        """
        Load credentials from file or use DEFAULT_* values.
        Priority: DEFAULT_EMAIL/PASSWORD (if set) > credentials file > empty
        Returns: (email, password) tuple
        """
        # If DEFAULT_EMAIL and DEFAULT_PASSWORD are set (not "" or None), use them
        if DEFAULT_EMAIL and DEFAULT_PASSWORD:
            return DEFAULT_EMAIL, DEFAULT_PASSWORD

        # Try to load from credentials file
        if os.path.exists(CREDENTIALS_FILE):
            try:
                with open(CREDENTIALS_FILE, 'r') as f:
                    creds = json.load(f)
                    email = creds.get('email', '')
                    password = creds.get('password', '')
                    if email and password:
                        return email, password
            except (json.JSONDecodeError, IOError):
                pass

        # No credentials available
        return "", ""

    def _save_credentials(self, email, password):
        """Save credentials to file for future auto-login."""
        try:
            with open(CREDENTIALS_FILE, 'w') as f:
                json.dump({'email': email, 'password': password}, f, indent=2)
        except IOError:
            pass  # Silently fail if can't write

    def _init_ui(self):
        self.setWindowTitle(T("BRAIN Alpha Simulater"))
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'brain.ico')
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(4, 0, 4, 0)

        # ── Custom Title Bar ──
        self._title_bar = CustomTitleBar(self)
        main_layout.addWidget(self._title_bar)

        # ── Content area with inner margins ──
        content_widget = QWidget()
        self._content_layout = QVBoxLayout(content_widget)
        self._content_layout.setSpacing(0)
        self._content_layout.setContentsMargins(0, 4, 0, 0)

        # ── Login Bar ──
        self.login_group = QGroupBox(T("Authentication"))
        login_layout = QHBoxLayout(self.login_group)

        login_layout.addWidget(QLabel(T("Email:")))
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("your@email.com")
        self.email_input.setMinimumWidth(220)
        login_layout.addWidget(self.email_input)

        login_layout.addWidget(QLabel(T("Password:")))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMinimumWidth(180)
        login_layout.addWidget(self.password_input)

        self.login_btn = QPushButton(T("Login"))
        self.login_btn.setFixedWidth(80)
        self.login_btn.clicked.connect(self._on_login)
        login_layout.addWidget(self.login_btn)

        self.login_status = QLabel(T("Not logged in"))
        self.login_status.setStyleSheet("color: #6c7086;")
        login_layout.addWidget(self.login_status)

        login_layout.addStretch()
        self._content_layout.addWidget(self.login_group)

        # ── User ID Bar (shown after login) ──
        self.user_id_bar = QWidget()
        self.user_id_bar.setFixedHeight(36)
        self.user_id_bar.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        user_id_layout = QHBoxLayout(self.user_id_bar)
        user_id_layout.setContentsMargins(6, 2, 6, 2)
        user_id_layout.setSpacing(8)
        self.user_id_label = QLabel(T("User ID: -"))
        self.user_id_label.setStyleSheet("color: #a6e3a1; font-weight: bold; font-size: 11pt; padding: 2px 8px;")
        user_id_layout.addWidget(self.user_id_label)

        self._today_sim_label = QLabel(T("Today Simulated: -"))
        self._today_sim_label.setStyleSheet("color: #89b4fa; font-weight: bold; font-size: 11pt; padding: 2px 4px;")
        user_id_layout.addWidget(self._today_sim_label)

        self._today_sim_refresh_btn = QPushButton("⟳")
        self._today_sim_refresh_btn.setFixedSize(24, 24)
        self._today_sim_refresh_btn.setToolTip(T("Refresh today's simulation count"))
        self._today_sim_refresh_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 12pt; font-weight: bold;
                border-radius: 4px; border: 1px solid #45475a;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self._today_sim_refresh_btn.clicked.connect(lambda: self._fetch_today_sim_count(manual=True))
        user_id_layout.addWidget(self._today_sim_refresh_btn)

        self._sim_speed_label = QLabel(T("Speed: -"))
        self._sim_speed_label.setStyleSheet("color: #89b4fa; font-weight: bold; font-size: 11pt; padding: 2px 4px;")
        user_id_layout.addWidget(self._sim_speed_label)

        self._signals_label = QLabel(T("Signals: -"))
        self._signals_label.setStyleSheet("color: #cba6f7; font-weight: bold; font-size: 11pt; padding: 2px 4px;")
        user_id_layout.addWidget(self._signals_label)

        self._signals_refresh_btn = QPushButton("⟳")
        self._signals_refresh_btn.setFixedSize(24, 24)
        self._signals_refresh_btn.setToolTip(T("Refresh this quarter's submission count"))
        self._signals_refresh_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cba6f7; font-size: 12pt; font-weight: bold;
                border-radius: 4px; border: 1px solid #45475a;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self._signals_refresh_btn.clicked.connect(lambda: self._fetch_signals_count(manual=True))
        user_id_layout.addWidget(self._signals_refresh_btn)

        self._pyramids_label = QLabel(T("Pyramids: -"))
        self._pyramids_label.setStyleSheet("color: #a6e3a1; font-weight: bold; font-size: 11pt; padding: 2px 4px;")
        user_id_layout.addWidget(self._pyramids_label)

        self._pyramids_refresh_btn = QPushButton("⟳")
        self._pyramids_refresh_btn.setFixedSize(24, 24)
        self._pyramids_refresh_btn.setToolTip(T("Refresh this quarter's completed pyramids count"))
        self._pyramids_refresh_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #a6e3a1; font-size: 12pt; font-weight: bold;
                border-radius: 4px; border: 1px solid #45475a;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self._pyramids_refresh_btn.clicked.connect(lambda: self._fetch_pyramids_count(manual=True))
        user_id_layout.addWidget(self._pyramids_refresh_btn)

        # ── EST (Eastern Time) live clock ──
        self._et_clock_label = QLabel("")
        self._et_clock_label.setStyleSheet("color: #89b4fa; font-weight: bold; font-size: 11pt; padding: 2px 6px;")
        user_id_layout.addWidget(self._et_clock_label)

        self._et_clock_timer = QTimer(self)
        self._et_clock_timer.timeout.connect(self._update_et_clock)
        self._et_clock_timer.start(1000)
        self._update_et_clock()

        user_id_layout.addStretch()

        self.alpha_id_input = QLineEdit("")
        self.alpha_id_input.setPlaceholderText("Alpha ID")
        self.alpha_id_input.setFixedHeight(28)
        self.alpha_id_input.setFixedWidth(120)
        self.alpha_id_input.setStyleSheet("""
            QLineEdit {
                background: #181825; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 4px; padding: 1px 6px; font-size: 10pt;
            }
            QLineEdit:focus { border: 1px solid #89b4fa; }
        """)
        self.alpha_id_input.returnPressed.connect(self._fetch_alpha_by_id)
        user_id_layout.addWidget(self.alpha_id_input)

        self.fetch_alpha_btn = QPushButton(T("Fetch Alpha"))
        self.fetch_alpha_btn.setFixedHeight(28)
        self.fetch_alpha_btn.setStyleSheet("""
            QPushButton {
                background: #94e2d5; color: #1e1e2e; font-weight: bold;
                border-radius: 4px; padding: 2px 10px; border: none; font-size: 10pt;
            }
            QPushButton:hover { background: #89dceb; }
            QPushButton:disabled { background: #45475a; color: #6c7086; }
        """)
        self.fetch_alpha_btn.clicked.connect(self._fetch_alpha_by_id)
        user_id_layout.addWidget(self.fetch_alpha_btn)

        # Funcs dropdown button
        self._funcs_btn = QPushButton(T("Funcs"))
        self._funcs_btn.setFixedHeight(28)
        self._funcs_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-weight: bold;
                border-radius: 4px; padding: 2px 10px; border: 1px solid #45475a; font-size: 10pt;
            }
            QPushButton:hover { background: #45475a; }
        """)
        funcs_menu = QMenu(self._funcs_btn)
        funcs_menu.setStyleSheet("""
            QMenu {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                padding: 4px; font-size: 10pt;
            }
            QMenu::item { padding: 4px 20px; border-radius: 3px; }
            QMenu::item:selected { background: #45475a; color: #89b4fa; }
        """)

        act_download_ops = funcs_menu.addAction(T("Download Operators"))
        act_download_ops.triggered.connect(self._download_operators)
        act_used_ops = funcs_menu.addAction(T("Used Operators"))
        act_used_ops.triggered.connect(self._archive_used_operators)
        act_unused_ops = funcs_menu.addAction(T("Unused Operators"))
        act_unused_ops.triggered.connect(self._show_unused_operators)
        act_used_df = funcs_menu.addAction(T("Used Datafields"))
        act_used_df.triggered.connect(self._show_used_datafields)
        act_download = funcs_menu.addAction(T("Download Submitted Alphas"))
        act_download.triggered.connect(self._download_submitted_alphas)
        act_archive_ppa = funcs_menu.addAction(T("Archive PPA Tags"))
        act_archive_ppa.triggered.connect(self._archive_ppa_tags)
        act_empty_ppa = funcs_menu.addAction(T("Empty PPA Tags"))
        act_empty_ppa.triggered.connect(self._empty_ppa_tags)
        act_restore_ppa = funcs_menu.addAction(T("Restore PPA Tags"))
        act_restore_ppa.triggered.connect(self._restore_ppa_tags)
        act_archive_osmosis = funcs_menu.addAction(T("Archive Osmosis"))
        act_archive_osmosis.triggered.connect(self._archive_osmosis)
        act_empty_osmosis = funcs_menu.addAction(T("Empty Osmosis"))
        act_empty_osmosis.triggered.connect(self._empty_osmosis)
        act_restore_osmosis = funcs_menu.addAction(T("Restore Osmosis"))
        act_restore_osmosis.triggered.connect(self._restore_osmosis)
        act_copy_aids = funcs_menu.addAction(T("Copy All AIDs"))
        act_copy_aids.triggered.connect(self._copy_all_alpha_ids)

        self._funcs_btn.setMenu(funcs_menu)
        user_id_layout.addWidget(self._funcs_btn)

        self.user_id_bar.setVisible(False)
        self._content_layout.addWidget(self.user_id_bar)

        # ── Tab Widget ──
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self._on_tab_close)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        # Use custom draggable tab bar
        self._tab_bar = DraggableTabBar(self.tab_widget)
        self.tab_widget.setTabBar(self._tab_bar)
        self._tab_bar.tabDropped.connect(self._on_tab_dropped)
        # Right-click context menu on tabs
        self._tab_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)
        self._tab_bar.setStyleSheet("""
            QTabBar::tab {
                background: #313244; padding: 6px 14px; margin-right: 2px;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
                border: 1px solid #45475a;
            }
            QTabBar::tab:selected { font-weight: bold; background: #45475a; border-bottom: 2px solid #89b4fa; }
            QTabBar::tab:hover { background: #45475a; }
        """)

        # Tab bar corner buttons
        corner_widget = QWidget()
        corner_layout = QHBoxLayout(corner_widget)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(2)

        self._tab_list_btn = QPushButton("▼")
        self._tab_list_btn.setFixedSize(28, 28)
        self._tab_list_btn.setToolTip(T("Show all tabs"))
        self._tab_list_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #89b4fa; font-size: 12pt; font-weight: bold;
                border-radius: 4px; border: 1px solid #45475a;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self._tab_list_btn.clicked.connect(self._show_tab_list)
        corner_layout.addWidget(self._tab_list_btn)

        self._move_running_btn = QPushButton("→R")
        self._move_running_btn.setFixedSize(32, 28)
        self._move_running_btn.setToolTip(T("Move all running tabs to the right"))
        self._move_running_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #f38ba8; font-size: 10pt; font-weight: bold;
                border-radius: 4px; border: 1px solid #45475a;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self._move_running_btn.clicked.connect(self._move_running_tabs_right)
        corner_layout.addWidget(self._move_running_btn)

        self._close_idle_btn = QPushButton("×I")
        self._close_idle_btn.setFixedSize(32, 28)
        self._close_idle_btn.setToolTip(T("Close all IDLE tabs"))
        self._close_idle_btn.setStyleSheet("""
            QPushButton {
                background: #313244; color: #6c7086; font-size: 10pt; font-weight: bold;
                border-radius: 4px; border: 1px solid #45475a;
            }
            QPushButton:hover { background: #45475a; color: #a6e3a1; }
        """)
        self._close_idle_btn.clicked.connect(self._close_idle_tabs)
        corner_layout.addWidget(self._close_idle_btn)

        self.tab_widget.setCornerWidget(corner_widget, Qt.TopRightCorner)

        # Ctrl+PgUp / Ctrl+PgDn to switch tabs (Chrome-style)
        QShortcut(QKeySequence("Ctrl+PgUp"), self, lambda: self._switch_tab(-1))
        QShortcut(QKeySequence("Ctrl+PgDown"), self, lambda: self._switch_tab(1))
        # Also support Ctrl+Shift+Tab / Ctrl+Tab
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, lambda: self._switch_tab(-1))
        QShortcut(QKeySequence("Ctrl+Tab"), self, lambda: self._switch_tab(1))
        # Ctrl+Right / Ctrl+Left to jump to nearest unviewed tab
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self._jump_to_unviewed(1))
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self._jump_to_unviewed(-1))
        # Ctrl++ to clone current tab
        QShortcut(QKeySequence("Ctrl++"), self, self._clone_current_tab)
        QShortcut(QKeySequence("Ctrl+="), self, self._clone_current_tab)
        # Ctrl+W to close current tab
        QShortcut(QKeySequence("Ctrl+W"), self, self._close_current_tab)
        # Ctrl+Shift+PgUp to move current tab left
        QShortcut(QKeySequence("Ctrl+Shift+PgUp"), self, lambda: self._move_tab(-1))
        # Ctrl+Shift+PgDown to move current tab right
        QShortcut(QKeySequence("Ctrl+Shift+PgDown"), self, lambda: self._move_tab(1))
        # # Ctrl+Home to switch to first tab
        # QShortcut(QKeySequence("Ctrl+Home"), self, lambda: self.tab_widget.setCurrentIndex(0))
        # # Ctrl+End to switch to last tab
        # QShortcut(QKeySequence("Ctrl+End"), self, lambda: self.tab_widget.setCurrentIndex(self.tab_widget.count() - 1))
        # # Ctrl+Enter to simulate current tab
        # QShortcut(QKeySequence("Ctrl+Enter"), self, self._simulate_current_tab)

        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._simulate_current_tab)
        # Ctrl+Shift+F to Fill current tab
        QShortcut(QKeySequence("Ctrl+Shift+F"), self, self._fill_current_tab)
        # Ctrl+Shift+C to cancel current tab's simulation
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self._cancel_current_tab)
        # Ctrl+Shift+T to Tune current tab
        QShortcut(QKeySequence("Ctrl+Shift+T"), self, self._tune_current_tab)

        # The queue workbench is a thin client over alpha_mining.  It does not
        # instantiate the legacy BrainClient or its submit worker.
        self.queue_workbench = QueueWorkbench()
        self.tab_widget.addTab(self.queue_workbench, "Queue")
        self._content_layout.addWidget(self.tab_widget, stretch=1)
        main_layout.addWidget(content_widget, stretch=1)

        # Status bar

        self._tab_count_label = QLabel(f"{T('Tabs: ')}1")
        self._tab_count_label.setStyleSheet("color: #cdd6f4; font-weight: bold; padding: 0 8px;")
        self.statusBar().addPermanentWidget(self._tab_count_label)
        self._unviewed_label = QLabel(f"{T('Unviewed: ')}0")
        self._unviewed_label.setStyleSheet("color: #fab387; font-weight: bold; padding: 0 8px;")
        self.statusBar().addPermanentWidget(self._unviewed_label)
        self._queued_label = QLabel(f"{T('Queued: ')}0")
        self._queued_label.setStyleSheet("color: #f9e2af; font-weight: bold; padding: 0 8px;")
        self.statusBar().addPermanentWidget(self._queued_label)
        self._running_count_label = QLabel(f"{T('Running: ')}0")
        self._running_count_label.setStyleSheet("color: #89b4fa; font-weight: bold; padding: 0 8px;")
        self.statusBar().addPermanentWidget(self._running_count_label)

        # Custom max running weight (default 8)
        max_weight_lbl = QLabel(T("Max Weight:"))
        max_weight_lbl.setStyleSheet("color: #a6adc8; padding: 0 4px 0 8px;")
        self.statusBar().addPermanentWidget(max_weight_lbl)
        self._max_running_weight_spin = QSpinBox()
        self._max_running_weight_spin.setRange(1, 32)
        self._max_running_weight_spin.setValue(8)
        self._max_running_weight_spin.setFixedHeight(22)
        self._max_running_weight_spin.setFixedWidth(56)
        self._max_running_weight_spin.setToolTip(T("Maximum running weight (GLB=2, others=1)"))
        self._max_running_weight_spin.setStyleSheet("""
            QSpinBox {
                background: #181825; color: #89b4fa; border: 1px solid #45475a;
                border-radius: 4px; padding: 0 4px; font-weight: bold;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 14px; border: none; background: #45475a;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #585b70; }
        """)
        self._max_running_weight_spin.valueChanged.connect(self._on_max_weight_changed)
        self.statusBar().addPermanentWidget(self._max_running_weight_spin)

        self._slot_check_countdown = 30
        self._slot_check_countdown_label = QLabel(f"{T('Check: ')}{self._slot_check_countdown}s")
        self._slot_check_countdown_label.setStyleSheet("color: #6c7086; padding: 0 8px;")
        self.statusBar().addPermanentWidget(self._slot_check_countdown_label)
        self._cancel_all_btn = QPushButton(T("Cancel All"))
        self._cancel_all_btn.setStyleSheet("""
            QPushButton {
                background: #f38ba8; color: #1e1e2e; font-weight: bold;
                border-radius: 4px; padding: 2px 10px; border: none;
            }
            QPushButton:hover { background: #eba0ac; }
        """)
        self._cancel_all_btn.setFixedHeight(22)
        self._cancel_all_btn.clicked.connect(self._cancel_all)
        self.statusBar().addPermanentWidget(self._cancel_all_btn)
        self.statusBar().showMessage(T("Ready"))

        # Add first tab
        self._add_tab()

        # Load credentials: DEFAULT_* takes priority, else load from file
        email, password = self._load_credentials()
        self.email_input.setText(email)
        self.password_input.setText(password)
        # Only auto-login if credentials are available
        if email and password:
            QTimer.singleShot(500, self._on_login)

    def _switch_tab(self, delta: int):
        n = self.tab_widget.count()
        if n <= 1:
            return
        cur = self.tab_widget.currentIndex()
        nxt = (cur + delta) % n
        self.tab_widget.setCurrentIndex(nxt)

    def _jump_to_unviewed(self, direction: int):
        """Jump to the nearest unviewed tab in the given direction.
        direction: 1 = right, -1 = left. Wraps around. No-op if none unviewed."""
        n = self.tab_widget.count()
        if n == 0:
            return
        cur = self.tab_widget.currentIndex()
        for step in range(1, n):
            idx = (cur + direction * step) % n
            tab = self.tab_widget.widget(idx)
            if isinstance(tab, SimulateTab) and tab._state == SimulateTab.STATE_DONE_UNVIEWED:
                self.tab_widget.setCurrentIndex(idx)
                return

    def _on_tab_dropped(self, from_index: int, to_index: int):
        """
        Handle tab drag-and-drop reordering.

        from_index: original position of the dragged tab
        to_index: position where the tab was dropped
        """
        DraggableTabBar._log(f"_on_tab_dropped: from={from_index}, to={to_index}, count={self.tab_widget.count()}")
        if from_index == to_index:
            DraggableTabBar._log("from == to, returning")
            return

        # Get the widget and title from the source position
        widget = self.tab_widget.widget(from_index)
        title = self.tab_widget.tabText(from_index)
        DraggableTabBar._log(f"widget={widget}, title={title}")

        # Remove from source position
        self.tab_widget.removeTab(from_index)
        DraggableTabBar._log(f"after removeTab: count={self.tab_widget.count()}")

        # Adjust drop index if dropping after source (because we removed a tab before it)
        if to_index > from_index:
            to_index -= 1
        DraggableTabBar._log(f"adjusted to_index={to_index}")

        # Insert at new position
        self.tab_widget.insertTab(to_index, widget, title)
        DraggableTabBar._log(f"after insertTab: count={self.tab_widget.count()}")
        self.tab_widget.setCurrentIndex(to_index)

    def _clone_current_tab(self):
        tab = self.tab_widget.currentWidget()
        if tab and isinstance(tab, SimulateTab):
            self._add_tab(tab.get_state())

    def _simulate_current_tab(self):
        tab = self.tab_widget.currentWidget()
        if tab and isinstance(tab, SimulateTab):
            tab._on_simulate()

    def _close_current_tab(self):
        idx = self.tab_widget.currentIndex()
        if idx >= 0:
            self._on_tab_close(idx)

    def _on_tab_context_menu(self, pos):
        """Right-click context menu on the tab bar."""
        idx = self._tab_bar.tabAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 6px; padding: 4px;
            }
            QMenu::item {
                padding: 4px 16px; border-radius: 4px;
            }
            QMenu::item:selected {
                background: #45475a; color: #89b4fa;
            }
            QMenu::separator {
                height: 1px; background: #45475a; margin: 4px 8px;
            }
        """)

        # 1. Close current tab
        close_act = menu.addAction(T("Close Current Tab") + "\tCtrl+W") if idx >= 0 else None

        # 2. Reload (refetch alpha) — only for DONE tabs
        reload_act = None
        if idx >= 0:
            tab = self.tab_widget.widget(idx)
            if isinstance(tab, SimulateTab) and tab._state in (SimulateTab.STATE_DONE_UNVIEWED, SimulateTab.STATE_DONE_VIEWED):
                reload_act = menu.addAction(T("Reload") + "\tF5")

        # 4. Show Reverse — only for DONE tabs
        reverse_act = None
        if idx >= 0:
            tab = self.tab_widget.widget(idx)
            if isinstance(tab, SimulateTab) and tab._state in (SimulateTab.STATE_DONE_UNVIEWED, SimulateTab.STATE_DONE_VIEWED):
                reverse_act = menu.addAction(T("Show Reverse"))

        menu.addSeparator()

        # 5. Move to leftmost
        move_left_act = menu.addAction(T("Move to Leftmost")) if idx > 0 else None

        # 6. Cancel all other running simulations
        cancel_others_act = menu.addAction(T("Cancel Other Simulations"))

        action = menu.exec_(self._tab_bar.mapToGlobal(pos))
        if action is None:
            return
        if action == close_act and idx >= 0:
            self._on_tab_close(idx)
        elif action == reload_act and idx >= 0:
            tab = self.tab_widget.widget(idx)
            if isinstance(tab, SimulateTab):
                tab._refetch_alpha()
        elif action == reverse_act and idx >= 0:
            tab = self.tab_widget.widget(idx)
            if isinstance(tab, SimulateTab):
                self._show_reverse_tab(tab)
        elif action == move_left_act and idx > 0:
            tab = self.tab_widget.widget(idx)
            title = self.tab_widget.tabText(idx)
            self.tab_widget.removeTab(idx)
            self.tab_widget.insertTab(0, tab, title)
            self.tab_widget.setCurrentIndex(0)
        elif action == cancel_others_act:
            self._cancel_others(idx if idx >= 0 else -1)

    def _cancel_others(self, keep_idx: int):
        """Cancel all running simulations except the tab at keep_idx (-1 means keep current)."""
        if keep_idx < 0:
            keep_idx = self.tab_widget.currentIndex()
        for i in range(self.tab_widget.count()):
            if i == keep_idx:
                continue
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab) and tab.is_running():
                tab._on_cancel()
            elif isinstance(tab, SimulateTab) and tab._state == SimulateTab.STATE_QUEUED:
                tab._dequeue_cancel()

    @staticmethod
    def _reverse_alpha_data(alpha, rev_pnl_data=None):
        """Deep-copy alpha dict and reverse all sign-sensitive metrics.
        Reversing = flipping long/short, negating PnL-like values.
        If rev_pnl_data is provided, compute drawdown from the reversed PnL curve.
        """
        a = copy.deepcopy(alpha)

        # Fields whose sign should be flipped in is/os/riskNeutralized/investabilityConstrained/glb sub-regions
        _FLIP_SIGN_FIELDS = {"sharpe", "fitness", "returns", "margin", "beta", "weightCorr"}

        # Compute drawdown from reversed PnL if available
        rev_dd = None
        rev_rn_dd = None
        rev_ic_dd = None
        rev_sub_dd = {}
        if rev_pnl_data and len(rev_pnl_data) > 1 and rev_pnl_data[1]:
            rev_dd = _calc_drawdown(rev_pnl_data[1])
        if rev_pnl_data and len(rev_pnl_data) > 2 and rev_pnl_data[2]:
            rev_rn_dd = _calc_drawdown(rev_pnl_data[2])
        if rev_pnl_data and len(rev_pnl_data) > 3 and rev_pnl_data[3]:
            rev_ic_dd = _calc_drawdown(rev_pnl_data[3])
        # GLB sub-region drawdowns
        if rev_pnl_data and len(rev_pnl_data) > 4 and isinstance(rev_pnl_data[4], dict):
            for region_name in ("AMER", "APAC", "EMEA"):
                sub_pnl = rev_pnl_data[4].get(region_name)
                if sub_pnl:
                    rev_sub_dd[region_name] = _calc_drawdown(sub_pnl)

        def _flip_section(sec, dd_val=None):
            """Flip sign of relevant fields in a metrics section dict."""
            if not isinstance(sec, dict):
                return
            for k in _FLIP_SIGN_FIELDS:
                v = sec.get(k)
                if isinstance(v, (int, float)):
                    sec[k] = -v
            # Swap long/short counts
            lc = sec.get("longCount")
            sc = sec.get("shortCount")
            if lc is not None and sc is not None:
                sec["longCount"], sec["shortCount"] = sc, lc
            # Set computed drawdown from reversed PnL
            if dd_val is not None:
                sec["drawdown"] = dd_val

        # IS / OS
        _flip_section(a.get("is"), rev_dd)
        _flip_section(a.get("os"))
        # Risk Neutralized / Investability Constrained
        if a.get("is"):
            _flip_section(a["is"].get("riskNeutralized"), rev_rn_dd)
            _flip_section(a["is"].get("investabilityConstrained"), rev_ic_dd)
            # GLB sub-regions
            for key, region_name in [("glbAmer", "AMER"), ("glbApac", "APAC"), ("glbEmea", "EMEA")]:
                sub_dd = rev_sub_dd.get(region_name)
                _flip_section(a["is"].get(key), sub_dd)
        if a.get("os"):
            _flip_section(a["os"].get("riskNeutralized"))
            _flip_section(a["os"].get("investabilityConstrained"))
            for key in ("glbAmer", "glbApac", "glbEmea"):
                _flip_section(a["os"].get(key))

        return a

    @staticmethod
    def _reverse_pnl_data(pnl_data):
        """Deep-copy PnL data and negate all PnL values (flip curve along time axis)."""
        if not pnl_data or not pnl_data[0]:
            return pnl_data
        # Convert to mutable list (pnl_data may be a tuple)
        d = list(copy.deepcopy(pnl_data))
        # d = [dates, pnl, risk, invest, sub_regions] or [dates, pnl, risk, invest]
        # Negate pnl values (index 1)
        if len(d) > 1 and d[1]:
            d[1] = [-v for v in d[1]]
        # Negate risk neutralized (index 2)
        if len(d) > 2 and d[2]:
            d[2] = [-v for v in d[2]]
        # Negate investability constrained (index 3)
        if len(d) > 3 and d[3]:
            d[3] = [-v for v in d[3]]
        # Negate sub-region PnLs (index 4, dict of lists)
        if len(d) > 4 and isinstance(d[4], dict):
            for region_name in d[4]:
                if d[4][region_name]:
                    d[4][region_name] = [-v for v in d[4][region_name]]
        return d

    @staticmethod
    def _reverse_yearly_data(yearly_data):
        """Deep-copy yearly stats and reverse sign-sensitive fields, swap long/short."""
        if not yearly_data:
            return yearly_data
        d = copy.deepcopy(yearly_data)

        _FLIP_SIGN_COLS = {"sharpe", "fitness", "returns", "margin"}

        if isinstance(d, dict) and "records" in d:
            records = d["records"]
            schema = d.get("schema", {})
            col_names = [p["name"] for p in schema.get("properties", [])]
            flip_indices = set()
            long_idx = None
            short_idx = None
            for ci, cn in enumerate(col_names):
                if cn in _FLIP_SIGN_COLS:
                    flip_indices.add(ci)
                if cn == "longCount":
                    long_idx = ci
                if cn == "shortCount":
                    short_idx = ci
            for entry in records:
                if isinstance(entry, list):
                    for fi in flip_indices:
                        if fi < len(entry) and isinstance(entry[fi], (int, float)):
                            entry[fi] = -entry[fi]
                    if long_idx is not None and short_idx is not None and long_idx < len(entry) and short_idx < len(entry):
                        entry[long_idx], entry[short_idx] = entry[short_idx], entry[long_idx]
                elif isinstance(entry, dict):
                    for k in _FLIP_SIGN_COLS:
                        v = entry.get(k)
                        if isinstance(v, (int, float)):
                            entry[k] = -v
                    lc = entry.get("longCount")
                    sc = entry.get("shortCount")
                    if lc is not None and sc is not None:
                        entry["longCount"], entry["shortCount"] = sc, lc
        elif isinstance(d, list):
            for entry in d:
                if isinstance(entry, dict):
                    for k in _FLIP_SIGN_COLS:
                        v = entry.get(k)
                        if isinstance(v, (int, float)):
                            entry[k] = -v
                    lc = entry.get("longCount")
                    sc = entry.get("shortCount")
                    if lc is not None and sc is not None:
                        entry["longCount"], entry["shortCount"] = sc, lc
        return d

    def _show_reverse_tab(self, source_tab):
        """Create a new tab showing the reversed version of source_tab's alpha."""
        alpha = getattr(source_tab, '_last_alpha', None)
        alpha_id = getattr(source_tab, '_alpha_id', None) or (alpha.get("id") if alpha else None)

        # If _last_alpha not available, fetch from API
        if not alpha and alpha_id:
            try:
                self.client.ensure_auth()
                resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{alpha_id}")
                if resp.status_code == 401:
                    self.client.authenticate(self.client.email, self.client.password)
                    resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{alpha_id}")
                resp.raise_for_status()
                alpha = resp.json()
                source_tab._last_alpha = alpha
            except Exception:
                return

        if not alpha:
            return
        pnl_data = getattr(source_tab.pnl_canvas, '_pnl_data', None)

        # Reverse the data
        rev_pnl = self._reverse_pnl_data(pnl_data)
        rev_alpha = self._reverse_alpha_data(alpha, rev_pnl)

        # Create new tab
        self.tab_counter += 1
        new_tab = SimulateTab(self.client)
        new_tab.fill_count_spin.setValue(self._max_running_weight_spin.value())

        # Set tab name with 🔁 prefix
        orig_name = source_tab._tab_base_name
        new_tab.set_tab_base_name(f"🔁{orig_name}")
        new_tab.tab_title_update.connect(self._update_tab_title_for(new_tab))
        new_tab.running_state_changed.connect(self._update_running_count)
        new_tab.sim_finished.connect(self._on_tab_sim_finished)

        tab_idx = self.tab_widget.addTab(new_tab, new_tab._tab_base_name)
        self._update_tab_style(tab_idx, new_tab)
        self._update_tab_count()
        self.tab_widget.setCurrentWidget(new_tab)

        # Populate the reversed tab with data (same as _on_result but with reversed data)
        new_tab._state = SimulateTab.STATE_DONE_VIEWED
        new_tab._alpha_id = alpha_id
        new_tab._last_alpha = rev_alpha
        new_tab._emit_title()

        # Display reversed metrics
        new_tab._display_metrics(rev_alpha)
        new_tab._display_classifications(rev_alpha)

        # Fetch yearly stats asynchronously
        if alpha_id:
            class ReverseYearlyWorker(QThread):
                finished = _sig(object)
                def __init__(self, client, alpha_id):
                    super().__init__()
                    self.client = client
                    self.alpha_id = alpha_id
                def run(self):
                    try:
                        self.client.ensure_auth()
                        data = self.client._get_yearly_stats(self.alpha_id)
                        self.finished.emit(data)
                    except Exception:
                        self.finished.emit([])
            new_tab._reverse_yearly_worker = ReverseYearlyWorker(self.client, alpha_id)
            new_tab._reverse_yearly_worker.finished.connect(
                lambda yd: new_tab._display_yearly(self._reverse_yearly_data(yd))
            )
            new_tab._reverse_yearly_worker.start()
            # Show placeholder
            new_tab.yearly_table.setRowCount(1)
            new_tab.yearly_table.setItem(0, 0, QTableWidgetItem("—"))
            new_tab.yearly_table.setItem(0, 1, QTableWidgetItem(T("Loading reversed yearly stats...")))
            new_tab.yearly_table.setFixedHeight(60)
        else:
            new_tab._display_yearly([])

        # Determine OS start date for PnL coloring
        os_start_date = None
        os_data = rev_alpha.get("os")
        if os_data and isinstance(os_data, dict) and os_data.get("startDate"):
            os_start_date = os_data["startDate"][:10]
        new_tab.pnl_canvas.plot_pnl(rev_pnl, os_start_date=os_start_date)

        # Populate expression and settings
        expr = rev_alpha.get("regular", "")
        if isinstance(expr, dict):
            expr = expr.get("code", json.dumps(expr))
        new_tab.expr_input.setPlainText(expr)
        settings = rev_alpha.get("settings", {})
        new_tab.region_combo.setCurrentText(settings.get("region", "USA"))
        new_tab._on_region_changed(new_tab.region_combo.currentText())
        new_tab.universe_combo.setCurrentText(settings.get("universe", ""))
        new_tab.delay_spin.setValue(settings.get("delay", 1))
        new_tab._set_decay_value(settings.get("decay", 0))
        new_tab.neutral_combo.setCurrentText(settings.get("neutralization", "NONE"))
        new_tab._set_truncation_value(settings.get("truncation", 0))
        new_tab.pasteur_combo.setCurrentText(settings.get("pasteurization", "ON"))
        new_tab.nan_combo.setCurrentText(settings.get("nanHandling", "OFF"))
        new_tab.max_trade_combo.setCurrentText(settings.get("maxTrade", "OFF"))
        new_tab.max_position_combo.setCurrentText(settings.get("maxPosition", "OFF"))
        new_tab.language_combo.setCurrentText(settings.get("language", "FASTEXPR"))
        new_tab.lookback_spin.setValue(settings.get("lookback", DEFAULT_LOOKBACK))

        # Display checks and properties
        new_tab._display_checks(rev_alpha)
        new_tab._display_properties(rev_alpha)

        # Make expression and settings read-only
        new_tab.expr_input.setReadOnly(True)
        new_tab._vscode_btn.setEnabled(False)
        for combo in [new_tab.region_combo, new_tab.universe_combo, new_tab.neutral_combo]:
            combo.setEnabled(False)
        new_tab.delay_spin.setEnabled(False)
        new_tab.decay_input.setEnabled(False)
        new_tab.truncation_input.setEnabled(False)
        new_tab.pasteur_combo.setEnabled(False)
        new_tab.nan_combo.setEnabled(False)
        new_tab.max_trade_combo.setEnabled(False)
        new_tab.max_position_combo.setEnabled(False)
        new_tab.language_combo.setEnabled(False)
        new_tab.lookback_spin.setEnabled(False)

        # Switch to view mode UI (hides sim/tune, shows corr/properties/etc.)
        new_tab._update_mode()

        # Load cached prod corr
        if alpha_id:
            cached = _lc_get_cached_pc(alpha_id)
            if cached and cached.get("max") is not None:
                new_tab.cached_pc_label.setText(f"{T('max: ')}{cached['max']:.4f}  {T('min: ')}{cached['min']:.4f}")

        new_tab.status_label.setText(f"🔁 {T('Reversed Alpha: ')}{alpha_id}")
        new_tab.status_label.setStyleSheet("color: #cba6f7; font-weight: bold;")

    def _fill_current_tab(self):
        tab = self.tab_widget.currentWidget()
        if tab and isinstance(tab, SimulateTab):
            tab._fill_8()

    def _cancel_current_tab(self):
        tab = self.tab_widget.currentWidget()
        if tab and isinstance(tab, SimulateTab):
            tab._on_cancel()

    def _tune_current_tab(self):
        tab = self.tab_widget.currentWidget()
        if tab and isinstance(tab, SimulateTab):
            tab._on_tune()

    def _refetch_current_tab(self):
        tab = self.tab_widget.currentWidget()
        if tab and isinstance(tab, SimulateTab):
            tab._refetch_alpha()

    def keyPressEvent(self, event):
        """Handle F5 to refetch current tab's alpha."""
        if event.key() == Qt.Key_F5:
            self._refetch_current_tab()
            return
        super().keyPressEvent(event)

    def _update_running_count(self):
        running_weight = 0
        glb_running = 0
        total_running = 0
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab) and tab.is_running():
                tab_region = getattr(tab, '_current_region', None)
                tune_count = getattr(tab, '_tune_active_count', 0)
                if tune_count > 0:
                    # Tuning: count each concurrent sim as a separate slot
                    slot_cost = 2 if tab_region == "GLB" else 1
                    total_running += tune_count
                    running_weight += tune_count * slot_cost
                    if tab_region == "GLB":
                        glb_running += tune_count
                else:
                    total_running += 1
                    if tab_region == "GLB":
                        running_weight += 2
                        glb_running += 1
                    else:
                        running_weight += 1

        # Display both total count and weighted count
        self._running_count_label.setText(f"{T('Running: ')}{total_running}/{running_weight}")
        self._cancel_all_btn.setVisible(total_running > 0 or len(self._sim_queue) > 0)
        self._update_unviewed_count()
        self._drain_queue()

    def _on_slot_check_timer(self):
        """Called every second to update countdown and perform check every 30 seconds."""
        self._slot_check_countdown -= 1
        if self._slot_check_countdown <= 0:
            self._slot_check_countdown = 30
            self._check_and_correct_slots()
        self._slot_check_countdown_label.setText(f"{T('Check: ')}{self._slot_check_countdown}s")

    def _check_and_correct_slots(self):
        """Background check to correct slot count every 30 seconds."""
        running_weight = 0
        glb_running = 0
        total_running = 0
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab) and tab._state == SimulateTab.STATE_RUNNING:
                tab_region = getattr(tab, '_current_region', None)
                tune_count = getattr(tab, '_tune_active_count', 0)
                if tune_count > 0:
                    slot_cost = 2 if tab_region == "GLB" else 1
                    total_running += tune_count
                    running_weight += tune_count * slot_cost
                    if tab_region == "GLB":
                        glb_running += tune_count
                else:
                    total_running += 1
                    if tab_region == "GLB":
                        running_weight += 2
                        glb_running += 1
                    else:
                        running_weight += 1
        # Update the display
        self._running_count_label.setText(f"{T('Running: ')}{total_running}/{running_weight}")

    def _update_unviewed_count(self):
        unviewed = 0
        queued = 0
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab):
                if tab._state == SimulateTab.STATE_DONE_UNVIEWED:
                    unviewed += 1
                elif tab._state == SimulateTab.STATE_QUEUED:
                    queued += 1
        prev_unviewed = int(self._unviewed_label.text().split(': ')[1])
        if prev_unviewed == 0 and unviewed >= 1:
            winsound.Beep(500, 1000)
        self._unviewed_label.setText(f"{T('Unviewed: ')}{unviewed}")
        self._queued_label.setText(f"{T('Queued: ')}{queued}")

    def _enqueue(self, tab):
        """Add a tab to the simulation queue."""
        if tab not in self._sim_queue:
            self._sim_queue.append(tab)
            self._drain_queue()

    def _dequeue(self, tab):
        """Remove a tab from the simulation queue."""
        if tab in self._sim_queue:
            self._sim_queue.remove(tab)
            # Update queue position labels
            for i, t in enumerate(self._sim_queue):
                if t._state == SimulateTab.STATE_QUEUED:
                    t._emit_title(f"⏳ Q{i + 1}")

    def _drain_queue(self):
        """Start queued simulations if running weight is below limit."""
        while self._sim_queue:
            # Get first queued tab's region
            tab = self._sim_queue[0]
            if tab._state == SimulateTab.STATE_QUEUED and hasattr(tab, 'region_combo'):
                queued_region = tab.region_combo.currentText()
            else:
                queued_region = "USA"
            queued_is_glb = queued_region == "GLB"

            # Calculate weighted running count: GLB = 2, others = 1
            running_weight = 0
            glb_running = 0
            for i in range(self.tab_widget.count()):
                t = self.tab_widget.widget(i)
                if isinstance(t, SimulateTab) and t._state == SimulateTab.STATE_RUNNING:
                    tab_region = getattr(t, '_current_region', None)
                    if tab_region == "GLB":
                        running_weight += 2
                        glb_running += 1
                    else:
                        running_weight += 1

            # Check limits: GLB max 4, total weight per user setting
            max_weight = self._get_max_running_weight()
            if queued_is_glb:
                if glb_running >= 4 or running_weight >= max_weight:
                    break
            else:
                if running_weight >= max_weight:
                    break

            tab = self._sim_queue.pop(0)
            if tab._state == SimulateTab.STATE_QUEUED:
                tab._start_queued()
            # Update remaining queue positions
            for i, t in enumerate(self._sim_queue):
                if t._state == SimulateTab.STATE_QUEUED:
                    t._emit_title(f"⏳ Q{i + 1}")

    def _on_tab_sim_finished(self, finished_tab):
        if not isinstance(finished_tab, SimulateTab):
            return
        batch_id = finished_tab._batch_id
        batch_expr = finished_tab._batch_expr
        if batch_id is None or batch_expr is None:
            return
        # Cancel and close same-batch tabs running the same expression
        tabs_to_close = []
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if tab is finished_tab or not isinstance(tab, SimulateTab):
                continue
            if tab._batch_id == batch_id and tab.is_running() and tab._batch_expr == batch_expr:
                tab._on_cancel()
                tabs_to_close.append(tab)
        # Close cancelled tabs after current event loop iteration
        for tab in tabs_to_close:
            QTimer.singleShot(0, lambda t=tab: self._close_tab_widget(t))

    def _cancel_all(self):
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab) and tab.is_running():
                tab._on_cancel()

    def _fetch_today_sim_count(self, manual=False):
        self._today_sim_refresh_btn.setEnabled(False)
        self._today_sim_manual_refresh = manual
        class Worker(QThread):
            finished = pyqtSignal(int)
            def __init__(self, client):
                super().__init__()
                self.client = client
            def run(self):
                try:
                    count = self.client.get_today_simulated_count()
                    self.finished.emit(count)
                except Exception:
                    self.finished.emit(-1)
        self._today_sim_worker = Worker(self.client)
        self._today_sim_worker.finished.connect(self._on_today_sim_fetched)
        self._today_sim_worker.start()

    def _on_today_sim_fetched(self, count):
        self._today_sim_refresh_btn.setEnabled(True)
        if count < 0:
            self._today_sim_label.setText(T("Today Simulated: ?"))
            return
        # Calculate speed
        now = datetime.now()
        prev_count = getattr(self, '_last_today_sim_count', None)
        prev_time = getattr(self, '_last_today_sim_time', None)
        if prev_count is not None and prev_time is not None:
            delta_count = count - prev_count
            delta_min = (now - prev_time).total_seconds() / 60.0
            if delta_min > 0 and delta_count >= 0:
                speed = delta_count / delta_min
                self._sim_speed_label.setText(f"{T('Speed: ')}{speed:.2f}/min")
        self._last_today_sim_count = count
        self._last_today_sim_time = now

        old_text = self._today_sim_label.text()
        new_text = f"{T('Today Simulated: ')}{count}"
        self._today_sim_label.setText(new_text)
        if old_text == new_text:
            if getattr(self, '_today_sim_manual_refresh', False):
                QMessageBox.information(self, T("Today Simulations"), f"{T('Count unchanged: ')}{count}")
        self._today_sim_manual_refresh = False

    def _fetch_signals_count(self, manual=False):
        self._signals_refresh_btn.setEnabled(False)
        self._signals_manual_refresh = manual
        class Worker(QThread):
            finished = pyqtSignal(object)  # int or str
            def __init__(self, client):
                super().__init__()
                self.client = client
            def run(self):
                try:
                    et = pytz.timezone('US/Eastern')
                    now_et = datetime.now(et)
                    quarter = (now_et.month - 1) // 3
                    quarter_start_month = quarter * 3 + 1
                    quarter_start = now_et.replace(month=quarter_start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
                    date_str = quarter_start.strftime("%Y-%m-%d")

                    self.client.ensure_auth()
                    resp = self.client.session.get(
                        f"{self.client.BASE_URL}users/self/activities/submissions",
                        params={"date>": date_str}
                    )
                    if resp.status_code != 200:
                        self.finished.emit(None)
                        return
                    data = resp.json()
                    total = data.get("total", {}).get("value", "?")
                    self.finished.emit(total)
                except Exception:
                    self.finished.emit(None)
        self._signals_worker = Worker(self.client)
        self._signals_worker.finished.connect(self._on_signals_fetched)
        self._signals_worker.start()

    def _on_signals_fetched(self, total):
        self._signals_refresh_btn.setEnabled(True)
        if total is None:
            self._signals_label.setText(T("Signals: ?"))
            return
        old_text = self._signals_label.text()
        new_text = f"{T('Signals: ')}{total}"
        self._signals_label.setText(new_text)
        if old_text == new_text:
            if getattr(self, '_signals_manual_refresh', False):
                QMessageBox.information(self, T("Signals"), f"{T('Count unchanged: ')}{total}")
        self._signals_manual_refresh = False

        # Cache signals count; auto-download if count increased
        cache_path = os.path.join(_LC_SCRIPT_DIR, 'signals_cache.json')
        prev_total = None
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                prev_total = json.load(f).get('total')
        except Exception:
            pass
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump({'total': total}, f)
        except Exception:
            pass
        if prev_total is not None and isinstance(total, int) and total > prev_total:
            print(f"Signals increased {prev_total} → {total}, auto-downloading submitted alphas...", flush=True)
            self.statusBar().showMessage(f"{T('Signals ')}{prev_total}→{total}{T(', syncing alphas...')}")
            self._download_submitted_alphas()

    def _fetch_pyramids_count(self, manual=False):
        self._pyramids_refresh_btn.setEnabled(False)
        self._pyramids_manual_refresh = manual
        class Worker(QThread):
            finished = pyqtSignal(object, object)  # (completed_count, records_list) or (None, None)
            def __init__(self, client):
                super().__init__()
                self.client = client
            def run(self):
                try:
                    et = pytz.timezone('US/Eastern')
                    now_et = datetime.now(et)
                    quarter = (now_et.month - 1) // 3
                    quarter_start_month = quarter * 3 + 1
                    if quarter_start_month + 3 > 12:
                        end_month = 1
                        end_year = now_et.year + 1
                    else:
                        end_month = quarter_start_month + 3
                        end_year = now_et.year
                    quarter_start = now_et.replace(month=quarter_start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
                    quarter_end = quarter_start.replace(year=end_year, month=end_month, day=1) - __import__('datetime').timedelta(days=1)
                    start_str = quarter_start.strftime("%Y-%m-%d")
                    end_str = quarter_end.strftime("%Y-%m-%d")

                    self.client.ensure_auth()
                    resp = self.client.session.get(
                        f"{self.client.BASE_URL}users/self/activities/pyramid-alphas",
                        params={"startDate": start_str, "endDate": end_str}
                    )
                    if resp.status_code != 200:
                        self.finished.emit(None, None)
                        return
                    data = resp.json()
                    records = data.get("pyramids", [])
                    completed = sum(1 for r in records if r.get("alphaCount", 0) >= 3)
                    self.finished.emit(completed, records)
                except Exception:
                    self.finished.emit(None, None)
        self._pyramids_worker = Worker(self.client)
        self._pyramids_worker.finished.connect(self._on_pyramids_fetched)
        self._pyramids_worker.start()

    def _on_pyramids_fetched(self, count, records):
        self._pyramids_refresh_btn.setEnabled(True)
        if count is None:
            self._pyramids_label.setText(T("Pyramids: ?"))
            return
        # Build lookup key for pyramid counts: "REGION/D{delay}/{CATEGORY_ID}" -> alphaCount
        # e.g. "USA/D1/pv" -> 1, matching the alpha pyramid name format (case-insensitive)
        counts = {}
        if records:
            for r in records:
                cat = r.get("category", {})
                cat_id = cat.get("id", "") if isinstance(cat, dict) else ""
                region = r.get("region", "")
                delay = r.get("delay", 0)
                if cat_id:
                    key = f"{region}/D{delay}/{cat_id}"
                    counts[key] = counts.get(key, 0) + r.get("alphaCount", 0)
        self._pyramid_alpha_counts = counts
        old_text = self._pyramids_label.text()
        new_text = f"{T('Pyramids: ')}{count}"
        self._pyramids_label.setText(new_text)
        if old_text == new_text:
            if getattr(self, '_pyramids_manual_refresh', False):
                QMessageBox.information(self, T("Pyramids"), f"{T('Count unchanged: ')}{count}")
        self._pyramids_manual_refresh = False

    def _download_submitted_alphas(self):
        """Sync submitted alpha metadata and download missing PnL CSVs."""
        self.statusBar().showMessage(T("Downloading submitted alphas..."))

        self._download_worker = DownloadSubmittedAlphasWorker(self.client)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.start()

    def _on_download_progress(self, msg):
        self.statusBar().showMessage(msg)

    def _on_download_finished(self, total, downloaded, failed):
        if total == 0 and downloaded == 0 and failed == 0:
            self.statusBar().showMessage(T("Download failed or no alphas found"), 5000)
        else:
            self.statusBar().showMessage(
                f"{T('Download done: ')}{total}{T(' total, ')}{downloaded}{T(' new, ')}{failed}{T(' failed')}", 8000
            )
        winsound.Beep(500, 500)

    def _download_operators(self):
        """从 BRAIN API 下载所有 operators 并保存为 JSON，然后刷新全局变量。"""
        try:
            self.client.ensure_auth()
            resp = self.client.session.get("https://api.worldquantbrain.com/operators")
            resp.raise_for_status()
            operators = resp.json()
        except Exception as e:
            msg = f"{T('Failed to download operators: ')}{e}"
            print(msg, flush=True)
            self.statusBar().showMessage(msg, 3000)
            return

        out_path = os.path.join(_LC_SCRIPT_DIR, 'operators.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(operators, f, indent=2, ensure_ascii=False)

        _refresh_operators()
        msg = f"{T('Downloaded ')}{len(operators)}{T(' operators to operators.json')}"
        print(msg, flush=True)
        self.statusBar().showMessage(msg, 5000)
        winsound.Beep(500, 500)

    def _archive_used_operators(self):
        """从 alphas_db.json 提取本季度 FASTEXPR alpha 用到的所有 operators，弹出窗口显示。"""
        if not os.path.exists(_LC_ALPHAS_DB_PATH):
            self.statusBar().showMessage(T("No alphas_db.json found. Run Download first."), 3000)
            return

        try:
            with open(_LC_ALPHAS_DB_PATH, 'r', encoding='utf-8') as f:
                db = json.load(f)
        except Exception as e:
            self.statusBar().showMessage(f"{T('Failed to read alphas_db.json: ')}{e}", 3000)
            return

        # 美东时间确定本季度
        et_now = dt_datetime.now(timezone(timedelta(hours=-5)))
        quarter = (et_now.month - 1) // 3 + 1
        year = et_now.year
        q_start = dt_datetime(year, (quarter - 1) * 3 + 1, 1)
        q_end_month = quarter * 3
        q_end = dt_datetime(year, q_end_month, 1) + timedelta(days=32)
        q_end = q_end.replace(day=1)

        # 提取本季度提交的 FASTEXPR alpha 的表达式
        ops_count = {}  # operator -> count
        alpha_count = 0
        sum_unique_ops = 0  # sum of unique ops per alpha
        regular_ops = _get_regular_scope_operators()
        for alpha in (db.values() if isinstance(db, dict) else db):
            settings = alpha.get('settings', {})
            if settings.get('language') != 'FASTEXPR':
                continue
            date_sub = alpha.get('dateSubmitted', '')
            if not date_sub:
                continue
            try:
                sub_dt = dt_datetime.strptime(date_sub[:10], '%Y-%m-%d')
            except ValueError:
                continue
            if sub_dt < q_start or sub_dt >= q_end:
                continue

            code = alpha.get('regular', {}).get('code', '')
            if not code:
                continue
            alpha_count += 1

            # 提取 operators（含符号运算符，- 区分 reverse/subtract）
            a_ops = _extract_ops_from_code(code)
            a_ops.pop('ts_backfill', None)
            a_ops.pop('group_backfill', None)
            if regular_ops is not None:
                a_ops = {op: cnt for op, cnt in a_ops.items() if op in regular_ops}
            sum_unique_ops += len(a_ops)
            for op, cnt in a_ops.items():
                ops_count[op] = ops_count.get(op, 0) + cnt

        sorted_ops = sorted(ops_count.keys())

        # 两种文本格式
        text_names = "\n".join(sorted_ops)
        text_with_count = "\n".join(f"{op} ({ops_count[op]})" for op in sorted_ops)

        # 弹出窗口
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{T('Used Operators — ')}Q{quarter} {year} ({alpha_count}{T(' alphas, ')}{len(sorted_ops)}{T(' operators')})")
        dlg.setMinimumSize(400, 500)
        layout = QVBoxLayout(dlg)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setStyleSheet("""
            QTextEdit {
                background: #1e1e2e; color: #cdd6f4; font-family: Consolas, monospace;
                font-size: 16pt; border: 1px solid #45475a; border-radius: 6px; padding: 8px;
            }
        """)
        text.setPlainText(text_names)
        layout.addWidget(text)

        # Show Count 按钮（切换显示次数）
        show_count_state = {"on": False}
        btn_row = QHBoxLayout()

        # Operators per Alpha 标签（每个alpha的唯一操作符数的平均值）
        ops_per_alpha = sum_unique_ops / alpha_count if alpha_count > 0 else 0
        per_alpha_label = QLabel(f"{T('Operators per Alpha: ')}{ops_per_alpha:.2f}")
        per_alpha_label.setStyleSheet("color: #cdd6f4; font-size: 11pt; font-weight: bold;")

        # Operators used 标签（只算用过几种，不算次数）
        used_label = QLabel(f"{T('Operators used: ')}{len(sorted_ops)}")
        used_label.setStyleSheet("color: #cdd6f4; font-size: 11pt; font-weight: bold;")

        count_btn = QPushButton(T("Show Count"))
        count_btn.setStyleSheet("""
            QPushButton {
                background: #89b4fa; color: #1e1e2e; font-weight: bold;
                border-radius: 4px; padding: 6px 20px; font-size: 11pt;
            }
            QPushButton:hover { background: #74c7ec; }
        """)

        def _toggle_count():
            show_count_state["on"] = not show_count_state["on"]
            if show_count_state["on"]:
                text.setPlainText(text_with_count)
                count_btn.setText(T("Hide Count"))
            else:
                text.setPlainText(text_names)
                count_btn.setText(T("Show Count"))

        count_btn.clicked.connect(_toggle_count)
        btn_row.addWidget(per_alpha_label)
        btn_row.addWidget(used_label)
        btn_row.addStretch()
        btn_row.addWidget(count_btn)
        layout.addLayout(btn_row)

        dlg.exec_()

    def _show_unused_operators(self):
        """显示本季度未使用的 REGULAR scope operators（全量 - used）。"""
        # 获取全量 REGULAR scope operators
        regular_ops = _get_regular_scope_operators()
        all_ops = regular_ops if regular_ops is not None else set(_BRAIN_OPERATORS)
        if not all_ops:
            self.statusBar().showMessage(T("No operators loaded. Download Operators first."), 3000)
            return

        # 获取本季度 used operators（复用 _archive_used_operators 的逻辑）
        used_ops = set()
        if os.path.exists(_LC_ALPHAS_DB_PATH):
            try:
                with open(_LC_ALPHAS_DB_PATH, 'r', encoding='utf-8') as f:
                    db = json.load(f)

                et_now = dt_datetime.now(timezone(timedelta(hours=-5)))
                quarter = (et_now.month - 1) // 3 + 1
                year = et_now.year
                q_start = dt_datetime(year, (quarter - 1) * 3 + 1, 1)
                q_end = dt_datetime(year, quarter * 3, 1) + timedelta(days=32)
                q_end = q_end.replace(day=1)

                for alpha in (db.values() if isinstance(db, dict) else db):
                    settings = alpha.get('settings', {})
                    if settings.get('language') != 'FASTEXPR':
                        continue
                    date_sub = alpha.get('dateSubmitted', '')
                    if not date_sub:
                        continue
                    try:
                        sub_dt = dt_datetime.strptime(date_sub[:10], '%Y-%m-%d')
                    except ValueError:
                        continue
                    if sub_dt < q_start or sub_dt >= q_end:
                        continue
                    code = alpha.get('regular', {}).get('code', '')
                    if not code:
                        continue
                    for op in _extract_ops_from_code(code):
                        used_ops.add(op)
            except Exception:
                pass

        used_ops.discard('ts_backfill')
        used_ops.discard('group_backfill')

        # 差集
        unused_ops = sorted(all_ops - used_ops)

        et_now = dt_datetime.now(timezone(timedelta(hours=-5)))
        quarter = (et_now.month - 1) // 3 + 1
        year = et_now.year

        # 弹出窗口
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{T('Unused Operators — ')}Q{quarter} {year} ({len(unused_ops)}/{len(all_ops)}{T(' unused')})")
        dlg.setMinimumSize(400, 500)
        layout = QVBoxLayout(dlg)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setStyleSheet("""
            QTextEdit {
                background: #1e1e2e; color: #cdd6f4; font-family: Consolas, monospace;
                font-size: 16pt; border: 1px solid #45475a; border-radius: 6px; padding: 8px;
            }
        """)
        text.setPlainText("\n".join(unused_ops))
        layout.addWidget(text)

        dlg.exec_()

    def _show_used_datafields(self):
        """显示本季度 alpha 中用到的所有 datafield，弹出窗口。
        FASTEXPR alpha: 提取代码中所有非已知标识符；
        PYTHON alpha: 从 alpha('field') / alpha["field"] 引用中提取。
        """
        if not os.path.exists(_LC_ALPHAS_DB_PATH):
            self.statusBar().showMessage(T("No alphas_db.json found. Run Download first."), 3000)
            return

        try:
            with open(_LC_ALPHAS_DB_PATH, 'r', encoding='utf-8') as f:
                db = json.load(f)
        except Exception as e:
            self.statusBar().showMessage(f"{T('Failed to read alphas_db.json: ')}{e}", 3000)
            return

        # 美东时间确定本季度
        et_now = dt_datetime.now(timezone(timedelta(hours=-5)))
        quarter = (et_now.month - 1) // 3 + 1
        year = et_now.year
        q_start = dt_datetime(year, (quarter - 1) * 3 + 1, 1)
        q_end = dt_datetime(year, quarter * 3, 1) + timedelta(days=32)
        q_end = q_end.replace(day=1)

        # 构建已知标识符集合（operators + constants + keywords + WHITE_LIST）
        wl = set(WHITE_LIST)
        known = set(_BRAIN_OPERATORS) | _BRAIN_CONSTANTS | _BRAIN_KEYWORDS | {'returns'} | wl

        # 提取本季度 datafield
        df_count = {}
        alpha_count = 0
        sum_unique_fields = 0  # sum of unique fields per alpha
        for alpha in (db.values() if isinstance(db, dict) else db):
            settings = alpha.get('settings', {})
            lang = settings.get('language', 'FASTEXPR')
            if lang not in ('FASTEXPR', 'PYTHON'):
                continue
            date_sub = alpha.get('dateSubmitted', '')
            if not date_sub:
                continue
            try:
                sub_dt = dt_datetime.strptime(date_sub[:10], '%Y-%m-%d')
            except ValueError:
                continue
            if sub_dt < q_start or sub_dt >= q_end:
                continue

            code = alpha.get('regular', {}).get('code', '')
            if not code:
                continue
            alpha_count += 1

            if lang == 'FASTEXPR':
                # 排除赋值左侧的变量名
                assigned = set(re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*=', code))
                # 提取所有标识符
                all_ids = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', code)
                a_fields = set()
                for ident in all_ids:
                    if ident not in known and ident not in assigned:
                        a_fields.add(ident)
                        df_count[ident] = df_count.get(ident, 0) + 1
                sum_unique_fields += len(a_fields)
            else:
                # PYTHON alpha: 从 alpha('field') / alpha["field"] 引用中提取
                py_fields = _extract_fields_from_python(code)
                sum_unique_fields += len(py_fields)
                for f, cnt in py_fields.items():
                    df_count[f] = df_count.get(f, 0) + cnt

        # 按字典排序
        sorted_df = sorted(df_count.items(), key=lambda x: x[0])

        # 两种文本格式
        text_names = "\n".join(name for name, _ in sorted_df)
        text_with_count = "\n".join(f"{name} ({cnt})" for name, cnt in sorted_df)

        # 弹出窗口
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{T('Used Datafields — ')}Q{quarter} {year} ({alpha_count}{T(' alphas, ')}{len(sorted_df)}{T(' datafields')})")
        dlg.setMinimumSize(500, 600)
        layout = QVBoxLayout(dlg)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setStyleSheet("""
            QTextEdit {
                background: #1e1e2e; color: #cdd6f4; font-family: Consolas, monospace;
                font-size: 16pt; border: 1px solid #45475a; border-radius: 6px; padding: 8px;
            }
        """)
        text.setPlainText(text_names)
        layout.addWidget(text)

        # Show Count 切换按钮（默认 hide count）
        show_count_state = {"on": False}
        btn_row = QHBoxLayout()

        # Fields per Alpha 标签（每个alpha的唯一字段数的平均值）
        fields_per_alpha = sum_unique_fields / alpha_count if alpha_count > 0 else 0
        per_alpha_label = QLabel(f"{T('Fields per Alpha: ')}{fields_per_alpha:.2f}")
        per_alpha_label.setStyleSheet("color: #cdd6f4; font-size: 11pt; font-weight: bold;")

        # Fields used 标签（只算用过几种，不算次数）
        used_label = QLabel(f"{T('Fields used: ')}{len(sorted_df)}")
        used_label.setStyleSheet("color: #cdd6f4; font-size: 11pt; font-weight: bold;")

        count_btn = QPushButton(T("Show Count"))
        count_btn.setStyleSheet("""
            QPushButton {
                background: #89b4fa; color: #1e1e2e; font-weight: bold;
                border-radius: 4px; padding: 6px 20px; font-size: 11pt;
            }
            QPushButton:hover { background: #74c7ec; }
        """)

        def _toggle_count():
            show_count_state["on"] = not show_count_state["on"]
            if show_count_state["on"]:
                text.setPlainText(text_with_count)
                count_btn.setText(T("Hide Count"))
            else:
                text.setPlainText(text_names)
                count_btn.setText(T("Show Count"))

        count_btn.clicked.connect(_toggle_count)
        btn_row.addWidget(per_alpha_label)
        btn_row.addWidget(used_label)
        btn_row.addStretch()
        btn_row.addWidget(count_btn)
        layout.addLayout(btn_row)

        dlg.exec_()

    # ── Generic archive/empty/restore helpers ──

    def _load_alphas_db(self):
        """Load alphas_db.json, returning (db_dict, error_msg) or (None, msg)."""
        if not os.path.exists(_LC_ALPHAS_DB_PATH):
            return None, T("No alphas_db.json found. Run Download first.")
        try:
            with open(_LC_ALPHAS_DB_PATH, 'r', encoding='utf-8') as f:
                return json.load(f), None
        except Exception as e:
            return None, f"{T('Failed to read alphas_db.json: ')}{e}"

    def _archive_to_csv(self, filter_fn, csv_prefix, csv_columns, not_found_msg):
        """Generic archive: filter alphas_db.json and write to CSV.

        Args:
            filter_fn: callable(alpha) -> list of (extra_col_name, extra_col_value) tuples
                       Return empty list to skip this alpha.
            csv_prefix: prefix for the CSV filename (e.g. "ppa_tags")
            csv_columns: list of column names (must include 'region', 'delay', 'alpha_id')
            not_found_msg: message when no alphas match
        """
        db, err = self._load_alphas_db()
        if db is None:
            self.statusBar().showMessage(err, 3000)
            return

        groups = {}  # (region, delay) -> [(alpha_id, extra_vals), ...]
        for alpha in (db.values() if isinstance(db, dict) else db):
            extra_vals = filter_fn(alpha)
            if not extra_vals:
                continue
            settings = alpha.get('settings', {})
            region = settings.get('region', 'UNKNOWN')
            delay = settings.get('delay', 'UNKNOWN')
            key = (region, delay)
            for vals in extra_vals:
                groups.setdefault(key, []).append((alpha['id'], vals))

        if not groups:
            self.statusBar().showMessage(not_found_msg, 3000)
            return

        et_now = dt_datetime.now(timezone(timedelta(hours=-5)))
        date_str = et_now.strftime('%Y-%m-%d')

        csv_path = os.path.join(_LC_SCRIPT_DIR, f'{csv_prefix}_archive_{date_str}.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(csv_columns)
            for (region, delay), items in sorted(groups.items()):
                for aid, vals in items:
                    writer.writerow([region, delay, aid] + list(vals))

        total = sum(len(items) for items in groups.values())
        msg = f"{T('Archived ')}{total}{T(' alphas to ')}{csv_prefix}_archive_{date_str}.csv ({len(groups)}{T(' groups')})"
        print(msg, flush=True)
        self.statusBar().showMessage(msg, 5000)
        winsound.Beep(500, 500)

    def _archive_ppa_tags(self):
        """将 alphas_db.json 中 tags 包含 PowerPoolSelected 的 alpha 按 region/delay 分组写入 CSV。"""
        self._archive_to_csv(
            filter_fn=lambda alpha: [
                (str(t),) for t in alpha.get('tags', []) if 'PowerPoolSelected' in str(t)
            ],
            csv_prefix='ppa_tags',
            csv_columns=['region', 'delay', 'alpha_id', 'tag_name'],
            not_found_msg=T("No PowerPoolSelected alphas found."),
        )

    def _show_region_filter_dialog(self, title, info_text):
        """Show a dialog with region/delay filter input. Returns (filter_region, filter_delay) or None on cancel."""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(320)
        dlg.setStyleSheet(STYLES['dialog'])

        layout = QVBoxLayout(dlg)

        info = QLabel(info_text)
        info.setStyleSheet(STYLES['label_dim'] + " padding: 4px;")
        layout.addWidget(info)

        input_line = QLineEdit()
        input_line.setPlaceholderText(T("USA  or  USA/D0  or  (empty for all)"))
        input_line.setStyleSheet(STYLES['input'])
        layout.addWidget(input_line)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton(T("Cancel"))
        cancel_btn.setStyleSheet(STYLES['btn'])
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton(T("OK"))
        confirm_btn.setStyleSheet(STYLES['btn_danger'])
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

        result = {"text": None}

        def on_confirm():
            result["text"] = input_line.text().strip()
            dlg.accept()

        confirm_btn.clicked.connect(on_confirm)
        input_line.returnPressed.connect(on_confirm)

        if dlg.exec_() != QDialog.Accepted:
            return None

        raw = result["text"].upper() if result["text"] else ""
        filter_region = None
        filter_delay = None
        if raw:
            parts = raw.split('/')
            if len(parts) == 1:
                filter_region = parts[0]
            elif len(parts) == 2 and parts[1].startswith('D'):
                filter_region = parts[0]
                try:
                    filter_delay = int(parts[1][1:])
                except ValueError:
                    QMessageBox.warning(self, T("Format Error"), T("Delay must be a number, e.g. USA/D0"))
                    return None
            else:
                QMessageBox.warning(self, T("Format Error"), T("Use format: REGION or REGION/Dx  (e.g. USA or USA/D0)"))
                return None

        return filter_region, filter_delay

    def _empty_ppa_tags(self):
        """清空 PowerPoolSelected 标签：从对应的 tag list 中移除 alpha，可指定 region/delay。"""
        if not os.path.exists(_LC_ALPHAS_DB_PATH):
            self.statusBar().showMessage(T("No alphas_db.json found. Run Download first."), 3000)
            return

        filter_result = self._show_region_filter_dialog(
            "Empty PPA Tags",
            T("Remove alphas from PowerPoolSelected lists.\nLeave empty for ALL, or specify region/delay (e.g. USA or USA/D0):")
        )
        if filter_result is None:
            return
        filter_region, filter_delay = filter_result

        # 从 alphas_db.json 筛选目标 alpha
        try:
            with open(_LC_ALPHAS_DB_PATH, 'r', encoding='utf-8') as f:
                db = json.load(f)
        except Exception as e:
            self.statusBar().showMessage(f"{T('Failed to read alphas_db.json: ')}{e}", 3000)
            return

        # 收集 (alpha_id, tag_name) 对
        targets = []
        for alpha in (db.values() if isinstance(db, dict) else db):
            tags = alpha.get('tags', [])
            ppa_tags = [str(t) for t in tags if 'PowerPoolSelected' in str(t)]
            if not ppa_tags:
                continue
            settings = alpha.get('settings', {})
            region = settings.get('region', 'UNKNOWN')
            delay = settings.get('delay', 'UNKNOWN')
            if filter_region is not None:
                if region != filter_region:
                    continue
                if filter_delay is not None and delay != filter_delay:
                    continue
            for tag_name in ppa_tags:
                targets.append((alpha['id'], tag_name))

        if not targets:
            scope = f"{filter_region}/D{filter_delay}" if filter_region and filter_delay is not None else (filter_region if filter_region else "all")
            self.statusBar().showMessage(f"{T('No PPA alphas found for ')}{scope}.", 3000)
            return

        scope = f"{filter_region}/D{filter_delay}" if filter_region and filter_delay is not None else (filter_region if filter_region else "ALL regions")
        confirm = QMessageBox.question(
            self, "Confirm Empty PPA Tags",
            f"{T('Remove ')}{len(targets)}{T(' alpha-tag pairs ')}({scope}){T(' from PowerPoolSelected lists?\nThis cannot be undone.')}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        # 异步清空
        class EmptyPPAWorker(QThread):
            progress = _sig(int, int, str)   # current, total, alpha_id
            finished = _sig(int, int, list)  # success_count, fail_count, [(aid, tag_name), ...]

            def __init__(self, client, targets_list):
                super().__init__()
                self.client = client
                self.targets = targets_list

            def run(self):
                # 先获取所有 tags，建立 name -> id 映射
                self.client.ensure_auth()
                all_tags = self.client.get_tags()
                tag_map = {}  # name -> id
                for tag in all_tags:
                    name = tag.get("name", "")
                    tid = tag.get("id", "")
                    if name and tid:
                        tag_map[name] = tid

                success = 0
                fail = 0
                ok_targets = []
                for i, (aid, tag_name) in enumerate(self.targets):
                    self.progress.emit(i + 1, len(self.targets), aid)
                    try:
                        tid = tag_map.get(tag_name)
                        if tid:
                            resp = self.client.session.patch(
                                f"{self.client.BASE_URL}tags/{tid}",
                                json={"op": "remove", "name": tag_name, "alphas": [aid]}
                            )
                            if resp.status_code == 401:
                                self.client.authenticate(self.client.email, self.client.password)
                                resp = self.client.session.patch(
                                    f"{self.client.BASE_URL}tags/{tid}",
                                    json={"op": "remove", "name": tag_name, "alphas": [aid]}
                                )
                            if resp.status_code in (200, 204):
                                success += 1
                                ok_targets.append((aid, tag_name))
                            else:
                                fail += 1
                                print(f"Failed to remove {aid} from '{tag_name}': HTTP {resp.status_code}", flush=True)
                        else:
                            # Tag list not found — try removing via PATCH /alphas/{id} tags
                            resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{aid}")
                            if resp.status_code == 401:
                                self.client.authenticate(self.client.email, self.client.password)
                                resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{aid}")
                            if resp.status_code == 200:
                                current_tags = resp.json().get('tags', [])
                                if tag_name in current_tags:
                                    new_tags = [t for t in current_tags if t != tag_name]
                                    resp2 = self.client.session.patch(
                                        f"{self.client.BASE_URL}alphas/{aid}",
                                        json={"tags": new_tags}
                                    )
                                    if resp2.status_code == 401:
                                        self.client.authenticate(self.client.email, self.client.password)
                                        resp2 = self.client.session.patch(
                                            f"{self.client.BASE_URL}alphas/{aid}",
                                            json={"tags": new_tags}
                                        )
                                    if resp2.status_code in (200, 204):
                                        success += 1
                                        ok_targets.append((aid, tag_name))
                                    else:
                                        fail += 1
                                        print(f"Failed to remove tag '{tag_name}' from alpha {aid}: HTTP {resp2.status_code}", flush=True)
                                else:
                                    # Tag already not on alpha
                                    success += 1
                                    ok_targets.append((aid, tag_name))
                            else:
                                fail += 1
                                print(f"Tag list '{tag_name}' not found and cannot fetch alpha {aid}", flush=True)
                    except Exception as e:
                        fail += 1
                        print(f"Error removing {aid} from '{tag_name}': {e}", flush=True)
                self.finished.emit(success, fail, ok_targets)

        self._empty_ppa_worker = EmptyPPAWorker(self.client, targets)
        self._empty_ppa_worker.progress.connect(
            lambda cur, total, aid: self.statusBar().showMessage(f"{T('Emptying PPA tags ')}{cur}/{total}: {aid}")
        )
        self._empty_ppa_worker.finished.connect(self._on_empty_ppa_done)
        self._empty_ppa_worker.start()
        self.statusBar().showMessage(f"{T('Emptying PPA tags ')}{T(' for ')}{len(targets)}{T(' pairs')}...")

    def _on_empty_ppa_done(self, success, fail, ok_targets):
        msg = f"{T('PPA tags cleared: ')}{success}{T(' success, ')}{fail}{T(' failed')}"
        print(msg, flush=True)
        self.statusBar().showMessage(msg, 5000)
        if fail == 0:
            winsound.Beep(500, 500)
        else:
            winsound.Beep(300, 400)
        # Update alphas_db.json: remove cleared tags
        if ok_targets:
            db = _lc_load_local_alphas_db()
            changed = 0
            for aid, tag_name in ok_targets:
                alpha = db.get(aid)
                if alpha and tag_name in alpha.get('tags', []):
                    alpha['tags'] = [t for t in alpha['tags'] if t != tag_name]
                    changed += 1
            if changed:
                _lc_save_local_alphas_db(db)

    def _show_restore_dialog(self, title, archive_prefix, item_label, not_found_msg):
        """Show a restore dialog with CSV dropdown + region/delay filter.

        Returns (csv_path, filter_region, filter_delay, rows) or None on cancel.
        rows is a list of dicts from the CSV.
        """
        pattern = os.path.join(_LC_SCRIPT_DIR, f'{archive_prefix}_archive_*.csv')
        csv_files = sorted(glob.glob(pattern), reverse=True)
        if not csv_files:
            self.statusBar().showMessage(not_found_msg, 3000)
            return None

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(360)
        dlg.setStyleSheet(STYLES['dialog'])

        layout = QVBoxLayout(dlg)

        csv_label = QLabel(T("Archive file:"))
        csv_label.setStyleSheet(STYLES['label_dim'])
        layout.addWidget(csv_label)

        csv_combo = QComboBox()
        csv_names = [os.path.basename(f) for f in csv_files]
        csv_combo.addItems(csv_names)
        csv_combo.setStyleSheet(STYLES['combo'])
        layout.addWidget(csv_combo)

        filter_label = QLabel(T("Region/delay filter (empty = restore all):"))
        filter_label.setStyleSheet(STYLES['label_dim'] + " padding-top: 8px;")
        layout.addWidget(filter_label)

        input_line = QLineEdit()
        input_line.setPlaceholderText(T("USA  or  USA/D0  or  (empty for all)"))
        input_line.setStyleSheet(STYLES['input'])
        layout.addWidget(input_line)

        preview_label = QLabel("")
        preview_label.setStyleSheet(STYLES['label_preview'])
        layout.addWidget(preview_label)

        def update_preview():
            raw = input_line.text().strip().upper()
            idx = csv_combo.currentIndex()
            if idx < 0:
                preview_label.setText("")
                return
            csv_path = csv_files[idx]
            fr, fd = None, None
            if raw:
                parts = raw.split('/')
                if len(parts) == 1:
                    fr = parts[0]
                elif len(parts) == 2 and parts[1].startswith('D'):
                    fr = parts[0]
                    try:
                        fd = int(parts[1][1:])
                    except ValueError:
                        preview_label.setText(T("Invalid format"))
                        return
            try:
                count = 0
                with open(csv_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if fr is not None:
                            if row.get('region', '') != fr:
                                continue
                            if fd is not None and int(row.get('delay', -1)) != fd:
                                continue
                        count += 1
                scope = f"{fr}/D{fd}" if fr and fd is not None else (fr if fr else "all")
                preview_label.setText(f"{T('Will restore ')}{count} {item_label} ({scope})")
            except Exception:
                preview_label.setText("")

        csv_combo.currentIndexChanged.connect(lambda _: update_preview())
        input_line.textChanged.connect(lambda _: update_preview())
        QTimer.singleShot(100, update_preview)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton(T("Cancel"))
        cancel_btn.setStyleSheet(STYLES['btn'])
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)

        restore_btn = QPushButton(T("Restore"))
        restore_btn.setStyleSheet(STYLES['btn_success'])
        btn_row.addWidget(restore_btn)
        layout.addLayout(btn_row)

        result = {"csv_idx": None, "text": None}

        def on_restore():
            result["csv_idx"] = csv_combo.currentIndex()
            result["text"] = input_line.text().strip()
            dlg.accept()

        restore_btn.clicked.connect(on_restore)

        if dlg.exec_() != QDialog.Accepted:
            return None

        raw = result["text"].upper() if result["text"] else ""
        filter_region = None
        filter_delay = None
        if raw:
            parts = raw.split('/')
            if len(parts) == 1:
                filter_region = parts[0]
            elif len(parts) == 2 and parts[1].startswith('D'):
                filter_region = parts[0]
                try:
                    filter_delay = int(parts[1][1:])
                except ValueError:
                    QMessageBox.warning(self, T("Format Error"), T("Delay must be a number, e.g. USA/D0"))
                    return None
            else:
                QMessageBox.warning(self, T("Format Error"), T("Use format: REGION or REGION/Dx  (e.g. USA or USA/D0)"))
                return None

        csv_path = csv_files[result["csv_idx"]]
        rows = []
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if filter_region is not None:
                        if row.get('region', '') != filter_region:
                            continue
                        if filter_delay is not None and int(row.get('delay', -1)) != filter_delay:
                            continue
                    rows.append(row)
        except Exception as e:
            self.statusBar().showMessage(f"{T('Failed to read CSV: ')}{e}", 3000)
            return None

        return csv_path, filter_region, filter_delay, rows

    def _restore_ppa_tags(self):
        """从 ppa_tags_archive_*.csv 恢复 PowerPoolSelected 标签，可指定 region/delay 过滤。"""
        result = self._show_restore_dialog(
            "Restore PPA Tags", "ppa_tags", "alpha-tag pairs",
            T("No ppa_tags_archive_*.csv found. Run Archive PPA Tags first.")
        )
        if result is None:
            return
        csv_path, filter_region, filter_delay, rows = result

        targets = [(row.get('alpha_id', '').strip(), row.get('tag_name', '').strip())
                    for row in rows if row.get('alpha_id', '').strip() and row.get('tag_name', '').strip()]

        if not targets:
            scope = f"{filter_region}/D{filter_delay}" if filter_region and filter_delay is not None else (filter_region if filter_region else "all")
            self.statusBar().showMessage(f"{T('No PPA records found for ')}{scope}{T(' in ')}{os.path.basename(csv_path)}.", 3000)
            return

        scope = f"{filter_region}/D{filter_delay}" if filter_region and filter_delay is not None else (filter_region if filter_region else "ALL regions")
        confirm = QMessageBox.question(
            self, "Confirm Restore PPA Tags",
            f"{T('Restore ')}{len(targets)}{T(' alpha-tag pairs ')}({scope}){T(' from ')}{os.path.basename(csv_path)}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        # 异步恢复
        class RestorePPAWorker(QThread):
            progress = _sig(int, int, str)   # current, total, alpha_id
            finished = _sig(int, int, list)  # success_count, fail_count, [(aid, tag_name), ...]

            def __init__(self, client, targets_list):
                super().__init__()
                self.client = client
                self.targets = targets_list

            def run(self):
                # 先获取所有 tags，建立 name -> id 映射
                self.client.ensure_auth()
                all_tags = self.client.get_tags()
                tag_map = {}  # name -> (id, alphas_set)
                for tag in all_tags:
                    name = tag.get("name", "")
                    tid = tag.get("id", "")
                    existing = set(a.get('id', a) if isinstance(a, dict) else a for a in tag.get("alphas", []))
                    if name and tid:
                        tag_map[name] = (tid, existing)

                success = 0
                fail = 0
                ok_targets = []
                for i, (aid, tag_name) in enumerate(self.targets):
                    self.progress.emit(i + 1, len(self.targets), aid)
                    try:
                        entry = tag_map.get(tag_name)
                        if entry:
                            tid, existing = entry
                            # 已在列表中则跳过
                            if aid in existing:
                                success += 1
                                ok_targets.append((aid, tag_name))
                                continue
                            resp = self.client.session.patch(
                                f"{self.client.BASE_URL}tags/{tid}",
                                json={"op": "add", "name": tag_name, "alphas": [aid]}
                            )
                            if resp.status_code == 401:
                                self.client.authenticate(self.client.email, self.client.password)
                                resp = self.client.session.patch(
                                    f"{self.client.BASE_URL}tags/{tid}",
                                    json={"op": "add", "name": tag_name, "alphas": [aid]}
                                )
                            if resp.status_code in (200, 201, 204):
                                success += 1
                                existing.add(aid)  # 更新本地缓存
                                ok_targets.append((aid, tag_name))
                            else:
                                fail += 1
                                print(f"Failed to add {aid} to '{tag_name}': HTTP {resp.status_code}", flush=True)
                        else:
                            # Tag list not found — try adding via PATCH /alphas/{id} tags
                            resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{aid}")
                            if resp.status_code == 401:
                                self.client.authenticate(self.client.email, self.client.password)
                                resp = self.client.session.get(f"{self.client.BASE_URL}alphas/{aid}")
                            if resp.status_code == 200:
                                current_tags = resp.json().get('tags', [])
                                if tag_name not in current_tags:
                                    new_tags = current_tags + [tag_name]
                                    resp2 = self.client.session.patch(
                                        f"{self.client.BASE_URL}alphas/{aid}",
                                        json={"tags": new_tags}
                                    )
                                    if resp2.status_code == 401:
                                        self.client.authenticate(self.client.email, self.client.password)
                                        resp2 = self.client.session.patch(
                                            f"{self.client.BASE_URL}alphas/{aid}",
                                            json={"tags": new_tags}
                                        )
                                    if resp2.status_code in (200, 204):
                                        success += 1
                                        ok_targets.append((aid, tag_name))
                                    else:
                                        fail += 1
                                        print(f"Failed to add tag '{tag_name}' to alpha {aid}: HTTP {resp2.status_code}", flush=True)
                                else:
                                    # Tag already on alpha
                                    success += 1
                                    ok_targets.append((aid, tag_name))
                            else:
                                # 列表不存在，创建
                                new_tag = self.client.create_tag(tag_name, aid)
                                tid = new_tag.get("id")
                                if tid:
                                    tag_map[tag_name] = (tid, {aid})
                                    success += 1
                                    ok_targets.append((aid, tag_name))
                                else:
                                    fail += 1
                                    print(f"Created list '{tag_name}' but got no ID", flush=True)
                    except Exception as e:
                        fail += 1
                        print(f"Error restoring {aid} to '{tag_name}': {e}", flush=True)
                self.finished.emit(success, fail, ok_targets)

        self._restore_ppa_worker = RestorePPAWorker(self.client, targets)
        self._restore_ppa_worker.progress.connect(
            lambda cur, total, aid: self.statusBar().showMessage(f"{T('Restoring PPA tags ')}{cur}/{total}: {aid}")
        )
        self._restore_ppa_worker.finished.connect(self._on_restore_ppa_done)
        self._restore_ppa_worker.start()
        self.statusBar().showMessage(f"{T('Restoring PPA tags ')}{T(' for ')}{len(targets)}{T(' pairs')}...")

    def _on_restore_ppa_done(self, success, fail, ok_targets):
        msg = f"{T('PPA tags restored: ')}{success}{T(' success, ')}{fail}{T(' failed')}"
        print(msg, flush=True)
        self.statusBar().showMessage(msg, 5000)
        if fail == 0:
            winsound.Beep(500, 500)
        else:
            winsound.Beep(300, 400)
        # Update alphas_db.json: add restored tags
        if ok_targets:
            db = _lc_load_local_alphas_db()
            changed = 0
            for aid, tag_name in ok_targets:
                alpha = db.get(aid)
                if alpha:
                    tags = alpha.get('tags', [])
                    if tag_name not in tags:
                        alpha['tags'] = tags + [tag_name]
                        changed += 1
            if changed:
                _lc_save_local_alphas_db(db)

    def _archive_osmosis(self):
        """将 alphas_db.json 中 osmosisPoints>0 的 alpha 按 region/delay 分组写入 CSV。"""
        self._archive_to_csv(
            filter_fn=lambda alpha: (
                [(alpha.get('osmosisPoints', 0),)] if (alpha.get('osmosisPoints', 0) or 0) > 0 else []
            ),
            csv_prefix='osmosis',
            csv_columns=['region', 'delay', 'alpha_id', 'osmosisPoints'],
            not_found_msg=T("No osmosis alphas found."),
        )

    def _empty_osmosis(self):
        """清空 osmosis points：可指定 region/delay（如 USA/D0），或留空清空所有。"""
        if not os.path.exists(_LC_ALPHAS_DB_PATH):
            self.statusBar().showMessage(T("No alphas_db.json found. Run Download first."), 3000)
            return

        filter_result = self._show_region_filter_dialog(
            "Empty Osmosis",
            T("Leave empty to clear ALL, or specify region/delay (e.g. USA or USA/D0):")
        )
        if filter_result is None:
            return
        filter_region, filter_delay = filter_result

        # 从 alphas_db.json 筛选目标 alpha
        try:
            with open(_LC_ALPHAS_DB_PATH, 'r', encoding='utf-8') as f:
                db = json.load(f)
        except Exception as e:
            self.statusBar().showMessage(f"{T('Failed to read alphas_db.json: ')}{e}", 3000)
            return

        targets = []  # [(alpha_id, old_pts), ...]
        for alpha in (db.values() if isinstance(db, dict) else db):
            pts = alpha.get('osmosisPoints', 0)
            if not pts or pts <= 0:
                continue
            settings = alpha.get('settings', {})
            region = settings.get('region', 'UNKNOWN')
            delay = settings.get('delay', 'UNKNOWN')
            if filter_region is not None:
                if region != filter_region:
                    continue
                if filter_delay is not None and delay != filter_delay:
                    continue
            targets.append((alpha['id'], pts))

        if not targets:
            scope = f"{filter_region}/D{filter_delay}" if filter_region and filter_delay is not None else (filter_region if filter_region else "all")
            self.statusBar().showMessage(f"{T('No osmosis alphas found for ')}{scope}.", 3000)
            return

        scope = f"{filter_region}/D{filter_delay}" if filter_region and filter_delay is not None else (filter_region if filter_region else "ALL regions")
        confirm = QMessageBox.question(
            self, "Confirm Empty Osmosis",
            f"{T('Clear osmosisPoints for ')}{len(targets)}{T(' alphas ')}({scope})?{T('\nThis cannot be undone.')}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        # 异步清空
        class EmptyOsmosisWorker(QThread):
            progress = _sig(int, int, str)   # current, total, alpha_id
            finished = _sig(int, int, list)  # success_count, fail_count, [aid, ...]

            def __init__(self, client, targets_list):
                super().__init__()
                self.client = client
                self.targets = targets_list

            def run(self):
                success = 0
                fail = 0
                ok_ids = []
                for i, (aid, old_pts) in enumerate(self.targets):
                    self.progress.emit(i + 1, len(self.targets), aid)
                    try:
                        self.client.ensure_auth()
                        resp = self.client.session.patch(
                            f"{self.client.BASE_URL}alphas/{aid}",
                            json={"osmosisPoints": None}
                        )
                        if resp.status_code == 401:
                            self.client.authenticate(self.client.email, self.client.password)
                            resp = self.client.session.patch(
                                f"{self.client.BASE_URL}alphas/{aid}",
                                json={"osmosisPoints": None}
                            )
                        if resp.status_code in (200, 204):
                            success += 1
                            ok_ids.append(aid)
                        else:
                            fail += 1
                            print(f"Failed to clear {aid}: HTTP {resp.status_code}", flush=True)
                    except Exception as e:
                        fail += 1
                        print(f"Error clearing {aid}: {e}", flush=True)
                self.finished.emit(success, fail, ok_ids)

        self._empty_osmosis_worker = EmptyOsmosisWorker(self.client, targets)
        self._empty_osmosis_worker.progress.connect(
            lambda cur, total, aid: self.statusBar().showMessage(f"{T('Emptying osmosis ')}{cur}/{total}: {aid}")
        )
        self._empty_osmosis_worker.finished.connect(self._on_empty_osmosis_done)
        self._empty_osmosis_worker.start()
        self.statusBar().showMessage(f"{T('Emptying osmosis ')}{T(' for ')}{len(targets)}{T(' alphas')}...")

    def _on_empty_osmosis_done(self, success, fail, ok_ids):
        msg = f"{T('Osmosis cleared: ')}{success}{T(' success, ')}{fail}{T(' failed')}"
        print(msg, flush=True)
        self.statusBar().showMessage(msg, 5000)
        if fail == 0:
            winsound.Beep(500, 500)
        else:
            winsound.Beep(300, 400)
        # Update alphas_db.json: clear osmosisPoints
        if ok_ids:
            db = _lc_load_local_alphas_db()
            changed = 0
            for aid in ok_ids:
                alpha = db.get(aid)
                if alpha and alpha.get('osmosisPoints'):
                    alpha['osmosisPoints'] = None
                    changed += 1
            if changed:
                _lc_save_local_alphas_db(db)

    def _restore_osmosis(self):
        """从 osmosis_archive_*.csv 恢复 osmosisPoints，可指定 region/delay 过滤。"""
        result = self._show_restore_dialog(
            "Restore Osmosis", "osmosis", "alphas",
            T("No osmosis_archive_*.csv found. Run Archive Osmosis first.")
        )
        if result is None:
            return
        csv_path, filter_region, filter_delay, rows = result

        targets = []
        for row in rows:
            aid = row.get('alpha_id', '').strip()
            pts_str = row.get('osmosisPoints', '0').strip()
            if aid and pts_str:
                try:
                    pts = int(float(pts_str))
                except ValueError:
                    continue
                targets.append((aid, pts))

        if not targets:
            scope = f"{filter_region}/D{filter_delay}" if filter_region and filter_delay is not None else (filter_region if filter_region else "all")
            self.statusBar().showMessage(f"{T('No osmosis records found for ')}{scope}{T(' in ')}{os.path.basename(csv_path)}.", 3000)
            return

        scope = f"{filter_region}/D{filter_delay}" if filter_region and filter_delay is not None else (filter_region if filter_region else "ALL regions")
        confirm = QMessageBox.question(
            self, "Confirm Restore Osmosis",
            f"{T('Restore osmosisPoints for ')}{len(targets)}{T(' alphas ')}({scope}){T(' from ')}{os.path.basename(csv_path)}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        # 异步恢复
        class RestoreOsmosisWorker(QThread):
            progress = _sig(int, int, str)   # current, total, alpha_id
            finished = _sig(int, int, list)  # success_count, fail_count, [(aid, pts), ...]

            def __init__(self, client, targets_list):
                super().__init__()
                self.client = client
                self.targets = targets_list

            def run(self):
                success = 0
                fail = 0
                ok_targets = []
                for i, (aid, pts) in enumerate(self.targets):
                    self.progress.emit(i + 1, len(self.targets), aid)
                    try:
                        self.client.ensure_auth()
                        resp = self.client.session.patch(
                            f"{self.client.BASE_URL}alphas/{aid}",
                            json={"osmosisPoints": pts}
                        )
                        if resp.status_code == 401:
                            self.client.authenticate(self.client.email, self.client.password)
                            resp = self.client.session.patch(
                                f"{self.client.BASE_URL}alphas/{aid}",
                                json={"osmosisPoints": pts}
                            )
                        if resp.status_code in (200, 204):
                            success += 1
                            ok_targets.append((aid, pts))
                        else:
                            fail += 1
                            print(f"Failed to restore {aid}: HTTP {resp.status_code}", flush=True)
                    except Exception as e:
                        fail += 1
                        print(f"Error restoring {aid}: {e}", flush=True)
                self.finished.emit(success, fail, ok_targets)

        self._restore_osmosis_worker = RestoreOsmosisWorker(self.client, targets)
        self._restore_osmosis_worker.progress.connect(
            lambda cur, total, aid: self.statusBar().showMessage(f"{T('Restoring osmosis ')}{cur}/{total}: {aid}")
        )
        self._restore_osmosis_worker.finished.connect(self._on_restore_osmosis_done)
        self._restore_osmosis_worker.start()
        self.statusBar().showMessage(f"{T('Restoring osmosis ')}{T(' for ')}{len(targets)}{T(' alphas')}...")

    def _on_restore_osmosis_done(self, success, fail, ok_targets):
        msg = f"{T('Osmosis restored: ')}{success}{T(' success, ')}{fail}{T(' failed')}"
        print(msg, flush=True)
        self.statusBar().showMessage(msg, 5000)
        if fail == 0:
            winsound.Beep(500, 500)
        else:
            winsound.Beep(300, 400)
        # Update alphas_db.json: restore osmosisPoints
        if ok_targets:
            db = _lc_load_local_alphas_db()
            changed = 0
            for aid, pts in ok_targets:
                alpha = db.get(aid)
                if alpha:
                    alpha['osmosisPoints'] = pts
                    changed += 1
            if changed:
                _lc_save_local_alphas_db(db)

    def _copy_all_alpha_ids(self):
        """Copy all alpha IDs from view-mode tabs, deduplicated, formatted as 'aid1',\\n'aid2',..."""
        aids = set()
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab):
                if tab._state in (SimulateTab.STATE_DONE_VIEWED, SimulateTab.STATE_DONE_UNVIEWED):
                    aid = getattr(tab, '_alpha_id', None)
                    if aid:
                        aids.add(aid)
        if not aids:
            self.statusBar().showMessage(T("No alpha IDs in view mode"), 2000)
            return
        sorted_aids = sorted(aids)
        formatted = ",\n".join(f"'{aid}'" for aid in sorted_aids) + ","
        QApplication.clipboard().setText(formatted)
        self._copy_aids_msg = QMessageBox(QMessageBox.Information, "Copy All AIDs", f"{T('Copied ')}{len(aids)}{T(' alpha ID(s)')}")
        self._copy_aids_msg.setWindowFlags(self._copy_aids_msg.windowFlags() | Qt.WindowStaysOnTopHint)
        QTimer.singleShot(3000, self._copy_aids_msg.close)
        self._copy_aids_msg.show()

    def _fetch_alpha_by_id(self, aid_list=None):
        if isinstance(aid_list, list):
            alpha_ids = [aid.strip() for aid in aid_list if aid.strip()]
        else:
            raw = self.alpha_id_input.text().strip()
            if not raw:
                # Try clipboard
                clipboard = QApplication.clipboard()
                raw = clipboard.text().strip()
                if not raw:
                    QMessageBox.warning(self, T("Input Error"), T("Please enter an Alpha ID."))
                    return
            alpha_ids = [aid.strip() for aid in raw.replace(',', ' ').split() if aid.strip()]
        if not alpha_ids:
            QMessageBox.warning(self, T("Input Error"), T("Please enter an Alpha ID."))
            return

        # Create all tabs first, then fetch each in its own QThread
        tabs = []
        for i, aid in enumerate(alpha_ids):
            self._add_tab()
            new_tab = self.tab_widget.widget(self.tab_widget.count() - 1)
            if isinstance(new_tab, SimulateTab):
                tabs.append((new_tab, aid))
        # Launch all fetch workers concurrently
        for tab, aid in tabs:
            tab._fetch_single_alpha_async(aid)

    def _move_tab(self, direction: int):
        idx = self.tab_widget.currentIndex()
        new_idx = idx + direction
        if 0 <= new_idx < self.tab_widget.count():
            tab = self.tab_widget.widget(idx)
            title = self.tab_widget.tabText(idx)
            self.tab_widget.removeTab(idx)
            self.tab_widget.insertTab(new_idx, tab, title)
            self.tab_widget.setCurrentIndex(new_idx)

    def _add_tab(self, source_state=None, batch_id=None, batch_expr=None):
        self.tab_counter += 1
        idx = self.tab_counter
        tab = SimulateTab(self.client)
        # Sync fill_count_spin to current max weight
        tab.fill_count_spin.setValue(self._max_running_weight_spin.value())
        if batch_id is not None:
            tab._batch_id = batch_id
            tab._batch_expr = batch_expr
            tab.set_tab_base_name(f"S{batch_id}")
        else:
            tab.set_tab_base_name(f"{T('Simulate ')}{idx}")
        tab.clone_requested.connect(lambda: self._add_tab(tab.get_state()))
        tab.tab_title_update.connect(self._update_tab_title_for(tab))
        tab.running_state_changed.connect(self._update_running_count)
        tab.sim_finished.connect(self._on_tab_sim_finished)
        if source_state:
            tab.set_state(source_state)
        tab_idx = self.tab_widget.addTab(tab, tab._tab_base_name)
        self._update_tab_style(tab_idx, tab)
        self._update_tab_count()
        self.tab_widget.setCurrentWidget(tab)
        tab.expr_input.setFocus()
        cursor = tab.expr_input.textCursor()
        cursor.movePosition(QTextCursor.End)
        tab.expr_input.setTextCursor(cursor)

    def _update_tab_count(self):
        self._tab_count_label.setText(f"{T('Tabs: ')}{self.tab_widget.count()}")

    def _update_tab_title_for(self, tab):
        def update(title):
            idx = self.tab_widget.indexOf(tab)
            if idx >= 0:
                self.tab_widget.setTabText(idx, title)
                self._update_tab_style(idx, tab)
        return update

    def _on_tab_changed(self, index: int):
        # Hide completion popups on all tabs when switching
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab):
                tab._completion_popup.hide()
        tab = self.tab_widget.widget(index)
        if tab and isinstance(tab, SimulateTab):
            tab.mark_viewed()
            self._update_tab_style(index, tab)
        self._update_unviewed_count()

    def _show_tab_list(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #313244; color: #cdd6f4; border: 1px solid #585b70;
                padding: 4px; font-size: 10pt;
            }
            QMenu::item {
                padding: 6px 24px; border-radius: 3px;
            }
            QMenu::item:selected {
                background: #45475a; color: #89b4fa;
            }
        """)
        state_icons = {
            SimulateTab.STATE_IDLE: "",
            SimulateTab.STATE_RUNNING: "⏳",
            SimulateTab.STATE_QUEUED: "⏳Q",
            SimulateTab.STATE_DONE_VIEWED: "✅",
            SimulateTab.STATE_DONE_UNVIEWED: "🟢",
        }
        current = self.tab_widget.currentIndex()
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab):
                icon = state_icons.get(tab._state, "")
                title = tab._tab_base_name
                alpha_id = getattr(tab, '_alpha_id', None)
                if alpha_id:
                    title = f"{title} ({alpha_id})"

                # Add progress for running tabs
                if tab._state == SimulateTab.STATE_RUNNING:
                    progress = getattr(tab, '_current_progress_pct', 0)
                    elapsed = getattr(tab, '_current_elapsed', 0)
                    title = f"{icon} {title} [{progress:.0f}% | {elapsed:.0f}s]"
                elif icon:
                    title = f"{icon} {title}"

                action = menu.addAction(title)
                action.setCheckable(True)
                action.setChecked(i == current)
                action.triggered.connect(lambda checked, idx=i: self.tab_widget.setCurrentIndex(idx))
        menu.exec_(self._tab_list_btn.mapToGlobal(self._tab_list_btn.rect().bottomLeft()))

    def _move_running_tabs_right(self):
        """Move all running tabs to the rightmost positions, preserving relative order."""
        running = []
        not_running = []
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            title = self.tab_widget.tabText(i)
            is_running = isinstance(tab, SimulateTab) and tab._state in (SimulateTab.STATE_RUNNING, SimulateTab.STATE_QUEUED)
            if is_running:
                running.append((tab, title))
            else:
                not_running.append((tab, title))
        new_order = not_running + running
        # Remove all tabs first, then re-insert in new order to avoid index drift
        widgets = []
        for i in range(self.tab_widget.count()):
            widgets.append(self.tab_widget.widget(0))
            self.tab_widget.removeTab(0)
        for i, (tab, title) in enumerate(new_order):
            self.tab_widget.insertTab(i, tab, title)
        # Restore tab text colors (removeTab/insertTab resets them)
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab):
                self._update_tab_style(i, tab)

    def _close_idle_tabs(self):
        """Close all IDLE state tabs."""
        idle_tabs = []
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab) and tab._state == SimulateTab.STATE_IDLE:
                idle_tabs.append(tab)

        if not idle_tabs:
            self.statusBar().showMessage(T("No IDLE tabs to close"), 2000)
            return

        # Confirm before closing
        reply = QMessageBox.question(
            self, "Close IDLE Tabs",
            f"{T('Close ')}{len(idle_tabs)}{T(' IDLE tab(s)?')}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            for tab in idle_tabs:
                idx = self.tab_widget.indexOf(tab)
                if idx >= 0:
                    self.tab_widget.removeTab(idx)
            self._update_tab_count()
            # Ensure at least one tab remains
            if self.tab_widget.count() == 0:
                self._add_tab()
            self.statusBar().showMessage(f"{T('Closed ')}{len(idle_tabs)}{T(' IDLE tab(s)')}", 3000)

    _BATCH_COLORS = [
        "#89b4fa",  # blue
        "#f9e2af",  # yellow
        "#fab387",  # peach
        "#cba6f7",  # mauve
        "#94e2d5",  # teal
        "#f38ba8",  # red
        "#a6e3a1",  # green
        "#74c7ec",  # sapphire
    ]

    def _update_tab_style(self, index: int, tab: SimulateTab):
        bar = self.tab_widget.tabBar()
        # If this tab has any FAIL checks, force red text regardless of state
        fail_text = tab.checks_fail_list.toPlainText() if hasattr(tab, 'checks_fail_list') else ""
        if fail_text:
            bar.setTabTextColor(index, QColor("#f38ba8"))
            return
        # Tune batch coloring — only when not IDLE (cancelled tune tabs look like normal idle)
        tune_batch = getattr(tab, '_tune_batch_id', None)
        if tab._state == SimulateTab.STATE_RUNNING and tab._batch_id is not None:
            color = self._BATCH_COLORS[(tab._batch_id - 1) % len(self._BATCH_COLORS)]
            bar.setTabTextColor(index, QColor(color))
        elif tune_batch is not None and tab._state != SimulateTab.STATE_IDLE:
            color = self._BATCH_COLORS[(tune_batch - 1) % len(self._BATCH_COLORS)]
            bar.setTabTextColor(index, QColor(color))
        elif tab._state in (SimulateTab.STATE_DONE_UNVIEWED, SimulateTab.STATE_DONE_VIEWED):
            bar.setTabTextColor(index, QColor("#a6e3a1"))
        elif tab._state == SimulateTab.STATE_QUEUED:
            bar.setTabTextColor(index, QColor("#f9e2af"))
        elif tab._state == SimulateTab.STATE_RUNNING:
            bar.setTabTextColor(index, QColor("#89b4fa"))
        else:
            bar.setTabTextColor(index, QColor("#6c7086"))

    def _on_tab_close(self, index: int):
        tab = self.tab_widget.widget(index)
        if tab and hasattr(tab, "is_running") and tab.is_running():
            QMessageBox.warning(self, T("Tab Busy"), T("Cannot close a tab while simulation is running."))
            return
        self.tab_widget.removeTab(index)
        self._update_tab_count()
        # Always keep at least one tab
        if self.tab_widget.count() == 0:
            self._add_tab()

    def _close_tab_widget(self, tab):
        idx = self.tab_widget.indexOf(tab)
        if idx >= 0:
            self.tab_widget.removeTab(idx)
        self._update_tab_count()
        if self.tab_widget.count() == 0:
            self._add_tab()

    # ── Login ──
    def _update_regions_from_pyramid(self):
        """从 pyramid-alphas API 获取可用 regions，更新所有 tab 的 region_combo。"""
        try:
            resp = self.client.session.get(
                f"{self.client.BASE_URL}users/self/activities/pyramid-alphas"
            )
            if resp.status_code != 200:
                return
            data = resp.json()
            pyramids = data.get('pyramids', [])
            if not pyramids:
                return
            available_regions = sorted(set(p.get('region', '') for p in pyramids if p.get('region')))
            if not available_regions:
                return
            # 更新所有 tab 的 region_combo
            for i in range(self.tab_widget.count()):
                tab = self.tab_widget.widget(i)
                if isinstance(tab, SimulateTab):
                    combo = tab.region_combo
                    current = combo.currentText()
                    combo.blockSignals(True)
                    combo.clear()
                    combo.addItems(available_regions)
                    if current in available_regions:
                        combo.setCurrentText(current)
                    combo.blockSignals(False)
                    tab._on_region_changed(combo.currentText())
            print(f"Region combo updated from pyramid-alphas: {available_regions}", flush=True)
        except Exception as e:
            print(f"Failed to update regions from pyramid-alphas: {e}", flush=True)

    def _toggle_use_local_corr(self, enabled: bool):
        """Toggle local-correlation mode live: update global, persist, refresh UI."""
        global use_local_corr
        if use_local_corr == bool(enabled):
            return
        use_local_corr = bool(enabled)
        # Persist to common_config.py so it survives restarts
        _save_config_value('use_local_corr', use_local_corr)
        # Keep the menu action check in sync
        self._title_bar.act_local_corr.setChecked(use_local_corr)
        # Strict Platform Parity is only meaningful when local corr is on
        self._title_bar.act_strict_parity.setEnabled(use_local_corr)
        if not use_local_corr:
            # Disable parity when local corr is off
            global strict_platform_parity
            if strict_platform_parity:
                strict_platform_parity = False
                _save_config_value('strict_platform_parity', False)
                self._title_bar.act_strict_parity.setChecked(False)
        # Refresh Self Corr / PPC button labels in every tab
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab):
                tab._refresh_corr_button_labels()

    def _toggle_strict_platform_parity(self, enabled: bool):
        """Toggle strict-platform-parity mode for local correlation pools."""
        global strict_platform_parity
        if strict_platform_parity == bool(enabled):
            return
        strict_platform_parity = bool(enabled)
        _save_config_value('strict_platform_parity', strict_platform_parity)
        self._title_bar.act_strict_parity.setChecked(strict_platform_parity)

    def _show_shortcuts(self):
        """Show a dialog listing all keyboard shortcuts."""
        shortcuts = [
            ("Ctrl+Tab", T("Next tab")),
            ("Ctrl+Shift+Tab", T("Previous tab")),
            ("Ctrl+PgDown", T("Next tab")),
            ("Ctrl+PgUp", T("Previous tab")),
            ("Ctrl+Right", T("Jump to next unviewed tab")),
            ("Ctrl+Left", T("Jump to previous unviewed tab")),
            ("Ctrl++ / Ctrl+=", T("Clone current tab")),
            ("Ctrl+W", T("Close current tab")),
            ("Ctrl+Shift+PgUp", T("Move current tab left")),
            ("Ctrl+Shift+PgDown", T("Move current tab right")),
            ("Ctrl+Shift+S", T("Simulate current tab")),
            ("Ctrl+Shift+F", T("Fill current tab")),
            ("Ctrl+Shift+C", T("Cancel current tab")),
            ("Ctrl+Shift+T", T("Tune current tab")),
            ("Esc", T("Close dialog / popup")),
        ]
        dlg = QDialog(self)
        dlg.setWindowTitle(T("Shortcuts"))
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet("QDialog { background-color: #1e1e2e; }")
        layout = QVBoxLayout(dlg)
        layout.setSpacing(0)
        layout.setContentsMargins(12, 12, 12, 12)
        for key, desc in shortcuts:
            row = QHBoxLayout()
            row.setSpacing(16)
            key_label = QLabel(key)
            key_label.setStyleSheet(
                "color: #cba6f7; font-size: 18pt; font-family: Consolas, monospace; font-weight: bold;"
            )
            key_label.setFixedWidth(240)
            desc_label = QLabel(desc)
            desc_label.setStyleSheet("color: #cdd6f4; font-size: 18pt;")
            row.addWidget(key_label)
            row.addWidget(desc_label)
            row.addStretch()
            layout.addLayout(row)
        layout.addSpacing(8)
        close_btn = QPushButton(T("OK"))
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #cba6f7; color: #1e1e2e; font-size: 11pt;
                font-weight: bold; border-radius: 4px; padding: 6px 24px; border: none;
            }
            QPushButton:hover { background-color: #b4befe; }
        """)
        close_btn.clicked.connect(dlg.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        dlg.exec_()

    def _switch_language(self, lang):
        """Switch the global language and refresh all visible UI text."""
        global SYSTEM_LANGUAGE
        if SYSTEM_LANGUAGE == lang:
            return
        SYSTEM_LANGUAGE = lang
        _save_config_value('SYSTEM_LANGUAGE', SYSTEM_LANGUAGE)
        # Update check marks in the Language submenu (in title bar)
        self._title_bar.update_language_checks(lang)

        # Refresh main window texts
        self.setWindowTitle(T("BRAIN Alpha Simulater"))
        self._title_bar.title_label.setText(T("BRAIN Alpha Simulater"))
        self._title_bar.settings_btn.setToolTip(T("Settings"))
        self._title_bar.help_btn.setToolTip(T("Help"))
        self._title_bar.min_btn.setToolTip(T("Minimize"))
        self._title_bar.close_btn.setToolTip(T("Quit"))
        if self.isMaximized():
            self._title_bar.max_btn.setToolTip(T("Restore"))
        else:
            self._title_bar.max_btn.setToolTip(T("Maximize"))
        self.login_group.setTitle(T("Authentication"))

        # Refresh user_id_bar labels — preserve numeric values, update prefix text
        uid_text = self.user_id_label.text()
        if ': -' in uid_text or '： -' in uid_text or ': ' in uid_text or '：' in uid_text:
            # Extract the value part after the colon
            for sep in (': ', '：'):
                if sep in uid_text:
                    val = uid_text.split(sep, 1)[1]
                    self.user_id_label.setText(f"{T('User ID: ')}{val}")
                    break

        for label, prefix_key in [
            (self._today_sim_label, "Today Simulated: "),
            (self._sim_speed_label, "Speed: "),
            (self._signals_label, "Signals: "),
            (self._pyramids_label, "Pyramids: "),
        ]:
            text = label.text()
            # Try to extract value after known prefixes (both languages)
            found = False
            for sep in (': ', '：'):
                if sep in text:
                    val = text.split(sep, 1)[1]
                    label.setText(f"{T(prefix_key)}{val}")
                    found = True
                    break
            if not found:
                label.setText(f"{T(prefix_key)}-")

        self.fetch_alpha_btn.setText(T("Fetch Alpha"))
        self._funcs_btn.setText(T("Funcs"))

        # Refresh login area
        self.login_btn.setText(T("Login"))
        login_text = self.login_status.text()
        if 'Not logged' in login_text or '未登录' in login_text:
            self.login_status.setText(T("Not logged in"))
        elif 'Logging' in login_text or '登录中' in login_text:
            self.login_status.setText(T("Logging in..."))
        elif 'Logged in' in login_text or '已登录' in login_text or 'Authenticated' in login_text or '认证成功' in login_text:
            self.login_status.setText(T("Authenticated successfully"))
        elif 'failed' in login_text.lower() or '失败' in login_text:
            self.login_status.setText(T("Login failed"))

        # Refresh all SimulateTab tabs
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SimulateTab):
                tab._refresh_language()

        # Refresh status bar
        self._tab_count_label.setText(f"{T('Tabs: ')}{self.tab_widget.count()}")
        self._check_and_correct_slots()
        # Reset unviewed/queued labels before calling _update_unviewed_count
        # to avoid parsing issues with Chinese colon
        self._unviewed_label.setText(f"{T('Unviewed: ')}0")
        self._queued_label.setText(f"{T('Queued: ')}0")
        self._update_unviewed_count()
        self._slot_check_countdown_label.setText(f"{T('Check: ')}{self._slot_check_countdown}s")
        self._cancel_all_btn.setText(T("Cancel All"))

    def _on_login(self):
        email = self.email_input.text().strip()
        password = self.password_input.text().strip()
        if not email or not password:
            QMessageBox.warning(self, T("Input Error"), T("Please enter email and password."))
            return

        self.login_btn.setEnabled(False)
        self.login_status.setText(T("Logging in..."))
        self.login_status.setStyleSheet("color: #fab387;")

        try:
            result = self.client.authenticate(email, password)
            if result["status"] == "ok":
                # Save credentials to file for future auto-login
                self._save_credentials(email, password)
                self.login_status.setText(f"{T('Logged in ')}({email})")
                self.login_status.setStyleSheet("color: #4CAF50; font-weight: bold;")
                self.statusBar().showMessage(T("Authenticated successfully"))
                # Fetch user ID and hide login bar
                user_id = self.client.get_user_id()
                if user_id:
                    self.user_id_label.setText(f"{T('User ID: ')}{user_id}")
                else:
                    self.user_id_label.setText(T("User ID: -"))
                self.login_group.setVisible(False)
                self.user_id_bar.setVisible(True)
                # Auto-fetch today sim count, signals, and pyramids on login
                QTimer.singleShot(100, self._fetch_today_sim_count)
                QTimer.singleShot(200, self._fetch_signals_count)
                QTimer.singleShot(300, self._fetch_pyramids_count)
                # 从 pyramid-alphas API 获取可用 regions 并更新所有 tab 的 region_combo
                self._update_regions_from_pyramid()
                # 异步下载 operators.json（如果不存在）
                if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'operators.json')):
                    self._ops_download_worker = _OperatorsDownloadWorker(self.client)
                    self._ops_download_worker.finished.connect(_refresh_operators)
                    self._ops_download_worker.start()
                # 异步下载 alphas_db.json（如果不存在）
                if not os.path.exists(_LC_ALPHAS_DB_PATH):
                    self._alphas_db_download_worker = DownloadSubmittedAlphasWorker(self.client)
                    self._alphas_db_download_worker.progress.connect(lambda msg: print(msg, flush=True))
                    self._alphas_db_download_worker.finished.connect(lambda t, d, f: print(f"Auto-download alphas done: {t} total, {d} new, {f} failed", flush=True))
                    self._alphas_db_download_worker.start()
                if fetch_aid_arr is not None and len(fetch_aid_arr) > 0:
                    self._fetch_alpha_by_id(fetch_aid_arr)
                    # Close the initial empty first tab
                    if self.tab_widget.count() > 1:
                        first_tab = self.tab_widget.widget(0)
                        self.tab_widget.removeTab(0)
                        if first_tab:
                            first_tab.deleteLater()
            elif result["status"] == "biometric":
                QMessageBox.information(self, T("Biometric Auth"),
                    T("Biometric authentication required.\nPlease complete verification in the browser window,\nthen click Login again."))
                self.login_status.setText(T("Biometric required"))
                self.login_status.setStyleSheet("color: #fab387;")
            else:
                QMessageBox.critical(self, T("Login Failed"), result["message"])
                self.login_status.setText(T("Login failed"))
                self.login_status.setStyleSheet("color: #F44336;")
        except Exception as e:
            QMessageBox.critical(self, T("Error"), str(e))
            self.login_status.setText(T("Error"))
            self.login_status.setStyleSheet("color: #F44336;")
        finally:
            self.login_btn.setEnabled(True)
            tab = self.tab_widget.currentWidget()
            if tab and isinstance(tab, SimulateTab):
                tab.expr_input.setFocus()
                cursor = tab.expr_input.textCursor()
                cursor.movePosition(QTextCursor.End)
                tab.expr_input.setTextCursor(cursor)



# ──────────────────────────────────────────────
#  Auto-setup write_desc skill for Claude
# ──────────────────────────────────────────────
def _setup_write_desc_skill():
    """If Claude CLI is installed and write_desc skill not present, copy it."""
    if not shutil.which('claude'):
        return
    # Locate .claude/skills dir relative to user home
    claude_dir = os.path.join(os.path.expanduser('~'), '.claude', 'skills')
    target_dir = os.path.join(claude_dir, 'write_desc')
    target_file = os.path.join(target_dir, 'SKILL.md')
    if os.path.exists(target_file):
        return
    # Source: <script_dir>/write_desc/SKILL.md
    source_file = os.path.join(_LC_SCRIPT_DIR, 'write_desc', 'SKILL.md')
    if not os.path.exists(source_file):
        return
    try:
        os.makedirs(target_dir, exist_ok=True)
        shutil.copy2(source_file, target_file)
        print(f"Copied write_desc skill to {target_file}", flush=True)
    except Exception as e:
        print(f"Warning: failed to setup write_desc skill: {e}", flush=True)


# ──────────────────────────────────────────────
#  Entry Point
# ──────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)

    app.setStyleSheet("""
        QMainWindow { background: #1e1e2e; }
        QWidget { background: #1e1e2e; color: #cdd6f4; font-size: 13pt; }
        QGroupBox {
            font-weight: bold; font-size: 14pt; color: #89b4fa;
            border: 1px solid #45475a; border-radius: 8px;
            margin-top: 12px; padding-top: 18px;
            background: #313244;
        }
        QGroupBox::title {
            subcontrol-origin: margin; left: 12px; padding: 0 6px;
            color: #89b4fa;
        }
        QLabel { color: #cdd6f4; background: transparent; }
        QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            background: #181825; color: #cdd6f4; border: 1px solid #45475a;
            border-radius: 6px; padding: 4px 8px; selection-background-color: #89b4fa;
        }
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
            border: 1px solid #89b4fa;
        }
        QComboBox::drop-down {
            border: none; width: 24px;
        }
        QComboBox QAbstractItemView {
            background: #313244; color: #cdd6f4; selection-background-color: #89b4fa;
            border: 1px solid #45475a;
        }
        QTableWidget {
            background: #181825; color: #cdd6f4; gridline-color: #45475a;
            font-size: 12pt; border: 1px solid #45475a; border-radius: 6px;
            alternate-background-color: #313244;
        }
        QTableWidget::item:selected { background: #89b4fa; color: #1e1e2e; }
        QHeaderView::section {
            background: #313244; color: #89b4fa; border: 1px solid #45475a;
            padding: 4px; font-weight: bold;
        }
        QProgressBar {
            border: 1px solid #45475a; border-radius: 6px; text-align: center;
            height: 22px; background: #181825; color: #cdd6f4;
        }
        QProgressBar::chunk { background: #89b4fa; border-radius: 5px; }
        QPushButton {
            background: #313244; color: #cdd6f4; border: 1px solid #45475a;
            border-radius: 6px; padding: 6px 16px; font-weight: bold;
        }
        QPushButton:hover { background: #45475a; border: 1px solid #89b4fa; }
        QPushButton:pressed { background: #585b70; }
        QPushButton:disabled { background: #181825; color: #585b70; border-color: #313244; }
        QTabWidget::pane { border: 1px solid #45475a; background: #1e1e2e; border-radius: 4px; }
        QSplitter::handle { background: #45475a; width: 3px; }
        QStatusBar { background: #181825; color: #a6adc8; border-top: 1px solid #45475a; }
        QMessageBox { background: #313244; }
        QMessageBox QLabel { color: #cdd6f4; }
        QMessageBox QPushButton { min-width: 80px; }
        QScrollBar:vertical {
            background: #181825; width: 10px; border: none;
        }
        QScrollBar::handle:vertical {
            background: #45475a; border-radius: 5px; min-height: 20px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar:horizontal {
            background: #181825; height: 10px; border: none;
        }
        QScrollBar::handle:horizontal {
            background: #45475a; border-radius: 5px; min-width: 20px;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    """)

    window = MainWindow()
    window.showMaximized()
    # Async setup write_desc skill for Claude (non-blocking)
    QTimer.singleShot(500, _setup_write_desc_skill)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
