"""Shared pytest fixtures and configuration."""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

# Load .env so that DISCOGS_TOKEN / DISCOGS_USERNAME are available when running
# pytest locally.  In CI these come from environment variables directly.
load_dotenv()

# ---------------------------------------------------------------------------
# VCR / pytest-recording configuration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def vcr_config() -> dict[str, object]:
    """Global vcrpy config applied to all @pytest.mark.vcr tests.

    - Scrubs the Discogs auth token from recorded cassettes so they are safe
      to commit (the header is replaced with a placeholder).
    - record_mode="none": cassettes are only replayed, never recorded, unless
      the caller passes --vcr-record=all on the CLI.
    """
    return {
        # Scrub the token from headers (OAuth-style auth)
        "filter_headers": [("Authorization", "Discogs token=REDACTED")],
        # Strip the token from URL query params (user_token-style auth used by
        # discogs_client — the library appends ?token=... to every request URL).
        # Using a bare string (not a tuple) removes the param entirely; vcrpy
        # also strips it from incoming requests before matching, so cassettes
        # replay correctly regardless of what token value the caller provides.
        "filter_query_parameters": ["token"],
    }


@pytest.fixture(scope="module")
def vcr_cassette_dir(request: pytest.FixtureRequest) -> str:
    """Store all cassettes under tests/cassettes/ regardless of test file location."""
    return os.path.join(os.path.dirname(__file__), "cassettes")


@pytest.fixture(autouse=True)
def skip_if_cassette_missing(request: pytest.FixtureRequest) -> None:
    """Skip any @pytest.mark.vcr test whose cassette file is not present.

    Some cassettes (e.g. large Lidarr library dumps) are excluded from the
    repository via .gitignore.  Without this guard, vcrpy raises
    CannotOverwriteExistingCassetteException in CI because it cannot replay
    a cassette that does not exist and is not allowed to record a new one.
    Re-record missing cassettes locally with --record-mode=all.
    """
    if request.node.get_closest_marker("vcr") is None:
        return
    cassette_dir = os.path.join(os.path.dirname(__file__), "cassettes")
    cassette_path = os.path.join(cassette_dir, request.node.name + ".yaml")
    if not os.path.exists(cassette_path):
        pytest.skip(
            f"Cassette not found: {request.node.name}.yaml — re-record with --record-mode=all"
        )


# ---------------------------------------------------------------------------
# Credentials fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def discogs_vcr_credentials() -> dict[str, str]:
    """Credentials for VCR cassette tests.

    Only DISCOGS_USERNAME is required — the token falls back to a placeholder
    because vcrpy intercepts HTTP calls before they reach the network, so the
    actual token value is irrelevant during cassette replay.

    Set DISCOGS_USERNAME as a repository variable in GitHub Actions (it is not
    sensitive — it is the public Discogs username). Leave DISCOGS_TOKEN unset
    in CI.

    To record cassettes locally:
        uv run pytest tests/test_discogs_recorded.py --record-mode=all
    """
    username = os.getenv("DISCOGS_USERNAME", "").strip()
    if not username:
        pytest.skip("DISCOGS_USERNAME required for VCR cassette tests (token not needed)")
    token = os.getenv("DISCOGS_TOKEN", "REDACTED").strip()
    return {"username": username, "token": token}


@pytest.fixture
def discogs_credentials() -> dict[str, str]:
    """Full Discogs credentials for live integration tests.

    Both DISCOGS_TOKEN and DISCOGS_USERNAME must be set in .env.
    Integration tests are always skipped in CI.
    """
    token = os.getenv("DISCOGS_TOKEN", "").strip()
    username = os.getenv("DISCOGS_USERNAME", "").strip()
    if not token or not username:
        pytest.skip("Set DISCOGS_TOKEN and DISCOGS_USERNAME in .env to run integration tests")
    return {"token": token, "username": username}


# ---------------------------------------------------------------------------
# Lidarr credentials fixtures
# ---------------------------------------------------------------------------

_LIDARR_REPLAY_URL = "http://localhost:8686"


@pytest.fixture
def lidarr_vcr_credentials() -> dict[str, str]:
    """Credentials for Lidarr VCR cassette tests.

    - When LIDARR_URL is set (recording locally): the client uses the real
      URL so vcrpy can make live HTTP calls. before_record_request in the
      test's vcr_config normalises the URL to localhost:8686 in the cassette.
    - When LIDARR_URL is unset (CI replay): the client uses localhost:8686,
      before_playback_request normalises outgoing requests to the same URL,
      and the cassette intercepts them. No credentials required; never skips.

    To record cassettes:
        uv run pytest tests/test_lidarr_recorded.py --record-mode=all
    (requires LIDARR_URL and LIDARR_API_KEY in .env)
    """
    url = os.getenv("LIDARR_URL", "").strip() or _LIDARR_REPLAY_URL
    api_key = os.getenv("LIDARR_API_KEY", "REDACTED").strip()
    return {"url": url, "api_key": api_key}


@pytest.fixture
def lidarr_credentials() -> dict[str, str]:
    """Full Lidarr credentials for live integration tests.

    Both LIDARR_URL and LIDARR_API_KEY must be set in .env.
    Integration tests are always skipped in CI.
    """
    url = os.getenv("LIDARR_URL", "").strip()
    api_key = os.getenv("LIDARR_API_KEY", "").strip()
    if not url or not api_key:
        pytest.skip("Set LIDARR_URL and LIDARR_API_KEY in .env to run Lidarr integration tests")
    return {"url": url, "api_key": api_key}
