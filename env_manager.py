# -*- coding: utf-8 -*-

import multiprocessing as mp
import os
import platform
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable

from config import APP_NAME, APP_VERSION, DEFAULT_MODEL_ID
from model_catalog import get_model_entry
from paths import setup_runtime_environment, hf_home_path


_DOWNLOAD_SPEEDS = [
    (100.0, "eta_100", "100 Mb/s"),
    (500.0, "eta_500", "500 Mb/s"),
    (1000.0, "eta_1000", "1 Gb/s"),
]


def _safe_import_torch():
    try:
        import torch
        return torch
    except Exception:
        return None


def _safe_import_ctranslate2():
    try:
        import ctranslate2
        return ctranslate2
    except Exception:
        return None


def _safe_import_psutil():
    try:
        import psutil
        return psutil
    except Exception:
        return None


def _safe_import_hf():
    try:
        from huggingface_hub import HfApi, snapshot_download
        return HfApi, snapshot_download
    except Exception:
        return None, None


def _safe_import_winreg():
    if os.name != "nt":
        return None
    try:
        import winreg
        return winreg
    except Exception:
        return None


_PSUTIL = _safe_import_psutil()
_PROCESS = None
if _PSUTIL is not None:
    try:
        _PSUTIL.cpu_percent(interval=None)
    except Exception:
        pass
    try:
        _PROCESS = _PSUTIL.Process(os.getpid())
        _PROCESS.cpu_percent(interval=None)
    except Exception:
        _PROCESS = None


def format_bytes(num_bytes: int | float | None) -> str:
    if num_bytes is None:
        return "알 수 없음"
    value = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    return f"{value:.2f} {units[idx]}"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "알 수 없음"
    sec = int(round(seconds))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}시간 {m}분"
    if m > 0:
        return f"{m}분 {s}초"
    return f"{s}초"


def estimate_time_from_size(num_bytes: int | None, mbps: float) -> str:
    if not num_bytes or mbps <= 0:
        return "알 수 없음"
    seconds = (num_bytes * 8.0) / (mbps * 1_000_000.0)
    return format_duration(seconds)


def format_speed_text(mbps: float | None) -> str:
    if mbps is None or mbps <= 0:
        return "알 수 없음"
    if mbps >= 1000.0:
        return f"{mbps / 1000.0:.2f} Gb/s"
    if mbps >= 100.0:
        return f"{mbps:.0f} Mb/s"
    return f"{mbps:.1f} Mb/s"


def compact_path_for_display(path: str | None, keep_tail: int = 3) -> str:
    if not path:
        return "알 수 없음"

    norm = os.path.normpath(path)
    hf_root = os.path.normpath(hf_home_path())

    try:
        if os.path.commonpath([norm, hf_root]).lower() == hf_root.lower():
            rel = os.path.relpath(norm, hf_root)
            parts = [p for p in rel.split(os.sep) if p and p != "."]
            if len(parts) > keep_tail + 1:
                parts = parts[:1] + ["…"] + parts[-keep_tail:]
            return "앱 캐시 > " + " > ".join(parts)
    except Exception:
        pass

    drive, tail = os.path.splitdrive(norm)
    parts = [p for p in tail.split(os.sep) if p]
    if len(parts) > keep_tail + 1:
        parts = parts[:1] + ["…"] + parts[-keep_tail:]
    prefix = f"{drive}{os.sep}" if drive else ""
    return prefix + " > ".join(parts)


def _repo_cache_root(repo_id: str) -> str:
    repo_cache_name = "models--" + repo_id.replace("/", "--")
    return os.path.join(hf_home_path(), "hub", repo_cache_name)


def _dir_size_bytes(root: str) -> int:
    if not root or not os.path.isdir(root):
        return 0
    total = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
    return total


def _download_progress_payload(downloaded_bytes: int, total_bytes: int | None, speed_mbps: float | None) -> dict:
    percent = None
    if total_bytes and total_bytes > 0:
        percent = max(0.0, min(100.0, (downloaded_bytes / float(total_bytes)) * 100.0))

    return {
        "downloaded_bytes": downloaded_bytes,
        "downloaded_text": format_bytes(downloaded_bytes),
        "total_bytes": total_bytes,
        "total_text": format_bytes(total_bytes),
        "percent": percent,
        "speed_mbps": speed_mbps,
        "speed_text": format_speed_text(speed_mbps),
        "eta_text": estimate_time_from_size(max((total_bytes or 0) - downloaded_bytes, 0), speed_mbps or 0.0),
    }


def probe_repo_download_speed(repo_id: str, total_bytes: int | None = None, timeout: float = 20.0, sample_bytes: int = 250 * 1024 * 1024) -> dict:
    """
    Hugging Face 원격 파일에 대해 비교적 큰 범위 요청을 보내
    실제 다운로드에 가까운 회선 속도를 추정한다.
    """
    HfApi, _snapshot_download = _safe_import_hf()
    if HfApi is None:
        return {
            "ok": False,
            "speed_mbps": None,
            "speed_text": "알 수 없음",
            "eta_text": "알 수 없음",
            "message": "huggingface_hub를 불러오지 못했습니다.",
        }

    try:
        api = HfApi()
        info = api.model_info(repo_id=repo_id, files_metadata=True)
        siblings = getattr(info, "siblings", []) or []

        target_name = None
        target_size = 0
        for file_info in siblings:
            name = getattr(file_info, "rfilename", None) or getattr(file_info, "path", None)
            size = int(getattr(file_info, "size", 0) or 0)
            if not name or size <= 0:
                continue
            if size > target_size:
                target_size = size
                target_name = name

        if not target_name:
            return {
                "ok": False,
                "speed_mbps": None,
                "speed_text": "알 수 없음",
                "eta_text": "알 수 없음",
                "message": "속도 측정에 사용할 원격 파일 정보를 찾지 못했습니다.",
            }

        encoded_name = urllib.parse.quote(target_name, safe="/._-")
        url = f"https://huggingface.co/{repo_id}/resolve/main/{encoded_name}?download=1"
        req = urllib.request.Request(
            url,
            headers={
                "Range": f"bytes=0-{sample_bytes - 1}",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                "Cache-Control": "no-cache",
            },
        )

        read_bytes = 0
        started = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            while True:
                chunk = resp.read(128 * 1024)
                if not chunk:
                    break
                read_bytes += len(chunk)
                elapsed = time.perf_counter() - started
                if read_bytes >= sample_bytes or elapsed >= timeout:
                    break
        elapsed = max(time.perf_counter() - started, 1e-6)

        if read_bytes <= 0:
            return {
                "ok": False,
                "speed_mbps": None,
                "speed_text": "알 수 없음",
                "eta_text": "알 수 없음",
                "message": "전송된 바이트가 없어 회선 속도를 계산하지 못했습니다.",
            }

        speed_mbps = (read_bytes * 8.0) / (elapsed * 1_000_000.0)
        return {
            "ok": True,
            "speed_mbps": speed_mbps,
            "speed_text": format_speed_text(speed_mbps),
            "eta_text": estimate_time_from_size(total_bytes, speed_mbps),
            "sampled_bytes": read_bytes,
            "sampled_bytes_text": format_bytes(read_bytes),
            "message": f"약 {format_bytes(read_bytes)} 전송 기준 추정",
            "target_file": target_name,
        }
    except Exception as exc:
        return {
            "ok": False,
            "speed_mbps": None,
            "speed_text": "알 수 없음",
            "eta_text": "알 수 없음",
            "message": str(exc),
        }


def humanize_compute_types(types: set[str] | list[str]) -> str:
    order = [
        "float16",
        "bfloat16",
        "float32",
        "int8",
        "int8_float16",
        "int8_bfloat16",
        "int8_float32",
        "int16",
    ]
    label_map = {
        "float16": "FP16",
        "bfloat16": "BF16",
        "float32": "FP32",
        "int8": "INT8",
        "int8_float16": "INT8 + FP16",
        "int8_bfloat16": "INT8 + BF16",
        "int8_float32": "INT8 + FP32",
        "int16": "INT16",
    }
    values = list(types)
    values.sort(key=lambda x: order.index(x) if x in order else 999)
    return ", ".join(label_map.get(v, v) for v in values) if values else "없음"


def register_cuda_dll_dirs() -> int:
    if os.name != "nt":
        return 0

    candidates = []
    roots = [
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA",
        r"C:\Program Files\NVIDIA Corporation",
    ]

    for base in roots:
        if not os.path.isdir(base):
            continue
        for root, _, _ in os.walk(base):
            low = root.lower()
            if low.endswith("\\bin") and "\\v12." in low:
                candidates.append(root)

    count = 0
    seen = set()
    for path in candidates:
        norm = os.path.abspath(path)
        if norm in seen:
            continue
        seen.add(norm)
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(norm)
                count += 1
            except Exception:
                pass
    return count


def _snapshot_download_worker(repo_id: str, result_queue):
    """
    모델 다운로드를 별도 프로세스에서 수행한다.
    부모 프로세스가 취소 시 즉시 종료할 수 있도록 분리한다.
    """
    try:
        setup_runtime_environment()
        register_cuda_dll_dirs()
        _HfApi, snapshot_download = _safe_import_hf()
        if snapshot_download is None:
            result_queue.put(("error", "huggingface_hub를 불러오지 못했습니다."))
            return
        snapshot_download(repo_id=repo_id, local_files_only=False)
        result_queue.put(("ok", None))
    except Exception as exc:
        result_queue.put(("error", str(exc)))


def _clear_repo_cache_artifacts(repo_id: str) -> None:
    repo_root = _repo_cache_root(repo_id)
    try:
        shutil.rmtree(repo_root, ignore_errors=True)
    except Exception:
        pass

    lock_root = os.path.join(hf_home_path(), "hub", ".locks")
    if not os.path.isdir(lock_root):
        return

    token = repo_id.replace("/", "--")
    for base, _dirs, files in os.walk(lock_root):
        for name in files:
            if token not in name:
                continue
            path = os.path.join(base, name)
            try:
                os.remove(path)
            except OSError:
                pass


def _collect_remote_download_metadata(repo_id: str) -> tuple[int | None, dict[str, str]]:
    HfApi, _snapshot_download = _safe_import_hf()
    if HfApi is None:
        return None, {}

    try:
        api = HfApi()
        info = api.model_info(repo_id=repo_id, files_metadata=True)
        total = 0
        siblings = getattr(info, "siblings", []) or []
        for file_info in siblings:
            size = getattr(file_info, "size", None)
            if size:
                total += int(size)
        if total <= 0:
            return None, {}

        eta_map = {
            key: estimate_time_from_size(total, speed)
            for speed, key, _label in _DOWNLOAD_SPEEDS
        }
        return total, eta_map
    except Exception:
        return None, {}


def inspect_model_availability(model_id: str, include_remote_meta: bool = True) -> dict:
    """
    로컬 캐시 존재 여부와 필요 시 원격 메타데이터를 조회한다.
    """
    setup_runtime_environment()
    entry = get_model_entry(model_id)
    repo_id = entry["load_id"]

    _HfApi, snapshot_download = _safe_import_hf()

    is_cached = False
    cached_path = ""
    remote_size_bytes = None
    eta_map = {key: "알 수 없음" for _speed, key, _label in _DOWNLOAD_SPEEDS}

    if snapshot_download is not None:
        try:
            cached_path = snapshot_download(repo_id=repo_id, local_files_only=True)
            if cached_path and os.path.isdir(cached_path):
                is_cached = True
        except Exception:
            is_cached = False
            cached_path = ""

    if include_remote_meta:
        remote_size_bytes, remote_etas = _collect_remote_download_metadata(repo_id)
        eta_map.update(remote_etas)

    return {
        "model_id": entry["id"],
        "label": entry["label"],
        "repo_id": repo_id,
        "short_note": entry["short_note"],
        "long_note": entry["long_note"],
        "display": entry["display"],
        "is_cached": is_cached,
        "cached_path": cached_path,
        "cached_path_display": compact_path_for_display(cached_path) if cached_path else "아직 다운로드되지 않음",
        "remote_size_bytes": remote_size_bytes,
        "remote_size_text": format_bytes(remote_size_bytes),
        "eta_100": eta_map["eta_100"],
        "eta_500": eta_map["eta_500"],
        "eta_1000": eta_map["eta_1000"],
        "download_source": f"Hugging Face Hub · {repo_id}",
        "download_target": hf_home_path(),
        "download_target_display": compact_path_for_display(hf_home_path(), keep_tail=4),
    }


def download_model_to_cache(
    model_id: str,
    log: Callable[[str], None] | None = None,
    progress: Callable[[dict], None] | None = None,
    measured_mbps: float | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """
    선택 모델을 Hugging Face 캐시에 다운로드한다.
    다운로드는 별도 프로세스에서 수행해 취소 요청을 즉시 반영한다.
    진행률은 캐시 디렉터리 증가량으로 추정한다.
    """
    setup_runtime_environment()
    register_cuda_dll_dirs()

    _HfApi, snapshot_download = _safe_import_hf()
    if snapshot_download is None:
        raise RuntimeError("huggingface_hub를 불러오지 못했습니다.")

    def emit(msg: str):
        if log:
            log(msg)

    def emit_progress(payload: dict):
        if progress:
            progress(payload)

    info = inspect_model_availability(model_id, include_remote_meta=True)

    if info["is_cached"]:
        emit(f"선택 모델이 이미 준비되어 있습니다: {info['label']}")
        ready_payload = _download_progress_payload(info.get('remote_size_bytes') or 0, info.get('remote_size_bytes'), None)
        ready_payload.update({"percent": 100.0, "message": "이미 준비된 모델입니다."})
        emit_progress(ready_payload)
        return info

    emit(f"선택 모델이 로컬 캐시에 없습니다: {info['label']}")
    if info["remote_size_bytes"]:
        if measured_mbps and measured_mbps > 0:
            measured_eta = estimate_time_from_size(info['remote_size_bytes'], measured_mbps)
            emit(
                f"다운로드를 시작합니다. 예상 크기 {info['remote_size_text']} "
                f"(현재 회선 기준 약 {measured_eta} / 측정 속도 {format_speed_text(measured_mbps)})"
            )
        else:
            emit(
                f"다운로드를 시작합니다. 예상 크기 {info['remote_size_text']} "
                f"(100 Mb/s 약 {info['eta_100']} / 500 Mb/s 약 {info['eta_500']} / 1 Gb/s 약 {info['eta_1000']})"
            )
    else:
        emit("다운로드를 시작합니다. 원격 크기 정보는 확인하지 못했습니다.")

    repo_root = _repo_cache_root(info["repo_id"])
    os.makedirs(os.path.dirname(repo_root), exist_ok=True)
    base_size = _dir_size_bytes(repo_root)
    total_bytes = info.get("remote_size_bytes") or None

    stop_event = threading.Event()

    def progress_poller():
        last_size = base_size
        last_time = time.perf_counter()
        while not stop_event.is_set():
            size_now = _dir_size_bytes(repo_root)
            now = time.perf_counter()
            elapsed = max(now - last_time, 1e-6)
            delta = max(0, size_now - last_size)
            speed_mbps = (delta * 8.0) / (elapsed * 1_000_000.0) if delta > 0 else None
            downloaded = max(0, size_now - base_size)
            payload = _download_progress_payload(downloaded, total_bytes, speed_mbps)
            if cancel_event is not None and cancel_event.is_set():
                payload["message"] = "취소 요청을 받았습니다. 다운로드 프로세스를 정리하고 있습니다."
            else:
                payload["message"] = (
                    f"{payload['downloaded_text']} / {payload['total_text']} · "
                    f"{payload['speed_text']} · 남은 시간 {payload['eta_text']}"
                )
            emit_progress(payload)
            last_size = size_now
            last_time = now
            stop_event.wait(0.8)

    poller = threading.Thread(target=progress_poller, daemon=True)
    poller.start()

    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()
    worker = ctx.Process(target=_snapshot_download_worker, args=(info["repo_id"], result_queue), daemon=True)
    worker.start()

    cancelled = False
    worker_error = None

    try:
        while worker.is_alive():
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                emit("취소 요청을 감지했습니다. 다운로드 프로세스를 즉시 중단합니다.")
                worker.terminate()
                worker.join(timeout=5.0)
                if worker.is_alive() and hasattr(worker, "kill"):
                    worker.kill()
                    worker.join(timeout=1.0)
                break
            worker.join(timeout=0.25)

        if not cancelled:
            worker.join(timeout=0.2)
            if not result_queue.empty():
                result_kind, result_payload = result_queue.get_nowait()
                if result_kind == "error":
                    worker_error = result_payload or "모델 다운로드 중 오류가 발생했습니다."
            elif worker.exitcode not in {0, None}:
                worker_error = f"모델 다운로드 프로세스가 비정상 종료했습니다. exit code={worker.exitcode}"
    finally:
        stop_event.set()
        poller.join(timeout=1.0)

    final_size = _dir_size_bytes(repo_root)
    downloaded = max(0, final_size - base_size)

    if cancelled:
        _clear_repo_cache_artifacts(info["repo_id"])
        payload = _download_progress_payload(downloaded, info.get('remote_size_bytes'), None)
        payload["message"] = "모델 다운로드를 취소했습니다."
        emit_progress(payload)
        emit("모델 다운로드를 취소했습니다. 부분 파일도 함께 정리했습니다.")
        raise RuntimeError("MODEL_DOWNLOAD_CANCELLED")

    if worker_error:
        raise RuntimeError(worker_error)

    final_info = inspect_model_availability(model_id, include_remote_meta=False)
    payload = _download_progress_payload(downloaded or (info.get('remote_size_bytes') or 0), info.get('remote_size_bytes'), None)
    payload["percent"] = 100.0
    payload["message"] = f"다운로드 완료 · {payload['downloaded_text']}"
    emit_progress(payload)
    emit("모델 다운로드가 완료되었습니다.")
    return final_info

def _read_gpu_info_from_torch() -> dict:
    torch = _safe_import_torch()
    if torch is None:
        return {
            "available": False,
            "name": "알 수 없음",
            "capability": "알 수 없음",
            "vram_total_bytes": None,
            "vram_used_bytes": None,
            "vram_free_bytes": None,
        }

    try:
        if not torch.cuda.is_available():
            return {
                "available": False,
                "name": "감지되지 않음",
                "capability": "알 수 없음",
                "vram_total_bytes": None,
                "vram_used_bytes": None,
                "vram_free_bytes": None,
            }

        idx = torch.cuda.current_device()
        name = torch.cuda.get_device_name(idx)
        props = torch.cuda.get_device_properties(idx)
        free, total = torch.cuda.mem_get_info()
        used = int(total - free)

        return {
            "available": True,
            "name": name,
            "capability": f"{props.major}.{props.minor}",
            "vram_total_bytes": int(total),
            "vram_used_bytes": used,
            "vram_free_bytes": int(free),
        }
    except Exception:
        return {
            "available": False,
            "name": "확인 실패",
            "capability": "알 수 없음",
            "vram_total_bytes": None,
            "vram_used_bytes": None,
            "vram_free_bytes": None,
        }


def _read_windows_cpu_name() -> str:
    winreg = _safe_import_winreg()
    if winreg is None:
        return ""

    key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(value).strip()
    except Exception:
        return ""


def _pretty_os_text() -> str:
    if os.name == "nt":
        release = platform.release() or "Windows"
        version = platform.version() or ""
        build = version.split(".")[-1] if version else ""
        if build:
            return f"Windows {release} (build {build})"
        return f"Windows {release}"
    return platform.platform()


def _pretty_cpu_name() -> str:
    cpu_name = _read_windows_cpu_name()
    if cpu_name:
        return cpu_name
    return platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "") or platform.machine()


def _read_cpu_and_process_state(psutil) -> dict:
    cpu_percent = None
    app_cpu_percent = None
    app_ram_bytes = None

    if psutil is None:
        return {
            "cpu_percent": None,
            "app_cpu_percent": None,
            "app_ram_bytes": None,
        }

    try:
        cpu_percent = float(psutil.cpu_percent(interval=0.12))
    except Exception:
        cpu_percent = None

    if _PROCESS is not None:
        try:
            logical = max(1, int(psutil.cpu_count(logical=True) or 1))
            app_raw = float(_PROCESS.cpu_percent(interval=None))
            app_cpu_percent = max(0.0, min(100.0, app_raw / logical))
        except Exception:
            app_cpu_percent = None

        try:
            app_ram_bytes = int(_PROCESS.memory_info().rss)
        except Exception:
            app_ram_bytes = None

    return {
        "cpu_percent": cpu_percent,
        "app_cpu_percent": app_cpu_percent,
        "app_ram_bytes": app_ram_bytes,
    }


def _read_system_resources() -> dict:
    psutil = _safe_import_psutil()

    usage = _read_cpu_and_process_state(psutil)
    os_text = _pretty_os_text()
    cpu_name = _pretty_cpu_name()

    ram_total = None
    ram_used = None
    ram_percent = None

    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            ram_total = int(vm.total)
            ram_used = int(vm.used)
            ram_percent = float(vm.percent)
        except Exception:
            pass

    gpu = _read_gpu_info_from_torch()

    return {
        "os_text": os_text,
        "cpu_name": cpu_name,
        "cpu_percent": usage["cpu_percent"],
        "app_cpu_percent": usage["app_cpu_percent"],
        "app_ram_bytes": usage["app_ram_bytes"],
        "ram_total_bytes": ram_total,
        "ram_used_bytes": ram_used,
        "ram_percent": ram_percent,
        "gpu": gpu,
        "python_version": sys.version.split()[0],
        "hf_home": hf_home_path(),
        "timestamp_text": time.strftime("%H:%M:%S"),
    }


def _resource_pressure_level(cpu_percent: float | None, ram_percent: float | None, vram_percent: float | None) -> tuple[str, str, list[str]]:
    alerts: list[str] = []

    def _check(name: str, value: float | None, warn: float, danger: float):
        if value is None:
            return None
        if value >= danger:
            alerts.append(f"{name} {value:.0f}%")
            return "danger"
        if value >= warn:
            alerts.append(f"{name} {value:.0f}%")
            return "warning"
        return "success"

    states = [
        _check("CPU", cpu_percent, 85.0, 95.0),
        _check("RAM", ram_percent, 80.0, 90.0),
        _check("VRAM", vram_percent, 82.0, 92.0),
    ]

    if "danger" in states:
        return "danger", "고부하", alerts
    if "warning" in states:
        return "warning", "주의", alerts
    if any(v is not None for v in (cpu_percent, ram_percent, vram_percent)):
        return "success", "안정", alerts
    return "neutral", "정보 없음", alerts


def collect_live_resource_status() -> dict:
    setup_runtime_environment()
    sys_info = _read_system_resources()
    gpu = sys_info["gpu"]

    system_cpu_percent = sys_info.get("cpu_percent")
    app_cpu_percent = sys_info.get("app_cpu_percent")
    ram_percent = sys_info.get("ram_percent")
    ram_total_bytes = sys_info.get("ram_total_bytes")
    ram_used_bytes = sys_info.get("ram_used_bytes")
    app_ram_bytes = sys_info.get("app_ram_bytes")

    vram_total_bytes = gpu.get("vram_total_bytes")
    vram_used_bytes = gpu.get("vram_used_bytes")
    vram_percent = None
    if vram_total_bytes:
        try:
            vram_percent = float(vram_used_bytes or 0) / float(vram_total_bytes) * 100.0
        except Exception:
            vram_percent = None

    level, pressure_label, alerts = _resource_pressure_level(system_cpu_percent, ram_percent, vram_percent)

    ram_summary = "RAM 정보 확인 불가"
    if ram_total_bytes is not None:
        if ram_percent is not None:
            ram_summary = f"{format_bytes(ram_used_bytes)} / {format_bytes(ram_total_bytes)} ({ram_percent:.0f}%)"
        else:
            ram_summary = f"{format_bytes(ram_used_bytes)} / {format_bytes(ram_total_bytes)}"

    vram_summary = "VRAM 정보 확인 불가"
    if vram_total_bytes is not None:
        if vram_percent is not None:
            vram_summary = f"{format_bytes(vram_used_bytes)} / {format_bytes(vram_total_bytes)} ({vram_percent:.0f}%)"
        else:
            vram_summary = f"{format_bytes(vram_used_bytes)} / {format_bytes(vram_total_bytes)}"

    system_cpu_text = f"{system_cpu_percent:.0f}%" if system_cpu_percent is not None else "정보 없음"
    app_cpu_text = f"{app_cpu_percent:.0f}%" if app_cpu_percent is not None else "정보 없음"
    app_ram_text = format_bytes(app_ram_bytes) if app_ram_bytes is not None else "정보 없음"
    alert_text = ", ".join(alerts) if alerts else "시스템 자원 사용률이 안정적입니다."

    return {
        "level": level,
        "pressure_label": pressure_label,
        "alert_text": alert_text,
        "timestamp_text": sys_info.get("timestamp_text", ""),
        "cpu_percent": system_cpu_percent,
        "cpu_text": system_cpu_text,
        "system_cpu_percent": system_cpu_percent,
        "system_cpu_text": system_cpu_text,
        "app_cpu_percent": app_cpu_percent,
        "app_cpu_text": app_cpu_text,
        "app_ram_bytes": app_ram_bytes,
        "app_ram_text": app_ram_text,
        "ram_percent": ram_percent,
        "ram_text": ram_summary,
        "vram_percent": vram_percent,
        "vram_text": vram_summary,
        "gpu_name": gpu.get("name") or "감지되지 않음",
        "gpu_available": bool(gpu.get("available")),
        "os_text": sys_info.get("os_text", ""),
        "cpu_name": sys_info.get("cpu_name", ""),
        "python_version": sys_info.get("python_version", ""),
        "hf_home": sys_info.get("hf_home", ""),
        "ram_total_bytes": ram_total_bytes,
        "ram_used_bytes": ram_used_bytes,
        "vram_total_bytes": vram_total_bytes,
        "vram_used_bytes": vram_used_bytes,
    }


def collect_startup_status(settings: dict) -> dict:
    setup_runtime_environment()
    dll_count = register_cuda_dll_dirs()

    torch = _safe_import_torch()
    ct2 = _safe_import_ctranslate2()

    model_id = settings.get("model_id") or DEFAULT_MODEL_ID
    preferred_device = str(settings.get("preferred_device") or "auto")
    model_info = inspect_model_availability(model_id, include_remote_meta=False)

    torch_version = "N/A"
    torch_cuda_available = False
    if torch is not None:
        try:
            torch_version = str(torch.__version__)
            torch_cuda_available = bool(torch.cuda.is_available())
        except Exception:
            pass

    ct2_status = "확인 실패"
    ct2_cpu_types = set()
    ct2_cuda_types = set()

    if ct2 is not None:
        try:
            ct2_cpu_types = set(ct2.get_supported_compute_types("cpu"))
            ct2_status = "정상"
        except Exception:
            ct2_status = "확인 실패"

        try:
            ct2_cuda_types = set(ct2.get_supported_compute_types("cuda"))
        except Exception:
            ct2_cuda_types = set()

    sys_info = _read_system_resources()
    gpu = sys_info["gpu"]
    live_resources = collect_live_resource_status()

    if model_info["is_cached"]:
        model_summary = model_info['label']
        model_meta = "앱 캐시에 준비됨 · 재다운로드 없이 즉시 사용"
        model_level = "success"
    else:
        model_summary = model_info['label']
        model_meta = "아직 로컬에 없음 · 필요 시 다운로드"
        model_level = "warning"

    engine_summary = "CTranslate2 준비 완료" if ct2_status == "정상" else "CTranslate2 점검 필요"
    engine_meta = f"CPU 형식 {humanize_compute_types(ct2_cpu_types)} · CUDA 형식 {humanize_compute_types(ct2_cuda_types)}"
    engine_level = "success" if ct2_status == "정상" else "warning"

    torch_summary = "PyTorch 준비 상태"
    if preferred_device == "cpu":
        torch_meta = f"버전 {torch_version} · 이번 설정은 CPU 전용으로 실행"
        torch_level = "info"
    elif torch_cuda_available:
        torch_meta = f"버전 {torch_version} · CUDA 경로를 사용할 수 있음"
        torch_level = "success"
    else:
        torch_meta = f"버전 {torch_version} · 현재는 CPU 경로 중심으로 실행"
        torch_level = "warning"

    pref_label = {"auto": "자동 선택", "cuda": "GPU 우선", "cpu": "CPU 우선"}.get(preferred_device, preferred_device)
    if preferred_device == "cpu":
        device_summary = "CPU 실행"
        device_meta = f"감지 GPU: {gpu['name']} · 현재 설정에서는 사용하지 않음" if gpu.get('name') else "현재 설정에서는 CPU만 사용"
        device_level = "info"
    elif preferred_device == "cuda":
        if gpu.get('available'):
            device_summary = gpu['name']
            device_meta = f"GPU 우선 실행 · VRAM {format_bytes(gpu['vram_total_bytes'])}"
            device_level = "success"
        else:
            device_summary = "GPU 실행 예정"
            device_meta = "현재는 GPU 실사용 여부를 확정하지 못했습니다. 실행 전 검증이 필요합니다."
            device_level = "warning"
    else:
        if gpu.get('available'):
            device_summary = gpu['name']
            device_meta = f"자동 선택 · GPU 사용 가능 시 우선 사용 · VRAM {format_bytes(gpu['vram_total_bytes'])}"
            device_level = "success"
        else:
            device_summary = "자동 선택"
            device_meta = "GPU를 사용할 수 없으면 CPU로 자동 전환합니다."
            device_level = "warning"

    resource_summary = live_resources['pressure_label']
    resource_meta = f"앱 CPU {live_resources['app_cpu_text']} · 앱 RAM {live_resources['app_ram_text']}"
    resource_level = live_resources["level"]

    last_good_device = settings.get("last_good_device", "")
    last_good_compute_type = settings.get("last_good_compute_type", "")
    if last_good_device and last_good_compute_type:
        runtime_summary = f"최근 검증 성공 · {last_good_device} / {last_good_compute_type}"
        runtime_meta = f"현재 장치 선호: {pref_label}"
        runtime_level = "success"
    else:
        runtime_summary = "아직 검증 전"
        runtime_meta = f"현재 장치 선호: {pref_label} · 첫 실행 시 자동으로 확정합니다."
        runtime_level = "neutral"

    execution_mode = "포터블 실행 파일" if bool(getattr(sys, "frozen", False)) else "개발용 Python 실행"

    details = {
        "model": (
            f"선택 모델: {model_info['label']}\n"
            f"모델 설명: {model_info['long_note']}\n"
            f"로딩 대상 리포지토리: {model_info['repo_id']}\n"
            f"로컬 캐시 상태: {'준비 완료' if model_info['is_cached'] else '없음'}\n"
            f"캐시 위치: {model_info.get('cached_path_display') or '아직 다운로드되지 않음'}"
        ),
        "engine": (
            f"CTranslate2 상태: {ct2_status}\n"
            f"CPU 추론 형식: {humanize_compute_types(ct2_cpu_types)}\n"
            f"CUDA 추론 형식: {humanize_compute_types(ct2_cuda_types)}\n"
            f"미디어 입력 경로: Bundled FFmpeg 우선, 없으면 PyAV 직접 디코드 fallback\n"
            f"설명: 실제 실행 조합은 장치 점검 또는 전사 시작 시 다시 검증됩니다."
        ),
        "torch": (
            f"PyTorch 버전: {torch_version}\n"
            f"torch.cuda.is_available(): {torch_cuda_available}\n"
            f"CUDA DLL 등록 개수: {dll_count}\n"
            f"현재 선호 장치: {pref_label}\n"
            f"설명: CPU 모드를 선택해도 CUDA 지원 PyTorch 자체가 문제를 일으키는 것은 아니며, 실제 연산 경로는 device/compute_type으로 결정됩니다."
        ),
        "device": (
            f"선호 장치: {pref_label}\n"
            f"감지된 GPU: {gpu['name']}\n"
            f"Compute Capability: {gpu['capability']}\n"
            f"VRAM 총량: {format_bytes(gpu['vram_total_bytes'])}\n"
            f"VRAM 사용량: {format_bytes(gpu['vram_used_bytes'])}\n"
            f"VRAM 여유량: {format_bytes(gpu['vram_free_bytes'])}\n"
            f"장치 판단: {device_meta}"
        ),
        "resources": (
            f"실시간 자원 상태: {live_resources['pressure_label']}\n"
            f"경고 요약: {live_resources['alert_text']}\n"
            f"앱 CPU 점유율: {live_resources['app_cpu_text']}\n"
            f"시스템 CPU 점유율: {live_resources['system_cpu_text']}\n"
            f"앱 RAM: {live_resources['app_ram_text']}\n"
            f"시스템 RAM: {live_resources['ram_text']}\n"
            f"GPU VRAM: {live_resources['vram_text']}\n"
            f"감지된 GPU: {live_resources['gpu_name']}\n"
            f"운영체제: {sys_info['os_text']}\n"
            f"CPU: {sys_info['cpu_name']}\n"
            f"Python: {sys_info['python_version']}\n"
            f"Hugging Face 캐시 위치: {sys_info['hf_home']}\n"
            f"마지막 갱신: {live_resources['timestamp_text']}"
        ),
        "runtime": (
            f"실행 형식: {execution_mode}\n"
            f"선호 장치 설정: {preferred_device}\n"
            f"최근 성공 조합: {last_good_device or '없음'} / {last_good_compute_type or '없음'}\n"
            f"권장 해석: 범용 배포는 CPU 실행을 기본선으로 두고, 호환 가능한 NVIDIA GPU가 있을 때만 GPU 가속을 추가하는 구성이 가장 안정적입니다."
        ),
    }


    return {
        "model": {"level": model_level, "summary": model_summary, "meta": model_meta},
        "engine": {"level": engine_level, "summary": engine_summary, "meta": engine_meta},
        "torch": {"level": torch_level, "summary": torch_summary, "meta": torch_meta},
        "device": {"level": device_level, "summary": device_summary, "meta": device_meta},
        "resources": {"level": resource_level, "summary": resource_summary, "meta": resource_meta},
        "runtime": {"level": runtime_level, "summary": runtime_summary, "meta": runtime_meta},
        "details": details,
        "live_resources": live_resources,
    }


def choose_runtime_device_and_type(
    model_id: str,
    preferred_device: str = "auto",
    log: Callable[[str], None] | None = None,
) -> dict:
    setup_runtime_environment()
    register_cuda_dll_dirs()

    from faster_whisper import WhisperModel

    entry = get_model_entry(model_id)
    load_id = entry["load_id"]

    def emit(msg: str):
        if log:
            log(msg)

    gpu_candidates = ["float16", "int8_float16", "int8", "int8_float32", "float32"]
    cpu_candidates = ["int8", "int8_float32", "float32", "int16"]

    ct2 = _safe_import_ctranslate2()
    if ct2 is None:
        raise RuntimeError("ctranslate2를 불러오지 못했습니다.")

    try:
        gpu_types = set(ct2.get_supported_compute_types("cuda"))
    except Exception:
        gpu_types = set()

    try:
        cpu_types = set(ct2.get_supported_compute_types("cpu"))
    except Exception:
        cpu_types = set()

    if preferred_device == "cuda":
        order = ["cuda", "cpu"]
    elif preferred_device == "cpu":
        order = ["cpu"]
    else:
        order = ["cuda", "cpu"]

    errors = []

    for device in order:
        if device == "cuda":
            if not gpu_types:
                errors.append("CUDA compute type 조회 실패")
                continue
            for compute_type in gpu_candidates:
                if compute_type not in gpu_types:
                    continue
                try:
                    emit(f"장치 점검: {device} / {compute_type} 시도")
                    model = WhisperModel(load_id, device=device, compute_type=compute_type)
                    del model
                    return {
                        "device": device,
                        "compute_type": compute_type,
                        "reason": "모델 로딩 검증 성공",
                        "load_id": load_id,
                        "model_id": model_id,
                    }
                except Exception as e:
                    errors.append(f"{device}/{compute_type}: {e}")

        if device == "cpu":
            if not cpu_types:
                errors.append("CPU compute type 조회 실패")
                continue
            for compute_type in cpu_candidates:
                if compute_type not in cpu_types:
                    continue
                try:
                    emit(f"장치 점검: {device} / {compute_type} 시도")
                    model = WhisperModel(load_id, device=device, compute_type=compute_type)
                    del model
                    return {
                        "device": device,
                        "compute_type": compute_type,
                        "reason": "모델 로딩 검증 성공",
                        "load_id": load_id,
                        "model_id": model_id,
                    }
                except Exception as e:
                    errors.append(f"{device}/{compute_type}: {e}")

    raise RuntimeError("사용 가능한 장치/정밀도 조합을 찾지 못했습니다. | " + " | ".join(errors))
