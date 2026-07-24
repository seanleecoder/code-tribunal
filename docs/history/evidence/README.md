# Live evidence index and runbook

Live evidence complements executable tests; it does not replace them. Record
only sanitized identifiers, digests, expected/actual outcomes, and audit results.
Never store credentials, CLI session material, proprietary source, or sensitive
model content.

## Release readiness gate

`release/release-inputs.json` is **`draft`** until every release-gating row below
is a scoped `Status: passed` against one frozen runtime source `R` and its
attested base/reviewer digests (or carries an explicit
`Release-evidence-waived: <reason>` line). `scripts/check_release_inputs.py`
rejects `status: active` when cited records are partial or bind a different
SHA/digest pair.

## Operator checklist (final image pair)

1. Freeze runtime commit `R` that includes the intended mock/gate code.
2. Publish attested base+reviewer images from exactly `R`; record anonymous
   pull, OCI revision label, and provenance in
   [`record-image-publication-verification.md`](record-image-publication-verification.md).
3. Repin both GitHub workflow copies and the three GitLab pin variables together;
   refresh `release/release-inputs.json` hashes.
4. Run Chain A (real default-model smoke) and Chain B (mock lifecycle, including
   `blocking_alt` changed-body update) on GitHub and GitLab per
   [`RUNBOOK-1.0-rc.md`](RUNBOOK-1.0-rc.md). Chain B requires
   `AI_REVIEW_LOCAL_MOCK=1` **and** `AI_REVIEW_ALLOW_LOCAL_MOCK=true`.
5. Finish GitLab hostile-MR trusted image/config override and forged-gate probes.
6. Set each release-gating record to `Status: passed` with matching
   `Release-runtime-source` / `Release-base-digest` / `Release-reviewer-digest`
   fields (see [`record-template.md`](record-template.md)).
7. Only then set `release-inputs.status` to `active`, cut release commit `P`,
   build the external manifest, and tag `v1.0.0`.

## 1.0 evidence matrix

> Historical candidates (`b674d1e`, `15d424f`, and earlier) remain useful
> provenance only. **Every release-gating row below, including image
> publication, must be repeated against the final rebuilt base+reviewer pair
> for the frozen runtime source `R`.**
>
> Digest note: `AI_REVIEW_MOCK_SCENARIO` and the gate `run_id` binding ship
> inside the product image. Both the real default-model smoke (Chain A) and the
> deterministic-mock lifecycle (Chain B) must run against that final pair.

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
| Image publication verification | release-gating | n/a (registry/attestation) | **Pending** against the final rebuilt pair (anonymous digest pull, OCI revision label, provenance attestation). Prior scoped passes are historical only. |
| GitHub default-model + current-image lifecycle | release-gating | `test_post.py`, `test_gate.py`, `integration/test_post_gate_e2e.py` (posting/state/gate logic) | **Pending** against final digests — prior replacement candidate was partial (changed-body in-place update still required). [smoke](record-github-default-model-smoke.md) · [lifecycle](record-github-current-image.md) |
| GitLab current-image lifecycle | release-gating | same posting/state/gate tests via `fake_gitlab` | **Pending** against final digests — prior replacement candidate was partial (changed-body in-place update still required). [record](record-gitlab-current-image.md) |
| GitLab hostile-MR credential/enforcement boundary | release-gating | `test_verify_pipeline_trust.py` (composition), fork-secret withholding in `test_input_bundle.py` | **Pending** against final digests — prior partial covered credential withholding/forwarding isolation; trusted image/config override and forged-gate at a credential-bearing boundary still required. [record](record-gitlab-hostile-mr.md) |
| Snapshot symlink containment (SPEC-31) | regression-covered | `test_input_bundle.py` — every variant (relative, absolute, parent-escaping, dangling, directory, `/proc/self/environ`) + copy/descent races + shared-builder | Confirm ≤1 representative variant live; regression suite is authoritative. Folded into the hostile-MR [record](record-gitlab-hostile-mr.md). |
| Gate/config artifact integrity logic (SPEC-33) | regression-covered | `test_consensus_integrity.py` (run-id/digest/critic forgery) + `test_gate.py` (post-result run-id binding, gate precedence) | Forged evidence from another run/config fails closed in consensus and gate. This covers the *integrity logic* only — the *live* forged-gate-at-a-credential-boundary probe stays release-gating in the hostile-MR row above. |
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
  and is the clearest remaining lifecycle gap; the mock `blocking_alt` scenario
  (same identity as `blocking`, different body) now reproduces it live without
  model spend (see the runbook).
- **Cursor reviewer** is an experimental opt-in substitute with a separate
  credential and egress path. It currently has only a permission smoke and **no
  evidence row**; do not advertise Cursor as evidence-backed.
- Until the release-gating rows are scoped passes against the final `R` and image
  digests, docs must qualify rather than assert product-wide "stable,"
  "credential isolated," or equivalent deployment claims. The regression-covered
  rows do not block the release.

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
body, resolve, reopen, and force a blocking finding while platform enforcement is
enabled. Record post/state/gate artifacts and platform object IDs at every step.
Unrelated line movement is **not** in the required live sequence: its internal
remap (finding identity + persisted anchor moved, existing discussion updated not
duplicated) is regression-covered, and only the *platform-visible* re-anchoring of
a moved comment is a live-optional confirmation — see the runbook.

Run this as two independent chains: one **real** default-model panel (the smoke),
and one **deterministic-mock** lifecycle chain on a separate finding identity
(`AI_REVIEW_LOCAL_MOCK=1` + `AI_REVIEW_MOCK_SCENARIO`, with `blocking_alt` for the
changed-body step). The below-quorum FYI/summary-comment path and the
inline-unmappable summary fallback are **regression-covered**
(`integration/test_post_gate_e2e.py`, `test_post.py`), not part of the live mock
chain. The exact minimal-token sequence is in the [RC runbook](RUNBOOK-1.0-rc.md).

## GitHub failure procedure

Forcing PR head movement at each prepare boundary and the oversized-diff HTTP 406
path are **regression-covered** by the SPEC-34 tests in
`ai-review/tests/unit/test_input_bundle.py` and `test_github_platform.py` (all
three race boundaries, including manifest-finalization, plus 406). A live smoke is
optional wiring confirmation only and does not gate the release — the timing
windows are milliseconds wide and two were never reproducible live.
