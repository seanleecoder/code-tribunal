# SPEC-68 — Posting and state correctness fixes for 2.0.3

- **Status:** Fixes 1 (#183), 2 (#184) and 4 are complete; see the
  [CHANGELOG](../../CHANGELOG.md). Only fix 3 remains.
- **Severity:** Medium. Mixing consensus and inputs from different runs can mutate
  threads under the wrong review result. · **Effort:** S (one remaining fix)
- **Vehicle:** 2.0.3. No schema, template, or artifact change.
- **Depends on:** nothing. Fix 3 lands in the same pull request as
  [SPEC-47](spec-47-trusted-project-review-config.md), because SPEC-47 already adds a
  pre-work binding check to post.
- **Found by:** the 2026-10-13 simplification review of the decision, state, and
  posting layers.

## Fix 3 — post does not bind its consensus artifact to this run

**Defect.** `post.cli` validates the consensus artifact against its schema and
nothing else. The artifact's `run_id`, `head_sha` and `input_manifest_sha256` are
never compared with the manifest post reads from `--inputs`. Nothing stops artifacts
from different runs being mixed: for example, a retried job that downloads another
run's consensus, or an operator replay. Consensus already writes
`input_manifest_sha256` for exactly this check.

**Fix.** Before creating the platform client, post rejects the artifact unless all
of these hold:

- `consensus.run_id == manifest.run_id`;
- `consensus.head_sha == manifest.head_sha`;
- `consensus.input_manifest_sha256` equals the canonical SHA-256 of `manifest.json`,
  computed exactly as consensus computes it.

Exit with the operational-failure code and a message naming the mismatched field.

This prevents mixing artifacts across runs; it is **not** tamper protection.
ADR-0002's note that inter-stage integrity is unaddressed still stands. Say so in a
code comment and in the [security model](../SECURITY_MODEL.md).

**Tests (`test_post.py`).**

- Each mismatched field fails before any platform call.
- A matching artifact posts as today.

## Acceptance

- Post refuses a consensus artifact from another run before any platform call.
- `make quality` passes, and the CHANGELOG lists the fix under 2.0.3.
- Delete this file and its index row when fix 3 has merged.
