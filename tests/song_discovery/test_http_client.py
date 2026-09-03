"""Unit tests for HttpClient and transport injection."""

import unittest
from song_discovery.exceptions import PlatformRequestError
from song_discovery.http_client import HttpClient, HttpResponse


class MockTransport:
    """Mock HTTP transport for testing without network."""
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, url, params=None, data=None, json_body=None, headers=None, timeout=10.0):
        self.calls.append({
            "method": method,
            "url": url,
            "params": params,
            "data": data,
            "json_body": json_body,
            "headers": headers,
            "timeout": timeout,
        })
        if not self.responses:
            return HttpResponse(200, "{}", url=url)
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class TestHttpClient(unittest.TestCase):
    def test_custom_transport_injection(self):
        mock_transport = MockTransport([
            HttpResponse(200, '{"status": "ok"}', url="http://api.test/data")
        ])
        client = HttpClient(transport=mock_transport)

        resp = client.get("http://api.test/data", params={"q": "music"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})
        self.assertEqual(len(mock_transport.calls), 1)
        self.assertEqual(mock_transport.calls[0]["params"], {"q": "music"})

    def test_response_json_error(self):
        resp = HttpResponse(200, "invalid json string", url="http://api.test")
        with self.assertRaises(PlatformRequestError):
            resp.json()

    def test_raise_for_status(self):
        resp_404 = HttpResponse(404, "Not Found", url="http://api.test")
        with self.assertRaises(PlatformRequestError) as ctx:
            resp_404.raise_for_status()
        self.assertEqual(ctx.exception.status_code, 404)

        resp_500 = HttpResponse(500, "Server Error", url="http://api.test")
        with self.assertRaises(PlatformRequestError) as ctx:
            resp_500.raise_for_status()
        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
