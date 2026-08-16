# SPEC-59 - Product invariants and lightweight complexity control

- **Status:** Ready
- **Severity:** Preventive maintainability control
- **Effort:** S to M
- **Depends on:** None; should land before or alongside the cleanup

## Objective

Prevent Code Tribunal from expanding into multiple overlapping systems again without
rebuilding the large repository-policing framework that was just removed.

Use explicit product decisions, a concise coding-agent map, pull-request disclosure, and
expiry ownership. Do not create a generalized complexity-budget engine.

## 1. Add ADR-0003: product invariants and complexity envelope

Create `docs/decisions/0003-product-invariants-and-complexity-envelope.md` and index it.

The ADR must record these accepted dimensions:

- first-party reviewers: Claude, Codex, OpenCode, Cursor;
- stages: one review round and one critique round;
- platforms: GitHub and GitLab;
- decision authority: deterministic reducer;
- finding policy: two independent supporters produce an informational surfaced finding;
- output: surfaced threads plus summary fallback;
- state: platform-hosted persistent state and thread reconciliation;
- adapter loading: explicit trusted registry, no dynamic plugins;
- third-party CLI trust: pinned dependencies are trusted to implement their documented
  behavior; Code Tribunal verifies its own invocation and credential isolation;
- workflow authority: one canonical GitHub template with one parity implementation;
- release authority: `release/release-inputs.json` plus source-bound evidence records.

A new ADR is required before adding:

- a fifth bundled reviewer;
- a third model stage or recursive critique;
- a third platform;
- a second state backend family;
- a new public artifact family;
- dynamic plugin loading;
- another decision implementation;
- another canonical workflow or release authority.

This is a review requirement, not an automated ban.

## 2. Add one concise root `AGENTS.md`

Create a tool-neutral coding-agent guide containing:

- the product invariants above;
- trust boundaries and major entry points;
- source-of-truth map;
- allowed dependency direction;
- public compatibility surface;
- required verification commands;
- rules against preserving private seams;
- rules for temporary compatibility code;
- instruction to update or delete completed specs.

Keep it concise and link to existing architecture, security, testing, and release docs. Do not
copy those documents into the agent guide. Tool-specific agent files may point to this file
but must not duplicate the full guidance.

## 3. Extend the pull-request template

Add an `Architecture delta` section:

```text
- Product dimensions added:
- Public config keys added/removed:
- Artifact schemas added/removed:
- Workflow jobs or matrices added/removed:
- Platform protocol methods added/removed:
- Runtime dependencies added/removed:
- Compatibility paths added, with removal target:
- Existing mechanism considered:
```

Add a prompt asking whether the change crosses more than one major boundary:

- input preparation;
- adapter execution;
- critique;
- consensus;
- posting/state;
- platform transport;
- release/supply chain;
- documentation/tooling.

Cross-boundary work is allowed, but the author must state why it cannot be staged as
contract, migration, and deletion.

## 4. Track temporary compatibility paths

Create `docs/development/temporary-compatibility.md` with one current table:

| ID | Owner | Introduced | Code references | Removal condition | Target release/issue |
|---|---|---|---|---|---|

Every new migration alias, schema decoder, retired environment-variable tombstone, duplicated
old/new path, or temporary feature flag must have a row.

Each code location must include a short comment naming the table ID.

Do not add a general CI policy engine. The release checklist must include:

- review all rows whose target release has arrived;
- delete expired paths or explicitly move the target with rationale;
- ensure completed spec files are removed from the active spec directory.

The current retired override names from the v2/v3 migrations must be registered here.

## 5. Clarify compatibility scope

Update `CONTRIBUTING.md` and architecture guidance to state that supported compatibility
covers:

- versioned configuration;
- versioned input/output artifacts;
- published container/template behavior;
- documented operator commands;
- current thread/state behavior.

It does not cover:

- private Python helpers;
- internal module paths;
- logger names or exact log prose;
- test fixture internals;
- undocumented environment variables;
- internal orchestration artifacts removed within a release migration.

## 6. Keep documentation authorities singular

Record the following source-of-truth map in the ADR or agent guide:

| Concern | Authority |
|---|---|
| Reviewer IDs and immutable metadata | `ai_review.reviewers` registry |
| Operator configuration | current config schema and shipped YAML |
| Finding/critique/consensus shapes | JSON schemas |
| Internal normalized model payload | the one adapter normalization seam |
| Consensus decision | the one support-policy function |
| Current workflow | canonical templates |
| Workflow installation copy | parity output, not a second authority |
| Current open work | `docs/improvement-specs/README.md` |
| Released identity and evidence | release inputs and cited evidence records |

Fix current stale claims while implementing this spec, including:

- `docs/decisions/README.md` must not say `check_docs.py` enforces an ADR table rule that no
  longer exists;
- the Makefile workflow-parity comment must not claim removed supply-chain and release-input
  parity checks still exist.

Do not restore exact-prose checks to prevent such drift. Review and direct source-of-truth
links are the control.

## 7. One-boundary default for coding agents

Add contribution guidance:

- normal pull requests should primarily change one major boundary;
- a behavior-changing cleanup should separate policy decisions from observational follow-ups;
- an abstraction is accepted only when it immediately removes duplication or implements an
  ADR boundary;
- temporary dual paths must have a deletion condition;
- completed specs are deleted, with history retained by git.

## Acceptance criteria

- ADR-0003 is accepted and indexed.
- A concise root `AGENTS.md` points agents to current authorities.
- The pull-request template exposes architecture and compatibility cost.
- Temporary compatibility paths have owners and deletion conditions.
- Current compatibility scope is documented narrowly.
- Stale enforcement claims are corrected.
- No line-count, file-count, test-count, or general architecture-budget CI gate is added.
- No new exact-prose documentation rule is added.
- `make quality` passes.

## Non-goals

- Do not create `architecture-budget.yaml`.
- Do not reject pull requests automatically for adding a config key or file.
- Do not require an ADR for ordinary implementation details.
- Do not create separate full instruction manuals for each coding agent product.
