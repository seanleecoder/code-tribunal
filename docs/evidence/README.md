# Live evidence index and runbook

Live evidence complements executable tests; it does not replace them. Record
only sanitized identifiers, digests, expected/actual outcomes, and audit results.
Never store credentials, CLI session material, proprietary source, or sensitive
model content.

## 1.0.1 readiness gate

The checked-in `release/release-inputs.json` is the **1.0.1 draft**. It must
remain **`draft`** until every release-gating row is a scoped `Status: passed`
against one frozen runtime source `R` and its attested base/reviewer digests (or
carries an explicit
`Release-evidence-waived: <reason>` line also registered under
`verification.evidence_waivers` in the hashed release-inputs artifact).
`scripts/check_release_inputs.py` rejects `status: active` when cited records
are partial, bind a different SHA/digest pair, or waive without that registry
entry.

The released 1.0.0 source/image coordinates are historical. Do not reactivate
them for 1.0.1; bind this draft to the new runtime source and final image pair
only after the new runs pass.

## Operator checklist (1.0.1 final image pair)

1. Freeze runtime commit `R` that includes the intended mock/gate code.
2. Publish attested base+reviewer images from exactly `R`; record anonymous
   pull, OCI revision label, and provenance in
   [`record-image-publication-verification.md`](record-image-publication-verification.md).
3. Repin both GitHub workflow copies and the three GitLab pin variables together;
   refresh `release/release-inputs.json` hashes.
4. Run Chain A (real default-model smoke) and Chain B (mock lifecycle, including
   `blocking_alt` changed-body update) on GitHub and GitLab per
   [`RUNBOOK.md`](RUNBOOK.md). Chain B requires
   `AI_REVIEW_LOCAL_MOCK=1` **and** `AI_REVIEW_ALLOW_LOCAL_MOCK=true`.
5. Before activation, run and record the first real Codex `max` and OpenCode
   `xhigh` route checks against the rebuilt images in the separate
   [`record-model-effort-routes.md`](record-model-effort-routes.md) record.
   Provider rejection is a failed validation, not a reason to omit or coerce
   the effort level.
6. Finish GitLab hostile-MR trusted image/config override and forged-gate probes.
7. Set each release-gating record to `Status: passed` with matching
   `Release-runtime-source` / `Release-base-digest` / `Release-reviewer-digest`
   fields (see [`record-template.md`](record-template.md)).
   Historical Identity-section source/image prose is not parsed as a release
   binding; re-stamp older records with these explicit fields.
8. Only then set the 1.0.1 `release-inputs.status` to `active`, cut release
   commit `P`, build the external manifest, and create the `v1.0.1` tag.

## 1.0 historical evidence matrix

This section records the evidence that supported the already-released `v1.0.0`.
It is retained for provenance and is not a passing matrix for 1.0.1.

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
  test can stand in for. A scoped live pass is required for the release under
  preparation.
- **Regression-covered (live-optional):** the logic is proven fail-closed by
  named tests inside `make quality`; a live run only adds CI-wiring confidence
  and is **not** a release gate. This is deliberate — two of these race windows
  were never reproducible live.

| Suite | Tier | Regression coverage (`make quality`) | Status |
|---|---|---|---|
| Image publication verification | release-gating | n/a (registry/attestation) | **Passed** 2026-07-25 against the final pair for `R = 88bc941` — anonymous digest resolution, OCI revision label equal to `R`, and provenance attestations bound to publication run `30125524008` on both subjects. [record](record-image-publication-verification.md) |
| GitHub default-model + current-image lifecycle | release-gating | `test_post.py`, `test_gate.py`, `integration/test_post_gate_e2e.py` (posting/state/gate logic) | **Passed** 2026-07-25 for `R = 88bc941`. Smoke: real 3-seat panel, `panel_status: full`, 4 security findings posted (run `30174011868`). Lifecycle: create → unchanged rerun → **changed-body in-place update** → resolve → persistence → reopen → stale-head no-op, with the required `gate` check genuinely blocking (`mergeable_state=blocked`), run `30173073036` attempts 1–7. [smoke](record-github-default-model-smoke.md) · [lifecycle](record-github-current-image.md) |
| GitLab current-image lifecycle | release-gating | same posting/state/gate tests via `fake_gitlab` | **Passed** 2026-07-25 for `R = 88bc941` (MR !11, hardened child): create → unchanged rerun → **changed-body in-place update** → resolve → reopen on one identity, with `detailed_merge_status: ci_must_pass` withholding the merge throughout. [record](record-gitlab-current-image.md) |
| Codex `max` / OpenCode `xhigh` effort routes | release-gating | n/a — real provider route | **Pending** — [record](record-model-effort-routes.md) |
| GitLab hostile-MR credential/enforcement boundary | release-gating | `test_verify_pipeline_trust.py` (composition), fork-secret withholding in `test_input_bundle.py` | **Passed** 2026-07-25 for `R = 88bc941` (MR !12, pipelines `2705749548`/`2705750931`): both protected credentials withheld on an unprotected ref (`OPENROUTER_API_KEY absent`, `GITLAB_TOKEN absent`), prepare failed closed with an empty `inputs/` artifact, no credential value in any trace, and the trust auditor rejected the hostile composition (exit 1) while accepting the legitimate one. **Caveat:** the hostile config *did* substitute the container image (ran `alpine:3.20`); containment came from credential withholding plus the out-of-band auditor, not in-pipeline enforcement — do not claim trusted-image enforcement. [record](record-gitlab-hostile-mr.md) |
| Snapshot symlink containment (SPEC-31) | regression-covered | `test_input_bundle.py` — every variant (relative, absolute, parent-escaping, dangling, directory, `/proc/self/environ`) + copy/descent races + shared-builder | Confirm ≤1 representative variant live; regression suite is authoritative. Folded into the hostile-MR [record](record-gitlab-hostile-mr.md). |
| Gate/config artifact integrity logic (SPEC-33) | regression-covered | `test_consensus_integrity.py` (run-id/digest/critic forgery) + `test_gate.py` (post-result run-id binding, gate precedence) | Forged evidence from another run/config fails closed in consensus and gate. This covers the *integrity logic* only — the *live* forged-gate-at-a-credential-boundary probe stays release-gating in the hostile-MR row above. |
| GitHub revision failures (SPEC-34) | regression-covered | `test_input_bundle.py`, `test_github_platform.py` — all three race boundaries incl. manifest-finalization, plus HTTP 406 | Live-optional; **waived** for 1.0.0 with a reason registered under `verification.evidence_waivers`. The **stale-head** boundary was nonetheless reproduced live in run `30173073036` attempt 7 (`post` returned `status: stale_head` and wrote nothing; `gate` returned `passed_stale_head`). The other two boundaries and the 406 path rest on the regression suite. [record](record-github-revision-failures.md) |

### Supplemental experimental evidence (not release-gating)

| Suite | Status | Evidence |
|---|---|---|
| Cursor reviewer real-run adapter and critique | **Observed; SPEC-21 partial; historical supporting evidence only** | [Supplemental record](record-cursor-real-runs.md): private GitLab pipeline `185695` and public GitHub workflow `30080420563` both produced successful, resolution-eligible Cursor artifacts and full panels. Both recorded `model: auto`; neither exercised the hostile permission-denial prompt or the 1.0.1 image pair. |

Previous GitHub dogfood runs proved workflow execution, authenticated state, and
some inline posting, but explicitly did not prove a genuinely blocking required
check or all current-image lifecycle paths. Previous GitLab runs proved a real
consumer flow but not the hostile-MR deployment boundary. See
[legacy acceptance](../history/README.md#legacy-milestone-acceptance).

## Cursor enablement queue (SPEC-21)

These are not 1.0.0 results. SPEC-21 completion is required before enabling
Cursor, not before shipping 1.0.1. Complete the canonical [SPEC-21 closure
checklist](../improvement-specs/spec-21-cursor-cli-reviewer.md#cursor-enablement-closure-checklist)
against one frozen runtime source `R` and final reviewer digest before changing
the disabled default.

| Suite | Current state | Closure evidence |
|---|---|---|
| Cursor reviewer enablement (SPEC-21) | **Required before enablement and pending** — historical GitLab and GitHub runs close real-route execution and artifact validity only | Complete the [canonical SPEC-21 checklist](../improvement-specs/spec-21-cursor-cli-reviewer.md#cursor-enablement-closure-checklist), then create a sanitized, explicitly bound supplemental enablement record. |

### Known gaps and missing evidence

- **Positive changed-body in-place update — closed 2026-07-25.** Demonstrated live
  on **both** platforms against `R = 88bc941` using the mock `blocking_alt`
  scenario, with platform confirmation that the existing comment was rewritten
  rather than duplicated: GitHub comment `3650942127` (`created 20:13:42` →
  `updated 20:20:37`) and GitLab note `3601861614` (`created 20:47:29` →
  `updated 20:59:24`), each with `updated_discussions: 1`, `created: 0`, and the
  same `issue_id` across both platforms. Also unit-covered by
  `test_post.py::test_post_existing_marker_updates_changed_body`.
- **Cursor reviewer is not yet accepted for enablement.** It is an
  experimental opt-in substitute with a separate credential and egress path.
  The supplemental [real-run record](record-cursor-real-runs.md) proves real
  execution and valid artifacts at historical coordinates only. The exact
  Composer model pin, the ask-mode product decision, a fresh final-image
  real-key fixture run, and the hostile write/shell denial smoke are still
  required. Do not add Cursor to release inputs, enable it, or advertise it as
  acceptance-complete until those required checks pass. The literal `auto`
  model is discovery-only and is never valid enablement evidence.
- **The added-file path has no live green evidence, even after the 1.0.1 fix.** The
  1.0.0 matrix used modify-only fixtures to work around the GitHub `/dev/null` anchor
  defect, so no live run has ever exercised a finding on a newly added or deleted
  file. Shipping the fix does not by itself close this — a Chain B run with an
  **adding** fixture is required, asserting
  `accepted_finding_count == raw_finding_count`. See the coverage-gap table in the
  [runbook](RUNBOOK.md).
- **Trusted-image enforcement is not established.** The hostile-MR probe showed a
  consumer `.gitlab-ci.yml` can substitute the pinned base/reviewer images by
  declaring them in its own top-level `variables:` and enabling variable
  forwarding; nothing in the pipeline verifies the running image. Credential
  withholding and the operator-run trust auditor are what contain it. Docs must not
  assert in-pipeline trusted-image or trusted-composition enforcement.
- With the release-gating rows now scoped passes against the final `R` and image
  digests, "credential isolated" may be claimed **only** in the specific, recorded
  sense — protected credentials withheld from an unprotected-ref MR in the
  hardened child topology — and not as a product-wide property. Network egress is
  still unenforced at the container/runner boundary, forks are untested on GitLab,
  and Cursor remains disabled pending the SPEC-21 queue above. The
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
chain. The exact minimal-token sequence is in the [RC runbook](RUNBOOK.md).

## GitHub failure procedure

Forcing PR head movement at each prepare boundary and the oversized-diff HTTP 406
path are **regression-covered** by the SPEC-34 tests in
`ai-review/tests/unit/test_input_bundle.py` and `test_github_platform.py` (all
three race boundaries, including manifest-finalization, plus 406). A live smoke is
optional wiring confirmation only and does not gate the release — the timing
windows are milliseconds wide and two were never reproducible live.
