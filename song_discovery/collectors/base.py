"""Base collector interface for music platforms."""

from abc import ABC, abstractmethod
from typing import List, Optional
from song_discovery.http_client import HttpClient
from song_discovery.models import Release, Track


class BaseCollector(ABC):
    """Abstract base class for all platform song/album collectors."""

    platform_name: str = "base"

    def __init__(self, http_client: Optional[HttpClient] = None):
        self.http_client = http_client or HttpClient()

    @abstractmethod
    def collect_new_releases(self, limit: int = 10, **kwargs) -> List[Release]:
        """
        Fetch new releases (albums/EPs/singles) from the platform.
        Must populate each Release with its complete Track list so that
        the unified selection rule can pick target tracks.

        Args:
            limit: Maximum number of releases to collect per category/area.
            **kwargs: Platform-specific parameters.

        Returns:
            List of Release objects with populated tracks.
        """
        pass
