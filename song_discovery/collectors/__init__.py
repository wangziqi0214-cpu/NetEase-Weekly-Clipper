"""Collectors subpackage for song discovery."""

from song_discovery.collectors.base import BaseCollector
from song_discovery.collectors.qq import QQMusicCollector
from song_discovery.collectors.netease import NetEaseCollector
from song_discovery.collectors.kkbox import KKBOXCollector

__all__ = [
    "BaseCollector",
    "QQMusicCollector",
    "NetEaseCollector",
    "KKBOXCollector",
]
