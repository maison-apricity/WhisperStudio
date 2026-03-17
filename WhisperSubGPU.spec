# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

ROOT = Path.cwd()
APP_NAME = "WhisperSubGPU"
ENTRY_SCRIPT = "app.py"

datas = []
binaries = []
hiddenimports = [
    "appdirs",
    "packaging",
    "pkg_resources",
    "setuptools",
    "faster_whisper",
    "faster_whisper.audio",
    "faster_whisper.tokenizer",
    "faster_whisper.transcribe",
    "faster_whisper.utils",
    "faster_whisper.vad",
    "ctranslate2",
    "huggingface_hub",
    "huggingface_hub.file_download",
    "huggingface_hub.hf_api",
    "tokenizers",
    "av",
    "psutil",
    "torch",
    "torch._C",
    "torch.cuda",
    "multiprocessing",
    "queue",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.font",
    "tkinter.messagebox",
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi.onnxruntime_pybind11_state",
]

excludes = [
    "tensorboard",
    "torch.utils.tensorboard",
    "torchaudio",
    "torchvision",
]


def add_file_or_tree(src_name: str, dest_root: str):
    src = ROOT / src_name
    if not src.exists():
        return

    if src.is_file():
        datas.append((str(src), dest_root))
        return

    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel_parent = p.relative_to(src).parent.as_posix()
        dest = dest_root if rel_parent == "." else f"{dest_root}/{rel_parent}"
        datas.append((str(p), dest))


def add_package(pkg_name: str):
    try:
        hiddenimports.extend(collect_submodules(pkg_name))
    except Exception:
        pass

    try:
        datas.extend(collect_data_files(pkg_name, include_py_files=False))
    except Exception:
        pass

    try:
        binaries.extend(collect_dynamic_libs(pkg_name))
    except Exception:
        pass

    try:
        datas.extend(copy_metadata(pkg_name))
    except Exception:
        pass


for pkg in [
    "faster_whisper",
    "ctranslate2",
    "huggingface_hub",
    "tokenizers",
    "av",
    "psutil",
    "torch",
    "onnxruntime",
    "appdirs",
    "packaging",
    "setuptools",
]:
    add_package(pkg)

add_file_or_tree("settings.json", ".")
add_file_or_tree("fonts", "fonts")
add_file_or_tree("ffmpeg", "ffmpeg")
add_file_or_tree("bin", "bin")
add_file_or_tree("licenses", "licenses")

a = Analysis(
    [str(ROOT / ENTRY_SCRIPT)],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=list(dict.fromkeys(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "rthook_stdio.py")],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)