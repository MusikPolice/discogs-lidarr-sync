"""Configuration loading and validation.

Settings are read from environment variables (populated via a .env file).
A ConfigError is raised at startup if any required value is absent or invalid,
so the script fails fast with a helpful message before making any API calls.

Typical usage
-------------
    from discogs_lidarr_sync.config import load_settings, ConfigError

    try:
        settings = load_settings()
    except ConfigError as exc:
        sys.exit(str(exc))
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


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


def load_settings(env_file: str = ".env") -> Settings:
    """Load and validate settings from environment variables / .env file.

    Reads from *env_file* first (via python-dotenv), then from the current
    environment. Environment variables already set in the shell take
    precedence over values in the .env file (dotenv's default behaviour).

    The *env_file* parameter is exposed primarily for testing — pass the path
    to a non-existent file to prevent any .env from being loaded.

    Raises:
        ConfigError: if any required variable is missing/empty, or if an
            integer-typed variable cannot be parsed.
    """
    load_dotenv(env_file)

    missing: list[str] = []

    def _require(key: str) -> str:
        val = os.getenv(key, "").strip()
        if not val:
            missing.append(key)
        return val

    discogs_token = _require("DISCOGS_TOKEN")
    discogs_username = _require("DISCOGS_USERNAME")
    lidarr_url = _require("LIDARR_URL")
    lidarr_api_key = _require("LIDARR_API_KEY")
    lidarr_root_folder = _require("LIDARR_ROOT_FOLDER")
    quality_profile_raw = _require("LIDARR_QUALITY_PROFILE_ID")
    metadata_profile_raw = _require("LIDARR_METADATA_PROFILE_ID")
    mbz_cache_path = os.getenv("MBZ_CACHE_PATH", ".cache/mbz_cache.json").strip()

    if missing:
        names = ", ".join(missing)
        raise ConfigError(
            f"Missing required environment variable(s): {names}\n"
            f"Copy .env.example to .env and fill in the missing values."
        )

    try:
        quality_profile_id = int(quality_profile_raw)
    except ValueError:
        raise ConfigError(
            f"LIDARR_QUALITY_PROFILE_ID must be an integer, got: {quality_profile_raw!r}"
        ) from None

    try:
        metadata_profile_id = int(metadata_profile_raw)
    except ValueError:
        raise ConfigError(
            f"LIDARR_METADATA_PROFILE_ID must be an integer, got: {metadata_profile_raw!r}"
        ) from None

    return Settings(
        discogs_token=discogs_token,
        discogs_username=discogs_username,
        lidarr_url=lidarr_url,
        lidarr_api_key=lidarr_api_key,
        lidarr_root_folder=lidarr_root_folder,
        lidarr_quality_profile_id=quality_profile_id,
        lidarr_metadata_profile_id=metadata_profile_id,
        mbz_cache_path=mbz_cache_path,
    )
