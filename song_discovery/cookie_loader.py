"""Netscape cookie file loader and NetEase login status validation."""

import os
import time
from typing import Any, Dict, Optional, Tuple
from song_discovery.exceptions import LoginRequiredError, PlatformRequestError
from song_discovery.http_client import HttpClient


def load_netscape_cookie_header(file_path: str) -> str:
    """
    Parse a Netscape format cookie file into a standard 'Cookie' header string.
    Never logs or outputs cookie values to maintain security.
    """
    if not os.path.exists(file_path):
        return ""

    cookies = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line_str = line.strip()
            if line_str.startswith("#HttpOnly_"):
                line_str = line_str[len("#HttpOnly_"):]
            elif not line_str or line_str.startswith("#"):
                continue
            parts = line_str.split("\t")
            if len(parts) >= 7:
                domain = parts[0].strip().lower()
                if not (domain == "163.com" or domain.endswith(".163.com")):
                    continue
                try:
                    expires_at = int(parts[4])
                    if expires_at > 0 and expires_at <= int(time.time()):
                        continue
                except (TypeError, ValueError):
                    pass
                name = parts[5].strip()
                value = parts[6].strip()
                if name:
                    cookies.append(f"{name}={value}")
    return "; ".join(cookies)


class NetEaseAuth:
    """Handles NetEase Cloud Music login status validation and authentication headers."""

    def __init__(self, base_url: str = "http://localhost:3000", http_client: Optional[HttpClient] = None):
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client or HttpClient()

    def get_login_status(self, cookie_file: Optional[str] = None, cookie_header: Optional[str] = None) -> Dict[str, Any]:
        """
        Query /login/status to check current user session.
        Never logs cookie contents.
        """
        cookie = cookie_header or (load_netscape_cookie_header(cookie_file) if cookie_file else "")
        headers = {}
        params = {}
        if cookie:
            headers["Cookie"] = cookie
            params["cookie"] = cookie

        url = f"{self.base_url}/login/status"
        try:
            resp = self.http_client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return {
                "is_logged_in": False,
                "user_id": None,
                "nickname": None,
                "error": f"Failed to connect to login status endpoint: {exc}",
            }

        # Parsing NetEase login status response format
        if not isinstance(data, dict):
            return {"is_logged_in": False, "user_id": None, "nickname": None}

        data_obj = data.get("data", {})
        profile = data_obj.get("profile") or data.get("profile")
        account = data_obj.get("account") or data.get("account") or {}

        is_anon = account.get("anonimous", False) or account.get("anonymous", False)
        is_logged_in = bool(profile and profile.get("userId") and not is_anon)

        return {
            "is_logged_in": is_logged_in,
            "user_id": str(profile.get("userId")) if profile else None,
            "nickname": profile.get("nickname") if profile else None,
            "vip_type": profile.get("vipType") if profile else None,
        }

    def require_login(self, cookie_file: Optional[str] = None, cookie_header: Optional[str] = None) -> Dict[str, Any]:
        """
        Validate login status, raising LoginRequiredError if not logged in.
        """
        status = self.get_login_status(cookie_file=cookie_file, cookie_header=cookie_header)
        if not status.get("is_logged_in"):
            err_detail = status.get("error") or "Session is anonymous, missing, or expired."
            raise LoginRequiredError(
                f"NetEase Cloud Music login required. {err_detail} Please verify cookie file."
            )
        return status
