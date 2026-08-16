# Agent Instructions

## Project State

`IS_LIVE = false`

`IS_LIVE` means this application is currently serving real users in production.

## Backward Compatibility Policy

Until `IS_LIVE = true`, do not propose, design, or implement backward compatibility.

This applies to:
* API contracts
* database schemas
* migrations
* configuration formats
* data models
* service boundaries
* local development workflows

While `IS_LIVE = false`, prefer clean replacement over compatibility layers. Breaking changes are acceptable when they improve the design.

Once `IS_LIVE = true`, this policy is overridden and compatibility, migrations, rollout safety, and user-data preservation must be considered explicitly.

## Architecture Source Of Truth

ADRs in `docs/adr/` are binding architecture decisions.

Implementation must not diverge from accepted ADRs. This applies to:
* service boundaries
* database/storage choices
* API style and contracts
* agent/tool boundaries
* deployment topology
* caching and data-flow strategies

If an implementation plan conflicts with an ADR, stop and update the ADR first. Do not silently simplify, collapse, rename, or bypass ADR-defined components because the project is pre-user or MVP-stage.

## Agent skills

### Issue tracker

PRDs and implementation issues live in GitHub Issues for `zer0plus/talk-to-your-stock`. External PRs are not treated as a request surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default workflow skill labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo. Read `CONTEXT.md`, relevant ADRs in `docs/adr/`, and this `AGENTS.md` before planning or implementation. See `docs/agents/domain.md`.

## Workflow

Use short Codex sessions and durable artifacts:

1. Use `grill-with-docs` for design/schema/product clarification.
2. Update `CONTEXT.md` when domain language is resolved.
3. Add ADRs only for hard-to-reverse, surprising, trade-off-heavy decisions.
4. Use `to-prd` once shared understanding exists.
5. Use `to-issues` to create vertical-slice issues that are demoable or verifiable end-to-end.
6. Implement one unblocked issue per fresh session with `tdd` where practical.
7. Run focused tests/typechecks during implementation and the full relevant suite at the end.
8. Use a fresh `review` session against both repo standards and the originating PRD/issue.

Do not create implementation issues during `grill-with-docs`; use that session to resolve domain language, decisions, and planning shape, then hand off to `to-prd` / `to-issues` for tracker artifacts.

## Pre-PR Contract Audit

Before opening or marking a PR ready, audit every new or changed contract exposed by the PR. In this repo, contracts include:

* OpenAPI paths, request fields, response fields, status codes, and error shapes.
* Web BFF, Agent Service, and Comps Service HTTP calls.
* Returned URLs such as events, exports, table, or trace links.
* Environment variables, readiness checks, and production/local auth behavior.
* Persisted table fields, Message/Run linkage, and service-owned storage boundaries.

For each contract, one of these must be true:

* It is implemented and covered by a boundary-level test through the real public/service interface.
* It is intentionally disabled or returned as `null` until the owning route/capability exists.
* It is not exposed yet.

Do not expose future behavior. If an endpoint does not exist, do not return its URL. If a request field is not honored, do not accept it. If production auth, provider, ADK, database, or service behavior is not implemented, readiness must fail clearly rather than report ready. If the Web BFF calls the Agent Service or Comps Service, the target route must exist and be tested at least once without mocking that service boundary.

## Vertical Acceptance Tests

Every implementation issue and feature must include at least one production-shaped vertical acceptance test. The test starts at the highest public or User-facing boundary in scope and crosses every affected internal service through its real interface, using real service clients, real HTTP routes, real migrations, real repositories, and PostgreSQL.

Unit and component tests may supplement the vertical test, but they never satisfy feature acceptance on their own. A collection of separately mocked boundary tests is not a vertical test.

Vertical acceptance tests must not replace internal services, repositories, persistence, or service clients with mocks, fakes, in-memory implementations, dependency overrides, or fixture data. Ordinary setup helpers and lifecycle fixtures are allowed when they start and configure the real stack rather than substitute for it.

A deterministic substitute may be used only at a genuinely external, nondeterministic, unsafe, or paid boundary such as an LLM, market-data provider, or external identity provider. The implementation issue must explain why the substitute is necessary and record the project owner's explicit approval before implementation begins.

Every implementation issue must name its vertical entry point, internal services crossed, durable store, observable result, and any approved external substitutes. Before a PR is marked ready, its vertical acceptance test must pass against the production-shaped local stack.
