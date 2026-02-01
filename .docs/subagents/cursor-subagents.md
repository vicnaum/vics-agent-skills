# Subagents

Subagents are specialized AI assistants that Cursor's agent can delegate tasks to. Each subagent operates in its own context window, handles specific types of work, and returns its result to the parent agent. Use subagents to break down complex tasks, do work in parallel, and preserve context in the main conversation.

- [Context isolation](): Each subagent has its own context window. Long research or exploration tasks don't consume space in your main conversation.
- [Parallel execution](): Launch multiple subagents simultaneously. Work on different parts of your codebase without waiting for sequential completion.
- [Specialized expertise](): Configure subagents with custom prompts, tool access, and models for domain-specific tasks.
- [Reusability](): Define custom subagents and use them across projects.

If you're on a legacy request-based plan, you must enable [Max Mode](/docs/context/max-mode) to use subagents. Usage-based plans have subagents enabled by default.

## How subagents work

When Agent encounters a complex task, it can launch a subagent automatically. The subagent receives a prompt with all necessary context, works autonomously, and returns a final message with its results.

Subagents start with a clean context. The parent agent includes relevant information in the prompt since subagents don't have access to prior conversation history.

### Foreground vs background

Subagents run in one of two modes:

ModeBehaviorBest for**Foreground**Blocks until the subagent completes. Returns the result immediately.Sequential tasks where you need the output.**Background**Returns immediately. The subagent works independently.Long-running tasks or parallel workstreams.
## Built-in subagents

Cursor includes three built-in subagents that handle context-heavy operations automatically. These subagents were designed based on analysis of agent conversations where context window limits were hit.

SubagentPurposeWhy it's a subagent**Explore**Searches and analyzes codebasesCodebase exploration generates large intermediate output that would bloat the main context. Uses a faster model to run many parallel searches.**Bash**Runs series of shell commandsCommand output is often verbose. Isolating it keeps the parent focused on decisions, not logs.**Browser**Controls browser via MCP toolsBrowser interactions produce noisy DOM snapshots and screenshots. The subagent filters this down to relevant results.
### Why these subagents exist

These three operations share common traits: they generate noisy intermediate output, benefit from specialized prompts and tools, and can consume significant context. Running them as subagents solves several problems:

- **Context isolation** — Intermediate output stays in the subagent. The parent only sees the final summary.
- **Model flexibility** — The explore subagent uses a faster model by default. This enables running 10 parallel searches in the time a single main-agent search would take.
- **Specialized configuration** — Each subagent has prompts and tool access tuned for its specific task.
- **Cost efficiency** — Faster models cost less. Isolating token-heavy work in subagents with appropriate model choices reduces overall cost.

You don't need to configure these subagents. Agent uses them automatically when appropriate.

## When to use subagents

Use subagents when...Use skills when...You need context isolation for long research tasksThe task is single-purpose (generate changelog, format)Running multiple workstreams in parallelYou want a quick, repeatable actionThe task requires specialized expertise across many stepsThe task completes in one shotYou want an independent verification of workYou don't need a separate context window
If you find yourself creating a subagent for a simple, single-purpose task like "generate a changelog" or "format imports," consider using a [skill](/docs/context/skills) instead.

## Quick start

Agent automatically uses subagents when appropriate. You can also create a custom subagent by asking Agent:

Create a subagent file at .cursor/agents/verifier.md with YAML frontmatter (name, description) followed by the prompt. The verifier subagent should validate completed work, check that implementations are functional, run tests, and report what passed vs what's incomplete.

[Try in Cursor](cursor://anysphere.cursor-deeplink/prompt?text=Create%20a%20subagent%20file%20at%20.cursor%2Fagents%2Fverifier.md%20with%20YAML%20frontmatter%20(name%2C%20description)%20followed%20by%20the%20prompt.%20The%20verifier%20subagent%20should%20validate%20completed%20work%2C%20check%20that%20implementations%20are%20functional%2C%20run%20tests%2C%20and%20report%20what%20passed%20vs%20what's%20incomplete.)
For more control, create custom subagents manually in your project or user directory.

## Custom subagents

Define custom subagents to encode specialized knowledge, enforce team standards, or automate repetitive workflows.

### File locations

TypeLocationScope**Project subagents**`.cursor/agents/`Current project only`.claude/agents/`Current project only (Claude compatibility)`.codex/agents/`Current project only (Codex compatibility)**User subagents**`~/.cursor/agents/`All projects for current user`~/.claude/agents/`All projects for current user (Claude compatibility)`~/.codex/agents/`All projects for current user (Codex compatibility)
Project subagents take precedence when names conflict. When multiple locations contain subagents with the same name, `.cursor/` takes precedence over `.claude/` or `.codex/`.

### File format

Each subagent is a markdown file with YAML frontmatter:

```
---
name: security-auditor
description: Security specialist. Use when implementing auth, payments, or handling sensitive data.
model: inherit
---

You are a security expert auditing code for vulnerabilities.

When invoked:
1. Identify security-sensitive code paths
2. Check for common vulnerabilities (injection, XSS, auth bypass)
3. Verify secrets are not hardcoded
4. Review input validation and sanitization

Report findings by severity:
- Critical (must fix before deploy)
- High (fix soon)
- Medium (address when possible)
```

### Configuration fields

FieldRequiredDescription`name`NoUnique identifier. Use lowercase letters and hyphens. Defaults to filename without extension.`description`NoWhen to use this subagent. Agent reads this to decide delegation.`model`NoModel to use: `fast`, `inherit`, or a specific model ID. Defaults to `inherit`.`readonly`NoIf `true`, the subagent runs with restricted write permissions.`is_background`NoIf `true`, the subagent runs in the background without waiting for completion.
## Using subagents

### Automatic delegation

Agent proactively delegates tasks based on:

- The task complexity and scope
- Custom subagent descriptions in your project
- Current context and available tools

Include phrases like "use proactively" or "always use for" in your description field to encourage automatic delegation.

### Explicit invocation

Request a specific subagent by using the `/name` syntax in your prompt:

```
> /verifier confirm the auth flow is complete
> /debugger investigate this error
> /security-auditor review the payment module
```

You can also invoke subagents by mentioning them naturally:

```
> Use the verifier subagent to confirm the auth flow is complete
> Have the debugger subagent investigate this error
> Run the security-auditor subagent on the payment module
```

### Parallel execution

Launch multiple subagents concurrently for maximum throughput:

```
> Review the API changes and update the documentation in parallel
```

Agent sends multiple Task tool calls in a single message, so subagents run simultaneously.

## Resuming subagents

Subagents can be resumed to continue previous conversations. This is useful for long-running tasks that span multiple invocations.

Each subagent execution returns an agent ID. Pass this ID to resume the subagent with full context preserved:

```
> Resume agent abc123 and analyze the remaining test failures
```

Background subagents write their state as they run. You can resume a subagent after it completes to continue the conversation with preserved context.

## Common patterns

### Verification agent

A verification agent independently validates whether claimed work was actually completed. This addresses a common issue where AI marks tasks as done but implementations are incomplete or broken.

```
---
name: verifier
description: Validates completed work. Use after tasks are marked done to confirm implementations are functional.
model: fast
---

You are a skeptical validator. Your job is to verify that work claimed as complete actually works.

When invoked:
1. Identify what was claimed to be completed
2. Check that the implementation exists and is functional
3. Run relevant tests or verification steps
4. Look for edge cases that may have been missed

Be thorough and skeptical. Report:
- What was verified and passed
- What was claimed but incomplete or broken
- Specific issues that need to be addressed

Do not accept claims at face value. Test everything.
```

Create a subagent file at .cursor/agents/verifier.md with YAML frontmatter containing name, description, and model: fast. The description should be 'Validates completed work. Use after tasks are marked done to confirm implementations are functional.' The prompt body should instruct it to be skeptical, verify implementations actually work by running tests, and look for edge cases.

[Try in Cursor](cursor://anysphere.cursor-deeplink/prompt?text=Create%20a%20subagent%20file%20at%20.cursor%2Fagents%2Fverifier.md%20with%20YAML%20frontmatter%20containing%20name%2C%20description%2C%20and%20model%3A%20fast.%20The%20description%20should%20be%20'Validates%20completed%20work.%20Use%20after%20tasks%20are%20marked%20done%20to%20confirm%20implementations%20are%20functional.'%20The%20prompt%20body%20should%20instruct%20it%20to%20be%20skeptical%2C%20verify%20implementations%20actually%20work%20by%20running%20tests%2C%20and%20look%20for%20edge%20cases.)
This pattern is useful for:

- Validating that features work end-to-end before marking tickets complete
- Catching partially implemented functionality
- Ensuring tests actually pass (not just that test files exist)

### Orchestrator pattern

For complex workflows, a parent agent can coordinate multiple specialist subagents in sequence:

1. **Planner** analyzes requirements and creates a technical plan
2. **Implementer** builds the feature based on the plan
3. **Verifier** confirms the implementation matches requirements

Each handoff includes structured output so the next agent has clear context.

## Example subagents

### Debugger

```
---
name: debugger
description: Debugging specialist for errors and test failures. Use when encountering issues.
---

You are an expert debugger specializing in root cause analysis.

When invoked:
1. Capture error message and stack trace
2. Identify reproduction steps
3. Isolate the failure location
4. Implement minimal fix
5. Verify solution works

For each issue, provide:
- Root cause explanation
- Evidence supporting the diagnosis
- Specific code fix
- Testing approach

Focus on fixing the underlying issue, not symptoms.
```

Create a subagent file at .cursor/agents/debugger.md with YAML frontmatter containing name and description. The debugger subagent should specialize in root cause analysis: capture stack traces, identify reproduction steps, isolate failures, implement minimal fixes, and verify solutions.

[Try in Cursor](cursor://anysphere.cursor-deeplink/prompt?text=Create%20a%20subagent%20file%20at%20.cursor%2Fagents%2Fdebugger.md%20with%20YAML%20frontmatter%20containing%20name%20and%20description.%20The%20debugger%20subagent%20should%20specialize%20in%20root%20cause%20analysis%3A%20capture%20stack%20traces%2C%20identify%20reproduction%20steps%2C%20isolate%20failures%2C%20implement%20minimal%20fixes%2C%20and%20verify%20solutions.)
### Test runner

```
---
name: test-runner
description: Test automation expert. Use proactively to run tests and fix failures.
---

You are a test automation expert.

When you see code changes, proactively run appropriate tests.

If tests fail:
1. Analyze the failure output
2. Identify the root cause
3. Fix the issue while preserving test intent
4. Re-run to verify

Report test results with:
- Number of tests passed/failed
- Summary of any failures
- Changes made to fix issues
```

Create a subagent file at .cursor/agents/test-runner.md with YAML frontmatter containing name and description (mentioning 'Use proactively'). The test-runner subagent should proactively run tests when it sees code changes, analyze failures, fix issues while preserving test intent, and report results.

[Try in Cursor](cursor://anysphere.cursor-deeplink/prompt?text=Create%20a%20subagent%20file%20at%20.cursor%2Fagents%2Ftest-runner.md%20with%20YAML%20frontmatter%20containing%20name%20and%20description%20(mentioning%20'Use%20proactively').%20The%20test-runner%20subagent%20should%20proactively%20run%20tests%20when%20it%20sees%20code%20changes%2C%20analyze%20failures%2C%20fix%20issues%20while%20preserving%20test%20intent%2C%20and%20report%20results.)
## Best practices

- **Write focused subagents** — Each subagent should have a single, clear responsibility. Avoid generic "helper" agents.
- **Invest in descriptions** — The `description` field determines when Agent delegates to your subagent. Spend time refining it. Test by making prompts and checking if the right subagent gets triggered.
- **Keep prompts concise** — Long, rambling prompts dilute focus. Be specific and direct.
- **Add subagents to version control** — Check `.cursor/agents/` into your repository so the team benefits.
- **Start with Agent-generated agents** — Let Agent help you draft the initial configuration, then customize.
- **Use hooks for file output** — If you need subagents to produce structured output files, consider using [hooks](/docs/agent/hooks) to process and save their results consistently.

### Anti-patterns to avoid

**Don't create dozens of generic subagents.** Having 50+ subagents with vague instructions like "helps with coding" is ineffective. Agent won't know when to use them, and you'll waste time maintaining them.

- **Vague descriptions** — "Use for general tasks" gives Agent no signal about when to delegate. Be specific: "Use when implementing authentication flows with OAuth providers."
- **Overly long prompts** — A 2,000-word prompt doesn't make a subagent smarter. It makes it slower and harder to maintain.
- **Duplicating slash commands** — If a task is single-purpose and doesn't need context isolation, use a [slash command](/docs/agent/chat/commands) instead.
- **Too many subagents** — Start with 2-3 focused subagents. Add more only when you have clear, distinct use cases.

## Managing subagents

### Creating subagents

The easiest way to create a subagent is to ask Agent to create one for you:

Create a subagent file at .cursor/agents/security-reviewer.md with YAML frontmatter containing name and description. The security-reviewer subagent should check code for common vulnerabilities like injection, XSS, and hardcoded secrets.

[Try in Cursor](cursor://anysphere.cursor-deeplink/prompt?text=Create%20a%20subagent%20file%20at%20.cursor%2Fagents%2Fsecurity-reviewer.md%20with%20YAML%20frontmatter%20containing%20name%20and%20description.%20The%20security-reviewer%20subagent%20should%20check%20code%20for%20common%20vulnerabilities%20like%20injection%2C%20XSS%2C%20and%20hardcoded%20secrets.)
You can also create subagents manually by adding markdown files to `.cursor/agents/` (project) or `~/.cursor/agents/` (user).

### Viewing subagents

Agent includes all custom subagents in its available tools. You can see which subagents are configured by checking the `.cursor/agents/` directory in your project.

## Performance and cost

Subagents have trade-offs. Understanding them helps you decide when to use them.

BenefitTrade-offContext isolationStartup overhead (each subagent gathers its own context)Parallel executionHigher token usage (multiple contexts running simultaneously)Specialized focusLatency (may be slower than main agent for simple tasks)
### Token and cost considerations

- **Subagents consume tokens independently** — Each subagent has its own context window and token usage. Running five subagents in parallel uses roughly five times the tokens of a single agent.
- **Evaluate the overhead** — For quick, simple tasks, the main agent is often faster. Subagents shine for complex, long-running, or parallel work.
- **Subagents can be slower** — The benefit is context isolation, not speed. A subagent doing a simple task may be slower than the main agent because it starts fresh.

## FAQ

### What are the built-in subagents?

### Can subagents launch other subagents?

### How do I see what a subagent is doing?

### What happens if a subagent fails?

### Can I use MCP tools in subagents?

### How do I debug a misbehaving subagent?

### Why can't I use subagents on my plan?

If you're on a legacy request-based plan, you must enable [Max Mode](/docs/context/max-mode) to use subagents. Enable Max Mode from the model picker, then try again. Usage-based plans have subagents enabled by default.