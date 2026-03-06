# I'm in Exports and Imports

One-way sync from a [Discogs](https://www.discogs.com/) vinyl collection to [Lidarr](https://lidarr.audio/).

Records present in Discogs but absent from Lidarr are added. The sync is additive — nothing is removed automatically. A separate `audit` / `purge` workflow lets you review and selectively remove albums that are no longer in your collection. The script is safe to re-run repeatedly.

---

## How it works

1. **Fetch** your Discogs collection (vinyl records only).
2. **Resolve** each record's Discogs IDs to MusicBrainz UUIDs — the IDs Lidarr requires. Results are cached on disk to avoid re-querying on subsequent runs.
3. **Diff** the resolved IDs against your current Lidarr library.
4. **Add** artists and albums that are missing. Artists are added before their albums.

---

## Prerequisites

- Python 3.11
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- A [Discogs account](https://www.discogs.com/) with a collection
- A running [Lidarr](https://lidarr.audio/) instance

---

## Installation

```bash
git clone https://github.com/youruser/discogs-lidarr-sync
cd discogs-lidarr-sync
uv sync
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `DISCOGS_TOKEN` | Personal access token from [discogs.com/settings/developers](https://www.discogs.com/settings/developers) |
| `DISCOGS_USERNAME` | Your Discogs username |
| `LIDARR_URL` | Base URL of your Lidarr instance, e.g. `http://192.168.1.10:8686` |
| `LIDARR_API_KEY` | Found at **Lidarr → Settings → General → Security → API Key** |
| `LIDARR_ROOT_FOLDER` | Root folder path configured in Lidarr, e.g. `/music` |
| `LIDARR_QUALITY_PROFILE_ID` | Integer ID of the quality profile to use — run `discogs-lidarr-sync profiles` to list them |
| `LIDARR_METADATA_PROFILE_ID` | Integer ID of the metadata profile to use — run `discogs-lidarr-sync profiles` to list them |
| `MBZ_CACHE_PATH` | *(optional)* Path to the MBZ lookup cache. Default: `.cache/mbz_cache.json` |

---

## Usage

### `sync` — add missing albums to Lidarr

```bash
uv run discogs-lidarr-sync sync
```

Options:

| Flag | Description |
|---|---|
| `--dry-run` | Show what would be added without making any changes |
| `--verbose` / `-v` | Print each item as it is processed |
| `--config PATH` | Path to a custom `.env` file (default: `.env`) |

A colour-coded summary table is printed at the end of every run. A timestamped JSON report is written to `runs/`.

### `status` — check collection and library sizes

```bash
uv run discogs-lidarr-sync status
```

Fetches and displays the current Discogs vinyl count and Lidarr artist/album counts. No changes are made.

### `profiles` — find quality and metadata profile IDs

```bash
uv run discogs-lidarr-sync profiles
```

Prints a table of all quality profiles and metadata profiles configured in your Lidarr instance, with their numeric IDs. Only `LIDARR_URL` and `LIDARR_API_KEY` need to be set — run this command before filling in the profile ID variables in `.env`.

### `audit` — find Lidarr albums absent from your Discogs collection

```bash
uv run discogs-lidarr-sync audit
```

Compares every monitored album in Lidarr against your current Discogs vinyl collection and exports a CSV of albums that have no matching Discogs record. All rows default to `action=delete`. Open the CSV in a spreadsheet, change `action` to `keep` for any album you want to retain, then pass it to `purge`.

Options:

| Flag | Description |
|---|---|
| `--output PATH` | Path for the output CSV. Default: `audit/audit_<timestamp>.csv` |
| `--verbose` / `-v` | Print each album found during the audit |
| `--config PATH` | Path to a custom `.env` file (default: `.env`) |

A summary table is printed at the end showing how many albums were matched, how many were exported to the CSV, and how many could not be resolved via MusicBrainz.

### `purge` — delete albums (and orphaned artists) from Lidarr

```bash
uv run discogs-lidarr-sync purge audit/audit_20240101T120000Z.csv
```

Reads an audit CSV produced by the `audit` command and deletes every album whose `action` column is set to `delete`. After album deletions, any artist that has no remaining monitored albums is also removed.

**Always review the CSV carefully before running purge.** Use `--dry-run` to confirm what will be deleted.

Options:

| Flag | Description |
|---|---|
| `--dry-run` | Show what would be deleted without making any changes |
| `--delete-files` | Also delete files from disk. Default: remove Lidarr entries only, leave files untouched |
| `--verbose` / `-v` | Print each album and artist as it is deleted |
| `--config PATH` | Path to a custom `.env` file (default: `.env`) |

#### Typical workflow

```bash
# 1. Generate the audit CSV
uv run discogs-lidarr-sync audit

# 2. Open the CSV in a spreadsheet. Change action=keep for anything you want to retain.

# 3. Preview what will be deleted
uv run discogs-lidarr-sync purge audit/audit_<timestamp>.csv --dry-run --verbose

# 4. Execute the purge
uv run discogs-lidarr-sync purge audit/audit_<timestamp>.csv --verbose
```

---

### `clear-cache` — delete the MBZ cache

```bash
uv run discogs-lidarr-sync clear-cache
```

Deletes `.cache/mbz_cache.json` (or the path set in `MBZ_CACHE_PATH`). You will be prompted for confirmation. The cache will be rebuilt from scratch on the next `sync` run.

---

## The MusicBrainz cache

Discogs uses integer IDs; Lidarr requires MusicBrainz UUIDs. Resolving each record requires one or two MusicBrainz API requests, subject to a **1 request/second** rate limit. A cold run over a 500-record collection takes around 8 minutes.

Results are cached in `.cache/mbz_cache.json` (gitignored). Subsequent runs skip already-resolved records and complete in seconds.

Failed lookups are also cached (with `status: "failed"`) so the script does not waste API quota re-querying records that have no MusicBrainz entry.

---

## `unresolved.log`

If any vinyl records could not be matched to a MusicBrainz Release Group, they are appended to `unresolved.log` (tab-separated: Discogs release ID, artist, album title, status). Review this file periodically to identify records that may need manual attention in MusicBrainz or Lidarr.

---

## Development

```bash
# Run tests
uv run pytest

# Linter
uv run ruff check .

# Type checker
uv run mypy src/

# Record VCR cassettes for Discogs tests (requires credentials in .env)
uv run pytest tests/test_discogs_recorded.py --record-mode=all

# Record VCR cassettes for Lidarr tests (requires LIDARR_URL and LIDARR_API_KEY in .env)
uv run pytest tests/test_lidarr_recorded.py --record-mode=all

# Run live integration tests (requires full credentials in .env)
uv run pytest -m integration
```

Pre-commit hooks run `ruff` and `mypy` on staged files automatically:

```bash
uv run pre-commit install
```
