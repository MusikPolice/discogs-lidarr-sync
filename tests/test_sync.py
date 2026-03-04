"""Unit tests for sync.py — Phase 6."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from discogs_lidarr_sync.config import Settings
from discogs_lidarr_sync.mbz import MbzCache
from discogs_lidarr_sync.models import DiscogsItem, MbzIds, RunReport, SyncAction, SyncResult
from discogs_lidarr_sync.sync import (
    apply_diff,
    compute_diff,
    write_report,
    write_unresolved,
)

# ── Test data helpers ──────────────────────────────────────────────────────────

_ARTIST_MBID = "9e0e2b01-41db-4008-bd8b-988977d6019a"
_ARTIST_MBID_2 = "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"
_RG_MBID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"
_RG_MBID_2 = "3d37c4e7-aaed-4d32-8d8c-9b8b3a7c68e4"


def _discogs_item(
    release_id: int = 1,
    artist_id: int = 100,
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


def _mbz_ids(
    release_id: int = 1,
    artist_mbid: str | None = _ARTIST_MBID,
    rg_mbid: str | None = _RG_MBID,
    status: str = "resolved",
) -> MbzIds:
    return MbzIds(
        discogs_release_id=release_id,
        artist_mbid=artist_mbid,
        release_group_mbid=rg_mbid,
        resolved_at=datetime.now(UTC),
        status=status,
    )


def _seeded_cache(*mbz_list: MbzIds) -> MbzCache:
    cache = MbzCache("irrelevant.json")
    for mbz in mbz_list:
        cache.set(mbz)
    return cache


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


def _mock_lidarr(existing_artist_mbids: set[str] | None = None) -> MagicMock:
    client = MagicMock()
    # get_all_artist_mbids is called via the sync module's imported function;
    # we patch that directly in the tests that need it.
    return client


# ── compute_diff ───────────────────────────────────────────────────────────────

class TestComputeDiff:
    def test_resolved_item_not_in_lidarr_goes_to_add(self) -> None:
        item = _discogs_item()
        cache = _seeded_cache(_mbz_ids(release_id=item.discogs_release_id))
        to_add, to_skip = compute_diff([item], set(), set(), cache)
        assert len(to_add) == 1
        assert len(to_skip) == 0
        assert to_add[0].action == SyncAction.ADDED_ALBUM

    def test_already_in_lidarr_goes_to_skip(self) -> None:
        item = _discogs_item()
        cache = _seeded_cache(_mbz_ids(release_id=item.discogs_release_id))
        to_add, to_skip = compute_diff([item], set(), {_RG_MBID}, cache)
        assert len(to_add) == 0
        assert len(to_skip) == 1
        assert to_skip[0].action == SyncAction.SKIPPED_EXISTS

    def test_unresolvable_item_goes_to_skip(self) -> None:
        item = _discogs_item()
        cache = _seeded_cache(
            _mbz_ids(
                release_id=item.discogs_release_id, artist_mbid=None, rg_mbid=None, status="failed"
            )
        )
        to_add, to_skip = compute_diff([item], set(), set(), cache)
        assert len(to_add) == 0
        assert len(to_skip) == 1
        assert to_skip[0].action == SyncAction.SKIPPED_UNRESOLVED

    def test_partial_no_rg_mbid_goes_to_skip(self) -> None:
        """Items with artist MBID but no release group MBID are unresolvable."""
        item = _discogs_item()
        cache = _seeded_cache(
            _mbz_ids(release_id=item.discogs_release_id, rg_mbid=None, status="partial")
        )
        to_add, to_skip = compute_diff([item], set(), set(), cache)
        assert len(to_skip) == 1
        assert to_skip[0].action == SyncAction.SKIPPED_UNRESOLVED

    def test_mixed_items_correctly_split(self) -> None:
        items = [
            _discogs_item(release_id=1, title="New Album"),
            _discogs_item(release_id=2, title="Existing Album"),
            _discogs_item(release_id=3, title="Unresolvable Album"),
        ]
        cache = _seeded_cache(
            _mbz_ids(release_id=1, rg_mbid="new-rg-mbid"),
            _mbz_ids(release_id=2, rg_mbid=_RG_MBID),
            _mbz_ids(release_id=3, rg_mbid=None, status="failed"),
        )
        to_add, to_skip = compute_diff(items, set(), {_RG_MBID}, cache)
        assert len(to_add) == 1
        assert to_add[0].item.discogs_release_id == 1
        assert len(to_skip) == 2

    def test_empty_collection_returns_empty_lists(self) -> None:
        cache = MbzCache("irrelevant.json")
        to_add, to_skip = compute_diff([], set(), set(), cache)
        assert to_add == []
        assert to_skip == []

    def test_mbz_ids_attached_to_results(self) -> None:
        item = _discogs_item()
        mbz = _mbz_ids(release_id=item.discogs_release_id)
        cache = _seeded_cache(mbz)
        to_add, _ = compute_diff([item], set(), set(), cache)
        assert to_add[0].mbz_ids is not None
        assert to_add[0].mbz_ids.artist_mbid == _ARTIST_MBID
        assert to_add[0].mbz_ids.release_group_mbid == _RG_MBID


# ── apply_diff ─────────────────────────────────────────────────────────────────

class TestApplyDiffDryRun:
    def test_dry_run_makes_no_api_calls(self) -> None:
        client = _mock_lidarr()
        item = _discogs_item()
        sr = SyncResult(item=item, mbz_ids=_mbz_ids(), action=SyncAction.ADDED_ALBUM)
        with patch("discogs_lidarr_sync.sync.get_all_artist_mbids") as mock_get:
            apply_diff([sr], client, _settings(), dry_run=True)
        mock_get.assert_not_called()
        client.add_artist.assert_not_called()
        client.add_album.assert_not_called()

    def test_dry_run_sets_skipped_dry_run_action(self) -> None:
        item = _discogs_item()
        sr = SyncResult(item=item, mbz_ids=_mbz_ids(), action=SyncAction.ADDED_ALBUM)
        report = apply_diff([sr], _mock_lidarr(), _settings(), dry_run=True)
        assert report.results[0].action == SyncAction.SKIPPED_DRY_RUN

    def test_dry_run_report_has_zero_counts(self) -> None:
        item = _discogs_item()
        sr = SyncResult(item=item, mbz_ids=_mbz_ids(), action=SyncAction.ADDED_ALBUM)
        report = apply_diff([sr], _mock_lidarr(), _settings(), dry_run=True)
        assert report.artists_added == 0
        assert report.albums_added == 0
        assert report.errors == 0
        assert report.dry_run is True

    def test_empty_to_add_dry_run(self) -> None:
        report = apply_diff([], _mock_lidarr(), _settings(), dry_run=True)
        assert report.albums_added == 0
        assert report.results == []


class TestApplyDiffFullRun:
    def _sr(
        self,
        release_id: int = 1,
        artist_mbid: str | None = _ARTIST_MBID,
        rg_mbid: str | None = _RG_MBID,
    ) -> SyncResult:
        return SyncResult(
            item=_discogs_item(release_id=release_id),
            mbz_ids=_mbz_ids(release_id=release_id, artist_mbid=artist_mbid, rg_mbid=rg_mbid),
            action=SyncAction.ADDED_ALBUM,
        )

    def test_adds_artist_and_album_when_artist_new(self) -> None:
        sr = self._sr()
        with (
            patch("discogs_lidarr_sync.sync.get_all_artist_mbids", return_value=set()),
            patch("discogs_lidarr_sync.sync.add_artist") as mock_add_artist,
            patch("discogs_lidarr_sync.sync.add_album") as mock_add_album,
        ):
            report = apply_diff([sr], _mock_lidarr(), _settings(), dry_run=False)
        mock_add_artist.assert_called_once()
        mock_add_album.assert_called_once()
        assert report.artists_added == 1
        assert report.albums_added == 1
        assert report.errors == 0

    def test_skips_artist_add_when_already_exists(self) -> None:
        sr = self._sr()
        with (
            patch("discogs_lidarr_sync.sync.get_all_artist_mbids", return_value={_ARTIST_MBID}),
            patch("discogs_lidarr_sync.sync.add_artist") as mock_add_artist,
            patch("discogs_lidarr_sync.sync.add_album"),
        ):
            report = apply_diff([sr], _mock_lidarr(), _settings(), dry_run=False)
        mock_add_artist.assert_not_called()
        assert report.artists_added == 0
        assert report.albums_added == 1

    def test_artist_add_failure_skips_album_and_records_error(self) -> None:
        from discogs_lidarr_sync.lidarr import LidarrError
        sr = self._sr()
        with (
            patch("discogs_lidarr_sync.sync.get_all_artist_mbids", return_value=set()),
            patch("discogs_lidarr_sync.sync.add_artist", side_effect=LidarrError("add failed")),
            patch("discogs_lidarr_sync.sync.add_album") as mock_add_album,
        ):
            report = apply_diff([sr], _mock_lidarr(), _settings(), dry_run=False)
        mock_add_album.assert_not_called()
        assert report.errors == 1
        assert report.albums_added == 0
        assert report.results[0].action == SyncAction.ERROR

    def test_album_add_failure_records_error(self) -> None:
        from discogs_lidarr_sync.lidarr import LidarrError
        sr = self._sr()
        with (
            patch("discogs_lidarr_sync.sync.get_all_artist_mbids", return_value={_ARTIST_MBID}),
            patch("discogs_lidarr_sync.sync.add_album", side_effect=LidarrError("add failed")),
        ):
            report = apply_diff([sr], _mock_lidarr(), _settings(), dry_run=False)
        assert report.errors == 1
        assert report.albums_added == 0
        assert report.results[0].action == SyncAction.ERROR
        assert "add failed" in (report.results[0].error or "")

    def test_artist_added_only_once_for_multiple_albums(self) -> None:
        """Two albums by the same artist should only trigger one add_artist call."""
        sr1 = self._sr(release_id=1, rg_mbid=_RG_MBID)
        sr2 = self._sr(release_id=2, rg_mbid=_RG_MBID_2)
        with (
            patch("discogs_lidarr_sync.sync.get_all_artist_mbids", return_value=set()),
            patch("discogs_lidarr_sync.sync.add_artist") as mock_add_artist,
            patch("discogs_lidarr_sync.sync.add_album"),
        ):
            report = apply_diff([sr1, sr2], _mock_lidarr(), _settings(), dry_run=False)
        assert mock_add_artist.call_count == 1
        assert report.artists_added == 1
        assert report.albums_added == 2

    def test_error_in_one_item_does_not_abort_run(self) -> None:
        from discogs_lidarr_sync.lidarr import LidarrError
        sr1 = self._sr(release_id=1, rg_mbid=_RG_MBID)
        sr2 = self._sr(release_id=2, rg_mbid=_RG_MBID_2)
        add_album_calls = 0

        def fake_add_album(client: object, mbid: str, *args: object, **kwargs: object) -> None:
            nonlocal add_album_calls
            add_album_calls += 1
            if mbid == _RG_MBID:
                raise LidarrError("first album failed")

        with (
            patch("discogs_lidarr_sync.sync.get_all_artist_mbids", return_value={_ARTIST_MBID}),
            patch("discogs_lidarr_sync.sync.add_album", side_effect=fake_add_album),
        ):
            report = apply_diff([sr1, sr2], _mock_lidarr(), _settings(), dry_run=False)

        assert add_album_calls == 2
        assert report.errors == 1
        assert report.albums_added == 1

    def test_empty_to_add_returns_empty_report(self) -> None:
        with patch("discogs_lidarr_sync.sync.get_all_artist_mbids", return_value=set()):
            report = apply_diff([], _mock_lidarr(), _settings(), dry_run=False)
        assert report.albums_added == 0
        assert report.artists_added == 0
        assert report.results == []

    def test_result_action_set_to_added_album_on_success(self) -> None:
        sr = self._sr()
        with (
            patch("discogs_lidarr_sync.sync.get_all_artist_mbids", return_value={_ARTIST_MBID}),
            patch("discogs_lidarr_sync.sync.add_album"),
        ):
            report = apply_diff([sr], _mock_lidarr(), _settings(), dry_run=False)
        assert report.results[0].action == SyncAction.ADDED_ALBUM


# ── Idempotency ────────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_second_run_adds_nothing(self) -> None:
        """After a successful run, a second compute_diff produces an empty to_add."""
        item = _discogs_item()
        cache = _seeded_cache(_mbz_ids(release_id=item.discogs_release_id))

        artist_mbids: set[str] = set()
        album_mbids: set[str] = set()

        # First run — item should go to to_add.
        to_add, to_skip = compute_diff([item], artist_mbids, album_mbids, cache)
        assert len(to_add) == 1

        # Simulate Lidarr state after apply_diff succeeded.
        album_mbids.add(_RG_MBID)
        artist_mbids.add(_ARTIST_MBID)

        # Second run — item already in Lidarr.
        to_add2, to_skip2 = compute_diff([item], artist_mbids, album_mbids, cache)
        assert len(to_add2) == 0
        assert len(to_skip2) == 1
        assert to_skip2[0].action == SyncAction.SKIPPED_EXISTS


# ── write_report ───────────────────────────────────────────────────────────────

class TestWriteReport:
    def _report(self, results: list[SyncResult] | None = None) -> RunReport:
        return RunReport(
            run_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
            dry_run=False,
            total_vinyl=3,
            artists_added=1,
            albums_added=2,
            skipped_exists=0,
            skipped_unresolved=1,
            errors=0,
            results=results or [],
        )

    def test_creates_json_file_in_output_dir(self, tmp_path: Path) -> None:
        write_report(self._report(), tmp_path)
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

    def test_filename_includes_timestamp(self, tmp_path: Path) -> None:
        write_report(self._report(), tmp_path)
        filename = list(tmp_path.glob("*.json"))[0].name
        assert "20240601T120000Z" in filename

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        write_report(self._report(), tmp_path)
        path = list(tmp_path.glob("*.json"))[0]
        data = json.loads(path.read_text())
        assert data["artists_added"] == 1
        assert data["albums_added"] == 2

    def test_results_included_in_json(self, tmp_path: Path) -> None:
        item = _discogs_item()
        sr = SyncResult(item=item, mbz_ids=_mbz_ids(), action=SyncAction.ADDED_ALBUM)
        write_report(self._report(results=[sr]), tmp_path)
        data = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
        assert len(data["results"]) == 1
        assert data["results"][0]["action"] == "added_album"

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "runs"
        write_report(self._report(), nested)
        assert any(nested.glob("*.json"))


# ── write_unresolved ───────────────────────────────────────────────────────────

class TestWriteUnresolved:
    def _unresolved_sr(self, release_id: int = 99) -> SyncResult:
        return SyncResult(
            item=_discogs_item(release_id=release_id, title="Unknown Album"),
            mbz_ids=_mbz_ids(release_id=release_id, rg_mbid=None, status="failed"),
            action=SyncAction.SKIPPED_UNRESOLVED,
        )

    def test_creates_file_and_writes_item(self, tmp_path: Path) -> None:
        path = tmp_path / "unresolved.log"
        write_unresolved([self._unresolved_sr()], path)
        assert path.exists()
        content = path.read_text()
        assert "Unknown Album" in content
        assert "99" in content

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "logs" / "unresolved.log"
        write_unresolved([self._unresolved_sr()], path)
        assert path.exists()

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "unresolved.log"
        write_unresolved([self._unresolved_sr(release_id=1)], path)
        write_unresolved([self._unresolved_sr(release_id=2)], path)
        lines = [ln for ln in path.read_text().splitlines() if ln]
        assert len(lines) == 2

    def test_empty_list_does_not_create_file(self, tmp_path: Path) -> None:
        path = tmp_path / "unresolved.log"
        write_unresolved([], path)
        assert not path.exists()

    def test_tab_separated_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "unresolved.log"
        write_unresolved([self._unresolved_sr()], path)
        line = path.read_text().strip()
        fields = line.split("\t")
        assert fields[0] == "99"
        assert fields[2] == "Unknown Album"
