"""VCR cassette tests for discogs.py.

These tests replay pre-recorded HTTP interactions stored in tests/cassettes/.
They verify that normalize_item() correctly handles the real Discogs API
response shape — without requiring credentials in CI.

To record (or re-record) cassettes:
    uv run pytest tests/test_discogs_recorded.py --record-mode=all
Requires DISCOGS_TOKEN and DISCOGS_USERNAME to be set in .env.

Cassettes are committed to the repository.  The auth token is scrubbed from
the cassette before saving (replaced with 'Discogs token=REDACTED').
"""

from __future__ import annotations

import discogs_client
import pytest

from discogs_lidarr_sync.discogs import is_vinyl, normalize_item

_DISCOGS_BASE = "https://api.discogs.com"
_CASSETTE_PER_PAGE = 10  # small page so cassettes stay compact


def _assert_discogs_item_schema(item: object) -> None:
    """Assert that a DiscogsItem has the expected field types."""
    from discogs_lidarr_sync.models import DiscogsItem

    assert isinstance(item, DiscogsItem)
    assert isinstance(item.discogs_release_id, int) and item.discogs_release_id > 0
    assert isinstance(item.discogs_artist_id, int)   # 0 is valid for Various Artists
    assert isinstance(item.artist_name, str) and item.artist_name
    assert isinstance(item.album_title, str) and item.album_title
    assert item.year is None or (isinstance(item.year, int) and item.year > 0)
    assert isinstance(item.formats, list)
    assert all(isinstance(f, str) for f in item.formats)


@pytest.mark.vcr
def test_real_collection_page_normalizes_correctly(
    discogs_vcr_credentials: dict[str, str],
) -> None:
    """normalize_item() produces well-typed DiscogsItems from real API data.

    Fetches the first page of the user's collection (10 items) via a recorded
    cassette and verifies every field of every item meets the expected schema.
    Also exercises is_vinyl() to confirm it returns a bool on real data.
    """
    client = discogs_client.Client(
        "discogs-lidarr-sync/test", user_token=discogs_vcr_credentials["token"]
    )
    url = (
        f"{_DISCOGS_BASE}/users/{discogs_vcr_credentials['username']}"
        f"/collection/folders/0/releases?page=1&per_page={_CASSETTE_PER_PAGE}"
    )
    data = client._get(url)

    assert data.get("releases"), (
        "Cassette contains no releases — re-record with a non-empty collection"
    )

    for raw in data["releases"]:
        item = normalize_item(raw)
        _assert_discogs_item_schema(item)
        # is_vinyl must not raise and must return a bool
        assert isinstance(is_vinyl(item), bool)
