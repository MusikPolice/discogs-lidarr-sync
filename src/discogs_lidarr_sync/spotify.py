"""Spotify API client for building a vinyl-collection playlist.

Responsibilities
----------------
- Authenticate via OAuth (spotipy handles browser redirect + token caching)
- Search Spotify for each DiscogsItem using a typed query with a plain fallback
- Enumerate all tracks from matched albums (paginated)
- Persist search results to a local JSON cache to avoid re-querying on every run
- Create or find the target playlist by name
- Incrementally add only tracks not already present (additive-only)
- Optionally wipe and rebuild the playlist from scratch (--rebuild)
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from discogs_lidarr_sync.config import SpotifySettings
from discogs_lidarr_sync.models import DiscogsItem, SpotifyAction, SpotifyMatchResult

_SCOPES = "playlist-read-private playlist-modify-public playlist-modify-private"


# ── Client ─────────────────────────────────────────────────────────────────────


def build_client(settings: SpotifySettings) -> spotipy.Spotify:
    """Build an authenticated Spotify client.

    On the very first call this opens a browser tab for the user to authorise
    the app; the resulting token pair is cached to *settings.spotify_token_cache_path*
    and silently refreshed on every subsequent call.
    """
    Path(settings.spotify_token_cache_path).parent.mkdir(parents=True, exist_ok=True)
    auth = SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=_SCOPES,
        cache_path=settings.spotify_token_cache_path,
    )
    return spotipy.Spotify(auth_manager=auth)


# ── Name normalisation ─────────────────────────────────────────────────────────


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Used to compare Discogs and Spotify metadata that may differ in punctuation
    or minor word-order variations (e.g. "The Beatles" vs "Beatles, The").
    """
    text = text.lower()
    # Strip leading "the " / "a " articles for artist matching
    text = re.sub(r"^(the|a|an)\s+", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _names_match(discogs: str, spotify: str) -> bool:
    """Return True if the normalised Discogs name is contained in the Spotify name."""
    return _normalise(discogs) in _normalise(spotify) or _normalise(spotify) in _normalise(discogs)


# ── Search ─────────────────────────────────────────────────────────────────────


def _pick_best_album(
    results: list[dict[str, Any]], artist_name: str, album_title: str
) -> dict[str, Any] | None:
    """Return the first Spotify album whose artist and title match the Discogs item."""
    for album in results:
        spotify_title = album.get("name", "")
        spotify_artists: list[dict[str, Any]] = album.get("artists", [])
        spotify_artist = spotify_artists[0].get("name", "") if spotify_artists else ""

        title_ok = _names_match(album_title, spotify_title)
        # For Various Artists compilations, skip artist matching
        artist_ok = artist_name.lower() in ("various", "various artists") or _names_match(
            artist_name, spotify_artist
        )

        if title_ok and artist_ok:
            return album
    return None


def search_album(
    sp: spotipy.Spotify, item: DiscogsItem
) -> tuple[str | None, str | None]:
    """Search Spotify for the album described by *item*.

    Tries a typed query first (``album:X artist:Y``); if that returns no
    usable result, falls back to a plain keyword query.

    Returns:
        (spotify_album_id, spotify_album_name) or (None, None) if not found.
    """
    artist = item.artist_name
    title = item.album_title

    # Pass 1: typed field query
    typed_q = f'album:"{title}" artist:"{artist}"'
    try:
        raw = sp.search(q=typed_q, type="album", limit=10)
        albums: list[dict[str, Any]] = (raw or {}).get("albums", {}).get("items", [])
        match = _pick_best_album(albums, artist, title)
        if match:
            return str(match["id"]), str(match["name"])
    except spotipy.SpotifyException:
        pass

    # Pass 2: plain keyword fallback
    plain_q = f"{artist} {title}"
    try:
        raw = sp.search(q=plain_q, type="album", limit=10)
        albums = (raw or {}).get("albums", {}).get("items", [])
        match = _pick_best_album(albums, artist, title)
        if match:
            return str(match["id"]), str(match["name"])
    except spotipy.SpotifyException:
        pass

    return None, None


# ── Track enumeration ──────────────────────────────────────────────────────────


def get_album_track_uris(sp: spotipy.Spotify, album_id: str) -> list[str]:
    """Return all track URIs for *album_id*, handling pagination."""
    uris: list[str] = []
    offset = 0
    while True:
        page: dict[str, Any] = sp.album_tracks(album_id, limit=50, offset=offset) or {}
        items: list[dict[str, Any]] = page.get("items", [])
        uris.extend(t["uri"] for t in items if t.get("uri"))
        if not page.get("next"):
            break
        offset += 50
    return uris


# ── Search cache ───────────────────────────────────────────────────────────────


class SpotifyCache:
    """Disk-backed cache for Spotify album search results.

    Cache key: ``"{normalised_artist}|||{normalised_title}"``
    Cache value: ``{"album_id": str, "track_uris": [str, ...]}``

    A missing key means the album was never searched; a stored ``null`` album_id
    means it was searched and not found (avoids re-querying known misses).
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._data: dict[str, Any] = {}

    def load(self) -> None:
        if self._path.exists():
            with open(self._path, encoding="utf-8") as f:
                self._data = json.load(f)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def _key(self, artist: str, title: str) -> str:
        return f"{_normalise(artist)}|||{_normalise(title)}"

    def get(self, artist: str, title: str) -> dict[str, Any] | None:
        """Return cached entry or None if not yet cached."""
        return self._data.get(self._key(artist, title))

    def put(self, artist: str, title: str, album_id: str | None, track_uris: list[str]) -> None:
        self._data[self._key(artist, title)] = {"album_id": album_id, "track_uris": track_uris}


# ── Playlist management ────────────────────────────────────────────────────────


def get_or_create_playlist(
    sp: spotipy.Spotify, playlist_name: str, description: str = ""
) -> str:
    """Return the Spotify playlist ID for *playlist_name*, creating it if absent.

    Searches the current user's playlists by exact name (case-insensitive).
    Creates a new private playlist if no match is found.
    """
    user_id: str = sp.current_user()["id"]

    # Page through the user's playlists looking for a name match
    offset = 0
    while True:
        page: dict[str, Any] = sp.current_user_playlists(limit=50, offset=offset) or {}
        items: list[dict[str, Any]] = page.get("items", [])
        for pl in items:
            if pl.get("name", "").lower() == playlist_name.lower():
                return str(pl["id"])
        if not page.get("next"):
            break
        offset += 50

    # Not found — create it
    new_pl: dict[str, Any] = sp.user_playlist_create(
        user=user_id,
        name=playlist_name,
        public=False,
        description=description or "Vinyl collection synced from Discogs",
    )
    return str(new_pl["id"])


def get_existing_track_uris(sp: spotipy.Spotify, playlist_id: str) -> set[str]:
    """Return the set of track URIs currently in *playlist_id*."""
    uris: set[str] = set()
    offset = 0
    while True:
        page: dict[str, Any] = (
            sp.playlist_items(
                playlist_id,
                fields="items(track(uri)),next",
                limit=100,
                offset=offset,
            )
            or {}
        )
        for entry in page.get("items", []):
            track = entry.get("track") or {}
            uri = track.get("uri")
            if uri:
                uris.add(uri)
        if not page.get("next"):
            break
        offset += 100
    return uris


def clear_playlist(sp: spotipy.Spotify, playlist_id: str) -> None:
    """Remove all tracks from *playlist_id* in batches of 100."""
    existing = list(get_existing_track_uris(sp, playlist_id))
    for i in range(0, len(existing), 100):
        batch = [{"uri": u} for u in existing[i : i + 100]]
        sp.playlist_remove_all_occurrences_of_items(playlist_id, [b["uri"] for b in batch])


def add_tracks_to_playlist(
    sp: spotipy.Spotify, playlist_id: str, uris: list[str]
) -> None:
    """Add *uris* to *playlist_id* in batches of 100."""
    for i in range(0, len(uris), 100):
        sp.playlist_add_items(playlist_id, uris[i : i + 100])


# ── Main sync logic ────────────────────────────────────────────────────────────


def sync_collection_to_playlist(
    sp: spotipy.Spotify,
    items: list[DiscogsItem],
    playlist_id: str,
    cache: SpotifyCache,
    dry_run: bool = False,
    rebuild: bool = False,
    progress_callback: Any = None,
) -> list[SpotifyMatchResult]:
    """Resolve every DiscogsItem to Spotify track URIs and update the playlist.

    Args:
        sp:                Authenticated Spotify client.
        items:             Vinyl collection from Discogs.
        playlist_id:       Target Spotify playlist ID.
        cache:             Persistent search result cache.
        dry_run:           If True, search Spotify but do not modify the playlist.
        rebuild:           If True, clear the playlist before adding (ignored in dry_run).
        progress_callback: Optional callable(item) invoked after each item is processed.

    Returns:
        List of SpotifyMatchResult, one per DiscogsItem.
    """
    if not dry_run and rebuild:
        clear_playlist(sp, playlist_id)
        existing_uris: set[str] = set()
    elif not dry_run:
        existing_uris = get_existing_track_uris(sp, playlist_id)
    else:
        existing_uris = set()

    results: list[SpotifyMatchResult] = []
    new_uris: list[str] = []

    for item in items:
        cached = cache.get(item.artist_name, item.album_title)

        if cached is not None:
            album_id: str | None = cached.get("album_id")
            track_uris: list[str] = cached.get("track_uris", [])
        else:
            try:
                album_id, _ = search_album(sp, item)
                if album_id:
                    track_uris = get_album_track_uris(sp, album_id)
                else:
                    track_uris = []
                cache.put(item.artist_name, item.album_title, album_id, track_uris)
                # Respect Spotify's rate limit: small delay between search calls
                time.sleep(0.1)
            except Exception as exc:
                results.append(
                    SpotifyMatchResult(
                        item=item,
                        action=SpotifyAction.ERROR,
                        spotify_album_id=None,
                        track_uris=[],
                        tracks_added=0,
                        error=str(exc),
                    )
                )
                if progress_callback:
                    progress_callback(item)
                continue

        if not album_id:
            results.append(
                SpotifyMatchResult(
                    item=item,
                    action=SpotifyAction.NOT_FOUND,
                    spotify_album_id=None,
                    track_uris=[],
                    tracks_added=0,
                )
            )
        else:
            to_add = [u for u in track_uris if u not in existing_uris]
            if not to_add:
                results.append(
                    SpotifyMatchResult(
                        item=item,
                        action=SpotifyAction.ALREADY_IN,
                        spotify_album_id=album_id,
                        track_uris=track_uris,
                        tracks_added=0,
                    )
                )
            else:
                new_uris.extend(to_add)
                existing_uris.update(to_add)
                results.append(
                    SpotifyMatchResult(
                        item=item,
                        action=SpotifyAction.ADDED,
                        spotify_album_id=album_id,
                        track_uris=track_uris,
                        tracks_added=len(to_add),
                    )
                )

        if progress_callback:
            progress_callback(item)

    if not dry_run and new_uris:
        add_tracks_to_playlist(sp, playlist_id, new_uris)

    return results
