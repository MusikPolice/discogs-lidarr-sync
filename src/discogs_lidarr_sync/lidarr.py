"""Lidarr API client.

Reads the current Lidarr library state and adds new artists / albums.
All public functions take a pyarr LidarrAPI client as their first argument
so they are easy to unit-test with a mock.

Add behaviour (matches decisions in PLAN.md §8):
  - Artists: monitored=True, addOptions.monitor="none"
    (albums are added explicitly; Lidarr won't auto-monitor discography)
  - Albums:  monitored=True, searchForNewAlbum=False
    (added to the wanted list; no immediate search triggered)
"""

from __future__ import annotations

import time
from typing import Any

from pyarr import LidarrAPI

from discogs_lidarr_sync.config import Settings

_POLL_TIMEOUT = 120.0   # seconds before giving up
_POLL_BASE_DELAY = 1.0  # initial retry interval
_POLL_MAX_DELAY = 16.0  # cap for exponential backoff


class LidarrError(Exception):
    """Raised when a Lidarr API call fails in an unexpected or unrecoverable way."""


def get_all_artist_mbids(client: LidarrAPI) -> set[str]:
    """Return the set of MusicBrainz Artist UUIDs for all artists in Lidarr."""
    artists: list[dict[str, Any]] = client.get_artist()
    return {a["foreignArtistId"] for a in artists if a.get("foreignArtistId")}


def get_all_album_mbids(client: LidarrAPI) -> set[str]:
    """Return the set of MusicBrainz Release Group UUIDs for all albums in Lidarr.

    Includes both monitored and unmonitored albums — the sync engine never
    modifies the monitoring state of albums that already exist.
    """
    albums: list[dict[str, Any]] = client.get_album()
    return {a["foreignAlbumId"] for a in albums if a.get("foreignAlbumId")}


def _wait_for_artist_in_search(
    client: LidarrAPI,
    mbid: str,
    artist_name: str,
    *,
    timeout: float = _POLL_TIMEOUT,
    base_delay: float = _POLL_BASE_DELAY,
) -> None:
    """Poll lookup_artist until the newly added artist appears with id > 0.

    Lidarr stores the artist synchronously on POST but may need a moment
    before it appears in search results with a valid internal id.  Album
    search responses embed the artist object; if the artist is not yet
    visible the 'artist' key is absent and pyarr raises KeyError.

    Retries with exponential backoff up to *timeout* seconds.

    Raises:
        LidarrError: if the artist does not become available within *timeout*.
    """
    deadline = time.monotonic() + timeout
    delay = base_delay
    while True:
        try:
            results: list[dict[str, Any]] = client.lookup_artist(term=f"lidarr:{mbid}")
            if results and results[0].get("id", 0) > 0:
                return
        except Exception:
            pass  # Transient error — keep trying

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, _POLL_MAX_DELAY)

    raise LidarrError(
        f"Timed out waiting for artist {artist_name!r} (MBID {mbid!r}) "
        f"to become available in Lidarr after {timeout:.0f}s"
    )


def add_artist(
    client: LidarrAPI,
    mbid: str,
    artist_name: str,
    settings: Settings,
    *,
    _poll_timeout: float = _POLL_TIMEOUT,
) -> None:
    """Look up the artist by MusicBrainz UUID and add them to Lidarr.

    After a successful POST, polls until the artist is visible in Lidarr's
    search results (id > 0), ensuring that subsequent album lookups can
    embed the artist object correctly.

    Raises:
        LidarrError: if the lookup returns no results, the POST fails, or
            the artist does not become available within the poll timeout.
    """
    try:
        results: list[dict[str, Any]] = client.lookup_artist(term=f"lidarr:{mbid}")
    except Exception as exc:
        raise LidarrError(
            f"Lookup failed for artist MBID {mbid!r} ({artist_name!r}): {exc}"
        ) from exc

    if not results:
        raise LidarrError(
            f"No Lidarr lookup result for artist MBID {mbid!r} ({artist_name!r})"
        )

    try:
        client.add_artist(
            results[0],
            root_dir=settings.lidarr_root_folder,
            quality_profile_id=settings.lidarr_quality_profile_id,
            metadata_profile_id=settings.lidarr_metadata_profile_id,
            monitored=True,
            artist_monitor="none",  # type: ignore[arg-type]  # valid in Lidarr API, missing from pyarr stubs
            artist_search_for_missing_albums=False,
        )
    except Exception as exc:
        raise LidarrError(
            f"Failed to add artist MBID {mbid!r} ({artist_name!r}): {exc}"
        ) from exc

    _wait_for_artist_in_search(client, mbid, artist_name, timeout=_poll_timeout)


def add_album(
    client: LidarrAPI,
    mbid: str,
    artist_mbid: str,
    settings: Settings,
) -> None:
    """Look up the album by MusicBrainz Release Group UUID and add it to Lidarr.

    The parent artist must already exist in Lidarr before calling this.

    Raises:
        LidarrError: if the lookup returns no results or the POST fails.
    """
    try:
        results: list[dict[str, Any]] = client.lookup(term=f"lidarr:{mbid}")
    except Exception as exc:
        raise LidarrError(
            f"Lookup failed for album MBID {mbid!r}: {exc}"
        ) from exc

    if not results:
        raise LidarrError(f"No Lidarr lookup result for album MBID {mbid!r}")

    # Lidarr's search endpoint wraps results: {"foreignId": ..., "album": {...}, "id": ...}
    # pyarr's add_album expects the album object directly (with "artist" at the top level).
    result = results[0]
    album_data: dict[str, Any] = result.get("album", result)

    try:
        client.add_album(
            album_data,
            root_dir=settings.lidarr_root_folder,
            quality_profile_id=settings.lidarr_quality_profile_id,
            metadata_profile_id=settings.lidarr_metadata_profile_id,
            monitored=True,
            artist_monitored=True,
            artist_monitor="none",  # type: ignore[arg-type]  # valid in Lidarr API, missing from pyarr stubs
            artist_search_for_missing_albums=False,
            search_for_new_album=False,
        )
    except Exception as exc:
        raise LidarrError(f"Failed to add album MBID {mbid!r}: {exc}") from exc
