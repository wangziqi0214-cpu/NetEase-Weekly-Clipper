"""Persistent MLX-Audio worker for Apple Silicon Qwen3-TTS voice cloning.

The worker uses a line-delimited JSON protocol on stdin/stdout so the main
research process can reuse one loaded model for every track in a weekly batch.
All library chatter is redirected to stderr; stdout is reserved for protocol
messages prefixed with ``__MLX_TTS__``.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
from mlx_audio.tts.utils import load_model


PROTOCOL_PREFIX = "__MLX_TTS__"
DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"


def emit(payload: dict) -> None:
    print(PROTOCOL_PREFIX + json.dumps(payload, ensure_ascii=False), flush=True)


def write_wav(path: str, audio, sample_rate: int) -> tuple[str, float]:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return str(output), len(samples) / float(sample_rate)


def main() -> int:
    model_name = os.getenv("MLX_TTS_MODEL", DEFAULT_MODEL)
    started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        model = load_model(model_name)
    emit({"type": "ready", "model": model_name, "load_seconds": time.perf_counter() - started})

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        request = json.loads(raw_line)
        request_id = request.get("id")
        started = time.perf_counter()
        try:
            ref_text = str(request.get("ref_text") or "").strip()
            if not ref_text:
                raise ValueError(
                    "ref_text is required for full ICL voice cloning; "
                    "refusing weak speaker-embedding fallback"
                )
            with contextlib.redirect_stdout(sys.stderr):
                results = list(
                    model.generate(
                        text=request["text"],
                        ref_audio=request["ref_audio"],
                        ref_text=ref_text,
                        lang_code=request.get("language", "chinese").lower(),
                        max_tokens=int(request.get("max_tokens", 128)),
                        verbose=False,
                        stream=False,
                    )
                )
            if not results:
                raise RuntimeError("MLX-Audio returned no generation results")
            result = results[0]
            output_path, duration = write_wav(
                request["output_path"], result.audio, int(result.sample_rate)
            )
            emit(
                {
                    "type": "result",
                    "id": request_id,
                    "output_path": output_path,
                    "duration_sec": duration,
                    "elapsed_sec": time.perf_counter() - started,
                }
            )
        except Exception as exc:
            emit({"type": "error", "id": request_id, "error": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
