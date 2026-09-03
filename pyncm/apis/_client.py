from __future__ import annotations

from typing import Any

from pyncm import GetCurrentSession

BASE_URL = "https://music.163.com"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Referer": "https://music.163.com/",
}


def request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = GetCurrentSession().get(
        f"{BASE_URL}{path}",
        params=params or {},
        headers=DEFAULT_HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
