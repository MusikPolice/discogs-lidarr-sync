# Library Purge Feature — Plan (Phase B)

> **Status:** Draft — incorporating first round of feedback.

---

## 1. Problem Statement

Phase A (`audit`) produces a CSV of monitored Lidarr albums that are absent from the
Discogs vinyl collection.  The user reviews that CSV in a spreadsheet, changes individual
rows from `action=delete` to `action=keep` for albums they want to retain, and saves it.

Phase B consumes that edited CSV and performs the actual deletions from Lidarr — removing
the listed albums, and removing any artist whose entire monitored library has been deleted
in the same run.

---

## 2. Scope

### In scope

- New `purge` CLI command.
- Reads a CSV produced by `audit` (potentially user-edited).
- For each row where `action == "delete"`:
  - Deletes the album from Lidarr via the API.
  - After all album deletions, checks whether the parent artist has any remaining
    monitored albums in Lidarr.  If not, deletes the artist too.
- Skips rows where `action != "delete"` (e.g. `"keep"`), with a count reported at the end.
- `--dry-run` mode: reads the CSV and reports what *would* be deleted without making any
  API calls.
- Summary table at the end: albums deleted, artists deleted, albums skipped (keep),
  albums skipped (already gone), errors.

### Out of scope

- Deleting files from disk **by default** — Lidarr's delete API removes the monitoring
  entry only unless `--delete-files` is passed (see §5).
- Modifying albums or artists that are not in the CSV.
- Re-running `audit` automatically before purging.

---

## 3. CSV Contract

The `purge` command reads the CSV schema produced by `audit`:

| Column | Used by purge? | Notes |
|---|---|---|
| `action` | **Yes** — primary decision column | `"delete"` → remove; anything else → skip |
| `artist_name` | Yes — progress display and reporting only | |
| `album_title` | Yes — progress display and reporting only | |
| `year` | No | |
| `tracks_owned` | No | |
| `total_tracks` | No | |
| `pct_owned` | No | |
| `discogs_match` | No | |
| `album_mbid` | No — lidarr_album_id used instead | Kept for human reference |
| `artist_mbid` | No — lidarr_artist_id used instead | Kept for human reference |
| `lidarr_album_id` | **Yes** — primary key for album deletion | |
| `lidarr_artist_id` | **Yes** — used for post-deletion artist check | |

`lidarr_album_id` and `lidarr_artist_id` are Lidarr's internal integer IDs.  They are
stable for the lifetime of the Lidarr instance but will change if the library is rebuilt
from scratch.  This means **the CSV should be used promptly** after `audit` produces it —
do not rely on a months-old audit file.

### Robustness against user edits

Users editing the CSV in Excel may introduce:
- Extra whitespace around `action` values → strip before comparing.
- Mixed case (`Keep`, `KEEP`) → compare case-insensitively.
- Blank `action` cells → treat as `"keep"` (conservative default).
- Extra columns or reordered columns → `csv.DictReader` handles this naturally.
- Completely missing `lidarr_album_id` (user deleted the column) → skip the row with a
  warning rather than crashing.

---

## 4. Deletion Logic

### Album deletion

Lidarr exposes `DELETE /api/v1/album/{id}`.  pyarr wraps this as `del_album(id)`.

Before deleting, the command confirms the album still exists in Lidarr by checking its
internal ID.  If it is already gone (deleted manually between audit and purge), the row
is counted as "already gone" and skipped — not an error.

The `--delete-files` flag is forwarded to every album deletion call.  Without it, only
the Lidarr monitoring entry is removed and any files on disk are left untouched.

### Artist deletion

After **all** album deletions are complete, each distinct `lidarr_artist_id` referenced
by a deleted row is checked:

1. Fetch the artist's current album list from Lidarr.
2. If **no monitored albums remain**, delete the artist via `del_artist(id)`.
3. If any monitored albums remain (the user kept some, or the artist has Discogs vinyl
   albums), leave the artist alone.

The `--delete-files` flag is also forwarded to every artist deletion call, consistent
with how album deletions are handled.

This two-pass design (all albums first, then artist check) avoids re-checking the artist
after every single album deletion, which would be slow for artists with many deletions.

### Deciding when to delete an artist

The artist-deletion check counts **monitored** albums only.  Unmonitored albums (Lidarr's
auto-indexed discography entries added when the artist was first imported) are ignored —
their presence does not prevent artist deletion.

---

## 5. CLI Design

```
discogs-lidarr-sync purge [OPTIONS] INPUT

  Delete Lidarr albums listed in an audit CSV with action=delete.

  INPUT is the path to a CSV file produced by the audit command.  Edit the
  file in a spreadsheet first, changing action to "keep" for any album you
  want to retain.

Arguments:
  INPUT    Path to the audit CSV file.  [required]

Options:
  --dry-run        Show what would be deleted without making any changes.
  --delete-files   Also delete files from disk when removing albums and artists.
                   Default: remove Lidarr monitoring entries only, leave files
                   untouched.
  --config PATH    Path to .env config file.  [default: .env]
  -v, --verbose    Print each album as it is processed.
  --help           Show this message and exit.
```

`INPUT` is a required positional argument rather than a flag.  This is intentional: the
file path is the primary input to the command and Click's convention is to use positional
arguments for required file inputs.

### Console output

Progress is shown with a Rich spinner.  At the end, a summary table is printed:

```
┌─────────────────────────────────┬───────┐
│ Albums to delete (from CSV)     │   342 │
│ Albums skipped (keep)           │    58 │
│ Albums already gone in Lidarr   │     4 │
│ Albums deleted                  │   337 │
│ Artists deleted                 │    89 │
│ Errors                          │     1 │
└─────────────────────────────────┴───────┘
```

In `--dry-run` mode the title reads "Purge Summary (dry run)" and no API calls are made.

---

## 6. Implementation Plan

### New / modified files

| File | Change |
|---|---|
| `src/discogs_lidarr_sync/lidarr.py` | Add `delete_album(client, lidarr_id)` and `delete_artist(client, lidarr_id)`; add `get_artist_album_count(client, lidarr_artist_id)` to check remaining monitored albums |
| `src/discogs_lidarr_sync/purge.py` | New module: `read_purge_csv()`, `compute_purge()`, `apply_purge()` |
| `src/discogs_lidarr_sync/models.py` | Add `PurgeRow` dataclass and `PurgeReport` dataclass |
| `src/discogs_lidarr_sync/cli.py` | Add `purge` Click command |
| `tests/test_purge.py` | Unit tests for purge logic |
| `tests/test_lidarr.py` | Tests for new lidarr.py functions |

### New dataclasses

```python
@dataclass
class PurgeRow:
    """A single row read from an audit CSV."""
    action: str               # "delete" | "keep" | other
    artist_name: str
    album_title: str
    lidarr_album_id: int
    lidarr_artist_id: int

@dataclass
class PurgeReport:
    """Aggregate summary of a purge run."""
    dry_run: bool
    total_rows: int           # rows read from CSV
    to_delete: int            # rows with action == "delete"
    skipped_keep: int         # rows with action != "delete"
    already_gone: int         # album not found in Lidarr (already deleted)
    albums_deleted: int
    artists_deleted: int
    errors: int
    error_details: list[str]  # one message per error, for verbose output
```

### New module: `purge.py`

```python
def read_purge_csv(path: Path) -> list[PurgeRow]:
    """Read an audit CSV and return rows parsed into PurgeRow objects.

    Validates that required columns are present.  Rows with a missing or
    non-integer lidarr_album_id are skipped with a warning.  action values
    are stripped of whitespace and lowercased before storage.
    """

def compute_purge(rows: list[PurgeRow]) -> tuple[list[PurgeRow], list[PurgeRow]]:
    """Split rows into (to_delete, to_skip) based on the action column."""

def apply_purge(
    to_delete: list[PurgeRow],
    client: LidarrAPI,
    dry_run: bool,
    delete_files: bool = False,
) -> PurgeReport:
    """Delete albums (and orphaned artists) from Lidarr.

    Pass 1: for each row, delete the album if it still exists.
    Pass 2: for each distinct artist_id touched in pass 1, delete the artist
            if it has no remaining monitored albums.

    delete_files is forwarded to both album and artist deletion calls.
    """
```

### New lidarr.py functions

```python
def delete_album(
    client: LidarrAPI,
    lidarr_id: int,
    delete_files: bool = False,
) -> None:
    """Delete an album from Lidarr by its internal ID.

    delete_files=True also removes associated files from disk.
    Raises LidarrError on unexpected API failures.  Callers should catch
    this and record it as an error rather than aborting the run.
    """

def delete_artist(
    client: LidarrAPI,
    lidarr_id: int,
    delete_files: bool = False,
) -> None:
    """Delete an artist from Lidarr by its internal ID.

    delete_files=True also removes all associated files from disk.
    Raises LidarrError on unexpected API failures.
    """

def get_monitored_album_count_for_artist(
    client: LidarrAPI,
    lidarr_artist_id: int,
) -> int:
    """Return the number of monitored albums for a given artist internal ID.

    Used after album deletion to decide whether to also delete the artist.
    """
```

---

## 7. Test Plan

| Test | What it covers |
|---|---|
| `test_read_purge_csv_parses_all_fields` | All columns read correctly, action normalised |
| `test_read_purge_csv_strips_and_lowercases_action` | `" Keep "` → `"keep"` |
| `test_read_purge_csv_skips_invalid_album_id` | Non-integer lidarr_album_id → row skipped with warning |
| `test_read_purge_csv_raises_on_missing_required_columns` | Missing `lidarr_album_id` column → clear error |
| `test_compute_purge_splits_on_action` | `"delete"` rows to_delete; `"keep"` and blank to_skip |
| `test_apply_purge_calls_delete_album_for_each_row` | del_album called once per delete row |
| `test_apply_purge_skips_already_gone_albums` | 404 on del_album → counted as already_gone, not error |
| `test_apply_purge_deletes_artist_when_no_monitored_albums_remain` | Artist deleted after last album deleted |
| `test_apply_purge_keeps_artist_with_remaining_monitored_albums` | Artist not deleted if monitored albums remain |
| `test_apply_purge_dry_run_makes_no_api_calls` | No del_album / del_artist calls in dry_run mode |
| `test_apply_purge_records_error_without_aborting` | LidarrError on one album → error recorded, rest processed |
| `test_apply_purge_deduplicates_artist_checks` | Artist with 3 deleted albums → artist check runs once |
| `test_apply_purge_delete_files_forwarded_to_album_delete` | `delete_files=True` passed through to del_album |
| `test_apply_purge_delete_files_forwarded_to_artist_delete` | `delete_files=True` passed through to del_artist |
| `test_apply_purge_delete_files_false_by_default` | del_album/del_artist called with `deleteFiles=False` when flag not set |
| `test_get_monitored_album_count_for_artist_counts_correctly` | 3 monitored, 2 unmonitored → returns 3 |

All tests use mocked Lidarr clients — no live API calls.

---

## 8. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| `INPUT` as positional arg | Yes | Click convention for required file inputs; makes the command read more naturally (`purge audit.csv`) |
| File deletion | `--delete-files` flag, default off | Safe default — removing a monitoring entry is reversible; deleting files from disk is not. The flag lets the user opt in when they're certain. |
| `--delete-files` scope | Both album *and* artist deletions | Consistent behaviour — no partial file removal where artist files linger after album entries are cleaned up |
| Artist deletion condition | Zero monitored albums remaining | Unmonitored auto-indexed entries do not count — the artist is only "empty" if there is nothing left to download |
| Artist deletion timing | After all album deletions complete (two-pass) | Avoids redundant per-album artist re-checks; correct even when an artist has multiple albums in the CSV |
| Error handling | Record and continue | Consistent with `sync` and `audit`; a single API failure should not abort a 300-album purge |
| Already-gone albums | Count as "already gone", not error | The desired end state (album absent from Lidarr) has been achieved; no action needed |
| `action` comparison | Strip + lowercase | Defensive against common spreadsheet editing artefacts |
| Blank `action` cells | Treat as `"keep"` | Conservative default — do not delete anything the user did not explicitly mark |
| `lidarr_album_id` staleness | Document; no automatic re-validation | Checking every ID against the live library before acting would require N API calls; instead, 404s during deletion are handled gracefully as "already gone" |
| Exclusion list | Not implemented | Too aggressive — the user may change their mind about an album in the future and want `sync` to re-add it |

---

## 9. Open Questions

All open questions resolved:

1. **File deletion** — `--delete-files` flag added; off by default.  Applies to both
   album and artist deletions.

2. **Exclusion list** — will not be implemented.  The user may change their mind about
   an album in the future and wants `sync` to be able to re-add it freely.
