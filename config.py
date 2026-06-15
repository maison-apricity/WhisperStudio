# -*- coding: utf-8 -*-

APP_NAME = "Whisper Studio"
APP_VERSION = "0.9.1"
APP_TAGLINE = "Whisper Studio는 OpenAI의 Whisper 엔진을 사용하여 영상 및 음성 파일에서 스크립트를 전사합니다."

DEFAULT_LANGUAGE = "auto"
DEFAULT_MODEL_ID = "large-v3-turbo"
DEFAULT_PREFERRED_DEVICE = "auto"   # auto / cuda / cpu
DEFAULT_PRESET_ID = "auto-balanced"
DEFAULT_AUDIO_ENHANCE_LEVEL = "off"   # off / standard / strong
DEFAULT_OUTPUT_FORMATS = ["srt"]

# transcription defaults
BEAM_SIZE = 5
VAD_MIN_SPEECH_MS = 250
VAD_MIN_SILENCE_MS = 1500

# subtitle shaping defaults
MAX_CUE_DURATION = 6.5
MIN_CUE_DURATION = 1.0
MAX_GAP_TO_MERGE = 0.35
TARGET_CPS = 18.0
MAX_CPS = 23.0

# persistent files / dirs
SETTINGS_FILENAME = "settings.json"
HF_HOME_DIRNAME = "hf_home"
TEMP_DIRNAME = "temp"

# GUI options
LANGUAGE_OPTIONS = [
    ("auto", "자동 감지"),
    ("ko", "한국어"),
    ("en", "영어"),
    ("ja", "일본어"),
    ("zh", "중국어"),
    ("de", "독일어"),
    ("fr", "프랑스어"),
    ("es", "스페인어"),
]

LANGUAGE_NATIVE_NAMES = {
    "auto": "자동 감지",
    "ko": "한국어",
    "en": "English",
    "ja": "日本語",
    "zh": "中文",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
}

LANGUAGE_KOREAN_NAMES = {
    "auto": "자동 감지",
    "ko": "한국어",
    "en": "영어",
    "ja": "일본어",
    "zh": "중국어",
    "de": "독일어",
    "fr": "프랑스어",
    "es": "스페인어",
}


def get_language_korean_name(lang_code: str) -> str:
    return LANGUAGE_KOREAN_NAMES.get((lang_code or "").strip().lower(), lang_code)

TRANSCRIPTION_PRESETS = [
    {
        "id": "auto-balanced",
        "label": "균형",
        "short_note": "대부분의 파일에 맞는 안정 기본값",
        "long_note": "faster-whisper 기본 디코딩값을 중심으로 반복 루프를 줄이도록 이전 문맥 주입을 끈 기본 프리셋입니다.",
        "display": "균형",
        "beam_size": 5,
        "vad_min_speech_ms": 250,
        "vad_min_silence_ms": 1000,
        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.03,
        "word_timestamps": True,
    },
    {
        "id": "lecture-meeting",
        "label": "강의·회의",
        "short_note": "긴 발화와 짧은 휴지를 자연스럽게 유지",
        "long_note": "강의, 회의, 발표처럼 문장이 길고 화자 휴지가 짧은 녹음에 맞춘 프리셋입니다.",
        "display": "강의·회의",
        "beam_size": 5,
        "vad_min_speech_ms": 200,
        "vad_min_silence_ms": 700,
        "temperature": [0.0, 0.2, 0.4, 0.6],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.04,
        "word_timestamps": True,
    },
    {
        "id": "dialogue-video",
        "label": "대화·영상",
        "short_note": "대사 전환과 자막 분리에 유리",
        "long_note": "인터뷰, 드라마, 영상 대사처럼 짧은 발화가 교차하는 파일에 맞춘 프리셋입니다.",
        "display": "대화·영상",
        "beam_size": 5,
        "vad_min_speech_ms": 180,
        "vad_min_silence_ms": 550,
        "temperature": [0.0, 0.2, 0.4, 0.6],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.35,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.05,
        "word_timestamps": True,
    },
    {
        "id": "noisy-performance",
        "label": "소음 많은 현장",
        "short_note": "배경음이 큰 녹음에서 오검출 억제",
        "long_note": "현장 녹음, 공연, 거리 소음처럼 무음과 잡음을 구분하기 어려운 파일에 맞춘 프리셋입니다.",
        "display": "소음 많은 현장",
        "beam_size": 4,
        "vad_min_speech_ms": 380,
        "vad_min_silence_ms": 1500,
        "temperature": [0.0, 0.2, 0.4, 0.6],
        "log_prob_threshold": -1.1,
        "compression_ratio_threshold": 2.4,
        "no_speech_threshold": 0.65,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.08,
        "word_timestamps": True,
    },
    {
        "id": "speed-priority",
        "label": "속도 우선",
        "short_note": "초안 확인용 빠른 처리",
        "long_note": "정확도보다 처리 시간을 우선할 때 쓰는 프리셋입니다.",
        "display": "속도 우선",
        "beam_size": 2,
        "vad_min_speech_ms": 260,
        "vad_min_silence_ms": 1000,
        "temperature": [0.0, 0.2],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.03,
        "word_timestamps": True,
    },
    {
        "id": "quality-priority",
        "label": "정확도 우선",
        "short_note": "시간을 더 쓰고 안정성을 높임",
        "long_note": "중요한 파일에서 더 넓게 탐색하되 반복 루프를 줄이도록 이전 문맥 주입은 끈 프리셋입니다.",
        "display": "정확도 우선",
        "beam_size": 6,
        "vad_min_speech_ms": 220,
        "vad_min_silence_ms": 900,
        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.35,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.05,
        "word_timestamps": True,
    },
]


def get_transcription_preset(preset_id: str) -> dict:
    for preset in TRANSCRIPTION_PRESETS:
        if preset["id"] == preset_id:
            return preset
    for preset in TRANSCRIPTION_PRESETS:
        if preset["id"] == DEFAULT_PRESET_ID:
            return preset
    return TRANSCRIPTION_PRESETS[0]
