"""Live integration tests for discogs.py.

These tests hit the real Discogs API and are always skipped in CI.
They catch regressions that cassettes can't: API contract changes, auth
failures, rate-limit behaviour on a real account, etc.

Requires both DISCOGS_TOKEN and DISCOGS_USERNAME set in .env.
Run with:
    uv run pytest tests/test_discogs_integration.py -v
"""

from __future__ import annotations

import pytest

from discogs_lidarr_sync.discogs import fetch_collection, is_vinyl
from discogs_lidarr_sync.models import DiscogsItem


def _assert_schema(item: DiscogsItem) -> None:
    assert isinstance(item.discogs_release_id, int) and item.discogs_release_id > 0
    assert isinstance(item.discogs_artist_id, int)
    assert isinstance(item.artist_name, str) and item.artist_name
    assert isinstance(item.album_title, str) and item.album_title
    assert item.year is None or (isinstance(item.year, int) and item.year > 0)
    assert isinstance(item.formats, list)
    assert all(isinstance(f, str) for f in item.formats)


@pytest.mark.integration
def test_fetch_collection_returns_items(discogs_credentials: dict[str, str]) -> None:
    """Real collection must contain at least one vinyl record."""
    items = fetch_collection(discogs_credentials["username"], discogs_credentials["token"])
    assert len(items) > 0, "Expected at least one vinyl item in the Discogs collection"


@pytest.mark.integration
def test_fetch_collection_schema(discogs_credentials: dict[str, str]) -> None:
    """All returned DiscogsItems have correct field types."""
    items = fetch_collection(discogs_credentials["username"], discogs_credentials["token"])
    for item in items[:5]:  # spot-check first 5 to keep runtime short
        _assert_schema(item)


@pytest.mark.integration
def test_fetch_collection_returns_only_vinyl(discogs_credentials: dict[str, str]) -> None:
    """fetch_collection must not return any non-vinyl items."""
    items = fetch_collection(discogs_credentials["username"], discogs_credentials["token"])
    non_vinyl = [i for i in items if not is_vinyl(i)]
    assert not non_vinyl, (
        f"fetch_collection returned {len(non_vinyl)} non-vinyl items: "
        + ", ".join(f"{i.album_title} ({i.formats})" for i in non_vinyl[:3])
    )
