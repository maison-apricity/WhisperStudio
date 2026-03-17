# -*- coding: utf-8 -*-

import json
import os
from copy import deepcopy

from config import DEFAULT_LANGUAGE, DEFAULT_MODEL_ID, DEFAULT_PREFERRED_DEVICE
from paths import settings_path

DEFAULT_SETTINGS = {
    "language": DEFAULT_LANGUAGE,
    "model_id": DEFAULT_MODEL_ID,
    "preferred_device": DEFAULT_PREFERRED_DEVICE,
    "last_good_device": "",
    "last_good_compute_type": "",
}

def load_settings() -> dict:
    path = settings_path()
    if not os.path.isfile(path):
        return deepcopy(DEFAULT_SETTINGS)

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        return deepcopy(DEFAULT_SETTINGS)

    merged = deepcopy(DEFAULT_SETTINGS)
    merged.update(loaded)
    return merged

def save_settings(settings: dict) -> None:
    path = settings_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)