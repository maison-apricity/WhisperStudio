# -*- coding: utf-8 -*-

APP_NAME = "Whisper Studio"
APP_VERSION = "0.9.1"
APP_TAGLINE = "자막 생성기 : OpenAI의 Whisper를 사용하여 영상 및 음성 파일에서 자막을 생성합니다."

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
        "short_note": "기본값 · 속도와 품질의 균형",
        "long_note": "파일 성격을 확신하기 어려울 때 사용하는 기본 프리셋입니다. 속도와 정확도의 균형을 우선합니다.",
        "display": "기본값 · 균형",
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
        "short_note": "긴 문장과 연속 발화에 유리",
        "long_note": "한 명 또는 소수 화자의 연속 발화가 많은 강의, 발표, 회의 녹음에 적합한 프리셋입니다.",
        "display": "연속 발화 · 강의 / 회의",
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
        "short_note": "짧은 대사 전환과 장면 변화에 적응",
        "long_note": "화자 전환이 잦은 영상 대사에 맞춘 프리셋입니다. 문맥 끌림보다 장면 단위 분리를 조금 더 우선합니다.",
        "display": "대사형 · 영화 / 드라마 / 인터뷰",
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
        "short_note": "배경음과 잔향이 큰 환경용",
        "long_note": "콘서트, 현장 녹음, 잡음이 많은 파일처럼 음성 검출이 까다로운 환경을 위한 보수적 프리셋입니다.",
        "display": "잡음 대응 · 공연 / 현장",
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
        "short_note": "빠른 초안 확인용",
        "long_note": "미리보기나 저사양 장치에서 빠르게 결과를 확인하려는 경우에 적합합니다. 정확도보다 처리 속도를 우선합니다.",
        "display": "고속 처리 · 속도 우선",
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
        "short_note": "시간이 더 걸려도 품질을 우선",
        "long_note": "처리 시간이 늘어나더라도 정확도를 최대한 확보하고 싶을 때 사용하는 프리셋입니다.",
        "display": "고품질 처리 · 정확도 우선",
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
