"""Unit tests for NetEaseServiceManager."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from song_discovery.exceptions import ServiceUnavailableError
from song_discovery.http_client import HttpClient, HttpResponse
from song_discovery.netease_service import NetEaseServiceManager


class TestNetEaseServiceManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.log_file = os.path.join(self.test_dir, "api.log")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_service_already_running(self):
        class RunningTransport:
            def request(self, method, url, **kwargs):
                return HttpResponse(200, '{"code": 200}', url=url)

        client = HttpClient(transport=RunningTransport())
        mgr = NetEaseServiceManager(
            base_url="http://localhost:3000",
            log_file=self.log_file,
            http_client=client,
        )

        ready = mgr.start()
        self.assertTrue(ready)
        self.assertFalse(mgr.started_child)
        self.assertIsNone(mgr.process)

        # Stop should be no-op
        mgr.stop()
        self.assertFalse(mgr.started_child)

    @patch("subprocess.Popen")
    def test_service_auto_start_and_cleanup(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        # First probe fails, second probe succeeds
        responses = [Exception("Refused"), HttpResponse(200, '{"code": 200}')]

        class DynamicTransport:
            def request(self, method, url, **kwargs):
                if responses:
                    res = responses.pop(0)
                    if isinstance(res, Exception):
                        raise res
                    return res
                return HttpResponse(200, "{}")

        client = HttpClient(transport=DynamicTransport())
        mgr = NetEaseServiceManager(
            base_url="http://localhost:3000",
            auto_start=True,
            timeout=2.0,
            log_file=self.log_file,
            http_client=client,
        )

        with mgr:
            self.assertTrue(mgr.started_child)
            mock_popen.assert_called_once()
            cmd_args = mock_popen.call_args[0][0]
            self.assertIn("@neteasecloudmusicapienhanced/api@4.40.1", cmd_args)

        # After exiting context manager, child must be terminated
        mock_proc.terminate.assert_called_once()
        self.assertFalse(mgr.started_child)

    def test_auto_start_disabled_raises_error(self):
        class DownTransport:
            def request(self, method, url, **kwargs):
                raise Exception("Down")

        client = HttpClient(transport=DownTransport())
        mgr = NetEaseServiceManager(
            base_url="http://localhost:3000",
            auto_start=False,
            log_file=self.log_file,
            http_client=client,
        )

        with self.assertRaises(ServiceUnavailableError):
            mgr.start()


if __name__ == "__main__":
    unittest.main()
