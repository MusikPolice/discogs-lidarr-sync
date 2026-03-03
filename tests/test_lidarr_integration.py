"""Live integration tests for lidarr.py.

These tests hit a real Lidarr instance and are always skipped in CI.
Run locally to validate end-to-end behaviour against a live server.

    uv run pytest tests/test_lidarr_integration.py -v

Requires LIDARR_URL and LIDARR_API_KEY in .env.
"""

from __future__ import annotations

import pytest
from pyarr import LidarrAPI

from discogs_lidarr_sync.lidarr import get_all_album_mbids, get_all_artist_mbids


@pytest.mark.integration
def test_get_all_artist_mbids_returns_set(lidarr_credentials: dict[str, str]) -> None:
    """Returns a set of strings from the live Lidarr artist library."""
    client = LidarrAPI(lidarr_credentials["url"], lidarr_credentials["api_key"])
    result = get_all_artist_mbids(client)
    assert isinstance(result, set)
    for mbid in result:
        assert isinstance(mbid, str)
        assert len(mbid) == 36, f"Expected UUID, got {mbid!r}"


@pytest.mark.integration
def test_get_all_album_mbids_returns_set(lidarr_credentials: dict[str, str]) -> None:
    """Returns a set of strings from the live Lidarr album library."""
    client = LidarrAPI(lidarr_credentials["url"], lidarr_credentials["api_key"])
    result = get_all_album_mbids(client)
    assert isinstance(result, set)
    for mbid in result:
        assert isinstance(mbid, str)
        assert len(mbid) == 36, f"Expected UUID, got {mbid!r}"


@pytest.mark.integration
def test_get_all_artist_mbids_no_duplicates(lidarr_credentials: dict[str, str]) -> None:
    """get_all_artist_mbids returns a set (no duplicates by definition)."""
    client = LidarrAPI(lidarr_credentials["url"], lidarr_credentials["api_key"])
    result = get_all_artist_mbids(client)
    # Since we return a set this is always true; this test validates the type
    assert result == set(result)
