# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` when needed and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v`. `gh` does this automatically when run inside this clone.

## Pull requests as a triage surface

**PRs as a request surface: no.**

External PRs are not treated as feature requests for this solo MVP workflow. Triage runs against GitHub Issues unless this file is edited later.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## Implementation issue test contract

Every implementation issue must include acceptance criteria for a production-shaped vertical test. The issue must identify:

- the highest public or User-facing entry point exercised;
- every internal service crossed through its real interface;
- the real migrated PostgreSQL persistence boundary;
- the final observable and persisted result; and
- each proposed deterministic substitute for an external dependency, including the reason and the project owner's recorded approval.

Internal services, service clients, HTTP routes, repositories, migrations, and PostgreSQL must not be replaced by mocks, fakes, in-memory implementations, dependency overrides, or fixture data in the vertical acceptance test. Setup fixtures may start and configure the real stack. Unit and component tests are supplemental and cannot replace this issue-level acceptance test.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
