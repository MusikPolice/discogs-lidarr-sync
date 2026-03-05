"""Unit tests for mbz.py — Phase 4."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import musicbrainzngs

from discogs_lidarr_sync.mbz import MbzCache, resolve, resolve_artist, resolve_release_group
from discogs_lidarr_sync.models import DiscogsItem, MbzIds

# ── Test data helpers ─────────────────────────────────────────────────────────

_ARTIST_MBID = "9e0e2b01-41db-4008-bd8b-988977d6019a"  # The Police
_RG_MBID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"  # Dark Side of the Moon
_RELEASE_MBID = "b84ee12a-09ef-421b-82de-0441a926375b"


def _discogs_item(
    release_id: int = 1873013,
    artist_id: int = 7987,
    artist_name: str = "The Police",
    title: str = "Greatest Hits",
) -> DiscogsItem:
    return DiscogsItem(
        discogs_release_id=release_id,
        discogs_artist_id=artist_id,
        artist_name=artist_name,
        album_title=title,
        year=1992,
        formats=["Vinyl"],
    )


def _artist_url_response(artist_id: str = _ARTIST_MBID) -> dict[str, object]:
    return {
        "url": {
            "id": "some-url-uuid",
            "resource": "https://www.discogs.com/artist/7987",
            "artist-relation-list": [
                {
                    "type": "discogs",
                    "target": artist_id,
                    "direction": "backward",
                    "artist": {"id": artist_id, "name": "The Police"},
                }
            ],
        }
    }


def _release_url_response(release_id: str = _RELEASE_MBID) -> dict[str, object]:
    return {
        "url": {
            "id": "some-url-uuid",
            "resource": "https://www.discogs.com/release/1873013",
            "release-relation-list": [
                {
                    "type": "discogs",
                    "target": release_id,
                    "direction": "backward",
                    "release": {"id": release_id, "title": "The Dark Side of the Moon"},
                }
            ],
        }
    }


def _release_by_id_response(rg_id: str = _RG_MBID) -> dict[str, object]:
    return {
        "release": {
            "id": _RELEASE_MBID,
            "title": "The Dark Side of the Moon",
            "release-group": {"id": rg_id, "title": "The Dark Side of the Moon"},
        }
    }


def _now() -> datetime:
    return datetime.now(UTC)


# ── MbzCache ──────────────────────────────────────────────────────────────────


class TestMbzCache:
    def test_get_miss_returns_none(self) -> None:
        cache = MbzCache("irrelevant.json")
        assert cache.get(12345) is None

    def test_set_and_get_roundtrip(self) -> None:
        cache = MbzCache("irrelevant.json")
        mbz = MbzIds(
            discogs_release_id=42,
            artist_mbid=_ARTIST_MBID,
            release_group_mbid=_RG_MBID,
            resolved_at=_now(),
            status="resolved",
        )
        cache.set(mbz)
        result = cache.get(42)
        assert result is not None
        assert result.artist_mbid == _ARTIST_MBID
        assert result.release_group_mbid == _RG_MBID
        assert result.status == "resolved"

    def test_set_failed_entry(self) -> None:
        cache = MbzCache("irrelevant.json")
        mbz = MbzIds(
            discogs_release_id=99,
            artist_mbid=None,
            release_group_mbid=None,
            resolved_at=_now(),
            status="failed",
            error="not found",
        )
        cache.set(mbz)
        result = cache.get(99)
        assert result is not None
        assert result.status == "failed"
        assert result.error == "not found"
        assert result.artist_mbid is None

    def test_load_missing_file_gives_empty_cache(self, tmp_path: Path) -> None:
        cache = MbzCache(str(tmp_path / "nonexistent.json"))
        cache.load()
        assert cache.get(1) is None

    def test_save_and_reload(self, tmp_path: Path) -> None:
        path = str(tmp_path / "cache.json")
        cache = MbzCache(path)
        cache.set(
            MbzIds(
                discogs_release_id=7,
                artist_mbid=_ARTIST_MBID,
                release_group_mbid=_RG_MBID,
                resolved_at=_now(),
                status="resolved",
            )
        )
        cache.save()

        cache2 = MbzCache(path)
        cache2.load()
        result = cache2.get(7)
        assert result is not None
        assert result.artist_mbid == _ARTIST_MBID

    def test_save_creates_parent_directory(self, tmp_path: Path) -> None:
        path = str(tmp_path / "subdir" / "cache.json")
        cache = MbzCache(path)
        cache.save()  # must not raise
        assert Path(path).exists()

    def test_save_file_is_valid_json(self, tmp_path: Path) -> None:
        path = str(tmp_path / "cache.json")
        cache = MbzCache(path)
        cache.set(
            MbzIds(
                discogs_release_id=1,
                artist_mbid=_ARTIST_MBID,
                release_group_mbid=None,
                resolved_at=_now(),
                status="partial",
            )
        )
        cache.save()
        with open(path) as f:
            data = json.load(f)
        assert "1" in data
        assert data["1"]["status"] == "partial"


# ── resolve_artist ────────────────────────────────────────────────────────────


class TestResolveArtist:
    def test_url_relation_match(self) -> None:
        with patch.object(musicbrainzngs, "browse_urls", return_value=_artist_url_response()):
            result = resolve_artist(7987, "The Police")
        assert result == _ARTIST_MBID

    def test_url_404_falls_back_to_name_search(self) -> None:
        name_result = {"artist-list": [{"id": _ARTIST_MBID, "name": "The Police"}]}
        with (
            patch.object(
                musicbrainzngs,
                "browse_urls",
                side_effect=musicbrainzngs.ResponseError(cause=Exception("404")),
            ),
            patch.object(musicbrainzngs, "search_artists", return_value=name_result),
        ):
            result = resolve_artist(7987, "The Police")
        assert result == _ARTIST_MBID

    def test_empty_relation_list_falls_back_to_name_search(self) -> None:
        empty_url = {"url": {"artist-relation-list": []}}
        name_result = {"artist-list": [{"id": _ARTIST_MBID}]}
        with (
            patch.object(musicbrainzngs, "browse_urls", return_value=empty_url),
            patch.object(musicbrainzngs, "search_artists", return_value=name_result),
        ):
            result = resolve_artist(7987, "The Police")
        assert result == _ARTIST_MBID

    def test_non_discogs_relation_type_ignored(self) -> None:
        """Relations with type != 'discogs' must not be returned."""
        url_resp: dict[str, object] = {
            "url": {"artist-relation-list": [{"type": "other", "artist": {"id": "wrong-id"}}]}
        }
        name_result = {"artist-list": [{"id": _ARTIST_MBID}]}
        with (
            patch.object(musicbrainzngs, "browse_urls", return_value=url_resp),
            patch.object(musicbrainzngs, "search_artists", return_value=name_result),
        ):
            result = resolve_artist(7987, "The Police")
        assert result == _ARTIST_MBID

    def test_both_methods_fail_returns_none(self) -> None:
        with (
            patch.object(
                musicbrainzngs,
                "browse_urls",
                side_effect=musicbrainzngs.ResponseError(cause=Exception("404")),
            ),
            patch.object(musicbrainzngs, "search_artists", return_value={"artist-list": []}),
        ):
            result = resolve_artist(7987, "The Police")
        assert result is None

    def test_name_search_error_returns_none(self) -> None:
        with (
            patch.object(
                musicbrainzngs,
                "browse_urls",
                side_effect=musicbrainzngs.ResponseError(cause=Exception("404")),
            ),
            patch.object(
                musicbrainzngs,
                "search_artists",
                side_effect=musicbrainzngs.WebServiceError("server error"),
            ),
        ):
            result = resolve_artist(7987, "The Police")
        assert result is None


# ── resolve_release_group ─────────────────────────────────────────────────────


class TestResolveReleaseGroup:
    def test_url_match_navigates_to_release_group(self) -> None:
        with (
            patch.object(musicbrainzngs, "browse_urls", return_value=_release_url_response()),
            patch.object(
                musicbrainzngs, "get_release_by_id", return_value=_release_by_id_response()
            ),
        ):
            result = resolve_release_group(1873013, "The Dark Side of the Moon", "Pink Floyd")
        assert result == _RG_MBID

    def test_url_404_falls_back_to_rg_name_search(self) -> None:
        rg_result = {"release-group-list": [{"id": _RG_MBID}]}
        with (
            patch.object(
                musicbrainzngs,
                "browse_urls",
                side_effect=musicbrainzngs.ResponseError(cause=Exception("404")),
            ),
            patch.object(musicbrainzngs, "search_release_groups", return_value=rg_result),
        ):
            result = resolve_release_group(1873013, "The Dark Side of the Moon", "Pink Floyd")
        assert result == _RG_MBID

    def test_release_found_but_no_release_group_falls_back(self) -> None:
        """If get_release_by_id returns no release-group, try name search."""
        release_no_rg: dict[str, object] = {"release": {"id": _RELEASE_MBID, "release-group": {}}}
        rg_result = {"release-group-list": [{"id": _RG_MBID}]}
        with (
            patch.object(musicbrainzngs, "browse_urls", return_value=_release_url_response()),
            patch.object(musicbrainzngs, "get_release_by_id", return_value=release_no_rg),
            patch.object(musicbrainzngs, "search_release_groups", return_value=rg_result),
        ):
            result = resolve_release_group(1873013, "The Dark Side of the Moon", "Pink Floyd")
        assert result == _RG_MBID

    def test_all_methods_fail_returns_none(self) -> None:
        with (
            patch.object(
                musicbrainzngs,
                "browse_urls",
                side_effect=musicbrainzngs.ResponseError(cause=Exception("404")),
            ),
            patch.object(
                musicbrainzngs, "search_release_groups", return_value={"release-group-list": []}
            ),
        ):
            result = resolve_release_group(9999, "Unknown Album", "Unknown Artist")
        assert result is None

    def test_empty_release_relation_list_falls_back_to_name(self) -> None:
        empty_url: dict[str, object] = {"url": {"release-relation-list": []}}
        rg_result = {"release-group-list": [{"id": _RG_MBID}]}
        with (
            patch.object(musicbrainzngs, "browse_urls", return_value=empty_url),
            patch.object(musicbrainzngs, "search_release_groups", return_value=rg_result),
        ):
            result = resolve_release_group(1873013, "The Dark Side of the Moon", "Pink Floyd")
        assert result == _RG_MBID


# ── resolve ───────────────────────────────────────────────────────────────────


class TestResolve:
    def _cache(self) -> MbzCache:
        return MbzCache("irrelevant.json")

    def test_cache_hit_returns_cached_value_without_network_call(self) -> None:
        cache = self._cache()
        cached = MbzIds(
            discogs_release_id=42,
            artist_mbid=_ARTIST_MBID,
            release_group_mbid=_RG_MBID,
            resolved_at=_now(),
            status="resolved",
        )
        cache.set(cached)

        with patch.object(musicbrainzngs, "browse_urls") as mock_browse:
            result = resolve(_discogs_item(release_id=42), cache)

        mock_browse.assert_not_called()
        assert result.artist_mbid == _ARTIST_MBID

    def test_cache_miss_resolved(self) -> None:
        cache = self._cache()
        with (
            patch.object(musicbrainzngs, "browse_urls") as mock_browse,
            patch.object(
                musicbrainzngs, "get_release_by_id", return_value=_release_by_id_response()
            ),
        ):
            mock_browse.side_effect = [
                _artist_url_response(),
                _release_url_response(),
            ]
            result = resolve(_discogs_item(), cache)

        assert result.status == "resolved"
        assert result.artist_mbid == _ARTIST_MBID
        assert result.release_group_mbid == _RG_MBID

    def test_cache_miss_partial_artist_only(self) -> None:
        cache = self._cache()
        with (
            patch.object(musicbrainzngs, "browse_urls") as mock_browse,
            patch.object(
                musicbrainzngs, "search_release_groups", return_value={"release-group-list": []}
            ),
        ):
            mock_browse.side_effect = [
                _artist_url_response(),
                musicbrainzngs.ResponseError(cause=Exception("404")),
            ]
            result = resolve(_discogs_item(), cache)

        assert result.status == "partial"
        assert result.artist_mbid == _ARTIST_MBID
        assert result.release_group_mbid is None

    def test_cache_miss_failed(self) -> None:
        cache = self._cache()
        with (
            patch.object(
                musicbrainzngs,
                "browse_urls",
                side_effect=musicbrainzngs.ResponseError(cause=Exception("404")),
            ),
            patch.object(musicbrainzngs, "search_artists", return_value={"artist-list": []}),
            patch.object(
                musicbrainzngs, "search_release_groups", return_value={"release-group-list": []}
            ),
        ):
            result = resolve(_discogs_item(), cache)

        assert result.status == "failed"
        assert result.artist_mbid is None
        assert result.release_group_mbid is None

    def test_resolve_writes_result_to_cache(self) -> None:
        cache = self._cache()
        item = _discogs_item()
        with (
            patch.object(musicbrainzngs, "browse_urls") as mock_browse,
            patch.object(
                musicbrainzngs, "get_release_by_id", return_value=_release_by_id_response()
            ),
        ):
            mock_browse.side_effect = [
                _artist_url_response(),
                _release_url_response(),
            ]
            resolve(item, cache)

        # Second call must use cache, not make new network calls
        with patch.object(musicbrainzngs, "browse_urls") as mock_browse2:
            resolve(item, cache)
        mock_browse2.assert_not_called()

    def test_resolve_failed_is_also_cached(self) -> None:
        """Failed results are cached so we don't re-query on subsequent runs."""
        cache = self._cache()
        item = _discogs_item()
        with (
            patch.object(
                musicbrainzngs,
                "browse_urls",
                side_effect=musicbrainzngs.ResponseError(cause=Exception("404")),
            ),
            patch.object(musicbrainzngs, "search_artists", return_value={"artist-list": []}),
            patch.object(
                musicbrainzngs, "search_release_groups", return_value={"release-group-list": []}
            ),
        ):
            resolve(item, cache)

        assert cache.get(item.discogs_release_id) is not None
        assert cache.get(item.discogs_release_id).status == "failed"  # type: ignore[union-attr]
