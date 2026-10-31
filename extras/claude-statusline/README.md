# Claude Code status line

Not a skill: a one-time install, kept out of `skills/` so it doesn't sit in every session's skill list. To install, point an agent at this file: *"Install the status line from extras/claude-statusline/README.md"*, or follow the steps yourself.

`statusline.sh` renders:

![Status line: Opus 5.5 (medium), weekly usage 95% at pace ×3.0, context 8%](screenshot.png)

| Part | Source field | Meaning |
|---|---|---|
| `Opus 5.5 (medium)` | `.model.display_name`, `.effort.level` | Model (the `(1M context)` suffix is trimmed) and reasoning effort. |
| `[95% …]` | `.rate_limits.seven_day.used_percentage` | Weekly (7-day) plan usage. Present only on Pro/Max subscription auth, after the first response. |
| `×3.0` | `.rate_limits.seven_day.resets_at` | Pace: share of the week left divided by share of the limit left. `1.0` = on track for an even spread. `2.0` = half the budget the calendar says should remain, so spend at half the average rate until reset. Capped at `9.9` (limit exhausted). |
| `8%` bar | `.context_window.total_input_tokens / context_window_size` | Context window fill. |

Colors: usage and context are green under 50%, yellow under 80%, red beyond. Pace is green under ×1.0, yellow to ×1.25, red beyond.

Why remaining-vs-remaining for pace: the alternative, used-share divided by elapsed-share, converges to 1.0 late in the week no matter what (90% used with 24% of the week left reads ×1.2), and it is noisy in the first hours. Remaining-vs-remaining reads ×2.4 there, and starts near ×1.0.

## Install

Requires bash, `jq` (`brew install jq` / `apt install jq`), awk. macOS or Linux; on Windows use WSL or Git Bash.

1. From this folder, copy the script and make it executable:

   ```bash
   cp statusline.sh ~/.claude/statusline.sh
   chmod +x ~/.claude/statusline.sh
   ```

2. Set `statusLine` in `~/.claude/settings.json` without clobbering other keys. If a `statusLine` already exists, show it to the user and ask before replacing it; back the file up first:

   ```bash
   cp ~/.claude/settings.json ~/.claude/settings.json.bak 2>/dev/null
   f=~/.claude/settings.json; [ -s "$f" ] || echo '{}' > "$f"
   jq '.statusLine = {"type":"command","command":"~/.claude/statusline.sh"}' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
   ```

3. Smoke test, then tell the user the bar appears after the next message (restart Claude Code if it doesn't):

   ```bash
   echo '{"model":{"display_name":"Opus"},"context_window":{"total_input_tokens":50000,"context_window_size":200000}}' | ~/.claude/statusline.sh
   ```

   Expect a magenta `Opus` and a green bar with `25%`.

## Customizing

The script reads all fields in one `jq` call near the top, then appends segments to `$out`. To add a field (for example `.session_id` or `.workspace.current_dir`), add it to the `jq` `@sh` string and append a segment before the final `printf`. Full field list: https://code.claude.com/docs/en/statusline.md
