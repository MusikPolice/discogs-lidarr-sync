# Spotify Sync — Feature Plan

> **Status:** Proposed — awaiting approval before implementation.

---

## 1. Goal

Add a `spotify-sync` command that reads the user's Discogs vinyl collection and
builds (or updates) a Spotify playlist containing every track from every owned
album that can be found on Spotify.

The result is a "Vinyl Collection" playlist in the user's Spotify library that
can be downloaded for offline listening.

---

## 2. Feasibility Summary

**Yes, this works exactly as described.** The Spotify Web API fully supports:

- Creating private/public playlists in a user's account
- Adding up to 100 tracks per request (batched automatically)
- Searching for albums by artist + title
- Fetching all tracks from a given album

The one caveat is that **Spotify playlists contain tracks, not albums** — there
is no "add album" endpoint. The implementation must enumerate every track from
each matched album and add them individually. For a vinyl collection of 400–700
albums at ~12 tracks each, this produces 5,000–8,400 tracks — well under
Spotify's hard 10,000-track per-playlist limit.

---

## 3. Library Choice: `spotipy`

**`spotipy`** (PyPI: `spotipy`, GitHub: `spotipy-dev/spotipy`) is the standard
Python wrapper for the Spotify Web API.

- Current version: **2.26.0** (released March 2026), actively maintained
- 5.4k GitHub stars, 33 releases
- Full support for playlist creation, track addition, and search
- Built-in OAuth 2.0 + PKCE flow with automatic token refresh and local caching
- `SpotifyOAuth` handles the browser-redirect dance on first run; tokens are
  cached to disk and refreshed silently on subsequent runs

Key methods used in this feature:

| Method | Purpose |
|---|---|
| `sp.search(q, type="album", limit=10)` | Find a Discogs album on Spotify |
| `sp.album_tracks(album_id, limit=50)` | Get all tracks from a matched album |
| `sp.current_user_playlists()` | Find an existing playlist by name |
| `sp.user_playlist_create(user, name, public, description)` | Create the playlist on first run |
| `sp.playlist_items(playlist_id, limit=100)` | Read existing playlist contents |
| `sp.playlist_add_items(playlist_id, uris)` | Add tracks (max 100 per call) |

---

## 4. Authentication Setup (one-time, manual)

Spotify requires OAuth — the user must authorise the app in a browser the first
time. Setup:

### 4a. Create a Spotify Developer App

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click **Create App**
3. Fill in app name/description (anything, e.g. "discogs-lidarr-sync")
4. Add redirect URI: **`http://127.0.0.1:8888/callback`** (must be exact; note
   `127.0.0.1`, not `localhost` — Spotify no longer accepts the latter)
5. Copy the **Client ID** and **Client Secret** to `.env`

### 4b. First-run OAuth Flow

On the first `spotify-sync` run, spotipy will:
1. Open a browser tab pointing to Spotify's authorisation page
2. After the user grants permission, Spotify redirects to
   `http://127.0.0.1:8888/callback?code=...`
3. spotipy exchanges the code for an access + refresh token pair
4. Tokens are cached to `.cache/spotify_token` (gitignored) and silently
   refreshed on every subsequent run — the user never has to log in again

Required OAuth scopes:
- `playlist-modify-public` — create and add to public playlists
- `playlist-modify-private` — create and add to private playlists (recommended
  default so the playlist isn't visible to followers)

---

## 5. Architectural Design

### 5a. New files

| File | Purpose |
|---|---|
| `src/discogs_lidarr_sync/spotify.py` | All Spotify API interactions |
| `tests/test_spotify.py` | Unit tests (mocked with `responses`) |

### 5b. Modified files

| File | Change |
|---|---|
| `src/discogs_lidarr_sync/config.py` | Add `SpotifySettings` dataclass + `load_spotify_settings()` |
| `src/discogs_lidarr_sync/models.py` | Add `SpotifyMatchResult`, `SpotifyReport` dataclasses |
| `src/discogs_lidarr_sync/cli.py` | Add `spotify-sync` command |
| `pyproject.toml` | Add `spotipy>=2.26` dependency |
| `.env.example` | Add Spotify config section |
| `.gitignore` | Ensure `.cache/spotify_token` is covered |

---

## 6. New Module: `spotify.py`

### 6a. Spotify client construction

```python
def build_spotify_client(settings: SpotifySettings) -> spotipy.Spotify:
    auth = SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope="playlist-modify-public playlist-modify-private",
        cache_path=settings.spotify_token_cache_path,
    )
    return spotipy.Spotify(auth_manager=auth)
```

### 6b. Album search

Each `DiscogsItem` is searched with a typed query:

```
album:"{album_title}" artist:"{artist_name}"
```

If that returns no results, fall back to an untyped query:

```
"{artist_name}" "{album_title}"
```

Both artist name and album title are normalised before comparison (lowercase,
strip punctuation, collapse whitespace) to handle minor formatting differences
between Discogs and Spotify metadata.

The best match is selected by checking whether the normalised Spotify album name
and primary artist name contain the normalised Discogs values. The first
passing result is taken. If no result passes, the item is logged to
`unresolved.log` with a `[spotify]` prefix and processing continues.

Return type: `SpotifyMatchResult` (see §7).

### 6c. Track enumeration

Once a Spotify album ID is found, all tracks are fetched:

```python
def get_album_track_uris(sp: Spotify, album_id: str) -> list[str]:
    uris = []
    offset = 0
    while True:
        page = sp.album_tracks(album_id, limit=50, offset=offset)
        uris.extend(t["uri"] for t in page["items"])
        if not page["next"]:
            break
        offset += 50
    return uris
```

Albums with more than 50 tracks (e.g. box sets, compilations) are handled via
pagination.

### 6d. Playlist management

On first run, the playlist is created:

```python
def get_or_create_playlist(sp, user_id, name, description) -> str:
    # search existing playlists by name
    # create if not found, return playlist_id
```

On subsequent runs, the existing playlist is found by name and reused.

### 6e. Incremental update (additive-only)

Consistent with the rest of this project, the Spotify sync is **additive only**:
tracks are never removed from the playlist. This means:

- If you sell a record and remove it from Discogs, its tracks stay in the playlist
- If you run the command twice, no duplicate tracks are added (existing URIs
  are fetched and deduped against the set to be added)
- Use `--rebuild` to wipe and fully rebuild the playlist from the current
  Discogs collection (see §8)

Existing playlist tracks are fetched in pages of 100, collecting all current
URIs into a set. Only URIs not already in the set are added.

### 6f. Batch add

Tracks are added in batches of 100 (Spotify's API limit per call):

```python
def add_tracks_to_playlist(sp, playlist_id, uris):
    for i in range(0, len(uris), 100):
        sp.playlist_add_items(playlist_id, uris[i:i+100])
```

### 6g. Rate limiting

Spotify returns HTTP 429 with a `Retry-After` header when rate-limited.
spotipy's default behaviour already retries on 429 via its internal `max_retries`
setting; no extra retry logic is needed in application code.

### 6h. Spotify search cache

Searching Spotify for 500+ albums would produce 1,000+ API calls on every run.
To avoid this, Spotify search results are cached to
`.cache/spotify_cache.json` (same pattern as the existing MBZ cache).

Cache key: `f"{artist_name}|||{album_title}"` (normalised)
Cache value: `{"album_id": "...", "track_uris": ["spotify:track:...", ...]}`

Cache entries are considered permanent — Spotify album IDs don't change. On a
warm cache, the entire run requires only the incremental playlist diff calls,
not per-album searches.

---

## 7. Data Models (additions to `models.py`)

```python
class SpotifyAction(StrEnum):
    ADDED       = "added"        # tracks added to playlist
    ALREADY_IN  = "already_in"   # all tracks already in playlist
    NOT_FOUND   = "not_found"    # no Spotify match found for album
    ERROR       = "error"        # API error during search or add

@dataclass
class SpotifyMatchResult:
    item: DiscogsItem
    action: SpotifyAction
    spotify_album_id: str | None
    track_uris: list[str]        # empty if NOT_FOUND or ERROR
    tracks_added: int            # 0 if ALREADY_IN or NOT_FOUND
    error: str | None = None

@dataclass
class SpotifyReport:
    run_at: datetime
    dry_run: bool
    playlist_id: str
    playlist_name: str
    total_vinyl: int
    albums_matched: int
    albums_not_found: int
    albums_already_complete: int
    tracks_added: int
    errors: int
    results: list[SpotifyMatchResult] = field(default_factory=list)
```

---

## 8. New CLI Command: `spotify-sync`

```
discogs-lidarr-sync spotify-sync [OPTIONS]

  Build or update a Spotify playlist from your Discogs vinyl collection.

  On first run this opens a browser tab for Spotify login.  Subsequent runs
  use a cached token and run non-interactively.

Options:
  --dry-run            Show what would be added without modifying the playlist.
  --rebuild            Clear the playlist and rebuild it from scratch.
  --playlist-name NAME Name of the Spotify playlist to create/update.
                       [default: "Vinyl Collection"]
  --config PATH        Path to .env config file.  [default: .env]
  --verbose / -v       Show each album and track count processed.
```

**Output (non-verbose):**

```
Fetching Discogs collection…   ✓ 487 vinyl records
Searching Spotify…             ✓ 451 matched, 36 not found
Updating playlist…             ✓ 842 tracks added

 Spotify Sync Summary
 ─────────────────────────────── ──────
 Albums matched on Spotify            451
 Albums not found on Spotify           36
 Albums already complete                0
 Tracks added to playlist             842
 Errors                                 0
 ─────────────────────────────── ──────
 Total vinyl records                  487
```

---

## 9. Configuration Changes

### New `.env` keys

```bash
# ------------------------------------------------------------------
# Spotify
# Create your app at: https://developer.spotify.com/dashboard
# Add redirect URI: http://127.0.0.1:8888/callback
# ------------------------------------------------------------------
SPOTIFY_CLIENT_ID=your_spotify_client_id_here
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret_here
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
SPOTIFY_PLAYLIST_NAME=Vinyl Collection

# Optional: path to token cache. Default shown.
SPOTIFY_TOKEN_CACHE_PATH=.cache/spotify_token
```

### New `SpotifySettings` dataclass

```python
@dataclass
class SpotifySettings:
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    spotify_playlist_name: str = "Vinyl Collection"
    spotify_token_cache_path: str = ".cache/spotify_token"
    spotify_search_cache_path: str = ".cache/spotify_cache.json"
```

A dedicated `load_spotify_settings(env_file)` function is added alongside the
existing `load_lidarr_settings()`, so `spotify-sync` can be used entirely
independently of Lidarr (no Lidarr keys required to run it).

---

## 10. Integration with Existing Code

The new command plugs into the existing architecture at the same level as `sync`:

1. **Fetch collection** — reuses `fetch_collection()` from `discogs.py` unchanged
2. **No MusicBrainz needed** — Spotify has its own search; no MBZ resolution required
3. **Config** — separate `SpotifySettings` loaded via `load_spotify_settings()`; the
   Discogs token is still read from `Settings` to fetch the collection, but all
   Lidarr keys are optional and not loaded
4. **Logging** — unresolvable albums logged to `unresolved.log` with `[spotify]`
   tag, same pattern as the sync command
5. **Caching** — Spotify search cache follows the same JSON-on-disk pattern as
   `.cache/mbz_cache.json`

Importantly, **`spotify-sync` is fully independent of `sync`** — you do not
need Lidarr installed or configured to use it.

---

## 11. Known Limitations & Edge Cases

| Issue | Handling |
|---|---|
| Album not on Spotify | Logged to `unresolved.log [spotify]`, counted in report |
| Multiple Spotify versions of same album (e.g. explicit + clean, remaster, deluxe) | Take first search result; normalised name match applied; acceptable for this use case |
| Regional availability | Tracks unavailable in user's market are silently skipped by Spotify (the API does not add them); market can be set via optional `SPOTIFY_MARKET` env var |
| Playlist > 10,000 tracks | Plan tracks total before adding; warn if approaching limit; unlikely for typical collection |
| Compilation / Various Artists albums | Search still works but artist matching is skipped for VA; album title match alone is used |
| `--rebuild` with large playlist | Spotify has no "clear playlist" endpoint; must read all URIs and call `remove_all_occurrences_of_items` in batches of 100 |
| Search limit (as of Feb 2026) | Spotify reduced max `limit` to 10 for search; this feature only needs top 1 result, so not affected |
| Token expiry | spotipy handles automatic refresh using the cached refresh token; no user action needed |

---

## 12. Test Plan

### Unit tests (`tests/test_spotify.py`)

| Test | What it verifies |
|---|---|
| `test_search_exact_match` | Typed query returns album — correct ID extracted |
| `test_search_fallback` | Typed query fails, untyped query succeeds |
| `test_search_no_match` | Both queries fail — `NOT_FOUND` action returned |
| `test_get_album_tracks_paginated` | Albums with >50 tracks paginate correctly |
| `test_dedup_existing_playlist_tracks` | URIs already in playlist are not re-added |
| `test_batch_add_splits_at_100` | 250 tracks → 3 API calls (100 + 100 + 50) |
| `test_normalise_artist_name` | "The Beatles" matches "Beatles, The" |
| `test_cache_hit` | Second search for same album uses cache, no API call |

All Spotify HTTP calls mocked with `responses` (same library already in dev deps).

### VCR cassette tests (optional)

If a `SPOTIFY_USERNAME` env var is present in CI, a cassette test records and
replays a real search response, providing a contract test against the live API
shape.

---

## 13. Phase Plan

This is a single self-contained phase with one natural commit boundary.

| Step | Work |
|---|---|
| 1 | Add `spotipy` to `pyproject.toml`; add mypy override for `spotipy.*` |
| 2 | Add `SpotifySettings` + `load_spotify_settings()` to `config.py` |
| 3 | Add `SpotifyAction`, `SpotifyMatchResult`, `SpotifyReport` to `models.py` |
| 4 | Implement `spotify.py` (client, search, cache, playlist management) |
| 5 | Add `spotify-sync` command to `cli.py` |
| 6 | Update `.env.example` with Spotify section |
| 7 | Write unit tests in `tests/test_spotify.py` |
| 8 | Pass all quality gates (`ruff`, `mypy`, `pytest`) |
| 9 | Pause for code review, then commit |

---

## 14. Open Questions

Before implementation, confirm:

1. **Playlist visibility**: Should the created playlist be **public** or
   **private**? Private means only you see it; public means your followers can
   see it. Recommendation: private (can be changed in the Spotify app later).

2. **Additive-only vs. two-way**: Should the command ever *remove* tracks from
   the playlist (e.g. if you sell a record and remove it from Discogs)?
   The rest of this project is additive-only; same stance recommended here for
   consistency. The `--rebuild` flag provides a manual escape hatch.

3. **Playlist name**: "Vinyl Collection" as default? You can configure it with
   `SPOTIFY_PLAYLIST_NAME` but a default is needed.

4. **Multiple playlists**: Is one playlist enough, or would you want per-genre
   or per-decade playlists? The current plan creates one flat playlist. Multiple
   would require Discogs genre data, which is a future extension.
