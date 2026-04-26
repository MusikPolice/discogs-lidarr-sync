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
class LidarrSettings:
    """Minimal settings needed to connect to Lidarr (no Discogs or add-options required).

    Used by commands (e.g. ``profiles``) that only need to talk to Lidarr.
    """

    lidarr_url: str
    lidarr_api_key: str


@dataclass
class Settings(LidarrSettings):
    """All configuration values needed for a sync run."""

    discogs_token: str
    discogs_username: str
    lidarr_root_folder: str
    lidarr_quality_profile_id: int
    lidarr_metadata_profile_id: int
    mbz_cache_path: str = ".cache/mbz_cache.json"


def load_lidarr_settings(env_file: str = ".env") -> LidarrSettings:
    """Load only the Lidarr connection settings from environment variables / .env file.

    Only LIDARR_URL and LIDARR_API_KEY are required.  Intended for commands
    (e.g. ``profiles``) that don't need Discogs credentials or add-options.

    Raises:
        ConfigError: if LIDARR_URL or LIDARR_API_KEY is missing/empty.
    """
    load_dotenv(env_file)

    missing: list[str] = []

    def _require(key: str) -> str:
        val = os.getenv(key, "").strip()
        if not val:
            missing.append(key)
        return val

    lidarr_url = _require("LIDARR_URL")
    lidarr_api_key = _require("LIDARR_API_KEY")

    if missing:
        names = ", ".join(missing)
        raise ConfigError(
            f"Missing required environment variable(s): {names}\n"
            f"Copy .env.example to .env and fill in the missing values."
        )

    return LidarrSettings(lidarr_url=lidarr_url, lidarr_api_key=lidarr_api_key)


@dataclass
class SpotifySettings:
    """Settings needed to run the Spotify sync command."""

    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    spotify_playlist_name: str = "Vinyl Collection"
    spotify_token_cache_path: str = ".cache/spotify_token"
    spotify_search_cache_path: str = ".cache/spotify_cache.json"
    spotify_playlist_id: str = ""
    discogs_token: str = ""
    discogs_username: str = ""


def load_spotify_settings(env_file: str = ".env") -> SpotifySettings:
    """Load settings needed for the spotify-sync command.

    Requires SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, DISCOGS_TOKEN,
    and DISCOGS_USERNAME.  All other values have defaults.

    Raises:
        ConfigError: if any required variable is missing/empty.
    """
    load_dotenv(env_file)

    missing: list[str] = []

    def _require(key: str) -> str:
        val = os.getenv(key, "").strip()
        if not val:
            missing.append(key)
        return val

    spotify_client_id = _require("SPOTIFY_CLIENT_ID")
    spotify_client_secret = _require("SPOTIFY_CLIENT_SECRET")
    discogs_token = _require("DISCOGS_TOKEN")
    discogs_username = _require("DISCOGS_USERNAME")

    if missing:
        names = ", ".join(missing)
        raise ConfigError(
            f"Missing required environment variable(s): {names}\n"
            f"Copy .env.example to .env and fill in the missing values."
        )

    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback").strip()
    playlist_name = os.getenv("SPOTIFY_PLAYLIST_NAME", "Vinyl Collection").strip()
    token_cache = os.getenv("SPOTIFY_TOKEN_CACHE_PATH", ".cache/spotify_token").strip()
    search_cache = os.getenv("SPOTIFY_SEARCH_CACHE_PATH", ".cache/spotify_cache.json").strip()
    playlist_id = os.getenv("SPOTIFY_PLAYLIST_ID", "").strip()

    return SpotifySettings(
        spotify_client_id=spotify_client_id,
        spotify_client_secret=spotify_client_secret,
        spotify_redirect_uri=redirect_uri,
        spotify_playlist_name=playlist_name,
        spotify_token_cache_path=token_cache,
        spotify_search_cache_path=search_cache,
        spotify_playlist_id=playlist_id,
        discogs_token=discogs_token,
        discogs_username=discogs_username,
    )


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
