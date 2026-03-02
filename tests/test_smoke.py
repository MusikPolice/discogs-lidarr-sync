"""Smoke tests: verify the package and all modules are importable.

These run in every phase and catch import errors, missing dependencies,
and obvious syntax mistakes before the real tests are added.
"""

import discogs_lidarr_sync
import discogs_lidarr_sync.cli
import discogs_lidarr_sync.config
import discogs_lidarr_sync.discogs
import discogs_lidarr_sync.lidarr
import discogs_lidarr_sync.mbz
import discogs_lidarr_sync.models
import discogs_lidarr_sync.sync


def test_package_importable() -> None:
    """The top-level package must be importable."""
    assert discogs_lidarr_sync is not None


def test_all_modules_importable() -> None:
    """Every module stub must be importable without errors."""
    modules = [
        discogs_lidarr_sync.cli,
        discogs_lidarr_sync.config,
        discogs_lidarr_sync.discogs,
        discogs_lidarr_sync.lidarr,
        discogs_lidarr_sync.mbz,
        discogs_lidarr_sync.models,
        discogs_lidarr_sync.sync,
    ]
    for mod in modules:
        assert mod is not None
