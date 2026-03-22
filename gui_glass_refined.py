# -*- coding: utf-8 -*-

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import ctypes
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from config import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    DEFAULT_AUDIO_ENHANCE_LEVEL,
    DEFAULT_LANGUAGE,
    DEFAULT_OUTPUT_FORMATS,
    DEFAULT_PREFERRED_DEVICE,
    DEFAULT_PRESET_ID,
    LANGUAGE_NATIVE_NAMES,
    LANGUAGE_OPTIONS,
    TRANSCRIPTION_PRESETS,
    get_language_korean_name,
    get_transcription_preset,
)
from env_manager import (
    choose_runtime_device_and_type,
    collect_live_resource_status,
    collect_startup_status,
    download_model_to_cache,
    inspect_model_availability,
)
from model_catalog import MODEL_CATALOG, default_model_id
from paths import bundled_icon_path, clear_temp_work_dir
from settings_manager import load_settings, save_settings
from subtitle_engine import probe_media_duration_seconds, run_transcription_job


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
LWA_ALPHA = 0x00000002


class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


COLOR = {
    "shell": QColor(0, 0, 0, 0),
    "shell_edge": QColor(255, 255, 255, 26),
    "panel_top": QColor(255, 255, 255, 154),
    "panel_mid": QColor(255, 255, 255, 118),
    "panel_bottom": QColor(255, 255, 255, 88),
    "panel_gloss": QColor(255, 255, 255, 78),
    "panel_edge": QColor(255, 255, 255, 152),
    "panel_edge_inner": QColor(255, 255, 255, 186),
    "panel_shadow": QColor(28, 18, 30, 30),
    "plain_fill": QColor(255, 255, 255, 116),
    "plain_fill_bottom": QColor(255, 255, 255, 92),
    "plain_edge": QColor(255, 255, 255, 128),
    "text": QColor(46, 38, 49),
    "muted": QColor(96, 86, 102),
    "soft": QColor(124, 113, 130),
    "accent": QColor(244, 153, 124),
    "accent_2": QColor(233, 133, 173),
    "accent_3": QColor(241, 190, 140),
    "success": QColor(88, 176, 129),
    "warning": QColor(214, 155, 82),
    "danger": QColor(216, 112, 112),
    "info": QColor(112, 159, 219),
    "neutral": QColor(168, 157, 172),
    "track": QColor(255, 255, 255, 58),
    "fill": QColor(244, 153, 124),
}


def compact_path_for_display(path: str, keep_tail: int = 3) -> str:
    if not path:
        return ""
    p = Path(path)
    parts = list(p.parts)
    if len(parts) <= keep_tail + 1:
        return str(p)
    return str(Path("…", *parts[-keep_tail:]))


def format_elapsed_text(seconds: float | int | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def open_path(path: str) -> None:
    if not path:
        return
    if os.name == "nt":
        os.startfile(path)
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
        return
    subprocess.Popen(["xdg-open", path])


def disable_windows_backdrop(widget: QWidget) -> None:
    if os.name != "nt":
        return
    try:
        hwnd = int(widget.winId())
    except Exception:
        return

    try:
        margins = MARGINS(0, 0, 0, 0)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    except Exception:
        pass

    try:
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def enable_true_layered_window(widget: QWidget) -> None:
    if os.name != "nt":
        return
    try:
        hwnd = int(widget.winId())
    except Exception:
        return

    disable_windows_backdrop(widget)

    try:
        user32 = ctypes.windll.user32
        get_window_long = getattr(user32, "GetWindowLongW")
        set_window_long = getattr(user32, "SetWindowLongW")
        ex_style = get_window_long(hwnd, GWL_EXSTYLE)
        ex_style |= WS_EX_LAYERED | WS_EX_APPWINDOW
        ex_style &= ~WS_EX_NOREDIRECTIONBITMAP
        set_window_long(hwnd, GWL_EXSTYLE, ex_style)
    except Exception:
        pass



class FrostedPanel(QFrame):
    def __init__(self, radius: int = 24, parent: QWidget | None = None):
        super().__init__(parent)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 14)
        shadow.setColor(COLOR["panel_shadow"])
        self.setGraphicsEffect(shadow)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(1, 1, -1, -1)
            if rect.width() <= 2 or rect.height() <= 2:
                return

            path = QPainterPath()
            path.addRoundedRect(rect, self._radius, self._radius)

            base = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            base.setColorAt(0.0, COLOR["panel_top"])
            base.setColorAt(0.48, COLOR["panel_mid"])
            base.setColorAt(1.0, COLOR["panel_bottom"])
            painter.fillPath(path, base)

            blush = QRadialGradient(rect.center().x(), rect.top() + rect.height() * 0.18, rect.width() * 0.78)
            blush.setColorAt(0.0, QColor(255, 246, 251, 34))
            blush.setColorAt(0.55, QColor(255, 223, 233, 18))
            blush.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.save()
            painter.setClipPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(blush)
            painter.drawRoundedRect(rect, self._radius, self._radius)

            sheen_rect = rect.adjusted(2, 2, -2, -rect.height() // 2)
            sheen_path = QPainterPath()
            sheen_path.addRoundedRect(sheen_rect, max(12, self._radius - 6), max(12, self._radius - 6))
            sheen = QLinearGradient(sheen_rect.left(), sheen_rect.top(), sheen_rect.left(), sheen_rect.bottom())
            sheen.setColorAt(0.0, COLOR["panel_gloss"])
            sheen.setColorAt(0.62, QColor(255, 255, 255, 14))
            sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(sheen_path, sheen)
            painter.restore()

            outer_pen = QPen(COLOR["panel_edge"], 1.0)
            painter.setPen(outer_pen)
            painter.drawPath(path)

            inner_rect = rect.adjusted(1, 1, -1, -1)
            inner_path = QPainterPath()
            inner_path.addRoundedRect(inner_rect, max(6, self._radius - 1), max(6, self._radius - 1))
            inner_pen = QPen(COLOR["panel_edge_inner"], 1.0)
            painter.setPen(inner_pen)
            painter.drawPath(inner_path)

            painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
            painter.drawLine(rect.left() + 14, rect.top() + 10, rect.right() - 14, rect.top() + 10)
            painter.setPen(QPen(QColor(40, 26, 42, 14), 1.0))
            painter.drawLine(rect.left() + 18, rect.bottom() - 10, rect.right() - 18, rect.bottom() - 10)
        finally:
            painter.end()


class PlainPanel(QFrame):
    def __init__(self, radius: int = 18, parent: QWidget | None = None):
        super().__init__(parent)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(1, 1, -1, -1)
            if rect.width() <= 2 or rect.height() <= 2:
                return
            path = QPainterPath()
            path.addRoundedRect(rect, self._radius, self._radius)

            fill = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            fill.setColorAt(0.0, COLOR["plain_fill"])
            fill.setColorAt(1.0, COLOR["plain_fill_bottom"])
            painter.fillPath(path, fill)

            painter.save()
            painter.setClipPath(path)
            soft_glow = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.top() + rect.height() * 0.42)
            soft_glow.setColorAt(0.0, QColor(255, 255, 255, 42))
            soft_glow.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillRect(rect.adjusted(0, 0, 0, -rect.height() // 2), soft_glow)
            painter.restore()

            painter.setPen(QPen(COLOR["plain_edge"], 1.0))
            painter.drawPath(path)
        finally:
            painter.end()


class BackdropSurface(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()
            painter.fillRect(rect, Qt.GlobalColor.transparent)

            shell_rect = rect.adjusted(6, 6, -6, -6)
            shell_path = QPainterPath()
            shell_path.addRoundedRect(shell_rect, 32, 32)
            painter.setPen(QPen(COLOR["shell_edge"], 1.0))
            painter.drawPath(shell_path)

            top_haze = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.top() + rect.height() * 0.32)
            top_haze.setColorAt(0.0, QColor(255, 250, 253, 52))
            top_haze.setColorAt(0.7, QColor(255, 250, 253, 14))
            top_haze.setColorAt(1.0, QColor(255, 250, 253, 0))
            painter.fillRect(rect.adjusted(0, 0, 0, -int(rect.height() * 0.60)), top_haze)

            blobs = [
                (QColor(255, 160, 132, 44), QRect(int(rect.width() * 0.56), -90, 430, 320)),
                (QColor(242, 124, 171, 34), QRect(-70, int(rect.height() * 0.18), 360, 300)),
                (QColor(241, 185, 132, 26), QRect(int(rect.width() * 0.18), int(rect.height() * 0.70), 430, 220)),
            ]
            for color, blob_rect in blobs:
                gradient = QRadialGradient(blob_rect.center(), max(blob_rect.width(), blob_rect.height()) * 0.55)
                c0 = QColor(color)
                c1 = QColor(color)
                c1.setAlpha(0)
                gradient.setColorAt(0.0, c0)
                gradient.setColorAt(1.0, c1)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(gradient)
                painter.drawEllipse(blob_rect)
        finally:
            painter.end()


class SidebarButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(46)
        self.setStyleSheet(
            """
            QPushButton {
                color: rgba(62,50,66,0.96);
                background: rgba(255,255,255,0.18);
                border: 1px solid rgba(255,255,255,0.42);
                border-radius: 16px;
                padding: 12px 16px;
                text-align: left;
                font-weight: 700;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.28);
                border: 1px solid rgba(255,255,255,0.56);
            }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(244,153,124,0.64),
                    stop:1 rgba(233,133,173,0.46));
                border: 1px solid rgba(255,255,255,0.74);
            }
            """
        )


class ChipButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(38)
        self.setStyleSheet(
            """
            QPushButton {
                color: rgba(62,50,66,0.96);
                background: rgba(255,255,255,0.18);
                border: 1px solid rgba(255,255,255,0.42);
                border-radius: 14px;
                padding: 8px 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.30);
                border: 1px solid rgba(255,255,255,0.56);
            }
            QPushButton:checked {
                background: rgba(244,153,124,0.44);
                border: 1px solid rgba(255,255,255,0.74);
            }
            """
        )


class SegmentedTabButton(QPushButton):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(42)
        self.setMinimumWidth(170)
        self.setStyleSheet(
            """
            QPushButton {
                color: rgba(58,46,62,0.96);
                background: rgba(255,255,255,0.14);
                border: 1px solid rgba(255,255,255,0.42);
                border-radius: 17px;
                padding: 10px 20px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.26);
                border: 1px solid rgba(255,255,255,0.56);
            }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(244,153,124,0.78),
                    stop:1 rgba(233,133,173,0.68));
                border: 1px solid rgba(255,255,255,0.82);
                color: rgba(38,24,33,0.98);
            }
            """
        )


class DraggableGlassPanel(FrostedPanel):
    def __init__(self, window: "SubtitleGUI", radius: int = 24, parent: QWidget | None = None):
        super().__init__(radius=radius, parent=parent)
        self.window_ref = window

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.window_ref.isMaximized():
            handle = self.window_ref.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window_ref.toggle_maximize_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class TitleBar(QWidget):
    def __init__(self, window: "SubtitleGUI"):
        super().__init__(window)
        self.window_ref = window
        self.setFixedHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(10)

        self.menu_btn = QToolButton(self)
        self.menu_btn.setText("☰")
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_btn.setAutoRaise(True)
        self.menu_btn.clicked.connect(window.toggle_sidebar)
        layout.addWidget(self.menu_btn)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self.title_label = QLabel(APP_NAME)
        self.title_label.setObjectName("WindowTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.subtitle_label = QLabel(APP_TAGLINE)
        self.subtitle_label.setObjectName("WindowSubtitle")
        self.subtitle_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.subtitle_label.setWordWrap(False)
        text_col.addWidget(self.title_label)
        text_col.addWidget(self.subtitle_label)
        layout.addLayout(text_col)
        layout.addStretch(1)

        self.min_btn = self._make_ctrl("—", window.showMinimized)
        self.max_btn = self._make_ctrl("▢", window.toggle_maximize_restore)
        self.close_btn = self._make_ctrl("✕", window.close)
        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

    def _make_ctrl(self, text: str, slot):
        btn = QToolButton(self)
        btn.setText(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAutoRaise(True)
        btn.clicked.connect(slot)
        btn.setStyleSheet(
            """
            QToolButton {
                color: rgba(70,58,74,0.96);
                background: rgba(255,255,255,0.16);
                border: 1px solid rgba(255,255,255,0.40);
                border-radius: 12px;
                min-width: 36px;
                min-height: 36px;
            }
            QToolButton:hover {
                background: rgba(255,255,255,0.28);
                border: 1px solid rgba(255,255,255,0.56);
            }
            """
        )
        return btn

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.window_ref.isMaximized():
            handle = self.window_ref.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window_ref.toggle_maximize_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ModelDownloadDialog(QDialog):
    def __init__(self, info: dict, reason: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("모델 다운로드 확인")
        self.setModal(True)
        self.setMinimumWidth(720)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        card = FrostedPanel(radius=26)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        title = QLabel("선택 모델 다운로드")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)

        reason_label = QLabel(reason)
        reason_label.setWordWrap(True)
        reason_label.setObjectName("BodyText")
        layout.addWidget(reason_label)

        rows = [
            ("모델", info.get("label", "알 수 없음")),
            ("설명", info.get("long_note", "알 수 없음")),
            ("원본", info.get("download_source", "알 수 없음")),
            ("저장 위치", info.get("download_target_display") or compact_path_for_display(info.get("download_target", ""), 4)),
            ("예상 크기", info.get("remote_size_text", "알 수 없음")),
            ("100 Mb/s", info.get("eta_100", "알 수 없음")),
            ("500 Mb/s", info.get("eta_500", "알 수 없음")),
            ("1 Gb/s", info.get("eta_1000", "알 수 없음")),
        ]

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for row, (key, value) in enumerate(rows):
            key_label = QLabel(key)
            key_label.setObjectName("FormLabel")
            value_label = QLabel(str(value))
            value_label.setObjectName("BodyText")
            value_label.setWordWrap(True)
            grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value_label, row, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("다운로드 시작")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SubtitleGUI(QMainWindow):
    def __init__(self):
        self.qt_app = QApplication.instance() or QApplication(sys.argv)
        self.qt_app.setStyle("Fusion")
        super().__init__()

        self.msg_queue = queue.Queue()
        self.worker_thread = None
        self.output_path = None
        self.output_paths = {}
        self.batch_results = []
        self.input_files = []
        self.settings = load_settings()
        self.status_details = {}
        self.base_status_details = {}
        self.live_resource_data = {}
        self.resource_refresh_blocked = False
        self.last_measured_speed_mbps = None
        self.last_measured_repo_id = ""
        self.cancel_event = None
        self.current_task_kind = ""
        self.current_task_label = ""
        self.current_progress_percent = 0.0
        self.job_started_at = None
        self.transfer_mode = ""
        self.sidebar_expanded = True
        self._layered_window_applied = False

        self.setWindowTitle(APP_NAME)
        self.resize(1440, 940)
        self.setMinimumSize(1180, 820)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)

        icon_path = bundled_icon_path()
        if icon_path and os.path.isfile(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self._init_fonts()
        self._build_ui()
        self._apply_styles()
        self._load_settings_into_ui()
        self._refresh_selection_hints()
        self._refresh_model_state_local()

        self.queue_timer = QTimer(self)
        self.queue_timer.timeout.connect(self._poll_queue)
        self.queue_timer.start(100)

        self.job_clock_timer = QTimer(self)
        self.job_clock_timer.timeout.connect(self._refresh_job_clock)
        self.job_clock_timer.setInterval(1000)

        self.resource_timer = QTimer(self)
        self.resource_timer.timeout.connect(self.refresh_live_resource_now)
        self.resource_timer.start(5000)

        QTimer.singleShot(150, self.start_system_check)
        QTimer.singleShot(300, self.refresh_live_resource_now)

    # -------------------------------------------------
    # Qt boot / shell
    # -------------------------------------------------
    def mainloop(self):
        self.show()
        return self.qt_app.exec()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._layered_window_applied:
            enable_true_layered_window(self)
            self._layered_window_applied = True


    def closeEvent(self, event):
        try:
            if self.cancel_event is not None:
                self.cancel_event.set()
        except Exception:
            pass
        super().closeEvent(event)

    def toggle_maximize_restore(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _init_fonts(self):
        db = QFontDatabase()
        families = set(db.families())

        def pick(candidates: list[str], fallback: str | None = None) -> str:
            for name in candidates:
                if name in families:
                    return name
            return fallback or self.qt_app.font().family() or "Segoe UI"

        self.ui_font_family = pick([
            "Pretendard",
            "Pretendard Variable",
            "Noto Sans KR",
            "Noto Sans CJK KR",
            "Source Han Sans KR",
            "Malgun Gothic",
            "Segoe UI",
        ])
        self.code_font_family = pick([
            "JetBrains Mono",
            "Cascadia Code",
            "D2Coding",
            "Consolas",
        ], fallback=self.ui_font_family)
        self.qt_app.setFont(QFont(self.ui_font_family, 10))

    def _apply_styles(self):
        self.setStyleSheet(
            f"""
            QWidget {{
                color: rgba(46,38,49,0.97);
                font-family: '{self.ui_font_family}';
                background: transparent;
            }}
            QLabel#WindowTitle {{
                font-size: 18px;
                font-weight: 800;
            }}
            QLabel#WindowSubtitle {{
                color: rgba(104,92,110,0.80);
                font-size: 11px;
            }}
            QLabel#PageTitle, QLabel#DialogTitle {{
                font-size: 22px;
                font-weight: 800;
            }}
            QLabel#SectionTitle {{
                font-size: 16px;
                font-weight: 800;
            }}
            QLabel#SectionSub, QLabel#BodyText {{
                color: rgba(92,82,98,0.92);
                font-size: 12px;
            }}
            QLabel#MutedText {{
                color: rgba(122,112,128,0.84);
                font-size: 11px;
            }}
            QLabel#FormLabel {{
                color: rgba(78,66,84,0.96);
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#HeroValue {{
                font-size: 18px;
                font-weight: 800;
            }}
            QLabel#StatusHeadline {{
                font-size: 20px;
                font-weight: 800;
            }}
            QComboBox {{
                background: rgba(255,255,255,0.22);
                border: 1px solid rgba(255,255,255,0.50);
                border-radius: 14px;
                padding: 10px 12px;
                min-height: 22px;
            }}
            QComboBox:hover {{
                background: rgba(255,255,255,0.28);
                border: 1px solid rgba(255,255,255,0.62);
            }}
            QComboBox::drop-down {{
                border: none;
                width: 26px;
            }}
            QComboBox QAbstractItemView {{
                background: rgba(248,245,248,0.98);
                border: 1px solid rgba(255,255,255,0.78);
                selection-background-color: rgba(244,153,124,0.24);
                color: rgba(46,38,49,0.98);
                padding: 6px;
                outline: 0;
            }}
            QListWidget, QPlainTextEdit {{
                background: rgba(255,255,255,0.20);
                border: 1px solid rgba(255,255,255,0.50);
                border-radius: 16px;
                padding: 10px;
                selection-background-color: rgba(244,153,124,0.18);
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: 10px;
                margin: 2px 0px;
            }}
            QListWidget::item:selected {{
                background: rgba(244,153,124,0.22);
            }}
            QPlainTextEdit {{
                color: rgba(60,48,66,0.98);
            }}
            QCheckBox {{
                spacing: 10px;
                color: rgba(46,38,49,0.97);
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 6px;
                border: 1px solid rgba(255,255,255,0.58);
                background: rgba(255,255,255,0.24);
            }}
            QCheckBox::indicator:checked {{
                background: rgba(244,153,124,0.74);
            }}
            QProgressBar {{
                background: rgba(255,255,255,0.18);
                border: 1px solid rgba(255,255,255,0.48);
                border-radius: 11px;
                min-height: 18px;
                text-align: center;
                color: rgba(72,58,76,0.96);
                font-weight: 700;
            }}
            QProgressBar::chunk {{
                border-radius: 10px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(244,153,124,0.92),
                    stop:1 rgba(233,133,173,0.86));
            }}
            QPushButton#PrimaryButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(244,153,124,0.92),
                    stop:1 rgba(233,133,173,0.80));
                border: 1px solid rgba(255,255,255,0.66);
                border-radius: 15px;
                padding: 12px 16px;
                color: rgba(38,24,33,0.97);
                font-weight: 800;
            }}
            QPushButton#PrimaryButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(246,171,141,0.95),
                    stop:1 rgba(238,149,186,0.84));
            }}
            QPushButton#SoftButton {{
                background: rgba(255,255,255,0.18);
                border: 1px solid rgba(255,255,255,0.48);
                border-radius: 15px;
                padding: 11px 14px;
                font-weight: 700;
                color: rgba(58,46,62,0.96);
            }}
            QPushButton#SoftButton:hover {{
                background: rgba(255,255,255,0.28);
                border: 1px solid rgba(255,255,255,0.60);
            }}
            QPushButton#DangerButton {{
                background: rgba(216,112,112,0.16);
                border: 1px solid rgba(216,112,112,0.32);
                border-radius: 15px;
                padding: 11px 14px;
                font-weight: 700;
                color: rgba(112,54,54,0.96);
            }}
            QPushButton#DangerButton:hover {{
                background: rgba(216,112,112,0.24);
            }}
            QPushButton:disabled {{
                color: rgba(114,104,120,0.54);
                background: rgba(255,255,255,0.10);
                border: 1px solid rgba(255,255,255,0.24);
            }}
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 12px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(172,160,179,0.46);
                border-radius: 6px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
                border: none;
                height: 0px;
            }}
            """
        )

    def _build_ui(self):
        surface = BackdropSurface()
        surface.setObjectName("SurfaceRoot")
        surface.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        surface.setAutoFillBackground(False)
        self.setCentralWidget(surface)

        root = QVBoxLayout(surface)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        self.title_bar = TitleBar(self)
        self.title_bar.menu_btn.hide()
        root.addWidget(self.title_bar)

        self.tab_shell = DraggableGlassPanel(self, radius=24)
        self.tab_shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        tab_layout = QHBoxLayout(self.tab_shell)
        tab_layout.setContentsMargins(18, 14, 18, 14)
        tab_layout.setSpacing(10)
        tab_layout.addStretch(1)
        self.nav_buttons = []
        for text_label, index in [("설정", 0), ("실행", 1)]:
            btn = SegmentedTabButton(text_label)
            btn.clicked.connect(lambda checked=False, idx=index: self._switch_page(idx))
            tab_layout.addWidget(btn)
            self.nav_buttons.append(btn)
        tab_layout.addStretch(1)
        build_label = QLabel(f"v{APP_VERSION}")
        build_label.setObjectName("MutedText")
        build_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        tab_layout.addWidget(build_label, 0, Qt.AlignmentFlag.AlignRight)
        root.addWidget(self.tab_shell)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.stack.addWidget(self._build_settings_page())
        self.stack.addWidget(self._build_activity_page())

        self.footer_panel = FrostedPanel(radius=26)
        self.footer_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(self.footer_panel)
        self._build_footer(self.footer_panel)

        self._switch_page(0)

    def _page_shell(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        body = QWidget()
        body.setObjectName("PageBody")
        body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        body.setAutoFillBackground(False)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        head = FrostedPanel(radius=26)
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(22, 20, 22, 20)
        head_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("SectionSub")
        subtitle_label.setWordWrap(True)
        head_layout.addWidget(title_label)
        head_layout.addWidget(subtitle_label)
        body_layout.addWidget(head)

        content = QWidget()
        content.setObjectName("PageContent")
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        content.setAutoFillBackground(False)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        body_layout.addWidget(content, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        scroll.setAutoFillBackground(False)
        viewport = scroll.viewport()
        if viewport is not None:
            viewport.setObjectName("ScrollViewport")
            viewport.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            viewport.setAutoFillBackground(False)
        scroll.setWidget(body)
        return scroll, content_layout

    def _build_home_page(self):
        return self._build_activity_page()

    def _build_settings_page(self):
        page, layout = self._page_shell(
            "전사 설정",
            "전사에 대한 다양한 설정을 제공합니다. 전사 언어, 프리셋, 음성 보정, 출력 형식 등을 조정할 수 있습니다. 추가적인 세부 설정은 버튼을 눌러서 확인하십시오.",
        )

        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(12)
        top_grid.setVerticalSpacing(12)
        layout.addLayout(top_grid)

        input_card = FrostedPanel(radius=26)
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(22, 20, 22, 20)
        input_layout.setSpacing(12)
        self._section_header(input_layout, "입력 파일", "한 개 또는 여러 개의 오디오/비디오 파일을 선택할 수 있습니다. 결과는 각 원본 파일과 같은 폴더에 저장됩니다.")

        file_btn_row = QHBoxLayout()
        self.pick_file_btn = self._make_button("파일 선택", self.browse_input_files, primary=True)
        self.add_file_btn = self._make_button("여러 파일 추가", self.add_more_input_files)
        self.remove_file_btn = self._make_button("선택 제거", self.remove_selected_input_files)
        self.clear_file_btn = self._make_button("목록 지우기", self.clear_input_files, danger=True)
        for widget in [self.pick_file_btn, self.add_file_btn, self.remove_file_btn, self.clear_file_btn]:
            file_btn_row.addWidget(widget)
        file_btn_row.addStretch(1)
        input_layout.addLayout(file_btn_row)

        self.file_summary_label = QLabel("선택된 파일이 없습니다.")
        self.file_summary_label.setObjectName("BodyText")
        self.file_summary_label.setWordWrap(True)
        input_layout.addWidget(self.file_summary_label)

        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(220)
        self.file_list.itemDoubleClicked.connect(lambda _item: self.remove_selected_input_files())
        input_layout.addWidget(self.file_list)

        top_grid.addWidget(input_card, 0, 0, 1, 2)

        basic_card = FrostedPanel(radius=26)
        basic_layout = QVBoxLayout(basic_card)
        basic_layout.setContentsMargins(22, 20, 22, 20)
        basic_layout.setSpacing(12)
        self._section_header(basic_layout, "전사 설정", "전사 언어, 프리셋, 모델을 선택합니다.")

        self.language_combo = QComboBox()
        for code, name in LANGUAGE_OPTIONS:
            self.language_combo.addItem(f"{name} · {code}", code)
        self.language_combo.currentIndexChanged.connect(self._refresh_selection_hints)
        self._form_row(basic_layout, "언어", self.language_combo)

        self.model_combo = QComboBox()
        for entry in MODEL_CATALOG:
            self.model_combo.addItem(f"{entry['label']} · {entry['short_note']}", entry["id"])
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._form_row(basic_layout, "모델", self.model_combo)

        self.preset_combo = QComboBox()
        for preset in TRANSCRIPTION_PRESETS:
            self.preset_combo.addItem(f"{preset['label']} · {preset['short_note']}", preset["id"])
        self.preset_combo.currentIndexChanged.connect(self._refresh_selection_hints)
        self._form_row(basic_layout, "프리셋", self.preset_combo)

        rec_box = PlainPanel(radius=18)
        rec_inner = QVBoxLayout(rec_box)
        rec_inner.setContentsMargins(16, 14, 16, 14)
        rec_inner.setSpacing(8)
        title = QLabel("자동 권장 설정")
        title.setObjectName("FormLabel")
        self.recommendation_summary_label = QLabel("파일을 선택하면 권장 설정을 제안합니다.")
        self.recommendation_summary_label.setObjectName("BodyText")
        self.recommendation_summary_label.setWordWrap(True)
        self.recommendation_meta_label = QLabel("")
        self.recommendation_meta_label.setObjectName("MutedText")
        self.recommendation_meta_label.setWordWrap(True)
        rec_btn_row = QHBoxLayout()
        self.apply_rec_btn = self._make_button("권장값 적용", self.apply_recommendations)
        rec_btn_row.addWidget(self.apply_rec_btn)
        rec_btn_row.addStretch(1)
        rec_inner.addWidget(title)
        rec_inner.addWidget(self.recommendation_summary_label)
        rec_inner.addWidget(self.recommendation_meta_label)
        rec_inner.addLayout(rec_btn_row)
        basic_layout.addWidget(rec_box)
        top_grid.addWidget(basic_card, 1, 0)

        device_card = FrostedPanel(radius=26)
        device_layout = QVBoxLayout(device_card)
        device_layout.setContentsMargins(22, 20, 22, 20)
        device_layout.setSpacing(12)
        self._section_header(device_layout, "장치 선호", "GPU와 CPU 중 사용 가능한 환경을 자동으로 선택하거나 고정할 수 있습니다.")
        self.device_note_label = QLabel("")
        self.device_note_label.setObjectName("BodyText")
        self.device_note_label.setWordWrap(True)
        device_layout.addWidget(self.device_note_label)
        chip_row = QHBoxLayout()
        self.device_buttons = {}
        for text_label, value in [("자동", "auto"), ("GPU", "cuda"), ("CPU", "cpu")]:
            btn = ChipButton(text_label)
            btn.clicked.connect(lambda checked=False, v=value: self._set_preferred_device(v))
            self.device_buttons[value] = btn
            chip_row.addWidget(btn)
        chip_row.addStretch(1)
        device_layout.addLayout(chip_row)

        device_action_row = QHBoxLayout()
        self.system_check_btn = self._make_button("시스템 점검", self.start_system_check)
        self.save_settings_btn = self._make_button("설정 저장", self.save_ui_settings, primary=True)
        device_action_row.addWidget(self.system_check_btn)
        device_action_row.addWidget(self.save_settings_btn)
        device_action_row.addStretch(1)
        device_layout.addLayout(device_action_row)
        top_grid.addWidget(device_card, 1, 1)

        audio_card = FrostedPanel(radius=26)
        audio_layout = QVBoxLayout(audio_card)
        audio_layout.setContentsMargins(22, 20, 22, 20)
        audio_layout.setSpacing(12)
        self._section_header(audio_layout, "음성 보정", "보정은 전처리 단계에서만 적용됩니다. 표준은 일반 음성, 강함은 소음이 큰 녹음에 권장합니다.")
        audio_chip_row = QHBoxLayout()
        self.audio_buttons = {}
        for text_label, value in [("끔", "off"), ("표준", "standard"), ("강함", "strong")]:
            btn = ChipButton(text_label)
            btn.clicked.connect(lambda checked=False, v=value: self._set_audio_enhance_level(v))
            self.audio_buttons[value] = btn
            audio_chip_row.addWidget(btn)
        audio_chip_row.addStretch(1)
        audio_layout.addLayout(audio_chip_row)
        top_grid.addWidget(audio_card, 2, 0)

        output_card = FrostedPanel(radius=26)
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(22, 20, 22, 20)
        output_layout.setSpacing(12)
        self._section_header(output_layout, "출력 형식", "SRT는 기본 자막, TXT는 문장 모음, VTT는 웹/영상 플레이어 연동에 적합합니다.")
        output_box = QWidget()
        output_row = QHBoxLayout(output_box)
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.setSpacing(18)
        self.output_fmt_srt = QCheckBox("SRT")
        self.output_fmt_txt = QCheckBox("TXT")
        self.output_fmt_vtt = QCheckBox("VTT")
        for cb in [self.output_fmt_srt, self.output_fmt_txt, self.output_fmt_vtt]:
            cb.stateChanged.connect(self._refresh_selection_hints)
            output_row.addWidget(cb)
        output_row.addStretch(1)
        output_layout.addWidget(output_box)
        top_grid.addWidget(output_card, 2, 1)

        return page

    def _build_activity_page(self):
        page, layout = self._page_shell(
            "진행 상태",
            "현재 단계, 진행률, 작업 로그, 장치 및 자원 상태를 확인할 수 있습니다.",
        )

        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(12)
        top_grid.setVerticalSpacing(12)
        layout.addLayout(top_grid)

        start_card = FrostedPanel(radius=26)
        start_layout = QVBoxLayout(start_card)
        start_layout.setContentsMargins(22, 20, 22, 20)
        start_layout.setSpacing(10)
        self._section_header(start_layout, "전사 시작", "입력 파일과 설정을 확인한 뒤 전사를 시작합니다.")
        self.launch_status_label = QLabel("준비됨")
        self.launch_status_label.setObjectName("StatusHeadline")
        self.launch_status_label.setWordWrap(True)
        self.launch_meta_label = QLabel("전사할 파일과 실행 설정을 확인하십시오.")
        self.launch_meta_label.setObjectName("BodyText")
        self.launch_meta_label.setWordWrap(True)
        start_layout.addWidget(self.launch_status_label)
        start_layout.addWidget(self.launch_meta_label)
        start_btn_row = QHBoxLayout()
        self.home_start_btn = self._make_button("전사 시작", self.start_transcription, primary=True)
        self.home_activity_btn = self._make_button("설정 보기", lambda: self._switch_page(0))
        self.execution_save_btn = self._make_button("설정 저장", self.save_ui_settings)
        start_btn_row.addWidget(self.home_start_btn)
        start_btn_row.addWidget(self.home_activity_btn)
        start_btn_row.addWidget(self.execution_save_btn)
        start_btn_row.addStretch(1)
        start_layout.addLayout(start_btn_row)
        top_grid.addWidget(start_card, 0, 0)

        quick_card = FrostedPanel(radius=26)
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(22, 20, 22, 20)
        quick_layout.setSpacing(10)
        self._section_header(quick_layout, "현재 구성", "가장 중요한 실행 설정만 요약합니다.")
        self.quick_lang_value = self._metric_row(quick_layout, "언어")
        self.quick_model_value = self._metric_row(quick_layout, "모델")
        self.quick_preset_value = self._metric_row(quick_layout, "프리셋")
        self.quick_device_value = self._metric_row(quick_layout, "장치")
        self.quick_audio_value = self._metric_row(quick_layout, "음성 보정")
        self.quick_output_value = self._metric_row(quick_layout, "출력 형식")
        quick_btn_row = QHBoxLayout()
        self.execution_settings_btn = self._make_button("설정 열기", lambda: self._switch_page(0))
        self.execution_check_btn = self._make_button("시스템 점검", self.start_system_check)
        quick_btn_row.addWidget(self.execution_settings_btn)
        quick_btn_row.addWidget(self.execution_check_btn)
        quick_btn_row.addStretch(1)
        quick_layout.addLayout(quick_btn_row)
        top_grid.addWidget(quick_card, 0, 1)

        mid_grid = QGridLayout()
        mid_grid.setHorizontalSpacing(12)
        mid_grid.setVerticalSpacing(12)
        layout.addLayout(mid_grid)

        model_card = FrostedPanel(radius=26)
        model_layout = QVBoxLayout(model_card)
        model_layout.setContentsMargins(22, 20, 22, 20)
        model_layout.setSpacing(10)
        self._section_header(model_layout, "선택 모델 상태", "캐시 존재 여부와 현재 준비 상태를 즉시 확인합니다.")
        badge_row = QHBoxLayout()
        self.model_state_badge = QLabel("확인 중")
        badge_row.addWidget(self.model_state_badge)
        badge_row.addStretch(1)
        model_layout.addLayout(badge_row)
        self.model_status_summary_label = QLabel("선택 모델의 상태를 확인하는 중입니다.")
        self.model_status_summary_label.setObjectName("BodyText")
        self.model_status_summary_label.setWordWrap(True)
        self.model_cache_path_label = QLabel("캐시 경로를 확인하는 중입니다.")
        self.model_cache_path_label.setObjectName("MutedText")
        self.model_cache_path_label.setWordWrap(True)
        model_layout.addWidget(self.model_status_summary_label)
        model_layout.addWidget(self.model_cache_path_label)
        model_btn_row = QHBoxLayout()
        self.download_model_btn = self._make_button("모델 다운로드", self.start_model_download)
        self.refresh_model_btn = self._make_button("상태 새로고침", self._refresh_model_state_local)
        model_btn_row.addWidget(self.download_model_btn)
        model_btn_row.addWidget(self.refresh_model_btn)
        model_btn_row.addStretch(1)
        model_layout.addLayout(model_btn_row)
        mid_grid.addWidget(model_card, 0, 0)

        progress_card = FrostedPanel(radius=26)
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(22, 20, 22, 20)
        progress_layout.setSpacing(10)
        self._section_header(progress_layout, "진행 상태", "현재 단계, 진행률, 경과 시간, 전처리 상태를 확인합니다.")
        self.activity_status_label = QLabel("준비됨")
        self.activity_status_label.setObjectName("StatusHeadline")
        self.activity_status_meta_label = QLabel("대기 중")
        self.activity_status_meta_label.setObjectName("BodyText")
        self.activity_status_meta_label.setWordWrap(True)
        self.activity_job_meta_label = QLabel("")
        self.activity_job_meta_label.setObjectName("MutedText")
        self.activity_job_meta_label.setWordWrap(True)
        self.activity_preprocess_label = QLabel("")
        self.activity_preprocess_label.setObjectName("MutedText")
        self.activity_preprocess_label.setWordWrap(True)
        self.activity_progress = QProgressBar()
        self.activity_progress.setRange(0, 100)
        self.activity_progress.setValue(0)
        progress_layout.addWidget(self.activity_status_label)
        progress_layout.addWidget(self.activity_status_meta_label)
        progress_layout.addWidget(self.activity_job_meta_label)
        progress_layout.addWidget(self.activity_preprocess_label)
        progress_layout.addWidget(self.activity_progress)
        mid_grid.addWidget(progress_card, 0, 1)

        lower_grid = QGridLayout()
        lower_grid.setHorizontalSpacing(12)
        lower_grid.setVerticalSpacing(12)
        layout.addLayout(lower_grid)

        resource_card = FrostedPanel(radius=26)
        resource_layout = QVBoxLayout(resource_card)
        resource_layout.setContentsMargins(22, 20, 22, 20)
        resource_layout.setSpacing(10)
        self._section_header(resource_layout, "작업 중 자원 상태", "앱/시스템 부하와 VRAM 사용량을 함께 보여줍니다.")
        badge_row = QHBoxLayout()
        self.resource_badge_label = QLabel("점검 중")
        badge_row.addWidget(self.resource_badge_label)
        badge_row.addStretch(1)
        badge_row.addWidget(self._make_button("새로고침", self.refresh_live_resource_now))
        resource_layout.addLayout(badge_row)
        self.resource_summary_label = QLabel("실시간 자원 상태를 불러오는 중입니다.")
        self.resource_summary_label.setObjectName("BodyText")
        self.resource_summary_label.setWordWrap(True)
        self.resource_meta_label = QLabel("마지막 갱신 --:--:--")
        self.resource_meta_label.setObjectName("MutedText")
        self.resource_meta_label.setWordWrap(True)
        resource_layout.addWidget(self.resource_summary_label)
        resource_layout.addWidget(self.resource_meta_label)
        lower_grid.addWidget(resource_card, 0, 0)

        status_card = FrostedPanel(radius=26)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(22, 20, 22, 20)
        status_layout.setSpacing(12)
        self._section_header(status_layout, "상태 요약", "모델, 엔진, 장치, 런타임 상태를 확인합니다.")
        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(12)
        status_grid.setVerticalSpacing(12)
        self.status_tiles = {}
        rows = [("model", "모델"), ("engine", "엔진"), ("torch", "PyTorch"), ("device", "장치"), ("runtime", "런타임")]
        for idx, (key, label_text) in enumerate(rows):
            tile = PlainPanel(radius=20)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(16, 14, 16, 14)
            tile_layout.setSpacing(6)
            title = QLabel(label_text)
            title.setObjectName("FormLabel")
            badge = QLabel("확인 중")
            summary = QLabel("상태를 확인하는 중입니다.")
            summary.setObjectName("BodyText")
            summary.setWordWrap(True)
            meta = QLabel("")
            meta.setObjectName("MutedText")
            meta.setWordWrap(True)
            tile_layout.addWidget(title)
            tile_layout.addWidget(badge)
            tile_layout.addWidget(summary)
            tile_layout.addWidget(meta)
            status_grid.addWidget(tile, idx // 2, idx % 2)
            self.status_tiles[key] = {"badge": badge, "summary": summary, "meta": meta}
        status_layout.addLayout(status_grid)
        lower_grid.addWidget(status_card, 0, 1)

        log_card = FrostedPanel(radius=26)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(22, 20, 22, 20)
        log_layout.setSpacing(12)
        self._section_header(log_layout, "작업 로그", "현재 단계, 진행률, 작업 로그, 다운로드 속도와 남은 시간, 장치 및 자원 상태를 함께 확인할 수 있습니다.")
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(320)
        self.log_text.setFont(QFont(self.code_font_family, 10))
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_card)

        return page

    def _build_footer(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        layout.addLayout(top)

        self.start_btn = self._make_button("전사 시작", self.start_transcription, primary=True)
        self.cancel_btn = self._make_button("취소", self._cancel_current_task, danger=True)
        self.cancel_btn.setEnabled(False)
        self.open_result_btn = self._make_button("결과 열기", self.open_result)
        self.open_folder_btn = self._make_button("폴더 열기", self.open_result_folder)
        self.open_result_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        for btn in [self.start_btn, self.cancel_btn, self.open_result_btn, self.open_folder_btn]:
            top.addWidget(btn)

        top.addSpacing(8)
        self.footer_status_label = QLabel("준비됨")
        self.footer_status_label.setObjectName("BodyText")
        top.addWidget(self.footer_status_label, 1)
        top.addSpacing(8)
        self.size_grip = QSizeGrip(parent)
        top.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignBottom)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(10)
        self.footer_transfer_label = QLabel("")
        self.footer_transfer_label.setObjectName("BodyText")
        self.footer_transfer_label.setWordWrap(True)
        self.footer_meta_label = QLabel("")
        self.footer_meta_label.setObjectName("MutedText")
        self.footer_meta_label.setWordWrap(True)
        meta_row.addWidget(self.footer_transfer_label, 1)
        meta_row.addWidget(self.footer_meta_label, 1)
        layout.addLayout(meta_row)

        self.footer_progress = QProgressBar()
        self.footer_progress.setRange(0, 100)
        self.footer_progress.setValue(0)
        layout.addWidget(self.footer_progress)

    # -------------------------------------------------
    # Small builders
    # -------------------------------------------------
    def _make_button(self, text: str, slot, primary: bool = False, danger: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        if primary:
            btn.setObjectName("PrimaryButton")
        elif danger:
            btn.setObjectName("DangerButton")
        else:
            btn.setObjectName("SoftButton")
        return btn

    def _section_header(self, layout: QVBoxLayout, title: str, subtitle: str):
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("SectionSub")
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)

    def _metric_row(self, layout: QVBoxLayout, label: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(10)
        left = QLabel(label)
        left.setObjectName("FormLabel")
        value = QLabel("-")
        value.setObjectName("HeroValue")
        value.setWordWrap(True)
        row.addWidget(left)
        row.addWidget(value, 1)
        layout.addLayout(row)
        return value

    def _form_row(self, layout: QVBoxLayout, label: str, widget: QWidget):
        label_widget = QLabel(label)
        label_widget.setObjectName("FormLabel")
        layout.addWidget(label_widget)
        layout.addWidget(widget)

    def _set_badge(self, label: QLabel, level: str, text: str):
        level = (level or "neutral").lower()
        palette = {
            "success": ("rgba(114,226,163,0.22)", "rgba(204,255,223,0.94)", "rgba(114,226,163,0.32)"),
            "warning": ("rgba(255,198,116,0.22)", "rgba(255,239,205,0.96)", "rgba(255,198,116,0.30)"),
            "danger": ("rgba(255,117,117,0.22)", "rgba(255,220,220,0.96)", "rgba(255,117,117,0.30)"),
            "info": ("rgba(129,191,255,0.22)", "rgba(225,239,255,0.96)", "rgba(129,191,255,0.30)"),
            "neutral": ("rgba(255,255,255,0.12)", "rgba(248,244,250,0.92)", "rgba(255,255,255,0.16)"),
        }
        bg, fg, border = palette.get(level, palette["neutral"])
        label.setText(text)
        label.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {border}; border-radius:11px; padding:6px 10px; font-weight:700;"
        )

    # -------------------------------------------------
    # Navigation
    # -------------------------------------------------
    def _switch_page(self, index: int):
        self.stack.setCurrentIndex(index)
        for idx, btn in enumerate(self.nav_buttons):
            btn.setChecked(idx == index)

    def toggle_sidebar(self):
        # 사이드바 기반 레이아웃은 제거하고 상단 탭 구조로 전환했습니다.
        return

    def _combo_data(self, combo: QComboBox, fallback: str) -> str:
        value = combo.currentData()
        return str(value) if value else fallback

    def current_lang_code(self) -> str:
        return self._combo_data(self.language_combo, DEFAULT_LANGUAGE)

    def current_model_id(self) -> str:
        return self._combo_data(self.model_combo, default_model_id())

    def current_preset_id(self) -> str:
        return self._combo_data(self.preset_combo, DEFAULT_PRESET_ID)

    def current_preferred_device(self) -> str:
        for value, btn in self.device_buttons.items():
            if btn.isChecked():
                return value
        return DEFAULT_PREFERRED_DEVICE

    def current_audio_enhance_level(self) -> str:
        for value, btn in self.audio_buttons.items():
            if btn.isChecked():
                return value
        return DEFAULT_AUDIO_ENHANCE_LEVEL

    def current_output_formats(self) -> list[str]:
        result = []
        if self.output_fmt_srt.isChecked():
            result.append("srt")
        if self.output_fmt_txt.isChecked():
            result.append("txt")
        if self.output_fmt_vtt.isChecked():
            result.append("vtt")
        return result or list(DEFAULT_OUTPUT_FORMATS)

    def _set_combo_by_data(self, combo: QComboBox, value: str):
        for idx in range(combo.count()):
            if combo.itemData(idx) == value:
                combo.setCurrentIndex(idx)
                return

    def _set_preferred_device(self, value: str):
        value = value if value in self.device_buttons else DEFAULT_PREFERRED_DEVICE
        for key, btn in self.device_buttons.items():
            btn.setChecked(key == value)
        self._refresh_selection_hints()

    def _set_audio_enhance_level(self, value: str):
        value = value if value in self.audio_buttons else DEFAULT_AUDIO_ENHANCE_LEVEL
        for key, btn in self.audio_buttons.items():
            btn.setChecked(key == value)
        self._refresh_selection_hints()

    def _on_model_changed(self):
        self._refresh_selection_hints()
        self._refresh_model_state_local()

    def _load_settings_into_ui(self):
        self._set_combo_by_data(self.language_combo, self.settings.get("language", DEFAULT_LANGUAGE))
        self._set_combo_by_data(self.model_combo, self.settings.get("model_id", default_model_id()))
        self._set_combo_by_data(self.preset_combo, self.settings.get("preset_id", DEFAULT_PRESET_ID))
        self._set_preferred_device(self.settings.get("preferred_device", DEFAULT_PREFERRED_DEVICE))
        self._set_audio_enhance_level(self.settings.get("audio_enhance_level", DEFAULT_AUDIO_ENHANCE_LEVEL))
        formats = self.settings.get("output_formats", DEFAULT_OUTPUT_FORMATS)
        self.output_fmt_srt.setChecked("srt" in formats or not formats)
        self.output_fmt_txt.setChecked("txt" in formats)
        self.output_fmt_vtt.setChecked("vtt" in formats)

    def save_ui_settings(self):
        self.settings["language"] = self.current_lang_code()
        self.settings["model_id"] = self.current_model_id()
        self.settings["preset_id"] = self.current_preset_id()
        self.settings["preferred_device"] = self.current_preferred_device()
        self.settings["audio_enhance_level"] = self.current_audio_enhance_level()
        self.settings["output_formats"] = self.current_output_formats()
        save_settings(self.settings)

    def _refresh_selection_hints(self):
        lang_code = self.current_lang_code()
        lang_name = get_language_korean_name(lang_code)
        model_entry = next((m for m in MODEL_CATALOG if m["id"] == self.current_model_id()), None)
        preset = get_transcription_preset(self.current_preset_id())
        device_text = {"auto": "자동", "cuda": "GPU", "cpu": "CPU"}.get(self.current_preferred_device(), "자동")
        audio_text = {"off": "끔", "standard": "표준", "strong": "강함"}.get(self.current_audio_enhance_level(), "끔")
        outputs = ", ".join(fmt.upper() for fmt in self.current_output_formats())

        self.quick_lang_value.setText(f"{lang_name} · {lang_code}")
        self.quick_model_value.setText(model_entry["label"] if model_entry else self.current_model_id())
        self.quick_preset_value.setText(preset["label"])
        self.quick_device_value.setText(device_text)
        self.quick_audio_value.setText(audio_text)
        self.quick_output_value.setText(outputs)

        self.launch_status_label.setText("준비됨" if not self.current_task_kind else self.current_task_label)
        self.launch_meta_label.setText(
            f"모델 {self.quick_model_value.text()} · 프리셋 {self.quick_preset_value.text()} · 출력 {outputs}"
        )

        device_note_map = {
            "auto": "GPU와 CPU 중 안정적으로 동작하는 조합을 자동 선택합니다.",
            "cuda": "GPU 가속을 우선 시도합니다. 실패하면 CPU fallback이 필요할 수 있습니다.",
            "cpu": "호환성과 재현성을 우선합니다. 처리 시간은 더 길어질 수 있습니다.",
        }
        self.device_note_label.setText(device_note_map.get(self.current_preferred_device(), "장치 선호를 확인하십시오."))

        rec = self._recommendations_for_current_inputs()
        self.recommendation_summary_label.setText(rec.get("summary", ""))
        self.recommendation_meta_label.setText(rec.get("meta", ""))

        self.home_start_btn.setEnabled(bool(self.input_files) and not (self.worker_thread and self.worker_thread.is_alive()))
        self.save_ui_settings()
        self._refresh_file_summary()

    # -------------------------------------------------
    # Files / recommendations
    # -------------------------------------------------
    def browse_input_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "전사할 파일 선택",
            "",
            "Media Files (*.mkv *.mp4 *.mov *.avi *.mp3 *.wav *.m4a *.flac *.ogg *.webm);;All Files (*)",
        )
        if files:
            self.set_input_files(files)

    def add_more_input_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "추가 파일 선택",
            "",
            "Media Files (*.mkv *.mp4 *.mov *.avi *.mp3 *.wav *.m4a *.flac *.ogg *.webm);;All Files (*)",
        )
        if files:
            self.add_input_files(files)

    def set_input_files(self, files: list[str]):
        self.input_files = []
        self.add_input_files(files)

    def add_input_files(self, files: list[str]):
        known = {os.path.normcase(os.path.abspath(path)) for path in self.input_files}
        for path in files:
            norm = os.path.normcase(os.path.abspath(path))
            if norm not in known and os.path.isfile(path):
                self.input_files.append(path)
                known.add(norm)
        self._refresh_file_list_ui()
        self._refresh_selection_hints()

    def clear_input_files(self):
        self.input_files = []
        self._refresh_file_list_ui()
        self._refresh_selection_hints()

    def remove_selected_input_files(self):
        selected = self.file_list.selectedItems()
        if not selected:
            return
        remove_set = {item.data(Qt.ItemDataRole.UserRole) for item in selected}
        self.input_files = [path for path in self.input_files if path not in remove_set]
        self._refresh_file_list_ui()
        self._refresh_selection_hints()

    def _refresh_file_list_ui(self):
        self.file_list.clear()
        for path in self.input_files:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.file_list.addItem(item)
        has_files = bool(self.input_files)
        self.remove_file_btn.setEnabled(has_files)
        self.clear_file_btn.setEnabled(has_files)
        if hasattr(self, "home_start_btn"):
            self.home_start_btn.setEnabled(has_files and not bool(self.current_task_kind))

    def _refresh_file_summary(self):
        valid = [path for path in self.input_files if os.path.isfile(path)]
        if not valid:
            self.file_summary_label.setText("선택된 파일이 없습니다.")
            return
        if len(valid) == 1:
            text = f"1개 파일 · {os.path.basename(valid[0])}\n{valid[0]}"
        else:
            text = f"총 {len(valid)}개 파일 선택됨 · 마지막 파일 {os.path.basename(valid[-1])}"
        self.file_summary_label.setText(text)

    def _recommendations_for_current_inputs(self) -> dict:
        result = {
            "summary": "파일을 선택하면 입력 특성을 바탕으로 프리셋, 음성 보정, 출력 형식을 제안합니다.",
            "meta": "",
            "preset_id": None,
            "audio_enhance_level": None,
            "output_formats": None,
            "model_id": None,
        }
        if not self.input_files:
            return result

        names = " ".join(os.path.basename(path).lower() for path in self.input_files)
        exts = {os.path.splitext(path)[1].lower() for path in self.input_files}
        batch_count = len(self.input_files)

        durations = []
        for path in self.input_files[:8]:
            try:
                duration = probe_media_duration_seconds(path)
                if duration:
                    durations.append(duration)
            except Exception:
                pass
        avg_duration = sum(durations) / len(durations) if durations else 0.0
        total_duration = sum(durations) if durations else 0.0

        noisy_keywords = ["noise", "noisy", "live", "field", "record", "현장", "소음", "잡음", "녹음"]
        lecture_keywords = ["lecture", "meeting", "seminar", "class", "회의", "강의", "세미나", "수업", "발표"]
        dialogue_keywords = ["interview", "dialogue", "drama", "movie", "podcast", "인터뷰", "드라마", "영화", "대화"]
        video_exts = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

        preset_id = "auto-balanced"
        audio_level = "off"
        why = []
        if any(keyword in names for keyword in noisy_keywords):
            preset_id = "noisy-performance"
            audio_level = "strong"
            why.append("파일명에 현장/잡음 계열 단서가 있어 잡음 대응 프리셋을 우선 권장했습니다.")
        elif avg_duration >= 1200 or any(keyword in names for keyword in lecture_keywords):
            preset_id = "lecture-meeting"
            audio_level = "standard"
            why.append("긴 발화 또는 강의/회의 계열로 보여 연속 발화 프리셋을 권장했습니다.")
        elif exts & video_exts or any(keyword in names for keyword in dialogue_keywords):
            preset_id = "dialogue-video"
            audio_level = "standard"
            why.append("영상 또는 대사형 콘텐츠로 보여 대사형 프리셋을 권장했습니다.")
        else:
            why.append("특정 단서가 강하지 않아 균형 프리셋을 유지하는 편이 안전합니다.")

        output_formats = ["srt"]
        if batch_count >= 3:
            output_formats.append("txt")
            why.append("여러 파일을 한 번에 처리하므로 전체 검토용 TXT 동시 저장을 권장했습니다.")
        if exts & video_exts:
            output_formats.append("vtt")
            why.append("영상 파일이 포함되어 웹/플레이어 호환용 VTT 저장을 함께 권장했습니다.")

        model_id = None
        if self.current_preferred_device() == "cpu":
            if total_duration >= 7200 or batch_count >= 5:
                model_id = "small"
                why.append("CPU 기준 작업량이 커 보여 처리 시간을 줄이기 위해 Small 모델을 권장했습니다.")
        elif preset_id == "quality-priority" and batch_count <= 2:
            model_id = "large-v3"

        result.update({
            "summary": f"권장 프리셋 {get_transcription_preset(preset_id)['label']} · 음성 보정 {{'off':'끔','standard':'표준','strong':'강함'}}[audio_level] · 출력 {', '.join(fmt.upper() for fmt in output_formats)}",
            "meta": " ".join(why),
            "preset_id": preset_id,
            "audio_enhance_level": audio_level,
            "output_formats": output_formats,
            "model_id": model_id,
        })
        return result

    def apply_recommendations(self):
        rec = self._recommendations_for_current_inputs()
        preset_id = rec.get("preset_id")
        audio_level = rec.get("audio_enhance_level")
        output_formats = rec.get("output_formats") or list(DEFAULT_OUTPUT_FORMATS)
        model_id = rec.get("model_id")

        if preset_id:
            self._set_combo_by_data(self.preset_combo, preset_id)
        if audio_level:
            self._set_audio_enhance_level(audio_level)
        self.output_fmt_srt.setChecked("srt" in output_formats)
        self.output_fmt_txt.setChecked("txt" in output_formats)
        self.output_fmt_vtt.setChecked("vtt" in output_formats)
        if model_id:
            self._set_combo_by_data(self.model_combo, model_id)
        self._refresh_selection_hints()
        self._append_log("자동 권장 설정을 적용했습니다.")

    def _format_preprocess_summary(self, preprocess_info: dict | None) -> str:
        if not preprocess_info:
            return ""
        summary = str(preprocess_info.get("summary", "")).strip()
        if summary:
            return summary
        mode = preprocess_info.get("mode", "")
        if mode == "enhanced":
            label = {"standard": "표준", "strong": "강함"}.get(preprocess_info.get("applied_level"), "적용")
            return f"전처리 완료 · 음성 보정 {label} 적용"
        if mode == "fallback-basic":
            return "전처리 fallback · 기본 전처리 사용"
        if mode == "basic":
            return "전처리 완료 · 기본 추출 사용"
        return "원본 입력 사용"

    def _update_preprocess_status(self, preprocess_info: dict | None, input_path: str | None = None):
        summary = self._format_preprocess_summary(preprocess_info)
        if not summary:
            self.activity_preprocess_label.setText("")
            return
        prefix = f"전처리 · {os.path.basename(input_path)} · " if input_path and len(self.input_files) > 1 else "전처리 · "
        self.activity_preprocess_label.setText(prefix + summary)

    # -------------------------------------------------
    # Status / logs / progress
    # -------------------------------------------------
    def _append_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{timestamp}] {message}")
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _set_status(self, text: str):
        self.footer_status_label.setText(text)
        if hasattr(self, "activity_status_label"):
            self.activity_status_label.setText(text)
        if hasattr(self, "launch_status_label"):
            self.launch_status_label.setText(text)

    def _set_transfer_texts(self, headline: str, meta: str = ""):
        self.footer_transfer_label.setText(headline)
        if hasattr(self, "activity_status_meta_label"):
            self.activity_status_meta_label.setText(headline)
        self.footer_meta_label.setText(meta)
        if hasattr(self, "launch_meta_label") and headline:
            self.launch_meta_label.setText(headline if not meta else f"{headline}\n{meta}")
        if meta and hasattr(self, "activity_job_meta_label"):
            self.activity_job_meta_label.setText(meta)

    def _set_progress_value(self, percent: float):
        value = int(max(0.0, min(100.0, percent)))
        self.current_progress_percent = value
        for bar in [self.footer_progress, self.activity_progress]:
            if bar.maximum() == 0:
                bar.setRange(0, 100)
            bar.setValue(value)

    def _progress_busy_on(self):
        for bar in [self.footer_progress, self.activity_progress]:
            bar.setRange(0, 0)

    def _progress_busy_off(self):
        for bar in [self.footer_progress, self.activity_progress]:
            bar.setRange(0, 100)

    def _begin_task(self, kind: str, label: str, cancellable: bool = True):
        self.current_task_kind = kind
        self.current_task_label = label
        self.cancel_event = threading.Event()
        self.job_started_at = time.time()
        self.start_btn.setEnabled(False)
        if hasattr(self, "home_start_btn"):
            self.home_start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(cancellable)
        if hasattr(self, "download_model_btn"):
            self.download_model_btn.setEnabled(False)
        self.job_clock_timer.start()
        self._set_status(label)
        self._refresh_job_clock()

    def _finish_task(self, clear_transfer: bool = False):
        self.current_task_kind = ""
        self.current_task_label = ""
        self.cancel_event = None
        self.worker_thread = None
        self.start_btn.setEnabled(True)
        if hasattr(self, "home_start_btn"):
            self.home_start_btn.setEnabled(bool(self.input_files))
        self.cancel_btn.setEnabled(False)
        if hasattr(self, "download_model_btn"):
            self.download_model_btn.setEnabled(True)
        self.job_clock_timer.stop()
        if clear_transfer:
            self.footer_transfer_label.setText("")
            self.footer_meta_label.setText("")
            if hasattr(self, "activity_job_meta_label"):
                self.activity_job_meta_label.setText("")
        self._progress_busy_off()

    def _refresh_job_clock(self):
        if not self.job_started_at:
            self.activity_job_meta_label.setText("")
            return
        elapsed = time.time() - self.job_started_at
        elapsed_text = format_elapsed_text(elapsed)
        if 0 < self.current_progress_percent < 100:
            estimated_total = elapsed * (100.0 / max(self.current_progress_percent, 1.0))
            remaining = max(0.0, estimated_total - elapsed)
            meta = f"경과 {elapsed_text} · 예상 잔여 {format_elapsed_text(remaining)}"
        else:
            meta = f"경과 {elapsed_text}"
        self.activity_job_meta_label.setText(meta)
        self.footer_meta_label.setText(meta)

    def _notify_task_busy(self, task_name: str):
        QMessageBox.information(self, "작업 진행 중", f"이미 다른 작업이 진행 중입니다.\n현재 요청: {task_name}")

    def _cancel_current_task(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
            self._append_log("취소 요청을 전달했습니다.")
            self._set_status("취소 요청을 처리하는 중입니다...")

    # -------------------------------------------------
    # Resource / model state / startup
    # -------------------------------------------------
    def _update_status_tile(self, key: str, level: str, summary: str, meta: str):
        tile = self.status_tiles.get(key)
        if not tile:
            return
        self._set_badge(tile["badge"], level, level.upper())
        tile["summary"].setText(summary)
        tile["meta"].setText(meta)

    def _refresh_model_state_local(self):
        try:
            info = inspect_model_availability(self.current_model_id(), include_remote_meta=False)
            self._apply_model_availability(info)
        except Exception as exc:
            self.model_status_summary_label.setText(f"모델 상태 확인 실패: {exc}")
            self.model_cache_path_label.setText("")
            self._set_badge(self.model_state_badge, "warning", "확인 실패")

    def _apply_model_availability(self, info: dict):
        if info.get("is_cached"):
            self._set_badge(self.model_state_badge, "success", "로컬 준비됨")
            summary = f"{info.get('label', self.current_model_id())} 모델이 이미 로컬 캐시에 있습니다."
            tile_level = "success"
        else:
            self._set_badge(self.model_state_badge, "warning", "다운로드 필요")
            summary = f"{info.get('label', self.current_model_id())} 모델이 아직 로컬에 없습니다."
            tile_level = "warning"
        self.model_status_summary_label.setText(summary)
        self.model_cache_path_label.setText(info.get("cached_path_display") or "아직 캐시가 없습니다.")
        self._update_status_tile("model", tile_level, info.get("label", self.current_model_id()), summary)

    def _update_live_resource_ui(self, payload: dict):
        level = payload.get("level", "neutral")
        self._set_badge(self.resource_badge_label, level, payload.get("pressure_label", "정보 없음"))
        summary = (
            f"앱 CPU {payload.get('app_cpu_text', '정보 없음')} · 앱 RAM {payload.get('app_ram_text', '정보 없음')} · "
            f"시스템 RAM {payload.get('ram_text', '정보 없음')} · GPU {payload.get('gpu_name', '감지되지 않음')}"
        )
        meta = (
            f"시스템 CPU {payload.get('system_cpu_text', '정보 없음')} · VRAM {payload.get('vram_text', '정보 없음')} · "
            f"마지막 갱신 {payload.get('timestamp_text', '--:--:--')}"
        )
        self.resource_summary_label.setText(summary)
        self.resource_meta_label.setText(meta)

    def refresh_live_resource_now(self):
        if self.worker_thread and self.worker_thread.is_alive() and self.current_task_kind == "model_download":
            return
        try:
            payload = collect_live_resource_status()
            self.live_resource_data = payload
            self._update_live_resource_ui(payload)
        except Exception as exc:
            self.resource_summary_label.setText(f"실시간 자원 상태 갱신 실패: {exc}")

    def start_system_check(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self._notify_task_busy("시스템 점검")
            return

        scan_settings = dict(self.settings)
        scan_settings["language"] = self.current_lang_code()
        scan_settings["model_id"] = self.current_model_id()
        scan_settings["preset_id"] = self.current_preset_id()
        scan_settings["preferred_device"] = self.current_preferred_device()

        self._set_status("시스템 상태를 점검하는 중입니다...")
        self._set_transfer_texts(
            f"시스템 점검 · 모델 {self.current_model_id()} · 장치 선호 {self.current_preferred_device().upper()}",
            "환경, 엔진, 장치, 실행 조합을 한 번에 점검합니다.",
        )
        self._begin_task("system_check", "시스템 점검", cancellable=False)
        self.worker_thread = threading.Thread(target=self._worker_system_check, args=(scan_settings,), daemon=True)
        self.worker_thread.start()

    def _worker_system_check(self, scan_settings: dict):
        def log(msg: str):
            self.msg_queue.put(("log", msg))

        try:
            info = collect_startup_status(scan_settings)
            self.msg_queue.put(("startup_info", info))

            model_id = scan_settings.get("model_id") or self.current_model_id()
            pref = scan_settings.get("preferred_device") or self.current_preferred_device()
            availability = inspect_model_availability(model_id, include_remote_meta=False)

            if availability.get("is_cached"):
                self.msg_queue.put(("status", "실행 가능한 장치 조합을 점검하는 중입니다..."))
                chosen = choose_runtime_device_and_type(model_id=model_id, preferred_device=pref, log=log)
                self.msg_queue.put(("runtime_choice", chosen))
            else:
                self.msg_queue.put(("runtime_choice_text", "선택 모델이 아직 로컬에 없어 실제 로딩 검증은 건너뛰었습니다. 모델 다운로드 후 전사 시작 시 자동 판정됩니다."))

            self.msg_queue.put(("status", "시스템 상태 점검이 완료되었습니다."))
        except Exception as exc:
            tb = traceback.format_exc()
            self.msg_queue.put(("log", f"시스템 상태 점검 실패\n{exc}\n{tb}"))
            self.msg_queue.put(("status", "시스템 상태 점검에 실패했습니다."))
        finally:
            self.msg_queue.put(("task_finished", "system_check"))

    def _apply_startup_info(self, info: dict):
        self.status_details = dict(info.get("details", {}))
        self.base_status_details = dict(info.get("details", {}))
        if info.get("live_resources"):
            self._update_live_resource_ui(info["live_resources"])
        for key in ["model", "engine", "torch", "device", "runtime"]:
            item = info.get(key, {})
            self._update_status_tile(key, item.get("level", "neutral"), item.get("summary", ""), item.get("meta", ""))

    def _apply_runtime_choice_cards(self, choice: dict):
        summary = f"{choice.get('device', '?')} / {choice.get('compute_type', '?')}"
        meta = choice.get("reason", "실행 조합 검증 성공")
        self._update_status_tile("runtime", "success", summary, meta)

    # -------------------------------------------------
    # Model download / confirmation
    # -------------------------------------------------
    def _request_model_download_permission(self, info: dict, reason: str) -> bool:
        ticket = {"info": info, "reason": reason, "approved": False, "event": threading.Event()}
        self.msg_queue.put(("ask_model_download", ticket))
        ticket["event"].wait()
        return bool(ticket.get("approved"))

    def _show_model_download_dialog(self, info: dict, reason: str) -> bool:
        dialog = ModelDownloadDialog(info, reason, self)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _ensure_model_ready(self, model_id: str, reason: str, log) -> dict:
        info = inspect_model_availability(model_id, include_remote_meta=True)
        self.msg_queue.put(("model_availability", info))
        if info.get("is_cached"):
            return info
        approved = self._request_model_download_permission(info, reason)
        if not approved:
            raise RuntimeError("MODEL_DOWNLOAD_CANCELLED")
        return info

    def start_model_download(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self._notify_task_busy("모델 다운로드")
            return

        model_id = self.current_model_id()
        self._set_status(f"모델 준비 상태를 확인하는 중입니다... ({model_id})")
        self._set_progress_value(0)
        self._set_transfer_texts(f"모델 다운로드 · {model_id}", "다운로드 준비를 마치는 중입니다.")
        self._begin_task("model_download", "모델 다운로드", cancellable=True)
        self.worker_thread = threading.Thread(target=self._worker_model_download, args=(model_id,), daemon=True)
        self.worker_thread.start()

    def _worker_model_download(self, model_id: str):
        def log(msg: str):
            self.msg_queue.put(("log", msg))

        def download_progress(payload: dict):
            self.msg_queue.put(("download_progress", payload))

        try:
            info = self._ensure_model_ready(
                model_id,
                "이 모델이 로컬에 없습니다. 다운로드 후 해당 모델을 사용하여 전사를 시작할 수 있습니다.",
                log,
            )
            if info.get("is_cached"):
                self.msg_queue.put(("progress", 100))
                self.msg_queue.put(("status", f"모델이 이미 준비되어 있습니다: {info['label']}"))
                self.msg_queue.put(("log", f"모델 준비 완료 상태: {info['label']}"))
                return

            self.msg_queue.put(("status", f"모델 다운로드를 시작합니다. ({info['label']})"))
            finished = download_model_to_cache(
                model_id,
                log=log,
                progress=download_progress,
                measured_mbps=self.last_measured_speed_mbps if self.last_measured_repo_id == info.get("repo_id", "") else None,
                cancel_event=self.cancel_event,
            )
            self.msg_queue.put(("model_availability", finished))
            self.msg_queue.put(("progress", 100))
            self.msg_queue.put(("status", f"모델 다운로드가 완료되었습니다: {info['label']}"))
            self.msg_queue.put(("log", f"모델 다운로드 완료: {info['label']}"))
        except Exception as exc:
            if str(exc) == "MODEL_DOWNLOAD_CANCELLED":
                self.msg_queue.put(("cancelled", "모델 다운로드를 취소했습니다."))
                return
            tb = traceback.format_exc()
            self.msg_queue.put(("log", f"모델 다운로드 실패\n{exc}\n{tb}"))
            self.msg_queue.put(("status", "모델 다운로드에 실패했습니다."))
        finally:
            self.msg_queue.put(("task_finished", "model_download"))

    # -------------------------------------------------
    # Transcription
    # -------------------------------------------------
    def start_transcription(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self._notify_task_busy("전사 작업")
            return

        input_paths = [path for path in self.input_files if os.path.isfile(path)]
        if not input_paths:
            QMessageBox.critical(self, "입력 파일 오류", "유효한 입력 파일을 하나 이상 선택하십시오.")
            return

        model_id = self.current_model_id()
        lang_code = self.current_lang_code()
        preset_id = self.current_preset_id()
        pref = self.current_preferred_device()
        audio_level = self.current_audio_enhance_level()
        output_formats = self.current_output_formats()

        self.output_path = None
        self.output_paths = {}
        self.batch_results = []
        self._set_progress_value(0)
        self.open_result_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        self._begin_task("transcription", "전사 작업", cancellable=True)
        self._set_transfer_texts("전사 준비 중", "모델 준비를 마치고 실행 장치를 결정하는 중입니다.")
        self._set_status("실행 준비 중입니다...")
        self._append_log("=" * 72)
        self._append_log(f"작업 시작 | files={len(input_paths)}")
        self._append_log(
            f"실행 설정 | lang={lang_code}, preset={preset_id}, model={model_id}, preferred_device={pref}, audio_enhance={audio_level}, outputs={output_formats}"
        )

        self.worker_thread = threading.Thread(
            target=self._worker_transcription,
            args=(input_paths, lang_code, model_id, preset_id, pref, audio_level, output_formats),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker_transcription(self, input_paths: list[str], lang_code: str, model_id: str, preset_id: str, pref: str, audio_level: str, output_formats: list[str]):
        def log(msg: str):
            self.msg_queue.put(("log", msg))

        try:
            self.msg_queue.put(("progress", 2))

            info = self._ensure_model_ready(
                model_id,
                "전사를 시작하기 위해 선택된 모델을 다운로드합니다. 다운로드가 끝나면 이어서 전사를 시작합니다.",
                log,
            )
            if not info.get("is_cached"):
                self.msg_queue.put(("status", f"선택 모델을 다운로드하는 중입니다... ({info['label']})"))
                download_model_to_cache(
                    model_id,
                    log=log,
                    progress=lambda payload: self.msg_queue.put(("download_progress", payload)),
                    measured_mbps=self.last_measured_speed_mbps if self.last_measured_repo_id == info.get("repo_id", "") else None,
                    cancel_event=self.cancel_event,
                )
                self.msg_queue.put(("model_availability", inspect_model_availability(model_id, include_remote_meta=False)))
                self.msg_queue.put(("progress", 10))

            self.msg_queue.put(("status", "실행 장치를 결정하는 중입니다..."))
            chosen = choose_runtime_device_and_type(model_id=model_id, preferred_device=pref, log=log)
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise RuntimeError("TRANSCRIPTION_CANCELLED")

            self.msg_queue.put(("progress", 12))
            self.msg_queue.put(("runtime_choice", chosen))
            self.msg_queue.put(("status", f"전사를 시작합니다... ({chosen['device']} / {chosen['compute_type']})"))
            self.msg_queue.put(("log", f"전사 실행 조합 확정: {chosen['device']} / {chosen['compute_type']}"))

            batch_results = []
            total = len(input_paths)
            for idx, in_path in enumerate(input_paths, start=1):
                if self.cancel_event is not None and self.cancel_event.is_set():
                    raise RuntimeError("TRANSCRIPTION_CANCELLED")
                self.msg_queue.put(("status", f"전사 중... ({idx}/{total}) {os.path.basename(in_path)}"))
                self.msg_queue.put(("batch_item_start", {"index": idx, "total": total, "path": in_path}))

                def progress(local_percent: float, index=idx):
                    base = 12.0
                    span = 88.0 / max(total, 1)
                    overall = base + ((index - 1) * span) + (max(0.0, min(100.0, float(local_percent))) / 100.0) * span
                    self.msg_queue.put(("progress", overall))

                result = run_transcription_job(
                    in_path=in_path,
                    lang_code=lang_code,
                    model_id=chosen["load_id"],
                    device=chosen["device"],
                    compute_type=chosen["compute_type"],
                    log=log,
                    progress=progress,
                    preset_id=preset_id,
                    audio_enhance_level=audio_level,
                    output_formats=output_formats,
                    cancel_event=self.cancel_event,
                )
                batch_item = {
                    "index": idx,
                    "total": total,
                    "input_path": in_path,
                    "primary_path": result["primary_path"],
                    "saved_paths": result["saved_paths"],
                    "effective_lang": result.get("effective_lang", lang_code),
                    "preprocess_info": result.get("preprocess_info"),
                }
                batch_results.append(batch_item)
                self.msg_queue.put(("batch_item_done", batch_item))

            self.msg_queue.put(("done", {"results": batch_results, "chosen": chosen, "model_id": model_id, "lang_code": lang_code, "preset_id": preset_id, "pref": pref}))
        except Exception as exc:
            self.msg_queue.put(("busy_off", None))
            if str(exc) == "MODEL_DOWNLOAD_CANCELLED":
                self.msg_queue.put(("cancelled", "전사를 시작하지 않았습니다. 모델 다운로드가 취소되었습니다."))
                return
            if str(exc) == "TRANSCRIPTION_CANCELLED":
                self.msg_queue.put(("cancelled", "전사를 취소했습니다."))
                return
            tb = traceback.format_exc()
            self.msg_queue.put(("error", f"{exc}\n\n{tb}"))

    # -------------------------------------------------
    # Result open
    # -------------------------------------------------
    def open_result(self):
        if self.output_path and os.path.isfile(self.output_path):
            open_path(self.output_path)

    def open_result_folder(self):
        if self.output_path:
            folder = os.path.dirname(self.output_path)
            if os.path.isdir(folder):
                open_path(folder)

    # -------------------------------------------------
    # Queue polling
    # -------------------------------------------------
    def _update_download_transfer_ui(self, payload: dict):
        percent = float(payload.get("percent") or 0.0)
        self._set_progress_value(percent)
        self._set_transfer_texts(
            f"다운로드 {percent:.0f}% · {payload.get('downloaded_text', '')} / {payload.get('total_text', '')}",
            payload.get("message", ""),
        )

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()

                if kind == "log":
                    self._append_log(payload)

                elif kind == "progress":
                    try:
                        percent = max(0.0, min(100.0, float(payload)))
                        self._set_progress_value(percent)
                        if self.current_task_kind == "transcription":
                            self.footer_meta_label.setText(f"전사 진행률 {percent:.0f}%")
                            self._refresh_job_clock()
                    except Exception:
                        pass

                elif kind == "busy_on":
                    self._progress_busy_on()

                elif kind == "busy_off":
                    self._progress_busy_off()

                elif kind == "status":
                    self._set_status(payload)

                elif kind == "startup_info":
                    self._apply_startup_info(payload)
                    self._set_status("시스템 상태 점검이 완료되었습니다.")

                elif kind == "model_availability":
                    self._apply_model_availability(payload)

                elif kind == "ask_model_download":
                    payload["approved"] = self._show_model_download_dialog(payload["info"], payload["reason"])
                    payload["event"].set()

                elif kind == "download_progress":
                    self._update_download_transfer_ui(payload)
                    self._refresh_job_clock()

                elif kind == "runtime_choice":
                    self._apply_runtime_choice_cards(payload)
                    if self.current_task_kind == "transcription":
                        self._set_transfer_texts(
                            f"전사 실행 조합 · {payload.get('device')} / {payload.get('compute_type')}",
                            "실행 조합 검증 완료, 첫 파일 전사를 준비하는 중입니다.",
                        )

                elif kind == "runtime_choice_text":
                    self._update_status_tile("runtime", "warning", payload, "자동 fallback이 필요할 수 있습니다.")

                elif kind == "task_finished":
                    if payload in {"model_download", "system_check"}:
                        self._finish_task(clear_transfer=False)

                elif kind == "batch_item_start":
                    self._append_log(f"[{payload['index']}/{payload['total']}] 처리 시작: {payload['path']}")
                    self._set_transfer_texts(
                        f"배치 {payload['index']}/{payload['total']} · {os.path.basename(payload['path'])}",
                        "현재 파일을 전사하는 중입니다.",
                    )

                elif kind == "batch_item_done":
                    preprocess_info = payload.get("preprocess_info")
                    preprocess_summary = self._format_preprocess_summary(preprocess_info)
                    if preprocess_summary:
                        self._append_log(f"[{payload['index']}/{payload['total']}] {preprocess_summary}")
                    saved = ", ".join(f"{fmt.upper()}={path}" for fmt, path in payload.get("saved_paths", {}).items())
                    self._append_log(f"[{payload['index']}/{payload['total']}] 저장 완료: {saved}")
                    self._update_preprocess_status(preprocess_info, payload.get("input_path"))
                    self.output_path = payload.get("primary_path")
                    self.output_paths = dict(payload.get("saved_paths", {}))
                    self.open_result_btn.setEnabled(True)
                    self.open_folder_btn.setEnabled(True)

                elif kind == "done":
                    results = payload.get("results", [])
                    chosen = payload.get("chosen", {})
                    model_id = payload.get("model_id", self.current_model_id())
                    lang_code = payload.get("lang_code", self.current_lang_code())
                    preset_id = payload.get("preset_id", self.current_preset_id())
                    pref = payload.get("pref", self.current_preferred_device())
                    self.batch_results = list(results)
                    if results:
                        self.output_path = results[-1].get("primary_path")
                        self.output_paths = dict(results[-1].get("saved_paths", {}))
                    self._set_progress_value(100)
                    self._set_status("작업이 완료되었습니다.")
                    self.open_result_btn.setEnabled(True)
                    self.open_folder_btn.setEnabled(True)
                    self._append_log(f"총 {len(results)}개 파일 처리 완료")
                    self._set_transfer_texts(f"전사 완료 · 총 {len(results)}개 파일", "전사와 저장이 완료되었습니다.")

                    self.settings["model_id"] = model_id
                    self.settings["language"] = lang_code
                    self.settings["preset_id"] = preset_id
                    self.settings["preferred_device"] = pref
                    self.settings["audio_enhance_level"] = self.current_audio_enhance_level()
                    self.settings["output_formats"] = self.current_output_formats()
                    self.settings["last_good_device"] = chosen.get("device", "")
                    self.settings["last_good_compute_type"] = chosen.get("compute_type", "")
                    save_settings(self.settings)
                    self._refresh_model_state_local()
                    self._finish_task(clear_transfer=False)
                    try:
                        clear_temp_work_dir(remove_root=False)
                    except Exception:
                        pass

                elif kind == "cancelled":
                    self._set_status(payload)
                    self._set_transfer_texts("작업 취소", payload)
                    self._append_log(payload)
                    self._finish_task(clear_transfer=False)
                    try:
                        clear_temp_work_dir(remove_root=False)
                    except Exception:
                        pass

                elif kind == "error":
                    self._set_status("오류가 발생했습니다.")
                    self._set_transfer_texts("작업 오류", "오류가 발생했습니다. 자세한 내용은 로그를 확인하십시오.")
                    self._append_log(payload)
                    self._finish_task(clear_transfer=False)
                    try:
                        clear_temp_work_dir(remove_root=False)
                    except Exception:
                        pass
                    QMessageBox.critical(self, "오류", payload)

        except queue.Empty:
            pass
