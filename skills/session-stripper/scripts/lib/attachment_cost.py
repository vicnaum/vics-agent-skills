"""Cost model and drop policy for Claude Code `attachment` JSONL entries.

WHY THIS EXISTS
---------------
`attachment` lines are not transcript decoration — they are real API context:

  * `utils/messages.ts::reorderAttachmentsForAPI()` places attachment entries
    directly into the message array sent to the model.
  * each type is *expanded* at request-build time into a `<system-reminder>`
    user message (see the big `switch (attachment.type)` in messages.ts).
  * `utils/conversationRecovery.ts` restores them from the JSONL on resume, so
    they are re-sent on every request for the life of the session.

Nothing dedupes them. A long session accumulates hundreds of superseded
reminders — e.g. 220 `task_reminder` snapshots of which 182 are byte-identical
consecutive repeats — and pays for all of them on every turn.

RENDERED COST != RECORD SIZE
----------------------------
The stored record and what CC actually sends differ, sometimes by 4x. A
`task_reminder` record carries the full task objects including long
`description` fields, but the renderer emits only `#{id}. [{status}] {subject}`
per task — descriptions are never sent. Sizing the JSON record over-counts the
real cost badly. `rendered_chars()` below models what is actually emitted.

VERSION DRIFT — READ THIS
-------------------------
These rules were read off a Claude Code source checkout that does NOT exactly
match every CC release (it lacks `total_tokens_reminder`, a type CC 2.1.197
emits). Attachment types come and go between versions. Therefore:

  * unknown types fall back to a generic (conservative) size estimate, and
  * unknown types are NEVER dropped by the default policy.

Cost numbers here are an approximation, not a promise.
"""

import json

# Fixed boilerplate the renderer wraps around a reminder, sent even when the
# payload is empty (measured from the source templates, rounded).
_TASK_REMINDER_BOILERPLATE = 460
_EDITED_FILE_BOILERPLATE = 330
_WRAPPER = 40  # <system-reminder> tags + framing


# Types whose renderer returns `[]` — they are recorded in the JSONL but cost
# ZERO API tokens. Deleting them shrinks the file and saves no context, so the
# default run skips them: no point rewiring the chain for nothing.
# (`--include-free` deletes them anyway, for file size.)
ZERO_COST_TYPES = frozenset({
    "already_read_file",
    "command_permissions",
    "edited_image_file",
    "hook_cancelled",
    "hook_error_during_execution",
    "hook_non_blocking_error",
    "hook_system_message",
    "hook_permission_decision",
    "structured_output",
    "dynamic_skill",
    # legacy types kept for old sessions
    "autocheckpointing",
    "background_task_status",
    "todo",
    "task_progress",
    "ultramemory",
})


# ── Drop policy ──────────────────────────────────────────────────────────
#
# KEEP_ALL  — never dropped. Two families:
#   (a) capability/identity: tell the model which tools, agents, skills, MCP
#       servers and memories exist. The *_delta types are CUMULATIVE — dropping
#       any one of them silently removes tool schemas from the model's view.
#   (b) user-referenced content: files/dirs/selections the user attached on
#       purpose. Dropping these destroys information the user put there.
#
# int       — keep only the last N (they are superseded by the newest one).
KEEP_ALL = "keep"

DEFAULT_POLICY = {
    # (a) capability / identity — never drop.
    #
    # The *_delta types are especially dangerous: CC computes each delta by
    # REPLAYING the prior delta attachments still in the message array
    # (attachments.ts: `for (const t of msg.attachment.addedTypes) announced.add(t)`).
    # Delete them and CC considers every tool "new" again, re-emitting a fresh
    # full-size announcement on the next turn — you get the tokens straight
    # back, plus a duplicate. Dropping these is worse than useless.
    "deferred_tools_delta": KEEP_ALL,
    "agent_listing_delta": KEEP_ALL,
    "mcp_instructions_delta": KEEP_ALL,
    "nested_memory": KEEP_ALL,
    "relevant_memories": KEEP_ALL,
    "dynamic_skill": KEEP_ALL,
    "invoked_skills": KEEP_ALL,
    "output_style": KEEP_ALL,
    "critical_system_reminder": KEEP_ALL,
    "plan_mode": KEEP_ALL,
    "plan_mode_reentry": KEEP_ALL,
    "plan_mode_exit": KEEP_ALL,
    "auto_mode": KEEP_ALL,
    "auto_mode_exit": KEEP_ALL,
    # (b) user-referenced content — never drop
    "file": KEEP_ALL,
    "directory": KEEP_ALL,
    "image": KEEP_ALL,
    "pdf_reference": KEEP_ALL,
    "compact_file_reference": KEEP_ALL,
    "plan_file_reference": KEEP_ALL,
    "selected_lines_in_ide": KEEP_ALL,
    "opened_file_in_ide": KEEP_ALL,
    "mcp_resource": KEEP_ALL,

    # (c) queued_command — NEVER DROP. Learned the hard way.
    #
    # This type looks like disposable system chatter and is not. It carries two
    # irreplaceable things:
    #
    #   1. HUMAN INPUT. When the user types while a turn is still running, CC
    #      drains that input mid-turn and stores it ONLY as a queued_command
    #      attachment (commandMode='prompt'). There is no corresponding `user`
    #      message line. An earlier version of this policy kept "the last 2" and
    #      silently deleted 9 real user messages from a live session — the sole
    #      copy of each.
    #
    #   2. BACKGROUND-TASK COMPLETION RECORDS. Task notifications
    #      (commandMode='task-notification') are how CC knows a background shell
    #      finished. Delete them and the next resume reports every task as having
    #      "no completion record" and marks them stopped.
    #
    # It is cheap anyway (~7k tokens on a 4,000-message session). Not worth it.
    "queued_command": KEEP_ALL,

    # superseded-by-latest: only the newest snapshot describes current state
    "task_reminder": 1,
    "todo_reminder": 1,
    "diagnostics": 1,

    # skill_listing: keep the LAST one, never zero. CC's resume path reads a
    # surviving skill_listing to set its `suppressNextSkillListing()` latch
    # (conversationRecovery.ts). Delete every copy and CC just emits a fresh
    # full-size listing on the next resume — net zero, or worse. Keeping one
    # preserves the latch while dropping the redundant duplicates.
    "skill_listing": 1,

    # stale chatter: recent ones may still be relevant, old ones never are
    "hook_success": 2,
    "hook_additional_context": 2,
    "edited_text_file": 3,

    # one per turn, never deduped by CC — grows without bound
    "total_tokens_reminder": 1,
    "token_usage": 1,  # same thing, renamed in later CC versions
    "date_change": 1,
}


def policy_for(att_type, policy=None):
    """Return the drop policy for an attachment type.

    Unknown types default to KEEP_ALL — we never drop what we don't understand.
    """
    table = DEFAULT_POLICY if policy is None else policy
    return table.get(att_type, KEEP_ALL)


# ── Rendered cost model ──────────────────────────────────────────────────

def _slen(value):
    """Length of a string / list-of-strings / arbitrary value, as rendered."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_slen(v) for v in value)
    return len(json.dumps(value, ensure_ascii=False))


def _generic_chars(att):
    """Fallback for unknown types: sum every string leaf in the record.

    Conservative — it may over-count (a record can hold fields the renderer
    never emits), but it never silently reports zero for a type we don't know.
    """
    total = 0

    def walk(v, key=None):
        nonlocal total
        if isinstance(v, str):
            if key not in ("type", "uuid", "toolUseID", "timestamp", "hookEvent"):
                total += len(v)
        elif isinstance(v, dict):
            for k, sub in v.items():
                walk(sub, k)
        elif isinstance(v, list):
            for sub in v:
                walk(sub, key)

    walk(att)
    return total


def rendered_chars(att):
    """Approximate the characters this attachment contributes to the API request.

    Models the per-type renderers in `utils/messages.ts`. Returns 0 for
    attachments the renderer drops entirely (e.g. a hook_success from a hook
    event other than SessionStart/UserPromptSubmit returns `[]`).
    """
    if not isinstance(att, dict):
        return 0
    t = att.get("type")

    if t in ZERO_COST_TYPES:
        # Renderer returns [] — recorded in the transcript, never sent.
        return 0

    if t in ("task_reminder", "todo_reminder"):
        items = att.get("content") or []
        body = sum(
            len("#{}. [{}] {}\n".format(i.get("id"), i.get("status"), i.get("subject")))
            for i in items if isinstance(i, dict)
        )
        return _TASK_REMINDER_BOILERPLATE + body

    if t and t.startswith("hook_"):
        # messages.ts: hook attachments render ONLY for SessionStart and
        # UserPromptSubmit, and only when content is non-empty. Everything
        # else (PostToolUse, Stop, ...) returns [] — zero API cost.
        if att.get("hookEvent") not in ("SessionStart", "UserPromptSubmit"):
            return 0
        content = att.get("content")
        if not content:
            return 0
        return _slen(att.get("hookName")) + _slen(content) + _WRAPPER

    if t == "edited_text_file":
        # Renders the diff snippet only — never the whole file.
        return _EDITED_FILE_BOILERPLATE + _slen(att.get("snippet"))

    if t == "skill_listing":
        content = att.get("content")
        return _slen(content) + _WRAPPER if content else 0

    if t == "nested_memory":
        inner = att.get("content")
        if isinstance(inner, dict):
            return _slen(inner.get("path")) + _slen(inner.get("content")) + _WRAPPER
        return _slen(inner) + _WRAPPER

    if t == "deferred_tools_delta":
        return _slen(att.get("addedLines")) + _WRAPPER

    if t == "agent_listing_delta":
        return _slen(att.get("addedLines")) + _WRAPPER

    if t == "mcp_instructions_delta":
        return _slen(att.get("addedBlocks")) + _WRAPPER

    if t == "queued_command":
        return _slen(att.get("prompt"))

    if t in ("total_tokens_reminder", "token_usage"):
        # Emitted once per turn and never deduped by CC, so these accumulate
        # linearly for the life of the session. Small individually, unbounded
        # in aggregate. (Renamed `token_usage` in later CC versions.)
        return _slen(att.get("text")) or _slen(att.get("content"))

    if t == "date_change":
        return _slen(att.get("content")) + _WRAPPER

    return _generic_chars(att)


def attachment_type(obj):
    """Return the attachment type of a JSONL envelope, or None if not one."""
    if not isinstance(obj, dict) or obj.get("type") != "attachment":
        return None
    att = obj.get("attachment")
    if not isinstance(att, dict):
        return None
    return att.get("type")


def envelope_rendered_chars(obj):
    """Rendered cost of an `attachment` JSONL envelope (0 for other line types)."""
    if not isinstance(obj, dict) or obj.get("type") != "attachment":
        return 0
    return rendered_chars(obj.get("attachment"))
