# Domain Docs

How engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- `CONTEXT.md` at the repo root.
- `docs/adr/` for binding architecture decisions that touch the area being worked on.
- `AGENTS.md` for project-specific implementation rules and backward-compatibility policy.

If any of these files do not exist, proceed silently. The domain model is created and updated lazily when terms or decisions are resolved.

## File structure

This repo uses a single-context layout:

```text
/
├── CONTEXT.md
├── AGENTS.md
├── docs/
│   ├── agents/
│   └── adr/
└── <service folders>
```

## Use the glossary's vocabulary

When output names a domain concept in an issue title, PRD, test name, schema, module, or code review finding, use the term as defined in `CONTEXT.md`. Do not drift to synonyms that the glossary explicitly avoids.

If the needed concept is not in the glossary, either reconsider whether the project already has a term for it or note it for `domain-modeling` / `grill-with-docs`.

## Flag ADR conflicts

ADRs in `docs/adr/` are binding. If a proposal or implementation contradicts an existing ADR, surface the conflict explicitly and update the ADR before implementing.
