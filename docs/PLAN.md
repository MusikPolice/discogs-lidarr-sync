# discogs-lidarr-sync — Project Plan

> **Status:** Approved — ready for implementation.

---

## 1. Problem Statement

You maintain a physical vinyl record collection tracked in **Discogs**, which serves as the authoritative inventory of what you own. You want a digital backup of those albums managed by **Lidarr**, which will monitor and download audio files for each album.

The sync is **one-directional**: Discogs → Lidarr.

- Records present in Discogs but absent from Lidarr should be **added** to Lidarr.
- Records present in Lidarr but absent from Discogs should be **left alone** (no deletion).
- If a Discogs album already exists in Lidarr (regardless of its monitoring state), the script leaves it untouched — manual changes made inside Lidarr are authoritative and must be respected.
- The script must be safe to re-run repeatedly (idempotent).

---

## 2. Language & Tooling

### Language: Python 3.11

The machine has Python 3.11.0 installed. This is the target version.

**Rationale:** Both APIs have high-quality Python wrapper libraries. Python is the natural choice for a data-sync CLI script.

### Package Manager: `uv`

`uv` replaces `pip` + `venv` in a single fast tool. It also integrates cleanly with `pyproject.toml`, `ruff`, and `pytest`, making it the right choice for a well-tooled project.

```bash
# Install uv (if not present)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create project
uv init discogs-lidarr-sync
uv python pin 3.11

# Add dependencies
uv add python3-discogs-client pyarr musicbrainzngs python-dotenv rich click

# Add dev dependencies
uv add --dev pytest pytest-cov pytest-recording responses ruff mypy pre-commit
```

### Core Dependencies

| Package | Purpose |
|---|---|
| `python3-discogs-client` | Discogs API wrapper (`joalla/discogs_client` on GitHub) |
| `pyarr` | Lidarr API wrapper (`totaldebug/pyarr`) |
| `musicbrainzngs` | MusicBrainz ID resolution — bridges Discogs IDs to MusicBrainz UUIDs |
| `python-dotenv` | Load credentials from a `.env` file |
| `rich` | Pretty terminal output, progress bars, tables |
| `click` | CLI argument/flag parsing |

### Dev / Tooling Dependencies

| Package | Purpose |
|---|---|
| `pytest` | Test runner |
| `pytest-cov` | Coverage reporting |
| `pytest-recording` | VCR cassettes for HTTP mocking in tests (`vcrpy` under the hood) |
| `responses` | Lightweight HTTP mocking for unit tests |
| `ruff` | Linting + import sorting (replaces flake8, isort, pyupgrade) |
| `mypy` | Static type checking |
| `pre-commit` | Local git hooks to enforce quality before commit |

### Single Config File: `pyproject.toml`

All tool configuration lives in `pyproject.toml` — no separate `.flake8`, `setup.cfg`, or `mypy.ini` files.

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.11"
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=src --cov-report=term-missing"
```

### CI: GitHub Actions

A workflow at `.github/workflows/ci.yml` will run on every push/PR:

```
jobs:
  ci:
    steps:
      - uv sync --frozen
      - uv run ruff check .
      - uv run mypy src/
      - uv run pytest
```

### Pre-commit Hooks

`.pre-commit-config.yaml` will run `ruff` (lint + format) and `mypy` on staged files before every commit, catching issues before they hit CI.

---

## 3. Authentication

### Discogs

- **Method:** Personal Access Token (Discogs → Settings → Developers → Generate Token)
- **Transport:** `Authorization: Discogs token=YOUR_TOKEN` header (handled by `python3-discogs-client`)
- **Rate limit:** 60 requests/minute (authenticated)

### Lidarr

- **Method:** API Key (Lidarr → Settings → General → Security → API Key)
- **Transport:** `X-Api-Key: YOUR_KEY` header (handled by `pyarr`)
- **Base URL:** Configurable — supports both local (`http://localhost:8686`) and remote instances

### Configuration: `.env` File

Credentials and per-instance settings live in a `.env` file (gitignored). CLI flags override `.env` values for scripted use.

```bash
# .env  (copy from .env.example and fill in)
DISCOGS_TOKEN=xxxxx
DISCOGS_USERNAME=your_discogs_username

LIDARR_URL=http://localhost:8686
LIDARR_API_KEY=xxxxx

# Applied when adding new artists/albums to Lidarr
LIDARR_ROOT_FOLDER=/music
LIDARR_QUALITY_PROFILE_ID=1
LIDARR_METADATA_PROFILE_ID=1

# Path to the persistent MusicBrainz lookup cache
MBZ_CACHE_PATH=.cache/mbz_cache.json
```

A `--dry-run` flag will log what would be added without making any changes.

---

## 4. High-Level Architecture

```
┌────────────────────────────────────────────────────┐
│                   cli.py (Click entry point)        │
│   Commands: sync, status, clear-cache              │
└──────────────────────┬─────────────────────────────┘
                       │
         ┌─────────────┴──────────────┐
         ▼                            ▼
┌─────────────────┐        ┌────────────────────┐
│  discogs.py     │        │  lidarr.py          │
│  fetch_         │        │  get_all_artists()  │
│  collection()   │        │  get_all_albums()   │
│  normalize()    │        │  add_artist()       │
└────────┬────────┘        │  add_album()        │
         │                 └──────────┬──────────┘
         │                            │
         └──────────┬─────────────────┘
                    ▼
         ┌──────────────────────┐
         │  mbz.py              │
         │  MusicBrainzResolver │
         │  - resolve_artist()  │
         │  - resolve_album()   │
         │  - load_cache()      │
         │  - save_cache()      │
         └──────────┬───────────┘
                    ▼
         ┌──────────────────────┐
         │  sync.py             │
         │  - run_sync()        │
         │  - compute_diff()    │
         │  - apply_diff()      │
         │  - write_report()    │
         └──────────────────────┘
```

### Module Responsibilities

| Module | Responsibility |
|---|---|
| `cli.py` | Click CLI entry point; `sync`, `status`, `clear-cache` commands |
| `config.py` | Load and validate config from `.env` + CLI flags; fail fast on missing required values |
| `discogs.py` | Fetch full collection (paginated), normalize to internal data model, filter vinyl-only |
| `lidarr.py` | Read current Lidarr state; add artists/albums |
| `mbz.py` | Resolve Discogs IDs → MusicBrainz UUIDs; manage on-disk lookup cache |
| `sync.py` | Core diff logic; orchestrate fetch → resolve → diff → apply → report |
| `models.py` | Shared dataclasses: `DiscogsItem`, `MbzIds`, `SyncResult` |

---

## 5. The ID Mapping Problem (Critical)

### The Gap

- **Discogs** identifies artists and releases by its own internal integer IDs (e.g. release `742874`, artist `125410`).
- **Lidarr** requires **MusicBrainz UUIDs** as canonical identifiers:
  - `foreignArtistId` = MusicBrainz **Artist** UUID
  - `foreignAlbumId` = MusicBrainz **Release Group** UUID

The two ID spaces are entirely separate. A bridge is required for every item.

### Bridging Strategy: MusicBrainz Relationships

MusicBrainz stores URL relationship links pointing back to Discogs:
- Artist entity → relationship URL: `https://www.discogs.com/artist/{discogs_artist_id}`
- Release entity → relationship URL: `https://www.discogs.com/release/{discogs_release_id}`

Resolution path for a single Discogs collection item:

```
Discogs artist ID ──► MBZ artist search (by URL relation) ──► MBZ Artist UUID
                                                                      │
Discogs release ID ──► MBZ release lookup (by URL relation) ──► MBZ Release
                                                                      │
                                                               navigate to
                                                                      │
                                                               MBZ Release Group UUID
                                                               (= foreignAlbumId for Lidarr)
```

**Key distinction:** A Discogs "release" is a specific pressing. MusicBrainz has the same concept. But Lidarr uses **Release Groups** (the abstract album entity that encompasses all pressings). After finding the MBZ release, we navigate up to its release group to get the correct UUID.

### Fallback Chain

When an exact Discogs-ID-to-MBID match via URL relationship fails:

1. Try a name-based search against MusicBrainz (`search_artists(artist=name)` / `search_release_groups(releasegroup=title, artist=name)`)
2. If still no confident match, log the item to `unresolved.log` with full details and continue
3. Never abort the entire sync for a single unresolvable item

The name-based fallback is less reliable (common names, punctuation differences like "Beatles, The" vs. "The Beatles"), so it's used only as a safety net.

### MusicBrainz Lookup Cache

MusicBrainz enforces a 1 req/sec rate limit. For a collection of 300 records, a cold first run would take ~5–10 minutes of MBZ calls alone. All resolution results — including failures — are persisted to a local JSON cache file (default: `.cache/mbz_cache.json`, gitignored).

Cache key: `discogs_release_id` (integer) → `{artist_mbid, release_group_mbid, resolved_at, status}`

On subsequent runs, cached entries are used directly and no MBZ network calls are made for those items. Only new additions to Discogs require fresh lookups.

---

## 6. Sync Algorithm

```
STARTUP
  1. Load and validate config; fail fast if required values are missing
  2. Test connectivity to Discogs and Lidarr; surface clear errors if unreachable

FETCH
  3. Fetch full Discogs collection (paginated, folder 0 = all releases)
     → List of DiscogsItem {discogs_release_id, discogs_artist_id,
                             artist_name, album_title, year, formats}
  4. Filter: keep only items where any format.name == "Vinyl"
  5. Load MBZ cache from disk

LIDARR STATE
  6. Fetch all artists from Lidarr → Set[foreignArtistId] (MBZ Artist UUIDs)
  7. Fetch all albums from Lidarr  → Set[foreignAlbumId]  (MBZ Release Group UUIDs)
     (includes both monitored and unmonitored — we don't touch either)

RESOLVE & DIFF
  8. For each vinyl DiscogsItem:
     a. If discogs_release_id in MBZ cache → use cached MbzIds; no network call
     b. Else → call MusicBrainz to resolve artist UUID + release group UUID
               → save result to cache (even on failure, to avoid re-querying)
     c. If resolution failed → append to unresolved list; continue to next item
     d. If artist UUID already in Lidarr → mark artist as "exists"; skip add
     e. If album UUID already in Lidarr  → mark album as "exists"; skip add
        (regardless of its current monitoring state — do NOT modify it)

APPLY (skipped in --dry-run mode)
  9. For each artist to add (not yet in Lidarr):
     a. POST /api/v1/artist with addOptions.monitor = "none"
        (artist is monitored=true but no albums auto-monitored)
  10. For each album to add (not yet in Lidarr):
      a. POST /api/v1/album with monitored=True, searchForNewAlbum=False
         (album is added and marked for eventual download; no immediate search)

REPORT
  11. Print rich summary table to terminal:
      - Artists added: N
      - Albums added:  M
      - Already in Lidarr (skipped): P
      - Unresolved (no MBZ match): K
  12. Write JSON report to runs/{timestamp}_report.json
  13. Write/append unresolved items to unresolved.log
  14. Save updated MBZ cache to disk
```

---

## 7. Rate Limits & Throttling

| Service | Limit | Strategy |
|---|---|---|
| Discogs API | 60 req/min (authenticated) | Respect `X-Discogs-Ratelimit-Remaining` response header; sleep on 429 with exponential backoff |
| Lidarr API | None documented (local instance) | No throttling needed |
| MusicBrainz API | 1 req/sec | `musicbrainzngs` enforces this automatically; on-disk cache minimizes call volume on repeat runs |

---

## 8. Decisions Log

| Decision | Choice | Rationale |
|---|---|---|
| Sync direction | Discogs → Lidarr only | One-way; Discogs is authoritative |
| Sync granularity | Album-level | Lidarr only monitors albums you actually own |
| Format filter | Vinyl only | Exclude CDs, cassettes, etc. |
| On-add monitoring | Add as `monitored=True`, no immediate search | Avoids search flood on large initial import |
| Artist monitor mode | `addOptions.monitor = "none"` | Albums are added explicitly; Lidarr won't auto-monitor all discography |
| Re-monitoring | Do NOT flip monitoring state on existing albums | User's manual Lidarr changes are authoritative |
| MBZ cache | Always; persisted to `.cache/mbz_cache.json` | Rate limit compliance + speed on recurring runs |
| Unresolvable items | Log to `unresolved.log`; continue | Non-interactive; sync should run unattended |
| Package manager | `uv` | Fast, single-tool, integrates with pyproject.toml and CI |
| Python version | 3.11 | Matches installed version |
| Run model | Recurring (designed for repeated runs) | New additions to Discogs picked up incrementally |

---

## 9. Project File Structure

```
discogs-lidarr-sync/
├── .github/
│   └── workflows/
│       └── ci.yml                  # Lint + typecheck + test on push/PR
├── .cache/                         # Gitignored; runtime cache files
│   └── mbz_cache.json
├── docs/
│   └── PLAN.md                     ← this file
├── runs/                           # Gitignored; per-run JSON reports
│   └── 2024-01-15T12-00-00_report.json
├── src/
│   └── discogs_lidarr_sync/
│       ├── __init__.py
│       ├── cli.py                  # Click commands: sync, status, clear-cache
│       ├── config.py               # Load + validate settings from .env / CLI
│       ├── models.py               # Dataclasses: DiscogsItem, MbzIds, SyncResult
│       ├── discogs.py              # Fetch + normalize Discogs collection
│       ├── lidarr.py               # Read/write Lidarr state
│       ├── mbz.py                  # MusicBrainz resolver + cache manager
│       └── sync.py                 # Core diff + apply + report logic
├── tests/
│   ├── cassettes/                  # VCR cassettes (recorded HTTP responses)
│   ├── test_discogs.py
│   ├── test_lidarr.py
│   ├── test_mbz.py
│   └── test_sync.py
├── .env.example                    # Template — copy to .env and fill in
├── .gitignore
├── .pre-commit-config.yaml         # ruff + mypy on staged files
├── pyproject.toml                  # All tool config: uv, ruff, mypy, pytest
├── uv.lock                         # Committed lockfile for reproducible installs
└── README.md
```

---

## 10. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Discogs release has no MusicBrainz entry | Medium — rare pressings, regional releases, unofficial bootlegs | Graceful skip + append to `unresolved.log` with enough detail for manual lookup |
| MBZ name search returns wrong artist | Low–Medium — common/ambiguous names | Prefer URL-relationship match; only fall back to name search; log match confidence |
| Lidarr rejects POST due to missing required fields | Low | Validate all required config at startup; fail with actionable error message before any API calls |
| Discogs rate limit hit on large collections | Medium for 500+ records | Backoff on 429; respect `X-Discogs-Ratelimit-Remaining` header |
| Artist already in Lidarr under a slightly different name | Low | Deduplicate by `foreignArtistId` (MBID), never by name |
| MBZ Release vs. Release Group confusion | Medium | Always navigate from release → release group; enforce in `mbz.py` with a dedicated function |
| MBZ cache grows stale (artist/album merged or corrected in MBZ) | Low–Medium over time | `clear-cache` CLI command; cache entries include `resolved_at` timestamp for future TTL support |

---

## 12. Implementation Phases

Each phase ends at a natural commit boundary. Phases are ordered so that every phase compiles cleanly and passes all tests before the next one begins. Code review happens between phases.

---

### Phase 1 — Project Scaffolding

**Goal:** A working project skeleton with the full tooling harness in place. No application logic yet — just structure, config, and automation. After this phase, `uv run pytest`, `uv run ruff check .`, `uv run mypy src/`, and the pre-commit hooks all run cleanly (passing on empty stubs).

**Deliverables:**

- `pyproject.toml` — project metadata, all dependencies declared, tool config sections for `ruff`, `mypy`, `pytest`
- `uv.lock` — committed lockfile
- `.python-version` — pins to 3.11
- `.env.example` — documented template for all required env vars
- `.gitignore` — covers `.env`, `.cache/`, `runs/`, `__pycache__`, `.mypy_cache`, `.ruff_cache`, `.venv`
- `.pre-commit-config.yaml` — `ruff --fix` and `mypy` on staged Python files
- `.github/workflows/ci.yml` — runs `ruff check`, `mypy`, `pytest` on push and pull request
- `src/discogs_lidarr_sync/__init__.py` — empty
- Module stubs (`cli.py`, `config.py`, `models.py`, `discogs.py`, `lidarr.py`, `mbz.py`, `sync.py`) — each with a module docstring and `pass`-body placeholder functions/classes matching the final signatures
- `tests/` directory with an empty `conftest.py` and placeholder test files

**Does not include:** Any real implementation logic.

---

### Phase 2 — Configuration & Data Models

**Goal:** The internal data contract is defined and the configuration layer is bulletproof. This phase is pure Python — no network calls, no external API dependencies. After this phase, every other module has a stable set of types to import from.

**Deliverables:**

- `models.py` — dataclasses with type annotations:
  - `DiscogsItem` — normalized Discogs collection entry (discogs_release_id, discogs_artist_id, artist_name, album_title, year, formats)
  - `MbzIds` — result of a MusicBrainz lookup (artist_mbid, release_group_mbid, status, resolved_at)
  - `SyncResult` — per-item outcome (item, mbz_ids, action taken, error if any)
  - `RunReport` — aggregate summary (counts, unresolved list, run timestamp)
- `config.py` — loads `.env` via `python-dotenv`; exposes a `Settings` dataclass; validates that required fields are present and non-empty; raises a clear `ConfigError` at startup if anything is missing; never crashes mid-run on a config problem
- `tests/test_config.py` — tests for missing fields, valid config, env var override precedence
- `tests/test_models.py` — basic construction and equality tests for all dataclasses

**Does not include:** Any network calls or API client code.

---

### Phase 3 — Discogs Client

**Goal:** The script can fetch and normalize a complete Discogs collection, filtered to vinyl only. This phase is fully testable offline using VCR cassettes.

**Deliverables:**

- `discogs.py` — implemented:
  - `fetch_collection(username, token) -> list[DiscogsItem]` — paginates through all pages of folder 0, respects rate limit headers, retries with backoff on 429
  - `normalize_item(raw) -> DiscogsItem` — maps Discogs API response fields to `DiscogsItem`
  - `is_vinyl(item) -> bool` — returns True if any entry in `formats[].name` is `"Vinyl"`
- `tests/test_discogs.py` — unit tests using recorded VCR cassettes (no live API calls); tests for pagination, vinyl filtering, rate limit handling, malformed responses

**Does not include:** MusicBrainz resolution or any Lidarr interaction.

---

### Phase 4 — MusicBrainz Resolver & Cache

**Goal:** Given a Discogs release ID and artist ID, the resolver can return the corresponding MusicBrainz Artist UUID and Release Group UUID — or record a clean failure. The on-disk cache is fully operational. This is the most technically complex phase.

**Deliverables:**

- `mbz.py` — implemented:
  - `MbzCache` class — loads/saves `.cache/mbz_cache.json`; keyed by `discogs_release_id`; thread-safe reads (writes only at end of run)
  - `resolve_artist(discogs_artist_id, artist_name) -> str | None` — queries MBZ by URL relation first, falls back to name search; returns Artist UUID or None
  - `resolve_release_group(discogs_release_id, album_title, artist_name) -> str | None` — queries MBZ by URL relation; navigates release → release group; returns Release Group UUID or None
  - `resolve(item: DiscogsItem, cache: MbzCache) -> MbzIds` — combines the above; writes result to cache
- `tests/test_mbz.py` — tests for URL-relation match, name-search fallback, release→release-group navigation, cache hit (no network call), cache miss (network call + write), failed resolution

**Does not include:** Any Discogs or Lidarr API interaction.

---

### Phase 5 — Lidarr Client

**Goal:** The script can read the full current state of a Lidarr instance and add artists and albums to it. Fully testable offline.

**Deliverables:**

- `lidarr.py` — implemented:
  - `get_all_artist_mbids(client) -> set[str]` — returns the set of all `foreignArtistId` values currently in Lidarr
  - `get_all_album_mbids(client) -> set[str]` — returns the set of all `foreignAlbumId` values currently in Lidarr (monitored and unmonitored)
  - `add_artist(client, mbid, artist_name, settings) -> None` — looks up by MBID, POSTs with `monitor="none"`, `monitored=True`
  - `add_album(client, mbid, artist_mbid, settings) -> None` — looks up by MBID, POSTs with `monitored=True`, `searchForNewAlbum=False`
  - `LidarrError` — exception class for API failures with enough context to log meaningfully
- `tests/test_lidarr.py` — tests for get methods (empty and populated), add artist (success, duplicate, API error), add album (success, missing artist, API error)

**Does not include:** Any Discogs or MusicBrainz interaction.

---

### Phase 6 — Sync Engine

**Goal:** The core diff-and-apply logic is complete and the script can execute a full end-to-end sync run. The `--dry-run` flag is functional. After this phase, the script does the actual job — even if the CLI is still rough.

**Deliverables:**

- `sync.py` — implemented:
  - `compute_diff(discogs_items, artist_mbids, album_mbids, cache) -> tuple[list[SyncResult], list[SyncResult]]` — returns (items to add, items already present or unresolvable)
  - `apply_diff(to_add, lidarr_client, settings, dry_run) -> RunReport` — adds artists then albums in dependency order (artist before its albums); skips adds in dry-run mode; catches and records per-item errors without aborting the run
  - `write_report(report: RunReport, output_dir) -> None` — writes timestamped JSON to `runs/`
  - `write_unresolved(unresolved: list[SyncResult], path) -> None` — appends to `unresolved.log`
- `tests/test_sync.py` — end-to-end tests using mocked Discogs, Lidarr, and MBZ responses; tests for dry-run (no writes), full run, partial failures, idempotency (second run adds nothing new)

**Does not include:** CLI wiring, progress bars, rich formatting.

---

### Phase 7 — CLI, Output & Documentation

**Goal:** The script is polished, user-facing, and fully documented. This is the release-ready phase.

**Deliverables:**

- `cli.py` — fully implemented Click commands:
  - `sync` — main command; accepts `--dry-run`, `--config`, `--verbose`; shows a `rich` progress bar while processing; prints a summary table at the end
  - `status` — fetches and displays current Discogs collection size and current Lidarr library size without making any changes
  - `clear-cache` — deletes `.cache/mbz_cache.json` with a confirmation prompt
- Rich terminal output throughout — progress bar during MBZ resolution, color-coded summary table (added / skipped / failed)
- `README.md` — complete: prerequisites, installation (`uv sync`), configuration (`.env.example` walkthrough), usage examples, notes on the MBZ cache and `unresolved.log`
- Final pass: ensure all public functions have type annotations; `mypy --strict` passes clean; `ruff` passes clean; `pytest --cov` shows meaningful coverage

**Does not include:** New logic — only wiring, formatting, and documentation.

---

### Phase Summary

| Phase | What it produces | External API calls | Status |
|---|---|---|---|
| 1 — Scaffolding | Tooling harness, empty stubs | None | ✅ Complete |
| 2 — Config & Models | Data contract, settings validation | None | 🔄 In progress |
| 3 — Discogs Client | Collection fetch + vinyl filter | Discogs (mocked in tests) | ⬜ Pending |
| 4 — MBZ Resolver | ID bridging + on-disk cache | MusicBrainz (mocked in tests) | ⬜ Pending |
| 5 — Lidarr Client | Read state + add artist/album | Lidarr (mocked in tests) | ⬜ Pending |
| 6 — Sync Engine | Diff + apply + report + dry-run | All three (mocked in tests) | ⬜ Pending |
| 7 — CLI & Docs | Polish, rich output, README | None (wiring only) | ⬜ Pending |

---

## 13. Out of Scope

- Removing items from Lidarr (explicitly excluded — one-way sync only)
- Pushing data back to Discogs
- Syncing Discogs want list, ratings, or notes
- Managing Lidarr download clients, indexers, or quality profiles (assumed pre-configured)
- A web UI or daemon/service mode (CLI script only)
- Scheduling (user runs manually or via OS scheduler; out of scope for the script itself)
