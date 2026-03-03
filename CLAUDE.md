# CLAUDE.md — discogs-lidarr-sync

## Workflow Rules

- **Always pause before committing.** Present a code review summary and wait
  for explicit approval before running `git commit`. Never auto-commit.
- Work through `docs/PLAN.md` one phase at a time. Each phase ends at a
  natural commit boundary; do not mix phase work.

## Quality Gates (run before every commit)

```bash
uv run ruff check .
uv run mypy src/
uv run pytest
```

All three must pass clean. Fix any failures before committing.

## Project Conventions

- Python 3.11 / `uv` — never use `pip` or `python -m venv` directly.
- One `.env` file (gitignored) holds credentials; `.env.example` is the
  committed template.
- Tests use `responses` for HTTP mocking (no live API calls in CI).
- Log unresolvable items to `unresolved.log`; never abort the run.
- Never remove or unmonitor existing Lidarr entries — sync is additive only.

## Test Layers

| Layer | File | Runs in CI? | Credentials needed |
|---|---|---|---|
| Unit | `test_discogs.py` | Yes | None (responses mocks) |
| VCR cassette | `test_discogs_recorded.py` | Yes (if `DISCOGS_USERNAME` set) | Username only |
| Integration | `test_discogs_integration.py` | No (always skipped) | Token + username |

### Recording VCR cassettes

Re-record when: (a) adding a new `@pytest.mark.vcr` test, or (b) the upstream
API response shape changes and the cassette is stale.

With full credentials in `.env`, run:
```bash
uv run pytest tests/test_discogs_recorded.py --record-mode=all
```
Commit the new/updated files in `tests/cassettes/`.
The auth token and `.env` loading are handled automatically — no manual setup needed.

To enable cassette replay in CI: add `DISCOGS_USERNAME` as a repository
variable under GitHub → Settings → Variables → Actions (not a secret).

## Implementation Reference

Full architecture, decisions, and phase plan: `docs/PLAN.md`
