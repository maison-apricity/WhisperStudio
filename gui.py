# -*- coding: utf-8 -*-
"""
WhisperStudio redesigned GUI.

This module intentionally keeps the public entry point compatible with app.py:

    app = SubtitleGUI()
    app.mainloop()

The implementation uses only Python's standard Tkinter/ttk stack so that the
application does not depend on PySide6 at runtime.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    DEFAULT_AUDIO_ENHANCE_LEVEL,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL_ID,
    DEFAULT_OUTPUT_FORMATS,
    DEFAULT_PREFERRED_DEVICE,
    DEFAULT_PRESET_ID,
    LANGUAGE_OPTIONS,
    TRANSCRIPTION_PRESETS,
)
from env_manager import (
    collect_live_resource_status,
    collect_startup_status,
    download_model_to_cache,
    inspect_model_availability,
    choose_runtime_device_and_type,
)
from model_catalog import MODEL_CATALOG, get_model_entry
from paths import bundled_icon_path
from settings_manager import load_settings, save_settings
from subtitle_engine import run_transcription_job


SUPPORTED_MEDIA_EXTS = {
    ".mkv",
    ".mp4",
    ".mov",
    ".avi",
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".webm",
}

DEVICE_OPTIONS = [
    ("auto", "자동 선택"),
    ("cuda", "GPU 우선"),
    ("cpu", "CPU 전용"),
]

AUDIO_ENHANCE_OPTIONS = [
    ("off", "끔"),
    ("standard", "표준 보정"),
    ("strong", "강한 보정"),
]


@dataclass(frozen=True)
class Palette:
    bg: str = "#0B1020"
    panel: str = "#111827"
    panel_2: str = "#151E2F"
    panel_3: str = "#1D293D"
    line: str = "#2A3A52"
    text: str = "#EAF0FA"
    muted: str = "#96A3B8"
    faint: str = "#6D7B90"
    accent: str = "#7C5CFF"
    accent_2: str = "#2DD4BF"
    danger: str = "#FB7185"
    warning: str = "#FBBF24"
    success: str = "#34D399"
    input_bg: str = "#0F172A"
    hover: str = "#25324A"


class SubtitleGUI(tk.Tk):
    """Complete, self-contained Tkinter GUI for WhisperStudio."""

    def __init__(self) -> None:
        super().__init__()
        self.palette = Palette()
        self.settings = load_settings()
        self.files: list[str] = []
        self.last_saved_paths: list[str] = []
        self.event_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self.busy = False
        self.start_time: float | None = None

        self._build_lookup_tables()
        self._configure_window()
        self._configure_styles()
        self._create_variables()
        self._build_layout()
        self._bind_events()
        self._refresh_file_list()
        self._refresh_selection_summary()
        self._set_status("대기 중", "파일을 추가한 뒤 시작하십시오.", "neutral")
        self._set_progress(0.0)
        self._log("WhisperStudio UI 초기화 완료")

        self.after(80, self._process_ui_events)
        self.after(250, self._run_startup_probe)
        self.after(2500, self._refresh_live_resources_loop)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    def _build_lookup_tables(self) -> None:
        self.language_display_to_code = {
            f"{label} · {code}": code for code, label in LANGUAGE_OPTIONS
        }
        self.language_code_to_display = {
            code: f"{label} · {code}" for code, label in LANGUAGE_OPTIONS
        }

        self.model_display_to_id = {entry["display"]: entry["id"] for entry in MODEL_CATALOG}
        self.model_id_to_display = {entry["id"]: entry["display"] for entry in MODEL_CATALOG}

        self.preset_display_to_id = {
            preset["display"]: preset["id"] for preset in TRANSCRIPTION_PRESETS
        }
        self.preset_id_to_display = {
            preset["id"]: preset["display"] for preset in TRANSCRIPTION_PRESETS
        }

        self.device_display_to_id = {display: code for code, display in DEVICE_OPTIONS}
        self.device_id_to_display = {code: display for code, display in DEVICE_OPTIONS}

        self.audio_display_to_id = {display: code for code, display in AUDIO_ENHANCE_OPTIONS}
        self.audio_id_to_display = {code: display for code, display in AUDIO_ENHANCE_OPTIONS}

    def _configure_window(self) -> None:
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1320x820")
        self.minsize(1120, 700)
        self.configure(bg=self.palette.bg)

        icon_path = bundled_icon_path()
        if icon_path:
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        try:
            self.tk.call("tk", "scaling", 1.12)
        except Exception:
            pass

    def _configure_styles(self) -> None:
        p = self.palette
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        base_font = ("Segoe UI", 10)
        title_font = ("Segoe UI Semibold", 15)
        small_font = ("Segoe UI", 9)
        button_font = ("Segoe UI Semibold", 10)

        self.option_add("*Font", base_font)
        self.option_add("*tearOff", False)

        self.style.configure("TFrame", background=p.bg)
        self.style.configure("Panel.TFrame", background=p.panel, borderwidth=0)
        self.style.configure("Card.TFrame", background=p.panel_2, borderwidth=0)
        self.style.configure("Header.TFrame", background=p.bg)

        self.style.configure("TLabel", background=p.bg, foreground=p.text, font=base_font)
        self.style.configure("Muted.TLabel", background=p.bg, foreground=p.muted, font=small_font)
        self.style.configure("Panel.TLabel", background=p.panel, foreground=p.text, font=base_font)
        self.style.configure("PanelMuted.TLabel", background=p.panel, foreground=p.muted, font=small_font)
        self.style.configure("Card.TLabel", background=p.panel_2, foreground=p.text, font=base_font)
        self.style.configure("CardMuted.TLabel", background=p.panel_2, foreground=p.muted, font=small_font)
        self.style.configure("Title.TLabel", background=p.bg, foreground=p.text, font=title_font)
        self.style.configure("Section.TLabel", background=p.panel, foreground=p.text, font=("Segoe UI Semibold", 11))
        self.style.configure("CardTitle.TLabel", background=p.panel_2, foreground=p.text, font=("Segoe UI Semibold", 10))

        self.style.configure(
            "Primary.TButton",
            background=p.accent,
            foreground="#FFFFFF",
            borderwidth=0,
            focusthickness=0,
            focuscolor=p.accent,
            padding=(16, 10),
            font=button_font,
        )
        self.style.map(
            "Primary.TButton",
            background=[("disabled", p.panel_3), ("active", "#6D4BFF")],
            foreground=[("disabled", p.faint)],
        )

        self.style.configure(
            "Ghost.TButton",
            background=p.panel_3,
            foreground=p.text,
            borderwidth=0,
            padding=(14, 9),
            font=button_font,
        )
        self.style.map(
            "Ghost.TButton",
            background=[("disabled", p.panel_2), ("active", p.hover)],
            foreground=[("disabled", p.faint)],
        )

        self.style.configure(
            "Danger.TButton",
            background=p.danger,
            foreground="#FFFFFF",
            borderwidth=0,
            padding=(14, 9),
            font=button_font,
        )
        self.style.map(
            "Danger.TButton",
            background=[("disabled", p.panel_3), ("active", "#E85D72")],
            foreground=[("disabled", p.faint)],
        )

        self.style.configure(
            "TCombobox",
            fieldbackground=p.input_bg,
            background=p.input_bg,
            foreground=p.text,
            arrowcolor=p.text,
            bordercolor=p.line,
            lightcolor=p.line,
            darkcolor=p.line,
            padding=(8, 6),
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", p.input_bg)],
            foreground=[("readonly", p.text)],
            selectbackground=[("readonly", p.input_bg)],
            selectforeground=[("readonly", p.text)],
        )

        self.style.configure(
            "Horizontal.TProgressbar",
            troughcolor=p.input_bg,
            background=p.accent_2,
            bordercolor=p.input_bg,
            lightcolor=p.accent_2,
            darkcolor=p.accent_2,
        )

        self.style.configure(
            "TCheckbutton",
            background=p.panel,
            foreground=p.text,
            font=base_font,
            indicatorcolor=p.input_bg,
            padding=(0, 4),
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", p.panel)],
            foreground=[("disabled", p.faint), ("active", p.text)],
        )

    def _create_variables(self) -> None:
        s = self.settings

        language = s.get("language") or DEFAULT_LANGUAGE
        model_id = s.get("model_id") or DEFAULT_MODEL_ID
        preset_id = s.get("preset_id") or DEFAULT_PRESET_ID
        device = s.get("preferred_device") or DEFAULT_PREFERRED_DEVICE
        audio_level = s.get("audio_enhance_level") or DEFAULT_AUDIO_ENHANCE_LEVEL

        self.language_var = tk.StringVar(value=self.language_code_to_display.get(language, self.language_code_to_display[DEFAULT_LANGUAGE]))
        self.model_var = tk.StringVar(value=self.model_id_to_display.get(model_id, self.model_id_to_display[DEFAULT_MODEL_ID]))
        self.preset_var = tk.StringVar(value=self.preset_id_to_display.get(preset_id, self.preset_id_to_display[DEFAULT_PRESET_ID]))
        self.device_var = tk.StringVar(value=self.device_id_to_display.get(device, self.device_id_to_display[DEFAULT_PREFERRED_DEVICE]))
        self.audio_var = tk.StringVar(value=self.audio_id_to_display.get(audio_level, self.audio_id_to_display[DEFAULT_AUDIO_ENHANCE_LEVEL]))

        formats = s.get("output_formats") or DEFAULT_OUTPUT_FORMATS
        self.output_srt_var = tk.BooleanVar(value="srt" in formats)
        self.output_vtt_var = tk.BooleanVar(value="vtt" in formats)
        self.output_txt_var = tk.BooleanVar(value="txt" in formats)

        self.status_title_var = tk.StringVar(value="대기 중")
        self.status_meta_var = tk.StringVar(value="")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="0%")
        self.elapsed_var = tk.StringVar(value="00:00")
        self.file_count_var = tk.StringVar(value="0개 파일")
        self.output_summary_var = tk.StringVar(value="SRT")
        self.model_state_var = tk.StringVar(value="모델 상태 확인 전")
        self.engine_state_var = tk.StringVar(value="엔진 상태 확인 전")
        self.device_state_var = tk.StringVar(value="장치 상태 확인 전")
        self.resource_state_var = tk.StringVar(value="자원 상태 확인 전")
        self.selection_summary_var = tk.StringVar(value="설정 요약 대기")

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = tk.Frame(self, bg=self.palette.panel, width=300, bd=0, highlightthickness=0)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)

        self.main = tk.Frame(self, bg=self.palette.bg, bd=0, highlightthickness=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        self._build_sidebar()
        self._build_header()
        self._build_content()
        self._build_footer()

    def _build_sidebar(self) -> None:
        p = self.palette
        self.sidebar.grid_columnconfigure(0, weight=1)

        brand = tk.Frame(self.sidebar, bg=p.panel)
        brand.grid(row=0, column=0, sticky="ew", padx=24, pady=(26, 18))
        brand.grid_columnconfigure(0, weight=1)

        tk.Label(
            brand,
            text="Whisper\nStudio",
            bg=p.panel,
            fg=p.text,
            justify="left",
            font=("Segoe UI Semibold", 24),
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            brand,
            text=APP_TAGLINE,
            bg=p.panel,
            fg=p.muted,
            justify="left",
            wraplength=240,
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.status_card = self._make_side_card(self.sidebar, "현재 상태", self.status_title_var, self.status_meta_var)
        self.status_card.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 12))

        progress_box = tk.Frame(self.sidebar, bg=p.panel_2, highlightbackground=p.line, highlightthickness=1)
        progress_box.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 12))
        progress_box.grid_columnconfigure(0, weight=1)
        tk.Label(progress_box, text="진행률", bg=p.panel_2, fg=p.text, font=("Segoe UI Semibold", 10)).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))
        tk.Label(progress_box, textvariable=self.progress_text_var, bg=p.panel_2, fg=p.accent_2, font=("Segoe UI Semibold", 18)).grid(row=1, column=0, sticky="w", padx=16)
        ttk.Progressbar(progress_box, variable=self.progress_var, maximum=100, mode="determinate").grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 8))
        tk.Label(progress_box, textvariable=self.elapsed_var, bg=p.panel_2, fg=p.muted, font=("Segoe UI", 9)).grid(row=3, column=0, sticky="w", padx=16, pady=(0, 14))

        self._make_side_card(self.sidebar, "파일", self.file_count_var, self.selection_summary_var).grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 12))
        self._make_side_card(self.sidebar, "출력", self.output_summary_var, tk.StringVar(value="입력 파일과 같은 폴더에 저장")).grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 12))

        button_box = tk.Frame(self.sidebar, bg=p.panel)
        button_box.grid(row=5, column=0, sticky="ew", padx=18, pady=(6, 16))
        button_box.grid_columnconfigure(0, weight=1)
        self.start_button = ttk.Button(button_box, text="전사 시작", style="Primary.TButton", command=self.start_transcription)
        self.start_button.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.cancel_button = ttk.Button(button_box, text="중단", style="Danger.TButton", command=self.cancel_current_task, state="disabled")
        self.cancel_button.grid(row=1, column=0, sticky="ew")

        bottom = tk.Frame(self.sidebar, bg=p.panel)
        bottom.grid(row=99, column=0, sticky="sew", padx=24, pady=(12, 24))
        self.sidebar.grid_rowconfigure(98, weight=1)
        tk.Label(bottom, text=f"v{APP_VERSION}", bg=p.panel, fg=p.faint, font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(bottom, text="Tkinter redesign", bg=p.panel, fg=p.faint, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

    def _make_side_card(self, parent: tk.Widget, title: str, main_var: tk.StringVar, meta_var: tk.StringVar) -> tk.Frame:
        p = self.palette
        card = tk.Frame(parent, bg=p.panel_2, highlightbackground=p.line, highlightthickness=1)
        card.grid_columnconfigure(0, weight=1)
        tk.Label(card, text=title, bg=p.panel_2, fg=p.muted, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 2))
        tk.Label(card, textvariable=main_var, bg=p.panel_2, fg=p.text, font=("Segoe UI Semibold", 12), wraplength=235, justify="left").grid(row=1, column=0, sticky="w", padx=16)
        tk.Label(card, textvariable=meta_var, bg=p.panel_2, fg=p.muted, font=("Segoe UI", 9), wraplength=235, justify="left").grid(row=2, column=0, sticky="w", padx=16, pady=(4, 14))
        return card

    def _build_header(self) -> None:
        p = self.palette
        header = tk.Frame(self.main, bg=p.bg)
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 14))
        header.grid_columnconfigure(0, weight=1)

        tk.Label(header, text="새 작업", bg=p.bg, fg=p.text, font=("Segoe UI Semibold", 22)).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="파일 추가 → 설정 선택 → 실행 상태 확인 → 자막 저장", bg=p.bg, fg=p.muted, font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(4, 0))

        actions = tk.Frame(header, bg=p.bg)
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        self.check_button = ttk.Button(actions, text="환경 점검", style="Ghost.TButton", command=self.run_environment_probe)
        self.check_button.grid(row=0, column=0, padx=(0, 8))
        self.download_button = ttk.Button(actions, text="모델 다운로드", style="Ghost.TButton", command=self.download_selected_model)
        self.download_button.grid(row=0, column=1)

    def _build_content(self) -> None:
        content = tk.Frame(self.main, bg=self.palette.bg)
        content.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 14))
        content.grid_columnconfigure(0, weight=1, uniform="main")
        content.grid_columnconfigure(1, weight=1, uniform="main")
        content.grid_rowconfigure(0, weight=1)

        left = tk.Frame(content, bg=self.palette.panel, highlightbackground=self.palette.line, highlightthickness=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        right = tk.Frame(content, bg=self.palette.panel, highlightbackground=self.palette.line, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        self._build_file_panel(left)
        self._build_settings_panel(left)
        self._build_log_panel(right)
        self._build_status_panel(right)

    def _build_file_panel(self, parent: tk.Frame) -> None:
        p = self.palette
        header = tk.Frame(parent, bg=p.panel)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)
        tk.Label(header, text="입력 파일", bg=p.panel, fg=p.text, font=("Segoe UI Semibold", 13)).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="영상/음성 파일을 여러 개 넣으면 순서대로 처리합니다.", bg=p.panel, fg=p.muted, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(3, 0))

        buttons = tk.Frame(header, bg=p.panel)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e")
        self.add_files_button = ttk.Button(buttons, text="파일 추가", style="Ghost.TButton", command=self.add_files)
        self.add_files_button.grid(row=0, column=0, padx=(0, 6))
        self.add_folder_button = ttk.Button(buttons, text="폴더 추가", style="Ghost.TButton", command=self.add_folder)
        self.add_folder_button.grid(row=0, column=1)

        self.file_list_frame = tk.Frame(parent, bg=p.input_bg, highlightbackground=p.line, highlightthickness=1)
        self.file_list_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.file_list_frame.grid_columnconfigure(0, weight=1)
        self.file_list_frame.grid_rowconfigure(0, weight=1)

        self.file_listbox = tk.Listbox(
            self.file_list_frame,
            bg=p.input_bg,
            fg=p.text,
            selectbackground=p.accent,
            selectforeground="#FFFFFF",
            activestyle="none",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Segoe UI", 10),
        )
        self.file_listbox.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        file_scroll = ttk.Scrollbar(self.file_list_frame, orient="vertical", command=self.file_listbox.yview)
        file_scroll.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 8))
        self.file_listbox.configure(yscrollcommand=file_scroll.set)

        file_controls = tk.Frame(parent, bg=p.panel)
        file_controls.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        file_controls.grid_columnconfigure(4, weight=1)
        self.remove_file_button = ttk.Button(file_controls, text="선택 제거", style="Ghost.TButton", command=self.remove_selected_file)
        self.remove_file_button.grid(row=0, column=0, padx=(0, 8))
        self.clear_files_button = ttk.Button(file_controls, text="전체 비우기", style="Ghost.TButton", command=self.clear_files)
        self.clear_files_button.grid(row=0, column=1, padx=(0, 8))
        self.move_up_button = ttk.Button(file_controls, text="위로", style="Ghost.TButton", command=lambda: self.move_selected_file(-1))
        self.move_up_button.grid(row=0, column=2, padx=(0, 8))
        self.move_down_button = ttk.Button(file_controls, text="아래로", style="Ghost.TButton", command=lambda: self.move_selected_file(1))
        self.move_down_button.grid(row=0, column=3)

    def _build_settings_panel(self, parent: tk.Frame) -> None:
        p = self.palette
        panel = tk.Frame(parent, bg=p.panel)
        panel.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_columnconfigure(1, weight=1)

        tk.Label(panel, text="전사 설정", bg=p.panel, fg=p.text, font=("Segoe UI Semibold", 13)).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self._setting_combo(panel, 1, 0, "언어", self.language_var, list(self.language_display_to_code.keys()))
        self._setting_combo(panel, 1, 1, "모델", self.model_var, [m["display"] for m in MODEL_CATALOG])
        self._setting_combo(panel, 3, 0, "프리셋", self.preset_var, [p["display"] for p in TRANSCRIPTION_PRESETS])
        self._setting_combo(panel, 3, 1, "장치", self.device_var, [display for _code, display in DEVICE_OPTIONS])
        self._setting_combo(panel, 5, 0, "음성 보정", self.audio_var, [display for _code, display in AUDIO_ENHANCE_OPTIONS])

        output_box = tk.Frame(panel, bg=p.panel)
        output_box.grid(row=5, column=1, sticky="ew", padx=(8, 0), pady=(0, 10))
        tk.Label(output_box, text="출력 형식", bg=p.panel, fg=p.muted, font=("Segoe UI", 9)).pack(anchor="w")
        checks = tk.Frame(output_box, bg=p.panel)
        checks.pack(anchor="w", fill="x", pady=(2, 0))
        ttk.Checkbutton(checks, text="SRT", variable=self.output_srt_var, command=self._on_setting_changed).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(checks, text="VTT", variable=self.output_vtt_var, command=self._on_setting_changed).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(checks, text="TXT", variable=self.output_txt_var, command=self._on_setting_changed).pack(side="left")

    def _setting_combo(self, parent: tk.Frame, row: int, col: int, label: str, variable: tk.StringVar, values: list[str]) -> ttk.Combobox:
        p = self.palette
        box = tk.Frame(parent, bg=p.panel)
        box.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 8, 8 if col == 0 else 0), pady=(0, 10))
        box.grid_columnconfigure(0, weight=1)
        tk.Label(box, text=label, bg=p.panel, fg=p.muted, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(box, textvariable=variable, values=values, state="readonly")
        combo.grid(row=1, column=0, sticky="ew", pady=(4, 0), ipady=2)
        combo.bind("<<ComboboxSelected>>", lambda _event: self._on_setting_changed())
        return combo

    def _build_log_panel(self, parent: tk.Frame) -> None:
        p = self.palette
        header = tk.Frame(parent, bg=p.panel)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)
        tk.Label(header, text="실행 로그", bg=p.panel, fg=p.text, font=("Segoe UI Semibold", 13)).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="전처리, 모델 로딩, 세그먼트 처리, 저장 경로를 표시합니다.", bg=p.panel, fg=p.muted, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.clear_log_button = ttk.Button(header, text="로그 비우기", style="Ghost.TButton", command=self.clear_log)
        self.clear_log_button.grid(row=0, column=1, rowspan=2, sticky="e")

        self.log_frame = tk.Frame(parent, bg=p.input_bg, highlightbackground=p.line, highlightthickness=1)
        self.log_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.log_frame.grid_columnconfigure(0, weight=1)
        self.log_frame.grid_rowconfigure(0, weight=1)

        self.log_text = tk.Text(
            self.log_frame,
            bg=p.input_bg,
            fg=p.text,
            insertbackground=p.text,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            font=("Consolas", 10),
            undo=False,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)
        log_scroll = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns", pady=10, padx=(0, 10))
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.tag_configure("muted", foreground=p.muted)
        self.log_text.tag_configure("success", foreground=p.success)
        self.log_text.tag_configure("warning", foreground=p.warning)
        self.log_text.tag_configure("danger", foreground=p.danger)
        self.log_text.tag_configure("accent", foreground=p.accent_2)

    def _build_status_panel(self, parent: tk.Frame) -> None:
        p = self.palette
        status = tk.Frame(parent, bg=p.panel)
        status.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        status.grid_columnconfigure(0, weight=1)
        status.grid_columnconfigure(1, weight=1)

        self._small_status_card(status, 0, 0, "모델", self.model_state_var)
        self._small_status_card(status, 0, 1, "엔진", self.engine_state_var)
        self._small_status_card(status, 1, 0, "장치", self.device_state_var)
        self._small_status_card(status, 1, 1, "자원", self.resource_state_var)

    def _small_status_card(self, parent: tk.Frame, row: int, col: int, title: str, value_var: tk.StringVar) -> None:
        p = self.palette
        card = tk.Frame(parent, bg=p.panel_2, highlightbackground=p.line, highlightthickness=1)
        card.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 8, 8 if col == 0 else 0), pady=(0 if row == 0 else 8, 8))
        card.grid_columnconfigure(0, weight=1)
        tk.Label(card, text=title, bg=p.panel_2, fg=p.muted, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 1))
        tk.Label(card, textvariable=value_var, bg=p.panel_2, fg=p.text, font=("Segoe UI Semibold", 10), wraplength=285, justify="left").grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

    def _build_footer(self) -> None:
        p = self.palette
        footer = tk.Frame(self.main, bg=p.bg)
        footer.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 20))
        footer.grid_columnconfigure(0, weight=1)
        tk.Label(footer, text="선택 모델이 로컬 캐시에 없으면 다운로드 버튼으로 먼저 준비할 수 있습니다.", bg=p.bg, fg=p.faint, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        self.open_output_button = ttk.Button(footer, text="최근 저장 폴더 열기", style="Ghost.TButton", command=self.open_last_output_folder)
        self.open_output_button.grid(row=0, column=1, sticky="e")

    def _bind_events(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-o>", lambda _event: self.add_files())
        self.bind("<Control-l>", lambda _event: self.clear_log())
        self.bind("<F5>", lambda _event: self.run_environment_probe())

    # ------------------------------------------------------------------
    # UI event bridge
    # ------------------------------------------------------------------
    def _ui(self, func: Callable[[], None]) -> None:
        self.event_queue.put(func)

    def _process_ui_events(self) -> None:
        while True:
            try:
                func = self.event_queue.get_nowait()
            except queue.Empty:
                break
            try:
                func()
            except Exception:
                traceback.print_exc()
        self.after(80, self._process_ui_events)

    # ------------------------------------------------------------------
    # File queue
    # ------------------------------------------------------------------
    def add_files(self) -> None:
        if self.busy:
            return
        paths = filedialog.askopenfilenames(
            title="전사할 영상 또는 음성 파일 선택",
            filetypes=[
                ("지원 미디어", "*.mkv *.mp4 *.mov *.avi *.mp3 *.wav *.m4a *.flac *.ogg *.webm"),
                ("모든 파일", "*.*"),
            ],
        )
        self._append_files(list(paths))

    def add_folder(self) -> None:
        if self.busy:
            return
        folder = filedialog.askdirectory(title="미디어 파일이 들어 있는 폴더 선택")
        if not folder:
            return
        collected: list[str] = []
        for root, _dirs, names in os.walk(folder):
            for name in names:
                path = os.path.join(root, name)
                if os.path.splitext(name)[1].lower() in SUPPORTED_MEDIA_EXTS:
                    collected.append(path)
        collected.sort(key=lambda x: x.lower())
        self._append_files(collected)

    def _append_files(self, paths: list[str]) -> None:
        added = 0
        known = {os.path.normcase(os.path.abspath(p)) for p in self.files}
        for path in paths:
            if not path:
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in SUPPORTED_MEDIA_EXTS:
                continue
            full = os.path.abspath(path)
            norm = os.path.normcase(full)
            if norm in known:
                continue
            self.files.append(full)
            known.add(norm)
            added += 1
        if added:
            self._log(f"입력 파일 추가: {added}개", "accent")
        self._refresh_file_list()
        self._refresh_selection_summary()

    def remove_selected_file(self) -> None:
        if self.busy:
            return
        selection = list(self.file_listbox.curselection())
        if not selection:
            return
        for index in reversed(selection):
            if 0 <= index < len(self.files):
                del self.files[index]
        self._refresh_file_list()
        self._refresh_selection_summary()

    def clear_files(self) -> None:
        if self.busy:
            return
        self.files.clear()
        self._refresh_file_list()
        self._refresh_selection_summary()
        self._log("입력 파일 목록을 비웠습니다.", "muted")

    def move_selected_file(self, direction: int) -> None:
        if self.busy:
            return
        selection = self.file_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.files):
            return
        self.files[idx], self.files[new_idx] = self.files[new_idx], self.files[idx]
        self._refresh_file_list()
        self.file_listbox.selection_set(new_idx)
        self.file_listbox.activate(new_idx)

    def _refresh_file_list(self) -> None:
        self.file_listbox.delete(0, tk.END)
        for index, path in enumerate(self.files, start=1):
            self.file_listbox.insert(tk.END, f"{index:02d}. {os.path.basename(path)}")
        self.file_count_var.set(f"{len(self.files)}개 파일")
        self._update_buttons_state()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _on_setting_changed(self) -> None:
        self._refresh_selection_summary()
        try:
            save_settings(self._collect_current_settings())
        except Exception:
            pass

    def _collect_current_settings(self) -> dict:
        formats: list[str] = []
        if self.output_srt_var.get():
            formats.append("srt")
        if self.output_vtt_var.get():
            formats.append("vtt")
        if self.output_txt_var.get():
            formats.append("txt")
        if not formats:
            formats = ["srt"]
            self.output_srt_var.set(True)

        current = dict(self.settings or {})
        current.update(
            {
                "language": self.language_display_to_code.get(self.language_var.get(), DEFAULT_LANGUAGE),
                "model_id": self.model_display_to_id.get(self.model_var.get(), DEFAULT_MODEL_ID),
                "preset_id": self.preset_display_to_id.get(self.preset_var.get(), DEFAULT_PRESET_ID),
                "preferred_device": self.device_display_to_id.get(self.device_var.get(), DEFAULT_PREFERRED_DEVICE),
                "audio_enhance_level": self.audio_display_to_id.get(self.audio_var.get(), DEFAULT_AUDIO_ENHANCE_LEVEL),
                "output_formats": formats,
            }
        )
        return current

    def _refresh_selection_summary(self) -> None:
        settings = self._collect_current_settings()
        model = get_model_entry(settings["model_id"])["label"]
        device = self.device_id_to_display.get(settings["preferred_device"], settings["preferred_device"])
        preset = self.preset_id_to_display.get(settings["preset_id"], settings["preset_id"])
        formats = ", ".join(fmt.upper() for fmt in settings["output_formats"])
        self.selection_summary_var.set(f"{model} · {device} · {preset}")
        self.output_summary_var.set(formats)

    # ------------------------------------------------------------------
    # Status, log, resources
    # ------------------------------------------------------------------
    def _set_status(self, title: str, meta: str = "", level: str = "neutral") -> None:
        self.status_title_var.set(title)
        self.status_meta_var.set(meta)

    def _set_progress(self, value: float) -> None:
        value = max(0.0, min(100.0, float(value)))
        self.progress_var.set(value)
        self.progress_text_var.set(f"{value:.0f}%")

    def _log(self, message: str, tag: str | None = None) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{stamp}] ", "muted")
        if tag:
            self.log_text.insert(tk.END, message + "\n", tag)
        else:
            self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def _thread_log(self, message: str, tag: str | None = None) -> None:
        self._ui(lambda: self._log(message, tag))

    def clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def _refresh_elapsed(self) -> None:
        if self.busy and self.start_time is not None:
            elapsed = int(time.time() - self.start_time)
            minutes, seconds = divmod(elapsed, 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                text = f"{minutes:02d}:{seconds:02d}"
            self.elapsed_var.set(text)
            self.after(500, self._refresh_elapsed)

    def _run_startup_probe(self) -> None:
        self.run_environment_probe(background=True)

    def run_environment_probe(self, background: bool = False) -> None:
        if self.busy and not background:
            return

        settings = self._collect_current_settings()
        self._log("환경 점검 시작", "accent")

        def worker() -> None:
            try:
                status = collect_startup_status(settings)
                self._ui(lambda: self._apply_startup_status(status))
                self._thread_log("환경 점검 완료", "success")
            except Exception as exc:
                self._thread_log(f"환경 점검 실패: {exc}", "danger")

        threading.Thread(target=worker, daemon=True).start()

    def _apply_startup_status(self, status: dict) -> None:
        model = status.get("model", {})
        engine = status.get("engine", {})
        device = status.get("device", {})
        resources = status.get("resources", {})
        self.model_state_var.set(f"{model.get('summary', '')} · {model.get('meta', '')}")
        self.engine_state_var.set(f"{engine.get('summary', '')} · {engine.get('meta', '')}")
        self.device_state_var.set(f"{device.get('summary', '')} · {device.get('meta', '')}")
        self.resource_state_var.set(f"{resources.get('summary', '')} · {resources.get('meta', '')}")

    def _refresh_live_resources_loop(self) -> None:
        def worker() -> None:
            try:
                status = collect_live_resource_status()
                self._ui(lambda: self.resource_state_var.set(
                    f"{status.get('pressure_label', '정보 없음')} · CPU {status.get('system_cpu_text', '정보 없음')} · RAM {status.get('ram_text', '정보 없음')}"
                ))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()
        self.after(4000, self._refresh_live_resources_loop)

    # ------------------------------------------------------------------
    # Model download
    # ------------------------------------------------------------------
    def download_selected_model(self) -> None:
        if self.busy:
            return
        settings = self._collect_current_settings()
        model_id = settings["model_id"]
        model_label = get_model_entry(model_id)["label"]

        self.cancel_event.clear()
        self._set_busy(True)
        self.start_time = time.time()
        self._refresh_elapsed()
        self._set_progress(0.0)
        self._set_status("모델 다운로드", model_label, "neutral")
        self._log(f"모델 다운로드 준비: {model_label}", "accent")

        def worker() -> None:
            try:
                info = inspect_model_availability(model_id, include_remote_meta=True)
                if info.get("is_cached"):
                    self._thread_log(f"이미 준비된 모델입니다: {info.get('label')}", "success")
                    self._ui(lambda: self._set_progress(100.0))
                    self._ui(lambda: self._set_status("모델 준비 완료", info.get("label", model_label), "success"))
                    return

                self._thread_log(f"다운로드 대상: {info.get('download_source', model_id)}")
                self._thread_log(f"저장 위치: {info.get('download_target_display', '')}")

                def progress(payload: dict) -> None:
                    pct = payload.get("percent")
                    msg = payload.get("message", "")
                    if pct is not None:
                        self._ui(lambda pct=pct: self._set_progress(float(pct)))
                    if msg:
                        self._ui(lambda msg=msg: self.status_meta_var.set(msg))

                final_info = download_model_to_cache(
                    model_id,
                    log=lambda msg: self._thread_log(msg),
                    progress=progress,
                    cancel_event=self.cancel_event,
                )
                self._thread_log(f"모델 다운로드 완료: {final_info.get('label', model_label)}", "success")
                self._ui(lambda: self._set_status("모델 준비 완료", final_info.get("label", model_label), "success"))
                self._ui(lambda: self._set_progress(100.0))
            except RuntimeError as exc:
                if str(exc) == "MODEL_DOWNLOAD_CANCELLED":
                    self._thread_log("모델 다운로드가 중단되었습니다.", "warning")
                    self._ui(lambda: self._set_status("다운로드 중단", "부분 파일은 정리되었습니다.", "warning"))
                else:
                    self._thread_log(f"모델 다운로드 실패: {exc}", "danger")
                    self._ui(lambda: self._set_status("다운로드 실패", str(exc), "danger"))
            except Exception as exc:
                self._thread_log(f"모델 다운로드 실패: {exc}", "danger")
                self._thread_log(traceback.format_exc(), "danger")
                self._ui(lambda: self._set_status("다운로드 실패", str(exc), "danger"))
            finally:
                self._ui(lambda: self._set_busy(False))
                self._ui(lambda: self.run_environment_probe(background=True))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------
    def start_transcription(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("입력 파일 없음", "전사할 영상 또는 음성 파일을 먼저 추가하십시오.")
            return

        settings = self._collect_current_settings()
        save_settings(settings)
        self.settings = settings

        self.cancel_event.clear()
        self.last_saved_paths.clear()
        self._set_busy(True)
        self.start_time = time.time()
        self._refresh_elapsed()
        self._set_progress(0.0)
        self._set_status("전사 준비", "장치와 모델 조합을 확인하고 있습니다.", "neutral")
        self._log("전사 작업 시작", "accent")

        files_snapshot = list(self.files)

        def worker() -> None:
            try:
                model_id = settings["model_id"]
                preferred_device = settings["preferred_device"]
                lang_code = settings["language"]
                preset_id = settings["preset_id"]
                audio_level = settings["audio_enhance_level"]
                output_formats = settings["output_formats"]

                model_label = get_model_entry(model_id)["label"]
                self._thread_log(f"선택 모델: {model_label}")
                self._thread_log(f"선호 장치: {preferred_device}")

                self._ui(lambda: self._set_status("장치 검증", "모델 로딩 가능한 device/compute_type 조합 확인 중", "neutral"))
                runtime = choose_runtime_device_and_type(
                    model_id=model_id,
                    preferred_device=preferred_device,
                    log=lambda msg: self._thread_log(msg),
                )
                device = runtime["device"]
                compute_type = runtime["compute_type"]
                load_id = runtime.get("load_id") or get_model_entry(model_id)["load_id"]

                self.settings["last_good_device"] = device
                self.settings["last_good_compute_type"] = compute_type
                save_settings(self.settings)

                self._thread_log(f"실행 조합 확정: {device} / {compute_type}", "success")
                total = len(files_snapshot)

                for idx, in_path in enumerate(files_snapshot):
                    if self.cancel_event.is_set():
                        raise RuntimeError("TRANSCRIPTION_CANCELLED")

                    basename = os.path.basename(in_path)
                    self._thread_log(f"[{idx + 1}/{total}] 작업 시작: {basename}", "accent")
                    self._ui(lambda idx=idx, total=total, basename=basename: self._set_status(
                        "전사 중",
                        f"{idx + 1}/{total} · {basename}",
                        "neutral",
                    ))

                    def progress(pct: float, idx: int = idx, total: int = total) -> None:
                        overall = ((idx + max(0.0, min(100.0, float(pct))) / 100.0) / total) * 100.0
                        self._ui(lambda overall=overall: self._set_progress(overall))

                    result = run_transcription_job(
                        in_path=in_path,
                        lang_code=lang_code,
                        model_id=load_id,
                        device=device,
                        compute_type=compute_type,
                        log=lambda msg: self._thread_log(msg),
                        progress=progress,
                        preset_id=preset_id,
                        audio_enhance_level=audio_level,
                        output_formats=output_formats,
                        cancel_event=self.cancel_event,
                    )

                    saved_paths = result.get("saved_paths", {})
                    for path in saved_paths.values():
                        if path:
                            self.last_saved_paths.append(path)
                    saved_text = ", ".join(f"{fmt.upper()}={path}" for fmt, path in saved_paths.items())
                    self._thread_log(f"[{idx + 1}/{total}] 저장 완료: {saved_text}", "success")

                self._ui(lambda: self._set_progress(100.0))
                self._ui(lambda: self._set_status("작업 완료", f"{total}개 파일 처리 완료", "success"))
                self._thread_log("전체 전사 작업 완료", "success")
            except RuntimeError as exc:
                if str(exc) == "TRANSCRIPTION_CANCELLED":
                    self._thread_log("전사 작업이 중단되었습니다.", "warning")
                    self._ui(lambda: self._set_status("작업 중단", "사용자 요청으로 중단됨", "warning"))
                else:
                    self._thread_log(f"전사 실패: {exc}", "danger")
                    self._ui(lambda: self._set_status("전사 실패", str(exc), "danger"))
            except Exception as exc:
                self._thread_log(f"전사 실패: {exc}", "danger")
                self._thread_log(traceback.format_exc(), "danger")
                self._ui(lambda: self._set_status("전사 실패", str(exc), "danger"))
            finally:
                self._ui(lambda: self._set_busy(False))
                self._ui(lambda: self.run_environment_probe(background=True))

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def cancel_current_task(self) -> None:
        if not self.busy:
            return
        self.cancel_event.set()
        self._set_status("중단 요청", "현재 단계가 정리되면 멈춥니다.", "warning")
        self._log("중단 요청을 보냈습니다.", "warning")
        self.cancel_button.configure(state="disabled")

    # ------------------------------------------------------------------
    # Buttons / state
    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        if not busy:
            self.start_time = None
        self._update_buttons_state()

    def _update_buttons_state(self) -> None:
        state_normal = "disabled" if self.busy else "normal"
        state_start = "normal" if (not self.busy and len(self.files) > 0) else "disabled"
        state_cancel = "normal" if self.busy and not self.cancel_event.is_set() else "disabled"

        for button in (
            self.add_files_button,
            self.add_folder_button,
            self.remove_file_button,
            self.clear_files_button,
            self.move_up_button,
            self.move_down_button,
            self.download_button,
            self.check_button,
        ):
            try:
                button.configure(state=state_normal)
            except Exception:
                pass

        self.start_button.configure(state=state_start)
        self.cancel_button.configure(state=state_cancel)

    def open_last_output_folder(self) -> None:
        paths = [p for p in self.last_saved_paths if p and os.path.isfile(p)]
        if not paths:
            messagebox.showinfo("최근 저장 경로 없음", "아직 저장된 출력 파일이 없습니다.")
            return
        folder = os.path.dirname(paths[-1])
        try:
            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo("저장 폴더", folder)

    def _on_close(self) -> None:
        if self.busy:
            if not messagebox.askyesno("작업 진행 중", "진행 중인 작업을 중단하고 종료하시겠습니까?"):
                return
            self.cancel_event.set()
        try:
            save_settings(self._collect_current_settings())
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = SubtitleGUI()
    app.mainloop()
