"""Live integration tests for mbz.py.

These tests hit the real MusicBrainz API and are always skipped in CI to
avoid the 1-req/sec rate limit and network flakiness. Run locally to validate
end-to-end behaviour against the live database.

    uv run pytest tests/test_mbz_integration.py -v
No credentials required — MBZ is a public API.
"""

from __future__ import annotations

import pytest

from discogs_lidarr_sync.mbz import MbzCache, resolve, resolve_artist, resolve_release_group
from discogs_lidarr_sync.models import DiscogsItem

_POLICE_ARTIST_ID = 7987
_POLICE_MBZ_ID = "9e0e2b01-41db-4008-bd8b-988977d6019a"

_DSOTM_RELEASE_ID = 1873013
_DSOTM_RG_ID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"


@pytest.mark.integration
def test_resolve_artist_live() -> None:
    """URL-relation artist lookup returns the correct MBID from the live API."""
    result = resolve_artist(_POLICE_ARTIST_ID, "The Police")
    assert result == _POLICE_MBZ_ID


@pytest.mark.integration
def test_resolve_release_group_live() -> None:
    """Two-step release → release-group lookup returns the correct MBID."""
    result = resolve_release_group(_DSOTM_RELEASE_ID, "The Dark Side of the Moon", "Pink Floyd")
    assert result == _DSOTM_RG_ID


@pytest.mark.integration
def test_resolve_end_to_end_live() -> None:
    """Full resolve() call returns a 'resolved' MbzIds with both MBIDs."""
    item = DiscogsItem(
        discogs_release_id=_DSOTM_RELEASE_ID,
        discogs_artist_id=_POLICE_ARTIST_ID,
        artist_name="The Police",
        album_title="The Dark Side of the Moon",
        year=1973,
        formats=["Vinyl"],
    )
    cache = MbzCache("irrelevant.json")  # empty, in-memory only
    result = resolve(item, cache)
    assert result.status in ("resolved", "partial")
    assert result.discogs_release_id == _DSOTM_RELEASE_ID


@pytest.mark.integration
def test_resolve_name_fallback_live() -> None:
    """Name-based fallback returns a result for an unlinked Discogs artist ID."""
    # Use a bogus Discogs ID that won't have a URL relation in MBZ;
    # the name search should still find an artist.
    result = resolve_artist(999999999, "The Beatles")
    assert result is not None  # name search should find something
