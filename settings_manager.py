# -*- coding: utf-8 -*-

import json
import os
from copy import deepcopy

from config import (
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL_ID,
    DEFAULT_PREFERRED_DEVICE,
    DEFAULT_PRESET_ID,
    DEFAULT_AUDIO_ENHANCE_LEVEL,
    DEFAULT_OUTPUT_FORMATS,
)
from paths import settings_path

DEFAULT_SETTINGS = {
    "language": DEFAULT_LANGUAGE,
    "model_id": DEFAULT_MODEL_ID,
    "preset_id": DEFAULT_PRESET_ID,
    "preferred_device": DEFAULT_PREFERRED_DEVICE,
    "audio_enhance_level": DEFAULT_AUDIO_ENHANCE_LEVEL,
    "output_formats": list(DEFAULT_OUTPUT_FORMATS),
    "last_good_device": "",
    "last_good_compute_type": "",
}

def _normalize_settings(settings: dict) -> dict:
    merged = deepcopy(DEFAULT_SETTINGS)
    merged.update(settings or {})

    # 구버전 호환: bool audio_enhance -> level
    if "audio_enhance" in merged and "audio_enhance_level" not in (settings or {}):
        merged["audio_enhance_level"] = "standard" if merged.get("audio_enhance") else DEFAULT_AUDIO_ENHANCE_LEVEL

    level = str(merged.get("audio_enhance_level") or DEFAULT_AUDIO_ENHANCE_LEVEL).strip().lower()
    if level not in {"off", "standard", "strong"}:
        level = DEFAULT_AUDIO_ENHANCE_LEVEL
    merged["audio_enhance_level"] = level

    formats = merged.get("output_formats", DEFAULT_OUTPUT_FORMATS)
    if isinstance(formats, str):
        formats = [formats]
    if not isinstance(formats, list):
        formats = list(DEFAULT_OUTPUT_FORMATS)
    normalized = []
    for fmt in formats:
        value = str(fmt).strip().lower()
        if value in {"srt", "txt", "vtt"} and value not in normalized:
            normalized.append(value)
    if not normalized:
        normalized = list(DEFAULT_OUTPUT_FORMATS)
    merged["output_formats"] = normalized
    return merged


def load_settings() -> dict:
    path = settings_path()
    if not os.path.isfile(path):
        return deepcopy(DEFAULT_SETTINGS)

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        return deepcopy(DEFAULT_SETTINGS)

    return _normalize_settings(loaded)


def save_settings(settings: dict) -> None:
    path = settings_path()
    normalized = _normalize_settings(settings)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
