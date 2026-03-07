"""Unit tests for audit.py."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from discogs_lidarr_sync.audit import compute_audit, write_audit_csv
from discogs_lidarr_sync.mbz import MbzCache
from discogs_lidarr_sync.models import AuditRow, DiscogsItem, MbzIds

# ── Test data helpers ──────────────────────────────────────────────────────────

_ARTIST_MBID = "9e0e2b01-41db-4008-bd8b-988977d6019a"
_RG_MBID_A = "f5093c06-23e3-404f-aeaa-40f72885ee3a"  # in Discogs
_RG_MBID_B = "3d37c4e7-aaed-4d32-8d8c-9b8b3a7c68e4"  # NOT in Discogs


def _discogs_item(release_id: int = 1) -> DiscogsItem:
    return DiscogsItem(
        discogs_release_id=release_id,
        discogs_artist_id=100,
        artist_name="The Police",
        album_title="Synchronicity",
        year=1983,
        formats=["Vinyl"],
    )


def _mbz_ids(release_id: int, rg_mbid: str | None) -> MbzIds:
    return MbzIds(
        discogs_release_id=release_id,
        artist_mbid=_ARTIST_MBID,
        release_group_mbid=rg_mbid,
        resolved_at=datetime.now(UTC),
        status="resolved" if rg_mbid else "failed",
    )


def _warm_cache(*pairs: tuple[int, str | None]) -> MbzCache:
    """Build an in-memory MbzCache pre-populated with (release_id, rg_mbid) pairs."""
    cache = MbzCache(".cache/test_audit.json")  # path never read/written in tests
    for release_id, rg_mbid in pairs:
        cache.set(_mbz_ids(release_id, rg_mbid))
    return cache


def _lidarr_album(
    album_mbid: str = _RG_MBID_B,
    *,
    artist_mbid: str = _ARTIST_MBID,
    title: str = "Ghost in the Machine",
    release_date: str = "1981-10-02T00:00:00Z",
    monitored: bool = True,
    track_file_count: int = 5,
    total_track_count: int = 10,
    lidarr_album_id: int = 42,
    lidarr_artist_id: int = 7,
) -> dict:
    return {
        "id": lidarr_album_id,
        "title": title,
        "foreignAlbumId": album_mbid,
        "monitored": monitored,
        "releaseDate": release_date,
        "artist": {
            "id": lidarr_artist_id,
            "artistName": "The Police",
            "foreignArtistId": artist_mbid,
        },
        "statistics": {
            "trackFileCount": track_file_count,
            "totalTrackCount": total_track_count,
        },
    }


# ── compute_audit ─────────────────────────────────────────────────────────────


class TestComputeAudit:
    def test_excludes_albums_in_discogs(self) -> None:
        """Album whose MBID is in the Discogs-owned set is silently omitted."""
        cache = _warm_cache((1, _RG_MBID_A))
        rows = compute_audit(
            [_discogs_item(1)],
            cache,
            [_lidarr_album(_RG_MBID_A)],
        )
        assert rows == []

    def test_includes_album_not_in_discogs(self) -> None:
        """Album whose MBID is absent from Discogs is included with discogs_match='no'."""
        cache = _warm_cache((1, _RG_MBID_A))
        rows = compute_audit(
            [_discogs_item(1)],
            cache,
            [_lidarr_album(_RG_MBID_B)],
        )
        assert len(rows) == 1
        assert rows[0].discogs_match == "no"
        assert rows[0].album_mbid == _RG_MBID_B

    def test_flags_album_with_no_foreign_id(self) -> None:
        """Album with no foreignAlbumId is included with discogs_match='unresolved'."""
        cache = _warm_cache((1, _RG_MBID_A))
        album = _lidarr_album(_RG_MBID_B)
        album["foreignAlbumId"] = ""  # simulate missing MBID
        rows = compute_audit([_discogs_item(1)], cache, [album])
        assert len(rows) == 1
        assert rows[0].discogs_match == "unresolved"
        assert rows[0].album_mbid == ""

    def test_action_defaults_to_delete(self) -> None:
        cache = _warm_cache((1, _RG_MBID_A))
        rows = compute_audit([_discogs_item(1)], cache, [_lidarr_album(_RG_MBID_B)])
        assert rows[0].action == "delete"

    def test_pct_owned_calculated_correctly(self) -> None:
        cache = _warm_cache()
        album = _lidarr_album(track_file_count=3, total_track_count=12)
        rows = compute_audit([], cache, [album])
        assert rows[0].tracks_owned == 3
        assert rows[0].total_tracks == 12
        assert rows[0].pct_owned == 25.0

    def test_pct_owned_zero_when_no_total_tracks(self) -> None:
        """No divide-by-zero when Lidarr hasn't indexed track count yet."""
        cache = _warm_cache()
        album = _lidarr_album(track_file_count=0, total_track_count=0)
        rows = compute_audit([], cache, [album])
        assert rows[0].pct_owned == 0.0

    def test_year_extracted_from_release_date(self) -> None:
        cache = _warm_cache()
        album = _lidarr_album(release_date="1983-06-17T00:00:00Z")
        rows = compute_audit([], cache, [album])
        assert rows[0].year == 1983

    def test_year_is_none_when_release_date_absent(self) -> None:
        cache = _warm_cache()
        album = _lidarr_album()
        del album["releaseDate"]
        rows = compute_audit([], cache, [album])
        assert rows[0].year is None

    def test_lidarr_ids_populated(self) -> None:
        cache = _warm_cache()
        album = _lidarr_album(lidarr_album_id=99, lidarr_artist_id=55)
        rows = compute_audit([], cache, [album])
        assert rows[0].lidarr_album_id == 99
        assert rows[0].lidarr_artist_id == 55

    def test_mixed_in_and_out_of_discogs(self) -> None:
        """Only the non-Discogs album appears in output."""
        cache = _warm_cache((1, _RG_MBID_A), (2, _RG_MBID_B))
        # _RG_MBID_A is owned; _RG_MBID_B is also owned; _RG_MBID_C is not
        _RG_MBID_C = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        rows = compute_audit(
            [_discogs_item(1), _discogs_item(2)],
            cache,
            [
                _lidarr_album(_RG_MBID_A),  # in Discogs → skip
                _lidarr_album(_RG_MBID_B),  # in Discogs → skip
                _lidarr_album(_RG_MBID_C),  # not in Discogs → include
            ],
        )
        assert len(rows) == 1
        assert rows[0].album_mbid == _RG_MBID_C

    def test_unresolved_discogs_items_dont_affect_lidarr_output(self) -> None:
        """Discogs items with failed MBZ resolution produce no owned MBIDs."""
        cache = _warm_cache((1, None))  # resolution failed — no MBID
        rows = compute_audit(
            [_discogs_item(1)],
            cache,
            [_lidarr_album(_RG_MBID_B)],
        )
        # Nothing was added to the owned set, so the Lidarr album is included
        assert len(rows) == 1
        assert rows[0].discogs_match == "no"

    def test_empty_lidarr_library(self) -> None:
        cache = _warm_cache((1, _RG_MBID_A))
        rows = compute_audit([_discogs_item(1)], cache, [])
        assert rows == []

    def test_empty_discogs_collection(self) -> None:
        """No owned MBIDs → every Lidarr album is a candidate."""
        cache = _warm_cache()
        rows = compute_audit([], cache, [_lidarr_album(_RG_MBID_B)])
        assert len(rows) == 1

    def test_monitored_field_reflects_album_state(self) -> None:
        """monitored column mirrors the Lidarr album's monitored flag."""
        cache = _warm_cache()
        monitored_album = _lidarr_album(_RG_MBID_B, monitored=True)
        unmonitored_album = _lidarr_album(_RG_MBID_B, monitored=False, lidarr_album_id=99)
        rows = compute_audit([], cache, [monitored_album, unmonitored_album])
        assert rows[0].monitored is True
        assert rows[1].monitored is False


# ── write_audit_csv ───────────────────────────────────────────────────────────


def _sample_row(**kwargs: object) -> AuditRow:
    defaults: dict[str, object] = {
        "action": "delete",
        "artist_name": "The Police",
        "album_title": "Ghost in the Machine",
        "year": 1981,
        "monitored": True,
        "tracks_owned": 5,
        "total_tracks": 10,
        "pct_owned": 50.0,
        "discogs_match": "no",
        "album_mbid": _RG_MBID_B,
        "artist_mbid": _ARTIST_MBID,
        "lidarr_album_id": 42,
        "lidarr_artist_id": 7,
    }
    defaults.update(kwargs)
    return AuditRow(**defaults)  # type: ignore[arg-type]


class TestWriteAuditCsv:
    def test_creates_file_with_correct_headers(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.csv"
        write_audit_csv([_sample_row()], out)
        with open(out, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames is not None
            assert "action" == reader.fieldnames[0], "action must be the first column"
            expected = {
                "action", "artist_name", "album_title", "year", "monitored",
                "tracks_owned", "total_tracks", "pct_owned", "discogs_match",
                "album_mbid", "artist_mbid", "lidarr_album_id", "lidarr_artist_id",
            }
            assert set(reader.fieldnames) == expected

    def test_one_row_per_audit_row(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.csv"
        write_audit_csv([_sample_row(), _sample_row()], out)
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2

    def test_empty_rows_writes_headers_only(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.csv"
        write_audit_csv([], out)
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows == []

    def test_year_none_written_as_empty_string(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.csv"
        write_audit_csv([_sample_row(year=None)], out)
        with open(out, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["year"] == ""

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        out = tmp_path / "a" / "b" / "audit.csv"
        write_audit_csv([_sample_row()], out)
        assert out.exists()

    def test_field_values_round_trip(self, tmp_path: Path) -> None:
        out = tmp_path / "audit.csv"
        write_audit_csv([_sample_row()], out)
        with open(out, newline="", encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert row["action"] == "delete"
        assert row["artist_name"] == "The Police"
        assert row["album_title"] == "Ghost in the Machine"
        assert row["year"] == "1981"
        assert row["tracks_owned"] == "5"
        assert row["total_tracks"] == "10"
        assert row["pct_owned"] == "50.0"
        assert row["discogs_match"] == "no"
        assert row["album_mbid"] == _RG_MBID_B
        assert row["artist_mbid"] == _ARTIST_MBID
        assert row["lidarr_album_id"] == "42"
        assert row["lidarr_artist_id"] == "7"
