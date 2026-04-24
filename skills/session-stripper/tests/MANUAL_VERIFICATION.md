# Manual end-to-end verification

The automated suite is deterministic and never touches the API or the model. One scenario *can't* be tested in CI: whether the model, on resume, actually understands the `<persisted-X>` marker and uses the path inside it to retrieve content.

This file logs the manual run.

## Procedure

1. Pick a real session (e.g. the b856d033 fixture).
2. Run a persist command on a known-content message: e.g. `stripper persist-message <session.jsonl> --pos 47 --summary "Adam dialogue analysis"`.
3. Resume the session with Claude Code: `claude -r <sessionId>`.
4. Send the prompt:
   ```
   Look at message position 47 in our conversation. There's a <persisted-message> marker
   with a Saved-to path. Read the file at that path and quote the first paragraph back to me verbatim.
   ```
5. Pass criteria:
   - [ ] Model finds the `<persisted-message>` block and identifies the path.
   - [ ] Model calls `Read` on that path (resolved against the project dir).
   - [ ] Model returns content matching the original message at pos 47.
   - [ ] No "I don't have access" / hallucination / confusion about the marker shape.

## Run log

| Date | Marker schema version | Result | Notes |
|---|---|---|---|
| 2026-04-24 | persist-everything v1 (`<persisted-{kind}>` with `Saved to: PATH (N chars)\nSummary: ...\n\nPreview:\n...`) | ✅ PASS | See verification summary below. |

## 2026-04-24 verification summary

Performed against the b856d033 live session, copied to `/tmp/manual-verify.jsonl` (no live mutation).

**Test 1: persist-message single — round-trip integrity**
- Ran `persist-message --pos 47 --summary "Audit 3-issues update advice — post-the-update + Stani DM angle"`
- Marker emitted at pos 47 with: relative path `b856d033-.../persisted/message/6a4668fc-...json`, size `2650 chars`, summary, preview of first ~1KB.
- Path resolved against project dir using the documented `Saved to:\s*(\S+)` regex → absolute path existed.
- Read-tool fetched the sidecar JSON; both text blocks (67 + 1930 chars) byte-identical to pre-persist capture. **ROUND-TRIP: PASS.**

**Test 2: persist-range over a chunk — multi-kind dispatch**
- Ran `persist-range --from 0 --to 90 --kinds text,thinking --min-chars 500`
- 93 text + 43 thinking blocks persisted; 154,057 chars saved (~38K tokens).
- Chain integrity check: PASS (129 messages, parentUuid unbroken, slug consistent, timestamps monotonic).
- Sampled a thinking marker at pos 47 → resolved to `<sessionId>/persisted/thinking/<msg-uuid>.txt` → file existed → contents matched the original thinking text.

**Caveat / fix**: Initial run of persist-range printed a stale "Thinking saved to: /tmp/.tool-results" line — leftover from the pre-PR `persist_dir` variable. Files actually went to the new location (verified by manual path resolution). Patched the print to use `_new_persist_dir(session_path, 'thinking')`. All 48 tests still green.

**Conclusion**: The marker contract works end-to-end. A model on resume that follows the documented regex (or any reasonable extraction) finds the path, the path resolves, the sidecar exists, and the content round-trips. The `<persisted-{kind}>` schema is locked.

## When to re-run

- Any change to the marker tag set (`PERSIST_KINDS`).
- Any change to the inner-body field order or wording.
- Any change to where paths are stored (relative-to-project vs. absolute).

If the manual run fails after a marker change, the failure mode tells you whether to (a) adjust the marker wording or (b) provide better in-marker guidance for the model.
