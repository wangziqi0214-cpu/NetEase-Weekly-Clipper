"""Selection rule implementation for song discovery."""

from typing import Optional
from song_discovery.exceptions import EmptyReleaseError
from song_discovery.models import Release, Track


def select_track_from_release(release: Release) -> Track:
    """
    Select target track according to the unified rule:
    - If release has 0 tracks: raise EmptyReleaseError.
    - If release has only 1 track: pick the only track (index 0).
    - If release_type is "single" (even with >= 2 tracks, e.g. main song + accompaniment):
      pick physical track 1 (index 0).
    - If release_type is "album" or "ep" (or any non-single) and track_count >= 2:
      pick physical track 2 (index 1).

    Args:
        release: Release instance with populated tracks.

    Returns:
        Selected Track instance.

    Raises:
        EmptyReleaseError: When release has no tracks.
    """
    tracks = release.tracks
    if not tracks:
        raise EmptyReleaseError(
            f"Release {release.source_id} ('{release.title}') has no tracks available.",
            release_id=release.source_id,
        )

    if len(tracks) == 1:
        return tracks[0]

    # Multiple tracks (len >= 2)
    rel_type = (release.release_type or "").lower().strip()
    if rel_type == "single":
        return tracks[0]

    # Album or EP with >= 2 tracks: select physical track 2 (index 1)
    return tracks[1]


def get_selection_rule_label(release: Release) -> str:
    """
    Generate an accurate human-readable label explaining why a track was chosen:
    - only_track (1/1)
    - single_main_track (1/N)
    - album_or_ep_2nd_track (2/N)
    """
    tracks = release.tracks
    total = len(tracks)
    if total == 0:
        return "empty_release (0/0)"
    if total == 1:
        return "only_track (1/1)"

    rel_type = (release.release_type or "").lower().strip()
    if rel_type == "single":
        return f"single_main_track (1/{total})"
    return f"album_or_ep_2nd_track (2/{total})"


# Backward-compatible alias
select_target_track = select_track_from_release
