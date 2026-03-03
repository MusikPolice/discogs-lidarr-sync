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
        pytest.skip(
            "Set DISCOGS_TOKEN and DISCOGS_USERNAME in .env to run integration tests"
        )
    return {"token": token, "username": username}
