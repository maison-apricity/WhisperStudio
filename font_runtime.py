# -*- coding: utf-8 -*-

import ctypes
import os
import tkinter.font as tkfont
from pathlib import Path

from paths import app_root

FR_PRIVATE = 0x10
FR_NOT_ENUM = 0x20

_loaded_font_files = []



def _font_dir() -> str:
    return os.path.join(app_root(), "fonts")



def _iter_font_files() -> list[str]:
    """
    fonts 폴더 아래의 모든 하위 폴더를 재귀적으로 탐색해
    ttf/otf/ttc/otc 파일을 반환한다.
    """
    root = Path(_font_dir())
    if not root.is_dir():
        return []

    allowed_exts = {".ttf", ".otf", ".ttc", ".otc"}
    files: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in allowed_exts:
            files.append(str(path))
    return files



def _font_priority(path: str) -> tuple[int, int, str]:
    """
    등록 우선순위:
    1) static 폴더 우선
    2) variable font보다 일반 regular/medium/bold 우선
    3) 파일명 정렬
    """
    lower = path.lower().replace("\\", "/")
    is_static = "/static/" in lower
    is_variable = "variablefont" in lower or "variable" in Path(path).stem.lower()
    return (
        0 if is_static else 1,
        1 if is_variable else 0,
        lower,
    )



def bundled_font_files() -> list[str]:
    """
    앱과 함께 번들된 폰트 파일 목록.
    실제 사용자가 넣어 둔 폴더 구조를 그대로 지원한다.
    """
    files = _iter_font_files()
    files.sort(key=_font_priority)
    return files



def register_private_fonts() -> int:
    """
    Windows에서 번들 폰트를 프로세스 private font로 등록.
    실패해도 예외를 올리지 않고 성공 개수만 반환.
    """
    if os.name != "nt":
        return 0

    add_font = ctypes.windll.gdi32.AddFontResourceExW
    add_font.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    add_font.restype = ctypes.c_int

    count = 0
    _loaded_font_files.clear()

    for font_path in bundled_font_files():
        try:
            added = add_font(font_path, FR_PRIVATE, 0)
            if added > 0:
                _loaded_font_files.append(font_path)
                count += added
        except Exception:
            pass

    return count



def unregister_private_fonts() -> None:
    """
    등록했던 private font 제거.
    """
    if os.name != "nt":
        return

    remove_font = ctypes.windll.gdi32.RemoveFontResourceExW
    remove_font.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    remove_font.restype = ctypes.c_int

    for font_path in _loaded_font_files:
        try:
            remove_font(font_path, FR_PRIVATE, 0)
        except Exception:
            pass



def _available_fonts() -> set[str]:
    return set(tkfont.families())



def pick_font_family(candidates: list[str], fallback: str = "TkDefaultFont") -> str:
    available = _available_fonts()
    for name in candidates:
        if name in available:
            return name
    return fallback



def pick_ui_font_family() -> str:
    preferred = [
        "Pretendard",
        "Pretendard Variable",
        "Noto Sans KR",
        "Noto Sans CJK KR",
        "Source Han Sans KR",
        "Malgun Gothic",
        "Apple SD Gothic Neo",
        "Segoe UI",
    ]
    return pick_font_family(preferred)



def pick_code_font_family(fallback: str | None = None) -> str:
    fallback = fallback or pick_ui_font_family()
    preferred = [
        "JetBrains Mono",
        "Cascadia Code",
        "Cascadia Mono",
        "D2Coding",
        "Fira Code",
        "IBM Plex Mono",
        "Consolas",
        "Courier New",
    ]
    return pick_font_family(preferred, fallback=fallback)



def pick_language_font_family(lang_code: str, fallback: str | None = None) -> str:
    fallback = fallback or pick_ui_font_family()
    lang_code = (lang_code or "").lower()

    if lang_code == "ja":
        return pick_font_family(
            [
                "Noto Sans JP",
                "Noto Sans CJK JP",
                "Source Han Sans JP",
                "BIZ UDPGothic",
                "Yu Gothic UI",
                "Yu Gothic",
                "Meiryo UI",
                "Meiryo",
                "Segoe UI",
            ],
            fallback=fallback,
        )

    if lang_code in {"zh", "zh-cn", "zh-sg"}:
        return pick_font_family(
            [
                "Noto Sans SC",
                "Noto Sans CJK SC",
                "Source Han Sans SC",
                "Microsoft YaHei UI",
                "Microsoft YaHei",
                "PingFang SC",
                "SimHei",
                "Segoe UI",
            ],
            fallback=fallback,
        )

    if lang_code in {"zh-tw", "zh-hk", "zh-mo"}:
        return pick_font_family(
            [
                "Noto Sans TC",
                "Noto Sans CJK TC",
                "Source Han Sans TC",
                "Microsoft JhengHei UI",
                "Microsoft JhengHei",
                "PingFang TC",
                "PMingLiU",
                "Segoe UI",
            ],
            fallback=fallback,
        )

    if lang_code == "ko":
        return pick_font_family(
            [
                "Pretendard",
                "Pretendard Variable",
                "Noto Sans KR",
                "Noto Sans CJK KR",
                "Source Han Sans KR",
                "Malgun Gothic",
                "Segoe UI",
            ],
            fallback=fallback,
        )

    return fallback
