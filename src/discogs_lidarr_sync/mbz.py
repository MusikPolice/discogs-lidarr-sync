"""MusicBrainz ID resolver and on-disk lookup cache.

Bridges Discogs integer IDs to the MusicBrainz UUIDs that Lidarr requires:
  - foreignArtistId  = MusicBrainz Artist UUID
  - foreignAlbumId   = MusicBrainz Release Group UUID

Resolution strategy:
  1. Check the on-disk cache (keyed by discogs_release_id).
  2. Query MusicBrainz by URL relationship (exact Discogs ID match).
  3. Fall back to a name-based search if step 2 finds nothing.
  4. On failure, record status="failed" in the cache so the item is not
     re-queried on subsequent runs.

MusicBrainz enforces a 1 req/sec rate limit; musicbrainzngs handles this
automatically. The cache eliminates repeat calls for items seen in prior runs.
"""

from __future__ import annotations

from discogs_lidarr_sync.models import DiscogsItem, MbzIds


class MbzCache:
    """Persistent on-disk cache of MusicBrainz lookup results.

    Keyed by discogs_release_id (int). Loaded from and flushed to a JSON
    file at the start and end of each sync run respectively.
    """

    def __init__(self, cache_path: str) -> None:
        self.cache_path = cache_path

    def get(self, discogs_release_id: int) -> MbzIds | None:
        """Return a cached MbzIds if present, else None."""
        raise NotImplementedError

    def set(self, mbz_ids: MbzIds) -> None:
        """Store a MbzIds result in the in-memory cache."""
        raise NotImplementedError

    def load(self) -> None:
        """Load the cache from disk. Creates an empty cache if the file does not exist."""
        raise NotImplementedError

    def save(self) -> None:
        """Flush the in-memory cache to disk."""
        raise NotImplementedError


def resolve_artist(discogs_artist_id: int, artist_name: str) -> str | None:
    """Resolve a Discogs artist ID to a MusicBrainz Artist UUID.

    Queries MusicBrainz by URL relationship first (exact Discogs ID match),
    then falls back to a name-based search.

    Returns the MBID string, or None if no confident match is found.
    """
    raise NotImplementedError


def resolve_release_group(
    discogs_release_id: int,
    album_title: str,
    artist_name: str,
) -> str | None:
    """Resolve a Discogs release ID to a MusicBrainz Release Group UUID.

    Queries MusicBrainz by URL relationship to find the individual release,
    then navigates to its release group.

    Returns the Release Group MBID, or None if no match is found.
    Note: always resolves to the Release Group (not the individual Release).
    """
    raise NotImplementedError


def resolve(item: DiscogsItem, cache: MbzCache) -> MbzIds:
    """Resolve a DiscogsItem to its MusicBrainz IDs.

    Uses the cache for previously-seen items. On a cache miss, calls
    resolve_artist() and resolve_release_group(), writes the result to the
    cache, and returns it. Always returns an MbzIds (status may be
    "resolved", "partial", or "failed").
    """
    raise NotImplementedError
