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

from pyarr import LidarrAPI

from discogs_lidarr_sync.config import Settings


class LidarrError(Exception):
    """Raised when a Lidarr API call fails in an unexpected or unrecoverable way."""


def get_all_artist_mbids(client: LidarrAPI) -> set[str]:
    """Return the set of MusicBrainz Artist UUIDs for all artists in Lidarr."""
    raise NotImplementedError


def get_all_album_mbids(client: LidarrAPI) -> set[str]:
    """Return the set of MusicBrainz Release Group UUIDs for all albums in Lidarr.

    Includes both monitored and unmonitored albums — the sync engine never
    modifies the monitoring state of albums that already exist.
    """
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError
