# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal, single-user MCP server (stdio) for Garmin Connect, built on the unofficial `python-garminconnect` 0.3.x library and the `mcp` 2.x SDK (`mcp.server.mcpserver.MCPServer`, not the 1.x `FastMCP` import path). The README is in Ukrainian and documents every tool for end users.

## Commands

```bash
uv sync --extra dev                      # install incl. pytest
uv run pytest                            # all tests (offline, no Garmin account needed)
uv run pytest tests/test_core.py::test_run_workout   # single test
uv run garmin-mcp-login                  # one-time interactive login (email, password, MFA) -> token file
uv run garmin-mcp                        # run the stdio server
uv lock --upgrade-package garminconnect  # when Garmin breaks auth/endpoints
```

There is no linter or formatter configured.

## Architecture

`src/garmin_mcp/` has a strict split between network code and pure logic:

- `auth.py` — `get_client()` lazily builds a single shared `Garmin` client from saved tokens only (`~/.garminconnect`, overridable via `GARMINTOKENS`). The server never handles a password; `login_cli()` is the only place one is typed. `server._g()` retries once via `reset_client()` on non-auth failures.
- `server.py` — every MCP tool lives here. Tools fetch raw Garmin JSON and reshape it into compact dicts (km, min/km paces via `_pace`, kg, minutes), dropping empty fields. Sub-sections of composite tools (`get_daily_summary`, `get_activity`, `get_training_status`, `get_performance`) are wrapped in individual `try/except` so partial data still returns. `garmin_get` is an escape hatch restricted to `get_*` methods.
- `strength.py` and `workouts.py` — pure, network-free transformations; these are what the tests cover. Put new non-trivial logic here rather than in `server.py` so it stays testable.

### Write-tool conventions (keep these when adding tools)

- Every mutating tool takes `confirm: bool = False`; without it, return a preview with `"applied": False`. The server `instructions` tell the model to show the preview and only confirm after the user agrees.
- Tool annotations: `RO` for reads, `RW` for edits/creates, `DEL` for destructive operations.
- Before overwriting or deleting Garmin data, save the original with `_backup()` to `~/.garmin-mcp/backups/`.
- `edit_strength_sets` re-reads the activity after the PUT so the response reflects what Garmin actually stored.

### Garmin data quirks encoded in the code

- Strength `weight` (exercise sets) and workout `weightValue` are in **grams**; convert with `GRAMS_PER_KG`.
- The exercise-sets PUT replaces the whole array. `strength.apply_set_edits` deep-copies the payload, edits ACTIVE sets positionally, inserts/removes adjacent REST sets when growing/shrinking, keeps `startTime`s monotonic, and renumbers `messageIndex`.
- Exercise names resolve through `garminconnect.exercises` (the catalog of ~1500 exercises). `resolve_exercise` accepts display names (case-insensitive), `CATEGORY/NAME` keys, or a bare category, and raises `ExerciseNotFound` with suggestions.
- Run workout pace targets are stored as m/s with the slower pace as `targetValueOne`. Steps get a global `stepOrder` that also counts repeat groups (depth-first). Strength workout steps advance `stepOrder` by 3 because `create_strength_set` uses three orders per block.
- Lactate-threshold speed sometimes comes back divided by 10 (`get_performance` corrects it when it is below 1).
- Garmin rate-limits requests. `get_wellness_range` makes about 3 calls per day and is capped at 60 days.
