"""Discogs API client.

Fetches the user's full collection (all pages, folder 0 = all releases),
normalises each entry into a DiscogsItem, and filters to vinyl-only records.
"""

from __future__ import annotations

from typing import Any

import discogs_client

from discogs_lidarr_sync.models import DiscogsItem

_USER_AGENT = "discogs-lidarr-sync/0.1"
_PER_PAGE = 100


def fetch_collection(username: str, token: str) -> list[DiscogsItem]:
    """Fetch the complete Discogs collection for *username*.

    Paginates through all pages of folder 0 (all releases).
    HTTP 429 responses trigger automatic exponential backoff courtesy of the
    discogs_client library's built-in backoff decorator; no extra retry logic
    is needed here.

    Returns only vinyl records (items for which is_vinyl() is True).
    """
    client = discogs_client.Client(_USER_AGENT, user_token=token)
    base_url = f"{client._base_url}/users/{username}/collection/folders/0/releases"

    items: list[DiscogsItem] = []
    page = 1
    while True:
        data: dict[str, Any] = client._get(f"{base_url}?page={page}&per_page={_PER_PAGE}")

        for raw in data["releases"]:
            item = normalize_item(raw)
            if is_vinyl(item):
                items.append(item)

        if page >= data["pagination"]["pages"]:
            break
        page += 1

    return items


def normalize_item(raw: dict[str, Any]) -> DiscogsItem:
    """Map a raw Discogs API collection-release dict to a DiscogsItem.

    *raw* is a single entry from the ``releases`` array returned by
    ``GET /users/{username}/collection/folders/0/releases``.

    Artist information comes from the first entry in
    ``basic_information.artists``; if that list is empty, sentinel values
    (id=0, name="Unknown") are used.  A ``year`` value of 0 (Discogs
    convention for "unknown") is converted to ``None``.
    """
    basic: dict[str, Any] = raw["basic_information"]

    artists: list[dict[str, Any]] = basic.get("artists", [])
    if artists:
        discogs_artist_id = int(artists[0]["id"])
        artist_name = str(artists[0]["name"])
    else:
        discogs_artist_id = 0
        artist_name = "Unknown"

    raw_year = basic.get("year", 0)
    year: int | None = int(raw_year) if raw_year else None

    formats: list[str] = [str(f["name"]) for f in basic.get("formats", [])]

    return DiscogsItem(
        discogs_release_id=int(raw["id"]),
        discogs_artist_id=discogs_artist_id,
        artist_name=artist_name,
        album_title=str(basic["title"]),
        year=year,
        formats=formats,
    )


def is_vinyl(item: DiscogsItem) -> bool:
    """Return True if the item has at least one 'Vinyl' format entry."""
    return "Vinyl" in item.formats
