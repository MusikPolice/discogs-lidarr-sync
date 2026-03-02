"""Configuration loading and validation.

Settings are read from environment variables (populated via a .env file).
A ConfigError is raised at startup if any required value is absent, so the
script fails fast with a helpful message before making any API calls.
"""

from __future__ import annotations

from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass
class Settings:
    """All configuration values needed for a sync run."""

    discogs_token: str
    discogs_username: str
    lidarr_url: str
    lidarr_api_key: str
    lidarr_root_folder: str
    lidarr_quality_profile_id: int
    lidarr_metadata_profile_id: int
    mbz_cache_path: str = ".cache/mbz_cache.json"


def load_settings() -> Settings:
    """Load and validate settings from environment variables / .env file.

    Raises:
        ConfigError: if any required value is missing or empty.
    """
    raise NotImplementedError
