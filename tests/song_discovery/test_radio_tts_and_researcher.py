"""Unit tests for radio TTS device selection, max_new_tokens, timeout fail-fast, and single-pass generation."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import torch

from src.researcher.radio_tts import (
    DEFAULT_MAX_NEW_TOKENS,
    DEFAULT_TTS_TIMEOUT_SEC,
    generate_radio_tts,
    get_device_and_dtype,
    get_max_new_tokens,
    get_tts_timeout,
    hard_timeout_enabled,
    resolve_voice_reference,
    reset_model,
    TTS_TIMEOUT_EXIT_CODE,
    use_mlx_backend,
)
from src.researcher.researcher import generate_radio_assets, generate_radio_copy


class TestRadioTTSDeviceAndTokens(unittest.TestCase):
    def setUp(self):
        reset_model()
        self._env_backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)
        reset_model()

    def test_device_selection_cuda(self):
        with patch.object(torch.cuda, "is_available", return_value=True):
            device, dtype = get_device_and_dtype()
            self.assertEqual(device, "cuda:0")
            self.assertEqual(dtype, torch.float16)

    def test_device_selection_mps_on_apple_silicon(self):
        with patch.object(torch.cuda, "is_available", return_value=False), \
             patch.object(torch.backends.mps, "is_available", return_value=True):
            device, dtype = get_device_and_dtype()
            self.assertEqual(device, "mps")
            self.assertEqual(dtype, torch.float16)

    def test_device_selection_cpu_fallback(self):
        with patch.object(torch.cuda, "is_available", return_value=False), \
             patch.object(torch.backends.mps, "is_available", return_value=False):
            device, dtype = get_device_and_dtype()
            self.assertEqual(device, "cpu")
            self.assertEqual(dtype, torch.float32)

    def test_device_selection_env_override(self):
        os.environ["TTS_DEVICE"] = "mps"
        os.environ["TTS_DTYPE"] = "float16"
        device, dtype = get_device_and_dtype()
        self.assertEqual(device, "mps")
        self.assertEqual(dtype, torch.float16)

        os.environ["TTS_DEVICE"] = "cpu"
        os.environ["TTS_DTYPE"] = "float32"
        device, dtype = get_device_and_dtype()
        self.assertEqual(device, "cpu")
        self.assertEqual(dtype, torch.float32)

    def test_max_new_tokens_default(self):
        os.environ.pop("QWEN3_TTS_MAX_NEW_TOKENS", None)
        os.environ.pop("TTS_MAX_NEW_TOKENS", None)
        self.assertEqual(get_max_new_tokens(), DEFAULT_MAX_NEW_TOKENS)
        self.assertEqual(get_max_new_tokens(), 128)

    def test_max_new_tokens_env_override(self):
        os.environ["QWEN3_TTS_MAX_NEW_TOKENS"] = "180"
        self.assertEqual(get_max_new_tokens(), 180)

        os.environ.pop("QWEN3_TTS_MAX_NEW_TOKENS", None)
        os.environ["TTS_MAX_NEW_TOKENS"] = "320"
        self.assertEqual(get_max_new_tokens(), 320)

    def test_max_new_tokens_invalid_env(self):
        os.environ["QWEN3_TTS_MAX_NEW_TOKENS"] = "not-a-number"
        self.assertEqual(get_max_new_tokens(), DEFAULT_MAX_NEW_TOKENS)

    def test_tts_timeout_default_and_env(self):
        os.environ.pop("TTS_TIMEOUT_SECONDS", None)
        os.environ.pop("QWEN3_TTS_TIMEOUT", None)
        self.assertEqual(get_tts_timeout(), DEFAULT_TTS_TIMEOUT_SEC)

        os.environ["TTS_TIMEOUT_SECONDS"] = "45.5"
        self.assertEqual(get_tts_timeout(), 45.5)

        os.environ.pop("TTS_TIMEOUT_SECONDS", None)
        os.environ["QWEN3_TTS_TIMEOUT"] = "90"
        self.assertEqual(get_tts_timeout(), 90.0)

    def test_hard_timeout_is_opt_in_for_dedicated_subprocess(self):
        os.environ.pop("TTS_HARD_TIMEOUT", None)
        self.assertFalse(hard_timeout_enabled())
        os.environ["TTS_HARD_TIMEOUT"] = "1"
        self.assertTrue(hard_timeout_enabled())

    def test_mlx_backend_is_selected_on_apple_silicon_when_runtime_exists(self):
        os.environ.pop("TTS_BACKEND", None)
        with patch("src.researcher.radio_tts.platform.system", return_value="Darwin"), \
             patch("src.researcher.radio_tts.platform.machine", return_value="arm64"), \
             patch("src.researcher.radio_tts.MLX_PYTHON") as mlx_python, \
             patch("src.researcher.radio_tts.MLX_WORKER") as mlx_worker:
            mlx_python.exists.return_value = True
            mlx_worker.exists.return_value = True
            self.assertTrue(use_mlx_backend())


class TestRadioTTSExecution(unittest.TestCase):
    def setUp(self):
        reset_model()
        self.test_dir = tempfile.mkdtemp()
        self._env_backup = dict(os.environ)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._env_backup)
        reset_model()

    @patch("src.researcher.radio_tts.get_model")
    @patch("src.researcher.radio_tts.load_or_create_voice_prompt")
    @patch("src.researcher.radio_tts.sf")
    @patch("src.researcher.radio_tts.use_mlx_backend", return_value=False)
    def test_generate_radio_tts_success(self, _mock_mlx, mock_sf, mock_load_prompt, mock_get_model):
        mock_model = MagicMock()
        import numpy as np
        fake_audio = np.zeros(24000, dtype=np.float32)
        mock_model.generate_voice_clone.return_value = ([fake_audio], 24000)
        mock_get_model.return_value = mock_model
        mock_load_prompt.return_value = ("fake_prompt", True)

        out_path = os.path.join(self.test_dir, "test.wav")
        ref_audio = os.path.join(self.test_dir, "ref.wav")

        result_path, duration = generate_radio_tts(
            text="测试电台串词",
            output_path=out_path,
            ref_audio=ref_audio,
            ref_text="参考文本",
            timeout_sec=5.0,
        )
        self.assertEqual(result_path, out_path)
        self.assertAlmostEqual(duration, 1.0)
        mock_sf.write.assert_called_once()
        mock_model.generate_voice_clone.assert_called_once()
        call_kwargs = mock_model.generate_voice_clone.call_args[1]
        self.assertEqual(call_kwargs["max_new_tokens"], 128)

    @patch("src.researcher.radio_tts.generate_mlx_radio_tts")
    @patch("src.researcher.radio_tts.use_mlx_backend", return_value=True)
    def test_generate_radio_tts_dispatches_to_mlx_worker(self, _mock_backend, mock_generate_mlx):
        output_path = os.path.join(self.test_dir, "mlx.wav")
        mock_generate_mlx.return_value = (output_path, 2.0, 4.0)
        with patch("src.researcher.radio_tts.get_model") as mock_get_model:
            result_path, duration = generate_radio_tts(
                text="MLX测试",
                output_path=output_path,
                ref_audio=os.path.join(self.test_dir, "ref.wav"),
                ref_text="参考文本",
                hard_timeout=False,
            )
        self.assertEqual(result_path, output_path)
        self.assertEqual(duration, 2.0)
        mock_generate_mlx.assert_called_once()
        mock_get_model.assert_not_called()

    @patch("src.researcher.radio_tts.get_model")
    @patch("src.researcher.radio_tts.load_or_create_voice_prompt")
    @patch("src.researcher.radio_tts.sf")
    @patch("src.researcher.radio_tts.use_mlx_backend", return_value=False)
    def test_generate_radio_tts_hard_timeout_terminates_dedicated_process(self, _mock_mlx, mock_sf, mock_load_prompt, mock_get_model):
        mock_model = MagicMock()
        mock_model.generate_voice_clone.return_value = ([MagicMock()], 24000)
        mock_get_model.return_value = mock_model
        mock_load_prompt.return_value = ("fake_prompt", True)

        out_path = os.path.join(self.test_dir, "test.wav")
        ref_audio = os.path.join(self.test_dir, "ref.wav")

        class ImmediateTimer:
            def __init__(self, interval, callback, args=()):
                self.callback = callback
                self.args = args
                self.daemon = False

            def start(self):
                self.callback(*self.args)

            def cancel(self):
                pass

        with patch("src.researcher.radio_tts.threading.Timer", ImmediateTimer), \
             patch("src.researcher.radio_tts.os._exit", side_effect=SystemExit(TTS_TIMEOUT_EXIT_CODE)) as mock_exit:
            with self.assertRaises(SystemExit) as ctx:
                generate_radio_tts(
                    text="测试超时电台串词",
                    output_path=out_path,
                    ref_audio=ref_audio,
                    ref_text="参考文本",
                    timeout_sec=0.05,
                    hard_timeout=True,
                )
        self.assertEqual(ctx.exception.code, TTS_TIMEOUT_EXIT_CODE)
        mock_exit.assert_called_once_with(TTS_TIMEOUT_EXIT_CODE)

    def test_generate_radio_tts_rejects_weak_clone_fallback(self):
        with self.assertRaisesRegex(ValueError, "exact reference transcript"):
            generate_radio_tts(
                text="不应该生成",
                output_path=os.path.join(self.test_dir, "weak.wav"),
                ref_audio=os.path.join(self.test_dir, "ref.wav"),
                ref_text=None,
            )

    def test_voice_manifest_resolves_audio_and_transcript_for_icl(self):
        voice_dir = os.path.join(self.test_dir, "voice")
        os.makedirs(voice_dir)
        with open(os.path.join(voice_dir, "reference_icl.wav"), "wb") as handle:
            handle.write(b"wav")
        with open(os.path.join(voice_dir, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "reference_audio": "reference_icl.wav",
                    "reference_text": "你好，我是参考文本。",
                    "clone_mode": "icl",
                },
                handle,
                ensure_ascii=False,
            )

        audio, transcript = resolve_voice_reference(voice_dir)

        self.assertEqual(audio, os.path.realpath(os.path.join(voice_dir, "reference_icl.wav")))
        self.assertEqual(transcript, "你好，我是参考文本。")


class TestResearcherRadioAssets(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.input_json = os.path.join(self.test_dir, "researched_playlist.json")
        self.output_json = os.path.join(self.test_dir, "output_playlist.json")
        self.voice_ref_dir = os.path.join(self.test_dir, "voice_ref")
        os.makedirs(self.voice_ref_dir, exist_ok=True)
        manifest_path = os.path.join(self.voice_ref_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "reference_audio": "ref.wav",
                "reference_text": "参考音频文本"
            }, f)
        with open(os.path.join(self.voice_ref_dir, "ref.wav"), "w") as f:
            f.write("fake-wav")

        self.sample_payload = {
            "tracks": [
                {
                    "id": 1001,
                    "name": "测试歌曲一",
                    "artists": ["测试乐队"],
                    "release_title": "测试专辑",
                    "normalized_release_type": "专辑",
                    "release_year": 2026,
                },
                {
                    "id": 1002,
                    "name": "测试歌曲二",
                    "artists": ["新乐队"],
                    "release_title": "新单曲",
                    "normalized_release_type": "单曲",
                    "release_year": 2026,
                    "is_new_song": True,
                },
            ]
        }
        with open(self.input_json, "w", encoding="utf-8") as f:
            json.dump(self.sample_payload, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("src.researcher.researcher.generate_radio_tts")
    def test_single_pass_generation_no_multi_retry(self, mock_generate_tts):
        # Return duration > 5.0s (e.g. 7.0s) which would previously trigger 2nd and 3rd retry
        mock_generate_tts.return_value = ("/fake/path/1001.wav", 7.0)

        generate_radio_assets(
            input_path=self.input_json,
            output_path=self.output_json,
            voice_ref_path=self.voice_ref_dir,
            force_tts=True,
            track_index=0,
        )

        # Ensure TTS was called EXACTLY ONCE for track 0
        self.assertEqual(mock_generate_tts.call_count, 1)

        with open(self.output_json, "r", encoding="utf-8") as f:
            result = json.load(f)

        track = result["tracks"][0]
        self.assertEqual(track["tts_duration_ms"], 7000)
        # Dynamic window calculation: min(9.0, max(5.0, 7.0 + 0.35)) = 7.35
        self.assertEqual(track["radio_window_sec"], 7.35)
        self.assertEqual(track["song_fadein_sec"], 7.35)
        self.assertEqual(track["clip_duration_sec"], 22.35)
        self.assertEqual(track["pure_song_duration_sec"], 15.0)

    @patch("src.researcher.researcher.generate_radio_tts")
    def test_fail_fast_on_tts_exception(self, mock_generate_tts):
        mock_generate_tts.side_effect = TimeoutError("TTS generation timed out after 60.0s")

        with self.assertRaises(TimeoutError):
            generate_radio_assets(
                input_path=self.input_json,
                output_path=self.output_json,
                voice_ref_path=self.voice_ref_dir,
                force_tts=True,
            )
