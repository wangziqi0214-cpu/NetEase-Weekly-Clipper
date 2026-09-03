"""Local NetEase API service manager with auto-start and graceful child termination."""

import os
import atexit
import subprocess
import time
from typing import Optional
from urllib.parse import urlparse

from song_discovery.exceptions import ServiceUnavailableError
from song_discovery.http_client import HttpClient


class NetEaseServiceManager:
    """
    Manages checking and starting the local NeteaseCloudMusicApi enhanced instance.
    Auto-starts @neteasecloudmusicapienhanced/api@4.40.1 via npx when running on localhost.
    Terminates only the child process it spawned.
    """

    PACKAGE_SPEC = "@neteasecloudmusicapienhanced/api@4.40.1"

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        auto_start: bool = True,
        timeout: float = 90.0,
        log_file: str = "output/netease_api.log",
        http_client: Optional[HttpClient] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auto_start = auto_start
        self.timeout = timeout
        self.log_file = log_file
        self.http_client = http_client or HttpClient(default_timeout=2.0, max_retries=0)
        self.process: Optional[subprocess.Popen] = None
        self.started_child: bool = False
        self._log_fh = None
        self._atexit_registered = False

    def is_localhost(self) -> bool:
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1")

    def probe(self) -> bool:
        """Probe the configured endpoint to see if service is responsive."""
        try:
            resp = self.http_client.get(f"{self.base_url}/login/status")
            return resp.status_code in (200, 301, 302, 400, 401, 404)
        except Exception:
            return False

    def start(self) -> bool:
        """
        Check if service is available; if not and on localhost, auto-start via npx.
        Returns True if service is ready.
        """
        if self.probe():
            self.started_child = False
            return True

        if not self.auto_start:
            raise ServiceUnavailableError(
                f"NetEase API service is not running at {self.base_url} (auto_start is disabled).",
                platform="netease",
                endpoint=self.base_url,
            )

        if not self.is_localhost():
            raise ServiceUnavailableError(
                f"NetEase API service is not running at remote URL {self.base_url}. Auto-start is only supported for localhost.",
                platform="netease",
                endpoint=self.base_url,
            )

        # Prepare log directory & file
        os.makedirs(os.path.dirname(os.path.abspath(self.log_file)), exist_ok=True)
        self._log_fh = open(self.log_file, "a", encoding="utf-8")
        self._log_fh.write(f"\n--- Starting NetEase API Service at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        self._log_fh.flush()

        cmd = ["npx", "--yes", self.PACKAGE_SPEC]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=self._log_fh,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
            self.started_child = True
            if not self._atexit_registered:
                atexit.register(self.stop)
                self._atexit_registered = True
        except Exception as exc:
            if self._log_fh:
                self._log_fh.close()
                self._log_fh = None
            raise ServiceUnavailableError(
                f"Failed to start NetEase API service via npx: {exc}",
                platform="netease",
                endpoint=self.base_url,
            ) from exc

        # Wait for readiness
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            # Check if process exited prematurely
            if self.process.poll() is not None:
                self.stop()
                raise ServiceUnavailableError(
                    f"NetEase API service process terminated unexpectedly with exit code {self.process.returncode}. Check logs at {self.log_file}.",
                    platform="netease",
                    endpoint=self.base_url,
                )

            if self.probe():
                return True
            time.sleep(0.5)

        # Timeout reached
        self.stop()
        raise ServiceUnavailableError(
            f"Timed out after {self.timeout}s waiting for NetEase API service to start at {self.base_url}. Check logs at {self.log_file}.",
            platform="netease",
            endpoint=self.base_url,
        )

    def stop(self) -> None:
        """Terminate the child process if we started it."""
        if self.started_child and self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            finally:
                self.process = None
                self.started_child = False

        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
