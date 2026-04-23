# session-stripper tests

TDD scaffolding for the persist-everything PR. Run:

```bash
./tests/run.sh
```

(or `python3 -m unittest discover tests -v` from the skill root).

No external dependencies — Python 3.8+ stdlib only, mirroring the production code's rule.

## What's tested

| File | What it pins down |
|---|---|
| `test_marker_contract.py` | The `<persisted-X>` wire format. Regex matches every kind. `marker_fields()` extracts path/size/summary/preview reliably. Path is relative + shell-safe. **No persist command needed — passes today.** |
| `test_persist_dir_layout.py` | `<sessionId>/tool-results/` for `kind=tool` (CC's native dir). `<sessionId>/persisted/<kind>/` for everything else. Session-scoped, never project-scoped. |
| `test_persist_text.py` | Single + bulk + range + idempotency for the new `persist-text` command. |
| `test_persist_message.py` | `persist-message` collapses all blocks to one marker. Refuses leaf persist. Auto-persists matching `tool_result` when persisting a `tool_use`-bearing message. |
| `test_persist_range.py` | The dispatcher: `--kinds tool,thinking,text,image,message`, `--from/--to`, `--keep-recent`, `--min-chars`, `--summaries-file`. |
| `test_replace_images_marker.py` | New marker shape `<persisted-image sha256="...">` (was `<image sha256="...">`). |
| `test_migrate_persisted.py` | Migration of pre-PR layouts: old `<image>` markers, old `.tool-results/` sidecar location. Idempotent. |
| `test_chain_integrity_after_persist.py` | Cross-cutting: every persist command leaves the parentUuid chain unbroken, slug consistent, timestamps monotonic. |

## Expected state right now (before the persist-everything implementation lands)

| File | Expected |
|---|---|
| `test_marker_contract.py` | **PASS** — independent of persist commands |
| All others | **FAIL with ImportError** until `lib/persist_layout.py`, `lib/persist_text.py`, `lib/persist_message.py`, `lib/persist_range.py`, `lib/migrate_persisted.py` ship and `lib/replace_images.py` adopts the new marker shape |

That's by design: the tests are the contract. They go red on this branch and turn green as the implementation lands in the follow-up PR.

## Manual sanity check

See [`MANUAL_VERIFICATION.md`](MANUAL_VERIFICATION.md). Run **once per marker-shape change** — verifies a real Claude Code session can resume against persisted markers and read the sidecar via the documented path. Not part of the automated suite (no API calls in CI).

## Helpers

`helpers.py` exports:

- `MARKER_RE`, `marker_fields()` — the contract
- `build_session(turns, ...)` — synthetic JSONL session builder; deterministic, in `/tmp/`
- `iter_persisted_markers(path)` — yields every marker in any text-block / tool_result.content
- `assert_chain_valid(path)` — chain integrity invariant
- `get_block_at(path, pos, idx)` — addressable content block fetch
- `resolve_persisted_path(session_path, advertised)` — turns a marker's relative path into an absolute one without symlink expansion (so `/tmp` stays `/tmp` on macOS)

Tests should always work on a copy in `/tmp/` (via `build_session(...)` returning a fresh path, or `copy_to_tmp(real_session)`) — never mutate live sessions.
