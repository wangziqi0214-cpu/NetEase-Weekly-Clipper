"""Song Discovery Package for multi-platform music release aggregation and NetEase publishing."""

from song_discovery.models import Artist, Track, Release, Platform, ReleaseType
from song_discovery.selection import select_track_from_release, select_target_track, get_selection_rule_label
from song_discovery.collectors.base import BaseCollector
from song_discovery.collectors.qq import QQMusicCollector
from song_discovery.collectors.netease import NetEaseCollector
from song_discovery.collectors.kkbox import KKBOXCollector
from song_discovery.db import DiscoveryDB
from song_discovery.scorer import RelevanceScorer, ScoringResult
from song_discovery.orchestrator import DiscoveryOrchestrator
from song_discovery.netease_service import NetEaseServiceManager
from song_discovery.cookie_loader import NetEaseAuth, load_netscape_cookie_header
from song_discovery.matcher import TrackMatcher, MatchResult
from song_discovery.publisher import NetEasePublisher
from song_discovery.bridge import KKBOXBridgeServer

__version__ = "0.4.0"
__all__ = [
    "Artist",
    "Track",
    "Release",
    "Platform",
    "ReleaseType",
    "select_track_from_release",
    "select_target_track",
    "get_selection_rule_label",
    "BaseCollector",
    "QQMusicCollector",
    "NetEaseCollector",
    "KKBOXCollector",
    "DiscoveryDB",
    "RelevanceScorer",
    "ScoringResult",
    "DiscoveryOrchestrator",
    "NetEaseServiceManager",
    "NetEaseAuth",
    "load_netscape_cookie_header",
    "TrackMatcher",
    "MatchResult",
    "NetEasePublisher",
    "KKBOXBridgeServer",
]
