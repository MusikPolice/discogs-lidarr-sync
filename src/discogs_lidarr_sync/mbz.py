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

import json
import os
from datetime import UTC, datetime
from typing import Any

import musicbrainzngs

from discogs_lidarr_sync.models import DiscogsItem, MbzIds

musicbrainzngs.set_useragent("discogs-lidarr-sync", "0.1")


class MbzCache:
    """Persistent on-disk cache of MusicBrainz lookup results.

    Keyed by discogs_release_id (int). Loaded from and flushed to a JSON
    file at the start and end of each sync run respectively.
    """

    def __init__(self, cache_path: str) -> None:
        self.cache_path = cache_path
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, discogs_release_id: int) -> MbzIds | None:
        """Return a cached MbzIds if present, else None."""
        raw = self._data.get(str(discogs_release_id))
        if raw is None:
            return None
        return MbzIds(
            discogs_release_id=discogs_release_id,
            artist_mbid=raw.get("artist_mbid"),
            release_group_mbid=raw.get("release_group_mbid"),
            resolved_at=datetime.fromisoformat(raw["resolved_at"]),
            status=raw["status"],
            error=raw.get("error"),
        )

    def set(self, mbz_ids: MbzIds) -> None:
        """Store a MbzIds result in the in-memory cache."""
        self._data[str(mbz_ids.discogs_release_id)] = {
            "artist_mbid": mbz_ids.artist_mbid,
            "release_group_mbid": mbz_ids.release_group_mbid,
            "resolved_at": mbz_ids.resolved_at.isoformat(),
            "status": mbz_ids.status,
            "error": mbz_ids.error,
        }

    def load(self) -> None:
        """Load the cache from disk. Creates an empty cache if the file does not exist."""
        if not os.path.exists(self.cache_path):
            self._data = {}
            return
        with open(self.cache_path, encoding="utf-8") as f:
            self._data = json.load(f)

    def save(self) -> None:
        """Flush the in-memory cache to disk."""
        parent = os.path.dirname(self.cache_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)


def resolve_artist(discogs_artist_id: int, artist_name: str) -> str | None:
    """Resolve a Discogs artist ID to a MusicBrainz Artist UUID.

    Queries MusicBrainz by URL relationship first (exact Discogs ID match),
    then falls back to a name-based search.

    Returns the MBID string, or None if no confident match is found.
    """
    # 1. URL relation lookup — exact Discogs ID match
    try:
        result = musicbrainzngs.browse_urls(
            resource=f"https://www.discogs.com/artist/{discogs_artist_id}",
            includes=["artist-rels"],
        )
        rels = result["url"].get("artist-relation-list", [])
        discogs_rels = [r for r in rels if r.get("type") == "discogs"]
        if discogs_rels:
            return str(discogs_rels[0]["artist"]["id"])
    except musicbrainzngs.ResponseError:
        pass  # 404: URL not registered in MBZ; fall through to name search

    # 2. Name-based fallback
    try:
        result = musicbrainzngs.search_artists(artist=artist_name, limit=5)
        artists = result.get("artist-list", [])
        if artists:
            return str(artists[0]["id"])
    except musicbrainzngs.WebServiceError:
        pass

    return None


def resolve_release_group(
    discogs_release_id: int,
    album_title: str,
    artist_name: str,
) -> str | None:
    """Resolve a Discogs release ID to a MusicBrainz Release Group UUID.

    Queries MusicBrainz by URL relationship to find the specific release,
    then navigates up to its release group (the abstract album entity that
    Lidarr uses as foreignAlbumId).

    Falls back to a name-based release-group search if the URL relation is
    absent from MusicBrainz.

    Returns the Release Group MBID, or None if no match is found.
    """
    # 1. URL relation lookup → MBZ release MBID
    release_mbid: str | None = None
    try:
        result = musicbrainzngs.browse_urls(
            resource=f"https://www.discogs.com/release/{discogs_release_id}",
            includes=["release-rels"],
        )
        rels = result["url"].get("release-relation-list", [])
        discogs_rels = [r for r in rels if r.get("type") == "discogs"]
        if discogs_rels:
            release_mbid = str(discogs_rels[0]["release"]["id"])
    except musicbrainzngs.ResponseError:
        pass  # 404: this pressing not linked in MBZ

    # 2. Release → release group (the UUID Lidarr needs)
    if release_mbid:
        try:
            result = musicbrainzngs.get_release_by_id(release_mbid, includes=["release-groups"])
            rg = result["release"].get("release-group", {})
            if rg_id := rg.get("id"):
                return str(rg_id)
        except musicbrainzngs.WebServiceError:
            pass

    # 3. Name-based release-group search as last resort
    try:
        result = musicbrainzngs.search_release_groups(
            releasegroup=album_title, artist=artist_name, limit=5
        )
        rgs = result.get("release-group-list", [])
        if rgs:
            return str(rgs[0]["id"])
    except musicbrainzngs.WebServiceError:
        pass

    return None


def resolve(item: DiscogsItem, cache: MbzCache) -> MbzIds:
    """Resolve a DiscogsItem to its MusicBrainz IDs.

    Uses the cache for previously-seen items. On a cache miss, calls
    resolve_artist() and resolve_release_group(), writes the result to the
    cache, and returns it. Always returns an MbzIds (status may be
    "resolved", "partial", or "failed").
    """
    cached = cache.get(item.discogs_release_id)
    if cached is not None:
        return cached

    artist_mbid = resolve_artist(item.discogs_artist_id, item.artist_name)
    release_group_mbid = resolve_release_group(
        item.discogs_release_id, item.album_title, item.artist_name
    )

    if artist_mbid and release_group_mbid:
        status = "resolved"
    elif artist_mbid or release_group_mbid:
        status = "partial"
    else:
        status = "failed"

    mbz_ids = MbzIds(
        discogs_release_id=item.discogs_release_id,
        artist_mbid=artist_mbid,
        release_group_mbid=release_group_mbid,
        resolved_at=datetime.now(UTC),
        status=status,
    )
    cache.set(mbz_ids)
    return mbz_ids
