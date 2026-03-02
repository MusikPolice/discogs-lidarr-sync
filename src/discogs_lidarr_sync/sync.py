"""Core sync logic: diff, apply, and report.

Orchestrates the full sync pipeline:
  1. compute_diff() — compares the Discogs collection against current Lidarr
     state and resolves MusicBrainz IDs, producing a list of items to add.
  2. apply_diff()   — adds artists then albums to Lidarr in dependency order,
     catching per-item errors without aborting the run.
  3. write_report() — persists a timestamped JSON summary to runs/.
  4. write_unresolved() — appends items with no MBZ match to unresolved.log.
"""

from __future__ import annotations

from pathlib import Path

from pyarr import LidarrAPI

from discogs_lidarr_sync.config import Settings
from discogs_lidarr_sync.mbz import MbzCache
from discogs_lidarr_sync.models import DiscogsItem, RunReport, SyncResult


def compute_diff(
    discogs_items: list[DiscogsItem],
    artist_mbids: set[str],
    album_mbids: set[str],
    cache: MbzCache,
) -> tuple[list[SyncResult], list[SyncResult]]:
    """Compute what needs to be added to Lidarr.

    For each item, resolves MusicBrainz IDs (via cache or network) and checks
    whether the artist and album already exist in Lidarr.

    Returns:
        (to_add, to_skip) where:
        - to_add:  items with resolved MBIDs not yet present in Lidarr.
        - to_skip: items already in Lidarr, or whose MBIDs could not be resolved.
    """
    raise NotImplementedError


def apply_diff(
    to_add: list[SyncResult],
    lidarr_client: LidarrAPI,
    settings: Settings,
    dry_run: bool,
) -> RunReport:
    """Add artists and albums to Lidarr.

    Artists are processed before albums to satisfy the dependency constraint
    (an album cannot be added before its artist exists in Lidarr).

    In dry_run mode actions are logged but no API calls are made.
    Per-item errors are caught and recorded without aborting the run.
    """
    raise NotImplementedError


def write_report(report: RunReport, output_dir: Path) -> None:
    """Write a timestamped JSON report of the sync run to *output_dir*."""
    raise NotImplementedError


def write_unresolved(unresolved: list[SyncResult], path: Path) -> None:
    """Append items with no MusicBrainz match to *path* (unresolved.log)."""
    raise NotImplementedError
