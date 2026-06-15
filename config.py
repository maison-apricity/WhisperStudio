# -*- coding: utf-8 -*-

APP_NAME = "Whisper Studio"
APP_VERSION = "0.9.1"
APP_TAGLINE = "오디오·비디오를 텍스트와 자막 파일로 변환합니다."

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
        "short_note": "균형형",
        "long_note": "일반 입력용 기본 설정입니다.",
        "display": "균형",
        "beam_size": 5,
        "vad_min_speech_ms": 250,
        "vad_min_silence_ms": 1200,
        "temperature": [0.0, 0.2, 0.4, 0.6],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.0,
        "word_timestamps": True,
    },
    {
        "id": "lecture-meeting",
        "label": "강의 / 회의",
        "short_note": "연속 발화",
        "long_note": "강의, 발표, 회의처럼 긴 발화가 이어지는 입력에 맞춥니다.",
        "display": "강의 / 회의",
        "beam_size": 6,
        "vad_min_speech_ms": 220,
        "vad_min_silence_ms": 900,
        "temperature": [0.0, 0.2, 0.4],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
        "condition_on_previous_text": True,
        "repetition_penalty": 1.02,
        "word_timestamps": True,
    },
    {
        "id": "dialogue-video",
        "label": "영화 / 드라마 / 인터뷰",
        "short_note": "대사 전환",
        "long_note": "인터뷰, 영화, 드라마처럼 짧은 대사 전환이 잦은 입력에 맞춥니다.",
        "display": "영화 / 드라마 / 인터뷰",
        "beam_size": 5,
        "vad_min_speech_ms": 180,
        "vad_min_silence_ms": 700,
        "temperature": [0.0, 0.2, 0.4, 0.6],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.3,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.03,
        "word_timestamps": True,
    },
    {
        "id": "noisy-performance",
        "label": "공연 / 현장 / 잡음 많은 소스",
        "short_note": "잡음 대응",
        "long_note": "현장 녹음, 공연, 잡음 많은 소스처럼 음성 검출이 까다로운 입력에 맞춥니다.",
        "display": "공연 / 현장",
        "beam_size": 4,
        "vad_min_speech_ms": 380,
        "vad_min_silence_ms": 1600,
        "temperature": [0.0, 0.2, 0.4],
        "log_prob_threshold": -1.15,
        "compression_ratio_threshold": 2.5,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.08,
        "word_timestamps": True,
    },
    {
        "id": "speed-priority",
        "label": "속도 우선",
        "short_note": "빠른 초안",
        "long_note": "초안 확인과 저사양 환경을 위해 처리 속도를 우선합니다.",
        "display": "속도 우선",
        "beam_size": 2,
        "vad_min_speech_ms": 260,
        "vad_min_silence_ms": 1400,
        "temperature": [0.0, 0.2],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
        "condition_on_previous_text": False,
        "repetition_penalty": 1.0,
        "word_timestamps": True,
    },
    {
        "id": "quality-priority",
        "label": "정확도 우선",
        "short_note": "품질 우선",
        "long_note": "처리 시간이 늘어나도 정확도를 우선합니다.",
        "display": "정확도 우선",
        "beam_size": 7,
        "vad_min_speech_ms": 220,
        "vad_min_silence_ms": 1100,
        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8],
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.3,
        "condition_on_previous_text": True,
        "repetition_penalty": 1.03,
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
