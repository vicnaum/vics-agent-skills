# Test fixtures

Most tests build their fixtures dynamically via `helpers.build_session(...)` so each test owns its data and there's no shared mutable state.

Files in this directory are reserved for fixtures that are too elaborate to build in code (e.g. real-world session snapshots). Currently empty; tests against the b856d033 live session look up `~/.claude/projects/-Users-vicnaum-github-vic-mental-model/b856d033-4f84-430b-a34c-f6776c4e0fcd.jsonl` directly and `skip` if it isn't present.
