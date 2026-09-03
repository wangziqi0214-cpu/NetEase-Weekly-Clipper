"""Unified data models for song discovery."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Platform(str, Enum):
    QQ = "qq"
    NETEASE = "netease"
    KKBOX = "kkbox"


class ReleaseType(str, Enum):
    SINGLE = "single"
    EP = "ep"
    ALBUM = "album"
    OTHER = "other"


class RejectionReason(str, Enum):
    NON_ROCK_BAND = "non_rock_band"
    MAINSTREAM_POP_IDOL = "mainstream_pop_idol"
    HIPHOP_RAP = "hiphop_rap"
    VIRTUAL_SINGER = "virtual_singer"
    GAME_BGM_OST = "game_bgm_ost"
    CLASSICAL_INSTRUMENTAL = "classical_instrumental"
    COVER_ACCOMPANIMENT_REMIX = "cover_accompaniment_remix"
    ARTIST_NOT_NEEDED = "artist_not_needed"
    DUPLICATE = "duplicate"
    OTHER = "other"


REJECTION_REASON_LABELS: Dict[str, str] = {
    "non_rock_band": "非摇滚乐队",
    "mainstream_pop_idol": "普通流行偶像",
    "hiphop_rap": "说唱",
    "virtual_singer": "虚拟歌手",
    "game_bgm_ost": "游戏BGM原声",
    "classical_instrumental": "古典纯音乐",
    "cover_accompaniment_remix": "翻唱伴奏Remix",
    "artist_not_needed": "歌手不需要",
    "duplicate": "重复",
    "other": "泛化不喜欢 / 不符合选歌偏好",
}

REJECTION_REASONS = REJECTION_REASON_LABELS


class ReasonScope(str, Enum):
    TRACK = "track"
    ARTIST = "artist"
    RELEASE = "release"
    GLOBAL = "global"


REASON_SCOPE_LABELS: Dict[str, str] = {
    "track": "当前单曲 (Track)",
    "artist": "该艺人 (Artist)",
    "release": "整张发行 (Release)",
    "global": "全局通用 (Global)",
}

REASON_SCOPES = REASON_SCOPE_LABELS


@dataclass
class Artist:
    """Artist metadata representation."""
    name: str
    id: Optional[str] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_raw: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "name": self.name,
            "id": self.id,
        }
        if include_raw:
            data["raw_metadata"] = self.raw_metadata
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Artist:
        return cls(
            name=data.get("name", ""),
            id=data.get("id"),
            raw_metadata=data.get("raw_metadata", {}),
        )


@dataclass
class Track:
    """Unified Track metadata model."""
    platform: str
    source_id: str
    title: str
    artists: List[Artist]
    album_title: str
    release_type: str
    release_date: str
    duration_ms: Optional[int] = None
    track_number: Optional[int] = None
    source_url: str = ""
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def artist_names(self) -> List[str]:
        return [a.name for a in self.artists if a.name]

    @property
    def artist_names_str(self) -> str:
        return " / ".join(self.artist_names)

    def to_dict(self, include_raw: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "platform": self.platform,
            "source_id": self.source_id,
            "title": self.title,
            "artists": [a.to_dict(include_raw=include_raw) for a in self.artists],
            "artist_names": self.artist_names,
            "artist_names_str": self.artist_names_str,
            "album_title": self.album_title,
            "release_type": self.release_type,
            "release_date": self.release_date,
            "duration_ms": self.duration_ms,
            "track_number": self.track_number,
            "source_url": self.source_url,
        }
        if include_raw:
            data["raw_metadata"] = self.raw_metadata
        return data


@dataclass
class Release:
    """Unified Release (Album/EP/Single) metadata model."""
    platform: str
    source_id: str
    title: str
    artists: List[Artist]
    album_title: str
    release_type: str
    release_date: str
    track_count: int = 0
    duration_ms: Optional[int] = None
    track_number: Optional[int] = None
    source_url: str = ""
    tracks: List[Track] = field(default_factory=list)
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def artist_names(self) -> List[str]:
        return [a.name for a in self.artists if a.name]

    @property
    def artist_names_str(self) -> str:
        return " / ".join(self.artist_names)

    def to_dict(self, include_raw: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "platform": self.platform,
            "source_id": self.source_id,
            "title": self.title,
            "artists": [a.to_dict(include_raw=include_raw) for a in self.artists],
            "artist_names": self.artist_names,
            "artist_names_str": self.artist_names_str,
            "album_title": self.album_title,
            "release_type": self.release_type,
            "release_date": self.release_date,
            "track_count": self.track_count if self.track_count > 0 else len(self.tracks),
            "duration_ms": self.duration_ms,
            "track_number": self.track_number,
            "source_url": self.source_url,
            "tracks": [t.to_dict(include_raw=include_raw) for t in self.tracks],
        }
        if include_raw:
            data["raw_metadata"] = self.raw_metadata
        return data


@dataclass
class ReviewFeedbackRecord:
    """Normalized feedback record capturing user editorial decision."""
    candidate_id: int
    decision: str  # approved, rejected, deferred, pending
    reason_code: Optional[str] = None
    reason_scope: str = "track"
    note: str = ""
    feature_snapshot: Dict[str, Any] = field(default_factory=dict)
    model_version: str = "v0_cold_start"
    created_at: Optional[str] = None
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason_scope": self.reason_scope,
            "note": self.note,
            "feature_snapshot": self.feature_snapshot,
            "model_version": self.model_version,
            "created_at": self.created_at,
        }


@dataclass
class ModelVersionRecord:
    """Version registry entry for personalized preference learner models."""
    version_id: str
    created_at: str
    status: str = "active"  # active, archived, rolled_back, candidate
    sample_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    metrics: Dict[str, Any] = field(default_factory=dict)
    weights: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version_id": self.version_id,
            "created_at": self.created_at,
            "status": self.status,
            "sample_count": self.sample_count,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "metrics": self.metrics,
            "weights": self.weights,
            "notes": self.notes,
        }
