from __future__ import annotations

import hashlib
import json
import os
import pickle
import platform
import subprocess
import threading
import time
import uuid
import atexit
from pathlib import Path
from typing import Any

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    import torch
except ImportError:
    torch = None

try:
    from qwen_tts import Qwen3TTSModel
except ImportError:
    Qwen3TTSModel = None

try:
    from transformers import logging as hf_logging
except ImportError:
    hf_logging = None


DEFAULT_MODEL_NAME = os.getenv(
    "QWEN3_TTS_MODEL_PATH",
    "models/Qwen3-TTS-12Hz-1.7B-Base"
    if Path("models/Qwen3-TTS-12Hz-1.7B-Base").exists()
    else "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
)
DEFAULT_MAX_NEW_TOKENS = 128
DEFAULT_TTS_TIMEOUT_SEC = 60.0
TTS_TIMEOUT_EXIT_CODE = 124
PROTOCOL_PREFIX = "__MLX_TTS__"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MLX_PYTHON = PROJECT_ROOT / ".venv-mlx" / "bin" / "python"
MLX_WORKER = PROJECT_ROOT / "src" / "researcher" / "mlx_tts_worker.py"
PROMPT_CACHE_DIR = Path("assets/radio_voice/cache")
AUDIO_CACHE_DIR = Path("assets/radio_voice/generated")
_MODEL = None
_MLX_PROCESS = None


def get_device_and_dtype() -> tuple[str, Any]:
    """Determine the optimal compute device and precision for Qwen3 TTS.

    Priority:
    1. Explicit env overrides (TTS_DEVICE, TTS_DTYPE)
    2. CUDA -> cuda:0, float16
    3. Apple Silicon (MPS) -> mps, float16
    4. CPU fallback -> cpu, float32 (with explicit downgrade warning)
    """
    env_device = os.getenv("TTS_DEVICE")
    if env_device:
        env_dtype_str = (os.getenv("TTS_DTYPE") or "float16").lower()
        env_dtype = (
            torch.float16
            if "16" in env_dtype_str
            else (torch.float32 if torch is not None else None)
        )
        return env_device, env_dtype

    if torch is not None and torch.cuda.is_available():
        return "cuda:0", torch.float16

    if (
        torch is not None
        and hasattr(torch.backends, "mps")
        and torch.backends.mps.is_available()
    ):
        return "mps", torch.float16

    return "cpu", (torch.float32 if torch is not None else None)


def get_max_new_tokens() -> int:
    """Retrieve max_new_tokens suitable for short Chinese radio intro sentences."""
    env_val = os.getenv("QWEN3_TTS_MAX_NEW_TOKENS") or os.getenv("TTS_MAX_NEW_TOKENS")
    if env_val:
        try:
            val = int(env_val)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_MAX_NEW_TOKENS


def get_tts_timeout() -> float:
    """Retrieve timeout in seconds for a single TTS generation."""
    env_val = os.getenv("TTS_TIMEOUT_SECONDS") or os.getenv("QWEN3_TTS_TIMEOUT")
    if env_val:
        try:
            val = float(env_val)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_TTS_TIMEOUT_SEC


def hard_timeout_enabled() -> bool:
    """Enable process-level timeout only in the dedicated TTS subprocess."""
    return (os.getenv("TTS_HARD_TIMEOUT") or "").strip().lower() in {"1", "true", "yes", "on"}


def use_mlx_backend() -> bool:
    requested = (os.getenv("TTS_BACKEND") or "auto").strip().lower()
    if requested in {"pytorch", "torch"}:
        return False
    if requested == "mlx":
        if not MLX_PYTHON.exists():
            raise RuntimeError(f"TTS_BACKEND=mlx but isolated MLX runtime is missing: {MLX_PYTHON}")
        return True
    return (
        platform.system() == "Darwin"
        and platform.machine() == "arm64"
        and MLX_PYTHON.exists()
        and MLX_WORKER.exists()
    )


def close_mlx_worker(force: bool = False) -> None:
    global _MLX_PROCESS
    process = _MLX_PROCESS
    _MLX_PROCESS = None
    if process is None or process.poll() is not None:
        return
    if not force and process.stdin is not None:
        process.stdin.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        process.wait(timeout=5)


atexit.register(close_mlx_worker)


def _terminate_tts_process(timeout: float, text: str) -> None:
    """Terminate the dedicated researcher subprocess when native inference hangs.

    A Python exception cannot reliably interrupt a long-running PyTorch/MPS native
    operation. The parent workflow already treats a non-zero subprocess exit as a
    failure, so a process-level timeout is the only reliable fail-fast boundary.
    """
    print(
        f"[!] [TTS] HARD TIMEOUT after {timeout:.1f}s for text: '{text}'. "
        f"Terminating dedicated TTS subprocess with exit code {TTS_TIMEOUT_EXIT_CODE}.",
        flush=True,
    )
    close_mlx_worker(force=True)
    os._exit(TTS_TIMEOUT_EXIT_CODE)


def reset_model() -> None:
    """Reset loaded singleton model instance (useful for testing or re-initialization)."""
    global _MODEL
    _MODEL = None
    close_mlx_worker()


def get_mlx_worker():
    global _MLX_PROCESS
    if _MLX_PROCESS is not None and _MLX_PROCESS.poll() is None:
        return _MLX_PROCESS
    print(f"[*] [TTS] Starting persistent MLX worker via {MLX_PYTHON}...", flush=True)
    _MLX_PROCESS = subprocess.Popen(
        [str(MLX_PYTHON), str(MLX_WORKER)],
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    while True:
        line = _MLX_PROCESS.stdout.readline()
        if not line:
            raise RuntimeError(f"MLX TTS worker exited during startup with code {_MLX_PROCESS.poll()}")
        if line.startswith(PROTOCOL_PREFIX):
            payload = json.loads(line[len(PROTOCOL_PREFIX):])
            if payload.get("type") == "ready":
                print(
                    f"[*] [TTS] MLX model ready in {payload.get('load_seconds', 0):.2f}s: {payload.get('model')}",
                    flush=True,
                )
                return _MLX_PROCESS


def generate_mlx_radio_tts(
    text: str,
    output_path: str,
    ref_audio: str,
    ref_text: str | None,
    language: str,
    max_new_tokens: int,
) -> tuple[str, float, float]:
    process = get_mlx_worker()
    request_id = uuid.uuid4().hex
    request = {
        "id": request_id,
        "text": text,
        "output_path": str(Path(output_path).resolve()),
        "ref_audio": str(Path(ref_audio).resolve()),
        "ref_text": ref_text,
        "language": language,
        "max_tokens": max_new_tokens,
    }
    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.flush()
    while True:
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(f"MLX TTS worker exited with code {process.poll()}")
        if not line.startswith(PROTOCOL_PREFIX):
            continue
        payload = json.loads(line[len(PROTOCOL_PREFIX):])
        if payload.get("id") != request_id:
            continue
        if payload.get("type") == "error":
            raise RuntimeError(f"MLX TTS generation failed: {payload.get('error')}")
        return (
            str(payload["output_path"]),
            float(payload["duration_sec"]),
            float(payload.get("elapsed_sec") or 0.0),
        )


def ensure_dirs() -> None:
    PROMPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_model(model_name: str = DEFAULT_MODEL_NAME):
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    if torch is None or Qwen3TTSModel is None:
        raise ImportError("Radio TTS requires torch and qwen-tts. Install them before using --force-tts or voice references.")

    if hf_logging is not None:
        hf_logging.set_verbosity_error()

    device, dtype = get_device_and_dtype()
    if device == "cpu":
        print(
            "[!] [TTS] Warning: CUDA/MPS not available. Falling back to CPU with float32 (generation will be slower).",
            flush=True,
        )
    else:
        print(
            f"[*] [TTS] Initializing model '{model_name}' on device='{device}', dtype={dtype}...",
            flush=True,
        )

    t0 = time.perf_counter()
    _MODEL = Qwen3TTSModel.from_pretrained(
        model_name,
        device_map=device,
        dtype=dtype,
        attn_implementation="sdpa",
    )
    load_time = time.perf_counter() - t0
    print(
        f"[*] [TTS] Model loaded successfully in {load_time:.2f}s (device={device}, dtype={dtype}).",
        flush=True,
    )
    return _MODEL


def prompt_cache_path(ref_audio: str, ref_text: str | None, use_x_vector_only: bool) -> Path:
    signature = hashlib.sha1(
        f"{Path(ref_audio).resolve()}|{ref_text or ''}|{use_x_vector_only}|{DEFAULT_MODEL_NAME}".encode("utf-8")
    ).hexdigest()[:16]
    return PROMPT_CACHE_DIR / f"clone_prompt_{signature}.pkl"


def resolve_voice_reference(ref_path: str, ref_text: str | None = None) -> tuple[str, str | None]:
    path = Path(ref_path)

    if path.is_dir():
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Voice asset manifest not found: {manifest_path}")

        with manifest_path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)

        reference_audio = manifest.get("reference_audio")
        if not reference_audio:
            raise ValueError(f"Voice asset manifest missing reference_audio: {manifest_path}")

        resolved_audio = (path / reference_audio).resolve()
        if not resolved_audio.exists():
            raise FileNotFoundError(f"Voice asset reference audio not found: {resolved_audio}")

        resolved_text = ref_text if ref_text is not None else manifest.get("reference_text")
        return str(resolved_audio), resolved_text

    if not path.exists():
        raise FileNotFoundError(f"Voice reference not found: {path}")

    return str(path.resolve()), ref_text


def load_or_create_voice_prompt(ref_audio: str, ref_text: str | None) -> tuple[object, bool]:
    ensure_dirs()
    use_x_vector_only = not bool(ref_text)
    cache_path = prompt_cache_path(ref_audio, ref_text, use_x_vector_only)
    if cache_path.exists():
        print(f"[*] [TTS] Loaded voice clone prompt from cache: {cache_path.name}", flush=True)
        with cache_path.open("rb") as handle:
            return pickle.load(handle), use_x_vector_only

    print(f"[*] [TTS] Creating voice clone prompt for '{ref_audio}'...", flush=True)
    t0 = time.perf_counter()
    model = get_model()
    prompt_items = model.create_voice_clone_prompt(
        ref_audio=ref_audio,
        ref_text=ref_text,
        x_vector_only_mode=use_x_vector_only,
    )
    with cache_path.open("wb") as handle:
        pickle.dump(prompt_items, handle)
    elapsed = time.perf_counter() - t0
    print(
        f"[*] [TTS] Voice clone prompt created and cached in {elapsed:.2f}s -> {cache_path.name}",
        flush=True,
    )
    return prompt_items, use_x_vector_only


def generate_radio_tts(
    text: str,
    output_path: str,
    ref_audio: str,
    ref_text: str | None = None,
    language: str = "Chinese",
    timeout_sec: float | None = None,
    hard_timeout: bool | None = None,
) -> tuple[str, float]:
    if sf is None:
        raise ImportError("Radio TTS requires soundfile.")
    if not str(ref_text or "").strip():
        raise ValueError(
            "Full voice cloning requires an exact reference transcript. "
            "ref_text is empty, so generation was stopped instead of silently "
            "falling back to weak speaker-embedding cloning."
        )

    ensure_dirs()
    max_new_tokens = get_max_new_tokens()
    timeout = timeout_sec if timeout_sec is not None else get_tts_timeout()
    use_hard_timeout = hard_timeout_enabled() if hard_timeout is None else hard_timeout
    backend = "mlx" if use_mlx_backend() else "pytorch"

    timeout_label = f"{timeout}s" if use_hard_timeout else "disabled"
    print(
        f"[*] [TTS] Starting speech generation (text_len={len(text)}, "
        f"backend={backend}, max_new_tokens={max_new_tokens}, hard_timeout={timeout_label}): '{text}'",
        flush=True,
    )
    t0 = time.perf_counter()

    timeout_timer = None
    if use_hard_timeout and timeout and timeout > 0:
        timeout_timer = threading.Timer(timeout, _terminate_tts_process, args=(timeout, text))
        timeout_timer.daemon = True
        timeout_timer.start()

    try:
        if backend == "mlx":
            generated_path, duration_sec, gen_time = generate_mlx_radio_tts(
                text=text,
                output_path=output_path,
                ref_audio=ref_audio,
                ref_text=ref_text,
                language=language,
                max_new_tokens=max_new_tokens,
            )
            rtf = gen_time / duration_sec if duration_sec > 0 else 0.0
            print(
                f"[*] [TTS] MLX generated {duration_sec:.2f}s audio in {gen_time:.2f}s "
                f"(RTF: {rtf:.2f}x) -> {Path(generated_path).name}",
                flush=True,
            )
            return generated_path, duration_sec

        model = get_model()
        prompt_items, use_x_vector_only = load_or_create_voice_prompt(ref_audio, ref_text)
        wavs, sample_rate = model.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=prompt_items,
                x_vector_only_mode=use_x_vector_only,
                non_streaming_mode=True,
                max_new_tokens=max_new_tokens,
            )
    finally:
        if timeout_timer is not None:
            timeout_timer.cancel()

    gen_time = time.perf_counter() - t0
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_file, wavs[0], sample_rate)
    duration_sec = len(wavs[0]) / float(sample_rate)
    rtf = gen_time / duration_sec if duration_sec > 0 else 0.0
    print(
        f"[*] [TTS] Generated {duration_sec:.2f}s audio in {gen_time:.2f}s (RTF: {rtf:.2f}x) -> {output_file.name}",
        flush=True,
    )
    return str(output_file), duration_sec
