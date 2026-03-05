"""CLI entry point for discogs-lidarr-sync.

Commands
--------
sync         Fetch the Discogs vinyl collection and sync new albums to Lidarr.
status       Show collection / library sizes without making any changes.
profiles     List Lidarr quality and metadata profiles with their IDs.
clear-cache  Delete the local MusicBrainz lookup cache.

Usage
-----
    discogs-lidarr-sync sync [--dry-run] [--config PATH] [--verbose]
    discogs-lidarr-sync status [--config PATH]
    discogs-lidarr-sync profiles [--config PATH]
    discogs-lidarr-sync clear-cache [--config PATH]
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import click
from pyarr import LidarrAPI
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from discogs_lidarr_sync.audit import compute_audit, write_audit_csv
from discogs_lidarr_sync.config import ConfigError, load_lidarr_settings, load_settings
from discogs_lidarr_sync.discogs import fetch_collection
from discogs_lidarr_sync.lidarr import (
    get_all_album_mbids,
    get_all_artist_mbids,
    get_discogs_album_coverage,
    get_monitored_album_mbids,
    get_monitored_albums_with_stats,
)
from discogs_lidarr_sync.mbz import MbzCache, resolve
from discogs_lidarr_sync.models import RunReport, SyncAction
from discogs_lidarr_sync.sync import apply_diff, compute_diff, write_report, write_unresolved

_console = Console()


# ── Helpers ────────────────────────────────────────────────────────────────────


def _load_or_exit(config: str) -> object:
    """Load settings and exit with a helpful message on failure."""
    try:
        return load_settings(env_file=config)
    except ConfigError as exc:
        _console.print(f"[red bold]Configuration error:[/red bold] {exc}")
        sys.exit(1)


def _print_summary(report: RunReport) -> None:
    """Print a colour-coded summary table to the console."""
    title = "Sync Summary" + (" [dim](dry run)[/dim]" if report.dry_run else "")
    table = Table(title=title, show_header=True)
    table.add_column("", style="bold", min_width=30)
    table.add_column("Count", justify="right", min_width=6)

    def _row(label: str, count: int, positive_style: str = "") -> None:
        style = positive_style if count > 0 else ""
        table.add_row(label, str(count), style=style)

    _row("Artists added", report.artists_added, "green")
    _row("Albums added", report.albums_added, "green")
    _row("Skipped (already in Lidarr)", report.skipped_exists)
    _row("Skipped (unresolvable MBZ ID)", report.skipped_unresolved, "yellow")
    _row("Errors", report.errors, "red")
    table.add_section()
    table.add_row("Total vinyl records", str(report.total_vinyl))
    table.add_section()
    monitored_str = f"{report.coverage_monitored}/{report.total_vinyl}"
    mon_style = "green" if report.coverage_monitored == report.total_vinyl else "yellow"
    table.add_row("Monitored in Lidarr", monitored_str, style=mon_style)
    table.add_row("  On disk", str(report.coverage_on_disk))
    table.add_row("  Wanted", str(report.coverage_wanted))

    _console.print()
    _console.print(table)


# ── Commands ───────────────────────────────────────────────────────────────────


@click.group()
def main() -> None:
    """Sync your Discogs vinyl collection to Lidarr."""


@main.command()
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be added without making any changes.",
)
@click.option(
    "--config",
    default=".env",
    show_default=True,
    help="Path to .env config file.",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show each item processed.")
def sync(dry_run: bool, config: str, verbose: bool) -> None:
    """Fetch the Discogs vinyl collection and sync new albums to Lidarr."""
    settings = _load_or_exit(config)
    from discogs_lidarr_sync.config import Settings  # narrow type after _load_or_exit

    assert isinstance(settings, Settings)

    client = LidarrAPI(settings.lidarr_url, settings.lidarr_api_key)
    cache = MbzCache(settings.mbz_cache_path)
    cache.load()

    # ── Phase 1-3: Fetch data and resolve IDs ─────────────────────────────────
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=_console,
    ) as progress:
        # Fetch Discogs collection
        t1 = progress.add_task("Fetching Discogs collection…", total=None)
        try:
            items = fetch_collection(settings.discogs_username, settings.discogs_token)
        except Exception as exc:
            _console.print(f"[red]Failed to fetch Discogs collection:[/red] {exc}")
            sys.exit(1)
        progress.update(
            t1,
            description=f"[green]✓[/green] Fetched {len(items)} vinyl records",
            total=1,
            completed=1,
        )

        # Read Lidarr state
        t2 = progress.add_task("Reading Lidarr library…", total=None)
        try:
            artist_mbids = get_all_artist_mbids(client)
            album_mbids = get_all_album_mbids(client)
            monitored_album_mbids = get_monitored_album_mbids(client)
        except Exception as exc:
            _console.print(f"[red]Failed to read Lidarr library:[/red] {exc}")
            sys.exit(1)
        progress.update(
            t2,
            description=(
                f"[green]✓[/green] Lidarr: {len(artist_mbids)} artists, {len(album_mbids)} albums"
            ),
            total=1,
            completed=1,
        )

        # Resolve MusicBrainz IDs (the slow step — 1 req/sec rate limit)
        t3 = progress.add_task("Resolving MusicBrainz IDs…", total=len(items))
        for item in items:
            resolve(item, cache)
            progress.advance(t3)
        progress.update(t3, description="[green]✓[/green] MusicBrainz IDs resolved")

    # ── Phase 4: Diff ─────────────────────────────────────────────────────────
    # Use monitored_album_mbids so that albums auto-indexed by Lidarr as
    # unmonitored (when an artist is added with monitor="none") are treated as
    # missing and flow through add_album() → upd_album(monitored=True).
    to_add, to_skip = compute_diff(items, artist_mbids, monitored_album_mbids, cache)

    if verbose:
        for sr in to_skip:
            _console.print(
                f"  [dim]skip[/dim]  {sr.item.artist_name} — {sr.item.album_title}  ({sr.action})"
            )

    # ── Phase 5: Apply ────────────────────────────────────────────────────────
    action_label = "Computing diff" if dry_run else "Syncing to Lidarr"
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_console,
        transient=True,
    ) as p:
        p.add_task(f"{action_label}…", total=None)
        report = apply_diff(to_add, client, settings, dry_run=dry_run)

    # apply_diff only sees to_add; fill in the complete picture.
    report.total_vinyl = len(items)
    report.skipped_exists = sum(1 for sr in to_skip if sr.action == SyncAction.SKIPPED_EXISTS)
    report.skipped_unresolved = sum(
        1 for sr in to_skip if sr.action == SyncAction.SKIPPED_UNRESOLVED
    )

    # Post-sync coverage: how many Discogs albums are monitored/on-disk/wanted.
    all_resolved_mbids: set[str] = {
        sr.mbz_ids.release_group_mbid
        for sr in to_add + to_skip
        if sr.mbz_ids and sr.mbz_ids.release_group_mbid
    }
    monitored, on_disk, wanted = get_discogs_album_coverage(client, all_resolved_mbids)
    report.coverage_monitored = monitored
    report.coverage_on_disk = on_disk
    report.coverage_wanted = wanted

    if verbose:
        for sr in report.results:
            _console.print(
                f"  [green]{sr.action}[/green]  {sr.item.artist_name} — {sr.item.album_title}"
            )

    # ── Phase 6: Persist ──────────────────────────────────────────────────────
    cache.save()
    write_report(report, Path("runs"))
    unresolved = [sr for sr in to_skip if sr.action == SyncAction.SKIPPED_UNRESOLVED]
    if unresolved:
        write_unresolved(unresolved, Path("unresolved.log"))

    _print_summary(report)


@main.command()
@click.option(
    "--config",
    default=".env",
    show_default=True,
    help="Path to .env config file.",
)
def status(config: str) -> None:
    """Show current Discogs collection size and Lidarr library size."""
    settings = _load_or_exit(config)
    from discogs_lidarr_sync.config import Settings

    assert isinstance(settings, Settings)

    client = LidarrAPI(settings.lidarr_url, settings.lidarr_api_key)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=_console,
        transient=True,
    ) as progress:
        t1 = progress.add_task("Fetching Discogs collection…", total=None)
        items = fetch_collection(settings.discogs_username, settings.discogs_token)
        progress.update(t1, total=1, completed=1)

        t2 = progress.add_task("Reading Lidarr library…", total=None)
        artist_mbids = get_all_artist_mbids(client)
        album_mbids = get_all_album_mbids(client)
        progress.update(t2, total=1, completed=1)

    table = Table(title="Current Status", show_header=True)
    table.add_column("Source", style="bold", min_width=25)
    table.add_column("Count", justify="right", min_width=6)
    table.add_row("Discogs vinyl records", str(len(items)))
    table.add_row("Lidarr artists", str(len(artist_mbids)))
    table.add_row("Lidarr albums", str(len(album_mbids)))

    _console.print()
    _console.print(table)


@main.command()
@click.option(
    "--config",
    default=".env",
    show_default=True,
    help="Path to .env config file.",
)
def profiles(config: str) -> None:
    """List Lidarr quality and metadata profiles with their IDs.

    Only LIDARR_URL and LIDARR_API_KEY need to be set to run this command.
    Use the displayed IDs to set LIDARR_QUALITY_PROFILE_ID and
    LIDARR_METADATA_PROFILE_ID in your .env file.
    """
    try:
        ls = load_lidarr_settings(env_file=config)
    except ConfigError as exc:
        _console.print(f"[red bold]Configuration error:[/red bold] {exc}")
        sys.exit(1)

    client = LidarrAPI(ls.lidarr_url, ls.lidarr_api_key)

    try:
        quality_profiles = client.get_quality_profile()
        metadata_profiles = client.get_metadata_profile()
    except Exception as exc:
        _console.print(f"[red]Failed to fetch profiles from Lidarr:[/red] {exc}")
        sys.exit(1)

    q_table = Table(title="Quality Profiles", show_header=True)
    q_table.add_column("ID", justify="right", style="bold cyan", min_width=4)
    q_table.add_column("Name", min_width=20)
    for p in quality_profiles:
        q_table.add_row(str(p["id"]), p["name"])

    m_table = Table(title="Metadata Profiles", show_header=True)
    m_table.add_column("ID", justify="right", style="bold cyan", min_width=4)
    m_table.add_column("Name", min_width=20)
    for p in metadata_profiles:
        m_table.add_row(str(p["id"]), p["name"])

    _console.print()
    _console.print(q_table)
    _console.print()
    _console.print(m_table)
    _console.print()
    _console.print(
        "Set [bold]LIDARR_QUALITY_PROFILE_ID[/bold] and "
        "[bold]LIDARR_METADATA_PROFILE_ID[/bold] in your .env using the IDs above."
    )


@main.command()
@click.option(
    "--output",
    default=None,
    help="Path for the output CSV. Default: audit/audit_{timestamp}.csv",
)
@click.option(
    "--config",
    default=".env",
    show_default=True,
    help="Path to .env config file.",
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show each album evaluated.")
def audit(output: str | None, config: str, verbose: bool) -> None:
    """Find monitored Lidarr albums not present in the Discogs vinyl collection.

    Exports results to a CSV with one row per album.  All rows default to
    action=delete.  Open the CSV in a spreadsheet, change individual rows to
    action=keep for albums you want to retain, then pass it to the future
    ``purge`` command for bulk deletion.
    """
    settings = _load_or_exit(config)
    from discogs_lidarr_sync.config import Settings

    assert isinstance(settings, Settings)

    client = LidarrAPI(settings.lidarr_url, settings.lidarr_api_key)
    cache = MbzCache(settings.mbz_cache_path)
    cache.load()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=_console,
    ) as progress:
        t1 = progress.add_task("Fetching Discogs collection…", total=None)
        try:
            items = fetch_collection(settings.discogs_username, settings.discogs_token)
        except Exception as exc:
            _console.print(f"[red]Failed to fetch Discogs collection:[/red] {exc}")
            sys.exit(1)
        progress.update(
            t1,
            description=f"[green]✓[/green] Fetched {len(items)} vinyl records",
            total=1,
            completed=1,
        )

        t2 = progress.add_task("Reading Lidarr library…", total=None)
        try:
            lidarr_albums = get_monitored_albums_with_stats(client)
        except Exception as exc:
            _console.print(f"[red]Failed to read Lidarr library:[/red] {exc}")
            sys.exit(1)
        progress.update(
            t2,
            description=f"[green]✓[/green] Lidarr: {len(lidarr_albums)} monitored albums",
            total=1,
            completed=1,
        )

        t3 = progress.add_task("Resolving MusicBrainz IDs…", total=len(items))
        for item in items:
            resolve(item, cache)
            progress.advance(t3)
        progress.update(t3, description="[green]✓[/green] MusicBrainz IDs resolved")

    rows = compute_audit(items, cache, lidarr_albums)

    if verbose:
        for row in rows:
            _console.print(
                f"  {row.artist_name} — {row.album_title}"
                f"  ({row.tracks_owned}/{row.total_tracks} tracks,"
                f" {row.pct_owned:.1f}%)"
            )

    out_path = (
        Path(output)
        if output
        else Path("audit") / f"audit_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.csv"
    )
    write_audit_csv(rows, out_path)
    cache.save()

    discogs_matched = len(lidarr_albums) - len(rows)
    unresolved_count = sum(1 for r in rows if r.discogs_match == "unresolved")

    table = Table(title="Audit Summary", show_header=True)
    table.add_column("", style="bold", min_width=35)
    table.add_column("Count", justify="right", min_width=6)
    table.add_row("Discogs vinyl records", str(len(items)))
    table.add_row("Monitored Lidarr albums", str(len(lidarr_albums)))
    table.add_row("Matched to Discogs (skipped)", str(discogs_matched))
    table.add_row("Unresolved MBZ (included, flagged)", str(unresolved_count))
    table.add_row("Exported to audit CSV", str(len(rows)))

    _console.print()
    _console.print(table)
    _console.print()
    _console.print(f"Output written to: [bold]{out_path}[/bold]")
    _console.print(
        "Sort by [bold]pct_owned[/bold] ascending to find the strongest deletion candidates."
    )


@main.command("clear-cache")
@click.option(
    "--config",
    default=".env",
    show_default=True,
    help="Path to .env config file.",
)
@click.confirmation_option(prompt="This will delete the local MusicBrainz lookup cache. Continue?")
def clear_cache(config: str) -> None:
    """Delete the local MusicBrainz lookup cache."""
    settings = _load_or_exit(config)
    from discogs_lidarr_sync.config import Settings

    assert isinstance(settings, Settings)

    path = Path(settings.mbz_cache_path)
    if path.exists():
        path.unlink()
        _console.print(f"[green]Deleted cache:[/green] {path}")
    else:
        _console.print(f"[yellow]Cache not found:[/yellow] {path}")
