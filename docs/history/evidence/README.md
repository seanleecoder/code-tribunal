# Live evidence index and runbook

Live evidence complements executable tests; it does not replace them. Record
only sanitized identifiers, digests, expected/actual outcomes, and audit results.
Never store credentials, CLI session material, proprietary source, or sensitive
model content.

## 1.0 evidence matrix

> The `b674d1e` candidate was invalidated after live run `29842017448`
> demonstrated that repository-owner disposition commands were ignored when
> the workflow token could not inspect collaborator permissions. The scoped
> results below remain historical evidence for those exact images. The
> replacement runtime source is `15d424feea730a04338ed423bf93b8797d807bbc`;
> every release-gating row below except image publication must be repeated
> against its two published digests.

Live evidence spends real model tokens and real platform quota, so each row is
classified by whether a live run proves something the regression suite cannot:

- **Release-gating (live-only):** exercises a real model, a real platform
  merge-block, real credential withholding, or the registry — behavior no unit
  test can stand in for. A scoped live pass is required for final 1.0.0.
- **Regression-covered (live-optional):** the logic is proven fail-closed by
  named tests inside `make quality`; a live run only adds CI-wiring confidence
  and is **not** a release gate. This is deliberate — two of these race windows
  were never reproducible live.

| Suite | Tier | Regression coverage (`make quality`) | Status |
|---|---|---|---|
| Image publication verification | release-gating | n/a (registry/attestation) | **Scoped pass** — [15d424f images](record-image-publication-verification.md) |
| GitHub default-model + current-image lifecycle | release-gating | `test_post.py`, `test_gate.py`, `integration/test_post_gate_e2e.py` (posting/state/gate logic) | **Replacement partial** — full three-model panel, classic-token resolve/reopen, owner-command persistence, stale-head no-op, PR-event required-check blocking, and exact-value audit passed; positive changed-body in-place update still pending. [smoke](record-github-default-model-smoke.md) · [lifecycle](record-github-current-image.md) |
| GitLab current-image lifecycle | release-gating | same posting/state/gate tests via `fake_gitlab` | **Replacement partial** — full-panel blocking run, direct resolve/reopen, unchanged idempotency, and exact-value audit passed; positive changed-body in-place update still pending. [record](record-gitlab-current-image.md) |
| GitLab hostile-MR credential/enforcement boundary | release-gating | `test_verify_pipeline_trust.py` (composition), fork-secret withholding in `test_input_bundle.py` | **Replacement partial** — credential withholding and forwarding isolation passed; trusted image/config override and forged-gate at a credential-bearing boundary still pending live. [record](record-gitlab-hostile-mr.md) |
| Snapshot symlink containment (SPEC-31) | regression-covered | `test_input_bundle.py` — every variant (relative, absolute, parent-escaping, dangling, directory, `/proc/self/environ`) + copy/descent races + shared-builder | Confirm ≤1 representative variant live; regression suite is authoritative. Folded into the hostile-MR [record](record-gitlab-hostile-mr.md). |
| Gate/config artifact integrity (SPEC-33) | regression-covered | `test_consensus_integrity.py` (run-id/digest/critic forgery) + `test_gate.py` (post-result run-id binding, gate precedence) | Forged evidence from another run/config fails closed in consensus and gate; confirm opportunistically in the hostile-MR run. |
| GitHub revision failures (SPEC-34) | regression-covered | `test_input_bundle.py`, `test_github_platform.py` — all three race boundaries incl. manifest-finalization, plus HTTP 406 | Live-optional. Timing windows are milliseconds wide; do not gate the release on reproducing them. [record](record-github-revision-failures.md) |

Previous GitHub dogfood runs proved workflow execution, authenticated state, and
some inline posting, but explicitly did not prove a genuinely blocking required
check or all current-image lifecycle paths. Previous GitLab runs proved a real
consumer flow but not the hostile-MR deployment boundary. See
[legacy acceptance](../acceptance/README.md).

### Known gaps and missing evidence

- **Positive changed-body in-place update** has never been demonstrated live on
  either platform (probes fell back to summary-only or resolved the old thread).
  It is unit-covered (`test_post.py::test_post_existing_marker_updates_changed_body`)
  and is the clearest remaining lifecycle gap; the deterministic mock now lets it
  be reproduced without model spend (see the runbook).
- **Cursor reviewer** is the opt-in substitute with a separate credential and
  egress path. It currently has only a permission smoke and **no evidence row**;
  its data-egress/permission behavior is out of the 1.0 live-evidence scope until
  a dedicated record exists. Do not advertise Cursor as evidence-backed.
- Until the release-gating rows are scoped passes against this exact RC source and
  image digests, current docs must qualify rather than assert product-wide
  "stable," "credential isolated," or equivalent deployment claims. The
  regression-covered rows do not block the release.

## Record format

Copy [record-template.md](record-template.md) for each independently repeatable
run. Required fields:

- Platform, date/time, deployment topology, and operator-controlled project.
- Change-request, pipeline/workflow, and job IDs/URLs.
- Exact source commit and base/reviewer image tags and digests.
- Template/workflow commit and protection/required-check settings.
- Expected attack or lifecycle operation and actual result.
- Artifact and log paths inspected.
- Secret audit result and known unexercised paths.

## GitLab hostile-MR procedure

Use an unprotected source branch or fork in a scratch consumer. Attempt to
replace jobs/templates, forward root/bridge variables, override trusted image
and config values, print protected credential names, forge the gate artifact,
and add a symlink targeting environment data. Confirm the protected composition
is retained or the pipeline safely withholds credentials/fails. Audit every
trace and downloaded artifact for credential values.

Exercise both the chosen production topology and the trust auditor. Child mode
must use exactly two same-project, same-SHA includes with inheritance and both
forwarding flags disabled.

The live-only value here is real protected-credential withholding and real
trusted-composition enforcement. The SPEC-31 symlink variants and the SPEC-33
forged-gate integrity binding are regression-covered
(`test_input_bundle.py`, `test_gate.py`, `test_consensus_integrity.py`); confirm
at most one representative symlink variant live rather than re-running every
class.

## Current-image lifecycle procedure

Publish both images from one reviewed release-candidate commit and verify their
digests. On each platform, create an inline finding, rerun unchanged, change the
body, resolve, reopen, push an unrelated line movement, exercise summary
fallback, and force a blocking finding while platform enforcement is enabled.
Record post/state/gate artifacts and platform object IDs at every step.

Spend model tokens on the **first real panel only**; drive every remaining step
with the deterministic mock reviewer (`AI_REVIEW_LOCAL_MOCK=1` +
`AI_REVIEW_MOCK_SCENARIO`). The exact minimal-token sequence, including how the
mock scenarios map to each lifecycle step, is in the
[RC runbook](RUNBOOK-1.0-rc.md).

## GitHub failure procedure

Forcing PR head movement at each prepare boundary and the oversized-diff HTTP 406
path are **regression-covered** by the SPEC-34 tests in
`ai-review/tests/unit/test_input_bundle.py` and `test_github_platform.py` (all
three race boundaries, including manifest-finalization, plus 406). A live smoke is
optional wiring confirmation only and does not gate the release — the timing
windows are milliseconds wide and two were never reproducible live.
