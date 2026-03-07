"""Unit tests for purge.py."""

from __future__ import annotations

import csv
import warnings
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from discogs_lidarr_sync.models import PurgeRow
from discogs_lidarr_sync.purge import apply_ghost_purge, apply_purge, compute_purge, read_purge_csv

# ── Helpers ────────────────────────────────────────────────────────────────────

_FULL_FIELDNAMES = [
    "action",
    "artist_name",
    "album_title",
    "year",
    "tracks_owned",
    "total_tracks",
    "pct_owned",
    "discogs_match",
    "album_mbid",
    "artist_mbid",
    "lidarr_album_id",
    "lidarr_artist_id",
]


def _make_csv(
    tmp_path: Path,
    rows: list[dict[str, str]],
    fieldnames: list[str] | None = None,
) -> Path:
    path = tmp_path / "audit.csv"
    if fieldnames is None:
        fieldnames = _FULL_FIELDNAMES
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _csv_row(
    action: str = "delete",
    artist_name: str = "The Police",
    album_title: str = "Ghost in the Machine",
    lidarr_album_id: str = "42",
    lidarr_artist_id: str = "7",
) -> dict[str, str]:
    return {
        "action": action,
        "artist_name": artist_name,
        "album_title": album_title,
        "year": "1981",
        "tracks_owned": "5",
        "total_tracks": "10",
        "pct_owned": "50.0",
        "discogs_match": "no",
        "album_mbid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "artist_mbid": "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee",
        "lidarr_album_id": lidarr_album_id,
        "lidarr_artist_id": lidarr_artist_id,
    }


def _purge_row(
    action: str = "delete",
    lidarr_album_id: int = 42,
    lidarr_artist_id: int = 7,
    artist_name: str = "The Police",
    album_title: str = "Ghost in the Machine",
) -> PurgeRow:
    return PurgeRow(
        action=action,
        artist_name=artist_name,
        album_title=album_title,
        lidarr_album_id=lidarr_album_id,
        lidarr_artist_id=lidarr_artist_id,
    )


def _mock_client() -> MagicMock:
    return MagicMock()


# ── read_purge_csv ─────────────────────────────────────────────────────────────


class TestReadPurgeCsv:
    def test_parses_all_fields(self, tmp_path: Path) -> None:
        path = _make_csv(tmp_path, [_csv_row()])
        rows = read_purge_csv(path)
        assert len(rows) == 1
        r = rows[0]
        assert r.action == "delete"
        assert r.artist_name == "The Police"
        assert r.album_title == "Ghost in the Machine"
        assert r.lidarr_album_id == 42
        assert r.lidarr_artist_id == 7

    def test_strips_and_lowercases_action(self, tmp_path: Path) -> None:
        path = _make_csv(tmp_path, [_csv_row(action=" Keep ")])
        rows = read_purge_csv(path)
        assert rows[0].action == "keep"

    def test_mixed_case_action_normalised(self, tmp_path: Path) -> None:
        path = _make_csv(tmp_path, [_csv_row(action="DELETE")])
        rows = read_purge_csv(path)
        assert rows[0].action == "delete"

    def test_blank_action_treated_as_keep(self, tmp_path: Path) -> None:
        path = _make_csv(tmp_path, [_csv_row(action="")])
        rows = read_purge_csv(path)
        assert rows[0].action == "keep"

    def test_skips_invalid_album_id_with_warning(self, tmp_path: Path) -> None:
        path = _make_csv(tmp_path, [_csv_row(lidarr_album_id="not-an-int")])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            rows = read_purge_csv(path)
        assert rows == []
        assert any("lidarr_album_id" in str(w.message) for w in caught)

    def test_raises_on_missing_required_column(self, tmp_path: Path) -> None:
        # omit lidarr_album_id from the fieldnames
        fields = ["action", "artist_name", "album_title", "lidarr_artist_id"]
        path = _make_csv(
            tmp_path,
            [{"action": "delete", "artist_name": "X", "album_title": "Y", "lidarr_artist_id": "7"}],
            fieldnames=fields,
        )
        with pytest.raises(ValueError, match="lidarr_album_id"):
            read_purge_csv(path)

    def test_extra_columns_ignored(self, tmp_path: Path) -> None:
        fields = _FULL_FIELDNAMES + ["my_notes"]
        row = dict(_csv_row())
        row["my_notes"] = "check this one"
        path = _make_csv(tmp_path, [row], fieldnames=fields)
        rows = read_purge_csv(path)
        assert len(rows) == 1

    def test_multiple_rows_parsed(self, tmp_path: Path) -> None:
        path = _make_csv(tmp_path, [_csv_row(lidarr_album_id="1"), _csv_row(lidarr_album_id="2")])
        rows = read_purge_csv(path)
        assert len(rows) == 2
        assert rows[0].lidarr_album_id == 1
        assert rows[1].lidarr_album_id == 2


# ── compute_purge ──────────────────────────────────────────────────────────────


class TestComputePurge:
    def test_splits_on_action(self) -> None:
        rows = [_purge_row("delete"), _purge_row("keep"), _purge_row("delete")]
        to_delete, to_skip = compute_purge(rows)
        assert len(to_delete) == 2
        assert len(to_skip) == 1

    def test_non_delete_action_goes_to_skip(self) -> None:
        rows = [_purge_row("keep"), _purge_row("hold")]
        to_delete, to_skip = compute_purge(rows)
        assert to_delete == []
        assert len(to_skip) == 2

    def test_all_delete(self) -> None:
        rows = [_purge_row("delete"), _purge_row("delete")]
        to_delete, to_skip = compute_purge(rows)
        assert len(to_delete) == 2
        assert to_skip == []

    def test_empty_input(self) -> None:
        assert compute_purge([]) == ([], [])


# ── apply_purge ────────────────────────────────────────────────────────────────


class TestApplyPurge:
    def test_calls_delete_album_for_each_row(self) -> None:
        client = _mock_client()
        client.get_album.return_value = []  # no monitored albums left → artist also deleted
        rows = [
            _purge_row(lidarr_album_id=1, lidarr_artist_id=5),
            _purge_row(lidarr_album_id=2, lidarr_artist_id=6),
        ]
        report = apply_purge(rows, client, dry_run=False)
        assert report.albums_deleted == 2

    def test_dry_run_makes_no_api_calls(self) -> None:
        client = _mock_client()
        rows = [_purge_row()]
        report = apply_purge(rows, client, dry_run=True)
        client._delete.assert_not_called()
        client.get_album.assert_not_called()
        assert report.albums_deleted == 0
        assert report.dry_run is True
        assert report.to_delete == 1

    def test_skips_already_gone_albums(self) -> None:
        client = _mock_client()
        client._delete.side_effect = Exception("404 Not Found")
        client.get_album.return_value = []
        rows = [_purge_row()]
        report = apply_purge(rows, client, dry_run=False)
        assert report.already_gone == 1
        assert report.albums_deleted == 0
        assert report.errors == 0

    def test_records_error_without_aborting(self) -> None:
        client = _mock_client()
        # First album call raises a 500; second album call and artist call succeed.
        client._delete.side_effect = [
            Exception("500 Internal Server Error"),
            None,  # second album
            None,  # artist (from touched by second album)
        ]
        client.get_album.return_value = []
        rows = [
            _purge_row(lidarr_album_id=1, lidarr_artist_id=7),
            _purge_row(lidarr_album_id=2, lidarr_artist_id=7),
        ]
        report = apply_purge(rows, client, dry_run=False)
        assert report.errors == 1
        assert report.albums_deleted == 1
        assert len(report.error_details) == 1

    def test_deletes_artist_when_no_monitored_albums_remain(self) -> None:
        client = _mock_client()
        client.get_album.return_value = []  # no monitored albums remain
        rows = [_purge_row(lidarr_album_id=10, lidarr_artist_id=5)]
        report = apply_purge(rows, client, dry_run=False)
        assert report.artists_deleted == 1

    def test_keeps_artist_with_remaining_monitored_albums(self) -> None:
        client = _mock_client()
        # Artist 5 still has one monitored album after the deletion
        client.get_album.return_value = [
            {"artist": {"id": 5}, "monitored": True, "foreignAlbumId": "xxx"}
        ]
        rows = [_purge_row(lidarr_album_id=10, lidarr_artist_id=5)]
        report = apply_purge(rows, client, dry_run=False)
        assert report.artists_deleted == 0

    def test_deduplicates_artist_checks(self) -> None:
        """Artist with 3 deleted albums → artist check runs once."""
        client = _mock_client()
        client.get_album.return_value = []
        rows = [
            _purge_row(lidarr_album_id=1, lidarr_artist_id=5),
            _purge_row(lidarr_album_id=2, lidarr_artist_id=5),
            _purge_row(lidarr_album_id=3, lidarr_artist_id=5),
        ]
        report = apply_purge(rows, client, dry_run=False)
        assert report.artists_deleted == 1
        # 3 album deletes + 1 artist delete = 4 _delete calls
        assert client._delete.call_count == 4
        # get_album called once for the artist check
        assert client.get_album.call_count == 1

    def test_delete_files_forwarded_to_album_delete(self) -> None:
        client = _mock_client()
        client.get_album.return_value = []
        rows = [_purge_row()]
        apply_purge(rows, client, dry_run=False, delete_files=True)
        album_call = client._delete.call_args_list[0]
        assert album_call.kwargs["params"]["deleteFiles"] is True

    def test_delete_files_forwarded_to_artist_delete(self) -> None:
        client = _mock_client()
        client.get_album.return_value = []
        rows = [_purge_row()]
        apply_purge(rows, client, dry_run=False, delete_files=True)
        assert client._delete.call_count == 2  # 1 album + 1 artist
        artist_call = client._delete.call_args_list[1]
        assert artist_call.kwargs["params"]["deleteFiles"] is True

    def test_delete_files_false_by_default(self) -> None:
        client = _mock_client()
        client.get_album.return_value = []
        rows = [_purge_row()]
        apply_purge(rows, client, dry_run=False)
        album_call = client._delete.call_args_list[0]
        assert album_call.kwargs["params"]["deleteFiles"] is False


# ── apply_ghost_purge ─────────────────────────────────────────────────────────


def _ghost_album(
    album_id: int = 1,
    artist_id: int = 10,
    artist_name: str = "Ben Folds Five",
    title: str = "Whatever and Ever Amen",
) -> dict:
    return {
        "id": album_id,
        "title": title,
        "foreignAlbumId": f"mbid-{album_id}",
        "monitored": False,
        "artist": {"id": artist_id, "artistName": artist_name},
        "statistics": {"trackFileCount": 0},
    }


class TestApplyGhostPurge:
    def test_dry_run_makes_no_api_calls(self) -> None:
        client = _mock_client()
        client.get_album.return_value = [_ghost_album()]
        report = apply_ghost_purge(client, dry_run=True)
        client._delete.assert_not_called()
        assert report.dry_run is True
        assert report.ghosts_found == 1
        assert report.albums_deleted == 0

    def test_deletes_all_ghost_albums(self) -> None:
        client = _mock_client()
        client.get_album.return_value = [
            _ghost_album(album_id=1),
            _ghost_album(album_id=2),
        ]
        report = apply_ghost_purge(client, dry_run=False)
        assert report.albums_deleted == 2
        assert report.ghosts_found == 2

    def test_deletes_artist_when_no_auditable_content_remains(self) -> None:
        client = _mock_client()
        client.get_album.return_value = [_ghost_album(artist_id=10)]
        # Pass 2 check: no auditable albums left
        client.get_album.side_effect = [
            [_ghost_album(artist_id=10)],  # get_ghost_albums
            [],  # get_auditable_album_count_for_artist
        ]
        report = apply_ghost_purge(client, dry_run=False)
        assert report.artists_deleted == 1

    def test_keeps_artist_with_remaining_auditable_content(self) -> None:
        client = _mock_client()
        remaining = {"artist": {"id": 10}, "monitored": True, "statistics": {"trackFileCount": 0}}
        client.get_album.side_effect = [
            [_ghost_album(artist_id=10)],  # get_ghost_albums
            [remaining],  # get_auditable_album_count_for_artist
        ]
        report = apply_ghost_purge(client, dry_run=False)
        assert report.artists_deleted == 0

    def test_already_gone_counted_not_errored(self) -> None:
        client = _mock_client()
        client.get_album.return_value = [_ghost_album()]
        client._delete.side_effect = Exception("404 Not Found")
        report = apply_ghost_purge(client, dry_run=False)
        assert report.already_gone == 1
        assert report.errors == 0

    def test_delete_error_recorded_without_aborting(self) -> None:
        client = _mock_client()
        client.get_album.side_effect = [
            [_ghost_album(album_id=1, artist_id=10), _ghost_album(album_id=2, artist_id=11)],
            [],  # auditable count for artist 11 after its album deleted
        ]
        client._delete.side_effect = [
            Exception("500 Internal Server Error"),
            None,  # second album
            None,  # artist 11
        ]
        report = apply_ghost_purge(client, dry_run=False)
        assert report.errors == 1
        assert report.albums_deleted == 1

    def test_deduplicates_artist_checks(self) -> None:
        client = _mock_client()
        client.get_album.side_effect = [
            [_ghost_album(album_id=1, artist_id=5), _ghost_album(album_id=2, artist_id=5)],
            [],  # auditable check — called once for artist 5
        ]
        report = apply_ghost_purge(client, dry_run=False)
        assert report.artists_deleted == 1
        assert client.get_album.call_count == 2  # once for ghosts, once for artist check

    def test_delete_files_forwarded(self) -> None:
        client = _mock_client()
        client.get_album.return_value = [_ghost_album(artist_id=10)]
        client.get_album.side_effect = [
            [_ghost_album(artist_id=10)],
            [],
        ]
        apply_ghost_purge(client, dry_run=False, delete_files=True)
        album_call = client._delete.call_args_list[0]
        assert album_call.kwargs["params"]["deleteFiles"] is True
