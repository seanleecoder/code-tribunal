# ADR-0003 — Product invariants and complexity envelope

- **Status:** Accepted; release authority amended by
  [ADR-0004](0004-simplified-release-process.md); reviewer tool policy amended
  2026-11-17 ([below](#amendment-2026-11-17-no-shell-codex))
- **Date:** 2026-08-22
- **Decision:** Product boundaries and review requirements

## Context

Code Tribunal has removed overlapping policy, adapter, state, workflow, and test
implementations. Without a small, explicit product envelope, ordinary additions
could recreate those parallel systems. The control should make architectural cost
visible without turning repository size or structure into an automated score.

## Decision

The accepted product dimensions are:

- four first-party reviewers: Claude, Codex, OpenCode, and Cursor;
- one review round followed by one critique round;
- two platforms: GitHub and GitLab;
- one deterministic reducer as decision authority;
- informational findings surfaced after support from two independent reviewer
  identities;
- surfaced discussion threads, with a summary fallback when a thread cannot be
  posted;
- platform-hosted persistent state with thread reconciliation;
- an explicit trusted reviewer registry, with no dynamic plugin loading;
- pinned third-party CLIs trusted to implement their documented behavior, while
  Code Tribunal verifies its own invocation and credential isolation;
- one canonical GitHub reusable workflow, called at a release SHA by consumers
  ([ADR-0004](0004-simplified-release-process.md) decision 10);
- signed release tags whose canonical templates pin the released image digests as
  release authority ([ADR-0004](0004-simplified-release-process.md)).

A new accepted ADR is required before adding a fifth bundled reviewer, a third
model stage or recursive critique, a third platform, a second state-backend
family, a new public artifact family, dynamic plugin loading, another decision
implementation, or another canonical workflow or release authority. This is a
review requirement, not an automated ban, and it does not apply to ordinary
implementation details within these boundaries.

Normal changes should primarily affect one of these major boundaries: input
preparation, adapter execution, critique, consensus, posting/state, platform
transport, release/supply chain, or documentation/tooling. Cross-boundary work
is permitted when the pull request explains why it cannot be staged as contract,
migration, and deletion.

### Amendment (2026-11-17): no-shell Codex

First-party reviewers read repositories only through fixed, read-only tool
policies: Claude's `Read,Grep,Glob`, OpenCode's read/glob/grep permissions,
Cursor's ask mode with shell and writes denied, and Codex with its shell tool
disabled. Codex 0.169.0 needs an OS sandbox (bwrap with nested user namespaces)
to confine its shell; default Docker seccomp and AppArmor profiles, and Ubuntu's
unprivileged user-namespace restriction, block that sandbox on standard Linux
runners. Relaxing runner kernel policy was rejected.

Codex review therefore reads the copied snapshot through a trusted, internal,
standard-library stdio MCP server,
[`ai_review.codex_repo_tools`](../../ai-review/src/ai_review/codex_repo_tools.py),
which the registry starts in isolated mode. It offers `read_file`, `list_dir` and
`grep` (the image's pinned `rg`), resolves every path inside the snapshot,
bounds output, writes nothing, and refuses to start if its environment carries
anything beyond plain process variables. Critique has no repository tools. The
registry disables other optional Codex tools that have feature flags;
`apply_patch` has no supported switch and stays subject to Codex's read-only
policy. The image gate pins the remaining tool set. A model the pinned CLI has
no catalog metadata for runs on fallback metadata, which drops code mode and
offers a different tool surface, including a provider-hosted web search; such
reviews fail closed with that cause rather than run on an unverified surface.

This is a fixed part of one reviewer's trusted invocation, not a plugin
mechanism: the server is not configurable, discoverable or replaceable by
consumers. Pinned CLIs remain trusted to honor documented behavior; Code
Tribunal owns this server's containment and credential isolation.

## Sources of truth

| Concern | Authority |
|---|---|
| Reviewer IDs and immutable metadata | [`ai_review.reviewers`](../../ai-review/src/ai_review/reviewers.py) registry |
| Operator configuration | [`ai_review.config`](../../ai-review/src/ai_review/config.py) and the [shipped YAML](../../ai-review/config/review.yaml) |
| Finding, critique, and consensus shapes | [JSON schemas](../../ai-review/schemas/) |
| Internal normalized model payload | [`adapter_output._coerce_adapter_root`](../../ai-review/src/ai_review/adapter_output.py) |
| Consensus decision | [`consensus_policy.apply_group_decision`](../../ai-review/src/ai_review/consensus_policy.py) |
| Current workflow | [GitHub reusable workflow](../../.github/workflows/ai-review-reusable.yml) and [GitLab template](../../ai-review/ci/review.gitlab-ci.yml) |
| GitHub installation | A release-SHA caller; [this repository's caller](../../.github/workflows/ai-review.yml) uses the same-commit workflow |
| Current open work | [`docs/improvement-specs/README.md`](../improvement-specs/README.md) |
| Public compatibility surface | [architecture guide](../development/architecture.md#compatibility-boundary) |
| Temporary compatibility paths | [compatibility register](../development/temporary-compatibility.md) |
| Released identity | The canonical workflows above at the signed release tag ([ADR-0004](0004-simplified-release-process.md)) |
| Release assurance | The Candidate Canary run cited in the release body, verified at publication |
| Release notes | The `CHANGELOG.md` section for the version |

## Consequences

- Architecture review is explicit and qualitative. No line-count, file-count,
  test-count, configuration-key-count, or generalized complexity-budget gate is
  introduced.
- Abstractions are accepted when they immediately remove duplication or
  implement an ADR boundary, not for hypothetical future dimensions.
- Temporary dual paths need an owner, deletion condition, code marker, and target
  in the [compatibility register](../development/temporary-compatibility.md).
- Public compatibility is deliberately narrow; internal Python seams and exact
  prose are free to change as described in the
  [architecture guide](../development/architecture.md#compatibility-boundary).
- Direct links to authorities and human review control documentation drift. Exact
  prose enforcement is not added.
