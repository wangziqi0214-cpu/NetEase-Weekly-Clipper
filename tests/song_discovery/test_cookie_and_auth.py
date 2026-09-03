"""Unit tests for Netscape cookie parsing and NetEase authentication."""

import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from song_discovery.cookie_loader import NetEaseAuth, load_netscape_cookie_header
from song_discovery.exceptions import LoginRequiredError
from song_discovery.http_client import HttpClient, HttpResponse


class TestCookieAndAuth(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.cookie_path = os.path.join(self.test_dir, "test_cookie.txt")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_netscape_cookie_header(self):
        content = (
            "# Netscape HTTP Cookie File\n"
            "# http://curl.haxx.se/rfc/cookie_spec.html\n"
            ".music.163.com\tTRUE\t/\tTRUE\t1790000000\tMUSIC_U\tabc123xyz\n"
            ".music.163.com\tTRUE\t/\tFALSE\t1790000000\t__csrf\tcsrf789\n"
            "\n"
            "# Comment line\n"
        )
        with open(self.cookie_path, "w", encoding="utf-8") as f:
            f.write(content)

        header = load_netscape_cookie_header(self.cookie_path)
        self.assertIn("MUSIC_U=abc123xyz", header)
        self.assertIn("__csrf=csrf789", header)

    def test_get_login_status_success(self):
        class MockAuthTransport:
            def request(self, method, url, **kwargs):
                return HttpResponse(
                    200,
                    '{"data": {"profile": {"userId": 123456, "nickname": "RockFan", "vipType": 11}, "account": {"anonimous": false}}}',
                    url=url,
                )

        client = HttpClient(transport=MockAuthTransport())
        auth = NetEaseAuth(http_client=client)
        status = auth.get_login_status(cookie_header="MUSIC_U=test")

        self.assertTrue(status["is_logged_in"])
        self.assertEqual(status["user_id"], "123456")
        self.assertEqual(status["nickname"], "RockFan")
        self.assertNotIn("raw", status)

    def test_http_only_and_expired_cookie_handling(self):
        future = int(time.time()) + 3600
        past = int(time.time()) - 3600
        content = (
            f"#HttpOnly_.music.163.com\tTRUE\t/\tTRUE\t{future}\tMUSIC_U\tvalid-token\n"
            f".music.163.com\tTRUE\t/\tFALSE\t{past}\t__csrf\texpired-token\n"
            f".example.com\tTRUE\t/\tFALSE\t{future}\tOTHER\tforeign-token\n"
        )
        with open(self.cookie_path, "w", encoding="utf-8") as f:
            f.write(content)

        header = load_netscape_cookie_header(self.cookie_path)
        self.assertEqual(header, "MUSIC_U=valid-token")

    def test_require_login_raises_on_anonymous_or_failure(self):
        class MockAnonTransport:
            def request(self, method, url, **kwargs):
                return HttpResponse(
                    200,
                    '{"data": {"profile": null, "account": {"anonimous": true}}}',
                    url=url,
                )

        client = HttpClient(transport=MockAnonTransport())
        auth = NetEaseAuth(http_client=client)

        with self.assertRaises(LoginRequiredError):
            auth.require_login(cookie_header="invalid")


if __name__ == "__main__":
    unittest.main()
