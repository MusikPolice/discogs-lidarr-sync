"""Unit tests for lidarr.py — Phase 5."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from discogs_lidarr_sync.config import Settings
from discogs_lidarr_sync.lidarr import (
    LidarrError,
    add_album,
    add_artist,
    get_all_album_mbids,
    get_all_artist_mbids,
    get_discogs_album_coverage,
)

# ── Test data helpers ──────────────────────────────────────────────────────────

_ARTIST_MBID = "9e0e2b01-41db-4008-bd8b-988977d6019a"  # The Police
_RG_MBID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"  # Dark Side of the Moon


def _settings() -> Settings:
    return Settings(
        discogs_token="x",
        discogs_username="u",
        lidarr_url="http://localhost:8686",
        lidarr_api_key="test-key",
        lidarr_root_folder="/music",
        lidarr_quality_profile_id=1,
        lidarr_metadata_profile_id=1,
    )


def _mock_client() -> MagicMock:
    return MagicMock()


def _artist_entry(mbid: str = _ARTIST_MBID) -> dict[str, object]:
    return {"id": 1, "artistName": "The Police", "foreignArtistId": mbid}


def _album_entry(mbid: str = _RG_MBID) -> dict[str, object]:
    """Unmonitored album — mirrors what Lidarr auto-indexes on artist add."""
    return {"id": 1, "title": "Greatest Hits", "foreignAlbumId": mbid, "artist": {}, "monitored": False}  # noqa: E501


def _monitored_album_entry(mbid: str = _RG_MBID) -> dict[str, object]:
    """Album already monitored in Lidarr."""
    return {"id": 1, "title": "Greatest Hits", "foreignAlbumId": mbid, "artist": {}, "monitored": True}  # noqa: E501


def _album_search_result(mbid: str = _RG_MBID) -> dict[str, object]:
    """Mirrors the real Lidarr /api/v1/search response shape for an album lookup."""
    return {"foreignId": mbid, "album": _album_entry(mbid), "id": 1}


# ── get_discogs_album_coverage ────────────────────────────────────────────────

class TestGetDiscogsAlbumCoverage:
    def _lib(self, monitored: bool, track_file_count: int, mbid: str = _RG_MBID) -> dict:
        return {
            "foreignAlbumId": mbid,
            "monitored": monitored,
            "statistics": {"trackFileCount": track_file_count},
        }

    def test_counts_monitored_on_disk_and_wanted(self) -> None:
        client = _mock_client()
        client.get_album.return_value = [
            self._lib(True, 10, "aaa"),   # monitored, on disk
            self._lib(True, 0, "bbb"),    # monitored, wanted
            self._lib(False, 5, "ccc"),   # unmonitored — excluded
        ]
        monitored, on_disk, wanted = get_discogs_album_coverage(
            client, {"aaa", "bbb", "ccc"}
        )
        assert monitored == 2
        assert on_disk == 1
        assert wanted == 1

    def test_mbid_not_in_library_excluded(self) -> None:
        client = _mock_client()
        client.get_album.return_value = []
        monitored, on_disk, wanted = get_discogs_album_coverage(client, {_RG_MBID})
        assert monitored == 0
        assert on_disk == 0
        assert wanted == 0

    def test_missing_statistics_treated_as_wanted(self) -> None:
        """Albums with no statistics field count as wanted (no files yet)."""
        client = _mock_client()
        client.get_album.return_value = [
            {"foreignAlbumId": _RG_MBID, "monitored": True},  # no statistics key
        ]
        monitored, on_disk, wanted = get_discogs_album_coverage(client, {_RG_MBID})
        assert monitored == 1
        assert on_disk == 0
        assert wanted == 1

    def test_ignores_mbids_not_in_requested_set(self) -> None:
        """Albums in Lidarr but not in the caller's Discogs set are excluded."""
        client = _mock_client()
        client.get_album.return_value = [
            self._lib(True, 5, "in-discogs"),
            self._lib(True, 5, "not-in-discogs"),
        ]
        monitored, on_disk, wanted = get_discogs_album_coverage(client, {"in-discogs"})
        assert monitored == 1
        assert on_disk == 1


# ── get_all_artist_mbids ───────────────────────────────────────────────────────

class TestGetAllArtistMbids:
    def test_returns_set_of_mbids(self) -> None:
        client = _mock_client()
        client.get_artist.return_value = [
            _artist_entry("aaa"),
            _artist_entry("bbb"),
        ]
        result = get_all_artist_mbids(client)
        assert result == {"aaa", "bbb"}

    def test_empty_library_returns_empty_set(self) -> None:
        client = _mock_client()
        client.get_artist.return_value = []
        assert get_all_artist_mbids(client) == set()

    def test_filters_entries_without_foreign_artist_id(self) -> None:
        client = _mock_client()
        client.get_artist.return_value = [
            {"id": 1, "artistName": "No MBID"},
            _artist_entry(_ARTIST_MBID),
        ]
        result = get_all_artist_mbids(client)
        assert result == {_ARTIST_MBID}

    def test_deduplicates_ids(self) -> None:
        """If two entries share a foreignArtistId, the set collapses them."""
        client = _mock_client()
        client.get_artist.return_value = [
            _artist_entry(_ARTIST_MBID),
            _artist_entry(_ARTIST_MBID),
        ]
        assert get_all_artist_mbids(client) == {_ARTIST_MBID}


# ── get_all_album_mbids ────────────────────────────────────────────────────────

class TestGetAllAlbumMbids:
    def test_returns_set_of_mbids(self) -> None:
        client = _mock_client()
        client.get_album.return_value = [
            _album_entry("aaa"),
            _album_entry("bbb"),
        ]
        result = get_all_album_mbids(client)
        assert result == {"aaa", "bbb"}

    def test_empty_library_returns_empty_set(self) -> None:
        client = _mock_client()
        client.get_album.return_value = []
        assert get_all_album_mbids(client) == set()

    def test_filters_entries_without_foreign_album_id(self) -> None:
        client = _mock_client()
        client.get_album.return_value = [
            {"id": 1, "title": "No MBID"},
            _album_entry(_RG_MBID),
        ]
        result = get_all_album_mbids(client)
        assert result == {_RG_MBID}


# ── add_artist ─────────────────────────────────────────────────────────────────

class TestAddArtist:
    def test_calls_lookup_with_lidarr_prefix(self) -> None:
        client = _mock_client()
        client.lookup_artist.return_value = [_artist_entry()]  # id=1 → poll exits immediately
        add_artist(client, _ARTIST_MBID, "The Police", _settings())
        # Called for both the initial lookup and at least one readiness poll.
        client.lookup_artist.assert_any_call(term=f"lidarr:{_ARTIST_MBID}")

    def test_calls_add_artist_with_correct_params(self) -> None:
        client = _mock_client()
        entry = _artist_entry()
        client.lookup_artist.return_value = [entry]
        settings = _settings()
        add_artist(client, _ARTIST_MBID, "The Police", settings)
        client.add_artist.assert_called_once_with(
            entry,
            root_dir=settings.lidarr_root_folder,
            quality_profile_id=settings.lidarr_quality_profile_id,
            metadata_profile_id=settings.lidarr_metadata_profile_id,
            monitored=True,
            artist_monitor="none",
            artist_search_for_missing_albums=False,
        )

    def test_empty_lookup_raises_lidarr_error(self) -> None:
        client = _mock_client()
        client.lookup_artist.return_value = []
        with pytest.raises(LidarrError, match="No Lidarr lookup result"):
            add_artist(client, _ARTIST_MBID, "The Police", _settings())

    def test_lookup_exception_raises_lidarr_error(self) -> None:
        client = _mock_client()
        client.lookup_artist.side_effect = RuntimeError("connection refused")
        with pytest.raises(LidarrError, match="Lookup failed"):
            add_artist(client, _ARTIST_MBID, "The Police", _settings())

    def test_add_artist_exception_raises_lidarr_error(self) -> None:
        client = _mock_client()
        client.lookup_artist.return_value = [_artist_entry()]
        client.add_artist.side_effect = RuntimeError("bad request")
        with pytest.raises(LidarrError, match="Failed to add artist"):
            add_artist(client, _ARTIST_MBID, "The Police", _settings())

    def test_uses_first_lookup_result(self) -> None:
        """Only the first lookup hit is submitted to add_artist."""
        client = _mock_client()
        first = _artist_entry("first-mbid")
        second = _artist_entry("second-mbid")
        client.lookup_artist.return_value = [first, second]
        add_artist(client, _ARTIST_MBID, "The Police", _settings())
        call_args = client.add_artist.call_args
        assert call_args[0][0] is first


class TestAddArtistPolling:
    """Tests for the readiness-poll that runs after a successful add_artist POST."""

    def test_polls_lookup_artist_after_add(self) -> None:
        """lookup_artist is called at least twice: once for lookup, once for poll."""
        client = _mock_client()
        client.lookup_artist.return_value = [_artist_entry()]  # id=1 → ready immediately
        add_artist(client, _ARTIST_MBID, "The Police", _settings())
        assert client.lookup_artist.call_count >= 2

    def test_retries_poll_until_artist_ready(self) -> None:
        """Polling retries when the artist has id=0, then succeeds when id>0."""
        client = _mock_client()
        not_ready = {"artistName": "The Police", "foreignArtistId": _ARTIST_MBID, "id": 0}
        ready = _artist_entry()  # id=1
        # lookup: initial (ready), poll attempt 1 (not ready), poll attempt 2 (ready)
        client.lookup_artist.side_effect = [[ready], [not_ready], [ready]]
        with patch("time.sleep"):
            add_artist(client, _ARTIST_MBID, "The Police", _settings())
        assert client.lookup_artist.call_count == 3

    def test_poll_timeout_raises_lidarr_error(self) -> None:
        """Raises LidarrError when artist never becomes available within timeout."""
        client = _mock_client()
        not_ready = {"artistName": "The Police", "foreignArtistId": _ARTIST_MBID, "id": 0}
        client.lookup_artist.return_value = [not_ready]
        with pytest.raises(LidarrError, match="Timed out"):
            # _poll_timeout=0.0: after the first failed attempt remaining≤0 → immediate raise
            add_artist(client, _ARTIST_MBID, "The Police", _settings(), _poll_timeout=0.0)

    def test_poll_handles_transient_lookup_exception(self) -> None:
        """A transient exception during polling is swallowed and retried."""
        client = _mock_client()
        ready = _artist_entry()
        # initial lookup succeeds; poll raises once, then succeeds
        client.lookup_artist.side_effect = [[ready], RuntimeError("transient"), [ready]]
        with patch("time.sleep"):
            add_artist(client, _ARTIST_MBID, "The Police", _settings())
        assert client.lookup_artist.call_count == 3


# ── add_album ──────────────────────────────────────────────────────────────────

class TestAddAlbum:
    def test_calls_lookup_with_lidarr_prefix(self) -> None:
        client = _mock_client()
        client.lookup.return_value = [_album_entry()]
        add_album(client, _RG_MBID, _ARTIST_MBID, _settings())
        client.lookup.assert_called_once_with(term=f"lidarr:{_RG_MBID}")

    def test_calls_add_album_with_correct_params(self) -> None:
        client = _mock_client()
        entry = _album_entry()
        client.lookup.return_value = [entry]
        settings = _settings()
        add_album(client, _RG_MBID, _ARTIST_MBID, settings)
        client.add_album.assert_called_once_with(
            entry,
            root_dir=settings.lidarr_root_folder,
            quality_profile_id=settings.lidarr_quality_profile_id,
            metadata_profile_id=settings.lidarr_metadata_profile_id,
            monitored=True,
            artist_monitored=True,
            artist_monitor="none",
            artist_search_for_missing_albums=False,
            search_for_new_album=False,
        )

    def test_empty_lookup_raises_lidarr_error(self) -> None:
        client = _mock_client()
        client.lookup.return_value = []
        client.get_album.return_value = []  # not in local library either
        with pytest.raises(LidarrError, match="No Lidarr lookup result"):
            add_album(client, _RG_MBID, _ARTIST_MBID, _settings(), _poll_timeout=0.0)

    def test_lookup_exception_raises_lidarr_error(self) -> None:
        """Persistent lookup exceptions are treated the same as empty results."""
        client = _mock_client()
        client.lookup.side_effect = RuntimeError("connection refused")
        client.get_album.return_value = []  # not in local library either
        with pytest.raises(LidarrError, match="No Lidarr lookup result"):
            add_album(client, _RG_MBID, _ARTIST_MBID, _settings(), _poll_timeout=0.0)

    def test_add_album_exception_raises_lidarr_error(self) -> None:
        client = _mock_client()
        client.lookup.return_value = [_album_entry()]
        client.add_album.side_effect = RuntimeError("bad request")
        with pytest.raises(LidarrError, match="Failed to add album"):
            add_album(client, _RG_MBID, _ARTIST_MBID, _settings())

    def test_uses_first_lookup_result(self) -> None:
        """Only the first lookup hit is submitted to add_album."""
        client = _mock_client()
        first = _album_entry("first-mbid")
        second = _album_entry("second-mbid")
        client.lookup.return_value = [first, second]
        add_album(client, _RG_MBID, _ARTIST_MBID, _settings())
        call_args = client.add_album.call_args
        assert call_args[0][0] is first

    def test_unwraps_album_from_search_result_wrapper(self) -> None:
        """When lookup() returns the real Lidarr wrapper shape, the inner album is extracted."""
        client = _mock_client()
        wrapper = _album_search_result()
        client.lookup.return_value = [wrapper]
        add_album(client, _RG_MBID, _ARTIST_MBID, _settings())
        call_args = client.add_album.call_args
        assert call_args[0][0] is wrapper["album"]


class TestAddAlbumFallback:
    """Tests for the local-library fallback paths in add_album().

    When Lidarr auto-indexes an artist's discography as unmonitored entries
    (triggered by AddArtistService), the search endpoint returns empty for
    those albums.  add_album() must detect them via get_album() and flip
    their monitored flag instead of treating the situation as an error.
    """

    def test_monitors_unmonitored_album_when_lookup_empty(self) -> None:
        """Empty lookup + album in local DB → upd_album(monitored=True) called."""
        client = _mock_client()
        client.lookup.return_value = []
        album = _album_entry()  # monitored=False
        client.get_album.return_value = [album]
        add_album(client, _RG_MBID, _ARTIST_MBID, _settings(), _poll_timeout=0.0)
        client.upd_album.assert_called_once()
        assert client.upd_album.call_args[0][0]["monitored"] is True

    def test_skips_upd_album_if_already_monitored(self) -> None:
        """Empty lookup + album already monitored → upd_album NOT called."""
        client = _mock_client()
        client.lookup.return_value = []
        client.get_album.return_value = [_monitored_album_entry()]
        add_album(client, _RG_MBID, _ARTIST_MBID, _settings(), _poll_timeout=0.0)
        client.upd_album.assert_not_called()

    def test_raises_when_lookup_empty_and_not_in_library(self) -> None:
        """Empty lookup + album absent from local DB → LidarrError raised."""
        client = _mock_client()
        client.lookup.return_value = []
        client.get_album.return_value = []
        with pytest.raises(LidarrError, match="No Lidarr lookup result"):
            add_album(client, _RG_MBID, _ARTIST_MBID, _settings(), _poll_timeout=0.0)

    def test_monitors_album_on_album_exists_validator(self) -> None:
        """AlbumExistsValidator on POST → find in local DB and set monitored=True."""
        client = _mock_client()
        client.lookup.return_value = [_album_search_result()]
        client.add_album.side_effect = RuntimeError(
            "Bad Request: AlbumExistsValidator: This album has already been added."
        )
        album = _album_entry()  # monitored=False
        client.get_album.return_value = [album]
        add_album(client, _RG_MBID, _ARTIST_MBID, _settings())
        client.upd_album.assert_called_once()
        assert client.upd_album.call_args[0][0]["monitored"] is True

    def test_album_exists_validator_raises_if_not_in_library(self) -> None:
        """AlbumExistsValidator but album not in local DB → LidarrError re-raised."""
        client = _mock_client()
        client.lookup.return_value = [_album_search_result()]
        client.add_album.side_effect = RuntimeError(
            "Bad Request: AlbumExistsValidator: This album has already been added."
        )
        client.get_album.return_value = []
        with pytest.raises(LidarrError, match="Failed to add album"):
            add_album(client, _RG_MBID, _ARTIST_MBID, _settings())

    def test_lookup_retries_until_results_appear(self) -> None:
        """Lookup returns empty twice then results — retries correctly."""
        client = _mock_client()
        client.lookup.side_effect = [[], [], [_album_search_result()]]
        client.get_album.return_value = []  # not in local DB on first miss
        with patch("time.sleep"):
            add_album(client, _RG_MBID, _ARTIST_MBID, _settings())
        assert client.lookup.call_count == 3
        client.add_album.assert_called_once()

    def test_poll_exits_early_when_album_found_in_library(self) -> None:
        """First lookup miss + album in local DB → exits without sleeping."""
        client = _mock_client()
        client.lookup.return_value = []
        album = _album_entry()  # monitored=False
        client.get_album.return_value = [album]
        with patch("time.sleep") as mock_sleep:
            add_album(client, _RG_MBID, _ARTIST_MBID, _settings(), _poll_timeout=120.0)
        # Should have returned as soon as the local-DB check confirmed the album,
        # without sleeping at all.
        mock_sleep.assert_not_called()
        client.upd_album.assert_called_once()

    def test_poll_timeout_then_library_found(self) -> None:
        """Lookup times out but album appears in local DB by then → monitored."""
        client = _mock_client()
        # Lookup always empty; album not in local DB on first miss (still indexing)
        # but present on the final check in add_album().
        client.lookup.return_value = []
        not_yet = []
        present = [_album_entry()]
        client.get_album.side_effect = [not_yet, present]
        add_album(client, _RG_MBID, _ARTIST_MBID, _settings(), _poll_timeout=0.0)
        client.upd_album.assert_called_once()
