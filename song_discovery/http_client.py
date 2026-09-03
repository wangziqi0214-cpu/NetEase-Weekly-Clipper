"""Injectable HTTP client with timeout, retries, and clear exception handling."""

import json
import ssl
import time
from typing import Any, Dict, Optional, Protocol, Union
from song_discovery.exceptions import PlatformRequestError, ServiceUnavailableError

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    import urllib.request
    import urllib.parse
    import urllib.error
    _REQUESTS_AVAILABLE = False


class HttpResponse:
    """Standardized HTTP response."""

    def __init__(self, status_code: int, text: str, headers: Optional[Dict[str, str]] = None, url: str = ""):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.url = url

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise PlatformRequestError(
                f"Failed to parse JSON response from {self.url}: {exc}",
                status_code=self.status_code,
            ) from exc

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 500:
            raise PlatformRequestError(
                f"Client error {self.status_code} for URL: {self.url}",
                status_code=self.status_code,
            )
        elif self.status_code >= 500:
            raise PlatformRequestError(
                f"Server error {self.status_code} for URL: {self.url}",
                status_code=self.status_code,
            )


class HttpTransport(Protocol):
    """Protocol for pluggable HTTP transport / session."""
    def request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        json_body: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        ...


class HttpClient:
    """
    Standard HTTP client supporting custom session/transport,
    configurable timeout, and automatic retries on transient errors.
    """

    def __init__(
        self,
        session: Optional[Any] = None,
        transport: Optional[HttpTransport] = None,
        default_timeout: float = 10.0,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        default_headers: Optional[Dict[str, str]] = None,
        verify_ssl: bool = True,
    ):
        self.session = session
        self.transport = transport
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.verify_ssl = verify_ssl
        self.default_headers = default_headers or {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }

    def request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        json_body: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        timeout_val = timeout if timeout is not None else self.default_timeout
        merged_headers = dict(self.default_headers)
        if headers:
            merged_headers.update(headers)

        if self.transport is not None:
            return self.transport.request(
                method=method,
                url=url,
                params=params,
                data=data,
                json_body=json_body,
                headers=merged_headers,
                timeout=timeout_val,
            )

        last_exception: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._perform_request(
                    method=method,
                    url=url,
                    params=params,
                    data=data,
                    json_body=json_body,
                    headers=merged_headers,
                    timeout=timeout_val,
                )
            except (PlatformRequestError, ServiceUnavailableError) as exc:
                last_exception = exc
                if isinstance(exc, PlatformRequestError) and exc.status_code and exc.status_code < 500:
                    raise exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise exc
            except Exception as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                raise PlatformRequestError(f"HTTP request failed: {exc}") from exc

        if last_exception:
            raise last_exception
        raise PlatformRequestError("Request failed after retries")

    def _perform_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]],
        data: Optional[Any],
        json_body: Optional[Any],
        headers: Dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        if _REQUESTS_AVAILABLE:
            return self._perform_requests_lib(
                method=method,
                url=url,
                params=params,
                data=data,
                json_body=json_body,
                headers=headers,
                timeout=timeout,
            )
        else:
            return self._perform_urllib(
                method=method,
                url=url,
                params=params,
                data=data,
                json_body=json_body,
                headers=headers,
                timeout=timeout,
            )

    def _perform_requests_lib(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]],
        data: Optional[Any],
        json_body: Optional[Any],
        headers: Dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        sess = self.session or requests
        try:
            resp = sess.request(
                method=method.upper(),
                url=url,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
                timeout=timeout,
                verify=self.verify_ssl,
            )
            return HttpResponse(
                status_code=resp.status_code,
                text=resp.text,
                headers=dict(resp.headers),
                url=resp.url,
            )
        except requests.exceptions.ConnectionError as exc:
            raise ServiceUnavailableError(
                f"Connection error to {url}: {exc}", endpoint=url
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise PlatformRequestError(
                f"Request timeout ({timeout}s) to {url}: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise PlatformRequestError(
                f"Requests error for {url}: {exc}"
            ) from exc

    def _perform_urllib(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]],
        data: Optional[Any],
        json_body: Optional[Any],
        headers: Dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        import urllib.parse
        import urllib.request
        import urllib.error

        req_url = url
        if params:
            query = urllib.parse.urlencode(params)
            req_url = f"{url}?{query}" if "?" not in url else f"{url}&{query}"

        body_bytes = None
        req_headers = dict(headers)
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            req_headers["Content-Type"] = "application/json; charset=utf-8"
        elif data is not None:
            if isinstance(data, dict):
                body_bytes = urllib.parse.urlencode(data).encode("utf-8")
                req_headers["Content-Type"] = "application/x-www-form-urlencoded"
            elif isinstance(data, (bytes, bytearray)):
                body_bytes = bytes(data)
            elif isinstance(data, str):
                body_bytes = data.encode("utf-8")

        req = urllib.request.Request(
            url=req_url,
            data=body_bytes,
            headers=req_headers,
            method=method.upper(),
        )

        def _open_with_context(ctx):
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                status_code = response.getcode()
                resp_body = response.read().decode("utf-8", errors="replace")
                resp_headers = dict(response.info())
                return HttpResponse(
                    status_code=status_code,
                    text=resp_body,
                    headers=resp_headers,
                    url=req_url,
                )

        try:
            ssl_ctx = (
                ssl.create_default_context()
                if self.verify_ssl
                else ssl._create_unverified_context()
            )
            return _open_with_context(ssl_ctx)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return HttpResponse(
                status_code=exc.code,
                text=body,
                headers=dict(exc.headers) if hasattr(exc, "headers") else {},
                url=req_url,
            )
        except urllib.error.URLError as exc:
            raise ServiceUnavailableError(
                f"Service unreachable at {req_url}: {exc.reason}",
                endpoint=req_url,
            ) from exc
        except Exception as exc:
            raise PlatformRequestError(
                f"HTTP request error for {req_url}: {exc}"
            ) from exc

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        return self.request("GET", url, params=params, headers=headers, timeout=timeout)

    def post(
        self,
        url: str,
        data: Optional[Any] = None,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        return self.request(
            "POST",
            url,
            params=params,
            data=data,
            json_body=json_body,
            headers=headers,
            timeout=timeout,
        )
