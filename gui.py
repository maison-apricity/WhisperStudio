# -*- coding: utf-8 -*-

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QDoubleSpinBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from config import (
    APP_NAME,
    DEFAULT_AUDIO_ENHANCE_LEVEL,
    DEFAULT_LANGUAGE,
    DEFAULT_OUTPUT_FORMATS,
    DEFAULT_PREFERRED_DEVICE,
    DEFAULT_PRESET_ID,
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
from font_runtime import bundled_font_files
from model_catalog import MODEL_CATALOG, default_model_id
from paths import bundled_icon_path, clear_temp_work_dir
from settings_manager import load_settings, save_settings
from subtitle_engine import probe_media_duration_seconds, run_transcription_job


LANGUAGE_LABELS = [
    ("auto", "자동 감지"),
    ("ko", "한국어"),
    ("en", "영어"),
    ("ja", "일본어"),
    ("zh", "중국어"),
    ("de", "독일어"),
    ("fr", "프랑스어"),
    ("es", "스페인어"),
]

PRESET_LABELS = {
    "auto-balanced": "균형",
    "lecture-meeting": "강의·회의",
    "dialogue-video": "대화·영상",
    "noisy-performance": "소음 많은 현장",
    "speed-priority": "속도 우선",
    "quality-priority": "정확도 우선",
}

DEVICE_LABELS = {"auto": "자동", "cuda": "GPU", "cpu": "CPU"}
AUDIO_LABELS = {"off": "끔", "standard": "표준", "strong": "강함"}


def compact_path_for_display(path: str, keep_tail: int = 3) -> str:
    if not path:
        return ""
    parts = list(Path(path).parts)
    if len(parts) <= keep_tail + 1:
        return str(Path(path))
    return str(Path("...", *parts[-keep_tail:]))


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
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


class CleanComboBox(QComboBox):
    def showPopup(self):
        view = self.view()
        if view is not None:
            popup = view.window()
            popup.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
            popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            popup.setStyleSheet("background:#1f2830; border:1px solid #546574;")
            view.viewport().setAutoFillBackground(True)
        super().showPopup()
        if view is not None:
            row_count = min(self.count(), self.maxVisibleItems())
            row_height = max(24, view.sizeHintForRow(0) if self.count() else 24)
            height = row_count * row_height + 6
            width = self.width()
            popup = view.window()
            popup.setFixedSize(width, height)
            view.setFixedSize(width, height)


class WindowBar(QWidget):
    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self.window = window
        self._drag_pos = None
        self.setObjectName("WindowBar")
        self.setFixedHeight(36)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addStretch(1)

        self.min_btn = QPushButton("−")
        self.close_btn = QPushButton("×")
        for btn in [self.min_btn, self.close_btn]:
            btn.setObjectName("WindowButton")
            btn.setFixedSize(32, 28)
            layout.addWidget(btn)
        self.min_btn.clicked.connect(self.window.showMinimized)
        self.close_btn.clicked.connect(self.window.close)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


class ModelDownloadDialog(QDialog):
    def __init__(self, info: dict, reason: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("모델 다운로드 확인")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("모델 다운로드가 필요합니다")
        title.setObjectName("DialogTitle")
        body = QLabel(reason)
        body.setWordWrap(True)
        desc = QLabel(str(info.get("long_note") or info.get("short_note") or "선택한 Whisper 모델을 로컬 캐시에 내려받습니다."))
        desc.setObjectName("MutedText")
        desc.setWordWrap(True)
        meta = QLabel(
            f"모델: {info.get('label', info.get('model_id', '알 수 없음'))}\n"
            f"저장 위치: {info.get('cache_root_display') or info.get('cached_path_display') or 'Hugging Face 캐시'}"
        )
        meta.setObjectName("MutedText")
        meta.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("다운로드")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(title)
        layout.addWidget(body)
        layout.addWidget(desc)
        layout.addWidget(meta)
        layout.addWidget(buttons)


class DownloadProgressDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("모델 다운로드")
        self.resize(560, 190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        self.title = QLabel("모델 다운로드 준비 중")
        self.title.setObjectName("DialogTitle")
        self.message = QLabel("다운로드 정보를 확인하고 있습니다.")
        self.message.setObjectName("MutedText")
        self.message.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(34)
        self.speed = QLabel("")
        self.speed.setObjectName("MutedText")
        layout.addWidget(self.title)
        layout.addWidget(self.message)
        layout.addWidget(self.progress)
        layout.addWidget(self.speed)

    def update_progress(self, payload: dict):
        percent = int(max(0, min(100, float(payload.get("percent") or 0))))
        self.progress.setValue(percent)
        self.title.setText(f"모델 다운로드 {percent}%")
        self.message.setText(payload.get("message", "") or "모델 파일을 내려받는 중입니다.")
        parts = []
        if payload.get("downloaded_text") or payload.get("total_text"):
            parts.append(f"{payload.get('downloaded_text', '')} / {payload.get('total_text', '')}".strip(" /"))
        if payload.get("speed_text"):
            parts.append(str(payload.get("speed_text")))
        if payload.get("eta_text"):
            parts.append(f"예상 남은 시간 {payload.get('eta_text')}")
        self.speed.setText(" · ".join(part for part in parts if part))


class SystemDetailsDialog(QDialog):
    def __init__(self, title: str, sections: list[dict] | str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("DialogTitle")
        layout.addWidget(heading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)

        if isinstance(sections, str):
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText(sections)
            body_layout.addWidget(text)
        else:
            for section in sections:
                body_layout.addWidget(self._section_card(section.get("title", ""), section.get("rows", [])))
            body_layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        buttons.rejected.connect(self.reject)

        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)

    def _section_card(self, title: str, rows: list[tuple[str, str]]) -> QWidget:
        card = QFrame()
        card.setObjectName("InlinePanel")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(7)
        for idx, (name, value) in enumerate(rows):
            key = QLabel(name)
            key.setObjectName("SideLabel")
            val = QLabel(str(value or "-"))
            val.setObjectName("ValueLabel")
            val.setWordWrap(True)
            grid.addWidget(key, idx, 0)
            grid.addWidget(val, idx, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return card


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
        self.last_measured_speed_mbps = None
        self.last_measured_repo_id = ""
        self.cancel_event = None
        self.current_task_kind = ""
        self.current_task_label = ""
        self.current_progress_percent = 0.0
        self.job_started_at = None
        self.download_progress_dialog = None
        self.loaded_font_families = []
        self._loading_settings = False

        self.setWindowTitle(APP_NAME)
        self.resize(1360, 860)
        self.setMinimumSize(1180, 760)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)

        icon_path = bundled_icon_path()
        if icon_path and os.path.isfile(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.auto_check_timer = QTimer(self)
        self.auto_check_timer.setSingleShot(True)
        self.auto_check_timer.timeout.connect(self.start_system_check)

        self._init_fonts()
        self._build_ui()
        self._apply_styles()
        self._normalize_control_sizes()
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

    def mainloop(self):
        self.show()
        return self.qt_app.exec()

    def closeEvent(self, event):
        if self.cancel_event is not None:
            self.cancel_event.set()
        super().closeEvent(event)

    def _init_fonts(self):
        db = QFontDatabase()
        for font_path in bundled_font_files():
            font_id = QFontDatabase.addApplicationFont(font_path)
            if font_id >= 0:
                self.loaded_font_families.extend(QFontDatabase.applicationFontFamilies(font_id))

        families = set(QFontDatabase.families())
        families.update(self.loaded_font_families)

        def pick(candidates: list[str], fallback: str) -> str:
            for name in candidates:
                if name in families:
                    return name
            return fallback

        self.ui_font_family = pick(
            ["Pretendard", "Pretendard Variable", "SUIT", "Noto Sans KR", "Malgun Gothic", "Segoe UI"],
            self.qt_app.font().family() or "Segoe UI",
        )
        self.code_font_family = pick(["Cascadia Code", "JetBrains Mono", "D2Coding", "Consolas"], self.ui_font_family)
        app_font = QFont(self.ui_font_family)
        app_font.setPointSizeF(11.5)
        app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
        try:
            app_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        except Exception:
            pass
        self.qt_app.setFont(app_font)

    def _apply_styles(self):
        self.setStyleSheet(
            f"""
            QWidget {{
                font-family: "{self.ui_font_family}";
                color: #eef3f7;
                background: #20262d;
            }}
            QWidget#WindowBar {{
                background: transparent;
            }}
            QLabel {{
                background: transparent;
            }}
            QLabel#AppTitle {{
                font-size: 28px;
                font-weight: 700;
                color: #f7fafc;
            }}
            QLabel#AppSubtitle, QLabel#MutedText {{
                color: #aebbc6;
                font-size: 13px;
            }}
            QLabel#SideLabel {{
                color: #aebbc6;
                font-size: 13px;
                font-weight: 600;
            }}
            QLabel#SideValue {{
                color: #f7fafc;
                font-size: 22px;
                font-weight: 700;
            }}
            QLabel#ResourceChip {{
                background: #26313a;
                border: 1px solid #3d4a56;
                border-radius: 6px;
                padding: 5px 10px;
                color: #eef3f7;
                font-size: 13px;
                font-weight: 700;
            }}
            QLabel#MeterLabel {{
                color: #e8eef3;
                font-size: 13px;
                font-weight: 700;
            }}
            QLabel#SectionTitle {{
                font-size: 18px;
                font-weight: 700;
                color: #f7fafc;
            }}
            QLabel#DialogTitle {{
                font-size: 20px;
                font-weight: 700;
            }}
            QLabel#StatusTitle {{
                font-size: 20px;
                font-weight: 700;
                color: #f7fafc;
            }}
            QLabel#FormLabel {{
                color: #d5dee7;
                font-size: 14px;
                font-weight: 700;
            }}
            QLabel#ValueLabel {{
                color: #f7fafc;
                font-size: 15px;
                font-weight: 700;
            }}
            QWidget#Transparent {{
                background: transparent;
            }}
            QFrame#HeroPanel, QFrame#Card {{
                background: #29323b;
                border: 1px solid #40505d;
                border-radius: 8px;
            }}
            QFrame#HeroPanel {{
                border-left: 4px solid #4fa3c7;
            }}
            QFrame#SideGroup {{
                background: #26313a;
                border: 1px solid #40505d;
                border-radius: 6px;
            }}
            QFrame#BottomBar {{
                background: #29323b;
                border: 1px solid #40505d;
                border-radius: 8px;
            }}
            QFrame#InlinePanel {{
                background: #242d35;
                border: 1px solid #3b4854;
                border-radius: 6px;
            }}
            QScrollArea, QScrollArea > QWidget > QWidget {{
                background: transparent;
                border: none;
            }}
            QComboBox {{
                color: #f7fafc;
                background: #1f2830;
                border: 1px solid #546574;
                border-radius: 6px;
                padding: 7px 32px 7px 11px;
                min-height: 20px;
                font-size: 14px;
            }}
            QComboBox:hover {{
                border-color: #4fa3c7;
            }}
            QComboBox:focus {{
                border: 1px solid #67b8d7;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 26px;
                border-left: 1px solid #40505d;
                background: #26313a;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            QComboBox QAbstractItemView {{
                color: #f7fafc;
                background: #1f2830;
                border: 1px solid #546574;
                selection-background-color: #2f6f91;
                selection-color: #ffffff;
                outline: 0;
            }}
            QLineEdit {{
                color: #f7fafc;
                background: #1f2830;
                border: 1px solid #546574;
                border-radius: 6px;
                padding: 7px 11px;
                min-height: 20px;
                font-size: 14px;
                selection-background-color: #2f6f91;
            }}
            QLineEdit:disabled {{
                color: #84919d;
                background: #26313a;
                border-color: #36434e;
            }}
            QSpinBox, QDoubleSpinBox {{
                color: #f7fafc;
                background: #1f2830;
                border: 1px solid #546574;
                border-radius: 6px;
                padding: 5px 8px;
                min-height: 20px;
                font-size: 14px;
                selection-background-color: #2f6f91;
            }}
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                width: 14px;
                background: #26313a;
                border-left: 1px solid #40505d;
            }}
            QListWidget, QPlainTextEdit {{
                color: #eef3f7;
                background: #1f2830;
                border: 1px solid #40505d;
                border-radius: 6px;
                selection-background-color: #2f6f91;
                selection-color: #ffffff;
                font-size: 14px;
            }}
            QListWidget::item {{
                padding: 8px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background: #2f6f91;
            }}
            QPushButton {{
                color: #eef3f7;
                background: #26313a;
                border: 1px solid #546574;
                border-radius: 6px;
                padding: 8px 13px;
                font-weight: 700;
                min-height: 22px;
                font-size: 14px;
            }}
            QPushButton#CompactButton {{
                padding: 0px 10px;
                min-height: 18px;
            }}
            QPushButton:hover {{
                background: #303b45;
                border-color: #4fa3c7;
            }}
            QPushButton:disabled {{
                color: #7e8a94;
                background: #26313a;
                border-color: #36434e;
            }}
            QPushButton#PrimaryButton {{
                color: #ffffff;
                background: #347fa6;
                border-color: #4fa3c7;
            }}
            QPushButton#PrimaryButton:hover {{
                background: #4092bd;
            }}
            QPushButton#DangerButton {{
                color: #f4f7fa;
                background: #34414d;
                border-color: #627381;
            }}
            QPushButton#BottomPrimary {{
                color: #ffffff;
                background: #347fa6;
                border: 1px solid #4fa3c7;
                border-radius: 6px;
                padding: 0px;
                min-height: 0px;
                font-weight: 700;
            }}
            QPushButton#BottomDanger {{
                color: #f4f7fa;
                background: #34414d;
                border: 1px solid #627381;
                border-radius: 6px;
                padding: 0px;
                min-height: 0px;
                font-weight: 700;
            }}
            QPushButton#BottomButton {{
                color: #eef3f7;
                background: #26313a;
                border: 1px solid #546574;
                border-radius: 6px;
                padding: 0px;
                min-height: 0px;
                font-weight: 700;
            }}
            QPushButton#ChipButton {{
                min-width: 64px;
            }}
            QPushButton#ChipButton:checked {{
                color: #ffffff;
                background: #347fa6;
                border-color: #4fa3c7;
            }}
            QPushButton#WindowButton {{
                font-size: 14px;
                font-weight: 700;
                min-height: 0px;
                padding: 0px;
                border-radius: 6px;
                background: transparent;
                border: 1px solid transparent;
                color: #d5dee7;
            }}
            QPushButton#WindowButton:hover {{
                background: #303b45;
                border-color: #546574;
            }}
            QCheckBox {{
                background: transparent;
                spacing: 8px;
                font-weight: 700;
                font-size: 14px;
            }}
            QCheckBox::indicator {{
                width: 17px;
                height: 17px;
                border-radius: 4px;
                border: 1px solid #546574;
                background: #1f2830;
            }}
            QCheckBox::indicator:checked {{
                background: #6f8799;
                border-color: #8aa1b1;
            }}
            QProgressBar {{
                background: #1f2830;
                border: 1px solid #546574;
                border-radius: 6px;
                min-height: 22px;
                text-align: center;
                font-weight: 700;
                color: #f7fafc;
                font-size: 14px;
            }}
            QProgressBar::chunk {{
                background: #4fa3c7;
                border-radius: 5px;
            }}
            QProgressBar#ResourceMeter {{
                background: #202830;
                border: 1px solid #435360;
                min-height: 22px;
            }}
            QProgressBar#ResourceMeter::chunk {{
                background: #6f96ac;
                border-radius: 5px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 12px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: #b7c4d4;
                border-radius: 6px;
                min-height: 28px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                height: 0px;
                background: transparent;
                border: none;
            }}
            """
        )

    def _build_ui(self):
        root_widget = QWidget()
        self.setCentralWidget(root_widget)

        root = QVBoxLayout(root_widget)
        root.setContentsMargins(24, 8, 24, 20)
        root.setSpacing(12)

        self.window_bar = WindowBar(self)
        root.addWidget(self.window_bar)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Whisper Studio")
        title.setObjectName("AppTitle")
        subtitle = QLabel("오디오와 영상 파일을 전사하고 자막 파일로 저장합니다.")
        subtitle.setObjectName("AppSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        root.addLayout(header)

        hero = QGridLayout()
        hero.setHorizontalSpacing(12)
        hero.setVerticalSpacing(12)
        hero.setColumnStretch(0, 1)
        hero.setColumnStretch(1, 1)
        hero.setColumnStretch(2, 1)
        self.quick_lang_value = self._hero_metric("언어", "-")
        self.quick_model_value = self._hero_metric("모델", "-")
        self.quick_preset_value = self._hero_metric("프리셋", "-")
        hero.addWidget(self.quick_lang_value.parentWidget(), 0, 0)
        hero.addWidget(self.quick_model_value.parentWidget(), 0, 1)
        hero.addWidget(self.quick_preset_value.parentWidget(), 0, 2)
        root.addLayout(hero)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        content = QWidget()
        scroll.setWidget(content)
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        grid.setColumnStretch(0, 5)
        grid.setColumnStretch(1, 3)

        right_stack = QWidget()
        right_layout = QVBoxLayout(right_stack)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        right_layout.addWidget(self._build_settings_card())
        right_layout.addWidget(self._build_system_card())
        right_layout.addWidget(self._build_options_card())

        grid.addWidget(self._build_input_card(), 0, 0)
        grid.addWidget(right_stack, 0, 1)
        grid.addWidget(self._build_log_card(), 1, 0, 1, 2)

        root.addWidget(self._build_bottom_bar())

    def _normalize_control_sizes(self):
        for combo in [self.language_combo, self.model_combo, self.preset_combo]:
            combo.setFixedHeight(40)
        for btn in [self.settings_download_model_btn, self.output_dir_btn]:
            btn.setFixedHeight(40)
        self.output_dir_edit.setFixedHeight(40)
        for btn in [self.start_btn, self.cancel_btn, self.open_result_btn, self.open_folder_btn]:
            btn.setMinimumSize(110, 40)
            btn.setMaximumHeight(40)
        if hasattr(self, "resource_refresh_btn"):
            self.resource_refresh_btn.setMinimumSize(96, 34)
            self.resource_refresh_btn.setMaximumHeight(34)
        if hasattr(self, "resource_state_btn"):
            self.resource_state_btn.setMinimumSize(86, 34)
            self.resource_state_btn.setMaximumHeight(34)
        if hasattr(self, "system_details_btn"):
            self.system_details_btn.setMinimumSize(66, 34)
            self.system_details_btn.setMaximumHeight(34)

    def _build_bottom_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("BottomBar")
        layout = QGridLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(6)
        layout.setColumnStretch(0, 3)
        layout.setColumnStretch(1, 2)

        self.footer_status_label = QLabel("준비됨")
        self.footer_status_label.setObjectName("StatusTitle")
        self.footer_transfer_label = QLabel("파일을 선택하면 시작할 수 있습니다.")
        self.footer_transfer_label.setObjectName("MutedText")
        self.activity_preprocess_label = QLabel("")
        self.activity_preprocess_label.setObjectName("MutedText")
        self.activity_preprocess_label.setVisible(False)
        for label in [self.footer_status_label, self.footer_transfer_label, self.activity_preprocess_label]:
            label.setVisible(False)

        progress_box = QVBoxLayout()
        progress_box.setSpacing(4)
        self.footer_meta_label = QLabel("")
        self.footer_meta_label.setObjectName("MutedText")
        self.footer_meta_label.setVisible(False)
        self.footer_progress = QProgressBar()
        self.footer_progress.setRange(0, 100)
        self.footer_progress.setValue(0)
        self.footer_progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.footer_progress.setFixedHeight(34)
        self.activity_progress = self.footer_progress
        progress_box.addWidget(self.footer_progress)
        layout.addLayout(progress_box, 0, 0, 1, 2)

        resource_line = QWidget()
        resource_row = QHBoxLayout(resource_line)
        resource_row.setContentsMargins(0, 0, 0, 0)
        resource_row.setSpacing(8)
        self.cpu_meter = self._resource_meter("CPU")
        self.ram_meter = self._resource_meter("RAM")
        self.vram_meter = self._resource_meter("VRAM")
        for meter in [self.cpu_meter, self.ram_meter, self.vram_meter]:
            resource_row.addWidget(meter, 1)
        self.resource_state_btn = self._make_button("상태 확인 중", self.show_system_details)
        self.resource_state_btn.setObjectName("CompactButton")
        self.resource_state_btn.setMinimumSize(86, 34)
        self.resource_refresh_btn = self._make_button("자원 갱신", self.refresh_live_resource_now)
        self.resource_refresh_btn.setObjectName("CompactButton")
        self.resource_refresh_btn.setMinimumSize(96, 34)
        self.system_details_btn = self._make_button("상세", self.show_system_details)
        self.system_details_btn.setObjectName("CompactButton")
        self.system_details_btn.setMinimumSize(66, 34)
        resource_row.addWidget(self.resource_state_btn)
        resource_row.addWidget(self.resource_refresh_btn)
        resource_row.addWidget(self.system_details_btn)
        layout.addWidget(resource_line, 1, 0, 1, 2)

        self.start_btn = self._make_button("전사 시작", self.start_transcription, primary=True)
        self.cancel_btn = self._make_button("취소", self._cancel_current_task, danger=True)
        self.open_result_btn = self._make_button("결과 열기", self.open_result)
        self.open_folder_btn = self._make_button("폴더 열기", self.open_result_folder)
        self.start_btn.setObjectName("BottomPrimary")
        self.cancel_btn.setObjectName("BottomDanger")
        self.open_result_btn.setObjectName("BottomButton")
        self.open_folder_btn.setObjectName("BottomButton")
        self.cancel_btn.setEnabled(False)
        self.open_result_btn.setEnabled(False)
        self.open_folder_btn.setEnabled(False)
        self.home_start_btn = self.start_btn
        actions = QHBoxLayout()
        actions.setSpacing(8)
        for btn in [self.start_btn, self.cancel_btn, self.open_result_btn, self.open_folder_btn]:
            btn.setMinimumSize(110, 40)
            btn.setMaximumHeight(40)
            actions.addWidget(btn)
        layout.addLayout(actions, 0, 2, 2, 1)

        self.activity_status_label = self.footer_status_label
        self.activity_status_meta_label = self.footer_transfer_label
        self.activity_job_meta_label = self.footer_meta_label
        return bar

    def _small_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FormLabel")
        return label

    def _sidebar_value(self, layout: QVBoxLayout, label_text: str) -> QLabel:
        label = QLabel(label_text)
        label.setObjectName("SideLabel")
        value = QLabel("-")
        value.setObjectName("SideValue")
        value.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value)
        return value

    def _hero_metric(self, label_text: str, value_text: str) -> QLabel:
        panel = QFrame()
        panel.setObjectName("HeroPanel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(5)
        label = QLabel(label_text)
        label.setObjectName("SideLabel")
        value = QLabel(value_text)
        value.setObjectName("SideValue")
        value.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value)
        value._metric_panel = panel
        return value

    def _mini_value(self, label_text: str, value_text: str) -> QLabel:
        panel = QFrame()
        panel.setObjectName("InlinePanel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        label = QLabel(label_text)
        label.setObjectName("SideLabel")
        value = QLabel(value_text)
        value.setObjectName("ValueLabel")
        value.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value)
        value._metric_panel = panel
        return value

    def _resource_meter(self, name: str) -> QProgressBar:
        meter = QProgressBar()
        meter.setObjectName("ResourceMeter")
        meter.setRange(0, 100)
        meter.setValue(0)
        meter.setFormat(f"{name} --")
        meter.setTextVisible(True)
        meter.setFixedHeight(28)
        return meter

    def _build_input_card(self) -> QWidget:
        card, layout = self._card("입력 파일", "")

        row = QHBoxLayout()
        row.setSpacing(8)
        self.pick_file_btn = self._make_button("파일 선택", self.browse_input_files, primary=True)
        self.add_file_btn = self._make_button("파일 추가", self.add_more_input_files)
        self.remove_file_btn = self._make_button("선택 제거", self.remove_selected_input_files)
        self.clear_file_btn = self._make_button("목록 비우기", self.clear_input_files, danger=True)
        for btn in [self.pick_file_btn, self.add_file_btn, self.remove_file_btn, self.clear_file_btn]:
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.file_summary_label = QLabel("선택된 파일이 없습니다.")
        self.file_summary_label.setObjectName("MutedText")
        self.file_summary_label.setWordWrap(True)
        self.file_summary_label.setVisible(False)

        output_panel = QFrame()
        output_panel.setObjectName("InlinePanel")
        output_panel.setMinimumHeight(48)
        output_layout = QGridLayout(output_panel)
        output_layout.setContentsMargins(10, 7, 10, 7)
        output_layout.setHorizontalSpacing(8)
        output_layout.setVerticalSpacing(4)
        self.use_source_folder_check = QCheckBox("원본 폴더에 저장")
        self.use_source_folder_check.setChecked(True)
        self.use_source_folder_check.stateChanged.connect(self._on_output_folder_mode_changed)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("출력 폴더를 따로 지정할 때만 사용")
        self.output_dir_edit.setEnabled(False)
        self.output_dir_edit.textChanged.connect(self._on_output_dir_text_changed)
        self.output_dir_btn = self._make_button("폴더 선택", self.browse_output_dir)
        self.output_dir_btn.setObjectName("CompactButton")
        self.output_dir_btn.setEnabled(False)
        for widget in [self.output_dir_edit, self.output_dir_btn]:
            policy = widget.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            widget.setSizePolicy(policy)
        output_layout.addWidget(self.use_source_folder_check, 0, 0)
        output_layout.addWidget(self.output_dir_edit, 0, 1)
        output_layout.addWidget(self.output_dir_btn, 0, 2)
        self.output_dir_edit.setFixedHeight(40)
        self.output_dir_btn.setFixedHeight(40)
        layout.addWidget(output_panel)

        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(230)
        self.file_list.itemDoubleClicked.connect(lambda _item: self.remove_selected_input_files())
        layout.addWidget(self.file_list)
        return card

    def _build_settings_card(self) -> QWidget:
        card, layout = self._card("전사 설정", "")

        self.language_combo = CleanComboBox()
        for code, name in LANGUAGE_LABELS:
            self.language_combo.addItem(f"{name} · {code}", code)
        self._configure_combo(self.language_combo, 16)
        self.language_combo.currentIndexChanged.connect(self._refresh_selection_hints)

        self.preset_combo = CleanComboBox()
        for preset in TRANSCRIPTION_PRESETS:
            preset_id = preset["id"]
            self.preset_combo.addItem(PRESET_LABELS.get(preset_id, preset_id), preset_id)
        self._configure_combo(self.preset_combo, 16)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(10)
        top_grid.setVerticalSpacing(6)
        top_grid.addWidget(self._small_label("언어"), 0, 0)
        top_grid.addWidget(self._small_label("프리셋"), 0, 1)
        top_grid.addWidget(self.language_combo, 1, 0)
        top_grid.addWidget(self.preset_combo, 1, 1)
        layout.addLayout(top_grid)

        self.preset_detail_label = QLabel("")
        self.preset_detail_label.setObjectName("MutedText")
        self.preset_detail_label.setWordWrap(True)
        layout.addWidget(self.preset_detail_label)

        preset_controls = QFrame()
        preset_controls.setObjectName("InlinePanel")
        preset_grid = QGridLayout(preset_controls)
        preset_grid.setContentsMargins(10, 8, 10, 8)
        preset_grid.setHorizontalSpacing(8)
        preset_grid.setVerticalSpacing(6)
        self.custom_beam_spin = QSpinBox()
        self.custom_beam_spin.setRange(1, 8)
        self.custom_vad_speech_spin = QSpinBox()
        self.custom_vad_speech_spin.setRange(80, 800)
        self.custom_vad_speech_spin.setSingleStep(10)
        self.custom_vad_silence_spin = QSpinBox()
        self.custom_vad_silence_spin.setRange(200, 2500)
        self.custom_vad_silence_spin.setSingleStep(50)
        self.custom_repetition_spin = QDoubleSpinBox()
        self.custom_repetition_spin.setRange(1.0, 1.3)
        self.custom_repetition_spin.setSingleStep(0.01)
        self.custom_repetition_spin.setDecimals(2)
        for col, (label_text, widget) in enumerate(
            [
                ("Beam", self.custom_beam_spin),
                ("말소리", self.custom_vad_speech_spin),
                ("무음", self.custom_vad_silence_spin),
                ("반복 억제", self.custom_repetition_spin),
            ]
        ):
            preset_grid.addWidget(self._small_label(label_text), 0, col)
            preset_grid.addWidget(widget, 1, col)
            widget.setFixedHeight(32)
            widget.valueChanged.connect(self._on_custom_preset_changed)
        layout.addWidget(preset_controls)

        self.model_combo = CleanComboBox()
        for entry in MODEL_CATALOG:
            self.model_combo.addItem(entry["label"], entry["id"])
        self._configure_combo(self.model_combo, 18)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)

        model_row = QHBoxLayout()
        model_row.setSpacing(10)
        model_row.addWidget(self.model_combo, 1)
        self.settings_download_model_btn = self._make_button("모델 다운로드", self.start_model_download)
        self.settings_download_model_btn.setMinimumWidth(132)
        model_row.addWidget(self.settings_download_model_btn)
        model_wrap = QWidget()
        model_wrap.setObjectName("Transparent")
        model_wrap.setLayout(model_row)
        self.settings_download_model_btn.setFixedHeight(40)
        self._form_row(layout, "모델", model_wrap)

        model_state = QFrame()
        model_state.setObjectName("Transparent")
        self.model_state_panel = model_state
        model_state_layout = QHBoxLayout(model_state)
        model_state_layout.setContentsMargins(10, 8, 10, 8)
        model_state_layout.setSpacing(10)
        self.model_state_badge = QLabel("확인 중")
        self.model_status_summary_label = QLabel("")
        self.model_status_summary_label.setObjectName("MutedText")
        self.model_status_summary_label.setWordWrap(True)
        self.model_cache_path_label = QLabel("")
        self.model_cache_path_label.setObjectName("MutedText")
        self.model_cache_path_label.setWordWrap(True)
        model_text = QVBoxLayout()
        model_text.setSpacing(2)
        model_text.addWidget(self.model_cache_path_label)
        self.model_detail_btn = self._make_button("위치", self.toggle_model_details)
        self.model_detail_btn.setObjectName("CompactButton")
        self.model_detail_btn.setFixedSize(64, 34)
        model_state_layout.addWidget(self.model_state_badge)
        model_state_layout.addLayout(model_text, 1)
        model_state_layout.addWidget(self.model_detail_btn)
        layout.addWidget(model_state)
        self.model_cache_path_label.setVisible(False)

        self.download_model_btn = self.settings_download_model_btn

        return card

    def _build_options_card(self) -> QWidget:
        card, layout = self._card("실행 옵션", "")

        self.quick_device_value = QLabel("")
        self.quick_audio_value = QLabel("")
        self.quick_output_value = QLabel("")

        self.device_buttons = {}
        self.device_group = QButtonGroup(self)
        self.device_group.setExclusive(True)
        device_row = QHBoxLayout()
        device_row.setSpacing(8)
        for text, value in [("자동", "auto"), ("GPU", "cuda"), ("CPU", "cpu")]:
            btn = self._make_chip(text, lambda checked=False, v=value: self._set_preferred_device(v))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.device_buttons[value] = btn
            self.device_group.addButton(btn)
            device_row.addWidget(btn)
        layout.addWidget(self._small_label("장치 선호"))
        layout.addLayout(device_row)

        self.audio_buttons = {}
        self.audio_group = QButtonGroup(self)
        self.audio_group.setExclusive(True)
        audio_row = QHBoxLayout()
        audio_row.setSpacing(8)
        for text, value in [("끔", "off"), ("표준", "standard"), ("강함", "strong")]:
            btn = self._make_chip(text, lambda checked=False, v=value: self._set_audio_enhance_level(v))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.audio_buttons[value] = btn
            self.audio_group.addButton(btn)
            audio_row.addWidget(btn)
        layout.addWidget(self._small_label("음성 보정"))
        layout.addLayout(audio_row)

        output_panel = QFrame()
        output_panel.setObjectName("InlinePanel")
        output_row = QHBoxLayout(output_panel)
        output_row.setContentsMargins(10, 8, 10, 8)
        output_row.setSpacing(18)
        self.output_fmt_srt = QCheckBox("SRT")
        self.output_fmt_txt = QCheckBox("TXT")
        self.output_fmt_vtt = QCheckBox("VTT")
        for cb in [self.output_fmt_srt, self.output_fmt_txt, self.output_fmt_vtt]:
            cb.stateChanged.connect(self._refresh_selection_hints)
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            output_row.addWidget(cb)
        layout.addWidget(self._small_label("출력 형식"))
        layout.addWidget(output_panel)

        self.device_note_label = QLabel("")
        self.device_note_label.setObjectName("MutedText")
        self.device_note_label.setWordWrap(True)
        self.device_note_label.setVisible(False)

        self.options_health_badge = QLabel("대기")
        self.options_health_title = QLabel("환경 점검 대기")
        self.options_health_detail = QLabel("모델과 장치 설정을 바꾸면 실행 환경을 자동으로 점검합니다.")
        for hidden in [self.options_health_badge, self.options_health_title, self.options_health_detail]:
            hidden.setVisible(False)

        return card

    def _build_system_card(self) -> QWidget:
        card, layout = self._card("전사 환경", "")
        self.system_overview_title = QLabel("점검 대기")
        self.system_overview_title.setObjectName("StatusTitle")
        self.system_overview_meta = QLabel("모델 · 엔진 · 장치 · 런타임")
        self.system_overview_meta.setObjectName("MutedText")
        self.system_overview_meta.setWordWrap(True)
        layout.addWidget(self.system_overview_title)
        layout.addWidget(self.system_overview_meta)

        check_row = QHBoxLayout()
        check_row.setSpacing(8)
        self.system_check_btn = self._make_button("시스템 점검", self.start_system_check, primary=True)
        self.system_check_btn.setFixedHeight(38)
        check_row.addWidget(self.system_check_btn)
        self.system_check_hint = QLabel("모델, 엔진, GPU/CPU, 런타임 조합을 확인합니다.")
        self.system_check_hint.setObjectName("MutedText")
        self.system_check_hint.setWordWrap(True)
        check_row.addWidget(self.system_check_hint, 1)
        layout.addLayout(check_row)

        self.system_check_steps = QLabel("대기")
        self.system_check_steps.setObjectName("MutedText")
        self.system_check_steps.setWordWrap(True)
        self.system_check_steps.setVisible(False)
        layout.addWidget(self.system_check_steps)

        self.status_tiles = {}
        for key, label_text in [("model", "모델"), ("engine", "엔진"), ("torch", "PyTorch"), ("device", "장치"), ("runtime", "런타임")]:
            title = QLabel(label_text)
            title.setObjectName("FormLabel")
            title.setFixedWidth(56)
            badge = QLabel("대기")
            badge.setFixedWidth(52)
            summary = QLabel("상태를 확인하는 중입니다.")
            summary.setObjectName("MutedText")
            summary.setWordWrap(True)
            title.setVisible(False)
            badge.setVisible(False)
            summary.setVisible(False)
            self.status_tiles[key] = {"badge": badge, "summary": summary, "meta": QLabel(""), "level": "neutral"}
        return card

    def _build_log_card(self) -> QWidget:
        card, layout = self._card("작업 로그", "")
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(180)
        self.log_text.setFont(QFont(self.code_font_family, 10))
        layout.addWidget(self.log_text)
        return card

    def _card(self, title: str, subtitle: str) -> tuple[Card, QVBoxLayout]:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(9)
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("MutedText")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)
        return card, layout

    def _configure_combo(self, combo: QComboBox, min_chars: int):
        combo.setMaxVisibleItems(5)
        combo.setMinimumContentsLength(min_chars)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setFixedHeight(40)
        view = QListView(combo)
        view.setUniformItemSizes(True)
        view.setSpacing(0)
        view.setFrameShape(QFrame.Shape.NoFrame)
        view.setMinimumWidth(220)
        view.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
        view.viewport().setAutoFillBackground(True)
        view.setStyleSheet(
            """
            QListView {
                color: #f7fafc;
                background: #1f2830;
                border: none;
                border-radius: 0px;
                padding: 2px;
                outline: 0;
                selection-background-color: #2f6f91;
                selection-color: #ffffff;
            }
            QListView::item {
                min-height: 24px;
                padding: 3px 10px;
                background: #1f2830;
            }
            QListView::item:hover, QListView::item:selected {
                background: #2f6f91;
                color: #ffffff;
            }
            """
        )
        combo.setView(view)

    def _make_button(self, text: str, slot, primary: bool = False, danger: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        if primary:
            btn.setObjectName("PrimaryButton")
        elif danger:
            btn.setObjectName("DangerButton")
        return btn

    def _make_chip(self, text: str, slot) -> QPushButton:
        btn = self._make_button(text, slot)
        btn.setObjectName("ChipButton")
        btn.setCheckable(True)
        return btn

    def _form_row(self, layout: QVBoxLayout, label: str, widget: QWidget):
        label_widget = self._small_label(label)
        layout.addWidget(label_widget)
        layout.addWidget(widget)

    def _set_badge(self, label: QLabel, level: str, text: str):
        palette = {
            "success": ("#213a32", "#c9f0dc", "#3e765d"),
            "warning": ("#3e3828", "#ffe6ad", "#8e7635"),
            "danger": ("#4a2829", "#ffdeda", "#9e3a36"),
            "info": ("#26384a", "#cfe5f8", "#496f91"),
            "neutral": ("#303b45", "#d5dee7", "#546574"),
        }
        bg, fg, border = palette.get((level or "neutral").lower(), palette["neutral"])
        label.setText(text)
        label.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {border}; border-radius:5px; padding:4px 8px; font-weight:800;"
        )

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

    def current_output_dir(self) -> str | None:
        if self.use_source_folder_check.isChecked():
            return None
        path = self.output_dir_edit.text().strip()
        return path or None

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
        self._schedule_system_check()

    def _set_audio_enhance_level(self, value: str):
        value = value if value in self.audio_buttons else DEFAULT_AUDIO_ENHANCE_LEVEL
        for key, btn in self.audio_buttons.items():
            btn.setChecked(key == value)
        self._refresh_selection_hints()

    def toggle_model_details(self):
        visible = not self.model_cache_path_label.isVisible()
        self.model_cache_path_label.setVisible(visible)
        self.model_detail_btn.setText("숨김" if visible else "위치")

    def _on_output_folder_mode_changed(self):
        use_source = self.use_source_folder_check.isChecked()
        self.output_dir_edit.setEnabled(not use_source)
        self.output_dir_btn.setEnabled(not use_source)
        self.output_dir_edit.setVisible(not use_source)
        self.output_dir_btn.setVisible(not use_source)
        self._refresh_selection_hints()

    def _on_output_dir_text_changed(self):
        self._refresh_selection_hints()

    def browse_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "출력 폴더 선택", self.output_dir_edit.text().strip() or "")
        if folder:
            self.output_dir_edit.setText(folder)

    def _on_model_changed(self):
        self._refresh_selection_hints()
        self._refresh_model_state_local()
        self._schedule_system_check()

    def _on_preset_changed(self):
        if self._loading_settings:
            return
        self.settings["custom_preset_enabled"] = False
        self.settings.pop("custom_preset_overrides", None)
        self._apply_preset_controls(get_transcription_preset(self.current_preset_id()))
        self._refresh_selection_hints()

    def _apply_preset_controls(self, preset: dict):
        was_loading = self._loading_settings
        self._loading_settings = True
        self.custom_beam_spin.setValue(int(preset.get("beam_size", 5)))
        self.custom_vad_speech_spin.setValue(int(preset.get("vad_min_speech_ms", 250)))
        self.custom_vad_silence_spin.setValue(int(preset.get("vad_min_silence_ms", 1000)))
        self.custom_repetition_spin.setValue(float(preset.get("repetition_penalty", 1.03)))
        self._loading_settings = was_loading

    def _on_custom_preset_changed(self):
        if self._loading_settings:
            return
        self.settings["custom_preset_enabled"] = True
        self.settings["custom_preset_overrides"] = self.current_preset_overrides()
        self.save_ui_settings()
        self._refresh_selection_hints()

    def current_preset_overrides(self) -> dict:
        return {
            "beam_size": int(self.custom_beam_spin.value()),
            "vad_min_speech_ms": int(self.custom_vad_speech_spin.value()),
            "vad_min_silence_ms": int(self.custom_vad_silence_spin.value()),
            "repetition_penalty": float(self.custom_repetition_spin.value()),
        }

    def _load_settings_into_ui(self):
        self._loading_settings = True
        self._set_combo_by_data(self.language_combo, self.settings.get("language", DEFAULT_LANGUAGE))
        self._set_combo_by_data(self.model_combo, self.settings.get("model_id", default_model_id()))
        self._set_combo_by_data(self.preset_combo, self.settings.get("preset_id", DEFAULT_PRESET_ID))
        self._set_preferred_device(self.settings.get("preferred_device", DEFAULT_PREFERRED_DEVICE))
        self._set_audio_enhance_level(self.settings.get("audio_enhance_level", DEFAULT_AUDIO_ENHANCE_LEVEL))
        formats = self.settings.get("output_formats", DEFAULT_OUTPUT_FORMATS)
        self.output_fmt_srt.setChecked("srt" in formats or not formats)
        self.output_fmt_txt.setChecked("txt" in formats)
        self.output_fmt_vtt.setChecked("vtt" in formats)
        self.use_source_folder_check.setChecked(bool(self.settings.get("use_source_folder", True)))
        self.output_dir_edit.setText(str(self.settings.get("output_dir", "")))
        self._on_output_folder_mode_changed()
        custom = self.settings.get("custom_preset_overrides")
        if self.settings.get("custom_preset_enabled") and isinstance(custom, dict) and custom:
            preset = dict(get_transcription_preset(self.current_preset_id()))
            preset.update(custom)
            self._apply_preset_controls(preset)
        else:
            self._apply_preset_controls(get_transcription_preset(self.current_preset_id()))
        self._loading_settings = False

    def _schedule_system_check(self):
        if self._loading_settings:
            return
        if self.worker_thread and self.worker_thread.is_alive():
            return
        if hasattr(self, "auto_check_timer"):
            self.auto_check_timer.start(650)

    def save_ui_settings(self):
        self.settings["language"] = self.current_lang_code()
        self.settings["model_id"] = self.current_model_id()
        self.settings["preset_id"] = self.current_preset_id()
        self.settings["preferred_device"] = self.current_preferred_device()
        self.settings["audio_enhance_level"] = self.current_audio_enhance_level()
        self.settings["output_formats"] = self.current_output_formats()
        self.settings["use_source_folder"] = self.use_source_folder_check.isChecked()
        self.settings["output_dir"] = self.output_dir_edit.text().strip()
        if hasattr(self, "custom_beam_spin") and self.settings.get("custom_preset_enabled"):
            self.settings["custom_preset_overrides"] = self.current_preset_overrides()
        elif hasattr(self, "custom_beam_spin"):
            self.settings.pop("custom_preset_overrides", None)
        save_settings(self.settings)

    def _refresh_selection_hints(self):
        lang_code = self.current_lang_code()
        lang_name = dict(LANGUAGE_LABELS).get(lang_code, get_language_korean_name(lang_code))
        model_entry = next((m for m in MODEL_CATALOG if m["id"] == self.current_model_id()), None)
        preset_id = self.current_preset_id()
        preset_name = PRESET_LABELS.get(preset_id, preset_id)
        device_text = DEVICE_LABELS.get(self.current_preferred_device(), "자동")
        audio_text = AUDIO_LABELS.get(self.current_audio_enhance_level(), "끔")
        outputs = ", ".join(fmt.upper() for fmt in self.current_output_formats())
        save_text = "원본 폴더" if self.use_source_folder_check.isChecked() else (compact_path_for_display(self.output_dir_edit.text().strip(), 2) or "폴더 선택 필요")
        preset = get_transcription_preset(preset_id)

        self.quick_lang_value.setText(f"{lang_name} · {lang_code}")
        self.quick_model_value.setText(model_entry["label"] if model_entry else self.current_model_id())
        self.quick_preset_value.setText(preset_name)
        self.quick_device_value.setText(device_text)
        self.quick_audio_value.setText(audio_text)
        self.quick_output_value.setText(outputs)
        if hasattr(self, "preset_detail_label"):
            temps = ", ".join(str(v) for v in preset.get("temperature", []))
            self.preset_detail_label.setText(
                f"{preset.get('short_note', '')}\n"
                f"beam {self.custom_beam_spin.value()} · VAD {self.custom_vad_speech_spin.value()}/{self.custom_vad_silence_spin.value()}ms · "
                f"temp {temps} · 반복 억제 {self.custom_repetition_spin.value():.2f}"
            )
        if hasattr(self, "quick_save_value"):
            self.quick_save_value.setText(save_text)

        if not self.current_task_kind:
            self.footer_status_label.setText("시작 대기")
        self.footer_transfer_label.setText(
            self._compact_status_text(f"{self.quick_model_value.text()} · {preset_name} · {outputs}", 76)
        )

        device_note_map = {
            "auto": "GPU와 CPU 중 안정적으로 동작하는 조합을 자동 선택합니다.",
            "cuda": "GPU 가속을 우선 시도합니다.",
            "cpu": "호환성과 재현성을 우선합니다. 처리 시간은 더 길어질 수 있습니다.",
        }
        self.device_note_label.setText(device_note_map.get(self.current_preferred_device(), "장치 선호를 확인하십시오."))

        is_busy = bool(self.worker_thread and self.worker_thread.is_alive())
        if hasattr(self, "home_start_btn"):
            self.home_start_btn.setEnabled(bool(self.input_files) and not is_busy)
        self.save_ui_settings()
        self._refresh_file_summary()

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
        self.home_start_btn.setEnabled(has_files and not bool(self.current_task_kind))

    def _refresh_file_summary(self):
        valid = [path for path in self.input_files if os.path.isfile(path)]
        if not valid:
            self.file_summary_label.setText("선택된 파일이 없습니다.")
            if hasattr(self, "quick_save_value"):
                if self.use_source_folder_check.isChecked():
                    self.quick_save_value.setText("원본 폴더")
                else:
                    self.quick_save_value.setText(compact_path_for_display(self.output_dir_edit.text().strip(), 2) or "폴더 선택 필요")
            return
        if len(valid) == 1:
            self.file_summary_label.setText(f"1개 파일 · {os.path.basename(valid[0])}\n{compact_path_for_display(valid[0], 4)}")
        else:
            self.file_summary_label.setText(f"총 {len(valid)}개 파일 선택됨 · 마지막 파일 {os.path.basename(valid[-1])}")
        if hasattr(self, "quick_save_value"):
            if self.use_source_folder_check.isChecked():
                self.quick_save_value.setText("원본 폴더")
            else:
                self.quick_save_value.setText(compact_path_for_display(self.output_dir_edit.text().strip(), 2) or "폴더 선택 필요")

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
            why.append("파일명에 현장/잡음 단서가 있어 잡음 대응 설정을 권장했습니다.")
        elif avg_duration >= 1200 or any(keyword in names for keyword in lecture_keywords):
            preset_id = "lecture-meeting"
            audio_level = "standard"
            why.append("긴 발화 또는 강의/회의 계열로 보여 연속 발화 설정을 권장했습니다.")
        elif exts & video_exts or any(keyword in names for keyword in dialogue_keywords):
            preset_id = "dialogue-video"
            audio_level = "standard"
            why.append("영상 또는 대사형 콘텐츠로 보여 대사형 설정을 권장했습니다.")
        else:
            why.append("특정 단서가 강하지 않아 균형 설정을 유지합니다.")

        output_formats = ["srt"]
        if batch_count >= 3:
            output_formats.append("txt")
            why.append("여러 파일 처리라 TXT 동시 저장을 권장했습니다.")
        if exts & video_exts:
            output_formats.append("vtt")
            why.append("영상 파일이 포함되어 VTT 저장을 함께 권장했습니다.")

        model_id = None
        if self.current_preferred_device() == "cpu" and (total_duration >= 7200 or batch_count >= 5):
            model_id = "small"
            why.append("CPU 기준 작업량이 커 보여 Small 모델을 권장했습니다.")

        result.update(
            {
                "summary": f"권장 프리셋 {PRESET_LABELS.get(preset_id, preset_id)} · 음성 보정 {AUDIO_LABELS.get(audio_level, audio_level)} · 출력 {', '.join(fmt.upper() for fmt in output_formats)}",
                "meta": " ".join(why),
                "preset_id": preset_id,
                "audio_enhance_level": audio_level,
                "output_formats": output_formats,
                "model_id": model_id,
            }
        )
        return result

    def apply_recommendations(self):
        rec = self._recommendations_for_current_inputs()
        if rec.get("preset_id"):
            self._set_combo_by_data(self.preset_combo, rec["preset_id"])
        if rec.get("audio_enhance_level"):
            self._set_audio_enhance_level(rec["audio_enhance_level"])
        output_formats = rec.get("output_formats") or list(DEFAULT_OUTPUT_FORMATS)
        self.output_fmt_srt.setChecked("srt" in output_formats)
        self.output_fmt_txt.setChecked("txt" in output_formats)
        self.output_fmt_vtt.setChecked("vtt" in output_formats)
        if rec.get("model_id"):
            self._set_combo_by_data(self.model_combo, rec["model_id"])
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
            label = AUDIO_LABELS.get(preprocess_info.get("applied_level"), "적용")
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
            self.activity_preprocess_label.setVisible(False)
            return
        prefix = f"전처리 · {os.path.basename(input_path)} · " if input_path and len(self.input_files) > 1 else "전처리 · "
        self.activity_preprocess_label.setText(prefix + summary)
        self.activity_preprocess_label.setVisible(True)

    def _append_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{timestamp}] {message}")
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _set_status(self, text: str):
        self.footer_status_label.setText(self._compact_status_text(text, 52))
        compact = self._compact_status_text(text, 40)
        self.activity_status_label.setText(compact)

    def _set_transfer_texts(self, headline: str, meta: str = ""):
        self.footer_transfer_label.setText(self._compact_status_text(headline, 76))
        self.activity_status_meta_label.setText(headline)
        self.footer_meta_label.setText(self._compact_status_text(meta, 76))
        if meta:
            self.activity_job_meta_label.setText(meta)

    def _compact_status_text(self, text: str, limit: int) -> str:
        text = str(text or "").replace("\r", " ").strip()
        text = " ".join(part.strip() for part in text.splitlines() if part.strip())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "..."

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
        self.cancel_btn.setEnabled(cancellable)
        if hasattr(self, "download_model_btn"):
            self.download_model_btn.setEnabled(False)
        if hasattr(self, "settings_download_model_btn"):
            self.settings_download_model_btn.setEnabled(False)
        self.job_clock_timer.start()
        self._set_status(label)
        self._refresh_job_clock()

    def _finish_task(self, clear_transfer: bool = False):
        self.current_task_kind = ""
        self.current_task_label = ""
        self.cancel_event = None
        self.worker_thread = None
        self.start_btn.setEnabled(bool(self.input_files))
        self.cancel_btn.setEnabled(False)
        if hasattr(self, "download_model_btn"):
            self.download_model_btn.setEnabled(True)
        if hasattr(self, "settings_download_model_btn"):
            self.settings_download_model_btn.setEnabled(True)
        self.job_clock_timer.stop()
        if clear_transfer:
            self.footer_transfer_label.setText("")
            self.footer_meta_label.setText("")
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
        self.footer_meta_label.setText(self._compact_status_text(meta, 76))

    def _notify_task_busy(self, task_name: str):
        QMessageBox.information(self, "작업 진행 중", f"이미 다른 작업이 진행 중입니다.\n현재 요청: {task_name}")

    def _cancel_current_task(self):
        if self.cancel_event is not None:
            self.cancel_event.set()
            self._append_log("취소 요청을 전달했습니다.")
            self._set_status("취소 요청을 처리하는 중입니다...")

    def _update_status_tile(self, key: str, level: str, summary: str, meta: str = ""):
        tile = self.status_tiles.get(key)
        if not tile:
            return
        level = (level or "neutral").lower()
        text_map = {"success": "정상", "warning": "주의", "danger": "오류", "info": "확인", "neutral": "대기"}
        self._set_badge(tile["badge"], level, text_map.get(level, "대기"))
        tile["summary"].setText(summary)
        tile["meta"].setText(meta)
        tile["level"] = level
        self._refresh_system_overview()

    def _refresh_system_overview(self):
        if not hasattr(self, "status_tiles") or not self.status_tiles:
            return
        levels = [tile.get("level", "neutral") for tile in self.status_tiles.values()]
        if any(level == "danger" for level in levels):
            title = "전사 환경 오류"
            meta = "오류 항목을 확인한 뒤 다시 점검하십시오."
            badge_level = "danger"
        elif any(level == "warning" for level in levels):
            title = "전사 환경 확인 필요"
            meta = "일부 항목이 준비되지 않았습니다. 모델 다운로드나 장치 설정을 확인하십시오."
            badge_level = "warning"
        elif levels and all(level == "success" for level in levels):
            title = "전사 환경 준비 완료"
            meta = f"모델 {self.current_model_id()} · 장치 {self.current_preferred_device().upper()}"
            badge_level = "success"
        else:
            title = "점검 중"
            meta = "모델, 엔진, 장치, 런타임 상태를 확인하는 중입니다."
            badge_level = "neutral"
        if hasattr(self, "system_overview_title"):
            self.system_overview_title.setText(title)
            self.system_overview_meta.setText(meta)
            if hasattr(self, "system_check_steps"):
                self.system_check_steps.setText(meta)
                self.system_check_steps.setVisible(self.current_task_kind == "system_check")
        if hasattr(self, "options_health_badge"):
            self._set_badge(self.options_health_badge, badge_level, {"success": "준비", "warning": "확인", "danger": "오류"}.get(badge_level, "점검"))
            self.options_health_title.setText(title)
            self.options_health_detail.setText(meta)

    def _refresh_model_state_local(self):
        try:
            info = inspect_model_availability(self.current_model_id(), include_remote_meta=False)
            self._apply_model_availability(info)
        except Exception as exc:
            self.model_cache_path_label.setText("")
            self._set_badge(self.model_state_badge, "warning", "모델 확인 실패")

    def _apply_model_availability(self, info: dict):
        if info.get("is_cached"):
            self._set_badge(self.model_state_badge, "success", "모델 로컬 준비됨")
            summary = f"{info.get('label', self.current_model_id())} 모델이 로컬 캐시에 있습니다."
            tile_level = "success"
        else:
            self._set_badge(self.model_state_badge, "warning", "모델 다운로드 필요")
            summary = f"{info.get('label', self.current_model_id())} 모델이 아직 로컬에 없습니다."
            tile_level = "warning"
        self.model_cache_path_label.setText(info.get("cached_path_display") or "아직 캐시가 없습니다.")
        cached = bool(info.get("is_cached"))
        if hasattr(self, "download_model_btn"):
            self.download_model_btn.setEnabled(not cached and not bool(self.current_task_kind))
        if hasattr(self, "settings_download_model_btn"):
            self.settings_download_model_btn.setEnabled(not cached and not bool(self.current_task_kind))
        self._update_status_tile("model", tile_level, info.get("label", self.current_model_id()), summary)

    def _update_live_resource_ui(self, payload: dict):
        if hasattr(self, "cpu_meter"):
            cpu_value = int(max(0, min(100, float(payload.get("system_cpu_percent") or payload.get("cpu_percent") or 0))))
            ram_value = int(max(0, min(100, float(payload.get("ram_percent") or 0))))
            vram_value = int(max(0, min(100, float(payload.get("vram_percent") or 0))))
            self.cpu_meter.setValue(cpu_value)
            self.cpu_meter.setFormat(f"CPU {cpu_value}%")
            self.ram_meter.setValue(ram_value)
            self.ram_meter.setFormat(f"RAM {ram_value}%")
            if payload.get("gpu_available"):
                self.vram_meter.setValue(vram_value)
                self.vram_meter.setFormat(f"VRAM {vram_value}%")
            else:
                self.vram_meter.setValue(0)
                self.vram_meter.setFormat("VRAM 없음")
            pressure = payload.get("pressure_label", "정보 없음")
            self.resource_state_btn.setText(pressure)
            level = payload.get("level", "neutral")
            palette = {
                "success": "#2c5c45",
                "warning": "#71602d",
                "danger": "#8e3a36",
                "info": "#36536e",
                "neutral": "#34414d",
            }
            self.resource_state_btn.setStyleSheet(f"background:{palette.get(level, '#34414d')}; color:#f7fafc;")

    def refresh_live_resource_now(self):
        if self.worker_thread and self.worker_thread.is_alive() and self.current_task_kind == "model_download":
            return
        try:
            payload = collect_live_resource_status()
            self.live_resource_data = payload
            self._update_live_resource_ui(payload)
        except Exception as exc:
            if hasattr(self, "resource_state_btn"):
                self.resource_state_btn.setText("갱신 실패")

    def show_system_details(self):
        try:
            latest = collect_live_resource_status()
            self.live_resource_data = latest
            self._update_live_resource_ui(latest)
        except Exception as exc:
            latest = {"error": str(exc)}

        dialog = SystemDetailsDialog("시스템 상세 정보", self._system_detail_sections(latest), self)
        dialog.exec()

    def _system_detail_sections(self, latest: dict) -> list[dict]:
        model_entry = next((m for m in MODEL_CATALOG if m["id"] == self.current_model_id()), None)
        lang_name = dict(LANGUAGE_LABELS).get(self.current_lang_code(), self.current_lang_code())
        settings_rows = [
            ("언어", f"{lang_name} · {self.current_lang_code()}"),
            ("모델", model_entry["label"] if model_entry else self.current_model_id()),
            ("프리셋", PRESET_LABELS.get(self.current_preset_id(), self.current_preset_id())),
            ("음성 보정", AUDIO_LABELS.get(self.current_audio_enhance_level(), self.current_audio_enhance_level())),
            ("출력", ", ".join(fmt.upper() for fmt in self.current_output_formats())),
            ("저장", "원본과 같은 폴더" if self.use_source_folder_check.isChecked() else (self.output_dir_edit.text().strip() or "미지정")),
        ]

        resource_rows = [
            ("상태", latest.get("pressure_label") or latest.get("alert_text") or "정보 없음"),
            ("CPU", latest.get("system_cpu_text") or latest.get("cpu_text") or "-"),
            ("RAM", latest.get("ram_text", "-")),
            ("VRAM", latest.get("vram_text", "GPU 없음")),
            ("GPU", latest.get("gpu_name", "감지되지 않음")),
            ("환경", f"{latest.get('os_text', '-')} · Python {latest.get('python_version', '-')}"),
        ]

        details = self.status_details or {}

        def section_dict(key: str) -> dict:
            value = details.get(key, {})
            if isinstance(value, dict):
                return value
            result = {}
            for line in str(value or "").splitlines():
                if ":" in line:
                    name, item = line.split(":", 1)
                    result[name.strip()] = item.strip()
                elif line.strip():
                    result.setdefault("상태", line.strip())
            return result

        model = section_dict("model")
        engine = section_dict("engine")
        torch = section_dict("torch")
        device = section_dict("device")
        runtime = section_dict("runtime")

        sections = [
            {"title": "현재 설정", "rows": settings_rows},
            {"title": "실시간 자원", "rows": resource_rows},
        ]
        if model:
            sections.append(
                {
                    "title": "모델",
                    "rows": [
                        ("선택 모델", model.get("선택 모델", model_entry["label"] if model_entry else self.current_model_id())),
                        ("캐시 상태", model.get("로컬 캐시 상태", "-")),
                        ("로딩 대상", model.get("로딩 대상 리포지토리", "-")),
                        ("캐시 위치", model.get("캐시 위치", "-")),
                    ],
                }
            )
        if engine or torch or device or runtime:
            sections.append(
                {
                    "title": "실행 환경",
                    "rows": [
                        ("엔진", engine.get("CTranslate2 상태", "-")),
                        ("CUDA 형식", engine.get("CUDA 추론 형식", "-")),
                        ("PyTorch", torch.get("PyTorch 버전", "-")),
                        ("CUDA 사용", torch.get("torch.cuda.is_available()", "-")),
                        ("장치 판단", device.get("장치 판단", "-")),
                        ("최근 조합", runtime.get("최근 성공 조합", "-")),
                    ],
                }
            )
        if not details:
            sections.append({"title": "점검 상세", "rows": [("상태", "아직 점검 상세 정보가 없습니다.")]})
        return sections

    def _format_detail_mapping(self, mapping: dict, prefix: str = "- ") -> list[str]:
        if not isinstance(mapping, dict):
            return [f"{prefix}{mapping}"]
        lines = []
        for key in sorted(mapping.keys()):
            value = mapping.get(key)
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.extend(self._format_detail_mapping(value, prefix=prefix + "  "))
            else:
                lines.append(f"{prefix}{key}: {value}")
        return lines

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
        self.system_overview_title.setText("전사 환경 점검 중")
        self.system_overview_meta.setText("모델 캐시 · CTranslate2 · PyTorch/CUDA · GPU/CPU · 런타임 조합")
        self.system_check_steps.setText("모델 캐시 확인 → 엔진 점검 → 장치 감지 → 실행 조합 검증")
        self.system_check_steps.setVisible(True)
        self._set_transfer_texts(
            f"시스템 점검 · 모델 {self.current_model_id()} · 장치 선호 {self.current_preferred_device().upper()}",
            "환경, 엔진, 장치, 실행 조합을 점검합니다.",
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
                self.msg_queue.put(("runtime_choice_text", "선택 모델이 로컬에 없어 실제 로딩 검증은 건너뛰었습니다."))

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

    def _request_model_download_permission(self, info: dict, reason: str) -> bool:
        ticket = {"info": info, "reason": reason, "approved": False, "event": threading.Event()}
        self.msg_queue.put(("ask_model_download", ticket))
        ticket["event"].wait()
        return bool(ticket.get("approved"))

    def _show_model_download_dialog(self, info: dict, reason: str) -> bool:
        dialog = ModelDownloadDialog(info, reason, self)
        approved = dialog.exec() == QDialog.DialogCode.Accepted
        if approved:
            self.download_progress_dialog = DownloadProgressDialog(self)
            self.download_progress_dialog.show()
        return approved

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
        preset_overrides = self.current_preset_overrides()
        output_dir = self.current_output_dir()
        if output_dir and not os.path.isdir(output_dir):
            QMessageBox.critical(self, "출력 폴더 오류", "출력 폴더를 선택하거나 원본 폴더 저장을 사용하십시오.")
            return

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
            f"실행 설정 | lang={lang_code}, preset={preset_id}, model={model_id}, preferred_device={pref}, audio_enhance={audio_level}, outputs={output_formats}, output_dir={output_dir or 'source'}, preset_overrides={preset_overrides}"
        )

        self.worker_thread = threading.Thread(
            target=self._worker_transcription,
            args=(input_paths, lang_code, model_id, preset_id, pref, audio_level, output_formats, output_dir, preset_overrides),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker_transcription(
        self,
        input_paths: list[str],
        lang_code: str,
        model_id: str,
        preset_id: str,
        pref: str,
        audio_level: str,
        output_formats: list[str],
        output_dir: str | None,
        preset_overrides: dict | None,
    ):
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
                    output_dir=output_dir,
                    preset_overrides=preset_overrides,
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

    def open_result(self):
        if self.output_path and os.path.isfile(self.output_path):
            open_path(self.output_path)

    def open_result_folder(self):
        if self.output_path:
            folder = os.path.dirname(self.output_path)
            if os.path.isdir(folder):
                open_path(folder)

    def _update_download_transfer_ui(self, payload: dict):
        percent = float(payload.get("percent") or 0.0)
        self._set_progress_value(percent)
        self._set_transfer_texts(
            f"다운로드 {percent:.0f}% · {payload.get('downloaded_text', '')} / {payload.get('total_text', '')}",
            payload.get("message", ""),
        )
        if self.download_progress_dialog is not None:
            self.download_progress_dialog.update_progress(payload)
            if not self.download_progress_dialog.isVisible():
                self.download_progress_dialog.show()

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
