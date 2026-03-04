"""Shared data models for discogs-lidarr-sync."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


@dataclass
class DiscogsItem:
    """A single vinyl entry from the user's Discogs collection."""

    discogs_release_id: int
    discogs_artist_id: int
    artist_name: str
    album_title: str
    year: int | None
    formats: list[str]  # e.g. ["Vinyl", "LP", "Album"]


@dataclass
class MbzIds:
    """Result of a MusicBrainz ID lookup for a single DiscogsItem."""

    discogs_release_id: int
    artist_mbid: str | None
    release_group_mbid: str | None
    resolved_at: datetime
    # "resolved" | "partial" (artist only) | "failed"
    status: str
    error: str | None = None


class SyncAction(StrEnum):
    """The outcome action recorded for a single DiscogsItem during a sync run."""

    ADDED_ARTIST = "added_artist"
    ADDED_ALBUM = "added_album"
    SKIPPED_EXISTS = "skipped_exists"
    SKIPPED_UNRESOLVED = "skipped_unresolved"
    SKIPPED_DRY_RUN = "skipped_dry_run"
    ERROR = "error"


@dataclass
class SyncResult:
    """Outcome of processing a single DiscogsItem."""

    item: DiscogsItem
    mbz_ids: MbzIds | None
    action: SyncAction
    error: str | None = None


@dataclass
class RunReport:
    """Aggregate summary of a single sync run."""

    run_at: datetime
    dry_run: bool
    total_vinyl: int
    artists_added: int
    albums_added: int
    skipped_exists: int
    skipped_unresolved: int
    errors: int
    results: list[SyncResult] = field(default_factory=list)
    # Post-sync coverage snapshot (filled in by the CLI after apply_diff)
    coverage_monitored: int = 0   # Discogs albums currently monitored in Lidarr
    coverage_on_disk: int = 0     # of monitored, have at least one file on disk
    coverage_wanted: int = 0      # of monitored, no files yet (queued for download)
