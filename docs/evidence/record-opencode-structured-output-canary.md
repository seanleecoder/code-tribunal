# Evidence record: GitLab / OpenCode structured-output rollout canary / 2026-08-12

Status: real-provider canary observed (non-release; SPEC-50 rollout)

Release-binding: none (supplemental; not cited by release inputs)

> Sanitized record. Never record credentials, CLI session material, proprietary
> source, or sensitive model content.

This supplemental record captures the real-provider canary that
[SPEC-50](../history/specs/spec-50-opencode-structured-reviewer-output.md) lists as its
last acceptance item: one live OpenCode review showing `status: success`,
`raw_finding_count > 0`, and the `used structured_output` log line. All three were
observed together in the run below.

It is **not** cited by `release/release-inputs.json` and does not add a row to the 1.0.2
release-gating matrix. The run used images built from `09f4e65`, which is neither the
`1.0-e2464a9` pair SPEC-50's rollout note named nor the released 1.0.2 pair, so it
cannot carry the `Release-*` bindings an active-release record requires. The
`Status:` line above is deliberately not the bare word `passed` so that citing this
record by mistake fails the release check loudly instead of binding silently.

The run was made while validating the SPEC-39 milestone B decomposition; the
structured-output observation was incidental to that purpose. See the
[SPEC-39 closure validation](../history/specs/spec-39-simplification-deletion.md#closure-validation-at-09f4e65)
for the rest of that campaign.

## Identity

- Platform and version: GitLab.com SaaS, hosted runner
  `3-green.saas-linux-small-amd64`.
- Date/time and timezone: 2026-08-12, review job 18:53:23Z–18:54:15Z (UTC).
- Deployment topology: hardened mirrored child pipeline — a single `ai_review` trigger
  job with `inherit.variables: false`, `strategy: mirror`, both forwarding flags false,
  and two same-project includes at one identical template SHA.
- Consumer/template project: `seanleecoder/code-tribunal-demo` (project `84667714`),
  templates from `seanleecoder/code-tribunal-ci-template`.
- Change request: MR `!14`, from the protected source branch
  `evidence/spec39-real-09f4e65`.
- Pipeline/workflow run: parent pipeline `2755154417`, child pipeline `2755154596`.
- Relevant job IDs: `15864567373` (`AI review: [opencode]` — the canary job),
  `15864567370` (prepare).
- Source commit: `45b64ac43863fc18bab8843b8e531a6eaaea22db`.
- Template/workflow commit: `299ca5035e72fd0bd2a1ba61e625135c78c36527`.
- Base image tag and digest:
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base:1.0-09f4e659333746b3d3a307e80cc70a1078c3c162@sha256:482dd13985457f1b2106ecfe670d15ae2b99e606eaf2a1f6b5b8863d28db3b2f`.
- Reviewer image tag and digest:
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:1.0-09f4e659333746b3d3a307e80cc70a1078c3c162@sha256:782e2d1faa70a1d4ad112d86458f66f6a1523ea9f61f1d2fc2916142daeb84b9`.
- Effective-config digest recorded by the review job:
  `8b2734a01b8b0a9cf7995866ca3e22b8bb11f5ee27aed769ea80a2a4572c84a1`.

## Preconditions

- Protected/masked variables or GitHub secret configuration verified: `OPENROUTER_API_KEY`
  and `GITLAB_TOKEN` are protected and masked, and the source branch was protected
  before the MR opened, so both injected. No secret value is recorded here.
- Required pipeline/check configuration verified: the mock adapter was **off**. No
  `AI_REVIEW_LOCAL_MOCK` / `AI_REVIEW_ALLOW_LOCAL_MOCK` / `AI_REVIEW_MOCK_SCENARIO`
  project variable existed, so the template defaults applied and every
  `AI_REVIEW_REQUIRE_REAL_*` flag stayed at `1` — the seat could not have silently
  fallen back to a canned batch. The reviewed diff contained one deliberate correctness
  defect for the panel to find.
- Panel scope: a two-seat panel (Claude and OpenCode) with critique disabled, selected
  through the per-seat `AI_REVIEW_*_ENABLED` variables. The OpenCode seat ran its
  default configured model over the OpenRouter route.
- Expected behavior: the OpenCode seat obtains its finding batch through the
  structured-output transport rather than the text fallback, and reports it honestly in
  the job log.

## Actual result

- Stage outcomes: prepare, both review seats, consensus, post, and gate all succeeded.
  The canary job `15864567373` reported `status: success` in `duration_ms: 14276`.
- OpenCode review status artifact (`out/status/opencode.json`,
  `schema_version: adapter_status.v1`): `status: success`, `raw_finding_count: 1`,
  `accepted_finding_count: 1`, `dropped_finding_count: 0`,
  `usable_for_resolution: true`, `error_class: null`,
  `error_message_redacted: null`, `run_id: gl-2755154596-15864567370`.
- Job log line, verbatim and in full:

  ~~~text
  ai-review: review adapter used structured_output
  ~~~

- Platform objects created/updated/resolved: the panel converged on one correctness
  finding and `post` created one inline discussion plus the machine-owned state note.
  Those belong to the SPEC-39 validation and are recorded there; they are not part of
  this canary's claim.
- Consensus/post/gate summary: `panel_convergence: 1.0` across the two seats;
  `block_merge: false`; gate passed. The finding's own text is model output and is
  deliberately not reproduced here.

## Audit

- Artifacts inspected: `out/status/opencode.json` and `out/findings/opencode.json`,
  downloaded from job `15864567373`. Both counts above are read from the status
  artifact, not inferred from the log.
- Logs inspected: the complete job trace of `15864567373`. It contains exactly one
  `ai-review:` adapter line, quoted in full above.
- **Why the log is the load-bearing evidence here.** `adapter_status.schema.json` has no
  structured-output field, so the artifact can only prove `status: success` and
  `raw_finding_count > 0`. The transport claim rests entirely on the log line — which is
  admissible because the degraded path is required to log a different wording
  (`response carried no structured_output; parsed answer text`) and SPEC-50 makes that
  distinction a non-negotiable contract. The line therefore cannot have been produced by
  the text fallback.
- Credential values absent: confirmed. `python3 scripts/scan_evidence_leaks.py` over the
  downloaded artifacts and the saved trace reported
  `OK: no credential material detected (scanned 3 files, 0.0 MB, 10 detectors)`. As that
  script's own notice states, pattern and entropy detectors cannot prove absence; no
  exact-value audit was performed and none is claimed.
- Sensitive model content omitted from this record: yes. No finding title, body,
  suggestion, reasoning text, prompt, or session material is reproduced. Only counts,
  statuses, digests, and sanitized identifiers appear.
- Job artifacts expire 2026-08-19, which is why every count is transcribed inline above
  rather than cited by reference. The job trace and the platform objects persist.
- Known unexercised paths:
  - **No `grep`/ripgrep claim.** The job log carries no tool-call output, so this run
    does not show SPEC-51's expectation that a canary "should show a working `grep` as
    well". That gap was closed provider-free instead, by
    `scripts/smoke_opencode_structured_output.py`, which forces a real `grep` through
    the pinned ripgrep inside the sanitized review root and requires a non-empty result.
  - **Critique stage not exercised.** Critique was disabled, so no
    `critique adapter used structured_output` line exists. The critique transport
    remains regression-covered only.
  - **Not a released image pair.** The images are `09f4e65` builds, so this proves
    nothing about any tagged release's pins.
  - **One seat, one model, one run.** No claim is made about other seats, other models,
    or repeatability.

## Verdict

Passed, scoped. On the recorded topology, source, and images, the OpenCode seat obtained
a non-empty, schema-valid, resolution-eligible finding batch from a real provider through
the **structured-output transport**, and said so with the honest log wording that the
text fallback is forbidden to emit. This satisfies the three conditions SPEC-50's rollout
canary names.

It does **not** establish a release-gating pass at any released image pair, does not
cover the critique stage or the reviewer search tools, and must not be promoted into a
release evidence matrix. A release-gating canary bound to a released pin is still
unrecorded; see the known-gaps note in [`README.md`](README.md).
