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
