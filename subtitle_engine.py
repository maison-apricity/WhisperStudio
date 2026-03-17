# -*- coding: utf-8 -*-

import gc
import os
import re
import subprocess
import time
import unicodedata
import uuid
from datetime import timedelta

from config import (
    BEAM_SIZE,
    VAD_MIN_SPEECH_MS,
    VAD_MIN_SILENCE_MS,
    MAX_CUE_DURATION,
    MIN_CUE_DURATION,
    MAX_GAP_TO_MERGE,
    TARGET_CPS,
    MAX_CPS,
    DEFAULT_PRESET_ID,
    get_transcription_preset,
)
from paths import setup_runtime_environment, temp_work_dir, ffmpeg_binary_path


# -------------------------------------------
# 텍스트 유틸
# -------------------------------------------
def _cell_width(ch: str) -> int:
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    if unicodedata.combining(ch):
        return 0
    return 1

def _disp_len(s: str) -> int:
    return sum(_cell_width(c) for c in s)

def _is_cjk_lang(lang: str) -> bool:
    return lang in {"ko", "ja", "zh"}

def _max_line_width(lang: str) -> int:
    return 22 if _is_cjk_lang(lang) else 42

def _max_block_width(lang: str) -> int:
    return _max_line_width(lang) * 2 + 4

def _clean_spaces_for_latin(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]}])", r"\1", text)
    text = re.sub(r"\s+(['’])", r"\1", text)
    return text

def normalize_subtitle_text(text: str, lang: str) -> str:
    if not text:
        return ""
    text = text.replace("\u00a0", " ")
    text = text.replace("…", "...")
    text = text.replace("，", ",").replace("。", ".")
    text = text.replace("？", "?").replace("！", "!")
    text = re.sub(r"\s+", " ", text).strip()

    if _is_cjk_lang(lang):
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r"([,.!?;:])(?=\S)", r"\1 ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    return _clean_spaces_for_latin(text)

def _recommended_duration(text: str) -> float:
    width = max(1, _disp_len(text))
    return max(MIN_CUE_DURATION, min(MAX_CUE_DURATION, 0.055 * width + 0.65))

def _ends_sentence(word: str) -> bool:
    return word.rstrip().endswith((".", "!", "?", "。", "！", "？"))

def _ends_clause(word: str) -> bool:
    return word.rstrip().endswith((",", ";", ":", "，", "；", "："))

def _char_len_for_cps(text: str) -> int:
    return len(text.replace("\n", " ").strip())

def wrap_subtitle_text(text: str, lang: str, max_lines: int = 2) -> str:
    text = normalize_subtitle_text(text, lang)
    max_width = _max_line_width(lang)

    if not text:
        return ""

    lines = []

    if _is_cjk_lang(lang):
        current = ""
        for ch in text:
            trial = current + ch
            if _disp_len(trial) > max_width and current:
                lines.append(current.strip())
                current = ch
            else:
                current = trial
        if current:
            lines.append(current.strip())
    else:
        tokens = text.split(" ")
        current = ""
        for tok in tokens:
            trial = tok if not current else f"{current} {tok}"
            if _disp_len(trial) > max_width and current:
                lines.append(current.strip())
                current = tok
            else:
                current = trial
        if current:
            lines.append(current.strip())

    if len(lines) <= max_lines:
        return "\n".join(lines)

    head = lines[:max_lines - 1]
    tail = " ".join(lines[max_lines - 1:]) if not _is_cjk_lang(lang) else "".join(lines[max_lines - 1:])
    return "\n".join(head + [tail.strip()])


# -------------------------------------------
# 미디어 길이 프로브
# -------------------------------------------
def probe_media_duration_seconds(media_path: str) -> float | None:
    try:
        import av

        with av.open(media_path) as container:
            if container.duration is not None:
                return float(container.duration) / 1_000_000.0

            for stream in container.streams:
                if stream.duration is not None and stream.time_base is not None:
                    return float(stream.duration * stream.time_base)
    except Exception:
        return None

    return None


# -------------------------------------------
# 자막 후처리
# -------------------------------------------
def _segment_to_word_items(segment):
    words = []
    seg_words = getattr(segment, "words", None)

    if seg_words:
        for w in seg_words:
            raw = getattr(w, "word", "")
            if raw is None:
                continue
            txt = raw.replace("\n", " ")
            if not txt.strip():
                continue

            start = getattr(w, "start", None)
            end = getattr(w, "end", None)
            if start is None:
                start = getattr(segment, "start", 0.0)
            if end is None:
                end = getattr(segment, "end", start)

            words.append({
                "start": float(start),
                "end": float(end),
                "word": txt,
            })

    return words

def _emit_cue_from_words(words, lang: str):
    raw_text = "".join(w["word"] for w in words)
    text = normalize_subtitle_text(raw_text, lang)
    start = float(words[0]["start"])
    end = float(words[-1]["end"])
    return {"start": start, "end": end, "text": text}

def split_segment_into_cues(segment, lang: str):
    seg_text = normalize_subtitle_text(getattr(segment, "text", ""), lang)
    seg_start = float(getattr(segment, "start", 0.0))
    seg_end = float(getattr(segment, "end", seg_start))
    word_items = _segment_to_word_items(segment)

    if not word_items:
        return [{"start": seg_start, "end": seg_end, "text": seg_text}]

    cues = []
    current = []

    for w in word_items:
        if current:
            trial_text = normalize_subtitle_text("".join(x["word"] for x in current + [w]), lang)
            trial_duration = max(0.01, float(w["end"]) - float(current[0]["start"]))
            trial_cps = _char_len_for_cps(trial_text) / trial_duration
            prev_word = current[-1]["word"]

            flush_before = False
            if _ends_sentence(prev_word) and trial_duration >= 1.0:
                flush_before = True
            elif _ends_clause(prev_word) and trial_duration >= 2.2 and _disp_len(trial_text) > int(_max_block_width(lang) * 0.8):
                flush_before = True
            elif _disp_len(trial_text) > _max_block_width(lang):
                flush_before = True
            elif trial_duration > MAX_CUE_DURATION:
                flush_before = True
            elif trial_cps > MAX_CPS and trial_duration >= 2.0:
                flush_before = True

            if flush_before:
                cues.append(_emit_cue_from_words(current, lang))
                current = []

        current.append(w)

    if current:
        cues.append(_emit_cue_from_words(current, lang))

    return cues

def merge_short_cues(cues, lang: str):
    if not cues:
        return cues

    merged = [cues[0].copy()]
    for cue in cues[1:]:
        prev = merged[-1]
        gap = max(0.0, cue["start"] - prev["end"])
        prev_text = prev["text"].replace("\n", " ").strip()
        cue_text = cue["text"].replace("\n", " ").strip()
        combined_text = normalize_subtitle_text(f"{prev_text} {cue_text}", lang)
        combined_duration = cue["end"] - prev["start"]

        prev_short = (prev["end"] - prev["start"] < 1.2) or (_disp_len(prev_text) < max(8, _max_line_width(lang) // 3))
        safe_length = _disp_len(combined_text) <= int(_max_block_width(lang) * 1.15)
        safe_duration = combined_duration <= MAX_CUE_DURATION
        prev_is_terminal = prev_text.endswith((".", "!", "?", "。", "！", "？"))

        if prev_short and gap <= MAX_GAP_TO_MERGE and safe_length and safe_duration and not prev_is_terminal:
            prev["end"] = max(prev["end"], cue["end"])
            prev["text"] = combined_text
        else:
            merged.append(cue.copy())

    return merged

def balance_timings(cues):
    if not cues:
        return cues

    balanced = [cue.copy() for cue in cues]

    for i, cue in enumerate(balanced):
        cue["start"] = float(cue["start"])
        cue["end"] = float(cue["end"])
        cue["text"] = cue["text"].strip()

        if cue["end"] <= cue["start"]:
            cue["end"] = cue["start"] + 0.20

        wanted = _recommended_duration(cue["text"])
        next_start = balanced[i + 1]["start"] if i + 1 < len(balanced) else None

        if cue["end"] - cue["start"] < wanted:
            candidate_end = cue["start"] + wanted
            if next_start is not None:
                cue["end"] = min(candidate_end, max(cue["end"], next_start - 0.02))
            else:
                cue["end"] = candidate_end

        if next_start is not None and cue["end"] >= next_start:
            cue["end"] = max(cue["start"] + 0.20, next_start - 0.02)

    return balanced

def format_cues_for_srt(cues, lang: str):
    final_cues = []
    for cue in cues:
        text = wrap_subtitle_text(cue["text"], lang, max_lines=2)
        if not text:
            continue
        final_cues.append({
            "start": float(cue["start"]),
            "end": float(cue["end"]),
            "text": text,
        })
    return final_cues

def postprocess_segments_for_subtitles(segments, lang: str):
    raw_cues = []
    for seg in segments:
        raw_cues.extend(split_segment_into_cues(seg, lang))

    normalized = []
    for cue in raw_cues:
        cue["text"] = normalize_subtitle_text(cue["text"], lang)
        if cue["text"]:
            normalized.append(cue)

    merged = merge_short_cues(normalized, lang)
    balanced = balance_timings(merged)
    return format_cues_for_srt(balanced, lang)


# -------------------------------------------
# 저장
# -------------------------------------------
def sec2ts(t: float) -> str:
    td = timedelta(seconds=float(t))
    total_seconds = int(td.total_seconds())
    ms = int(td.microseconds / 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"

def save_results(base_path: str, cues: list[dict]) -> str:
    out_path = base_path + ".srt"
    srt_lines = []

    for i, cue in enumerate(cues):
        ts = f"{sec2ts(cue['start'])} --> {sec2ts(cue['end'])}"
        txt = cue["text"].strip()
        srt_lines.append(f"{i+1}\n{ts}\n{txt}\n")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))

    return out_path


# -------------------------------------------
# 입력 미디어 전처리
# -------------------------------------------
def _raise_if_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("TRANSCRIPTION_CANCELLED")


def prepare_media_input(media_path: str, log, cancel_event=None):
    ffmpeg = ffmpeg_binary_path()
    if not ffmpeg:
        log("FFmpeg를 찾지 못했습니다. 원본 미디어를 직접 전사 입력으로 사용합니다.")
        return media_path, None

    work_dir = temp_work_dir()
    tmp_name = f"whisper_input_{uuid.uuid4().hex}.wav"
    out_path = os.path.join(work_dir, tmp_name)
    cmd = [
        ffmpeg,
        "-y",
        "-i", media_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "pcm_s16le",
        out_path,
    ]

    try:
        log(f"입력 전처리: FFmpeg 사용 ({os.path.basename(ffmpeg)})")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        while True:
            _raise_if_cancelled(cancel_event)
            code = proc.poll()
            if code is not None:
                break
            time.sleep(0.15)

        stdout, stderr_bytes = proc.communicate()
        if proc.returncode == 0 and os.path.isfile(out_path):
            return out_path, out_path

        stderr = stderr_bytes.decode("utf-8", errors="ignore").strip().splitlines()[-1:]
        err_text = stderr[0] if stderr else f"return code {proc.returncode}"
        log(f"FFmpeg 전처리 실패: {err_text} · 원본 미디어로 계속합니다.")
    except RuntimeError:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    except Exception as exc:
        log(f"FFmpeg 전처리 실패: {exc} · 원본 미디어로 계속합니다.")

    try:
        if os.path.isfile(out_path):
            os.remove(out_path)
    except Exception:
        pass
    return media_path, None


# -------------------------------------------
# Whisper 전사
# -------------------------------------------
def load_faster_whisper_model(model_id: str, device: str, compute_type: str, log):
    setup_runtime_environment()
    from faster_whisper import WhisperModel

    log(f"모델 로딩 시작: {model_id}")
    log(f"연산 장치: {device} / {compute_type}")
    model = WhisperModel(model_id, device=device, compute_type=compute_type)
    log("모델 로딩 완료")
    return model

def transcribe_media(model, media_path: str, lang: str, preset: dict, log, progress, cancel_event=None):
    duration = probe_media_duration_seconds(media_path)
    if duration:
        log(f"입력 길이 확인: 약 {duration/60:.1f}분")
    else:
        log("입력 길이 확인 실패: 세그먼트 기준 진행률로 표시합니다.")

    beam_size = int(preset.get("beam_size", BEAM_SIZE))
    vad_min_speech_ms = int(preset.get("vad_min_speech_ms", VAD_MIN_SPEECH_MS))
    vad_min_silence_ms = int(preset.get("vad_min_silence_ms", VAD_MIN_SILENCE_MS))
    temperature = list(preset.get("temperature", [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]))
    log_prob_threshold = float(preset.get("log_prob_threshold", -1.0))
    compression_ratio_threshold = float(preset.get("compression_ratio_threshold", 2.4))
    condition_on_previous_text = bool(preset.get("condition_on_previous_text", False))
    repetition_penalty = float(preset.get("repetition_penalty", 1.0))
    word_timestamps = bool(preset.get("word_timestamps", True))

    transcribe_language = None if lang == "auto" else lang
    if transcribe_language is None:
        log("언어 설정: 자동 감지")
    else:
        log(f"언어 설정: {transcribe_language}")

    log(
        "전사 프리셋 적용 | "
        f"beam={beam_size}, vad=({vad_min_speech_ms}/{vad_min_silence_ms}ms), "
        f"temp={temperature}, condition_prev={condition_on_previous_text}, rep_penalty={repetition_penalty:.2f}"
    )

    segments_iter, info = model.transcribe(
        media_path,
        language=transcribe_language,
        vad_filter=True,
        vad_parameters=dict(
            min_speech_duration_ms=vad_min_speech_ms,
            min_silence_duration_ms=vad_min_silence_ms,
        ),
        beam_size=beam_size,
        temperature=temperature,
        log_prob_threshold=log_prob_threshold,
        compression_ratio_threshold=compression_ratio_threshold,
        condition_on_previous_text=condition_on_previous_text,
        repetition_penalty=repetition_penalty,
        word_timestamps=word_timestamps,
    )

    detected_lang = getattr(info, "language", None) or (transcribe_language or "en")
    language_prob = getattr(info, "language_probability", None)
    if transcribe_language is None:
        if language_prob is not None:
            log(f"자동 감지 결과: {detected_lang} (신뢰도 {language_prob:.2f})")
        else:
            log(f"자동 감지 결과: {detected_lang}")

    log("전사 시작")
    started = time.time()
    results = []
    segment_count = 0

    for s in segments_iter:
        _raise_if_cancelled(cancel_event)
        txt = normalize_subtitle_text(getattr(s, "text", ""), detected_lang)
        if not txt:
            continue

        segment_count += 1
        results.append(s)

        seg_end = float(getattr(s, "end", 0.0))
        if duration and duration > 0:
            pct = min(92.0, max(24.0, 24.0 + (seg_end / duration) * 64.0))
        else:
            pct = min(92.0, 24.0 + segment_count * 1.8)

        progress(pct)
        log(f"세그먼트 {segment_count:04d} | {float(getattr(s, 'start', 0.0)):8.1f}s | {txt[:90]}")

    elapsed = time.time() - started
    log(f"전사 완료: {len(results)}개 세그먼트 / {elapsed:.1f}초")
    return results, detected_lang


def run_transcription_job(
    in_path: str,
    lang_code: str,
    model_id: str,
    device: str,
    compute_type: str,
    log,
    progress,
    preset_id: str = DEFAULT_PRESET_ID,
    cancel_event=None,
):
    setup_runtime_environment()

    whisper_model = None
    prepared_input_path = None
    try:
        _raise_if_cancelled(cancel_event)
        progress(6)
        log("작업 초기화")

        _raise_if_cancelled(cancel_event)
        progress(8)
        transcribe_input, prepared_input_path = prepare_media_input(in_path, log, cancel_event=cancel_event)

        _raise_if_cancelled(cancel_event)
        progress(10)
        whisper_model = load_faster_whisper_model(model_id, device, compute_type, log)

        _raise_if_cancelled(cancel_event)
        preset = get_transcription_preset(preset_id)
        log(f"선택 프리셋: {preset['label']} ({preset['id']})")

        progress(20)
        segments, effective_lang = transcribe_media(whisper_model, transcribe_input, lang_code, preset, log, progress, cancel_event=cancel_event)

        _raise_if_cancelled(cancel_event)
        progress(95)
        log("자막 후처리 시작")
        cues = postprocess_segments_for_subtitles(segments, effective_lang)

        _raise_if_cancelled(cancel_event)
        progress(98)
        base, _ = os.path.splitext(in_path)
        out_path = save_results(base, cues)

        progress(100)
        log(f"결과 저장 완료: {out_path}")
        return out_path


    finally:
        if whisper_model:
            del whisper_model
        gc.collect()

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass