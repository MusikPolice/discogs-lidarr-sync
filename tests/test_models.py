"""Tests for models.py."""

from __future__ import annotations

from datetime import UTC, datetime

from discogs_lidarr_sync.models import (
    DiscogsItem,
    MbzIds,
    RunReport,
    SyncAction,
    SyncResult,
)

NOW = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# DiscogsItem
# ---------------------------------------------------------------------------


def test_discogs_item_construction() -> None:
    item = DiscogsItem(
        discogs_release_id=742874,
        discogs_artist_id=125410,
        artist_name="Psychedelic Furs, The",
        album_title="The Psychedelic Furs",
        year=1980,
        formats=["Vinyl", "LP", "Album"],
    )
    assert item.discogs_release_id == 742874
    assert item.discogs_artist_id == 125410
    assert item.artist_name == "Psychedelic Furs, The"
    assert item.album_title == "The Psychedelic Furs"
    assert item.year == 1980
    assert item.formats == ["Vinyl", "LP", "Album"]


def test_discogs_item_year_can_be_none() -> None:
    item = DiscogsItem(
        discogs_release_id=1,
        discogs_artist_id=2,
        artist_name="Unknown",
        album_title="Untitled",
        year=None,
        formats=["Vinyl"],
    )
    assert item.year is None


def test_discogs_item_equality() -> None:
    item_a = DiscogsItem(1, 2, "Artist", "Album", 2000, ["Vinyl"])
    item_b = DiscogsItem(1, 2, "Artist", "Album", 2000, ["Vinyl"])
    assert item_a == item_b


def test_discogs_item_inequality() -> None:
    item_a = DiscogsItem(1, 2, "Artist", "Album", 2000, ["Vinyl"])
    item_b = DiscogsItem(9, 2, "Artist", "Album", 2000, ["Vinyl"])
    assert item_a != item_b


# ---------------------------------------------------------------------------
# MbzIds
# ---------------------------------------------------------------------------


def test_mbz_ids_resolved() -> None:
    mbz = MbzIds(
        discogs_release_id=742874,
        artist_mbid="b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
        release_group_mbid="1dc4c347-a1db-32aa-b14f-bc9cc507b843",
        resolved_at=NOW,
        status="resolved",
    )
    assert mbz.artist_mbid is not None
    assert mbz.release_group_mbid is not None
    assert mbz.status == "resolved"
    assert mbz.error is None


def test_mbz_ids_failed() -> None:
    mbz = MbzIds(
        discogs_release_id=99999,
        artist_mbid=None,
        release_group_mbid=None,
        resolved_at=NOW,
        status="failed",
        error="No MusicBrainz entry found",
    )
    assert mbz.artist_mbid is None
    assert mbz.release_group_mbid is None
    assert mbz.status == "failed"
    assert mbz.error == "No MusicBrainz entry found"


def test_mbz_ids_partial() -> None:
    """Partial means the artist resolved but the release group did not."""
    mbz = MbzIds(
        discogs_release_id=12345,
        artist_mbid="b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
        release_group_mbid=None,
        resolved_at=NOW,
        status="partial",
    )
    assert mbz.artist_mbid is not None
    assert mbz.release_group_mbid is None
    assert mbz.status == "partial"


# ---------------------------------------------------------------------------
# SyncAction
# ---------------------------------------------------------------------------


def test_sync_action_values() -> None:
    assert SyncAction.ADDED_ARTIST == "added_artist"
    assert SyncAction.ADDED_ALBUM == "added_album"
    assert SyncAction.SKIPPED_EXISTS == "skipped_exists"
    assert SyncAction.SKIPPED_UNRESOLVED == "skipped_unresolved"
    assert SyncAction.SKIPPED_DRY_RUN == "skipped_dry_run"
    assert SyncAction.ERROR == "error"


def test_sync_action_is_str() -> None:
    """SyncAction values must behave as plain strings (StrEnum)."""
    assert isinstance(SyncAction.ADDED_ARTIST, str)
    assert SyncAction.ADDED_ARTIST.upper() == "ADDED_ARTIST"


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------


def _make_item() -> DiscogsItem:
    return DiscogsItem(1, 2, "Artist", "Album", 2000, ["Vinyl"])


def test_sync_result_added() -> None:
    mbz = MbzIds(1, "artist-mbid", "rg-mbid", NOW, "resolved")
    result = SyncResult(item=_make_item(), mbz_ids=mbz, action=SyncAction.ADDED_ALBUM)
    assert result.action == SyncAction.ADDED_ALBUM
    assert result.error is None


def test_sync_result_error() -> None:
    result = SyncResult(
        item=_make_item(),
        mbz_ids=None,
        action=SyncAction.ERROR,
        error="API timeout",
    )
    assert result.mbz_ids is None
    assert result.error == "API timeout"


# ---------------------------------------------------------------------------
# RunReport
# ---------------------------------------------------------------------------


def test_run_report_construction() -> None:
    report = RunReport(
        run_at=NOW,
        dry_run=False,
        total_vinyl=50,
        artists_added=3,
        albums_added=5,
        skipped_exists=40,
        skipped_unresolved=2,
        errors=0,
    )
    assert report.total_vinyl == 50
    assert report.dry_run is False
    assert report.results == []  # default_factory


def test_run_report_results_list_is_independent() -> None:
    """Each RunReport instance must have its own results list (not shared)."""
    r1 = RunReport(NOW, False, 0, 0, 0, 0, 0, 0)
    r2 = RunReport(NOW, False, 0, 0, 0, 0, 0, 0)
    r1.results.append(SyncResult(_make_item(), None, SyncAction.ERROR))
    assert r2.results == []


def test_run_report_dry_run_flag() -> None:
    report = RunReport(
        NOW,
        dry_run=True,
        total_vinyl=10,
        artists_added=0,
        albums_added=0,
        skipped_exists=10,
        skipped_unresolved=0,
        errors=0,
    )
    assert report.dry_run is True
