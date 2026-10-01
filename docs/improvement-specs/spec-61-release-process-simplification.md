# SPEC-61 — Simplify the release process

- **Severity:** Medium (operator toil and late-discovered drift) · **Effort:** M overall
- **Status:** Phases 1–2 delivered (#137–#146) and live-validated, except for the panel
  record-generation check (see Handover). Phase 3 is [SPEC-62](spec-62-scripted-release-finalization.md);
  Phase 4 is [SPEC-63](spec-63-documentation-drift-checks.md).
- **Depends on:** ADR-0003 source-of-truth map; the Candidate Canary (#129, #132).

## Why

The 2.0.0 release took seven pull requests (#130–#136), two image candidates,
about thirty manual operator actions, and eight hand-written evidence records. Its
live checks earned their cost. The canary found a consensus crash (#131) and a
doubled GitHub panel (#132), and the lifecycle chain exposed an expired resolve
token that silently disabled `wontfix` (addressed by SPEC-64).
Most of the time, however, went to:
- overlapping checks
- the same content copied by hand into four places
- hand-driven deterministic steps
- drift discovered mid-campaign
- docs swept at release time

## Constraints (binding on all phases)

- Keep ADR-0003. Release identity comes from `release/release-inputs.json` and
  source-bound evidence records. No new release authority, no parallel registry of
  evidence rows.
- Keep the two-key property: waiving a row requires a change to the release
  authority file *and* to the waived record.
- Signing stays local and manual.
- **No scheduled or per-PR canary.** A real four-seat panel per run is not worth its
  token cost (maintainer decision). The canary stays a release-time, manually
  dispatched gate. Zero-token campaigns may be dispatched alone for validation.

## Delivered

| Phase | Item | PR | Live validation |
|---|---|---|---|
| 1 | Canary owns image identity; separate image record retired (banner, kept for history) | #140 | — |
| 1 | Single-source waiver reasons (`Release-evidence-waived: registered`; reason only in `evidence_waivers`) | #138 | — |
| 1 | Evidence index has no per-release sections; release notes are the single copy | #140 | — |
| 1 | `make demo-preflight`, a read-only demo drift check | #139 | reports both demos ready |
| 2 | `campaigns` selector input (`panel`, `lifecycle`, `hostile`; default all) | #141 | — |
| 2 | GitHub mock lifecycle (Chain B incl. stale head) | #141 | runs `36602259241`, `36630018877`, `36855687237` green |
| 2 | GitLab mock lifecycle (temp `AI_REVIEW_MOCK_SCENARIO` project variable) | #142, #145 | `36630018877`, `36855687237` green |
| 2 | GitLab hostile-MR probe | #143, #146 | run `36863857862` green; all three credentials withheld |
| 2 | `make evidence-records RUN=<id>` generates records from summaries | #144 | both lifecycle records and hostile record from `36927853324` generated in scratch and accepted by `validate_evidence_records` |

Design decisions taken during delivery:
- **Waivers:** option B, chosen over waived-rows-without-records (needs a row
  registry) and reason-only-in-record (breaks two-key, bumps the schema).
- **GitLab scenario:** switched through a temporary project variable, because the
  child topology forwards no pipeline variables. Mock mode lives only on the
  canary's temporary template branch, so a leftover variable is inert, and
  `demo-preflight` flags it.
- **GitLab branch protection:** the canary protects the branch *name* before
  pushing, then polls until GitLab reports the branch protected. GitLab applies
  protection asynchronously, and run `36684239922` lost `GITLAB_TOKEN` to that race.
- **Hostile trace parsing:** GitLab log lines carry a timestamp and stream prefix
  (`2026-10-01T11:32:00.124914Z 01O `), so output is normalized before the
  whole-line comparison. Run `36855687237` misread every credential as `unknown`.
- **Body-format refresh:** not automated. It's needed only when `render-body.vN`
  changes, and it requires a thread authored by the previous image. It stays a
  manual RUNBOOK step.

## Handover

### Phase 2 validation tail

The zero-token `lifecycle,hostile` dispatch
[`36927853324`](https://github.com/seanleecoder/code-tribunal/actions/runs/36927853324)
passed both platforms and hostile probes against the published 2.0.0 image pair.
The current loader generated all three records into a scratch directory, and
`validate_evidence_records` accepted their exact candidate bindings. Its actual
workflow metadata, artifact inventory, and summaries are captured in
`ai-review/tests/fixtures/release/canary-lifecycle-hostile.json` for offline
release-command tests.

The **panel record** still needs a real panel run, which spends tokens. The next
release's full canary will prove it. Older runs missing current job families stay
rejected, and runs whose overall conclusion is not `success` stay rejected even
when some summaries passed (`36855687237`).

### Next phases

- [SPEC-62](spec-62-scripted-release-finalization.md): `release-repin`,
  `release-finalize`, `release-manifest`, a tag-push publish workflow, and the
  next-draft reset.
- [SPEC-63](spec-63-documentation-drift-checks.md): code-span path and test-name
  checks in `check_docs.py`.
- SPEC-64 is delivered: failed thread resolutions and reopenings report
  `partial_failed`, retain the previous disposition for a retry, and fail `post`.

### Operational follow-ups carried from 2.0.0

- **COMPAT-005 is retired.** The gate-free 2.0.0 release satisfies its removal
  condition; the auditor no longer reserves `ai_review_gate`.
- **GitLab body refresh not run.** The 1.0.0-format GitLab note `3601861614` on demo
  MR !11 has not been refreshed live (RUNBOOK coverage-gap table).
- **The tag shows "Unverified" on GitHub.** The maintainer's SSH key isn't
  registered as a GitHub *signing* key. That's an account action
  (`release-process.md#tag-signing`).
- **Draft state.** `release/release-inputs.json` is the `2.0.1` draft (#136).

### Working conventions in this repo

- Run `make quality` with the repo venv first on `PATH`
  (`PATH="$PWD/.venv/bin:$PATH" make quality`); docs-check needs PyYAML from it.
- Lychee is CI-only. Locally:
  - install it with `python scripts/check_markdown_links.py install --cache-dir <dir> --bin-dir <dir>`
  - run `python scripts/check_markdown_links.py --lychee <bin>`
- One boundary per PR, and register any compatibility path
  ([AGENTS.md](../../AGENTS.md)). Canary changes can only be validated live after
  merge, because the workflow runs from protected `main`, so test against the real
  artifact formats first. Two Phase 2 bugs came from fixtures simpler than reality.

## Target shape (after SPEC-62)

1. Merge, which freezes `R`.
2. Run `make demo-preflight`.
3. Dispatch one canary.
4. `make evidence-records RUN=<id>`, then `make release-repin` and
   `make release-finalize`, all reviewed as one release PR.
5. Sign and push the tag; publishing is automatic.
