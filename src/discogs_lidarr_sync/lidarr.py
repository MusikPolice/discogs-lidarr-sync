"""Lidarr API client.

Reads the current Lidarr library state and adds new artists / albums.
All public functions take a pyarr LidarrAPI client as their first argument
so they are easy to unit-test with a mock.

Add behaviour (matches decisions in PLAN.md §8):
  - Artists: monitored=True, addOptions.monitor="none"
    (albums are added explicitly; Lidarr won't auto-monitor discography)
  - Albums:  monitored=True, searchForNewAlbum=False
    (added to the wanted list; no immediate search triggered)

When an artist is added to Lidarr with monitor="none", Lidarr's background
RefreshArtistService immediately indexes the artist's entire discography as
*unmonitored* album entries.  This has two knock-on effects for album adds:

  1. lookup() returns empty for albums already in the local library — Lidarr's
     search endpoint only surfaces albums not yet present locally.
  2. add_album() POST returns AlbumExistsValidator — the album was indexed
     between our lookup and our POST (race condition).

In both cases the album is in Lidarr but unmonitored.  The fix is to detect
these situations and call upd_album(monitored=True) instead of erroring.
"""

from __future__ import annotations

import time
from typing import Any

from pyarr import LidarrAPI

from discogs_lidarr_sync.config import Settings

_POLL_TIMEOUT = 120.0  # seconds before giving up
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

    Includes both monitored and unmonitored albums.
    """
    albums: list[dict[str, Any]] = client.get_album()
    return {a["foreignAlbumId"] for a in albums if a.get("foreignAlbumId")}


def get_monitored_album_mbids(client: LidarrAPI) -> set[str]:
    """Return the set of MusicBrainz Release Group UUIDs for monitored albums only.

    Unmonitored albums (e.g. auto-indexed by Lidarr when an artist is added
    with monitor="none") are excluded.  Used by compute_diff so that those
    albums flow through add_album() → upd_album(monitored=True) rather than
    being silently skipped as "already in Lidarr".
    """
    albums: list[dict[str, Any]] = client.get_album()
    return {a["foreignAlbumId"] for a in albums if a.get("foreignAlbumId") and a.get("monitored")}


def get_discogs_album_coverage(
    client: LidarrAPI,
    release_group_mbids: set[str],
) -> tuple[int, int, int]:
    """Return (monitored, on_disk, wanted) counts for a set of Discogs album MBIDs.

    Cross-references the caller's Discogs release-group MBIDs against the
    current Lidarr library to answer three questions:
      - monitored: how many are monitored in Lidarr
      - on_disk:   of those, how many have at least one file downloaded
                   (statistics.trackFileCount > 0)
      - wanted:    of those, how many have no files yet (queued for download)

    Intended to be called once after apply_diff() to produce the final
    coverage snapshot shown in the sync summary.
    """
    albums: list[dict[str, Any]] = client.get_album()
    by_mbid = {a["foreignAlbumId"]: a for a in albums if a.get("foreignAlbumId")}

    monitored = 0
    on_disk = 0
    wanted = 0
    for mbid in release_group_mbids:
        album = by_mbid.get(mbid)
        if album is None or not album.get("monitored"):
            continue
        monitored += 1
        track_file_count = (album.get("statistics") or {}).get("trackFileCount", 0)
        if track_file_count > 0:
            on_disk += 1
        else:
            wanted += 1

    return monitored, on_disk, wanted


# ── Album local-library helpers ───────────────────────────────────────────────


def _find_album_in_library(client: LidarrAPI, mbid: str) -> dict[str, Any] | None:
    """Return the Lidarr album record for *mbid* if it is in the local library.

    Fetches the full album list and filters client-side.  Returns None if the
    album is not present.
    """
    albums: list[dict[str, Any]] = client.get_album()
    return next((a for a in albums if a.get("foreignAlbumId") == mbid), None)


def _set_album_monitored(client: LidarrAPI, album: dict[str, Any]) -> None:
    """Ensure *album* has monitored=True, calling upd_album() if needed.

    Albums auto-indexed by Lidarr when an artist is added are unmonitored by
    default (because the artist was added with monitor="none").  This function
    flips them to monitored so they are queued for download.
    """
    if not album.get("monitored"):
        album["monitored"] = True
        client.upd_album(album)


# ── Album lookup polling ──────────────────────────────────────────────────────


def _poll_album_lookup(
    client: LidarrAPI,
    mbid: str,
    *,
    timeout: float = _POLL_TIMEOUT,
    base_delay: float = _POLL_BASE_DELAY,
) -> list[dict[str, Any]]:
    """Poll Lidarr's search endpoint until the album appears, with backoff.

    Lidarr's search index can lag briefly after a background artist refresh.
    Retries with exponential backoff up to *timeout* seconds.

    On the *first* cache miss the local library is checked immediately: if the
    album is already indexed there (as an unmonitored entry added during artist
    refresh), the search will *never* return it, so there is no point burning
    the full timeout.  Returning early lets add_album() take the upd_album()
    path without delay.

    Returns:
        Non-empty list if the album was found via search.
        Empty list if the album could not be found via search (either it is
        already in the local library or it is genuinely absent after *timeout*).
    """
    deadline = time.monotonic() + timeout
    delay = base_delay
    first_miss = True

    while True:
        try:
            results: list[dict[str, Any]] = client.lookup(term=f"lidarr:{mbid}")
            if results:
                return results
        except Exception:
            pass  # Transient error — keep retrying

        if first_miss:
            first_miss = False
            # If the album is already in the local library the search endpoint
            # will never return it.  Bail out early instead of polling the full
            # timeout period pointlessly.
            if _find_album_in_library(client, mbid) is not None:
                return []

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return []
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, _POLL_MAX_DELAY)


# ── Artist polling ────────────────────────────────────────────────────────────


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


# ── Public add helpers ────────────────────────────────────────────────────────


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
        raise LidarrError(f"No Lidarr lookup result for artist MBID {mbid!r} ({artist_name!r})")

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
        raise LidarrError(f"Failed to add artist MBID {mbid!r} ({artist_name!r}): {exc}") from exc

    _wait_for_artist_in_search(client, mbid, artist_name, timeout=_poll_timeout)


def add_album(
    client: LidarrAPI,
    mbid: str,
    artist_mbid: str,
    settings: Settings,
    *,
    _poll_timeout: float = _POLL_TIMEOUT,
) -> None:
    """Look up the album by MusicBrainz Release Group UUID and add it to Lidarr.

    The parent artist must already exist in Lidarr before calling this.

    Handles albums that are already in Lidarr's local library as *unmonitored*
    entries (auto-indexed by Lidarr when the artist was added with
    monitor="none").  Two cases are detected and handled gracefully:

      1. _poll_album_lookup() returns empty — Lidarr's search omits albums
         already in the local library.  Falls back to get_album() to confirm
         the album is present, then calls upd_album(monitored=True).
      2. add_album() POST returns AlbumExistsValidator — race between our
         lookup and our POST.  Same fallback: find and monitor the entry.

    In both cases the album ends up monitored, which is the desired end state
    (queued for download without triggering the full artist discography).

    Raises:
        LidarrError: if the album cannot be found via search or in the local
            library, or if the POST fails for an unexpected reason.
    """
    results = _poll_album_lookup(client, mbid, timeout=_poll_timeout)

    if not results:
        # Search exhausted — check the local library as a final fallback.
        # _poll_album_lookup already checked once on first miss; we check again
        # here in case Lidarr finished indexing the album during the poll period.
        album = _find_album_in_library(client, mbid)
        if album is None:
            raise LidarrError(f"No Lidarr lookup result for album MBID {mbid!r}")
        _set_album_monitored(client, album)
        return

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
        if "AlbumExistsValidator" in str(exc) or "already been added" in str(exc):
            # Race between lookup and POST: Lidarr indexed the album in its
            # local DB between our search and our add.  Find and monitor it.
            album = _find_album_in_library(client, mbid)
            if album is not None:
                _set_album_monitored(client, album)
                return
        raise LidarrError(f"Failed to add album MBID {mbid!r}: {exc}") from exc
