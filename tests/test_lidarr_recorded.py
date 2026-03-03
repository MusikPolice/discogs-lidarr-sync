"""VCR cassette tests for lidarr.py.

These tests replay pre-recorded Lidarr API interactions stored in
tests/cassettes/.  The X-Api-Key header is scrubbed from cassettes and
the recorded URL is normalised to http://localhost:8686 so that cassettes
are safe to commit and replay in CI without a running Lidarr instance.

To re-record cassettes (requires a running Lidarr at LIDARR_URL):
    uv run pytest tests/test_lidarr_recorded.py --record-mode=all
Credentials must be set in .env: LIDARR_URL, LIDARR_API_KEY.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pyarr import LidarrAPI

from discogs_lidarr_sync.lidarr import get_all_album_mbids, get_all_artist_mbids

_REPLAY_URL = "http://localhost:8686"


def _normalise_url(request: Any) -> Any:
    """Rewrite any Lidarr host:port to localhost:8686 before recording."""
    request.uri = re.sub(r"https?://[^/]+", _REPLAY_URL, request.uri)
    return request


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, object]:
    """Module-level VCR config — overrides the session fixture in conftest.py.

    Scrubs the API key from recorded cassettes and normalises the Lidarr URL:
    - before_record_request: rewrites the real LIDARR_URL → localhost:8686 in
      the cassette so recordings are host-agnostic.
    - before_playback_request: rewrites the outgoing request URL → localhost:8686
      before matching against cassette entries, so replay works whether the
      client was initialised with the real URL or the replay URL.
    """
    return {
        "filter_headers": [("X-Api-Key", "REDACTED")],
        "before_record_request": _normalise_url,
        "before_playback_request": _normalise_url,
    }


@pytest.mark.vcr
def test_get_all_artist_mbids_returns_set(lidarr_vcr_credentials: dict[str, str]) -> None:
    """get_all_artist_mbids returns a set (empty or non-empty) of UUID strings."""
    client = LidarrAPI(lidarr_vcr_credentials["url"], lidarr_vcr_credentials["api_key"])
    result = get_all_artist_mbids(client)
    assert isinstance(result, set)
    # Every element must look like a UUID
    for mbid in result:
        assert len(mbid) == 36, f"Expected UUID, got {mbid!r}"


@pytest.mark.vcr
def test_get_all_album_mbids_returns_set(lidarr_vcr_credentials: dict[str, str]) -> None:
    """get_all_album_mbids returns a set (empty or non-empty) of UUID strings."""
    client = LidarrAPI(lidarr_vcr_credentials["url"], lidarr_vcr_credentials["api_key"])
    result = get_all_album_mbids(client)
    assert isinstance(result, set)
    for mbid in result:
        assert len(mbid) == 36, f"Expected UUID, got {mbid!r}"
