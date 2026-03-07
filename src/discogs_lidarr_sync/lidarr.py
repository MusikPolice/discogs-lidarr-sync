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


class LidarrNotFoundError(LidarrError):
    """Raised when a Lidarr resource is not found (HTTP 404)."""


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


def get_monitored_albums_with_stats(client: LidarrAPI) -> list[dict[str, Any]]:
    """Return full album records for every monitored album in Lidarr.

    Unlike get_monitored_album_mbids(), this returns the complete record so
    that callers (e.g. the audit command) can access statistics, internal IDs,
    and the embedded artist sub-object without a second API call.
    """
    albums: list[dict[str, Any]] = client.get_album()
    return [a for a in albums if a.get("monitored")]


def get_albums_for_audit(client: LidarrAPI) -> list[dict[str, Any]]:
    """Return all Lidarr album records that should appear in an audit.

    Includes:
    - All monitored albums.
    - Unmonitored albums that have at least one file on disk
      (statistics.trackFileCount > 0).

    Excludes unmonitored albums with no files — these are ghost catalog entries
    that Lidarr auto-indexes when an artist is added with monitor="none".
    Including them would flood the audit with hundreds of irrelevant rows.
    """
    albums: list[dict[str, Any]] = client.get_album()
    result = []
    for a in albums:
        if a.get("monitored"):
            result.append(a)
        elif (a.get("statistics") or {}).get("trackFileCount", 0) > 0:
            result.append(a)
    return result


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


# ── Ghost-purge helpers ───────────────────────────────────────────────────────


def get_ghost_albums(client: LidarrAPI) -> list[dict[str, Any]]:
    """Return all unmonitored albums that have no files on disk.

    These are catalog entries auto-indexed by Lidarr when an artist is added
    with monitor="none".  They are never monitored and have no files, so they
    are safe to delete without user review.
    """
    albums: list[dict[str, Any]] = client.get_album()
    return [
        a
        for a in albums
        if not a.get("monitored") and (a.get("statistics") or {}).get("trackFileCount", 0) == 0
    ]


def get_auditable_album_count_for_artist(
    client: LidarrAPI,
    lidarr_artist_id: int,
    *,
    _max_retries: int = 5,
    _base_delay: float = _POLL_BASE_DELAY,
) -> int:
    """Return the number of auditable albums for a given artist internal ID.

    Auditable = monitored OR unmonitored with at least one file on disk.
    Used after ghost album deletion to determine whether an artist has any
    remaining content and should be kept.

    Retries on transient "database is locked" errors with exponential backoff.
    """
    delay = _base_delay
    last_exc: Exception | None = None
    for _ in range(_max_retries):
        try:
            albums: list[dict[str, Any]] = client.get_album()
            return sum(
                1
                for a in albums
                if a.get("artist", {}).get("id") == lidarr_artist_id
                and (a.get("monitored") or (a.get("statistics") or {}).get("trackFileCount", 0) > 0)
            )
        except Exception as exc:
            last_exc = exc
            if "database is locked" in str(exc).lower():
                time.sleep(delay)
                delay = min(delay * 2, _POLL_MAX_DELAY)
                continue
            raise LidarrError(f"Failed to get albums for artist {lidarr_artist_id}: {exc}") from exc
    raise LidarrError(
        f"Lidarr database still locked after {_max_retries} retries for artist {lidarr_artist_id}"
    ) from last_exc


# ── Purge helpers ─────────────────────────────────────────────────────────────


def _is_not_found(exc: Exception) -> bool:
    """Return True if *exc* looks like an HTTP 404 response."""
    msg = str(exc).lower()
    return "404" in msg or "not found" in msg


def delete_album(
    client: LidarrAPI,
    lidarr_id: int,
    delete_files: bool = False,
    *,
    _max_retries: int = 5,
    _base_delay: float = _POLL_BASE_DELAY,
) -> None:
    """Delete an album from Lidarr by its internal ID.

    delete_files=True also removes associated files from disk.
    Raises LidarrNotFoundError when the album does not exist (HTTP 404) so
    callers can distinguish "already gone" from genuine failures.
    Retries on transient "database is locked" errors (background jobs
    triggered by prior deletions can briefly hold a SQLite write lock).
    Raises LidarrError on other unexpected API failures.
    """
    delay = _base_delay
    last_exc: Exception | None = None
    for _ in range(_max_retries):
        try:
            client._delete(
                f"album/{lidarr_id}", client.ver_uri, params={"deleteFiles": delete_files}
            )
            return
        except Exception as exc:
            last_exc = exc
            if _is_not_found(exc):
                raise LidarrNotFoundError(f"Album {lidarr_id} not found in Lidarr") from exc
            if "database is locked" in str(exc).lower():
                time.sleep(delay)
                delay = min(delay * 2, _POLL_MAX_DELAY)
                continue
            raise LidarrError(f"Failed to delete album {lidarr_id}: {exc}") from exc
    raise LidarrError(
        f"Lidarr database still locked after {_max_retries} retries deleting album {lidarr_id}"
    ) from last_exc


def delete_artist(
    client: LidarrAPI,
    lidarr_id: int,
    delete_files: bool = False,
    *,
    _max_retries: int = 5,
    _base_delay: float = _POLL_BASE_DELAY,
) -> None:
    """Delete an artist from Lidarr by its internal ID.

    delete_files=True also removes all associated files from disk.
    Retries on transient "database is locked" errors.
    Raises LidarrNotFoundError on HTTP 404, LidarrError on other failures.
    """
    delay = _base_delay
    last_exc: Exception | None = None
    for _ in range(_max_retries):
        try:
            client._delete(
                f"artist/{lidarr_id}", client.ver_uri, params={"deleteFiles": delete_files}
            )
            return
        except Exception as exc:
            last_exc = exc
            if _is_not_found(exc):
                raise LidarrNotFoundError(f"Artist {lidarr_id} not found in Lidarr") from exc
            if "database is locked" in str(exc).lower():
                time.sleep(delay)
                delay = min(delay * 2, _POLL_MAX_DELAY)
                continue
            raise LidarrError(f"Failed to delete artist {lidarr_id}: {exc}") from exc
    raise LidarrError(
        f"Lidarr database still locked after {_max_retries} retries deleting artist {lidarr_id}"
    ) from last_exc


def get_monitored_album_count_for_artist(
    client: LidarrAPI,
    lidarr_artist_id: int,
    *,
    _max_retries: int = 5,
    _base_delay: float = _POLL_BASE_DELAY,
) -> int:
    """Return the number of monitored albums for a given artist internal ID.

    Used after album deletion to decide whether to also delete the artist.
    Unmonitored albums (auto-indexed by Lidarr when the artist was added)
    are not counted.

    Retries up to *_max_retries* times when Lidarr's SQLite database is
    temporarily locked (a transient condition that can occur immediately
    after bulk album deletions trigger background jobs in Lidarr).
    All other API failures are wrapped as LidarrError.
    """
    delay = _base_delay
    last_exc: Exception | None = None
    for _ in range(_max_retries):
        try:
            albums: list[dict[str, Any]] = client.get_album()
            return sum(
                1
                for a in albums
                if a.get("artist", {}).get("id") == lidarr_artist_id and a.get("monitored")
            )
        except Exception as exc:
            last_exc = exc
            if "database is locked" in str(exc).lower():
                time.sleep(delay)
                delay = min(delay * 2, _POLL_MAX_DELAY)
                continue
            raise LidarrError(f"Failed to get albums for artist {lidarr_artist_id}: {exc}") from exc
    raise LidarrError(
        f"Lidarr database still locked after {_max_retries} retries for artist {lidarr_artist_id}"
    ) from last_exc
