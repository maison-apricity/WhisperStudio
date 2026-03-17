# -*- coding: utf-8 -*-

import os
import queue
import threading
import traceback
import time
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import tkinter.font as tkfont

from config import (
    APP_NAME,
    APP_VERSION,
    APP_TAGLINE,
    DEFAULT_LANGUAGE,
    DEFAULT_PREFERRED_DEVICE,
    DEFAULT_PRESET_ID,
    LANGUAGE_OPTIONS,
    LANGUAGE_KOREAN_NAMES,
    LANGUAGE_NATIVE_NAMES,
    TRANSCRIPTION_PRESETS,
    get_language_korean_name,
    get_transcription_preset,
)
from model_catalog import MODEL_CATALOG, default_model_id
from settings_manager import load_settings, save_settings
from env_manager import (
    collect_live_resource_status,
    collect_startup_status,
    choose_runtime_device_and_type,
    download_model_to_cache,
    inspect_model_availability,
    probe_repo_download_speed,
)
from subtitle_engine import run_transcription_job
from font_runtime import pick_code_font_family, pick_language_font_family, pick_ui_font_family


class SubtitleGUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1260x920")
        self.minsize(1020, 720)

        self.msg_queue = queue.Queue()
        self.worker_thread = None
        self.output_path = None
        self.settings = load_settings()
        self.status_details = {}
        self.base_status_details = {}
        self.live_resource_data = {}
        self.status_expanded = False
        self.selector_popup = None
        self.selector_canvas = None
        self.selector_inner = None
        self.selector_window_id = None
        self.selector_filter_var = None
        self.selector_items = []
        self.selector_rebuild_callback = None
        self.resource_refresh_job = None
        self.status_meta = {}
        self.last_measured_speed_mbps = None
        self.last_measured_repo_id = ""
        self.cancel_event = None
        self.current_task_kind = ""
        self.current_task_label = ""
        self.current_progress_percent = 0.0
        self.job_started_at = None
        self.job_clock_job = None
        self.transfer_mode = ""

        self.language_value_var = tk.StringVar(value=DEFAULT_LANGUAGE)
        self.language_display_var = tk.StringVar(value="언어를 선택하십시오")
        self.model_value_var = tk.StringVar(value=default_model_id())
        self.model_display_var = tk.StringVar(value="모델을 선택하십시오")
        self.model_meta_var = tk.StringVar(value="모델 설명을 불러오는 중입니다.")
        self.preset_value_var = tk.StringVar(value=DEFAULT_PRESET_ID)
        self.preset_display_var = tk.StringVar(value="프리셋을 선택하십시오")
        self.preset_note_var = tk.StringVar(value="전사 프리셋 설명을 불러오는 중입니다.")
        self.pref_device_value_var = tk.StringVar(value=DEFAULT_PREFERRED_DEVICE)

        self.status_var = tk.StringVar(value="준비됨")
        self.lang_note_var = tk.StringVar(value="선택 언어 정보를 불러오는 중입니다.")
        self.model_note_var = tk.StringVar(value="모델 설명을 불러오는 중입니다.")
        self.device_note_var = tk.StringVar(value="장치 선호 설명을 불러오는 중입니다.")
        self.model_state_var = tk.StringVar(value="모델 상태 확인 중")
        self.resource_summary_var = tk.StringVar(value="실시간 자원 상태를 불러오는 중입니다.")
        self.resource_meta_var = tk.StringVar(value="마지막 갱신 --:--:--")

        self.device_display_map = {
            "자동": "auto",
            "GPU": "cuda",
            "CPU": "cpu",
        }

        self._init_palette()
        self._init_fonts()
        self._init_style()
        self._build_ui()
        self._load_settings_into_ui()
        self._refresh_selection_hints()
        self._refresh_model_state_local()

        self.after(50, self.start_startup_scan)
        self.after(150, self.refresh_live_resource_now)
        self.after(100, self._poll_queue)

    # -------------------------------------------------
    # Theme / Style
    # -------------------------------------------------
    def _init_palette(self):
        self.colors = {
            "bg": "#F4F7FB",
            "card": "#FFFFFF",
            "border": "#D9E2EC",
            "text": "#1F2937",
            "subtext": "#6B7280",
            "accent": "#2563EB",
            "accent_hover": "#1D4ED8",
            "success": "#059669",
            "warning": "#D97706",
            "danger": "#DC2626",
            "info": "#0284C7",
            "log_bg": "#F8FAFC",
            "log_fg": "#1F2937",
            "dock": "#F8FAFC",
        }

    def _init_fonts(self):
        self.font_family = pick_ui_font_family()
        self.mono_family = pick_code_font_family(self.font_family)

        self.font_title = tkfont.Font(family=self.font_family, size=21, weight="bold")
        self.font_heading = tkfont.Font(family=self.font_family, size=12, weight="bold")
        self.font_body = tkfont.Font(family=self.font_family, size=11)
        self.font_body_bold = tkfont.Font(family=self.font_family, size=11, weight="bold")
        self.font_emphasis = tkfont.Font(family=self.font_family, size=12, weight="bold")
        self.font_small = tkfont.Font(family=self.font_family, size=10)
        self.font_badge = tkfont.Font(family=self.font_family, size=9, weight="bold")
        self.font_mono = tkfont.Font(family=self.mono_family, size=10)

        self.font_family_ja = pick_language_font_family("ja", fallback=self.font_family)
        self.font_family_zh = pick_language_font_family("zh", fallback=self.font_family)
        self.font_body_ja = tkfont.Font(family=self.font_family_ja, size=11)
        self.font_body_zh = tkfont.Font(family=self.font_family_zh, size=11)
        self.selector_title_fonts = {
            "default": self.font_body_bold,
            "ja": tkfont.Font(family=self.font_family_ja, size=11, weight="bold"),
            "zh": tkfont.Font(family=self.font_family_zh, size=11, weight="bold"),
        }
        self.selector_subtitle_fonts = {
            "default": self.font_small,
            "ja": tkfont.Font(family=self.font_family_ja, size=10),
            "zh": tkfont.Font(family=self.font_family_zh, size=10),
        }

    def _init_style(self):
        self.configure(bg=self.colors["bg"])

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            ".",
            background=self.colors["bg"],
            foreground=self.colors["text"],
            fieldbackground="#FFFFFF",
            font=(self.font_family, 10),
        )

        style.configure(
            "Modern.TEntry",
            fieldbackground="#FFFFFF",
            foreground=self.colors["text"],
            padding=8,
            bordercolor=self.colors["border"],
            lightcolor=self.colors["border"],
            darkcolor=self.colors["border"],
        )

        style.configure(
            "Modern.Horizontal.TProgressbar",
            troughcolor="#E5E7EB",
            background=self.colors["accent"],
            bordercolor="#E5E7EB",
            lightcolor=self.colors["accent"],
            darkcolor=self.colors["accent"],
            thickness=12,
        )

    # -------------------------------------------------
    # Layout helpers
    # -------------------------------------------------
    def _make_card(self, parent, expand=False):
        outer = tk.Frame(parent, bg=self.colors["bg"], bd=0, highlightthickness=0)
        outer.pack(fill="both" if expand else "x", expand=expand, pady=(0, 10))

        card = tk.Frame(
            outer,
            bg=self.colors["card"],
            bd=0,
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )
        card.pack(fill="both", expand=True)
        return card

    def _make_section_header(self, parent, title, subtitle=""):
        header = tk.Frame(parent, bg=self.colors["card"])
        header.pack(fill="x", padx=18, pady=(14, 6))

        tk.Label(
            header,
            text=title,
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=self.font_heading,
            anchor="w",
        ).pack(anchor="w")

        if subtitle:
            tk.Label(
                header,
                text=subtitle,
                bg=self.colors["card"],
                fg=self.colors["subtext"],
                font=self.font_small,
                anchor="w",
            ).pack(anchor="w", pady=(2, 0))

    def _make_button(self, parent, text, command, kind="secondary", state="normal", width=None):
        palette = {
            "primary": {
                "bg": self.colors["accent"],
                "fg": "#FFFFFF",
                "activebg": self.colors["accent_hover"],
                "activefg": "#FFFFFF",
            },
            "secondary": {
                "bg": "#E8EEF7",
                "fg": self.colors["text"],
                "activebg": "#D9E6F7",
                "activefg": self.colors["text"],
            },
            "soft": {
                "bg": "#F1F5F9",
                "fg": self.colors["text"],
                "activebg": "#E2E8F0",
                "activefg": self.colors["text"],
            },
        }
        p = palette.get(kind, palette["secondary"])

        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=p["bg"],
            fg=p["fg"],
            activebackground=p["activebg"],
            activeforeground=p["activefg"],
            disabledforeground="#94A3B8",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=14 if kind == "primary" else 12,
            pady=8 if kind == "primary" else 7,
            cursor="hand2",
            font=self.font_body_bold if kind == "primary" else self.font_body,
            state=state,
            width=width,
        )
        return btn

    def _set_badge(self, widget, text, level="info"):
        palette = {
            "info": ("#E0F2FE", "#0369A1"),
            "success": ("#DCFCE7", "#166534"),
            "warning": ("#FEF3C7", "#92400E"),
            "danger": ("#FEE2E2", "#991B1B"),
            "neutral": ("#E5E7EB", "#374151"),
        }
        bg, fg = palette.get(level, palette["neutral"])
        widget.configure(text=text, bg=bg, fg=fg)

    def _append_log(self, msg: str):
        stamp = time.strftime("%H:%M:%S")
        if not hasattr(self, "log_text"):
            return
        self.log_text.insert("end", f"[{stamp}] ", ("timestamp",))
        tag = "message_ascii" if all(ord(ch) < 128 for ch in msg) else "message_text"
        self.log_text.insert("end", msg, (tag,))
        self.log_text.insert("end", "\n")
        self.log_text.see("end")

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    def _format_elapsed_text(self, seconds: float | None) -> str:
        if seconds is None:
            return "--:--"
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _begin_task(self, kind: str, label: str, cancellable: bool = False):
        self.current_task_kind = kind
        self.current_task_label = label
        self.current_progress_percent = 0.0
        self.job_started_at = time.time()
        self.start_btn.config(state="disabled")
        try:
            self.model_download_btn.config(state="disabled")
        except Exception:
            pass
        if hasattr(self, "cancel_btn"):
            if cancellable:
                self.cancel_event = threading.Event()
                self.cancel_btn.configure(state="normal", text="취소")
            else:
                self.cancel_event = None
                self.cancel_btn.configure(state="disabled", text="취소")
        self._refresh_job_clock()

    def _finish_task(self, clear_transfer: bool = False):
        self.current_task_kind = ""
        self.current_task_label = ""
        self.current_progress_percent = 0.0
        self.job_started_at = None
        self.cancel_event = None
        if hasattr(self, "cancel_btn"):
            self.cancel_btn.configure(state="disabled", text="취소")
        if self.job_clock_job is not None:
            try:
                self.after_cancel(self.job_clock_job)
            except Exception:
                pass
            self.job_clock_job = None
        if hasattr(self, "job_meta_var"):
            self.job_meta_var.set("")
        self.start_btn.config(state="normal")
        self._refresh_model_state_local()
        if clear_transfer:
            self._clear_download_transfer_ui()

    def _refresh_job_clock(self):
        if self.job_clock_job is not None:
            try:
                self.after_cancel(self.job_clock_job)
            except Exception:
                pass
            self.job_clock_job = None

        if not self.current_task_kind or self.job_started_at is None:
            if hasattr(self, "job_meta_var"):
                self.job_meta_var.set("")
            return

        elapsed = max(0.0, time.time() - self.job_started_at)
        msg = f"{self.current_task_label} · {self._format_elapsed_text(elapsed)} 경과"
        if 8.0 <= self.current_progress_percent < 99.0 and elapsed >= 5.0:
            total = elapsed * 100.0 / max(self.current_progress_percent, 1.0)
            remain = max(0.0, total - elapsed)
            msg += f" · 남은 예상 시간 {self._format_elapsed_text(remain)}"
        if self.cancel_event is not None and self.cancel_event.is_set():
            msg += " · 취소 요청됨"
        self.job_meta_var.set(msg)
        self.job_clock_job = self.after(500, self._refresh_job_clock)

    def _set_transcription_transfer_summary(self):
        lang_code = self.current_lang_code()
        lang_name = get_language_korean_name(lang_code)
        preset = get_transcription_preset(self.current_preset_id())
        self.transfer_mode = "transcription"
        self.transfer_var.set(
            f"전사 실행 · 언어 {lang_name} · 프리셋 {preset['label']} · 모델 {self.model_display_var.get()}"
        )
        self.transfer_meta_var.set("전사 진행률 0% · 장치와 모델을 준비하는 중입니다.")

    def _cancel_current_task(self):
        if not self.current_task_kind or self.cancel_event is None or self.cancel_event.is_set():
            return
        self.cancel_event.set()
        if self.current_task_kind == "model_download":
            status = "모델 다운로드 취소를 요청했습니다. 현재 전송 단계가 정리되는 즉시 중단합니다."
        else:
            status = "전사 취소를 요청했습니다. 현재 단계가 정리되는 즉시 중단합니다."
        self._set_status(status)
        self.transfer_meta_var.set(status)
        self._append_log(status)
        if hasattr(self, "cancel_btn"):
            self.cancel_btn.configure(state="disabled", text="취소 요청됨")
        self._refresh_job_clock()

    def _progress_busy_on(self):
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

    def _progress_busy_off(self):
        self.progress.stop()
        self.progress.configure(mode="determinate")

    # -------------------------------------------------
    # Scroll layout
    # -------------------------------------------------
    def _build_ui(self):
        self._build_scrollable_main()
        self._build_top_header(self.content_frame)
        self._build_file_card(self.content_frame)
        self._build_options_card(self.content_frame)
        self._build_status_card(self.content_frame)
        self._build_log(self.content_frame)
        self._build_bottom_dock()

    def _build_scrollable_main(self):
        self.body_host = tk.Frame(self, bg=self.colors["bg"])
        self.body_host.pack(side="top", fill="both", expand=True)

        self.main_canvas = tk.Canvas(
            self.body_host,
            bg=self.colors["bg"],
            highlightthickness=0,
            bd=0,
        )
        self.main_canvas.pack(side="left", fill="both", expand=True)

        self.main_scrollbar = ttk.Scrollbar(self.body_host, orient="vertical", command=self.main_canvas.yview)
        self.main_scrollbar.pack(side="right", fill="y")
        self.main_canvas.configure(yscrollcommand=self.main_scrollbar.set)

        self.content_frame = tk.Frame(self.main_canvas, bg=self.colors["bg"])
        self.content_window_id = self.main_canvas.create_window((0, 0), window=self.content_frame, anchor="nw")

        self.content_frame.bind("<Configure>", self._on_content_configure)
        self.main_canvas.bind("<Configure>", self._on_canvas_configure)
        self.main_canvas.bind("<Enter>", lambda _e: self._bind_main_wheel())
        self.main_canvas.bind("<Leave>", lambda _e: self._unbind_main_wheel())
        self.content_frame.bind("<Enter>", lambda _e: self._bind_main_wheel())
        self.content_frame.bind("<Leave>", lambda _e: self._unbind_main_wheel())

    def _on_content_configure(self, _event=None):
        self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.main_canvas.itemconfigure(self.content_window_id, width=event.width)

    def _bind_main_wheel(self):
        self.bind_all("<MouseWheel>", self._on_main_mousewheel)
        self.bind_all("<Button-4>", self._on_main_mousewheel)
        self.bind_all("<Button-5>", self._on_main_mousewheel)

    def _unbind_main_wheel(self):
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")

    def _widget_is_descendant(self, widget, ancestor) -> bool:
        if widget is None or ancestor is None:
            return False
        current = widget
        while current is not None:
            if current == ancestor:
                return True
            current = getattr(current, "master", None)
        return False

    def _on_main_mousewheel(self, event):
        if self.selector_popup is not None:
            return
        widget = getattr(event, "widget", None)
        if self._widget_is_descendant(widget, getattr(self, "log_text", None)):
            return
        if hasattr(event, "delta") and event.delta:
            step = -1 * int(event.delta / 120)
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1
        self.main_canvas.yview_scroll(step, "units")

    # -------------------------------------------------
    # Selection helpers
    # -------------------------------------------------
    def current_lang_code(self) -> str:
        value = self.language_value_var.get().strip()
        valid_codes = {code for code, _ in LANGUAGE_OPTIONS}
        return value if value in valid_codes else DEFAULT_LANGUAGE

    def current_model_id(self) -> str:
        value = self.model_value_var.get().strip()
        valid_ids = {m["id"] for m in MODEL_CATALOG}
        return value if value in valid_ids else default_model_id()

    def current_preset_id(self) -> str:
        value = self.preset_value_var.get().strip()
        valid_ids = {preset["id"] for preset in TRANSCRIPTION_PRESETS}
        return value if value in valid_ids else DEFAULT_PRESET_ID

    def current_preferred_device(self) -> str:
        value = self.pref_device_value_var.get().strip()
        return value if value in {"auto", "cuda", "cpu"} else DEFAULT_PREFERRED_DEVICE

    def _set_language(self, lang_code: str):
        valid_codes = {code for code, _ in LANGUAGE_OPTIONS}
        self.language_value_var.set(lang_code if lang_code in valid_codes else DEFAULT_LANGUAGE)
        self._refresh_selection_hints()
        self._close_selector_popup()

    def _set_model(self, model_id: str):
        valid_ids = {m["id"] for m in MODEL_CATALOG}
        self.model_value_var.set(model_id if model_id in valid_ids else default_model_id())
        self._refresh_selection_hints()
        self._refresh_model_state_local()
        self._close_selector_popup()

    def _set_preset(self, preset_id: str):
        valid_ids = {preset["id"] for preset in TRANSCRIPTION_PRESETS}
        self.preset_value_var.set(preset_id if preset_id in valid_ids else DEFAULT_PRESET_ID)
        self._refresh_selection_hints()
        self._close_selector_popup()

    def _set_preferred_device(self, value: str):
        self.pref_device_value_var.set(value if value in {"auto", "cuda", "cpu"} else "auto")
        self._refresh_selection_hints()

    def _language_selector_items(self) -> list[dict]:
        items = []
        for code, name in LANGUAGE_OPTIONS:
            items.append({
                "value": code,
                "title": name,
                "subtitle": "언어 자동 감지" if code == "auto" else f"전사 언어 · {get_language_korean_name(code)}",
                "badge": "AUTO" if code == "auto" else code.upper(),
                "keywords": f"{code} {name} {get_language_korean_name(code)} {LANGUAGE_NATIVE_NAMES.get(code, name)}",
                "font_key": "default",
            })
        return items

    def _preset_selector_items(self) -> list[dict]:
        items = []
        for preset in TRANSCRIPTION_PRESETS:
            items.append({
                "value": preset["id"],
                "title": preset["label"],
                "subtitle": f"{preset['short_note']} · {preset['long_note']}",
                "badge": preset["short_note"],
                "keywords": f"{preset['id']} {preset['label']} {preset['short_note']} {preset['long_note']}",
            })
        return items

    def _model_selector_items(self) -> list[dict]:
        items = []
        for entry in MODEL_CATALOG:
            try:
                availability = inspect_model_availability(entry["id"], include_remote_meta=False)
                if availability.get("is_cached"):
                    state_text = "로컬 준비됨"
                    status_level = "success"
                else:
                    state_text = "다운로드 필요"
                    status_level = "warning"
            except Exception:
                state_text = "상태 확인 필요"
                status_level = "neutral"

            items.append({
                "value": entry["id"],
                "title": entry["label"],
                "subtitle": f"{entry['short_note']} · {entry['long_note']} · {state_text}",
                "badge": state_text,
                "keywords": f"{entry['id']} {entry['label']} {entry['short_note']} {entry['long_note']} {state_text}",
                "status_level": status_level,
            })
        return items

    def _refresh_selection_hints(self):
        lang_code = self.current_lang_code()
        lang_name = get_language_korean_name(lang_code)
        self.language_display_var.set(f"{lang_name} · {lang_code}")
        if lang_code == "auto":
            self.lang_note_var.set("입력 음성의 언어를 자동으로 추정합니다. 정확도 향상을 위해 대상 언어 지정을 권장합니다.")
        else:
            self.lang_note_var.set(f"입력 음성을 {lang_name} 기준으로 우선 해석합니다.")
        self._refresh_language_button_font()

        preset = get_transcription_preset(self.current_preset_id())
        self.preset_display_var.set(preset["label"])
        self.preset_note_var.set(f"{preset['short_note']} · {preset['long_note']}")

        entry = next((m for m in MODEL_CATALOG if m["id"] == self.current_model_id()), None)
        if entry is None:
            self.model_display_var.set(default_model_id())
            self.model_meta_var.set("선택 모델 설명을 찾지 못했습니다.")
            self.model_note_var.set("선택 모델 설명을 찾지 못했습니다.")
        else:
            self.model_display_var.set(entry["label"])
            model_meta = f"{entry['short_note']} · {entry['long_note']}"
            self.model_meta_var.set(model_meta)
            self.model_note_var.set(model_meta)

        device_value = self.current_preferred_device()
        self._refresh_device_buttons()
        device_note_map = {
            "auto": "GPU와 CPU 중 사용 가능한 환경을 자동으로 선택합니다. 속도를 위해 GPU 가속이 우선 시도됩니다.",
            "cuda": "GPU 가속 사용합니다. 장치와 드라이버가 맞지 않으면 실행 전 점검이 필요합니다.",
            "cpu": "호환성과 재현성을 우선합니다. 처리 시간은 늘어날 수 있지만 환경 의존성이 가장 낮습니다.",
        }
        self.device_note_var.set(device_note_map.get(device_value, "장치 선호 설정을 확인하십시오."))

    def _build_selector_popup(self, title: str, anchor_widget, width: int = 520, max_height: int = 380):
        self._close_selector_popup()

        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.transient(self)
        popup.configure(bg=self.colors["border"])
        popup.lift()

        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height() + 6
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(width, screen_w - 40)
        height = min(max_height, screen_h - 80)
        x = max(20, min(x, screen_w - width - 20))
        y = max(20, min(y, screen_h - height - 40))
        popup.geometry(f"{width}x{height}+{x}+{y}")

        shell = tk.Frame(popup, bg=self.colors["card"], highlightthickness=1, highlightbackground=self.colors["border"])
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg="#F8FAFC")
        header.pack(fill="x")
        tk.Label(header, text=title, bg="#F8FAFC", fg=self.colors["text"], font=self.font_heading).pack(side="left", padx=12, pady=10)
        self._make_button(header, "닫기", self._close_selector_popup, kind="soft").pack(side="right", padx=8, pady=6)

        self.selector_filter_var = tk.StringVar()
        search_wrap = tk.Frame(shell, bg=self.colors["card"])
        search_wrap.pack(fill="x", padx=12, pady=(10, 8))
        tk.Label(search_wrap, text="검색", bg=self.colors["card"], fg=self.colors["subtext"], font=self.font_small).pack(anchor="w")
        search_entry = ttk.Entry(search_wrap, textvariable=self.selector_filter_var, style="Modern.TEntry")
        search_entry.pack(fill="x", pady=(4, 0), ipady=3)

        list_wrap = tk.Frame(shell, bg=self.colors["card"])
        list_wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.selector_canvas = tk.Canvas(list_wrap, bg=self.colors["card"], highlightthickness=0, bd=0)
        self.selector_canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.selector_canvas.yview)
        scroll.pack(side="right", fill="y")
        self.selector_canvas.configure(yscrollcommand=scroll.set)

        self.selector_inner = tk.Frame(self.selector_canvas, bg=self.colors["card"])
        self.selector_window_id = self.selector_canvas.create_window((0, 0), window=self.selector_inner, anchor="nw")

        self.selector_inner.bind(
            "<Configure>",
            lambda _e: self.selector_canvas.configure(scrollregion=self.selector_canvas.bbox("all")),
        )
        self.selector_canvas.bind(
            "<Configure>",
            lambda e: self.selector_canvas.itemconfigure(self.selector_window_id, width=e.width),
        )
        popup.bind("<Escape>", lambda _e: self._close_selector_popup())
        popup.bind("<FocusOut>", self._on_selector_popup_focus_out)
        popup.bind("<MouseWheel>", self._on_selector_mousewheel)
        popup.bind("<Button-4>", self._on_selector_mousewheel)
        popup.bind("<Button-5>", self._on_selector_mousewheel)

        self.selector_popup = popup
        popup.after(10, lambda: search_entry.focus_set())
        return popup

    def _on_selector_popup_focus_out(self, _event=None):
        if not self.selector_popup:
            return
        current = self.focus_displayof()
        if current is None:
            self._close_selector_popup()

    def _on_selector_mousewheel(self, event):
        if not self.selector_canvas:
            return
        if hasattr(event, "delta") and event.delta:
            step = -1 * int(event.delta / 120)
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1
        self.selector_canvas.yview_scroll(step, "units")

    def _close_selector_popup(self):
        if self.selector_popup is not None:
            try:
                self.selector_popup.grab_release()
            except Exception:
                pass
            try:
                self.selector_popup.destroy()
            except Exception:
                pass
        self.selector_popup = None
        self.selector_canvas = None
        self.selector_inner = None
        self.selector_window_id = None
        self.selector_filter_var = None
        self.selector_items = []
        self.selector_rebuild_callback = None

    def _populate_selector_popup(self, items: list[dict], selected_value: str, on_select):
        if self.selector_inner is None or self.selector_filter_var is None:
            return
        self.selector_items = list(items)

        def rebuild(*_args):
            if self.selector_inner is None:
                return
            keyword = self.selector_filter_var.get().strip().lower()
            for child in self.selector_inner.winfo_children():
                child.destroy()

            filtered = []
            for item in self.selector_items:
                haystack = f"{item.get('title', '')} {item.get('subtitle', '')} {item.get('badge', '')} {item.get('keywords', '')}".lower()
                if not keyword or keyword in haystack:
                    filtered.append(item)

            if not filtered:
                empty = tk.Label(
                    self.selector_inner,
                    text="조건에 맞는 항목이 없습니다.",
                    bg=self.colors["card"],
                    fg=self.colors["subtext"],
                    font=self.font_body,
                    pady=18,
                )
                empty.pack(fill="x")
                return

            for idx, item in enumerate(filtered):
                selected = item["value"] == selected_value
                base_palette = self._selector_item_palette(item, selected=False)
                active_palette = self._selector_item_palette(item, selected=selected)

                tile = tk.Frame(
                    self.selector_inner,
                    bg=active_palette["bg"],
                    highlightthickness=1,
                    highlightbackground=active_palette["border"],
                    padx=12,
                    pady=10,
                    cursor="hand2",
                )
                tile.pack(fill="x", pady=(0, 8 if idx < len(filtered) - 1 else 0))

                top = tk.Frame(tile, bg=active_palette["bg"])
                top.pack(fill="x")
                title = tk.Label(
                    top,
                    text=item.get("title", ""),
                    bg=active_palette["bg"],
                    fg=self.colors["text"],
                    font=self._selector_title_font(item),
                    anchor="w",
                    cursor="hand2",
                )
                title.pack(side="left", fill="x", expand=True)
                badge = tk.Label(
                    top,
                    text=item.get("badge", ""),
                    bg=active_palette["badge_bg"],
                    fg=active_palette["badge_fg"],
                    font=self.font_badge,
                    padx=8,
                    pady=3,
                    cursor="hand2",
                )
                badge.pack(side="right")

                subtitle = tk.Label(
                    tile,
                    text=item.get("subtitle", ""),
                    bg=active_palette["bg"],
                    fg=active_palette["subtext_fg"],
                    font=self._selector_subtitle_font(item),
                    anchor="w",
                    justify="left",
                    wraplength=540,
                    cursor="hand2",
                )
                subtitle.pack(fill="x", pady=(6, 0))

                def apply_palette(palette, tile=tile, top=top, title=title, subtitle=subtitle, badge=badge):
                    try:
                        tile.configure(bg=palette["bg"], highlightbackground=palette["border"])
                        top.configure(bg=palette["bg"])
                        title.configure(bg=palette["bg"], fg=self.colors["text"])
                        subtitle.configure(bg=palette["bg"], fg=palette["subtext_fg"])
                        badge.configure(bg=palette["badge_bg"], fg=palette["badge_fg"])
                    except Exception:
                        pass

                def handle_enter(_e, selected_row=selected):
                    if selected_row:
                        return
                    apply_palette(self._selector_item_palette(item, selected=False, hovered=True))

                def handle_leave(_e, selected_row=selected, palette=base_palette):
                    if selected_row:
                        return
                    apply_palette(palette)

                for widget in (tile, top, title, subtitle, badge):
                    widget.bind("<Enter>", handle_enter)
                    widget.bind("<Leave>", handle_leave)
                    widget.bind("<Button-1>", lambda _e, value=item["value"]: on_select(value))

            self.selector_canvas.update_idletasks()
            self.selector_canvas.configure(scrollregion=self.selector_canvas.bbox("all"))
            self.selector_canvas.yview_moveto(0)

        self.selector_rebuild_callback = rebuild
        self.selector_filter_var.trace_add("write", rebuild)
        rebuild()
        try:
            self.selector_popup.grab_set()
        except Exception:
            pass

    def open_language_selector(self):
        self._build_selector_popup("언어 선택", self.lang_selector_btn, width=480, max_height=380)
        self._populate_selector_popup(self._language_selector_items(), self.current_lang_code(), self._set_language)

    def open_model_selector(self):
        self._build_selector_popup("모델 선택", self.model_selector_btn, width=700, max_height=420)
        self._populate_selector_popup(self._model_selector_items(), self.current_model_id(), self._set_model)

    def open_preset_selector(self):
        self._build_selector_popup("전사 프리셋 선택", self.preset_selector_btn, width=700, max_height=420)
        self._populate_selector_popup(self._preset_selector_items(), self.current_preset_id(), self._set_preset)

    def _selector_title_font(self, item: dict):
        return self.selector_title_fonts.get(item.get("font_key", "default"), self.font_body_bold)

    def _selector_item_palette(self, item: dict, selected: bool = False, hovered: bool = False) -> dict:
        level = item.get("status_level", "info")

        if level == "success":
            bg = "#ECFDF3" if selected else ("#F0FDF4" if hovered else self.colors["card"])
            border = "#34D399" if selected else ("#86EFAC" if hovered else self.colors["border"])
            badge_bg = "#DCFCE7"
            badge_fg = "#166534"
            subtext_fg = "#4B5563"
        elif level == "warning":
            bg = "#FFF7ED" if selected else ("#FFFBEB" if hovered else self.colors["card"])
            border = "#FB923C" if selected else ("#FCD34D" if hovered else self.colors["border"])
            badge_bg = "#FED7AA"
            badge_fg = "#9A3412"
            subtext_fg = "#4B5563"
        elif level == "danger":
            bg = "#FEF2F2" if selected else ("#FEF2F2" if hovered else self.colors["card"])
            border = "#F87171" if selected else ("#FCA5A5" if hovered else self.colors["border"])
            badge_bg = "#FEE2E2"
            badge_fg = "#991B1B"
            subtext_fg = "#4B5563"
        else:
            bg = "#EFF6FF" if selected else ("#F8FAFC" if hovered else self.colors["card"])
            border = self.colors["accent"] if selected else self.colors["border"]
            badge_bg = "#BFDBFE" if selected else "#DBEAFE"
            badge_fg = "#1D4ED8"
            subtext_fg = self.colors["subtext"]

        return {
            "bg": bg,
            "border": border,
            "badge_bg": badge_bg,
            "badge_fg": badge_fg,
            "subtext_fg": subtext_fg,
        }
    
    def _selector_subtitle_font(self, item: dict):
        return self.font_small

    def _selected_language_font(self):
        return self.font_body

    def _refresh_language_button_font(self):
        try:
            self.lang_selector_btn.configure(font=self._selected_language_font())
        except Exception:
            pass

    def _format_display_path(self, path: str | None, empty: str = "알 수 없음") -> str:
        if not path:
            return empty
        try:
            info = inspect_model_availability(self.current_model_id(), include_remote_meta=False)
            if os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(info.get("cached_path", ""))):
                return info.get("cached_path_display") or path
        except Exception:
            pass
        from env_manager import compact_path_for_display
        try:
            return compact_path_for_display(path, keep_tail=4)
        except Exception:
            return path

    def _preset_detail_rows(self, preset: dict) -> list[tuple[str, str]]:
        return [
            ("프리셋", preset["label"]),
            ("설명", preset["long_note"]),
            ("Beam size", str(preset.get("beam_size", "-"))),
            ("VAD 최소 발화", f"{preset.get('vad_min_speech_ms', '-')} ms"),
            ("VAD 최소 무음", f"{preset.get('vad_min_silence_ms', '-')} ms"),
            ("Temperature", ", ".join(str(v) for v in preset.get("temperature", [])) or "-"),
            ("이전 문맥 사용", "예" if preset.get("condition_on_previous_text") else "아니오"),
            ("반복 억제", str(preset.get("repetition_penalty", "-"))),
            ("로그 확률 임계값", str(preset.get("log_prob_threshold", "-"))),
            ("압축비 임계값", str(preset.get("compression_ratio_threshold", "-"))),
            ("단어 타임스탬프", "사용" if preset.get("word_timestamps") else "사용 안 함"),
        ]

    def show_preset_details(self):
        preset = get_transcription_preset(self.current_preset_id())

        dialog = tk.Toplevel(self)
        dialog.title("프리셋 상세")
        dialog.transient(self)
        dialog.configure(bg=self.colors["bg"])
        dialog.geometry("640x540")
        dialog.minsize(560, 460)

        outer = tk.Frame(dialog, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(
            outer,
            text=f"프리셋 상세 · {preset['label']}",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=self.font_heading,
            anchor="w",
        ).pack(anchor="w")

        tk.Label(
            outer,
            text="프리셋은 입력 파일 성격에 맞춰 Whisper 옵션을 묶어 둔 실행 프로파일입니다. 파라미터를 조정하지 않아도 용도별로 속도와 품질 균형을 빠르게 선택할 수 있습니다.",
            bg=self.colors["bg"],
            fg=self.colors["subtext"],
            font=self.font_small,
            justify="left",
            wraplength=600,
            anchor="w",
        ).pack(anchor="w", pady=(6, 12))

        card = tk.Frame(
            outer,
            bg=self.colors["card"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )
        card.pack(fill="both", expand=True)

        canvas = tk.Canvas(card, bg=self.colors["card"], highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(card, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scroll.set)

        inner = tk.Frame(canvas, bg=self.colors["card"])
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))

        for idx, (label_text, value_text) in enumerate(self._preset_detail_rows(preset)):
            row = tk.Frame(inner, bg=self.colors["card"], padx=14, pady=10)
            row.pack(fill="x")
            if idx < len(self._preset_detail_rows(preset)) - 1:
                tk.Frame(inner, bg=self.colors["border"], height=1).pack(fill="x", padx=14)
            tk.Label(
                row,
                text=label_text,
                bg=self.colors["card"],
                fg=self.colors["subtext"],
                font=self.font_small,
                width=14,
                anchor="nw",
            ).pack(side="left")
            tk.Label(
                row,
                text=value_text,
                bg=self.colors["card"],
                fg=self.colors["text"],
                font=self.font_body,
                justify="left",
                wraplength=430,
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

        footer = tk.Frame(outer, bg=self.colors["bg"])
        footer.pack(fill="x", pady=(12, 0))
        self._make_button(footer, "닫기", dialog.destroy, kind="primary").pack(side="right")

    # -------------------------------------------------
    # Build sections
    # -------------------------------------------------
    def _build_top_header(self, parent):
        header = tk.Frame(parent, bg=self.colors["bg"])
        header.pack(fill="x", pady=(16, 14), padx=18)

        left = tk.Frame(header, bg=self.colors["bg"])
        left.pack(side="left", fill="x", expand=True)

        tk.Label(
            left,
            text=APP_NAME,
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=self.font_title,
            anchor="w",
        ).pack(anchor="w")

        tk.Label(
            left,
            text=APP_TAGLINE,
            bg=self.colors["bg"],
            fg=self.colors["subtext"],
            font=self.font_body,
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        right = tk.Frame(header, bg=self.colors["bg"])
        right.pack(side="right")

        self.header_badge = tk.Label(
            right,
            text=f"v{APP_VERSION}",
            bg="#DBEAFE",
            fg="#1D4ED8",
            font=self.font_small,
            padx=10,
            pady=6,
        )
        self.header_badge.pack(anchor="e")

    def _build_file_card(self, parent):
        card = self._make_card(parent)
        self._make_section_header(card, "입력 파일", "전사할 오디오 또는 비디오 파일을 선택하십시오. 결과 자막은 원본 파일과 같은 폴더에 저장됩니다.")

        body = tk.Frame(card, bg=self.colors["card"])
        body.pack(fill="x", padx=18, pady=(4, 16))

        self.file_var = tk.StringVar()

        ttk.Entry(body, textvariable=self.file_var, style="Modern.TEntry").pack(
            side="left", fill="x", expand=True, padx=(0, 10), ipady=3
        )
        self._make_button(body, "파일 선택", self.browse_input_file, kind="secondary").pack(side="left")

    def _build_options_card(self, parent):
        card = self._make_card(parent)
        self._make_section_header(card, "전사 설정", "언어, 프리셋, 모델, 장치 선호를 조합해 처리 방식과 품질/속도 균형을 정합니다.")

        body = tk.Frame(card, bg=self.colors["card"])
        body.pack(fill="x", padx=18, pady=(4, 16))

        tk.Label(body, text="언어", bg=self.colors["card"], fg=self.colors["text"], font=self.font_body_bold).grid(
            row=0, column=0, sticky="nw", padx=(0, 12), pady=(8, 4)
        )
        lang_wrap = tk.Frame(body, bg=self.colors["card"])
        lang_wrap.grid(row=0, column=1, sticky="w", padx=(0, 18), pady=(8, 4))
        self.lang_selector_btn = self._make_button(lang_wrap, "", self.open_language_selector, kind="secondary")
        self.lang_selector_btn.configure(textvariable=self.language_display_var, width=22, anchor="w", font=self._selected_language_font())
        self.lang_selector_btn.pack(side="left")
        self._make_button(lang_wrap, "변경", self.open_language_selector, kind="soft").pack(side="left", padx=(8, 0))
        tk.Label(
            body,
            textvariable=self.lang_note_var,
            bg=self.colors["card"],
            fg=self.colors["subtext"],
            font=self.font_small,
            anchor="w",
            justify="left",
            wraplength=760,
        ).grid(row=0, column=2, columnspan=3, sticky="w", pady=(8, 4))

        tk.Label(body, text="프리셋", bg=self.colors["card"], fg=self.colors["text"], font=self.font_body_bold).grid(
            row=1, column=0, sticky="nw", padx=(0, 12), pady=(8, 4)
        )
        preset_wrap = tk.Frame(body, bg=self.colors["card"])
        preset_wrap.grid(row=1, column=1, sticky="w", padx=(0, 18), pady=(8, 4))
        self.preset_selector_btn = self._make_button(preset_wrap, "", self.open_preset_selector, kind="secondary")
        self.preset_selector_btn.configure(textvariable=self.preset_display_var, width=22, anchor="w")
        self.preset_selector_btn.pack(side="left")
        self._make_button(preset_wrap, "변경", self.open_preset_selector, kind="soft").pack(side="left", padx=(8, 0))
        self._make_button(preset_wrap, "값 자세히", self.show_preset_details, kind="soft").pack(side="left", padx=(8, 0))
        tk.Label(
            body,
            textvariable=self.preset_note_var,
            bg=self.colors["card"],
            fg=self.colors["subtext"],
            font=self.font_small,
            anchor="w",
            justify="left",
            wraplength=760,
        ).grid(row=1, column=2, columnspan=3, sticky="w", pady=(8, 4))

        tk.Label(body, text="모델", bg=self.colors["card"], fg=self.colors["text"], font=self.font_body_bold).grid(
            row=2, column=0, sticky="nw", padx=(0, 12), pady=(8, 4)
        )
        model_wrap = tk.Frame(
            body,
            bg="#F8FAFC",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            padx=12,
            pady=10,
        )
        model_wrap.grid(row=2, column=1, sticky="we", padx=(0, 18), pady=(8, 4), columnspan=3)

        text_wrap = tk.Frame(model_wrap, bg="#F8FAFC")
        text_wrap.pack(side="left", fill="x", expand=True)
        top_line = tk.Frame(text_wrap, bg="#F8FAFC")
        top_line.pack(fill="x")

        tk.Label(
            top_line,
            textvariable=self.model_display_var,
            bg="#F8FAFC",
            fg=self.colors["text"],
            font=self.font_body_bold,
            anchor="w",
            justify="left",
        ).pack(side="left", anchor="w")

        self.model_cache_badge = tk.Label(
            top_line,
            text="확인 중",
            bg="#E5E7EB",
            fg="#374151",
            font=self.font_badge,
            padx=8,
            pady=3,
        )
        self.model_cache_badge.pack(side="left", padx=(10, 0))

        tk.Label(
            text_wrap,
            textvariable=self.model_meta_var,
            bg="#F8FAFC",
            fg=self.colors["subtext"],
            font=self.font_small,
            anchor="w",
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(4, 0), fill="x")
        self.model_selector_btn = self._make_button(model_wrap, "모델 선택", self.open_model_selector, kind="soft")
        self.model_selector_btn.pack(side="right", padx=(10, 0))

        action_row = tk.Frame(body, bg=self.colors["card"])
        action_row.grid(row=2, column=4, sticky="e", pady=(8, 4))
        self.model_download_btn = self._make_button(action_row, "모델 다운로드", self.start_model_download, kind="soft")
        self.model_download_btn.pack(side="right")

        tk.Label(
            body,
            textvariable=self.model_state_var,
            bg=self.colors["card"],
            fg=self.colors["subtext"],
            font=self.font_small,
            anchor="w",
            justify="left",
            wraplength=940,
        ).grid(row=3, column=1, columnspan=4, sticky="w", pady=(0, 4))

        tk.Label(body, text="장치 선호", bg=self.colors["card"], fg=self.colors["text"], font=self.font_body_bold).grid(
            row=4, column=0, sticky="nw", padx=(0, 12), pady=(8, 4)
        )
        device_wrap = tk.Frame(body, bg=self.colors["card"])
        device_wrap.grid(row=4, column=1, sticky="w", padx=(0, 18), pady=(8, 4), columnspan=2)

        self.device_buttons = {}
        device_specs = [("auto", "자동"), ("cuda", "GPU"), ("cpu", "CPU")]
        for idx, (value, label_text) in enumerate(device_specs):
            btn = self._make_button(
                device_wrap,
                label_text,
                lambda v=value: self._set_preferred_device(v),
                kind="soft",
                width=10,
            )
            btn.pack(side="left", padx=(0, 8 if idx < len(device_specs) - 1 else 0))
            self.device_buttons[value] = btn

        action_wrap = tk.Frame(body, bg=self.colors["card"])
        action_wrap.grid(row=4, column=3, columnspan=2, sticky="e", pady=(8, 4))
        self._make_button(action_wrap, "시스템 점검", self.start_system_check, kind="soft").pack(side="left", padx=(0, 8))
        self._make_button(action_wrap, "설정 저장", self.save_ui_settings, kind="secondary").pack(side="left")

        tk.Label(
            body,
            textvariable=self.device_note_var,
            bg=self.colors["card"],
            fg=self.colors["subtext"],
            font=self.font_small,
            anchor="w",
            justify="left",
            wraplength=940,
        ).grid(row=5, column=1, columnspan=4, sticky="w", pady=(0, 4))

        body.columnconfigure(1, weight=0)
        body.columnconfigure(2, weight=0)
        body.columnconfigure(3, weight=1)
        body.columnconfigure(4, weight=0)

    def _build_status_card(self, parent):
        card = self._make_card(parent)
        self._make_section_header(card, "시스템 준비 상태", "전사를 위해 필요한 환경이 구축되었는지 확인합니다. 자세한 정보는 세부 상태 및 상태보고서를 참고하십시오.")

        action_row = tk.Frame(card, bg=self.colors["card"])
        action_row.pack(fill="x", padx=18, pady=(0, 10))

        self.status_toggle_btn = self._make_button(action_row, "▼ 세부 상태", self.toggle_status_expand, kind="soft")
        self.status_toggle_btn.pack(side="left")
        self._make_button(action_row, "새로고침", self.start_system_check, kind="soft").pack(side="right")
        self._make_button(action_row, "상태 보고서", self.show_status_details, kind="soft").pack(side="right", padx=(0, 8))

        tiles = tk.Frame(card, bg=self.colors["card"])
        tiles.pack(fill="x", padx=18, pady=(0, 10))

        self.status_rows = {}
        rows = [
            ("model", "모델"),
            ("engine", "엔진"),
            ("torch", "PyTorch"),
            ("device", "장치"),
            ("runtime", "실행 조합"),
        ]

        for col, (key, label_text) in enumerate(rows):
            tile = tk.Frame(
                tiles,
                bg="#F8FAFC",
                highlightthickness=1,
                highlightbackground=self.colors["border"],
                padx=12,
                pady=10,
            )
            tile.grid(row=0, column=col, sticky="nsew", padx=(0, 8 if col < len(rows) - 1 else 0))
            tiles.columnconfigure(col, weight=1)

            head = tk.Frame(tile, bg="#F8FAFC")
            head.pack(fill="x")

            tk.Label(
                head,
                text=label_text,
                bg="#F8FAFC",
                fg=self.colors["subtext"],
                font=self.font_small,
                anchor="w",
            ).pack(side="left", anchor="w")

            badge = tk.Label(
                head,
                text="대기",
                bg="#E5E7EB",
                fg="#374151",
                font=self.font_badge,
                padx=10,
                pady=4,
            )
            badge.pack(side="right")

            summary_var = tk.StringVar(value="상태를 확인하는 중입니다.")
            meta_var = tk.StringVar(value="")

            tk.Label(
                tile,
                textvariable=summary_var,
                bg="#F8FAFC",
                fg=self.colors["text"],
                font=self.font_body,
                justify="left",
                wraplength=210,
                anchor="w",
            ).pack(anchor="w", fill="x", pady=(10, 4))

            tk.Label(
                tile,
                textvariable=meta_var,
                bg="#F8FAFC",
                fg=self.colors["subtext"],
                font=self.font_small,
                justify="left",
                wraplength=210,
                anchor="w",
            ).pack(anchor="w", fill="x")

            self.status_rows[key] = {"badge": badge, "summary_var": summary_var, "meta_var": meta_var}

        self.status_detail_panel = tk.Frame(
            card,
            bg="#F8FAFC",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )

        self.status_detail_labels = {}
        for idx, (key, label_text) in enumerate(rows):
            row = tk.Frame(self.status_detail_panel, bg="#F8FAFC")
            row.pack(fill="x", padx=16, pady=(12 if idx == 0 else 4, 4))

            tk.Label(
                row,
                text=label_text,
                bg="#F8FAFC",
                fg=self.colors["text"],
                font=self.font_body_bold,
                width=10,
                anchor="nw",
                justify="left",
            ).pack(side="left", anchor="n", padx=(0, 12))

            content = tk.Frame(row, bg="#F8FAFC")
            content.pack(side="left", fill="x", expand=True)

            summary_var = tk.StringVar(value="확인 중입니다.")
            detail_var = tk.StringVar(value="")

            tk.Label(
                content,
                textvariable=summary_var,
                bg="#F8FAFC",
                fg=self.colors["text"],
                font=self.font_body,
                anchor="w",
                justify="left",
                wraplength=900,
            ).pack(anchor="w")

            tk.Label(
                content,
                textvariable=detail_var,
                bg="#F8FAFC",
                fg=self.colors["subtext"],
                font=self.font_small,
                anchor="w",
                justify="left",
                wraplength=900,
            ).pack(anchor="w", pady=(2, 0))

            self.status_detail_labels[key] = {
                "summary_var": summary_var,
                "detail_var": detail_var,
            }

        self._update_status_row("model", "neutral", "모델 상태를 확인하는 중입니다.", "앱 캐시와 원격 준비 상태를 점검합니다.")
        self._update_status_row("engine", "neutral", "전사 엔진 상태를 확인하는 중입니다.", "CTranslate2와 미디어 입력 경로를 점검합니다.")
        self._update_status_row("torch", "neutral", "실행 라이브러리 상태를 확인하는 중입니다.", "현재 설치된 PyTorch 런타임을 점검합니다.")
        self._update_status_row("device", "neutral", "연산 장치 상태를 확인하는 중입니다.", "GPU 감지 여부와 선호 장치를 함께 봅니다.")
        self._update_status_row("runtime", "neutral", "실행 조합을 아직 검증하지 않았습니다.", "장치 점검 또는 첫 실행 때 실제 조합을 확정합니다.")
        self._apply_status_expand_state()

    def _build_log(self, parent):
        card = self._make_card(parent)
        self._make_section_header(card, "작업 로그", "다운로드, 실행 준비, 전사 진행, 저장 결과를 시간순으로 기록합니다.")

        body = tk.Frame(card, bg=self.colors["card"])
        body.pack(fill="both", expand=True, padx=18, pady=(4, 16))

        log_wrap = tk.Frame(
            body,
            bg=self.colors["log_bg"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )
        log_wrap.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_wrap,
            wrap="word",
            bg=self.colors["log_bg"],
            fg=self.colors["log_fg"],
            insertbackground=self.colors["text"],
            selectbackground="#DBEAFE",
            relief="flat",
            bd=0,
            font=self.font_body,
            padx=12,
            pady=12,
            height=15,
        )
        self.log_text.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.tag_configure("timestamp", font=self.font_mono, foreground=self.colors["accent"])
        self.log_text.tag_configure("message_ascii", font=self.font_mono, foreground=self.colors["log_fg"])
        self.log_text.tag_configure("message_text", font=self.font_body, foreground=self.colors["log_fg"])

    def _build_bottom_dock(self):
        dock = tk.Frame(
            self,
            bg=self.colors["dock"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )
        dock.pack(side="bottom", fill="x")
        self.bottom_dock = dock

        top = tk.Frame(dock, bg=self.colors["dock"])
        top.pack(fill="x", padx=18, pady=(12, 8))

        actions = tk.Frame(top, bg=self.colors["dock"])
        actions.pack(side="left")

        self.start_btn = self._make_button(actions, "전사 시작", self.start_transcription, kind="primary")
        self.start_btn.pack(side="left", padx=(0, 10))

        self.cancel_btn = self._make_button(actions, "취소", self._cancel_current_task, kind="soft", state="disabled")
        self.cancel_btn.pack(side="left", padx=(0, 10))

        self.open_result_btn = self._make_button(
            actions, "결과 열기", self.open_result, kind="secondary", state="disabled"
        )
        self.open_result_btn.pack(side="left", padx=(0, 8))

        self.open_folder_btn = self._make_button(
            actions, "폴더 열기", self.open_result_folder, kind="secondary", state="disabled"
        )
        self.open_folder_btn.pack(side="left")

        resource = tk.Frame(
            top,
            bg="#FFFFFF",
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            padx=12,
            pady=10,
        )
        resource.pack(side="right", fill="x", expand=True, padx=(16, 0))

        resource_head = tk.Frame(resource, bg="#FFFFFF")
        resource_head.pack(fill="x")

        tk.Label(
            resource_head,
            text="작업 중 자원 상태",
            bg="#FFFFFF",
            fg=self.colors["text"],
            font=self.font_body_bold,
        ).pack(side="left")

        self.resource_badge = tk.Label(
            resource_head,
            text="점검 중",
            bg="#E5E7EB",
            fg="#374151",
            font=self.font_badge,
            padx=8,
            pady=3,
        )
        self.resource_badge.pack(side="left", padx=(10, 0))

        self._make_button(resource_head, "새로고침", self.refresh_live_resource_now, kind="soft").pack(side="right")

        tk.Label(
            resource,
            textvariable=self.resource_summary_var,
            bg="#FFFFFF",
            fg=self.colors["text"],
            font=self.font_small,
            anchor="w",
            justify="left",
            wraplength=820,
        ).pack(anchor="w", fill="x", pady=(8, 2))

        tk.Label(
            resource,
            textvariable=self.resource_meta_var,
            bg="#FFFFFF",
            fg=self.colors["subtext"],
            font=self.font_small,
            anchor="w",
            justify="left",
        ).pack(anchor="w", fill="x")

        status_row = tk.Frame(dock, bg=self.colors["dock"])
        status_row.pack(fill="x", padx=18, pady=(0, 6))

        tk.Label(
            status_row,
            textvariable=self.status_var,
            bg=self.colors["dock"],
            fg=self.colors["subtext"],
            font=self.font_body,
            anchor="w",
        ).pack(anchor="w")

        self.transfer_var = tk.StringVar(value="")
        self.transfer_meta_var = tk.StringVar(value="")
        transfer_row = tk.Frame(dock, bg=self.colors["dock"])
        transfer_row.pack(fill="x", padx=18, pady=(0, 6))

        tk.Label(
            transfer_row,
            textvariable=self.transfer_var,
            bg=self.colors["dock"],
            fg=self.colors["text"],
            font=self.font_emphasis,
            anchor="w",
            justify="left",
        ).pack(anchor="w")
        tk.Label(
            transfer_row,
            textvariable=self.transfer_meta_var,
            bg=self.colors["dock"],
            fg=self.colors["subtext"],
            font=self.font_small,
            anchor="w",
            justify="left",
        ).pack(anchor="w")

        self.job_meta_var = tk.StringVar(value="")
        tk.Label(
            transfer_row,
            textvariable=self.job_meta_var,
            bg=self.colors["dock"],
            fg=self.colors["subtext"],
            font=self.font_small,
            anchor="w",
            justify="left",
        ).pack(anchor="w")

        progress_row = tk.Frame(dock, bg=self.colors["dock"])
        progress_row.pack(fill="x", padx=18, pady=(0, 14))

        self.progress = ttk.Progressbar(
            progress_row,
            style="Modern.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
        )
        self.progress.pack(fill="x")

    # -------------------------------------------------
    # Small UI state helpers
    # -------------------------------------------------
    def _refresh_device_buttons(self):
        if not hasattr(self, "device_buttons"):
            return
        current = self.current_preferred_device()
        for key, btn in self.device_buttons.items():
            if key == current:
                btn.configure(bg=self.colors["accent"], fg="#FFFFFF", activebackground=self.colors["accent_hover"], activeforeground="#FFFFFF")
            else:
                btn.configure(bg="#F1F5F9", fg=self.colors["text"], activebackground="#E2E8F0", activeforeground=self.colors["text"])

    def _level_to_badge_text(self, level: str) -> str:
        return {
            "success": "정상",
            "warning": "주의",
            "danger": "오류",
            "info": "안내",
            "neutral": "대기",
        }.get(level, "안내")

    def _update_status_row(self, key, level, text, meta_text=""):
        badge_text = self._level_to_badge_text(level)
        detail_text = self.status_details.get(key, "")
        self.status_meta[key] = meta_text or self.status_meta.get(key, "")
        if key in self.status_rows:
            self._set_badge(self.status_rows[key]["badge"], badge_text, level)
            self.status_rows[key]["summary_var"].set(text)
            self.status_rows[key]["meta_var"].set(self.status_meta.get(key, ""))
        if key in self.status_detail_labels:
            self.status_detail_labels[key]["summary_var"].set(text)
            self.status_detail_labels[key]["detail_var"].set(detail_text)

    def toggle_status_expand(self):
        self.status_expanded = not self.status_expanded
        self._apply_status_expand_state()

    def _apply_status_expand_state(self):
        if self.status_expanded:
            self.status_detail_panel.pack(fill="x", padx=18, pady=(0, 16))
            self.status_toggle_btn.configure(text="▲ 세부 상태 접기")
        else:
            self.status_detail_panel.pack_forget()
            self.status_toggle_btn.configure(text="▼ 세부 상태 보기")

    # -------------------------------------------------
    # Model availability / resource UI
    # -------------------------------------------------
    def _refresh_model_state_local(self):
        try:
            info = inspect_model_availability(self.current_model_id(), include_remote_meta=False)
            self._apply_model_availability(info)
        except Exception:
            self._set_badge(self.model_cache_badge, "확인 실패", "warning")
            self.model_state_var.set("선택 모델의 로컬 준비 상태를 확인하지 못했습니다.")
            self.model_download_btn.configure(text="모델 다운로드", state="normal")

    def _apply_model_availability(self, info: dict):
        if info.get("is_cached"):
            self._set_badge(self.model_cache_badge, "로컬 준비됨", "success")
            display_path = info.get("cached_path_display") or self._format_display_path(info.get("cached_path"), empty="앱 내부 캐시")
            self.model_state_var.set(
                "선택 모델이 이미 준비되어 있습니다. 다운로드 없이 바로 사용할 수 있습니다.\n"
                f"캐시 위치: {display_path}"
            )
            self.model_download_btn.configure(text="모델 준비됨", state="disabled")
        else:
            self._set_badge(self.model_cache_badge, "다운로드 필요", "warning")
            self.model_state_var.set("선택 모델이 아직 로컬에 없습니다. 지금 수동으로 받거나, 실행 시 필요한 시점에 자동으로 받을 수 있습니다.")
            self.model_download_btn.configure(text="모델 다운로드", state="normal")

    def _update_live_resource_ui(self, info: dict):
        self.live_resource_data = dict(info)
        self._set_badge(self.resource_badge, info.get("pressure_label", "정보 없음"), info.get("level", "neutral"))

        summary = (
            f"앱 CPU {info.get('app_cpu_text', '정보 없음')} · "
            f"앱 RAM {info.get('app_ram_text', '정보 없음')} · "
            f"시스템 CPU {info.get('system_cpu_text', '정보 없음')} · "
            f"시스템 RAM {info.get('ram_text', '정보 없음')} · "
            f"VRAM {info.get('vram_text', '정보 없음')}"
        )
        meta = f"GPU {info.get('gpu_name', '감지되지 않음')} · 마지막 갱신 {info.get('timestamp_text', '-')}"
        self.resource_summary_var.set(summary)
        self.resource_meta_var.set(meta)

    def refresh_live_resource_now(self):
        try:
            self._update_live_resource_ui(collect_live_resource_status())
        except Exception:
            pass

        if self.resource_refresh_job is not None:
            try:
                self.after_cancel(self.resource_refresh_job)
            except Exception:
                pass
        self.resource_refresh_job = self.after(2000, self.refresh_live_resource_now)

    def _update_download_transfer_ui(self, payload: dict):
        self.transfer_mode = "download"
        percent = payload.get("percent")
        if percent is not None:
            try:
                percent_value = max(0.0, min(100.0, float(percent)))
                self.current_progress_percent = percent_value
                self.progress.configure(mode="determinate")
                self.progress["value"] = percent_value
            except Exception:
                pass

        headline = payload.get("message") or "다운로드 진행 중"
        speed = payload.get("speed_text", "알 수 없음")
        eta = payload.get("eta_text", "알 수 없음")
        downloaded = payload.get("downloaded_text", "알 수 없음")
        total = payload.get("total_text", "알 수 없음")
        self.transfer_var.set(f"모델 다운로드 · {downloaded} / {total}")
        self.transfer_meta_var.set(f"현재 속도 {speed} · 남은 시간 {eta} · {headline}")

    def _clear_download_transfer_ui(self):
        self.transfer_mode = ""
        self.transfer_var.set("")
        self.transfer_meta_var.set("")

    def _apply_runtime_choice_cards(self, chosen: dict):
        device = chosen.get("device", "-")
        compute_type = chosen.get("compute_type", "-")
        load_id = chosen.get("load_id", "-")
        reason = chosen.get("reason", "")
        pref = self.current_preferred_device()
        live = self.live_resource_data or {}
        gpu_name = live.get("gpu_name", "감지되지 않음")
        base_engine = self.base_status_details.get("engine", self.status_details.get("engine", ""))
        base_torch = self.base_status_details.get("torch", self.status_details.get("torch", ""))

        self.status_details["runtime"] = (
            f"확정된 실행 조합: {device} / {compute_type}\n"
            f"판정 사유: {reason}\n"
            f"실제 로딩 대상: {load_id}"
        )
        self._update_status_row("runtime", "success", f"검증 완료 · {device} / {compute_type}", f"장치 선호 {pref} · 실제 로딩 {load_id}")

        if device == "cpu":
            self.status_details["device"] = (
                f"선호 장치: {pref}\n"
                f"실제 사용 장치: CPU\n"
                f"감지된 GPU: {gpu_name}\n"
                f"설명: 이번 실행은 CPU 경로로 고정되었습니다."
            )
            self._update_status_row("device", "info", "CPU 실행", f"감지 GPU: {gpu_name} · 현재 실행에서는 사용하지 않음")
            self.status_details["torch"] = base_torch + f"\n실제 사용 경로: CPU / {compute_type}"
            self._update_status_row("torch", "info", "PyTorch 준비 상태", f"현재 실행 경로: CPU / {compute_type}")
            self.status_details["engine"] = base_engine + f"\n실제 사용 경로: CPU / {compute_type}"
            self._update_status_row("engine", "success", "CTranslate2 준비 완료", f"현재 실행 경로: CPU / {compute_type}")
        else:
            self.status_details["device"] = (
                f"선호 장치: {pref}\n"
                f"실제 사용 장치: {gpu_name}\n"
                f"실제 추론 형식: {compute_type}\n"
                f"설명: GPU 경로 검증이 완료되었습니다."
            )
            self._update_status_row("device", "success", gpu_name, f"현재 실행 경로: GPU / {compute_type}")
            self.status_details["torch"] = base_torch + f"\n실제 사용 경로: CUDA / {compute_type}"
            self._update_status_row("torch", "success", "PyTorch 준비 상태", f"현재 실행 경로: CUDA / {compute_type}")
            self.status_details["engine"] = base_engine + f"\n실제 사용 경로: CUDA / {compute_type}"
            self._update_status_row("engine", "success", "CTranslate2 준비 완료", f"현재 실행 경로: CUDA / {compute_type}")

    # -------------------------------------------------
    # Settings / file actions
    # -------------------------------------------------
    def _load_settings_into_ui(self):
        lang_code = self.settings.get("language", DEFAULT_LANGUAGE)
        self.language_value_var.set(lang_code if lang_code in {code for code, _ in LANGUAGE_OPTIONS} else DEFAULT_LANGUAGE)

        model_id = self.settings.get("model_id", default_model_id())
        self.model_value_var.set(model_id if model_id in {m["id"] for m in MODEL_CATALOG} else default_model_id())

        preset_id = self.settings.get("preset_id", DEFAULT_PRESET_ID)
        self.preset_value_var.set(preset_id if preset_id in {preset["id"] for preset in TRANSCRIPTION_PRESETS} else DEFAULT_PRESET_ID)

        pref = self.settings.get("preferred_device", DEFAULT_PREFERRED_DEVICE)
        self.pref_device_value_var.set(pref if pref in {"auto", "cuda", "cpu"} else DEFAULT_PREFERRED_DEVICE)

    def save_ui_settings(self):
        self.settings["language"] = self.current_lang_code()
        self.settings["model_id"] = self.current_model_id()
        self.settings["preset_id"] = self.current_preset_id()
        self.settings["preferred_device"] = self.current_preferred_device()
        save_settings(self.settings)
        self._append_log("환경 설정을 저장했습니다.")
        self._refresh_model_state_local()

    def browse_input_file(self):
        path = filedialog.askopenfilename(
            title="입력 파일 선택",
            filetypes=[
                ("Media Files", "*.mkv *.mp4 *.mov *.avi *.mp3 *.wav *.m4a *.flac *.ogg *.webm"),
                ("All Files", "*.*"),
            ],
        )
        if path:
            self.file_var.set(path)

    # -------------------------------------------------
    # Status detail window
    # -------------------------------------------------
    def show_status_details(self):
        detail_win = tk.Toplevel(self)
        detail_win.title("상태 보고서")
        detail_win.geometry("920x720")
        detail_win.minsize(760, 560)
        detail_win.configure(bg=self.colors["bg"])

        outer = tk.Frame(detail_win, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        header = tk.Frame(outer, bg=self.colors["bg"])
        header.pack(fill="x", pady=(0, 12))

        tk.Label(
            header,
            text="상태 보고서",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=self.font_heading,
        ).pack(anchor="w")

        tk.Label(
            header,
            text="실행 환경, 모델 준비 상태, 장치 판정, 현재 자원 사용량을 한 번에 확인할 수 있습니다. 일부 오류가 있을 수 있으니 참조용으로만 활용하시기 바랍니다.",
            bg=self.colors["bg"],
            fg=self.colors["subtext"],
            font=self.font_small,
            justify="left",
            wraplength=860,
        ).pack(anchor="w", pady=(4, 0))

        action_row = tk.Frame(outer, bg=self.colors["bg"])
        action_row.pack(fill="x", pady=(0, 10))
        self._make_button(action_row, "새로고침", lambda: (self.start_startup_scan(), self.refresh_live_resource_now()), kind="soft").pack(side="right")

        wrap = tk.Frame(
            outer,
            bg=self.colors["card"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
        )
        wrap.pack(fill="both", expand=True)

        text = tk.Text(
            wrap,
            wrap="word",
            relief="flat",
            bd=0,
            font=self.font_body,
            padx=16,
            pady=16,
            bg=self.colors["card"],
            fg=self.colors["text"],
        )
        text.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)

        live = self.live_resource_data or {}
        live_block = (
            f"실시간 부하 상태: {live.get('pressure_label', '정보 없음')}\n"
            f"경고 요약: {live.get('alert_text', '정보 없음')}\n"
            f"앱 CPU: {live.get('app_cpu_text', '정보 없음')}\n"
            f"시스템 CPU: {live.get('system_cpu_text', '정보 없음')}\n"
            f"앱 RAM: {live.get('app_ram_text', '정보 없음')}\n"
            f"RAM: {live.get('ram_text', '정보 없음')}\n"
            f"VRAM: {live.get('vram_text', '정보 없음')}\n"
            f"장치: {live.get('gpu_name', '정보 없음')}\n"
            f"마지막 갱신: {live.get('timestamp_text', '정보 없음')}"
        )

        preset = get_transcription_preset(self.current_preset_id())
        sections = [
            ("현재 전사 프리셋", f"선택 프리셋: {preset['label']}\n설명: {preset['long_note']}"),
            ("작업 중 자원 상태", live_block),
            ("모델", self.status_details.get("model", "정보 없음")),
            ("엔진", self.status_details.get("engine", "정보 없음")),
            ("PyTorch", self.status_details.get("torch", "정보 없음")),
            ("장치", self.status_details.get("device", "정보 없음")),
            ("시스템 자원", self.status_details.get("resources", "정보 없음")),
            ("실행 조합", self.status_details.get("runtime", "정보 없음")),
        ]

        for title, body in sections:
            text.insert("end", f"{title}\n", ("heading",))
            text.insert("end", f"{body}\n\n")

        text.tag_configure("heading", font=self.font_heading, foreground=self.colors["text"])
        text.configure(state="disabled")

    # -------------------------------------------------
    # Download confirmation dialog
    # -------------------------------------------------
    def _show_model_download_dialog(self, info: dict, reason: str) -> bool:
        dialog = tk.Toplevel(self)
        dialog.title("모델 다운로드 확인")
        dialog.transient(self)
        dialog.grab_set()
        dialog.configure(bg=self.colors["bg"])
        dialog.geometry("660x520")
        dialog.resizable(False, False)

        approved = {"value": False}

        outer = tk.Frame(dialog, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(
            outer,
            text="선택 모델 다운로드",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=self.font_heading,
            anchor="w",
        ).pack(anchor="w")

        tk.Label(
            outer,
            text=reason,
            bg=self.colors["bg"],
            fg=self.colors["subtext"],
            font=self.font_body,
            anchor="w",
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(6, 12))

        card = tk.Frame(
            outer,
            bg=self.colors["card"],
            highlightthickness=1,
            highlightbackground=self.colors["border"],
            padx=16,
            pady=14,
        )
        card.pack(fill="both", expand=True)

        rows = [
            ("모델", info.get("label", "알 수 없음")),
            ("설명", info.get("long_note", "알 수 없음")),
            ("원본", info.get("download_source", "알 수 없음")),
            ("저장 위치", info.get("download_target_display") or self._format_display_path(info.get("download_target"))),
            ("예상 크기", info.get("remote_size_text", "알 수 없음")),
            ("100 Mb/s", info.get("eta_100", "알 수 없음")),
            ("500 Mb/s", info.get("eta_500", "알 수 없음")),
            ("1 Gb/s", info.get("eta_1000", "알 수 없음")),
        ]

        for idx, (label_text, value_text) in enumerate(rows):
            row = tk.Frame(card, bg=self.colors["card"])
            row.pack(fill="x", pady=(0, 8 if idx < len(rows) - 1 else 0))
            tk.Label(row, text=label_text, bg=self.colors["card"], fg=self.colors["subtext"], font=self.font_small, width=10, anchor="nw").pack(side="left")
            tk.Label(row, text=value_text, bg=self.colors["card"], fg=self.colors["text"], font=self.font_small, justify="left", wraplength=460, anchor="w").pack(side="left", fill="x", expand=True)

        speed_var = tk.StringVar(value="초기 연결 속도를 짧게 측정하는 중입니다.")
        speed_row = tk.Frame(card, bg=self.colors["card"])
        speed_row.pack(fill="x", pady=(10, 0))
        tk.Label(speed_row, text="현재 회선", bg=self.colors["card"], fg=self.colors["subtext"], font=self.font_small, width=10, anchor="nw").pack(side="left")

        speed_value_wrap = tk.Frame(speed_row, bg=self.colors["card"])
        speed_value_wrap.pack(side="left", fill="x", expand=True)
        tk.Label(
            speed_value_wrap,
            textvariable=speed_var,
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=self.font_small,
            justify="left",
            wraplength=330,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        probe_btn = self._make_button(speed_row, "다시 측정", lambda: None, kind="soft")
        probe_btn.pack(side="right")

        def start_speed_probe():
            probe_btn.configure(state="disabled", text="측정 중…")
            speed_var.set("Hugging Face 연결 속도를 측정하는 중입니다.")

            def worker():
                result = probe_repo_download_speed(
                    info.get("repo_id", ""),
                    total_bytes=info.get("remote_size_bytes"),
                )

                def apply_result():
                    if not dialog.winfo_exists():
                        return
                    if result.get("ok"):
                        self.last_measured_speed_mbps = result.get("speed_mbps")
                        self.last_measured_repo_id = info.get("repo_id", "")
                        speed_var.set(
                            f"대략 {result.get('speed_text', '알 수 없음')} · 현재 회선 기준 예상 {result.get('eta_text', '알 수 없음')}\n{result.get('message', '')}"
                        )
                    else:
                        self.last_measured_speed_mbps = None
                        self.last_measured_repo_id = ""
                        speed_var.set(f"속도 측정 실패 · {result.get('message', '알 수 없음')}")
                    probe_btn.configure(state="normal", text="다시 측정")

                dialog.after(0, apply_result)

            threading.Thread(target=worker, daemon=True).start()

        probe_btn.configure(command=start_speed_probe)
        dialog.after(120, start_speed_probe)

        footer = tk.Frame(outer, bg=self.colors["bg"])
        footer.pack(fill="x", pady=(12, 0))

        def approve():
            approved["value"] = True
            dialog.destroy()

        def reject():
            approved["value"] = False
            dialog.destroy()

        self._make_button(footer, "취소", reject, kind="soft").pack(side="right")
        self._make_button(footer, "다운로드 시작", approve, kind="primary").pack(side="right", padx=(0, 8))

        dialog.protocol("WM_DELETE_WINDOW", reject)
        self.wait_window(dialog)
        return approved["value"]

    # -------------------------------------------------
    # Startup scan / model readiness
    # -------------------------------------------------
    def start_startup_scan(self):
        self.start_system_check()

    def start_system_check(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        scan_settings = dict(self.settings)
        scan_settings["language"] = self.current_lang_code()
        scan_settings["model_id"] = self.current_model_id()
        scan_settings["preset_id"] = self.current_preset_id()
        scan_settings["preferred_device"] = self.current_preferred_device()

        self._set_status("시스템 상태를 점검하는 중입니다...")
        self.transfer_var.set(f"시스템 점검 · 모델 {self.model_display_var.get()} · 장치 선호 {self.current_preferred_device().upper()}")
        self.transfer_meta_var.set("환경, 엔진, 장치, 실행 조합을 한 번에 점검합니다.")
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
        except Exception as e:
            tb = traceback.format_exc()
            self.msg_queue.put(("log", f"시스템 상태 점검 실패\n{e}\n{tb}"))
            self.msg_queue.put(("status", "시스템 상태 점검에 실패했습니다."))
        finally:
            self.msg_queue.put(("task_finished", "system_check"))

    def _request_model_download_permission(self, info: dict, reason: str) -> bool:
        ticket = {
            "info": info,
            "reason": reason,
            "approved": False,
            "event": threading.Event(),
        }
        self.msg_queue.put(("ask_model_download", ticket))
        ticket["event"].wait()
        return bool(ticket.get("approved"))

    def _ensure_model_ready(self, model_id: str, reason: str, log) -> dict:
        info = inspect_model_availability(model_id, include_remote_meta=True)
        self.msg_queue.put(("model_availability", info))
        if info["is_cached"]:
            return info

        approved = self._request_model_download_permission(info, reason)
        if not approved:
            raise RuntimeError("MODEL_DOWNLOAD_CANCELLED")
        return info

    # -------------------------------------------------
    # Model download
    # -------------------------------------------------
    def start_model_download(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        model_id = self.current_model_id()
        self._set_status(f"모델 준비 상태를 확인하는 중입니다... ({model_id})")
        self.progress["value"] = 0
        self._clear_download_transfer_ui()
        self.transfer_var.set(f"모델 다운로드 · {self.model_display_var.get()}")
        self.transfer_meta_var.set("다운로드 준비를 마치는 중입니다.")
        self._begin_task("model_download", "모델 다운로드", cancellable=True)

        self.worker_thread = threading.Thread(
            target=self._worker_model_download,
            args=(model_id,),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker_model_download(self, model_id: str):
        def log(msg: str):
            self.msg_queue.put(("log", msg))

        def download_progress(payload: dict):
            self.msg_queue.put(("download_progress", payload))

        try:
            info = self._ensure_model_ready(
                model_id,
                "이 모델이 로컬에 없습니다.다운로드 후 해당 모델을 사용하여 전사를 시작할 수 있습니다.",
                log,
            )
            if info["is_cached"]:
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
        except Exception as e:
            if str(e) == "MODEL_DOWNLOAD_CANCELLED":
                self.msg_queue.put(("cancelled", "모델 다운로드를 취소했습니다."))
                return
            tb = traceback.format_exc()
            self.msg_queue.put(("log", f"모델 다운로드 실패\n{e}\n{tb}"))
            self.msg_queue.put(("status", "모델 다운로드에 실패했습니다."))
        finally:
            self.msg_queue.put(("task_finished", "model_download"))

    # -------------------------------------------------
    # Device test
    # -------------------------------------------------
    def start_device_test(self):
        self.start_system_check()

    def _worker_device_test(self, model_id: str, pref: str):
        return

    # -------------------------------------------------
    # Transcription
    # -------------------------------------------------
    def start_transcription(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        in_path = self.file_var.get().strip()
        if not in_path or not os.path.isfile(in_path):
            messagebox.showerror("입력 파일 오류", "유효한 입력 파일을 선택하십시오.")
            return

        model_id = self.current_model_id()
        lang_code = self.current_lang_code()
        preset_id = self.current_preset_id()
        pref = self.current_preferred_device()

        self.output_path = None
        self.progress["value"] = 0
        self._clear_download_transfer_ui()
        self.open_result_btn.config(state="disabled")
        self.open_folder_btn.config(state="disabled")
        self._begin_task("transcription", "전사 작업", cancellable=True)
        self._set_transcription_transfer_summary()

        self._set_status("실행 준비 중입니다...")
        self._append_log("=" * 72)
        self._append_log(f"작업 시작 | file={in_path}")
        self._append_log(f"실행 설정 | lang={lang_code}, preset={preset_id}, model={model_id}, preferred_device={pref}")

        self.worker_thread = threading.Thread(
            target=self._worker_transcription,
            args=(in_path, lang_code, model_id, preset_id, pref),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker_transcription(self, in_path: str, lang_code: str, model_id: str, preset_id: str, pref: str):
        def log(msg: str):
            self.msg_queue.put(("log", msg))

        def progress(val: float):
            self.msg_queue.put(("progress", val))

        try:
            self.msg_queue.put(("progress", 3))

            info = self._ensure_model_ready(
                model_id,
                "전사를 시작하기 위해 선택된 모델을 다운로드합니다. 다운로드가 끝나면 이어서 전사를 시작합니다.",
                log,
            )
            if not info["is_cached"]:
                self.msg_queue.put(("status", f"선택 모델을 다운로드하는 중입니다... ({info['label']})"))
                download_model_to_cache(
                    model_id,
                    log=log,
                    progress=lambda payload: self.msg_queue.put(("download_progress", payload)),
                    measured_mbps=self.last_measured_speed_mbps if self.last_measured_repo_id == info.get("repo_id", "") else None,
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

            out_path = run_transcription_job(
                in_path=in_path,
                lang_code=lang_code,
                model_id=chosen["load_id"],
                device=chosen["device"],
                compute_type=chosen["compute_type"],
                log=log,
                progress=progress,
                preset_id=preset_id,
                cancel_event=self.cancel_event,
            )

            self.msg_queue.put(("done", (out_path, chosen, model_id, lang_code, preset_id, pref)))
        except Exception as e:
            self.msg_queue.put(("busy_off", None))
            if str(e) == "MODEL_DOWNLOAD_CANCELLED":
                self.msg_queue.put(("cancelled", "전사를 시작하지 않았습니다. 모델 다운로드가 취소되었습니다."))
                return
            if str(e) == "TRANSCRIPTION_CANCELLED":
                self.msg_queue.put(("cancelled", "전사를 취소했습니다."))
                return
            tb = traceback.format_exc()
            self.msg_queue.put(("error", f"{e}\n\n{tb}"))

    # -------------------------------------------------
    # Result open
    # -------------------------------------------------
    def open_result(self):
        if self.output_path and os.path.isfile(self.output_path):
            os.startfile(self.output_path)

    def open_result_folder(self):
        if self.output_path:
            folder = os.path.dirname(self.output_path)
            if os.path.isdir(folder):
                os.startfile(folder)

    # -------------------------------------------------
    # Queue polling
    # -------------------------------------------------
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()

                if kind == "log":
                    self._append_log(payload)

                elif kind == "progress":
                    try:
                        percent = max(0.0, min(100.0, float(payload)))
                        self.current_progress_percent = percent
                        self.progress["value"] = percent
                        if self.current_task_kind == "transcription" and self.transfer_mode != "download":
                            self.transfer_meta_var.set(f"전사 진행률 {percent:.0f}%")
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

                elif kind == "runtime_choice_text":
                    self.status_details["runtime"] = payload
                    self._update_status_row("runtime", "warning", payload, "자동 fallback이 필요할 수 있습니다.")

                elif kind == "task_finished":
                    if payload in {"model_download", "system_check"}:
                        self._finish_task(clear_transfer=False)

                elif kind == "done":
                    out_path, chosen, model_id, lang_code, preset_id, pref = payload
                    self.output_path = out_path
                    self.progress["value"] = 100
                    self._set_status("작업이 완료되었습니다.")
                    self.open_result_btn.config(state="normal")
                    self.open_folder_btn.config(state="normal")
                    self.start_btn.config(state="normal")
                    self._append_log(f"출력 파일 준비 완료: {out_path}")
                    self.transfer_meta_var.set("전사와 저장이 완료되었습니다.")

                    self.settings["model_id"] = model_id
                    self.settings["language"] = lang_code
                    self.settings["preset_id"] = preset_id
                    self.settings["preferred_device"] = pref
                    self.settings["last_good_device"] = chosen["device"]
                    self.settings["last_good_compute_type"] = chosen["compute_type"]
                    save_settings(self.settings)
                    self._refresh_model_state_local()
                    self._finish_task(clear_transfer=False)

                elif kind == "cancelled":
                    self._set_status(payload)
                    self.transfer_meta_var.set(payload)
                    self._append_log(payload)
                    self._finish_task(clear_transfer=False)

                elif kind == "error":
                    self._set_status("오류가 발생했습니다.")
                    self.transfer_meta_var.set("오류가 발생했습니다. 자세한 내용은 로그를 확인하십시오.")
                    self._append_log(payload)
                    self._finish_task(clear_transfer=False)
                    messagebox.showerror("오류", payload)

        except queue.Empty:
            pass

        self.after(100, self._poll_queue)

    # -------------------------------------------------
    # Apply startup info
    # -------------------------------------------------
    def _apply_startup_info(self, info: dict):
        self.status_details = dict(info["details"])
        self.base_status_details = dict(info["details"])
        if info.get("live_resources"):
            self._update_live_resource_ui(info["live_resources"])

        self._update_status_row("model", info["model"]["level"], info["model"]["summary"], info["model"].get("meta", ""))
        self._update_status_row("engine", info["engine"]["level"], info["engine"]["summary"], info["engine"].get("meta", ""))
        self._update_status_row("torch", info["torch"]["level"], info["torch"]["summary"], info["torch"].get("meta", ""))
        self._update_status_row("device", info["device"]["level"], info["device"]["summary"], info["device"].get("meta", ""))
        self._update_status_row("runtime", info["runtime"]["level"], info["runtime"]["summary"], info["runtime"].get("meta", ""))
        self._refresh_model_state_local()
