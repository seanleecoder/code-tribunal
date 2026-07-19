# SPEC-33 — Make gate failures and cross-stage configuration integrity explicit

> **Historical completed requirement.** Current behavior is documented under
> [`../reference/`](../reference/README.md); this file is non-normative.

- **Severity:** High (false-green operational failure / policy mismatch) · **Effort:** M · **ROI rank:** 3 (pre-1.0)
- **Depends on:** SPEC-32 for the final reviewer-quality artifact shape.

## Why

Two contracts are currently unsafe or ambiguous:

1. `gate.evaluate_gate` returns `skipped_disabled` before checking
   `failed`, `partial_failed`, or `state_overflow`. Disabling finding-based merge
   gating therefore also hides failures to publish or persist review state, despite
   the documented fail-closed guarantee.
2. Prepare records effective configuration in the manifest, but consensus merely
   warns when its view differs. Finding batches are not bound to the manifest's
   run, enabled reviewers, or effective models. A mis-scoped variable can make the
   reducer apply policy different from the policy that produced the evidence.

## Contract decisions

- `merge_gate.enabled=false` disables blocking based on finding consensus only.
  Operational failures remain nonzero. If a fully nonblocking mode is needed, it
  must be a separately named, explicit policy with distinct documentation.
- Consequential effective-config divergence is fatal. Every consumed artifact is
  bound to one run and one prepared effective configuration.

## Scope

**In:** `gate.py`, `consensus.py`, manifest/finding/critique schemas, adapter runner,
CI templates, tests, docs, exit-code reference.

**Out:** changing severity/vote policy; retrying platform POST operations; remote
configuration services.

## Implementation

1. Reorder gate evaluation: validate post/state outcome first, then stale-head
   handling, then finding-based enablement/blocking. Document exact precedence and
   exit codes.
2. Add tests for every post status with merge gate both enabled and disabled.
3. Compute one canonical effective-config digest during prepare and carry it in the
   manifest and reviewer/critique outputs (or status artifacts).
4. At consensus, require:
   - batch `run_id` equals manifest `run_id`;
   - reviewer is configured and enabled in the prepared config;
   - at most one batch per enabled reviewer;
   - recorded model/config digest matches the prepared manifest;
   - critique targets belong to the same run and known finding IDs.
5. Fail on consequential divergence instead of warning. Diagnostic text may show
   field names and hashes, but not credentials or full prompts.
6. Make consensus validate each loaded finding/critique batch before typed access.
7. Ensure GitLab and GitHub templates propagate every supported override to all
   relevant jobs or rely solely on the immutable config captured by prepare.
8. Add a schema-version/migration note for artifact consumers.

## Tests

- Advisory mode plus `failed`, `partial_failed`, or `state_overflow` exits nonzero.
- Advisory mode plus successful posting ignores only `summary.block_merge`.
- Wrong run ID, duplicate reviewer, disabled reviewer, wrong model, wrong digest,
  unknown critique target, and malformed artifact all fail consensus cleanly.
- Reordered artifact files produce identical consensus.
- Canonical GitHub/GitLab templates pass an environment-consistency contract test.

## Acceptance criteria

- No posting/state loss is reported as a green advisory run.
- Consensus never combines evidence from different runs or policies.
- All CLI failures produce stable, documented exit codes and actionable redacted
  messages.
- README fail-closed claims match executable tests.

## Risk / rollback

Custom workflows with inconsistent job-scoped variables will begin failing instead
of warning. Provide a migration message naming the divergent keys. Do not restore
warning-only behavior for decision-affecting fields.
