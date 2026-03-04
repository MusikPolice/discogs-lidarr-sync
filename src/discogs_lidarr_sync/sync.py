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

import json
from datetime import UTC, datetime
from pathlib import Path

from pyarr import LidarrAPI

from discogs_lidarr_sync.config import Settings
from discogs_lidarr_sync.lidarr import LidarrError, add_album, add_artist, get_all_artist_mbids
from discogs_lidarr_sync.mbz import MbzCache, resolve
from discogs_lidarr_sync.models import DiscogsItem, RunReport, SyncAction, SyncResult


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
    to_add: list[SyncResult] = []
    to_skip: list[SyncResult] = []

    for item in discogs_items:
        mbz = resolve(item, cache)

        if not mbz.release_group_mbid:
            # No Release Group MBID — can't add to Lidarr.
            to_skip.append(
                SyncResult(item=item, mbz_ids=mbz, action=SyncAction.SKIPPED_UNRESOLVED)
            )
        elif mbz.release_group_mbid in album_mbids:
            to_skip.append(
                SyncResult(item=item, mbz_ids=mbz, action=SyncAction.SKIPPED_EXISTS)
            )
        else:
            to_add.append(
                SyncResult(item=item, mbz_ids=mbz, action=SyncAction.ADDED_ALBUM)
            )

    return to_add, to_skip


def apply_diff(
    to_add: list[SyncResult],
    lidarr_client: LidarrAPI,
    settings: Settings,
    dry_run: bool,
) -> RunReport:
    """Add artists and albums to Lidarr.

    Uses a two-pass approach: all missing artists are added first (each call to
    add_artist blocks until the artist is confirmed visible in Lidarr's search),
    then albums are added in a second pass.

    In dry_run mode actions are logged but no API calls are made.
    Per-item errors are caught and recorded without aborting the run.
    """
    if dry_run:
        for sr in to_add:
            sr.action = SyncAction.SKIPPED_DRY_RUN
        return RunReport(
            run_at=datetime.now(UTC),
            dry_run=True,
            total_vinyl=len(to_add),
            artists_added=0,
            albums_added=0,
            skipped_exists=0,
            skipped_unresolved=0,
            errors=0,
            results=list(to_add),
        )

    existing_artist_mbids = get_all_artist_mbids(lidarr_client)

    artists_added = 0
    albums_added = 0
    errors = 0
    failed_artist_mbids: dict[str, str] = {}  # mbid → error message

    # ── Pass 1: Add all missing artists ──────────────────────────────────────
    for sr in to_add:
        assert sr.mbz_ids is not None
        artist_mbid = sr.mbz_ids.artist_mbid
        if (
            not artist_mbid
            or artist_mbid in existing_artist_mbids
            or artist_mbid in failed_artist_mbids
        ):
            continue
        try:
            add_artist(lidarr_client, artist_mbid, sr.item.artist_name, settings)
            existing_artist_mbids.add(artist_mbid)
            artists_added += 1
        except LidarrError as exc:
            failed_artist_mbids[artist_mbid] = str(exc)

    # ── Pass 2: Add all albums ────────────────────────────────────────────────
    for sr in to_add:
        assert sr.mbz_ids is not None
        artist_mbid = sr.mbz_ids.artist_mbid
        release_group_mbid = sr.mbz_ids.release_group_mbid
        assert release_group_mbid is not None  # compute_diff guarantees this

        if artist_mbid and artist_mbid in failed_artist_mbids:
            sr.action = SyncAction.ERROR
            sr.error = failed_artist_mbids[artist_mbid]
            errors += 1
            continue

        try:
            add_album(lidarr_client, release_group_mbid, artist_mbid or "", settings)
            albums_added += 1
            sr.action = SyncAction.ADDED_ALBUM
        except LidarrError as exc:
            sr.action = SyncAction.ERROR
            sr.error = str(exc)
            errors += 1

    return RunReport(
        run_at=datetime.now(UTC),
        dry_run=False,
        total_vinyl=len(to_add),
        artists_added=artists_added,
        albums_added=albums_added,
        skipped_exists=0,
        skipped_unresolved=0,
        errors=errors,
        results=list(to_add),
    )


def write_report(report: RunReport, output_dir: Path) -> None:
    """Write a timestamped JSON report of the sync run to *output_dir*."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"sync_{report.run_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    data = {
        "run_at": report.run_at.isoformat(),
        "dry_run": report.dry_run,
        "total_vinyl": report.total_vinyl,
        "artists_added": report.artists_added,
        "albums_added": report.albums_added,
        "skipped_exists": report.skipped_exists,
        "skipped_unresolved": report.skipped_unresolved,
        "errors": report.errors,
        "results": [
            {
                "discogs_release_id": r.item.discogs_release_id,
                "artist": r.item.artist_name,
                "album": r.item.album_title,
                "action": r.action,
                "error": r.error,
                "artist_mbid": r.mbz_ids.artist_mbid if r.mbz_ids else None,
                "release_group_mbid": r.mbz_ids.release_group_mbid if r.mbz_ids else None,
            }
            for r in report.results
        ],
    }
    with open(output_dir / filename, "w") as f:
        json.dump(data, f, indent=2)


def write_unresolved(unresolved: list[SyncResult], path: Path) -> None:
    """Append items with no MusicBrainz match to *path* (unresolved.log)."""
    if not unresolved:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for sr in unresolved:
            parts = [
                str(sr.item.discogs_release_id),
                sr.item.artist_name,
                sr.item.album_title,
                str(sr.action),
            ]
            if sr.error:
                parts.append(sr.error)
            f.write("\t".join(parts) + "\n")
