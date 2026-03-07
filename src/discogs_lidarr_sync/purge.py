"""Purge command logic for discogs-lidarr-sync.

Two purge modes:

apply_purge()       — CSV-driven: reads an audit CSV, deletes albums marked
                      action=delete, then removes artists with no remaining
                      monitored albums.

apply_ghost_purge() — Auto-discovery: finds all unmonitored albums with no
                      files on disk and deletes them without requiring a CSV,
                      then removes artists with no remaining auditable content.
"""

from __future__ import annotations

import csv
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pyarr import LidarrAPI

from discogs_lidarr_sync.lidarr import (
    LidarrError,
    LidarrNotFoundError,
    delete_album,
    delete_artist,
    get_auditable_album_count_for_artist,
    get_ghost_albums,
    get_monitored_album_count_for_artist,
)
from discogs_lidarr_sync.models import GhostPurgeReport, PurgeReport, PurgeRow

_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {"action", "lidarr_album_id", "lidarr_artist_id"}
)


def read_purge_csv(path: Path) -> list[PurgeRow]:
    """Read an audit CSV and return rows parsed into PurgeRow objects.

    Validates that required columns are present.  Rows with a missing or
    non-integer lidarr_album_id or lidarr_artist_id are skipped with a
    UserWarning.  action values are stripped of whitespace and lowercased
    before storage; blank action cells are treated as "keep".
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV file is empty or has no header: {path}")
        fieldnames = set(reader.fieldnames)
        missing = _REQUIRED_COLUMNS - fieldnames
        if missing:
            raise ValueError(
                f"CSV is missing required columns: {', '.join(sorted(missing))}"
            )

        rows: list[PurgeRow] = []
        for i, raw in enumerate(reader, start=2):  # row 1 is the header
            action = (raw.get("action") or "").strip().lower() or "keep"
            artist_name = raw.get("artist_name") or ""
            album_title = raw.get("album_title") or ""

            album_id_raw = (raw.get("lidarr_album_id") or "").strip()
            artist_id_raw = (raw.get("lidarr_artist_id") or "").strip()

            try:
                lidarr_album_id = int(album_id_raw)
            except ValueError:
                warnings.warn(
                    f"Row {i}: lidarr_album_id {album_id_raw!r} is not a valid integer"
                    " — row skipped",
                    UserWarning,
                    stacklevel=2,
                )
                continue

            try:
                lidarr_artist_id = int(artist_id_raw)
            except ValueError:
                warnings.warn(
                    f"Row {i}: lidarr_artist_id {artist_id_raw!r} is not a valid integer"
                    " — row skipped",
                    UserWarning,
                    stacklevel=2,
                )
                continue

            rows.append(
                PurgeRow(
                    action=action,
                    artist_name=artist_name,
                    album_title=album_title,
                    lidarr_album_id=lidarr_album_id,
                    lidarr_artist_id=lidarr_artist_id,
                )
            )

    return rows


def compute_purge(rows: list[PurgeRow]) -> tuple[list[PurgeRow], list[PurgeRow]]:
    """Split rows into (to_delete, to_skip) based on the action column."""
    to_delete = [r for r in rows if r.action == "delete"]
    to_skip = [r for r in rows if r.action != "delete"]
    return to_delete, to_skip


def apply_purge(
    to_delete: list[PurgeRow],
    client: LidarrAPI,
    dry_run: bool,
    delete_files: bool = False,
    log: Callable[[str], None] | None = None,
) -> PurgeReport:
    """Delete albums (and orphaned artists) from Lidarr.

    Pass 1: for each row, attempt to delete the album.  404 responses are
            counted as "already gone" (not an error).  Other failures are
            counted as errors and the run continues.
    Pass 2: for each distinct artist_id touched by a successful deletion,
            check whether any monitored albums remain.  If none remain,
            delete the artist too.

    delete_files is forwarded to both album and artist deletion calls.
    In dry_run mode no API calls are made and all counts other than
    to_delete remain zero.
    """
    report = PurgeReport(
        dry_run=dry_run,
        total_rows=len(to_delete),  # CLI updates this to include skipped rows
        to_delete=len(to_delete),
        skipped_keep=0,  # CLI fills this in from compute_purge output
        already_gone=0,
        albums_deleted=0,
        artists_deleted=0,
        errors=0,
    )

    if dry_run:
        return report

    # ── Pass 1: delete albums ─────────────────────────────────────────────────
    touched_artist_ids: set[int] = set()
    # Build a name map so Pass 2 can log artist deletions by name.
    artist_names: dict[int, str] = {r.lidarr_artist_id: r.artist_name for r in to_delete}
    for row in to_delete:
        try:
            delete_album(client, row.lidarr_album_id, delete_files=delete_files)
            report.albums_deleted += 1
            touched_artist_ids.add(row.lidarr_artist_id)
            if log:
                log(f"  [green]deleted album[/green]   {row.artist_name} — {row.album_title}")
        except LidarrNotFoundError:
            report.already_gone += 1
            if log:
                log(f"  [dim]already gone[/dim]    {row.artist_name} — {row.album_title}")
        except LidarrError as exc:
            report.errors += 1
            report.error_details.append(str(exc))
            if log:
                log(f"  [red]error[/red]           {row.artist_name} — {row.album_title}: {exc}")

    # ── Pass 2: remove orphaned artists ───────────────────────────────────────
    for artist_id in touched_artist_ids:
        try:
            remaining = get_monitored_album_count_for_artist(client, artist_id)
            if remaining == 0:
                delete_artist(client, artist_id, delete_files=delete_files)
                report.artists_deleted += 1
                if log:
                    name = artist_names.get(artist_id, str(artist_id))
                    log(f"  [green]deleted artist[/green]  {name}")
        except LidarrError as exc:
            report.errors += 1
            report.error_details.append(str(exc))

    return report


def apply_ghost_purge(
    client: LidarrAPI,
    dry_run: bool,
    delete_files: bool = False,
    log: Callable[[str], None] | None = None,
) -> GhostPurgeReport:
    """Delete ghost albums (unmonitored, no files) from Lidarr without a CSV.

    Auto-discovers all ghost albums from the current Lidarr state and deletes
    them.  Unlike apply_purge(), no audit CSV is required — every ghost album
    is deleted unless --dry-run is set.

    Pass 1: delete every unmonitored album with trackFileCount == 0.
    Pass 2: for each artist touched by a deletion, check whether any auditable
            albums remain (monitored OR unmonitored-with-files).  If none
            remain, delete the artist.

    delete_files is forwarded to both album and artist deletion calls.
    In dry_run mode no API calls are made.
    """
    try:
        ghost_albums = get_ghost_albums(client)
    except Exception as exc:
        raise LidarrError(f"Failed to fetch albums from Lidarr: {exc}") from exc

    report = GhostPurgeReport(
        dry_run=dry_run,
        ghosts_found=len(ghost_albums),
        already_gone=0,
        albums_deleted=0,
        artists_deleted=0,
        errors=0,
    )

    if dry_run:
        if log:
            for album in ghost_albums:
                artist_name = str((album.get("artist") or {}).get("artistName", ""))
                album_title = str(album.get("title", ""))
                log(f"  [dim]would delete ghost[/dim]  {artist_name} — {album_title}")
        return report

    # ── Pass 1: delete ghost albums ───────────────────────────────────────────
    touched_artist_ids: set[int] = set()
    artist_names: dict[int, str] = {}

    for album in ghost_albums:
        artist: dict[str, Any] = album.get("artist") or {}
        artist_id = int(artist.get("id", 0))
        artist_name = str(artist.get("artistName", ""))
        album_title = str(album.get("title", ""))
        album_id = int(album.get("id", 0))

        artist_names[artist_id] = artist_name

        try:
            delete_album(client, album_id, delete_files=delete_files)
            report.albums_deleted += 1
            touched_artist_ids.add(artist_id)
            if log:
                log(f"  [green]deleted ghost[/green]    {artist_name} — {album_title}")
        except LidarrNotFoundError:
            report.already_gone += 1
            if log:
                log(f"  [dim]already gone[/dim]     {artist_name} — {album_title}")
        except LidarrError as exc:
            report.errors += 1
            report.error_details.append(str(exc))
            if log:
                log(f"  [red]error[/red]            {artist_name} — {album_title}: {exc}")

    # ── Pass 2: remove artists with no remaining auditable content ────────────
    for artist_id in touched_artist_ids:
        try:
            remaining = get_auditable_album_count_for_artist(client, artist_id)
            if remaining == 0:
                delete_artist(client, artist_id, delete_files=delete_files)
                report.artists_deleted += 1
                if log:
                    name = artist_names.get(artist_id, str(artist_id))
                    log(f"  [green]deleted artist[/green]   {name}")
        except LidarrError as exc:
            report.errors += 1
            report.error_details.append(str(exc))

    return report
