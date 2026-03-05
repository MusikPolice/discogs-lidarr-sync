"""Tests for discogs.py — Phase 3."""

from __future__ import annotations

from unittest.mock import patch

import responses as resp

from discogs_lidarr_sync.discogs import fetch_collection, is_vinyl, normalize_item
from discogs_lidarr_sync.models import DiscogsItem

_BASE = "https://api.discogs.com"
_COLLECTION_URL = f"{_BASE}/users/testuser/collection/folders/0/releases"


# ── Test-data helpers ─────────────────────────────────────────────────────────


def _raw_item(
    release_id: int = 1,
    artist_id: int = 100,
    artist_name: str = "Test Artist",
    title: str = "Test Album",
    year: int = 2020,
    formats: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Build a minimal Discogs collection-release dict."""
    if formats is None:
        formats = [{"name": "Vinyl", "qty": "1", "descriptions": ["LP"]}]
    return {
        "id": release_id,
        "instance_id": 9999,
        "rating": 0,
        "folder_id": 0,
        "date_added": "2024-01-01T00:00:00-08:00",
        "basic_information": {
            "id": release_id,
            "title": title,
            "year": year,
            "formats": formats,
            "artists": [{"id": artist_id, "name": artist_name, "anv": "", "join": ""}],
        },
    }


def _page(releases: list[dict[str, object]], page: int = 1, pages: int = 1) -> dict[str, object]:
    """Build a minimal Discogs paginated collection response."""
    return {
        "pagination": {
            "page": page,
            "pages": pages,
            "per_page": 100,
            "items": len(releases),
        },
        "releases": releases,
    }


# ── normalize_item ────────────────────────────────────────────────────────────


class TestNormalizeItem:
    def test_basic_fields(self) -> None:
        item = normalize_item(_raw_item())
        assert item.discogs_release_id == 1
        assert item.discogs_artist_id == 100
        assert item.artist_name == "Test Artist"
        assert item.album_title == "Test Album"
        assert item.year == 2020
        assert item.formats == ["Vinyl"]

    def test_year_zero_becomes_none(self) -> None:
        assert normalize_item(_raw_item(year=0)).year is None

    def test_multiple_formats(self) -> None:
        raw = _raw_item(formats=[{"name": "Vinyl", "qty": "1"}, {"name": "LP", "qty": "1"}])
        assert normalize_item(raw).formats == ["Vinyl", "LP"]

    def test_no_artists_uses_sentinels(self) -> None:
        raw = _raw_item()
        raw["basic_information"]["artists"] = []  # type: ignore[index]
        item = normalize_item(raw)
        assert item.discogs_artist_id == 0
        assert item.artist_name == "Unknown"

    def test_string_ids_are_coerced(self) -> None:
        """Discogs occasionally returns numeric IDs as strings."""
        raw = _raw_item(release_id=42, artist_id=7)
        raw["id"] = "42"  # type: ignore[assignment]
        raw["basic_information"]["artists"][0]["id"] = "7"  # type: ignore[index]
        item = normalize_item(raw)
        assert item.discogs_release_id == 42
        assert item.discogs_artist_id == 7


# ── is_vinyl ──────────────────────────────────────────────────────────────────


class TestIsVinyl:
    def _item(self, formats: list[str]) -> DiscogsItem:
        return DiscogsItem(1, 100, "A", "B", 2020, formats)

    def test_vinyl_true(self) -> None:
        assert is_vinyl(self._item(["Vinyl"])) is True

    def test_vinyl_with_other_formats(self) -> None:
        assert is_vinyl(self._item(["Vinyl", "LP"])) is True

    def test_cd_false(self) -> None:
        assert is_vinyl(self._item(["CD"])) is False

    def test_empty_formats_false(self) -> None:
        assert is_vinyl(self._item([])) is False

    def test_case_sensitive(self) -> None:
        """'vinyl' (lowercase) should not match."""
        assert is_vinyl(self._item(["vinyl"])) is False


# ── fetch_collection ──────────────────────────────────────────────────────────


class TestFetchCollection:
    @resp.activate
    def test_single_page(self) -> None:
        resp.add(resp.GET, _COLLECTION_URL, json=_page([_raw_item(1), _raw_item(2)]))

        result = fetch_collection("testuser", "tok123")

        assert len(result) == 2
        assert result[0].discogs_release_id == 1
        assert result[1].discogs_release_id == 2

    @resp.activate
    def test_pagination_fetches_all_pages(self) -> None:
        resp.add(resp.GET, _COLLECTION_URL, json=_page([_raw_item(1)], page=1, pages=2))
        resp.add(resp.GET, _COLLECTION_URL, json=_page([_raw_item(2)], page=2, pages=2))

        result = fetch_collection("testuser", "tok123")

        assert {r.discogs_release_id for r in result} == {1, 2}

    @resp.activate
    def test_vinyl_filter_excludes_non_vinyl(self) -> None:
        vinyl = _raw_item(1, formats=[{"name": "Vinyl", "qty": "1"}])
        cd = _raw_item(2, formats=[{"name": "CD", "qty": "1"}])
        cassette = _raw_item(3, formats=[{"name": "Cassette", "qty": "1"}])
        resp.add(resp.GET, _COLLECTION_URL, json=_page([vinyl, cd, cassette]))

        result = fetch_collection("testuser", "tok123")

        assert len(result) == 1
        assert result[0].discogs_release_id == 1

    @resp.activate
    def test_empty_collection_returns_empty_list(self) -> None:
        resp.add(resp.GET, _COLLECTION_URL, json=_page([]))

        result = fetch_collection("testuser", "tok123")

        assert result == []

    @resp.activate
    def test_rate_limit_retry_on_429(self) -> None:
        """The library's backoff decorator retries on 429; we verify it surfaces the
        final successful response."""
        resp.add(resp.GET, _COLLECTION_URL, status=429)
        resp.add(resp.GET, _COLLECTION_URL, json=_page([_raw_item(1)]))

        # Patch sleep so the test doesn't wait for actual backoff.
        with patch("discogs_client.utils.sleep"):
            result = fetch_collection("testuser", "tok123")

        assert len(result) == 1
        assert result[0].discogs_release_id == 1

    @resp.activate
    def test_mixed_vinyl_across_pages(self) -> None:
        """Vinyl filter is applied per-item across all pages."""
        page1 = _page(
            [
                _raw_item(1, formats=[{"name": "Vinyl", "qty": "1"}]),
                _raw_item(2, formats=[{"name": "CD", "qty": "1"}]),
            ],
            page=1,
            pages=2,
        )
        page2 = _page(
            [
                _raw_item(3, formats=[{"name": "CD", "qty": "1"}]),
                _raw_item(4, formats=[{"name": "Vinyl", "qty": "1"}]),
            ],
            page=2,
            pages=2,
        )
        resp.add(resp.GET, _COLLECTION_URL, json=page1)
        resp.add(resp.GET, _COLLECTION_URL, json=page2)

        result = fetch_collection("testuser", "tok123")

        assert {r.discogs_release_id for r in result} == {1, 4}
