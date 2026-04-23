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
| _pending_ | _pending_ | _pending_ | Run after persist-everything implementation lands. |

## When to re-run

- Any change to the marker tag set (`PERSIST_KINDS`).
- Any change to the inner-body field order or wording.
- Any change to where paths are stored (relative-to-project vs. absolute).

If the manual run fails after a marker change, the failure mode tells you whether to (a) adjust the marker wording or (b) provide better in-marker guidance for the model.
