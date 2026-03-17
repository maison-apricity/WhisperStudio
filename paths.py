# -*- coding: utf-8 -*-

import os
import sys
import shutil

from config import SETTINGS_FILENAME, HF_HOME_DIRNAME, TEMP_DIRNAME

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

def app_root() -> str:
    """
    개발 중:
        현재 파일 기준 폴더
    PyInstaller onedir:
        실행 파일(.exe) 기준 폴더
    """
    if is_frozen():
        return os.path.dirname(sys.executable)
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
    root = app_root()
    names = [
        os.path.join(root, 'assets', 'ffmpeg', 'ffmpeg.exe'),
        os.path.join(root, '_include', 'ffmpeg', 'ffmpeg.exe'),
        os.path.join(root, 'ffmpeg', 'ffmpeg.exe'),
        os.path.join(root, 'bin', 'ffmpeg.exe'),
        os.path.join(root, 'ffmpeg.exe'),
    ]
    # 중복 제거
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
    found = shutil.which('ffmpeg')
    return found if found else None


def bundled_icon_path() -> str | None:
    root = app_root()
    candidates = [
        os.path.join(root, 'assets', 'icons', 'WhisperStudio.ico'),
        os.path.join(root, 'WhisperStudio.ico'),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def bundled_font_dirs() -> list[str]:
    root = app_root()
    candidates = [
        os.path.join(root, 'assets', 'fonts'),
        os.path.join(root, 'fonts'),
    ]
    return [path for path in candidates if os.path.isdir(path)]
