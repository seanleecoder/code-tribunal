# SPEC-42 — Decide whether a human `wontfix` may clear the merge gate

- **Severity:** Medium (documented behaviour, but no operator escape hatch) · **Effort:** M · **ROI rank:** post-1.0
- **Depends on:** SPEC-33 artifact/config integrity, SPEC-34 revision binding.

## Why

A human `/ai-review wontfix` durably dismisses a finding and stops it being
re-posted, but it never clears the merge gate. The reviewers keep emitting the
finding, consensus keeps counting it as blocking, and the required check stays red
indefinitely. The only escapes are disabling `merge_gate.enabled` or removing the
required check — both of which disable gating wholesale rather than dismissing one
false positive.

Proven live on 2026-07-25 in the GitHub evidence demo (PR #6, run `30173073036`),
against the shipped 1.0.0 image pair:

- **Attempt 4**, after `/ai-review wontfix` from a write-access author:
  `post_result` reported `resolved_discussions=1`, `skipped_unchanged=1`,
  `warnings=[]` — the command was accepted, not rejected for access — and the thread
  showed `isResolved=true` on the platform. The gate still failed.
- **Attempt 5**, an unchanged rerun: `inputs/prior_decisions.json` correctly carried
  `settled: [{status: "wontfix", …}]` and post reported
  `created=0, updated=0, skipped_unchanged=1`, so the dismissal persisted and
  suppressed re-posting. Consensus still produced `block_merge: true` and the gate
  failed again.

The cause is deliberate precedence, not a bug:
[`gate.py`](../../ai-review/src/ai_review/gate.py) enforces
`consensus.summary.block_merge` and nothing else, and post-side state is not fed back
into the same run's consensus. The documentation is honest about what `wontfix` is —
[`docs/TROUBLESHOOTING.md`](../TROUBLESHOOTING.md) describes it as a durable
dismissal and never claims it overrides the gate. So this is a **design decision to
confirm or change**, not a defect to fix. It is recorded as observed behaviour in
[`record-github-current-image.md`](../history/evidence/record-github-current-image.md)
and reproduced on GitLab in
[`record-gitlab-current-image.md`](../history/evidence/record-gitlab-current-image.md).

## Decision to make

- **Option A — keep as-is, document the escape hatch.** State explicitly in the
  troubleshooting and merge-gate docs that `wontfix` does not clear the gate, and
  give the intended operator path for a genuine false positive (for example a scoped
  config exclusion, or a deliberate temporary gate disable with an audit trail).
  Cheapest, and defensible as fail-closed.
- **Option B — honour settled dismissals in consensus.** Have consensus consult
  `prior_decisions.settled` and exclude `wontfix` findings from
  `summary.block_merge` while still surfacing them.

## The constraint that makes Option B non-trivial

A dismissal must not become a self-service bypass. Before implementing B, establish
how `prior_decisions` is built and how far it is trusted — see
[`input_bundle.py`](../../ai-review/src/ai_review/input_bundle.py) — and confirm:

- the dismissal originates from verified platform state written by the trusted bot
  identity, not from any artifact a change request can author or forge (the SPEC-33
  integrity properties must survive);
- a hostile merge request cannot dismiss its own blockers, directly or by replaying
  another run's state;
- author authorization is re-verified, not inherited from the earlier run. Note the
  authorization path here is exactly the area whose defect invalidated the `b674d1e`
  release candidate, so it deserves care rather than reuse by assumption.

If those cannot be guaranteed cleanly, Option A is the better outcome — an
inconvenient gate is preferable to a bypassable one.

## Tests

- Unit: consensus with a `settled: wontfix` prior decision → asserts the chosen
  `block_merge` outcome.
- Security: a forged or cross-run `prior_decisions` claiming `wontfix` must not clear
  the gate; a hostile change request must not dismiss its own blocking finding.
- Integration: the full create → wontfix → rerun sequence asserts the intended gate
  result at each step, mirroring the live chain recorded for 1.0.0.
