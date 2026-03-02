"""Discogs API client.

Fetches the user's full collection (all pages, folder 0 = all releases),
normalises each entry into a DiscogsItem, and filters to vinyl-only records.
"""

from __future__ import annotations

from typing import Any

from discogs_lidarr_sync.models import DiscogsItem


def fetch_collection(username: str, token: str) -> list[DiscogsItem]:
    """Fetch the complete Discogs collection for *username*.

    Paginates through all pages of folder 0 (all releases).
    Respects the X-Discogs-Ratelimit-Remaining response header and retries
    with exponential backoff on HTTP 429.

    Returns only vinyl records (items for which is_vinyl() is True).
    """
    raise NotImplementedError


def normalize_item(raw: dict[str, Any]) -> DiscogsItem:
    """Map a raw Discogs API release dict to a DiscogsItem."""
    raise NotImplementedError


def is_vinyl(item: DiscogsItem) -> bool:
    """Return True if the item has at least one 'Vinyl' format entry."""
    raise NotImplementedError
