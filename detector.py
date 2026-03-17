# -*- coding: utf-8 -*-

import os
import shutil

from config import MODEL_DIR_NAME, DEVICE_AUTO, DEVICE_CPU, DEVICE_CUDA
from paths import ffmpeg_default_candidates, model_default_candidates

def detect_ffmpeg(saved_path: str = "") -> tuple[str | None, str]:
    """
    반환:
      (경로, 상태)
      상태: auto / manual_required
    """
    candidates = []

    if saved_path:
        candidates.append(saved_path)

    candidates.extend(ffmpeg_default_candidates())

    which_path = shutil.which("ffmpeg")
    if which_path:
        candidates.append(which_path)

    checked = set()
    for p in candidates:
        if not p:
            continue
        norm = os.path.abspath(p)
        if norm in checked:
            continue
        checked.add(norm)
        if os.path.isfile(norm):
            return norm, "auto"

    return None, "manual_required"

def _looks_like_ct2_model_dir(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False

    # CTranslate2 whisper model directory에서 흔히 보이는 파일들 기준
    must_have_any = [
        "model.bin",
        "config.json",
        "tokenizer.json",
        "vocabulary.json",
    ]
    files = set(os.listdir(path))
    return any(name in files for name in must_have_any)

def detect_model(saved_path: str = "", model_dir_name: str = MODEL_DIR_NAME) -> tuple[str | None, str]:
    """
    반환:
      (경로, 상태)
      상태: auto / manual_required
    """
    candidates = []

    if saved_path:
        candidates.append(saved_path)

    candidates.extend(model_default_candidates(model_dir_name))

    # Hugging Face cache 쪽도 약하게 탐색
    hf_cache = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    if os.path.isdir(hf_cache):
        for root, dirs, files in os.walk(hf_cache):
            leaf = os.path.basename(root).lower()
            if model_dir_name.lower() in leaf or "faster-whisper" in leaf:
                candidates.append(root)

    checked = set()
    for p in candidates:
        if not p:
            continue
        norm = os.path.abspath(p)
        if norm in checked:
            continue
        checked.add(norm)
        if _looks_like_ct2_model_dir(norm):
            return norm, "auto"

    return None, "manual_required"

def get_torch_cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False

def get_ct2_supported_types(device: str) -> set[str]:
    import ctranslate2
    return set(ctranslate2.get_supported_compute_types(device))

def choose_device_and_compute_type(model_path: str, preferred_device: str = DEVICE_AUTO):
    """
    실제로 WhisperModel 로딩 테스트까지 수행해서
    사용 가능한 장치/compute_type 조합을 선택한다.

    반환:
      {
        "device": "cuda" or "cpu",
        "compute_type": "...",
        "reason": "..."
      }
    """
    from faster_whisper import WhisperModel

    gpu_candidates = ["float16", "int8_float16", "int8", "int8_float32", "float32"]
    cpu_candidates = ["int8", "int8_float32", "float32", "int16"]

    gpu_types = set()
    cpu_types = set()

    try:
        gpu_types = get_ct2_supported_types("cuda")
    except Exception:
        gpu_types = set()

    try:
        cpu_types = get_ct2_supported_types("cpu")
    except Exception:
        cpu_types = set()

    def try_load(device: str, compute_types: list[str], supported: set[str]):
        last_error = None
        for ct in compute_types:
            if ct not in supported:
                continue
            try:
                model = WhisperModel(model_path, device=device, compute_type=ct)
                del model
                return {
                    "device": device,
                    "compute_type": ct,
                    "reason": f"{device}/{ct} 로딩 성공",
                }
            except Exception as e:
                last_error = e
        return last_error

    # 우선순위 결정
    if preferred_device == DEVICE_CUDA:
        order = ["cuda", "cpu"]
    elif preferred_device == DEVICE_CPU:
        order = ["cpu"]
    else:
        order = ["cuda", "cpu"]

    errors = []

    for device in order:
        if device == "cuda":
            if not gpu_types:
                errors.append("CUDA compute type 조회 실패 또는 비어 있음")
                continue
            result = try_load("cuda", gpu_candidates, gpu_types)
            if isinstance(result, dict):
                return result
            errors.append(f"CUDA 로딩 실패: {result}")
        elif device == "cpu":
            if not cpu_types:
                errors.append("CPU compute type 조회 실패 또는 비어 있음")
                continue
            result = try_load("cpu", cpu_candidates, cpu_types)
            if isinstance(result, dict):
                return result
            errors.append(f"CPU 로딩 실패: {result}")

    raise RuntimeError(" | ".join(errors))