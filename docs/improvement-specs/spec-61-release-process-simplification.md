# SPEC-61 — Simplify the release process

- **Severity:** Medium (operator toil and late-discovered drift) · **Effort:** M overall; Phase 1 S
- **Depends on:** ADR-0003 source-of-truth map; the Candidate Canary (#129, #132).

## Why

The 2.0.0 release took seven pull requests (#130–#136), two image candidates,
about thirty manual operator actions, and eight hand-written evidence records. Its
live checks earned their cost. The canary found a consensus crash (#131) and a
doubled GitHub panel (#132). The lifecycle chain exposed an expired resolve token
that silently disabled `wontfix`. Most of the time, however, went to redundant
bookkeeping, hand-driven steps, and drift discovered mid-campaign:

- **Redundant checks.** Image identity was verified by hand. Then the canary's
  `verify-candidate` job verified the same digests, labels, and attestations again.
- **The same content, copied by hand.** A waiver reason had to match byte for byte
  in the record and in `verification.evidence_waivers`. It was also repeated,
  unchecked, in the evidence index and release notes. Waived records were stamped
  with `Release-*` fields the checker ignores for them. The evidence table existed
  in four places: records, release notes, the evidence index, and the tag message.
- **Hand-driven deterministic steps.** Chain B, the body refresh, and the hostile
  probe are zero-token and scriptable, and were scripted ad hoc during the release.
- **Late drift.** The demo consumer still required the retired `gate` check. Its
  resolve token had expired, and `AI_REVIEW_MANUAL` had flipped. All three surfaced
  only while the campaign was running.
- **Docs swept at release time.** Stale test names, gate-era wording, and a wrong
  timeout fallback had accumulated across behavior PRs.

## Constraints

- Keep ADR-0003: release identity comes from `release/release-inputs.json` and
  source-bound evidence records. No new release authority, no parallel registry
  of evidence rows.
- Keep the two-key property: waiving a row requires a change to the release
  authority file *and* to the waived record.
- Signing stays local and manual.
- No scheduled or per-PR canary: a real four-seat panel on every run is not worth
  its token cost. The canary stays a release-time, manually dispatched gate.

## Phases

### Phase 1 — cut ceremony

1. **Fold image verification into the canary record.** The canary already verifies
   both digests, OCI revision labels, and provenance from protected `main`. Record
   those results in `record-candidate-canary.md`. Retire the separate
   image-publication record and the manual Step 0.
2. **Single-source waiver reasons** (option B). A waived record carries exactly one
   `Release-evidence-waived: <release_version>` line. The reason lives only in
   `verification.evidence_waivers`. The checker requires:
   - marker ⇔ declaration
   - a marker naming the release being activated, so re-waiving a row in a later
     release changes the record too
   - a non-empty declared reason No `release_inputs` schema or manifest change, and
   waived records are no longer stamped with `Release-*` fields.

   Rejected alternatives:
   - Waived rows without records: needs a row registry, which ADR-0003 forbids.
   - Reason only in the record: drops the two-key property and bumps the schema.
3. **Stop per-release evidence-index sections.** The index keeps the classification
   and the historical matrix, and links to the current release notes, whose table
   is the single copy. Release notes link to `evidence_waivers` instead of
   restating reasons.
4. **Demo preflight.** A read-only `scripts/demo_preflight.py`, run before a
   campaign, checks both demo consumers:
   - GitHub: required checks and rulesets; the demo variables, meaning mock
     variables absent, `AI_REVIEW_MANUAL`, and a roster naming `cursor`; and the
     presence of the required secrets.
   - GitLab: variable protection and absence of mock variables; the consumer's
     include SHA and the template project's pins.

### Phase 2 — automate the live campaign

5. Extend the Candidate Canary with mock-mode campaigns on its temporary branches:
   - Chain B lifecycle on both platforms: create, unchanged rerun, changed body,
     `wontfix`, reopen, stale head (GitHub), and blocker-does-not-block.
   - The GitLab hostile probe on an unprotected temporary branch.
   - A body-refresh step, only when the posted-body format version changes.

   Each writes a redacted summary. Exercising `wontfix` also covers resolve-token
   validity. Repinning the demos becomes post-release hygiene.
6. Generate evidence records from canary summaries, with the `Release-*` fields
   filled in. The operator adds only the verdict and notes.

### Phase 3 — script finalization

7. `make release-repin` rewrites every template pin from the canary inputs and
   syncs the installed workflow.
8. `make release-finalize`:
   - activates the inputs from the cited records
   - promotes the CHANGELOG
   - builds and validates the manifest
   - emits the tag message
9. A tag-push workflow re-validates the manifest and publishes the GitHub release
   with correctly named assets.

### Phase 4 — shift left

10. Extend `scripts/check_docs.py` so backticked test and repository file names in
    current documentation must exist.

## Target shape

1. Merge, which freezes `R`.
2. Run the demo preflight.
3. Dispatch one release campaign.
4. Review one release PR produced by `make release-repin` and `make release-finalize`.
5. Sign and push the tag.

## Acceptance

- Phase 1:
  - A waived row needs one reason, written once, in `release-inputs.json`.
  - `check_release_inputs.py` rejects:
    - a marker without a declaration, and a declaration without a marker
    - a marker naming another release
    - more than one waiver line
    - an empty reason
    - a legacy reason-bearing line
  - The evidence index has no per-release section.
  - The preflight exits non-zero on each drift observed in 2.0.0.
- Each later phase lands with its own acceptance criteria.
