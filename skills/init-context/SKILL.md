---
name: init-context
description: "Build a complete mental map of a project through phased exploration so you can work effectively without rediscovering things. Use when the user wants to: (1) Onboard onto a new or unfamiliar codebase, (2) Get a full project overview or briefing, (3) Understand project architecture, structure, and open work, (4) Initialize context at the start of a deep work session, (5) Explore and summarize a repository end-to-end. Triggers on requests like \"explore this repo\", \"get familiar with this project\", \"give me a project overview\", \"onboard me\", \"init context\", or any request to deeply understand a codebase before working on it."
---

Build a complete mental map of this project so you can work effectively without rediscovering things. Follow these phases:

## Phase 1: Direct Reading (your own context)
Find and read key documentation files directly — do NOT delegate these, you need them in YOUR context:
- Look for root-level markdown files: README, STATUS, CHANGELOG, CONTRIBUTING, ARCHITECTURE, ROADMAP, REFERENCE, WORKLOG, and any project-specific docs (CLAUDE.md, AGENTS.md, etc.)
- Look for a CLAUDE.md or similar agent instruction file
- Check for a `.claude/` directory with project config or memory files
- If the codebase is large - use a sub-agent with a `layered-summary` skill for the code sources folder to generate (or update, if exists) the AGENTS.md summaries

## Phase 2: Sub-agent Exploration (parallel)
Based on what you learned in Phase 1, spawn sub-agents to explore the major areas of the codebase. Each agent should read all documentation files in its area, skim key source files, and return a structured summary of: what's there, how it works, key decisions, and where to look for what.

Typical areas to explore (adapt based on actual project structure):
- **Each major source directory** — architecture, key modules, entry points, important types/interfaces
- **Tests & CI** — test structure, CI config, how to run tests
- **Config & Infrastructure** — deployment configs, environment setup, docker files, scripts
- **Documentation subdirectories** — any docs/, wiki/, knowledge-base/, research/, or analysis/ folders
- **GitHub/GitLab history** — previous commits list, closed PRs, closed Issues (using `gh` if available)
- **GitHub/GitLab current state** — open issues, open PRs, recent commits and activity (`gh issue list`, `gh pr list` if available)

Adjust the number and focus of sub-agents based on project size. Small project = 2-3 agents. Large monorepo = 5-6 agents, iterative.

## Phase 3: Fill Gaps
After receiving all sub-agent summaries, identify gaps in your understanding:
- Are there key source files referenced in docs that you haven't seen?
- Is the data flow / request lifecycle clear end-to-end?
- Do you understand how to build, test, and run the project?
- Is there anything that you're still missing and need to see?
- Spawn additional sub-agents or read files yourself as needed
- Check recent git log (`git log --oneline -20`) for context on latest changes

## Phase 4: Synthesize
Provide a concise project briefing covering:
- **Project overview**: What is this? What problem does it solve?
- **Current state**: What phase/version? What works? What's in progress?
- **Architecture map**: How the major pieces fit together, data flow, key abstractions
- **Open work**: Issues, PRs, blockers, next steps
- **Key numbers**: Any important metrics, benchmarks, or thresholds
- **Developer guide**: How to build, test, run. Key commands.
- **Where to find things**: Quick reference for common lookups (config, entry points, types, tests)

The goal: after this runs, you should have FULL project context and never need to ask "where is X?" or "what does Y do?" for the common cases.
