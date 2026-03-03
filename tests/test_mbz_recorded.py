"""VCR cassette tests for mbz.py.

These tests replay pre-recorded MusicBrainz API interactions stored in
tests/cassettes/.  MusicBrainz requires no authentication, so cassettes
contain no sensitive data and replay without any credentials — these tests
always run in CI once cassettes are committed.

Reference data used for recordings:
  - The Police     (Discogs artist ID 7987)  → MBZ artist 9e0e2b01-...
  - Dark Side of the Moon (Discogs release ID 1873013) → MBZ RG f5093c06-...

To re-record cassettes:
    uv run pytest tests/test_mbz_recorded.py --record-mode=all
No credentials required.
"""

from __future__ import annotations

import musicbrainzngs
import pytest

from discogs_lidarr_sync.mbz import resolve_artist, resolve_release_group

# Known-good IDs confirmed against the live MBZ database.
_POLICE_DISCOGS_ARTIST_ID = 7987
_POLICE_MBZ_ARTIST_ID = "9e0e2b01-41db-4008-bd8b-988977d6019a"

_DSOTM_DISCOGS_RELEASE_ID = 1873013  # a specific pressing in Discogs
_DSOTM_MBZ_RG_ID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"  # the release group


@pytest.fixture(autouse=True, scope="module")
def disable_rate_limit() -> None:
    """Disable the 1-req/sec rate limit so cassette replay is instant."""
    musicbrainzngs.set_rate_limit(False)


@pytest.mark.vcr
def test_resolve_artist_url_relation() -> None:
    """resolve_artist returns the correct MBID via URL relation lookup."""
    result = resolve_artist(_POLICE_DISCOGS_ARTIST_ID, "The Police")
    assert result == _POLICE_MBZ_ARTIST_ID


@pytest.mark.vcr
def test_resolve_release_group_url_relation() -> None:
    """resolve_release_group returns the correct Release Group MBID.

    Exercises the two-step: browse_urls → release MBID, then
    get_release_by_id → release group MBID.
    """
    result = resolve_release_group(
        _DSOTM_DISCOGS_RELEASE_ID,
        "The Dark Side of the Moon",
        "Pink Floyd",
    )
    assert result == _DSOTM_MBZ_RG_ID
