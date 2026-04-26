"""Tests for spotify.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from discogs_lidarr_sync.models import DiscogsItem, SpotifyAction
from discogs_lidarr_sync.spotify import (
    SpotifyCache,
    _names_match,
    _normalise,
    _pick_best_album,
    add_tracks_to_playlist,
    get_album_track_uris,
    get_existing_track_uris,
    get_or_create_playlist,
    search_album,
    sync_collection_to_playlist,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _item(
    artist: str = "Test Artist",
    title: str = "Test Album",
    release_id: int = 1,
) -> DiscogsItem:
    return DiscogsItem(
        discogs_release_id=release_id,
        discogs_artist_id=10,
        artist_name=artist,
        album_title=title,
        year=2020,
        formats=["Vinyl"],
    )


def _spotify_album(album_id: str, name: str, artist: str) -> dict[str, Any]:
    return {"id": album_id, "name": name, "artists": [{"name": artist}]}


def _sp_mock() -> MagicMock:
    return MagicMock()


# ── Normalisation ──────────────────────────────────────────────────────────────


def test_normalise_strips_punctuation() -> None:
    assert _normalise("Hello, World!") == "hello world"


def test_normalise_strips_leading_the() -> None:
    assert _normalise("The Beatles") == "beatles"


def test_normalise_strips_leading_a() -> None:
    assert _normalise("A Tribe Called Quest") == "tribe called quest"


def test_names_match_exact() -> None:
    assert _names_match("Radiohead", "Radiohead")


def test_names_match_article_stripped() -> None:
    assert _names_match("The Beatles", "Beatles")


def test_names_match_different_names_false() -> None:
    assert not _names_match("Radiohead", "Pink Floyd")


# ── _pick_best_album ───────────────────────────────────────────────────────────


def test_pick_best_album_returns_first_match() -> None:
    albums = [
        _spotify_album("aaa", "Wrong Album", "Wrong Artist"),
        _spotify_album("bbb", "OK Computer", "Radiohead"),
    ]
    result = _pick_best_album(albums, "Radiohead", "OK Computer")
    assert result is not None
    assert result["id"] == "bbb"


def test_pick_best_album_no_match_returns_none() -> None:
    albums = [_spotify_album("aaa", "Definitely Not This Album", "Some Band")]
    assert _pick_best_album(albums, "Radiohead", "OK Computer") is None


def test_pick_best_album_various_artists_skips_artist_check() -> None:
    albums = [_spotify_album("ccc", "Now That's What I Call Music", "Various Artists")]
    result = _pick_best_album(albums, "Various Artists", "Now That's What I Call Music")
    assert result is not None
    assert result["id"] == "ccc"


# ── search_album ───────────────────────────────────────────────────────────────


def test_search_exact_match() -> None:
    sp = _sp_mock()
    sp.search.return_value = {
        "albums": {"items": [_spotify_album("xyz", "OK Computer", "Radiohead")]}
    }
    album_id, _ = search_album(sp, _item("Radiohead", "OK Computer"))
    assert album_id == "xyz"
    assert sp.search.call_count == 1


def test_search_fallback_when_typed_returns_no_match() -> None:
    sp = _sp_mock()
    no_match_result = {"albums": {"items": [_spotify_album("wrong", "Something Else", "Nobody")]}}
    good_result = {"albums": {"items": [_spotify_album("xyz", "OK Computer", "Radiohead")]}}
    sp.search.side_effect = [no_match_result, good_result]

    album_id, _ = search_album(sp, _item("Radiohead", "OK Computer"))
    assert album_id == "xyz"
    assert sp.search.call_count == 2


def test_search_no_match_returns_none() -> None:
    sp = _sp_mock()
    sp.search.return_value = {"albums": {"items": []}}

    album_id, name = search_album(sp, _item("Radiohead", "OK Computer"))
    assert album_id is None
    assert name is None
    assert sp.search.call_count == 2  # typed + fallback both tried


def test_search_empty_items_list() -> None:
    sp = _sp_mock()
    sp.search.return_value = {"albums": {"items": []}}
    album_id, _ = search_album(sp, _item())
    assert album_id is None


# ── get_album_track_uris ───────────────────────────────────────────────────────


def test_get_album_track_uris_single_page() -> None:
    sp = _sp_mock()
    sp.album_tracks.return_value = {
        "items": [{"uri": "spotify:track:aaa"}, {"uri": "spotify:track:bbb"}],
        "next": None,
    }
    uris = get_album_track_uris(sp, "album123")
    assert uris == ["spotify:track:aaa", "spotify:track:bbb"]
    sp.album_tracks.assert_called_once_with("album123", limit=50, offset=0)


def test_get_album_track_uris_paginated() -> None:
    sp = _sp_mock()
    page1 = {
        "items": [{"uri": f"spotify:track:{i}"} for i in range(50)],
        "next": "http://next",
    }
    page2 = {
        "items": [{"uri": f"spotify:track:{i}"} for i in range(50, 60)],
        "next": None,
    }
    sp.album_tracks.side_effect = [page1, page2]
    uris = get_album_track_uris(sp, "bigalbum")
    assert len(uris) == 60
    assert sp.album_tracks.call_count == 2
    sp.album_tracks.assert_any_call("bigalbum", limit=50, offset=50)


# ── add_tracks_to_playlist ─────────────────────────────────────────────────────


def test_add_tracks_splits_into_batches_of_100() -> None:
    sp = _sp_mock()
    uris = [f"spotify:track:{i}" for i in range(250)]
    add_tracks_to_playlist(sp, "pl123", uris)
    assert sp.playlist_add_items.call_count == 3
    calls = sp.playlist_add_items.call_args_list
    assert len(calls[0].args[1]) == 100
    assert len(calls[1].args[1]) == 100
    assert len(calls[2].args[1]) == 50


def test_add_tracks_empty_list_no_api_call() -> None:
    sp = _sp_mock()
    add_tracks_to_playlist(sp, "pl123", [])
    sp.playlist_add_items.assert_not_called()


# ── get_existing_track_uris ────────────────────────────────────────────────────


def test_get_existing_track_uris_deduplicates() -> None:
    sp = _sp_mock()
    sp.playlist_items.return_value = {
        "items": [
            {"track": {"uri": "spotify:track:aaa"}},
            {"track": {"uri": "spotify:track:bbb"}},
            {"track": {"uri": "spotify:track:aaa"}},  # duplicate
        ],
        "next": None,
    }
    uris = get_existing_track_uris(sp, "pl1")
    assert uris == {"spotify:track:aaa", "spotify:track:bbb"}


def test_get_existing_track_uris_paginated() -> None:
    sp = _sp_mock()
    page1 = {
        "items": [{"track": {"uri": f"spotify:track:{i}"}} for i in range(100)],
        "next": "http://next",
    }
    page2 = {
        "items": [{"track": {"uri": f"spotify:track:{i}"}} for i in range(100, 110)],
        "next": None,
    }
    sp.playlist_items.side_effect = [page1, page2]
    uris = get_existing_track_uris(sp, "pl2")
    assert len(uris) == 110


# ── get_or_create_playlist ─────────────────────────────────────────────────────


def test_get_or_create_playlist_finds_existing() -> None:
    sp = _sp_mock()
    sp.current_user.return_value = {"id": "user1"}
    sp.current_user_playlists.return_value = {
        "items": [{"id": "existing123", "name": "Vinyl Collection"}],
        "next": None,
    }
    pl_id = get_or_create_playlist(sp, "Vinyl Collection")
    assert pl_id == "existing123"
    sp.user_playlist_create.assert_not_called()


def test_get_or_create_playlist_creates_when_missing() -> None:
    sp = _sp_mock()
    sp.current_user.return_value = {"id": "user1"}
    sp.current_user_playlists.return_value = {
        "items": [{"id": "other", "name": "Some Other Playlist"}],
        "next": None,
    }
    sp.user_playlist_create.return_value = {"id": "new123"}
    pl_id = get_or_create_playlist(sp, "Vinyl Collection")
    assert pl_id == "new123"
    sp.user_playlist_create.assert_called_once()
    _, kwargs = sp.user_playlist_create.call_args
    assert kwargs["public"] is False


def test_get_or_create_playlist_case_insensitive() -> None:
    sp = _sp_mock()
    sp.current_user.return_value = {"id": "user1"}
    sp.current_user_playlists.return_value = {
        "items": [{"id": "found99", "name": "vinyl collection"}],
        "next": None,
    }
    pl_id = get_or_create_playlist(sp, "Vinyl Collection")
    assert pl_id == "found99"


# ── SpotifyCache ───────────────────────────────────────────────────────────────


def test_cache_miss_returns_none(tmp_path: Any) -> None:
    cache = SpotifyCache(str(tmp_path / "cache.json"))
    cache.load()
    assert cache.get("Radiohead", "OK Computer") is None


def test_cache_roundtrip(tmp_path: Any) -> None:
    path = str(tmp_path / "cache.json")
    cache = SpotifyCache(path)
    cache.load()
    cache.put("Radiohead", "OK Computer", "album123", ["spotify:track:a"])
    cache.save()

    cache2 = SpotifyCache(path)
    cache2.load()
    entry = cache2.get("Radiohead", "OK Computer")
    assert entry is not None
    assert entry["album_id"] == "album123"
    assert entry["track_uris"] == ["spotify:track:a"]


def test_cache_normalises_key(tmp_path: Any) -> None:
    cache = SpotifyCache(str(tmp_path / "cache.json"))
    cache.load()
    cache.put("The Beatles", "Abbey Road", "ab1", ["spotify:track:x"])
    # Should hit even with punctuation differences
    entry = cache.get("Beatles", "Abbey Road")
    assert entry is not None
    assert entry["album_id"] == "ab1"


def test_cache_stores_not_found_as_null(tmp_path: Any) -> None:
    path = str(tmp_path / "cache.json")
    cache = SpotifyCache(path)
    cache.load()
    cache.put("Unknown Band", "Obscure Record", None, [])
    cache.save()

    cache2 = SpotifyCache(path)
    cache2.load()
    entry = cache2.get("Unknown Band", "Obscure Record")
    assert entry is not None
    assert entry["album_id"] is None


# ── sync_collection_to_playlist ────────────────────────────────────────────────


def _make_cache_with_entries(
    tmp_path: Any, entries: list[tuple[str, str, str, list[str]]]
) -> SpotifyCache:
    cache = SpotifyCache(str(tmp_path / "spotify_cache.json"))
    cache.load()
    for artist, title, album_id, uris in entries:
        cache.put(artist, title, album_id, uris)
    return cache


def test_sync_adds_new_tracks(tmp_path: Any) -> None:
    sp = _sp_mock()
    sp.playlist_items.return_value = {"items": [], "next": None}

    items = [_item("Radiohead", "OK Computer", 1)]
    cache = _make_cache_with_entries(
        tmp_path, [("Radiohead", "OK Computer", "alb1", ["spotify:track:a", "spotify:track:b"])]
    )

    results = sync_collection_to_playlist(sp, items, "pl1", cache, dry_run=False)

    assert len(results) == 1
    assert results[0].action == SpotifyAction.ADDED
    assert results[0].tracks_added == 2
    sp.playlist_add_items.assert_called_once_with("pl1", ["spotify:track:a", "spotify:track:b"])


def test_sync_skips_already_present_tracks(tmp_path: Any) -> None:
    sp = _sp_mock()
    sp.playlist_items.return_value = {
        "items": [
            {"track": {"uri": "spotify:track:a"}},
            {"track": {"uri": "spotify:track:b"}},
        ],
        "next": None,
    }

    items = [_item("Radiohead", "OK Computer", 1)]
    cache = _make_cache_with_entries(
        tmp_path, [("Radiohead", "OK Computer", "alb1", ["spotify:track:a", "spotify:track:b"])]
    )

    results = sync_collection_to_playlist(sp, items, "pl1", cache, dry_run=False)

    assert results[0].action == SpotifyAction.ALREADY_IN
    assert results[0].tracks_added == 0
    sp.playlist_add_items.assert_not_called()


def test_sync_not_found_album(tmp_path: Any) -> None:
    sp = _sp_mock()
    sp.playlist_items.return_value = {"items": [], "next": None}

    items = [_item("Obscure Band", "Rare Record", 99)]
    cache = _make_cache_with_entries(tmp_path, [("Obscure Band", "Rare Record", None, [])])

    results = sync_collection_to_playlist(sp, items, "pl1", cache, dry_run=False)

    assert results[0].action == SpotifyAction.NOT_FOUND
    sp.playlist_add_items.assert_not_called()


def test_sync_dry_run_does_not_modify_playlist(tmp_path: Any) -> None:
    sp = _sp_mock()

    items = [_item("Radiohead", "OK Computer", 1)]
    cache = _make_cache_with_entries(
        tmp_path, [("Radiohead", "OK Computer", "alb1", ["spotify:track:a"])]
    )

    results = sync_collection_to_playlist(sp, items, "pl_dry", cache, dry_run=True)

    assert results[0].action == SpotifyAction.ADDED
    sp.playlist_items.assert_not_called()
    sp.playlist_add_items.assert_not_called()


def test_sync_uses_cache_avoids_search_call(tmp_path: Any) -> None:
    sp = _sp_mock()
    sp.playlist_items.return_value = {"items": [], "next": None}

    items = [_item("Radiohead", "OK Computer", 1)]
    cache = _make_cache_with_entries(
        tmp_path, [("Radiohead", "OK Computer", "alb1", ["spotify:track:a"])]
    )

    sync_collection_to_playlist(sp, items, "pl1", cache, dry_run=False)

    sp.search.assert_not_called()
    sp.album_tracks.assert_not_called()


def test_sync_partial_tracks_already_in_adds_remainder(tmp_path: Any) -> None:
    sp = _sp_mock()
    sp.playlist_items.return_value = {
        "items": [{"track": {"uri": "spotify:track:a"}}],
        "next": None,
    }

    items = [_item("Artist", "Album", 1)]
    cache = _make_cache_with_entries(
        tmp_path,
        [("Artist", "Album", "alb1", ["spotify:track:a", "spotify:track:b", "spotify:track:c"])],
    )

    results = sync_collection_to_playlist(sp, items, "pl1", cache, dry_run=False)

    assert results[0].action == SpotifyAction.ADDED
    assert results[0].tracks_added == 2
    sp.playlist_add_items.assert_called_once_with(
        "pl1", ["spotify:track:b", "spotify:track:c"]
    )
