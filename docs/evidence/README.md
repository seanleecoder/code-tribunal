# Live evidence index and runbook

Live evidence complements executable tests; it does not replace them. Record
only sanitized identifiers, digests, expected/actual outcomes, and audit results.
Never store credentials, CLI session material, proprietary source, or sensitive
model content.

## 1.0.2 release evidence

The 1.0.2 release binds every cited row to one frozen runtime and immutable image
pair. `scripts/check_release_inputs.py` rejects activation when a cited record is
not a matching scoped pass or does not carry an explicit waiver identical to its
`verification.evidence_waivers` entry.

All rows below bind to `R = 54dffa130be5c921602f264a2123fda4b1895f13`, base
`sha256:960600d3…`, and reviewer `sha256:6bf8fdfb…`. The campaign was scoped by
the [change-impact triage table](../development/release-process.md#scoping-the-live-campaign),
with the rationale recorded in [`release/1.0.2.md`](../../release/1.0.2.md).

| Suite | Tier | Status |
|---|---|---|
| Image publication verification | release-gating | **Passed** 2026-08-10 — public anonymous pulls resolved both tags to the recorded digests, both OCI revision labels equal `R`, and both provenance attestations verified against `refs/heads/main`, source digest `R`, and the publish workflow. Run `31369496025`; quality run `31369496045`. [record](record-image-publication-verification.md) |
| GitHub default-model panel | release-gating | **Passed** 2026-08-10 — demo PR #14, run `31370873644`. Claude, Codex, and OpenCode resolved at the real provider; all three were resolution-eligible; `panel_status: full`; three findings posted; the required gate exited 7 on a genuine blocker. [record](record-github-default-model-smoke.md) |
| OpenCode `max` effort | release-gating | **Waived** — the operator reports prior acceptance in real runs on a real project, but those runs are not a public exact-image binding. The final runtime delta is post-provider structured-item normalization, so a duplicate rerun was skipped under an explicit residual-risk waiver. [record](record-model-effort-routes.md) |
| GitLab hostile-MR credential/enforcement boundary | release-gating | **Waived** — trust-boundary code is unchanged and remains covered by `test_verify_pipeline_trust.py` plus fork-secret withholding cases in `test_input_bundle.py`; the historical live pass stays supporting evidence only. [record](record-gitlab-hostile-mr.md) |
| GitHub revision failures (SPEC-34) | regression-covered | **Waived** — revision-race behavior is unchanged and the three boundaries plus oversized-diff 406 path remain covered by `test_input_bundle.py` and `test_github_platform.py`. [record](record-github-revision-failures.md) |
| Cursor reviewer | enablement-gating, not release-gating | **Not enabled.** The publication job retained the `auto`-model skip annotation; Cursor stays disabled pending the SPEC-21 closure checklist. |

The first candidate campaign (`f21418f…`, demo PR #12, run `31367545101`,
attempts 1 and 2) is retained as a superseded failed validation. It exposed a
repeatable string item inside OpenCode's schema-backed findings array. PR #116
closed that gap, the runtime and images were refrozen, and only the final pair above
was used for release evidence.

## Operator checklist (1.0.2 final image pair)

1. Runtime source, publication, public resolution, revision labels, and
   attestations: complete.
2. Canonical GitHub and GitLab consumers repinned together: complete.
3. Real default-model panel with fail-closed required gate: complete.
4. Waived rows stamped and registered with identical reasons: complete.
5. Downloaded live artifacts scanned with pattern/entropy detectors: complete;
   exact-value audit not performed and not claimed.
6. Release inputs, changelog, historical snapshot, manifest, signed tag, and
   published release: completed by the final release sequence.

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
| Codex `max` / OpenCode `xhigh` effort routes | release-gating | n/a — real provider route | Not completed at 1.0.0; waived for 1.0.1 with a registered reason — [record](record-model-effort-routes.md) |
| GitLab hostile-MR credential/enforcement boundary | release-gating | `test_verify_pipeline_trust.py` (composition), fork-secret withholding in `test_input_bundle.py` | **Passed** 2026-07-25 for `R = 88bc941` (MR !12, pipelines `2705749548`/`2705750931`): both protected credentials withheld on an unprotected ref (`OPENROUTER_API_KEY absent`, `GITLAB_TOKEN absent`), prepare failed closed with an empty `inputs/` artifact, no credential value in any trace, and the trust auditor rejected the hostile composition (exit 1) while accepting the legitimate one. **Caveat:** the hostile config *did* substitute the container image (ran `alpine:3.20`); containment came from credential withholding plus the out-of-band auditor, not in-pipeline enforcement — do not claim trusted-image enforcement. [record](record-gitlab-hostile-mr.md) |
| Snapshot symlink containment (SPEC-31) | regression-covered | `test_input_bundle.py` — every variant (relative, absolute, parent-escaping, dangling, directory, `/proc/self/environ`) + copy/descent races + shared-builder | Confirm ≤1 representative variant live; regression suite is authoritative. Folded into the hostile-MR [record](record-gitlab-hostile-mr.md). |
| Gate/config artifact integrity logic (SPEC-33) | regression-covered | `test_consensus_integrity.py` (run-id/digest/critic forgery) + `test_gate.py` (post-result run-id binding, gate precedence) | Forged evidence from another run/config fails closed in consensus and gate. This covers the *integrity logic* only — the *live* forged-gate-at-a-credential-boundary probe stays release-gating in the hostile-MR row above. |
| GitHub revision failures (SPEC-34) | regression-covered | `test_input_bundle.py`, `test_github_platform.py` — all three race boundaries incl. manifest-finalization, plus HTTP 406 | Live-optional; **waived** for 1.0.0 with a reason registered under `verification.evidence_waivers`. The **stale-head** boundary was nonetheless reproduced live in run `30173073036` attempt 7 (`post` returned `status: stale_head` and wrote nothing; `gate` returned `passed_stale_head`). The other two boundaries and the 406 path rest on the regression suite. [record](record-github-revision-failures.md) |

### Supplemental experimental evidence (not release-gating)

| Suite | Status | Evidence |
|---|---|---|
| Cursor reviewer real-run adapter and critique | **Observed; SPEC-21 partial; historical supporting evidence only** | [Supplemental record](record-cursor-real-runs.md): private GitLab pipeline `185695` and public GitHub workflow `30080420563` both produced successful, resolution-eligible Cursor artifacts and full panels. Both recorded `model: auto`; neither exercised the hostile permission-denial prompt or the 1.0.1 image pair. |
| OpenCode structured-output rollout canary (SPEC-50) | **Observed; not bound to a released image pair** | [Supplemental record](record-opencode-structured-output-canary.md): GitLab MR `!14`, child pipeline `2755154596`, job `15864567373` on `09f4e65` images showed `status: success`, `raw_finding_count: 1`, and the `used structured_output` log line from a real provider. Two-seat panel, critique off. The status artifact has no structured-output field, so the job log is the load-bearing evidence. |

Previous GitHub dogfood runs proved workflow execution, authenticated state, and
some inline posting, but explicitly did not prove a genuinely blocking required
check or all current-image lifecycle paths. Previous GitLab runs proved a real
consumer flow but not the hostile-MR deployment boundary. Those pre-1.0
acceptance records were deleted rather than archived; see
[documentation history](../history/README.md#completed-specifications-and-pre-10-acceptance-records)
for why, and `git log` for the files.

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
- **The OpenCode structured-output canary has no release-gating record.** The
  transport was observed working against a real provider — `status: success`,
  `raw_finding_count: 1`, and the `used structured_output` log line together in the
  [supplemental record](record-opencode-structured-output-canary.md) — but on `09f4e65`
  images, so it carries no `Release-*` binding and is not cited by release inputs. The
  1.0.2 release-gating panel record cannot stand in for it: that run returned a valid
  but **empty** OpenCode batch, so it fails `raw_finding_count > 0` and makes no
  transport claim. Closing this at gating tier needs one real OpenCode review on a
  released pin with a non-empty batch. It is cheap on GitHub, whose consumer workflow
  already pins the active release runtime, so no repin is required — but budget more
  than one attempt, because the seat has returned an empty batch before, and give the
  fixture an obvious defect. The critique-stage transport and the reviewer search tools
  remain regression-covered only; the search tools are closed provider-free by
  `scripts/smoke_opencode_structured_output.py`.
- **Cursor reviewer is not yet accepted for enablement.** It is an
  experimental opt-in peer seat — selected like any other through
  `AI_REVIEW_REVIEWERS`, not a substitute for one particular reviewer — with a
  separate credential and egress path.
  The supplemental [real-run record](record-cursor-real-runs.md) proves real
  execution and valid artifacts at historical coordinates only. The exact
  Composer model pin, the ask-mode product decision, a fresh final-image
  real-key fixture run, and the hostile write/shell denial smoke are still
  required. Do not add Cursor to release inputs, enable it, or advertise it as
  acceptance-complete until those required checks pass. The literal `auto`
  model is discovery-only and is never valid enablement evidence.
- **The added-file path is closed — do not re-run it as a gap.** The 1.0.0 matrix used
  modify-only fixtures to work around the GitHub `/dev/null` anchor defect, and the
  1.0.1 campaign closed the added-file half as its headline run: GitHub Chain B, PR #10,
  workflow run `30541110970` at runtime source `5817e99`, with
  `accepted_finding_count == raw_finding_count` on all three seats and inline comment
  `3682518404` posted **on the added file itself**
  ([record](record-github-current-image.md)). Re-confirmed at `09f4e65` by the SPEC-39
  Chain B run, which also added a file, reported `accepted == raw` on both seats, and
  posted comment `3769428333` on the added `src/report.py:6` (`side: RIGHT`)
  (closure evidence) —
  that one is a supplemental re-confirmation, not gating evidence. Note neither record is
  cited by the active 1.0.2 inputs, so this path is proven historically rather than
  re-proven per release.
- **The deleted-file path still has no live evidence, and it splits in two.** The 1.0.1
  added-file record states its own limit — "It does not establish deleted-file
  behavior" — and deletion is the other shape that triggered the original `/dev/null`
  anchor defect. The two halves differ in what can actually be driven:
  - **A diff that *contains* a deletion — drivable with the mock today, zero tokens, and
    only on GitHub.** This is the shape the 1.0.0 defect really broke: the raise happened
    while *scanning* the diff, before the anchor's own paths were compared, so a deleted
    file anywhere in the diff poisoned findings on **other** files. Probe: a Chain B
    fixture on **GitHub** that deletes one file while adding or modifying another carrying
    the `records[0]` marker. Assert `accepted_finding_count == raw_finding_count` **and**
    that the discussion posted on the marker file at the marker line. **A GitLab run does
    not close this and must not be accepted as closure:** GitLab's diff text is
    synthesized from API metadata as `--- a/<old>` / `+++ b/<new>` with each side falling
    back to the other, so it never emits the `/dev/null` sentinel and cannot reach the
    defect; only GitHub fetches a real git diff carrying it.
  - **A finding anchored *to* the deleted path — not drivable with the mock.** The
    deterministic mock cannot express it: both anchor selectors in
    `ai-review/src/ai_review/mock_reviewer.py` skip any diff line whose `kind` is not
    `added`, and `_anchor` hard-codes `side: "new"` with `old_line: None`, so a
    deleting-only fixture returns an empty batch — a failure that mimics the very defect
    it was meant to test. The product path itself is supported: `side` accepts `old`, and
    both platforms map it (GitLab `position.old_path`/`old_line`, GitHub `LEFT`). Closing
    it needs either a **real two-seat run** over a diff that deletes code — still
    non-deterministic, because the model must choose to anchor in the removed region — or
    a deletion-capable mock scenario, the deterministic option, not currently scheduled.
    It must be **two** seats, not one: a one-name roster is rejected by
    `_MINIMUM_PANEL_REVIEWERS`, and a single enabled seat fails threshold clamping while
    `votes_required` is `2`, so one seat is unreachable both ways without a custom image
    carrying a one-reviewer `review.yaml` (project-supplied policy is SPEC-47, still
    proposed).

    **Two seats are the floor, but they are not automatically sufficient — an inline
    object appears only on specific decision paths, and which ones exist depends on
    whether critique runs.** A `fyi` decision is the failure mode to watch: with the
    shipped `fyi_mode: summary_comment` it yields a summary line and **no platform
    position at all**, and the summary's own location text prefers `new_path`/`new_line`,
    so it cannot even distinguish sides. A `fyi` outcome cannot close this gap.

    - **Critique off** (what this probe prescribes, to keep it cheap) — `decision_for_group`
      surfaces on exactly two paths: `vote_count >= votes_required` (2 seats emitting a
      matching finding), or `vote_count == 1` with `final_severity: blocker` in a
      `severity_policy.single_reviewer_blocker` category (`[security, correctness]`).
      Anything else is `fyi`.
    - **Critique on** (the shipped default: `critique.enabled: true`) — a **third** path
      opens. `_recompute_group_decision` escalates a `fyi` group to `surface` when
      `critique_support_count > 0` and `critique.allow_advisory_escalation` is true, which
      is the shipped default; the escalation stays non-blocking. For this probe that is
      the *most achievable* route, because eligible critics exclude the group's own
      contributing reviewers — on a two-seat panel the peer seat that did **not** find the
      deletion-anchored issue is exactly the seat that can support it into an inline post.
      Critique on also adds a way to **lose** the finding: a group is dropped outright when
      `critique_noise_count` exceeds half the eligible critics. Choose deliberately —
      critique on costs critique tokens and risks the drop, critique off needs either two
      independent matching findings or a qualifying blocker.

    So the probe is non-deterministic on two axes: the model must anchor in the removed
    region **and** the group must land on a surfacing path. Before treating a run as
    proof, read the consensus artifact and record which path it took — `decision`,
    `vote_count` and `contributing_reviewers`, `final_severity`, and
    `critique_support_count` (plus `critique_noise_count` if the group was dropped).
    Without `critique_support_count` an escalated advisory is indistinguishable from a
    quorum surface.

    For the record, the `09f4e65` runs ran with **critique disabled** and so exercised
    only the first two paths — which is why they cannot be cited for the third: GitHub took
    the single-reviewer-blocker path (`vote_count: 1`,
    `contributing_reviewers: ['claude']`, `final_severity: blocker`,
    `critique_support_count: 0`) and GitLab took the quorum path (`vote_count: 2`,
    `['claude', 'opencode']`, `final_severity: major`, `critique_support_count: 0`).
    Neither shows a non-blocker surfacing without quorum.

  For either half, **counts alone never establish placement**: `accepted == raw` plus
  "some inline discussion" is equally satisfied by a comment on the wrong file or on the
  new side — and a `fyi` decision produces no inline object at all. Assert the posted
  object's own coordinates — for a deleted-path anchor, GitLab `position.old_path` equal
  to the deleted path with `position.old_line` set to the pre-image line and
  `position.new_line` null, or GitHub `side: "LEFT"` with `line` as the pre-image line —
  and record the decision inputs that produced it: `decision`, `vote_count`,
  `contributing_reviewers`, `final_severity`, and `critique_support_count`. Those five
  distinguish a quorum surface from a single-reviewer-blocker surface from a
  critique-escalated advisory; fewer than five leaves the route ambiguous. See the carried
  coverage-gap table in the [runbook](RUNBOOK.md).

<!-- verified-claims:
config panel.quorum.votes_required == 2
config posting.fyi_mode == summary_comment
config severity_policy.single_reviewer_blocker.categories == [security, correctness]
config critique.enabled == true
config critique.allow_advisory_escalation == true
const ai_review.config._MINIMUM_PANEL_REVIEWERS == 2
source ai-review/src/ai_review/consensus.py contains if len(reviewers) >= votes_required:
source ai-review/src/ai_review/critique.py contains int(group["critique_support_count"]) > 0
source ai-review/src/ai_review/critique.py contains int(group["critique_noise_count"]) > len(eligible_critics) / 2
source ai-review/src/ai_review/critique.py contains if critic not in set(group["contributing_reviewers"])
source ai-review/src/ai_review/mock_reviewer.py contains if line.kind != "added":
source ai-review/src/ai_review/mock_reviewer.py contains "side": "new",
source ai-review/src/ai_review/platform/gitlab.py contains chunks.append(f"--- a/{old_path}")
source ai-review/src/ai_review/summary_render.py contains anchor.get("new_path") or anchor.get("old_path")
-->

> **The premises above are machine-checked.** The paragraphs in this section assert
> specific runtime behavior — panel floors, decision paths, mock capabilities, per-platform
> diff shapes — and every one of those assertions was, at some point, written from
> narrative rather than from the code. The `verified-claims` comment beside this entry
> declares those premises, and `scripts/check_docs.py` proves each against
> `ai-review/config/review.yaml`, module constants, and source text on every
> `make docs-check`. If you change a threshold, a decision branch, or the mock's anchor
> selection, this section fails until the prose and the claim are both updated. Add a claim
> whenever you write a new behavioral assertion here; the checker cannot protect a premise
> nobody declared, and it cannot tell you an assertion is too weak to prove what you
> claim.
- **`render-body.v3` has no live rendering or migration evidence.** The format
  changed after `v1.0.0`, so no live run has confirmed that prose renders as wrapping
  code spans on either platform without autolink/mention/issue-reference expansion,
  nor that a thread authored by an older image receives exactly one body update
  (`updated_discussions=1`, `created=0`, same `issue_id`). Goldens prove generation;
  only a real comment proves rendering.
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

## Consumer projects

The live runs use two long-lived, operator-controlled, public scratch consumers —
`seanleecoder/code-tribunal-demo` on GitHub and on GitLab (project id `84667714`),
plus the protected GitLab template project `seanleecoder/code-tribunal-ci-template`.
Reuse them every release: they already carry the required-check ruleset, protected
credentials, the mock-variable mapping, the `evidence/` fixture branches, and the
pre-v3 bot threads the posted-body refresh check needs. See
[`CONSUMER-PROJECTS.md`](CONSUMER-PROJECTS.md).

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
