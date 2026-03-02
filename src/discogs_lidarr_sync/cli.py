"""CLI entry point for discogs-lidarr-sync.

Commands
--------
sync         Fetch the Discogs vinyl collection and sync new albums to Lidarr.
status       Show collection / library sizes without making any changes.
clear-cache  Delete the local MusicBrainz lookup cache.

Usage
-----
    discogs-lidarr-sync sync [--dry-run] [--verbose]
    discogs-lidarr-sync status
    discogs-lidarr-sync clear-cache
"""

from __future__ import annotations

import click


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
@click.option("--verbose", "-v", is_flag=True, default=False, help="Verbose output.")
def sync(dry_run: bool, verbose: bool) -> None:
    """Fetch the Discogs vinyl collection and sync new albums to Lidarr."""
    raise NotImplementedError


@main.command()
def status() -> None:
    """Show current Discogs collection size and Lidarr library size."""
    raise NotImplementedError


@main.command("clear-cache")
@click.confirmation_option(prompt="This will delete the local MusicBrainz lookup cache. Continue?")
def clear_cache() -> None:
    """Delete the local MusicBrainz lookup cache."""
    raise NotImplementedError
