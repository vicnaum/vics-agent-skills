# Rules

Rules provide system-level instructions to Agent. They bundle prompts, scripts, and more together, making it easy to manage and share workflows across your team.

Cursor supports four types of rules:

- [Project Rules](): Stored in .cursor/rules, version-controlled and scoped to your codebase.
- [User Rules](): Global to your Cursor environment. Used by Agent (Chat).
- [Team Rules](): Team-wide rules managed from the dashboard. Available on Team and Enterprise plans.
- [AGENTS.md](): Agent instructions in markdown format. Simple alternative to
.cursor/rules.

## How rules work

Large language models don't retain memory between completions. Rules provide persistent, reusable context at the prompt level.

When applied, rule contents are included at the start of the model context. This gives the AI consistent guidance for generating code, interpreting edits, or helping with workflows.

## Project rules

Project rules live in `.cursor/rules` as markdown files and are version-controlled. They are scoped using path patterns, invoked manually, or included based on relevance.

Use project rules to:

- Encode domain-specific knowledge about your codebase
- Automate project-specific workflows or templates
- Standardize style or architecture decisions

### Rule file structure

Each rule is a markdown file that you can name anything you want. Cursor supports `.md` and `.mdc` extensions. Use `.mdc` files with frontmatter to specify `description` and `globs` for more control over when rules are applied.

```
.cursor/rules/
  react-patterns.mdc       # Rule with frontmatter (description, globs)
  api-guidelines.md        # Simple markdown rule
  frontend/                # Organize rules in folders
    components.md
```

### Rule anatomy

Each rule is a markdown file with frontmatter metadata and content. Control how rules are applied from the type dropdown which changes properties `description`, `globs`, `alwaysApply`.

Rule TypeDescription`Always Apply`Apply to every chat session`Apply Intelligently`When Agent decides it's relevant based on description`Apply to Specific Files`When file matches a specified pattern`Apply Manually`When @-mentioned in chat (e.g., `@my-rule`)
```
---
globs:
alwaysApply: false
---

- Use our internal RPC pattern when defining services
- Always use snake_case for service names.

@service-template.ts
```

### Creating a rule

Create rules using the `New Cursor Rule` command or going to `Cursor Settings > Rules, Commands`. This creates a new rule file in `.cursor/rules`. From settings you can see all rules and their status.

## Best practices

Good rules are focused, actionable, and scoped.

- Keep rules under 500 lines
- Split large rules into multiple, composable rules
- Provide concrete examples or referenced files
- Avoid vague guidance. Write rules like clear internal docs
- Reuse rules when repeating prompts in chat
- Reference files instead of copying their contents—this keeps rules short and prevents them from becoming stale as code changes

### What to avoid in rules

- **Copying entire style guides**: Use a linter instead. Agent already knows common style conventions.
- **Documenting every possible command**: Agent knows common tools like npm, git, and pytest.
- **Adding instructions for edge cases that rarely apply**: Keep rules focused on patterns you use frequently.
- **Duplicating what's already in your codebase**: Point to canonical examples instead of copying code.

Start simple. Add rules only when you notice Agent making the same mistake repeatedly. Don't over-optimize before you understand your patterns.

Check your rules into git so your whole team benefits. When you see Agent make a mistake, update the rule. You can even tag `@cursor` on a GitHub issue or PR to have Agent update the rule for you.

## Rule file format

Each rule is a markdown file with frontmatter metadata and content. The frontmatter metadata is used to control how the rule is applied. The content is the rule itself.

```
---
description: "This rule provides standards for frontend components and API validation"
alwaysApply: false
---

...rest of the rule content
```

If alwaysApply is true, the rule will be applied to every chat session. Otherwise, the description of the rule will be presented to the Cursor Agent to decide if it should be applied.

## Examples

### Standards for frontend components and API validation

### Templates for Express services and React components

### Automating development workflows and documentation generation

### Adding a new setting in Cursor

First create a property to toggle in `@reactiveStorageTypes.ts`.

Add default value in `INIT_APPLICATION_USER_PERSISTENT_STORAGE` in `@reactiveStorageService.tsx`.

For beta features, add toggle in `@settingsBetaTab.tsx`, otherwise add in `@settingsGeneralTab.tsx`. Toggles can be added as `<SettingsSubSection>` for general checkboxes. Look at the rest of the file for examples.

```
<SettingsSubSection  label="Your feature name"  description="Your feature description"  value={    vsContext.reactiveStorageService.applicationUserPersistentStorage      .myNewProperty ?? false  }  onChange={(newVal) => {    vsContext.reactiveStorageService.setApplicationUserPersistentStorage(      "myNewProperty",      newVal,    );  }}/>
```

To use in the app, import reactiveStorageService and use the property:

```
const flagIsEnabled =  vsContext.reactiveStorageService.applicationUserPersistentStorage    .myNewProperty;
```

Examples are available from providers and frameworks. Community-contributed rules are found across crowdsourced collections and repositories online.

## Team Rules

Team and [Enterprise](/docs/enterprise) plans can create and enforce rules across their entire organization from the [Cursor dashboard](https://cursor.com/dashboard?tab=team-content). Admins can configure whether or not each rule is required for team members.

Team Rules work alongside other rule types and take precedence to ensure organizational standards are maintained across all projects. They provide a powerful way to ensure consistent coding standards, practices, and workflows across your entire team without requiring individual setup or configuration.

### Managing Team Rules

Team administrators can create and manage rules directly from the Cursor dashboard:

Once team rules are created, they automatically apply to all team members and are visible in the dashboard:

### Activation and enforcement

- **Enable this rule immediately**: When checked, the rule is active as soon as you create it. When unchecked, the rule is saved as a draft and does not apply until you enable it later.
- **Enforce this rule**: When enabled, the rule is required for all team members and cannot be disabled in their Cursor settings. When not enforced, team members can toggle the rule off in `Cursor Settings → Rules` under the Team Rules section.

By default, non‑enforced Team Rules can be disabled by users. Use **Enforce this rule** to prevent that.

### Format and how Team Rules are applied

- **Plain text**: Team Rules are free‑form text. They do not use the folder structure of Project Rules and do not support metadata such as `globs`, `alwaysApply`, or rule types.
- **Where they apply**: When a Team Rule is enabled (and not disabled by the user, unless enforced), it is included in the model context for Agent (Chat) across all repositories and projects for that team.
- **Precedence**: Rules are applied in this order: **Team Rules → Project Rules → User Rules**. All applicable rules are merged; earlier sources take precedence when guidance conflicts.

Some teams use enforced rules as part of internal compliance workflows. While this is supported, AI guidance should not be your only security control.

## Importing Rules

You can import rules from external sources to reuse existing configurations or bring in rules from other tools.

### Remote rules (via GitHub)

Import rules directly from any GitHub repository you have access to—public or private.

1. Open **Cursor Settings → Rules, Commands**
2. Click `+ Add Rule` next to `Project Rules`, then select Remote Rule (Github)
3. Paste the GitHub repository URL containing the rule
4. Cursor will pull and sync the rule into your project

Imported rules stay synced with their source repository, so updates to the remote rule are automatically reflected in your project.

### Agent Skills

Cursor can load rules from [Agent Skills](/docs/context/skills), an open standard for extending AI agents with specialized capabilities. These imported skills are always applied as agent-decided rules, meaning Cursor determines when they are relevant based on context.

To enable or disable Agent Skills:

1. Open **Cursor Settings → Rules**
2. Find the **Import Settings** section
3. Toggle **Agent Skills** on or off

Agent Skills are treated as agent-decided rules and cannot be configured as always-apply or manual rules.

## AGENTS.md

`AGENTS.md` is a simple markdown file for defining agent instructions. Place it in your project root as an alternative to `.cursor/rules` for straightforward use cases.

Unlike Project Rules, `AGENTS.md` is a plain markdown file without metadata or complex configurations. It's perfect for projects that need simple, readable instructions without the overhead of structured rules.

Cursor supports AGENTS.md in the project root and subdirectories.

```
# Project Instructions

## Code Style

- Use TypeScript for all new files
- Prefer functional components in React
- Use snake_case for database columns

## Architecture

- Follow the repository pattern
- Keep business logic in service layers
```

### Improvements

### Nested AGENTS.md support

Nested `AGENTS.md` support in subdirectories is now available. You can place `AGENTS.md` files in any subdirectory of your project, and they will be automatically applied when working with files in that directory or its children.

This allows for more granular control of agent instructions based on the area of your codebase you're working in:

```
project/
  AGENTS.md              # Global instructions
  frontend/
    AGENTS.md            # Frontend-specific instructions
    components/
      AGENTS.md          # Component-specific instructions
  backend/
    AGENTS.md            # Backend-specific instructions
```

Instructions from nested `AGENTS.md` files are combined with parent directories, with more specific instructions taking precedence.

## User Rules

User Rules are global preferences defined in **Cursor Settings → Rules** that apply across all projects. They are used by Agent (Chat) and are perfect for setting preferred communication style or coding conventions:

```
Please reply in a concise style. Avoid unnecessary repetition or filler language.
```

## Legacy Cursor Rules

### .cursorrules

The `.cursorrules` (legacy) file in your project root is still supported but **will be deprecated**. We recommend migrating to Project Rules or to `AGENTS.md`.

## FAQ

### Why isn't my rule being applied?

### Can rules reference other rules or files?

### Can I create a rule from chat?

### Do rules impact Cursor Tab or other AI features?

### Do User Rules apply to Inline Edit (Cmd/Ctrl+K)?

No. User Rules are not applied to Inline Edit (Cmd/Ctrl+K). They are only
used by Agent (Chat).