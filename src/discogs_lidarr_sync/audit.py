"""Library audit: identify monitored Lidarr albums absent from Discogs.

compute_audit() cross-references the user's Discogs vinyl collection (resolved
to MBZ Release Group MBIDs via the on-disk cache) against the list of monitored
Lidarr albums, and returns one AuditRow for every Lidarr album that cannot be
confirmed as present in Discogs.

write_audit_csv() serialises those rows to a CSV file that can be opened in
Excel, sorted by pct_owned to find deletion candidates, and then fed back to
the future ``purge`` command (which will act on the ``action`` column).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from discogs_lidarr_sync.mbz import MbzCache
from discogs_lidarr_sync.models import AuditRow, DiscogsItem

# Column order in the CSV — action is first so it is immediately visible
# in Excel without horizontal scrolling.
_CSV_FIELDS = [
    "action",
    "artist_name",
    "album_title",
    "year",
    "tracks_owned",
    "total_tracks",
    "pct_owned",
    "discogs_match",
    "album_mbid",
    "artist_mbid",
    "lidarr_album_id",
    "lidarr_artist_id",
]


def _owned_mbids(discogs_items: list[DiscogsItem], cache: MbzCache) -> set[str]:
    """Return the set of MBZ Release Group MBIDs for all resolved Discogs items."""
    mbids: set[str] = set()
    for item in discogs_items:
        mbz = cache.get(item.discogs_release_id)
        if mbz and mbz.release_group_mbid:
            mbids.add(mbz.release_group_mbid)
    return mbids


def _extract_year(album: dict[str, Any]) -> int | None:
    """Extract the release year from a Lidarr album record.

    Lidarr returns ``releaseDate`` as an ISO-8601 string such as
    "1973-03-17T00:00:00Z".  The year is the first four characters.
    Returns None if the field is absent or unparseable.
    """
    date_str = str(album.get("releaseDate", ""))
    if len(date_str) >= 4:
        try:
            return int(date_str[:4])
        except ValueError:
            pass
    return None


def _pct_owned(tracks_owned: int, total_tracks: int) -> float:
    """Percentage of tracks owned, rounded to one decimal place.

    Returns 0.0 when *total_tracks* is zero (Lidarr sometimes has no track
    data for albums that haven't been indexed yet).
    """
    if total_tracks == 0:
        return 0.0
    return round(tracks_owned / total_tracks * 100, 1)


def compute_audit(
    discogs_items: list[DiscogsItem],
    cache: MbzCache,
    lidarr_albums: list[dict[str, Any]],
) -> list[AuditRow]:
    """Cross-reference Discogs-owned MBIDs against monitored Lidarr albums.

    For each Lidarr album:
    - If its ``foreignAlbumId`` is in the Discogs-owned set → silently omit it.
    - If its ``foreignAlbumId`` is absent or empty → include with
      ``discogs_match="unresolved"`` (cannot confirm either way).
    - Otherwise → include with ``discogs_match="no"`` (definitively not in
      Discogs).

    All included rows default to ``action="delete"``.  The caller (or the user
    in a spreadsheet) can change individual rows to ``action="keep"`` before
    passing the file to the future ``purge`` command.

    Args:
        discogs_items: Vinyl records from the Discogs collection (already
            fetched; MBZ resolution must have been run beforehand so the cache
            is warm).
        cache: The MBZ lookup cache populated by prior resolve() calls.
        lidarr_albums: Full album records from get_monitored_albums_with_stats().

    Returns:
        List of AuditRow, one per Lidarr album not confirmed in Discogs.
    """
    owned = _owned_mbids(discogs_items, cache)
    rows: list[AuditRow] = []

    for album in lidarr_albums:
        album_mbid: str = album.get("foreignAlbumId") or ""

        if album_mbid and album_mbid in owned:
            continue  # Confirmed in Discogs — skip

        artist: dict[str, Any] = album.get("artist") or {}
        stats: dict[str, Any] = album.get("statistics") or {}
        tracks_owned = int(stats.get("trackFileCount", 0))
        total_tracks = int(stats.get("totalTrackCount", 0))

        rows.append(
            AuditRow(
                action="delete",
                artist_name=str(artist.get("artistName", "")),
                album_title=str(album.get("title", "")),
                year=_extract_year(album),
                tracks_owned=tracks_owned,
                total_tracks=total_tracks,
                pct_owned=_pct_owned(tracks_owned, total_tracks),
                discogs_match="unresolved" if not album_mbid else "no",
                album_mbid=album_mbid,
                artist_mbid=str(artist.get("foreignArtistId", "")),
                lidarr_album_id=int(album.get("id", 0)),
                lidarr_artist_id=int(artist.get("id", 0)),
            )
        )

    return rows


def write_audit_csv(rows: list[AuditRow], path: Path) -> None:
    """Write audit rows to a CSV file at *path*.

    Parent directories are created automatically.  The file is UTF-8 encoded
    with CRLF line endings (``newline=""`` — csv module handles line endings)
    so that Excel opens it correctly on Windows without a double-newline issue.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "action": row.action,
                    "artist_name": row.artist_name,
                    "album_title": row.album_title,
                    "year": row.year if row.year is not None else "",
                    "tracks_owned": row.tracks_owned,
                    "total_tracks": row.total_tracks,
                    "pct_owned": row.pct_owned,
                    "discogs_match": row.discogs_match,
                    "album_mbid": row.album_mbid,
                    "artist_mbid": row.artist_mbid,
                    "lidarr_album_id": row.lidarr_album_id,
                    "lidarr_artist_id": row.lidarr_artist_id,
                }
            )
