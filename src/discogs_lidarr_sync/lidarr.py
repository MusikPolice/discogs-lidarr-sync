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

from typing import Any

from pyarr import LidarrAPI

from discogs_lidarr_sync.config import Settings


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


def add_artist(
    client: LidarrAPI,
    mbid: str,
    artist_name: str,
    settings: Settings,
) -> None:
    """Look up the artist by MusicBrainz UUID and add them to Lidarr.

    Raises:
        LidarrError: if the lookup returns no results or the POST fails.
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

    try:
        client.add_album(
            results[0],
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
