# Agent Skills

Agent Skills are only available in the nightly release channel.

To switch channels, open Cursor Settings (Cmd+Shift+JCtrl+Shift+J), select
**Beta**, then set your update channel to **Nightly**. You may need to restart
Cursor after the update.

Agent Skills is an open standard for extending AI agents with specialized capabilities. Skills package domain-specific knowledge and workflows that agents can use to perform specific tasks.

## What are skills?

A skill is a portable, version-controlled package that teaches agents how to perform domain-specific tasks.

- [Portable](): Skills work across any agent that supports the Agent Skills standard.
- [Version-controlled](): Skills are stored as files and can be tracked in your repository, or installed via GitHub repository links.

## How skills work

When Cursor starts, it automatically discovers skills from skill directories and makes them available to Agent. The agent is presented with available skills and decides when they are relevant based on context.

Skills can also be manually invoked by typing `/` in Agent chat and searching for the skill name.

## Skill directories

Skills are automatically loaded from these locations:

LocationScope`.cursor/skills/`Project-level`.claude/skills/`Project-level (Claude compatibility)`~/.cursor/skills/`User-level (global)`~/.claude/skills/`User-level (global)
Each skill should be a folder containing a `SKILL.md` file:

```
.cursor/
└── skills/
    └── my-skill/
        └── SKILL.md
```

## SKILL.md file format

Each skill is defined in a `SKILL.md` file with YAML frontmatter:

```
---
name: my-skill
description: Short description of what this skill does and when to use it.
---

# My Skill

Detailed instructions for the agent.

## When to Use

- Use this skill when...
- This skill is helpful for...

## Instructions

- Step-by-step guidance for the agent
- Domain-specific conventions
- Best practices and patterns
```

### Frontmatter fields

FieldRequiredDescription`description`YesShort description shown in menus. Used by the agent to determine when to apply the skill.`name`NoHuman-readable name. If omitted, the parent folder name is used.
## Viewing skills

To view discovered skills:

1. Open **Cursor Settings** (Cmd+Shift+J on Mac, Ctrl+Shift+J on Windows/Linux)
2. Navigate to **Rules**
3. Skills appear in the **Agent Decides** section

## Installing skills from GitHub

You can import skills from GitHub repositories:

1. Open **Cursor Settings → Rules**
2. In the **Project Rules** section, click **Add Rule**
3. Select **Remote Rule (Github)**
4. Enter the GitHub repository URL

## Learn more

Agent Skills is an open standard. Learn more at [agentskills.io](https://agentskills.io).