import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import run


class RunPipelineFailFastTests(unittest.TestCase):
    @patch("run.subprocess.run")
    def test_research_prepare_requires_successful_subprocess(self, mock_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = str(Path(temp_dir) / "researched.json")
            mock_run.side_effect = RuntimeError("research failed")
            with self.assertRaisesRegex(RuntimeError, "research failed"):
                run.run_research_prepare("raw.json", output)
            self.assertTrue(mock_run.call_args.kwargs["check"])

    @patch("run.subprocess.run")
    def test_renderer_passes_task_paths_and_requires_output(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        with tempfile.TemporaryDirectory() as temp_dir:
            output = str(Path(temp_dir) / "final_video.mp4")
            intro = str(Path(temp_dir) / "intro.mp4")
            with self.assertRaisesRegex(RuntimeError, "Final video was not created"):
                run.run_renderer("project.json", output_path=output, intro_path=intro)
            command = mock_run.call_args.args[0]
            self.assertEqual(command[-4:], ["--output-path", output, "--intro-path", intro])
            self.assertTrue(mock_run.call_args.kwargs["check"])

    def test_managed_artifact_paths_are_job_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = run._artifact_paths(temp_dir, "researched.json")
            self.assertEqual(paths["final_video"], os.path.join(temp_dir, "final_video.mp4"))
            self.assertEqual(paths["release_report"], os.path.join(temp_dir, "release_report.md"))
            self.assertEqual(paths["publish_copy"], os.path.join(temp_dir, "publish_copy.md"))
            self.assertEqual(paths["publish_copy_auto"], os.path.join(temp_dir, "publish_copy.auto.md"))

    def test_run_publish_copy_export_generates_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "researched.json"
            json_path.write_text(
                json.dumps({
                    "playlist_name": "华语新歌周刊 2026年第35周",
                    "tracks": [
                        {"name": "单曲A", "artists": ["乐队A"], "normalized_release_type": "单曲"},
                    ],
                }),
                encoding="utf-8",
            )
            result = run.run_publish_copy_export(project_file=str(json_path), artifact_dir=temp_dir)
            self.assertIsNotNone(result)
            self.assertTrue((Path(temp_dir) / "publish_copy.auto.md").is_file())
            self.assertTrue((Path(temp_dir) / "publish_copy.md").is_file())


if __name__ == "__main__":
    unittest.main()
