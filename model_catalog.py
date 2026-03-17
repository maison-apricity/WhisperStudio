# -*- coding: utf-8 -*-

from config import DEFAULT_MODEL_ID

MODEL_CATALOG = [
    {
        "id": "large-v3-turbo",
        "label": "Large v3 Turbo",
        "load_id": "dropbox-dash/faster-whisper-large-v3-turbo",
        "short_note": "기본값 · 균형형",
        "long_note": "속도와 품질의 균형이 좋은 기본 선택입니다.",
        "display": "권장 기본값 · Large v3 Turbo",
    },
    {
        "id": "large-v3",
        "label": "Large v3",
        "load_id": "Systran/faster-whisper-large-v3",
        "short_note": "품질 우선",
        "long_note": "품질을 더 우선하지만 실행 시간은 길어질 수 있습니다.",
        "display": "고품질 우선 · Large v3",
    },
    {
        "id": "large-v2",
        "label": "Large v2",
        "load_id": "guillaumekln/faster-whisper-large-v2",
        "short_note": "이전 대형 모델",
        "long_note": "이전 세대 대형 모델입니다.",
        "display": "이전 세대 대형 · Large v2",
    },
    {
        "id": "medium",
        "label": "Medium",
        "load_id": "Systran/faster-whisper-medium",
        "short_note": "중간급 절충형",
        "long_note": "속도와 품질을 절충한 중간급 모델입니다.",
        "display": "절충형 · Medium",
    },
    {
        "id": "small",
        "label": "Small",
        "load_id": "guillaumekln/faster-whisper-small",
        "short_note": "가벼운 실행",
        "long_note": "가벼운 환경이나 CPU 실행에 비교적 유리합니다.",
        "display": "가벼운 실행 · Small",
    },
    {
        "id": "base",
        "label": "Base",
        "load_id": "guillaumekln/faster-whisper-base",
        "short_note": "경량 모델",
        "long_note": "더 작은 리소스로 실행 가능한 경량 모델입니다.",
        "display": "경량 모델 · Base",
    },
    {
        "id": "tiny",
        "label": "Tiny",
        "load_id": "guillaumekln/faster-whisper-tiny",
        "short_note": "초경량 모델",
        "long_note": "가장 가벼운 초경량 모델입니다.",
        "display": "초경량 · Tiny",
    },
    {
        "id": "distil-large-v3",
        "label": "Distil Large v3",
        "load_id": "Systran/faster-distil-whisper-large-v3",
        "short_note": "증류형 대형 모델",
        "long_note": "faster-whisper 친화형 증류 모델입니다.",
        "display": "증류 대형 · Distil Large v3",
    },
]


def model_ids() -> list[str]:
    return [m["id"] for m in MODEL_CATALOG]


def model_display_values() -> list[str]:
    return [m["display"] for m in MODEL_CATALOG]


def parse_model_id_from_display(text: str) -> str:
    if not text:
        return DEFAULT_MODEL_ID
    stripped = text.strip()
    for m in MODEL_CATALOG:
        if stripped == m["display"]:
            return m["id"]
    lowered = stripped.lower()
    for m in MODEL_CATALOG:
        if lowered == m["id"].lower() or lowered == m["label"].lower():
            return m["id"]
    return DEFAULT_MODEL_ID


def get_model_entry(model_id: str) -> dict:
    for m in MODEL_CATALOG:
        if m["id"] == model_id:
            return m
    return {
        "id": model_id,
        "label": model_id,
        "load_id": model_id,
        "short_note": "사용자 지정 모델",
        "long_note": "사용자 지정 모델입니다.",
        "display": f"사용자 지정 · {model_id}",
    }


def default_model_id() -> str:
    return DEFAULT_MODEL_ID
