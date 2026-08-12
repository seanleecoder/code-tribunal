# Improvement specifications and status

These files are requirement and implementation history. They are not current
product documentation. Where a spec conflicts with code, schemas, tests,
canonical templates, or the task-oriented docs, the executable/current contract
wins.

## Current status

| Specs | Status | Evidence or remaining work |
|---|---|---|
| SPEC-01–05 | Complete | Initial quality, security, and documentation foundations shipped before Phase 1. |
| SPEC-06 | Complete | Trust auditor/template tests plus the recorded 1.0.0 [hostile-MR evidence](../evidence/record-gitlab-hostile-mr.md) (scoped: unprotected-ref MR in the hardened child). |
| SPEC-07–19 | Complete | State, consensus, correctness, platform, supply-chain, and reviewer optimization changes are represented by tests/changelog. |
| [SPEC-20](spec-20-reviewer-usage-accounting.md), [SPEC-22](spec-22-project-rules-and-learning.md) | Proposed (stale baselines — see the refresh notes in each) | Reviewer usage accounting and project learning/rules are not advertised product features. SPEC-20's OpenCode extraction plan predates the SPEC-50 session transport and the current CLI pins; SPEC-22 predates SPEC-47's trusted target-revision policy channel and the reviewer roster. Rebase both before executing them. |
| [SPEC-21](spec-21-cursor-cli-reviewer.md) | Implemented (experimental); historical supporting evidence; Cursor enablement outstanding | Cursor is now a peer seat selected by `AI_REVIEW_REVIEWERS`, not a substitute for the OpenCode seat; the substitution recipe and CI wiring in the specification are superseded in place. The opt-in Cursor reviewer ships disabled: adapter, config block, `CURSOR_API_KEY` credential separation, supply-chain notes, and the permission smoke harness landed with unit coverage. Historical private GitLab pipeline `185695` and [public GitHub run](https://github.com/seanleecoder/code-tribunal/actions/runs/30080420563) prove real execution and valid finding/critique artifacts at historical coordinates. The canonical [SPEC-21 acceptance checklist](spec-21-cursor-cli-reviewer.md#cursor-enablement-closure-checklist) covers the remaining enablement decisions and evidence. The [supplemental record](../evidence/record-cursor-real-runs.md) is not a release-gating record. |
| [SPEC-23–30](../history/specs/README.md#completed-specifications) | Complete history | Implemented on `main`; requirements archived in [history specs](../history/specs/README.md) for provenance. |
| [SPEC-31–36](../history/specs/README.md#completed-specifications) | Complete on `main` | Snapshot containment, reviewer validity, artifact/config integrity, revision binding, distribution contract, and quality/type gates landed. |
| [SPEC-37](../history/specs/spec-37-final-release-artifacts.md) | Complete at `v1.0.0` | Runtime source `88bc941` frozen, images published and attested, release commit `3ad443e` tagged with a validated external manifest. |
| [SPEC-38](../history/specs/spec-38-documentation-evidence-restructure.md) | Complete at `v1.0.0` | Docs/checks implemented and the required live evidence is recorded in the [evidence matrix](../evidence/README.md). |
| [SPEC-39](../history/specs/spec-39-simplification-deletion.md) | Complete on `main` — archived to [history](../history/specs/spec-39-simplification-deletion.md) | Milestone A's container-only contract cleanup landed pre-1.0; Milestone B landed post-1.0 in `09f4e65` (PR #118) as ordered extraction commits, leaving `post.py` 53, `consensus.py` 577, and `adapter_runner.py` 463 lines with `posting.py`, `state_plan.py`, `critique.py`, and `summary_render.py` extracted out. Golden consensus and post→gate E2E fixtures are byte-identical without `make update-golden`, and `plan_state` is now tested without a platform client — the acceptance criterion that motivated waiving the SPEC-45/46 sequencing. Items 5 and 6 were substantially satisfied already; consensus artifact I/O was deliberately not split because item 4's own import-coupling condition fails there, and `scripts/check_supply_chain_pins.py` stays standalone because it runs from `/opt/scripts` inside the base image. Behavior was confirmed live at `09f4e65` on both consumer platforms — real two-seat panel plus the full deterministic mock lifecycle and blocking gate ([closure evidence](../history/specs/spec-39-simplification-deletion.md#closure-validation-at-09f4e65), scoped confirmation runs, not release-gating records). |
| [SPEC-40](../history/specs/spec-40-1.0-finalization-execution-plan.md) | Complete at `v1.0.0` | The coding-agent/human-operator handoff it coordinates was executed for the 1.0.0 release. |
| [SPEC-41](spec-41-reviewer-confidence-default.md) | Proposed (post-1.0) | A reviewer that omits the required `confidence` loses every finding and silently degrades the panel; observed live with a weak default model. Still unfixed; the incident's default model id is historical, and SPEC-50's schema transport narrows exposure on the OpenCode seat only. |
| [SPEC-42](spec-42-wontfix-gate-semantics.md) | Proposed (post-1.0) | A human `wontfix` persists and suppresses re-posting but never clears the merge gate; decide the intended escape hatch. |
| [SPEC-43](spec-43-in-pipeline-trusted-image.md) | Proposed (post-1.0) | A consumer config can substitute the pinned images; nothing in-pipeline verifies the running image. |
| [SPEC-44](../history/specs/spec-44-literal-model-output.md) | Complete on `main` — archived to [history](../history/specs/spec-44-literal-model-output.md) | Shipped as `render-body.v3`; SPEC-45 and SPEC-46 still build on its renderer boundary and section descriptors. |
| [SPEC-45](spec-45-critique-provenance.md) | Proposed (post-1.0; SPEC-44 prerequisite met) | Retain all effective duplicate/noise/dispute reasoning, display it tiered behind a disclosure, and record the suppression reason for an opt-in majority-noise audit. Its one-refresh packaging constraint is no longer an open decision: `render-body.v3` shipped in `v1.0.2` without SPEC-45, so the first body-refresh wave is already spent across maintainers' open change requests. What remains is whether a later `render-body.v4` spends a second wave — so SPEC-45 should land inside a release that changes the body format for another reason, or accept that second wave explicitly. |
| [SPEC-46](spec-46-unanchored-advisories.md) | Proposed (post-1.0; SPEC-44 prerequisite met, after SPEC-45) | Carry genuinely non-line-anchored concerns as summary-only, reviewer-attributed advisories with no consensus or lifecycle authority. |
| [SPEC-47](spec-47-trusted-project-review-config.md) | Proposed (post-1.0) | Read complete project policy only from the immutable target/base revision, preserve trusted runtime ownership, and bind the resolved policy through every stage. |
| [SPEC-48](spec-48-auditable-review-scope-exclusions.md) | Proposed (post-1.0; after SPEC-47) | Apply trusted generated/lockfile/vendored exclusions only after a complete diff is fetched, with explicit coverage provenance and a first-class no-reviewable-changes gate. |
| [SPEC-49](spec-49-opencode-session-title-inference.md) | Superseded by SPEC-50 (title decision still in force) | Give every OpenCode review/critique session a deterministic data-free title, preventing automatic title inference from making a second model request. |
| [SPEC-50](../history/specs/spec-50-opencode-structured-reviewer-output.md) | Complete on `main` — archived to [history](../history/specs/spec-50-opencode-structured-reviewer-output.md) | Obtain OpenCode reviewer output through the structured-output transport instead of parsing model prose, and stop treating reasoning parts as answer text in any adapter. Image publication and the merge-gating provider-free stub preflight landed earlier; the last item, the real-OpenRouter rollout canary, is now recorded — `status: success`, `raw_finding_count: 1`, and the `used structured_output` log line together on the OpenCode seat at `09f4e65` ([record](../evidence/record-opencode-structured-output-canary.md)). That record is **supplemental, not release-gating**, because the run used unreleased images; the release-gating equivalent and the critique-stage transport are tracked as [evidence gaps](../evidence/README.md#known-gaps-and-missing-evidence), not as open specification work. |
| [SPEC-51](../history/specs/spec-51-opencode-search-tool-reach.md) | Complete on `main` — archived to [history](../history/specs/spec-51-opencode-search-tool-reach.md) | In the published `1.0-e2464a9` images; its deferred live canary was closed provider-free by `scripts/smoke_opencode_structured_output.py`, which forces a real `grep` with a non-empty result inside the sanitized root. |
| [SPEC-53](spec-53-stringified-structured-output.md) | Proposed (post-1.0) | Normalize provider-stringified structured reviewer output once at the shared runner boundary instead of at the OpenCode client, covering list roots, the text fallback, whole stringified arrays, and every seat. PR #116 shipped the narrow per-item case in 1.0.2; the remaining shapes still cost a finding or a whole reviewer or critic seat, always failing closed. Source-only, provider-free acceptance. Deliberately does not loosen `finalize_critique_batch`. |

## Active dependency order

Steps 1–5 of the pre-1.0 order (regression gates, documentation/checking changes,
release binding, the live evidence matrix, and linking claims to evidence) were
completed for `v1.0.0`. Remaining order:

1. Keep SPEC-31–36 and SPEC-39 regression tests green — including, for SPEC-39
   milestone B, the golden consensus and post→gate E2E fixtures and the
   import-boundary test that keeps the extracted planning modules platform-free.
2. **Done at `v1.0.1`.** The `/dev/null` anchor fix shipped in `1f83978` and the
   added-file path has a live green run — GitHub Chain B, PR #10, workflow run
   `30541110970` at runtime source `5817e99`
   ([record](../evidence/record-github-current-image.md)).
3. **Done — the [SPEC-50](../history/specs/spec-50-opencode-structured-reviewer-output.md)
   rollout canary is recorded**, and the specification is
   [archived to history](../history/specs/spec-50-opencode-structured-reviewer-output.md).
   All three required conditions — `status: success`, `raw_finding_count > 0`, and the
   `used structured_output` log line — were observed together on the OpenCode seat
   against a real provider during the SPEC-39 closure validation: GitLab demo MR `!14`,
   child pipeline `2755154596`, job `15864567373`
   ([record](../evidence/record-opencode-structured-output-canary.md)). Note that the
   run used `09f4e65` images, not the `1.0-e2464a9` pair this item originally named —
   a pin that was already stale, since 1.0.2 ships `54dffa1`. What remains is an
   **evidence-tier** matter rather than specification work: no record binds the canary
   to a *released* image pair, and the 1.0.2 gating panel record cannot substitute
   because its OpenCode batch was valid but empty. That gap, and the unexercised
   critique-stage transport, are tracked in the
   [evidence gaps](../evidence/README.md#known-gaps-and-missing-evidence) and the
   runbook's carried-coverage table.
4. Land [SPEC-53](spec-53-stringified-structured-output.md), which moves the PR #116
   stringified-item workaround from the OpenCode client to the shared runner boundary and
   covers the shapes #116 left open. It is small, source-only, and independent of the
   SPEC-44/45/46 review-output sequence. It neither blocked nor was blocked by the
   step-3 canary, and the two must not be conflated: that canary ran images that predate
   this change and so cannot emit its decode log line. Acceptance here is provider-free;
   seeing the decode line against a real provider needs a separately authorized build,
   publish, repin, and run.
5. Decide SPEC-41–43 (reviewer `confidence` handling, `wontfix` gate semantics,
   in-pipeline trusted-image enforcement). None is implemented; SPEC-43's hostile-MR
   finding still stands — nothing in-pipeline verifies the running image.
6. Continue the post-1.0 review-output sequence in order: SPEC-44 literal-safe
   rendering is implemented as `render-body.v3` and
   [archived to history](../history/specs/spec-44-literal-model-output.md); next is
   SPEC-45 critique provenance/suppression audit, then SPEC-46
   non-line-anchored advisories. SPEC-45 and SPEC-46 rely on the renderer boundary
   and the summary section-descriptor refactor established by SPEC-44; SPEC-46 is
   handed off after SPEC-45 so its summary priority composes with the
   critique-disposition section, and degrades cleanly if SPEC-45 slips.
   SPEC-45 owns the later critique disclosure and `render-body.v4` change. That release
   decision is now closed by shipping: `render-body.v3` went out in `v1.0.2` without
   SPEC-45, spending the first body-refresh wave. So the remaining question is only
   whether `v4` spends a second one — still a decision to make deliberately rather than
   by default, but a narrower one.
   Critique of advisories is deliberately deferred out of SPEC-46 and needs its own
   specification before the advisory caps there are raised.
   Product-policy decisions for this sequence — critique display tiering, the opt-in
   disposition audit, the advisory cap default, invalid-scope handling, and the SPEC-44
   rendering boundary — are ratified in
   [ADR-0002](../decisions/0002-post-1.0-review-output-policy.md). All three specifications
   cite it from their "Deviations from the original draft" sections, which also record the
   SPEC-44 and SPEC-46 effort re-estimates from M to L.
7. Establish trusted project-policy delivery with
   [SPEC-47](spec-47-trusted-project-review-config.md) before any consumer can
   narrow review scope. Then, and only then, deliver
   [SPEC-48](spec-48-auditable-review-scope-exclusions.md): it depends on
   SPEC-47's target-revision source selection, sealed runtime ownership, and
   effective-config binding. Neither specification is currently implemented.
8. Complete the required [SPEC-21](spec-21-cursor-cli-reviewer.md) gate before
   enabling or advertising the Cursor reviewer. It does not block a release while
   Cursor stays off the default roster. Roster selection changed *how* Cursor is
   enabled, not the evidence it owes. Its acceptance checklist is canonical;
   keep the reviewer disabled until the complete scoped evidence set passes.

## Historical indexes

- [Completion audit](../history/specs/completion-audit.md)
- [Completed specification history](../history/specs/README.md)
- [Paused plans](../archived-improvement-plans/README.md)
- [Live and legacy evidence](../history/README.md)

Completed plans are archived in [`docs/history/specs/`](../history/specs/); their
implementation sequencing and image examples are historical. The temporary
redirect stubs that held `docs/history/acceptance/` and
`docs/history/completed-specs/` open for one compatibility release were retired
after `v1.0.0`, and their content now lives in
[documentation history](../history/README.md). The remaining SPEC-38 stubs at
`docs/ARCHITECTURE.md`, `docs/CONSENSUS.md`, and `docs/REVISION_LIFECYCLE.md`
were retired on the same basis; their current replacements are
[development/architecture.md](../development/architecture.md),
[reference/consensus.md](../reference/consensus.md), and
[reference/revision-lifecycle.md](../reference/revision-lifecycle.md).
