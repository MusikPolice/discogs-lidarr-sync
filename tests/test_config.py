"""Tests for config.py."""

from __future__ import annotations

import pytest

from discogs_lidarr_sync.config import ConfigError, Settings, load_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All required environment variable names.
REQUIRED = [
    "DISCOGS_TOKEN",
    "DISCOGS_USERNAME",
    "LIDARR_URL",
    "LIDARR_API_KEY",
    "LIDARR_ROOT_FOLDER",
    "LIDARR_QUALITY_PROFILE_ID",
    "LIDARR_METADATA_PROFILE_ID",
]

# A complete set of valid env-var values used across multiple tests.
VALID_ENV: dict[str, str] = {
    "DISCOGS_TOKEN": "tok_abc123",
    "DISCOGS_USERNAME": "vinyl_fan",
    "LIDARR_URL": "http://localhost:8686",
    "LIDARR_API_KEY": "key_xyz789",
    "LIDARR_ROOT_FOLDER": "/music",
    "LIDARR_QUALITY_PROFILE_ID": "1",
    "LIDARR_METADATA_PROFILE_ID": "2",
}


def _set_valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate os.environ with all required variables."""
    for key, val in VALID_ENV.items():
        monkeypatch.setenv(key, val)


def _no_env_file(tmp_path: pytest.TempPathFactory) -> str:
    """Return a path that does not exist, preventing any .env from loading."""
    return str(tmp_path / "nonexistent.env")  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Valid configuration
# ---------------------------------------------------------------------------


def test_load_settings_returns_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    _set_valid_env(monkeypatch)
    settings = load_settings(env_file=_no_env_file(tmp_path))
    assert isinstance(settings, Settings)


def test_load_settings_field_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    _set_valid_env(monkeypatch)
    s = load_settings(env_file=_no_env_file(tmp_path))
    assert s.discogs_token == "tok_abc123"
    assert s.discogs_username == "vinyl_fan"
    assert s.lidarr_url == "http://localhost:8686"
    assert s.lidarr_api_key == "key_xyz789"
    assert s.lidarr_root_folder == "/music"
    assert s.lidarr_quality_profile_id == 1
    assert s.lidarr_metadata_profile_id == 2


def test_load_settings_default_cache_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """MBZ_CACHE_PATH should default to .cache/mbz_cache.json if unset."""
    _set_valid_env(monkeypatch)
    monkeypatch.delenv("MBZ_CACHE_PATH", raising=False)
    s = load_settings(env_file=_no_env_file(tmp_path))
    assert s.mbz_cache_path == ".cache/mbz_cache.json"


def test_load_settings_custom_cache_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("MBZ_CACHE_PATH", "/tmp/my_cache.json")
    s = load_settings(env_file=_no_env_file(tmp_path))
    assert s.mbz_cache_path == "/tmp/my_cache.json"


def test_load_settings_strips_whitespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """Values with surrounding whitespace should be stripped."""
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("DISCOGS_USERNAME", "  vinyl_fan  ")
    s = load_settings(env_file=_no_env_file(tmp_path))
    assert s.discogs_username == "vinyl_fan"


# ---------------------------------------------------------------------------
# Missing required variables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_key", REQUIRED)
def test_missing_single_required_var(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
    missing_key: str,
) -> None:
    """Omitting any single required variable must raise ConfigError."""
    _set_valid_env(monkeypatch)
    monkeypatch.delenv(missing_key, raising=False)
    with pytest.raises(ConfigError, match=missing_key):
        load_settings(env_file=_no_env_file(tmp_path))


def test_missing_multiple_vars_lists_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """When several variables are absent, the error must mention all of them."""
    _set_valid_env(monkeypatch)
    monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
    monkeypatch.delenv("LIDARR_API_KEY", raising=False)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=_no_env_file(tmp_path))
    message = str(exc_info.value)
    assert "DISCOGS_TOKEN" in message
    assert "LIDARR_API_KEY" in message


def test_empty_string_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """A variable set to an empty string is treated as missing."""
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("DISCOGS_TOKEN", "")
    with pytest.raises(ConfigError, match="DISCOGS_TOKEN"):
        load_settings(env_file=_no_env_file(tmp_path))


def test_whitespace_only_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """A variable set to only whitespace is treated as missing."""
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("DISCOGS_TOKEN", "   ")
    with pytest.raises(ConfigError, match="DISCOGS_TOKEN"):
        load_settings(env_file=_no_env_file(tmp_path))


# ---------------------------------------------------------------------------
# Integer parsing
# ---------------------------------------------------------------------------


def test_non_integer_quality_profile_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "not_a_number")
    with pytest.raises(ConfigError, match="LIDARR_QUALITY_PROFILE_ID"):
        load_settings(env_file=_no_env_file(tmp_path))


def test_non_integer_metadata_profile_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("LIDARR_METADATA_PROFILE_ID", "abc")
    with pytest.raises(ConfigError, match="LIDARR_METADATA_PROFILE_ID"):
        load_settings(env_file=_no_env_file(tmp_path))


def test_integer_profile_ids_are_converted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """String integers must be converted to int in the returned Settings."""
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("LIDARR_QUALITY_PROFILE_ID", "42")
    monkeypatch.setenv("LIDARR_METADATA_PROFILE_ID", "7")
    s = load_settings(env_file=_no_env_file(tmp_path))
    assert s.lidarr_quality_profile_id == 42
    assert s.lidarr_metadata_profile_id == 7
