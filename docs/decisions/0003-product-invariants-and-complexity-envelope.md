# ADR-0003 — Product invariants and complexity envelope

- **Status:** Accepted
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
- one canonical GitHub workflow template and one mechanically synchronized
  installed copy;
- `release/release-inputs.json` and its source-bound evidence records as release
  authority.

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

## Sources of truth

| Concern | Authority |
|---|---|
| Reviewer IDs and immutable metadata | [`ai_review.reviewers`](../../ai-review/src/ai_review/reviewers.py) registry |
| Operator configuration | [`ai_review.config`](../../ai-review/src/ai_review/config.py) and the [shipped YAML](../../ai-review/config/review.yaml) |
| Finding, critique, and consensus shapes | [JSON schemas](../../ai-review/schemas/) |
| Internal normalized model payload | [`adapter_output._coerce_adapter_root`](../../ai-review/src/ai_review/adapter_output.py) |
| Consensus decision | [`consensus_policy.apply_group_decision`](../../ai-review/src/ai_review/consensus_policy.py) |
| Current workflow | [canonical templates](../../ai-review/ci/) |
| GitHub workflow installation copy | [parity output](../../.github/workflows/ai-review.yml), not a second authority |
| Current open work | [`docs/improvement-specs/README.md`](../improvement-specs/README.md) |
| Public compatibility surface | [architecture guide](../development/architecture.md#compatibility-boundary) |
| Temporary compatibility paths | [compatibility register](../development/temporary-compatibility.md) |
| Released identity and evidence | [`release/release-inputs.json`](../../release/release-inputs.json) and its cited [evidence records](../evidence/) |

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
