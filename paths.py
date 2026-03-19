# -*- coding: utf-8 -*-

import os
import sys
import shutil

from config import SETTINGS_FILENAME, HF_HOME_DIRNAME, TEMP_DIRNAME

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

def app_root() -> str:
    """
    쓰기 가능한 앱 루트.
    개발 중:
        현재 파일 기준 폴더
    PyInstaller onedir:
        실행 파일(.exe) 기준 폴더
    """
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def bundle_root() -> str:
    """
    번들된 읽기 전용 리소스 루트.
    PyInstaller onedir에서는 보통 _internal 경로(sys._MEIPASS)를 가리킨다.
    """
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path

def settings_path() -> str:
    return os.path.join(app_root(), SETTINGS_FILENAME)

def hf_home_path() -> str:
    return ensure_dir(os.path.join(app_root(), HF_HOME_DIRNAME))

def temp_work_dir() -> str:
    return ensure_dir(os.path.join(app_root(), TEMP_DIRNAME))

def clear_temp_work_dir(remove_root: bool = False) -> None:
    root = os.path.join(app_root(), TEMP_DIRNAME)
    if not os.path.isdir(root):
        return
    if remove_root:
        shutil.rmtree(root, ignore_errors=True)
        return
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass

def setup_runtime_environment() -> None:
    """
    앱 시작 직후 한 번 호출.
    Hugging Face 캐시를 앱 폴더 내부로 유도하고,
    CTranslate2 / OpenMP 관련 기본 환경변수를 세팅한다.
    """
    os.environ.setdefault("HF_HOME", hf_home_path())
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("CT2_USE_CUBLASLT", "1")

def bundled_ffmpeg_candidates() -> list[str]:
    app = app_root()
    bundle = bundle_root()

    names = [
        # 사용자가 exe 옆에 직접 넣는 경우를 우선 허용
        os.path.join(app, "_include", "ffmpeg", "ffmpeg.exe"),
        os.path.join(app, "assets", "ffmpeg", "ffmpeg.exe"),
        os.path.join(app, "ffmpeg", "ffmpeg.exe"),
        os.path.join(app, "bin", "ffmpeg.exe"),
        os.path.join(app, "ffmpeg.exe"),

        # PyInstaller 번들 내부 리소스
        os.path.join(bundle, "assets", "ffmpeg", "ffmpeg.exe"),
        os.path.join(bundle, "_include", "ffmpeg", "ffmpeg.exe"),
        os.path.join(bundle, "ffmpeg", "ffmpeg.exe"),
        os.path.join(bundle, "bin", "ffmpeg.exe"),
        os.path.join(bundle, "ffmpeg.exe"),
    ]

    unique = []
    seen = set()
    for item in names:
        norm = os.path.normcase(os.path.normpath(item))
        if norm not in seen:
            seen.add(norm)
            unique.append(item)
    return unique


def ffmpeg_binary_path() -> str | None:
    for path in bundled_ffmpeg_candidates():
        if os.path.isfile(path):
            return path
    found = shutil.which("ffmpeg")
    return found if found else None


def bundled_icon_path() -> str | None:
    app = app_root()
    bundle = bundle_root()

    candidates = [
        # 번들 내부 우선
        os.path.join(bundle, "assets", "icons", "WhisperStudio.ico"),
        os.path.join(bundle, "WhisperStudio.ico"),

        # 필요 시 exe 옆 override도 허용
        os.path.join(app, "assets", "icons", "WhisperStudio.ico"),
        os.path.join(app, "WhisperStudio.ico"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def bundled_font_dirs() -> list[str]:
    app = app_root()
    bundle = bundle_root()

    candidates = [
        os.path.join(bundle, "assets", "fonts"),
        os.path.join(bundle, "fonts"),
        os.path.join(app, "assets", "fonts"),
        os.path.join(app, "fonts"),
    ]

    unique = []
    seen = set()
    for path in candidates:
        norm = os.path.normcase(os.path.normpath(path))
        if norm not in seen and os.path.isdir(path):
            seen.add(norm)
            unique.append(path)
    return unique