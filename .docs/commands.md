# Commands

Custom commands allow you to create reusable workflows that can be triggered with a simple `/` prefix in the chat input box. These commands help standardize processes across your team and make common tasks more efficient.

Commands are currently in beta. The feature and syntax may change as we continue to improve it.

## How commands work

Commands are defined as plain Markdown files that can be stored in three locations:

1. **Project commands**: Stored in the `.cursor/commands` directory of your project
2. **Global commands**: Stored in the `~/.cursor/commands` directory in your home directory
3. **Team commands**: Created by team admins in the [Cursor Dashboard](https://cursor.com/dashboard?tab=team-content&section=commands) and automatically available to all team members

When you type `/` in the chat input box, Cursor will automatically detect and display available commands from all locations, making them instantly accessible across your workflow.

## Creating commands

1. Create a `.cursor/commands` directory in your project root
2. Add `.md` files with descriptive names (e.g., `review-code.md`, `write-tests.md`)
3. Write plain Markdown content describing what the command should do
4. Commands will automatically appear in the chat when you type `/`

Here's an example of how your commands directory structure might look:

```
.cursor/
└── commands/
    ├── address-github-pr-comments.md
    ├── code-review-checklist.md
    ├── create-pr.md
    ├── light-review-existing-diffs.md
    ├── onboard-new-developer.md
    ├── run-all-tests-and-fix.md
    ├── security-audit.md
    └── setup-new-feature.md
```

## Team commands

Team commands are available on Team and Enterprise plans.

Team admins can create server-enforced custom commands that are automatically available to all team members. This makes it easy to share standardized prompts and workflows across your entire organization.

### Creating team commands

1. Navigate to the [Team Content dashboard](https://cursor.com/dashboard?tab=team-content&section=commands)
2. Click to create a new command
3. Provide:
- **Name**: The command name that will appear after the `/` prefix
- **Description** (optional): Helpful context about what the command does
- **Content**: The Markdown content that defines the command's behavior
4. Save the command

Once created, team commands are immediately available to all team members when they type `/` in the chat input box. Team members don't need to manually sync or download anything - the commands are automatically synchronized.

### Benefits of team commands

- **Centralized management**: Update commands once and changes are instantly available to all team members
- **Standardization**: Ensure everyone uses consistent workflows and best practices
- **Easy sharing**: No need to distribute files or coordinate updates across the team
- **Access control**: Only team admins can create and modify team commands

## Parameters

You can provide additional context to a command in the Agent chat input. Anything you type after the command name is included in the model prompt alongside your provided input. For example:

```
/commit and /pr these changes to address DX-523
```

## Examples

Try these commands in your projects to get a feel for how they work.

### Code review checklist

### Security audit

### Setup new feature

### Create pull request

### Run tests and fix failures

### Onboard new developer

```
# Onboard New Developer## OverviewComprehensive onboarding process to get a new developer up and running quickly.## Steps1. **Environment setup**   - Install required tools   - Set up development environment   - Configure editor and extensions   - Set up git and SSH keys2. **Project familiarization**   - Review project structure   - Understand architecture   - Read key documentation   - Set up local database## Onboarding Checklist- [ ] Development environment ready- [ ] All tests passing- [ ] Can run application locally- [ ] Database set up and working- [ ] First PR submitted
```