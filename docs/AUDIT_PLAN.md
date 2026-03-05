# Library Audit Feature — Plan

> **Status:** Draft — incorporating first round of feedback.

---

## 1. Problem Statement

The Lidarr library was originally built up during a pre-streaming era of digital music
collecting, when individual tracks and incomplete albums were routinely added.  The CD
collection was sold after being ripped to lossless FLAC, so those files exist in Lidarr
but have no corresponding Discogs entries (the Discogs collection tracks vinyl only).
A significant number of Lidarr entries therefore represent albums that were never
purchased on vinyl — partial rips, digital singles, CD rips, and historical acquisitions
with no matching record in the Discogs collection.

There is no quick way to identify and clean these up inside Lidarr's UI.  The goal of
this feature is to produce a structured, human-readable export of every monitored Lidarr
album that does **not** have a corresponding entry in the Discogs vinyl collection, along
with enough per-album metadata to make informed keep-or-delete decisions in a spreadsheet.

A future second phase will allow the tool to read that export back in and perform the
actual bulk deletion from Lidarr.

---

## 2. Scope

### Phase A — Audit export (this document)

- New `audit` CLI command.
- Fetches the Discogs vinyl collection and resolves MBZ Release Group MBIDs (re-using the
  existing MBZ cache — no cold-run penalty if `sync` has been run recently).
- Fetches all monitored albums from Lidarr.
- Cross-references the two sets by MBZ Release Group MBID and filters out albums that are
  in Discogs.
- Exports the remainder to a CSV file with enough columns to sort, filter, and decide in
  a spreadsheet, **and** enough identifiers for Phase B to drive bulk deletion without
  additional lookups.

### Phase B — Bulk purge (future, not designed here)

- New `purge` CLI command.
- Reads a (potentially user-edited) CSV produced by Phase A.
- Acts on the `action` column: rows marked `delete` are removed from Lidarr; rows
  marked `keep` are skipped.  This lets the user make keep/delete decisions in Excel
  before handing the file back to the tool.
- After deleting albums, checks whether the parent artist has any remaining monitored
  albums; if not, deletes the artist too.
- Supports `--dry-run`.

Phase B is called out here only to constrain the Phase A design — specifically to ensure
the CSV schema carries the identifiers and the `action` column that Phase B will need.

---

## 3. The Cross-Reference Problem

The Discogs collection is identified by Discogs-internal integer IDs.  Lidarr identifies
albums by MusicBrainz Release Group UUIDs (`foreignAlbumId`).  The existing MBZ cache
(`mbz_cache.json`) already bridges these: it maps each Discogs release ID to a
`release_group_mbid`.

The audit command re-uses this bridge:

```
Discogs collection (vinyl)
  └─ for each item → resolve via MBZ cache → release_group_mbid
                                                      │
                                                      ▼
                                            set of "Discogs-owned" MBIDs
                                                      │
Lidarr monitored albums                               │
  └─ foreignAlbumId ─────────────────────────── NOT IN ──► audit candidates
```

Albums whose `foreignAlbumId` is **not** in the Discogs-owned set are written to the
export.  Albums that *are* in Discogs are silently omitted.

Items whose MBZ resolution failed (no `release_group_mbid` in cache) are treated
conservatively: they are included in the export and flagged with
`discogs_match = "unresolved"` so the user can decide manually.

---

## 4. Data Requirements

### From Lidarr (`GET /api/v1/album`)

The `statistics` sub-object returned by Lidarr's album endpoint contains:

| Field | Meaning |
|---|---|
| `statistics.trackFileCount` | Files actually on disk |
| `statistics.totalTrackCount` | Total tracks the album is supposed to have |
| `statistics.percentOfTracks` | Pre-computed file percentage (convenience) |

The `artist` sub-object on each album record contains `artistName` and
`foreignArtistId`.  Lidarr also returns its own internal integer `id` for both albums
and artists — these are needed for deletion in Phase B.

### From the MBZ cache / Discogs

We need the set of `release_group_mbid` values that correspond to the user's Discogs
vinyl collection.  If the cache is warm this requires zero network calls.  If items are
missing from the cache (new Discogs additions since the last `sync`) the command will
resolve them the same way `sync` does, respecting the 1 req/sec MusicBrainz rate limit.

---

## 5. Export Format

**File:** CSV, written to `audit/audit_{YYYYMMDDTHHMMSSZ}.csv` by default.
A `--output` flag allows the caller to specify a different path.

CSV is chosen because:
- Excel opens it natively without an import wizard.
- No extra Python dependencies (stdlib `csv` module).
- It is trivially machine-readable for Phase B.

### Columns

| Column | Type | Description |
|---|---|---|
| `action` | string | **Phase B instruction:** `delete` (default) or `keep`. Change to `keep` in Excel for albums you want to retain. |
| `artist_name` | string | Artist name as stored in Lidarr |
| `album_title` | string | Album title as stored in Lidarr |
| `year` | int \| blank | Release year from Lidarr |
| `tracks_owned` | int | Files currently on disk (`trackFileCount`) |
| `total_tracks` | int | Total tracks the album should have (`totalTrackCount`) |
| `pct_owned` | float (0–100) | `tracks_owned / total_tracks × 100`, rounded to 1 dp |
| `discogs_match` | string | `"no"` or `"unresolved"` — why this album was included in the audit |
| `album_mbid` | UUID string | MBZ Release Group UUID (`foreignAlbumId`) |
| `artist_mbid` | UUID string | MBZ Artist UUID (`foreignArtistId`) |
| `lidarr_album_id` | int | Lidarr internal album ID (for Phase B deletion) |
| `lidarr_artist_id` | int | Lidarr internal artist ID (for Phase B deletion) |

`action` is placed first so it is immediately visible in Excel without scrolling right.
All rows default to `delete`; the user changes individual rows to `keep` before running
Phase B.  Phase B treats any value other than `delete` as an instruction to skip the row.

`discogs_match` is `"no"` for albums definitively absent from Discogs, or `"unresolved"`
for albums whose MBZ ID could not be looked up (included conservatively for human review).

### Sorting recommendation (documented in command output)

Sort by `pct_owned` ascending: albums at 0–10 % are the strongest deletion candidates.
Sort by `tracks_owned` ascending as a secondary key: an album with 1 of 18 tracks is a
clearer candidate than one with 9 of 10.

---

## 6. CLI Design

```
discogs-lidarr-sync audit [OPTIONS]

  Compare the Discogs vinyl collection against monitored Lidarr albums and
  export albums not present in Discogs to a CSV file.

Options:
  --output PATH    Path for the output CSV.  [default: audit/audit_{timestamp}.csv]
  --config PATH    Path to .env config file.  [default: .env]
  -v, --verbose    Print each album as it is evaluated.
  --help           Show this message and exit.
```

No `--dry-run` is needed — the audit command never modifies anything.

### Console output

At the end of the run, the command prints a Rich summary table:

```
┌─────────────────────────────────────┬───────┐
│ Discogs vinyl records               │   223 │
│ Monitored Lidarr albums             │   312 │
│ Matched to Discogs (skipped)        │   187 │
│ Unresolved MBZ (included, flagged)  │     8 │
│ Exported to audit CSV               │   125 │
└─────────────────────────────────────┴───────┘

Output written to: audit/audit_20260305T091500Z.csv
Sort by pct_owned ascending to find the strongest deletion candidates.
```

---

## 7. Implementation Plan

### New / modified files

| File | Change |
|---|---|
| `src/discogs_lidarr_sync/lidarr.py` | Add `get_monitored_albums_with_stats()` — returns full album records (not just MBIDs) including `statistics`, internal `id`, and `artist` sub-object |
| `src/discogs_lidarr_sync/audit.py` | New module: `compute_audit()` and `write_audit_csv()` |
| `src/discogs_lidarr_sync/cli.py` | Add `audit` Click command |
| `tests/test_audit.py` | Unit tests for `compute_audit()` and `write_audit_csv()` |
| `tests/test_lidarr.py` | Tests for `get_monitored_albums_with_stats()` |

### New function: `get_monitored_albums_with_stats(client) -> list[dict]`

Returns every monitored album from `GET /api/v1/album`, keeping the full record
(not just the MBID) so that `statistics`, internal IDs, and artist metadata are
available downstream.  Filters to `monitored=True` on the client side.

### New module: `audit.py`

```python
def compute_audit(
    discogs_items: list[DiscogsItem],
    cache: MbzCache,
    lidarr_albums: list[dict],
) -> list[AuditRow]:
    """Cross-reference Discogs-owned MBIDs against monitored Lidarr albums.

    Returns one AuditRow per Lidarr album that is NOT in the Discogs collection,
    plus albums whose MBZ resolution was inconclusive (flagged accordingly).
    """

def write_audit_csv(rows: list[AuditRow], path: Path) -> None:
    """Write audit rows to a CSV file at *path*."""
```

### New dataclass: `AuditRow`

Added to `models.py`:

```python
@dataclass
class AuditRow:
    action: str                 # "delete" (default) | "keep" (user-set in Excel)
    artist_name: str
    album_title: str
    year: int | None
    tracks_owned: int
    total_tracks: int
    pct_owned: float
    discogs_match: str          # "no" | "unresolved"
    album_mbid: str
    artist_mbid: str
    lidarr_album_id: int
    lidarr_artist_id: int
```

---

## 8. Test Plan

| Test | What it covers |
|---|---|
| `test_compute_audit_excludes_discogs_albums` | Albums whose MBID is in the Discogs set are omitted |
| `test_compute_audit_includes_non_discogs` | Albums not in Discogs appear in output with `discogs_match="no"` |
| `test_compute_audit_flags_unresolved` | Albums with no MBZ resolution appear with `discogs_match="unresolved"` |
| `test_compute_audit_pct_owned_calculation` | `pct_owned` is computed correctly; zero-track albums don't divide-by-zero |
| `test_write_audit_csv_schema` | CSV has the correct headers and one row per audit row |
| `test_write_audit_csv_creates_parent_dir` | Output directory is created if it doesn't exist |
| `test_get_monitored_albums_with_stats_filters_unmonitored` | Unmonitored albums excluded from result |

All tests use mocked/stubbed data — no live API calls.

---

## 9. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Output format | CSV | Zero extra dependencies; Excel opens natively; machine-readable for Phase B |
| `action` column default | `delete` | Every row is a deletion candidate; the user opts rows *out* rather than in — less work for the common case |
| `action` column position | First column | Immediately visible in Excel without horizontal scrolling |
| Phase B skip condition | Any value other than `delete` | Robust to capitalisation differences (`Keep`, `KEEP`) and accidental whitespace |
| Albums with failed MBZ resolution | Include, flagged as `"unresolved"` | Conservative — better to surface for human review than silently omit |
| Discogs format filter | Vinyl only | Discogs collection tracks vinyl exclusively; no CDs are present to cross-reference |
| MBZ resolution during audit | Re-use existing cache; resolve new items if needed | Consistency with `sync`; avoids a separate resolution step |
| `pct_owned` when `total_tracks == 0` | 0.0 | Lidarr sometimes returns 0 for albums with no track data; treat as 0 % owned |
| Output directory | `audit/` (gitignored, like `runs/`) | Keeps generated files out of the repo |
| Internal Lidarr IDs in CSV | Yes — `lidarr_album_id`, `lidarr_artist_id` | Required for Phase B to perform deletions without additional lookups |

---

## 10. Out of Scope

- Deleting anything from Lidarr (Phase B, separate command).
- Auditing unmonitored Lidarr albums.
- Comparing against non-vinyl Discogs formats — the Discogs collection tracks vinyl
  only; CD rips and other historical digital files in Lidarr have no Discogs counterpart
  and are expected to appear in the audit output.  If other formats are added to Discogs
  in the future, the audit's format filter should be updated alongside `sync`.
- Deduplication of Lidarr albums that appear under multiple artists.
