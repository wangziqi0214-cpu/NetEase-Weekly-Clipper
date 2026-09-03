"""Exceptions for the song_discovery package."""


class SongDiscoveryError(Exception):
    """Base exception for all song discovery errors."""
    pass


class PlatformRequestError(SongDiscoveryError):
    """Raised when an HTTP request to a platform fails."""
    def __init__(self, message: str, status_code: int = None, platform: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.platform = platform


class PlatformResponseError(SongDiscoveryError):
    """Raised when the platform response format or data is unexpected."""
    def __init__(self, message: str, platform: str = None, raw_response: dict = None):
        super().__init__(message)
        self.platform = platform
        self.raw_response = raw_response


class AuthenticationError(SongDiscoveryError):
    """Raised when authentication credentials are missing or invalid."""
    def __init__(self, message: str, platform: str = None):
        super().__init__(message)
        self.platform = platform


class LoginRequiredError(SongDiscoveryError):
    """Raised when NetEase Cloud Music login session is missing or expired."""
    def __init__(self, message: str = "NetEase Cloud Music login is required."):
        super().__init__(message)


class ServiceUnavailableError(SongDiscoveryError):
    """Raised when a local or remote platform service is not reachable."""
    def __init__(self, message: str, platform: str = None, endpoint: str = None):
        super().__init__(message)
        self.platform = platform
        self.endpoint = endpoint


class EmptyReleaseError(SongDiscoveryError):
    """Raised when a release contains no tracks for selection."""
    def __init__(self, message: str, release_id: str = None):
        super().__init__(message)
        self.release_id = release_id


class MatchError(SongDiscoveryError):
    """Raised when matching an external track to NetEase fails or is ambiguous."""
    pass


class PublishError(SongDiscoveryError):
    """Raised when publishing candidates to NetEase playlist fails."""
    pass
