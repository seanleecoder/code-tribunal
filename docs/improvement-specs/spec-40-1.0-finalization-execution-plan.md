# SPEC-40 — Execute and verify the 1.0 finalization sequence

- **Severity:** Release blocker coordination
- **Effort:** M implementation plus credentialed live operations
- **Depends on:** SPEC-31–39 milestone A
- **Closes:** the remaining execution work in SPEC-37 and SPEC-38

## Purpose

This is the handoff plan for the coding agent and human release operator who
finish Code Tribunal 1.0. It adds no product features. It turns the remaining
configuration fix, release-candidate freeze, image publication, live evidence,
artifact binding, and release publication into one restartable sequence.

The coding agent owns repository changes and automated verification. The human
operator owns credentials, protected platform settings, real consumer projects,
release approval, and publication. Neither role may infer that the other role's
work passed from a green unit-test run.

## Baseline to revalidate before starting

The last release audit observed all of the following. Treat the commit IDs as
historical inputs, not as permission to skip current-state discovery.

- `main` was `45ec1d3c094317703c483ff97f4aff0b04c2d596` and passed CI/image
  publication.
- The open evidence work used multiple candidate identities: records named
  `5a24b55`, template pins named `f8d8e98`, and current source was `45ec1d3`.
- `ai-review/config/review.yaml` used the bare Claude model
  `claude-haiku-4.5`, while the canonical templates route Claude through
  OpenRouter, whose provider-qualified model is
  `anthropic/claude-haiku-4.5`.
- The live-evidence matrix had one partial GitLab run and three outstanding
  rows. State continuity and actual merge blocking were not proved.
- No final `release-manifest.json`, `[1.0.0]` changelog section, `v1.0.0` tag,
  or 1.0 GitHub release existed.

If current repository or platform state differs, update this spec's execution
record or the active evidence records before changing code. Do not silently
apply stale SHAs or digests.

### Execution record — 2026-07-21

- Reconciled `origin/main` at
  `52c5313928806a565eddc44454e154f11273f075`, which includes this execution
  plan after the historical `45ec1d3` baseline.
- GitHub CI run `29816017626` and image-publication run `29816017686` both
  succeeded for that exact head. These runs are discovery inputs only; the
  commit has not been approved as runtime source `R`.
- PR #69, “Add 1.0 RC evidence runbook and test records,” remains open at
  `3e6535711ccea005e3127d7f565b9681e2c2dac0` and must be reviewed or otherwise
  dispositioned before the runtime freeze.
- Existing releases remain prereleases through `v0.4.0`; no `v1.0.0` tag or
  GitHub release was observed.
- The local worktree was clean before creating
  `codex/spec-40-phase1-finalization` for Phase 1 implementation.

## Roles

| Role | May do | Must not do |
|---|---|---|
| Coding agent | Inspect repository/GitHub state, change files, add tests and release tooling, run local checks, prepare reviewed PRs | Handle or print real credentials; alter protected settings; claim live evidence from mocks |
| Human operator | Approve/freeze source, configure protected secrets/settings, execute GitHub/GitLab live runs, verify registry/attestations, tag and publish | Hand-edit generated hashes without rerunning checks; publish after an unreviewed runtime change |
| Both | Review exact SHAs/digests, classify failures, decide whether a failed phase invalidates the RC | Continue with mismatched source, images, templates, evidence, or release metadata |

## Non-negotiable invariants

1. One immutable **runtime source commit** (`R`) produces both supported images.
2. One later **release commit** (`P`) may point templates at the images from
   `R`, but `R..P` must contain no runtime/config/schema/dependency/Dockerfile
   changes.
3. Every required live run uses templates from `P` and image digests produced
   from `R`.
4. Any runtime-affecting change after `R` invalidates its images and every live
   result collected against them. Restart at Phase 2 with a new `R`.
5. Evidence records contain sanitized identifiers and never credentials,
   proprietary source, raw sensitive model content, or private organization
   topology that has not been approved for publication.
6. `v1.0.0` is created only after all required evidence is a scoped pass and
   the release manifest binds `R`, `P`, the tag, both image digests, and the
   checked release inputs.

## Release model: two-commit bootstrap

Use the two-commit option anticipated by SPEC-37:

```text
R  runtime source ──build/attest──► base@digest + reviewer@digest
│
└── P release commit ──pins templates to R digests; adds final docs/evidence
                         v1.0.0 points to P
```

Images contain runtime from `R`. Repository templates at `P` are the supported
template distribution. The release manifest records both commits and proves
that `R..P` changed only the release allowlist. This avoids pretending that an
image can contain its own not-yet-known registry digest.

Before freezing `R`, implement all release-manifest tooling and checks that are
copied into an image. After freezing `R`, only the following paths may normally
change in `P`:

- `.github/workflows/ai-review.yml`
- `ai-review/ci/review.github-actions.yml`
- the three pin variables at the top of `ai-review/ci/review.gitlab-ci.yml`
- `CHANGELOG.md`
- `docs/history/evidence/**`
- `docs/improvement-specs/**` status only
- a release-input file under `release/`
- release notes under `release/`

If another path is necessary, a human must classify it. A runtime-affecting
path forces a new `R`; a demonstrably documentation-only path may be added to
the allowlist with an explanation and test.

The automated changed-path check operates at path granularity only. It cannot
prove that `docs/improvement-specs/**` contains “status only” edits or that an
allowed template edit changes pins and nothing else. A human must inspect the
actual `R..P` diff for those semantic claims; a passing allowlist check is not
sufficient release approval.

## Phase 0 — Reconcile and protect the worktree

**Owner:** coding agent. **Human gate:** confirm target repository and release
intent.

1. Fetch remote state and record:
   - current `main` SHA;
   - open PRs and their head SHAs;
   - dirty/untracked files;
   - latest CI and image-publication runs;
   - existing tags/releases.
2. Preserve unrelated or uncommitted human evidence. Never overwrite, discard,
   or absorb it without review.
3. Create a dedicated release-finalization branch from current `main`.
4. Compare current state with the baseline above and list every changed
   assumption in the PR description.

Suggested read-only commands:

```bash
git fetch --prune origin
git status --short --branch
git log --oneline --decorate -15 origin/main
gh pr list --state open
gh run list --branch main --limit 15
gh release list
```

**Exit gate:** the agent and human agree which changes must land before `R` and
which local evidence files are authorized for publication.

## Phase 1 — Close pre-R code and tooling gaps

**Owner:** coding agent. **Human step:** real default-configuration smoke.

### 1A. Implement release binding before the freeze

Add the smallest deterministic release tooling needed by SPEC-37:

1. `release/release-inputs.json` containing fields that can be reviewed before
   tagging:
   - schema version, release version, and lifecycle status (`draft` or
     `active`);
   - runtime source commit `R` (filled after Phase 2);
   - base/reviewer image names and digests (filled after Phase 3);
   - hashes of dependency locks/constraints;
   - deterministic aggregate hashes for configuration, schemas, canonical
     templates, and supported documentation entry points.
2. A checker, preferably `scripts/check_release_inputs.py`, that:
   - validates shape and lowercase full SHA/digest formats;
   - recomputes every file/set hash deterministically;
   - verifies all GitHub and GitLab template pins equal the recorded image
     names, digests, and `R`;
   - verifies the two GitHub workflow copies remain identical where required;
   - rejects placeholders and mutable/bare image tags;
   - is invoked by `make quality` and CI once release inputs are marked active.
   In `draft` status, `R`, image digests, and evidence identifiers may be null,
   but placeholder strings such as `TODO`, `TBD`, and `sha256:replace-me` remain
   invalid. In `active` status, every required value must be present and fully
   validated. Run draft-mode validation as soon as the file exists and require
   active-mode validation before tagging.
3. A generator and validator, preferably
   `scripts/build_release_manifest.py` and
   `scripts/check_release_manifest.py`, which run after `P` exists and emit and
   validate a release asset containing:
   - version/tag;
   - runtime source commit `R`;
   - release commit `P`;
   - both image subjects/digests;
   - hash of `release-inputs.json`;
   - the validated `R..P` changed-path list;
   - relevant CI, publication, and evidence record identifiers.
4. Tests for deterministic ordering, changed-path allowlisting, mismatched pins,
   malformed SHAs/digests, changed schema/config/lock hashes, and a clean happy
   path.

Do not put `P` inside a checked file committed at `P`; that creates a commit
self-reference. `P` belongs in the generated release asset produced from the
already-created tag/commit.

This work has no credentialed-live-test dependency. Review and merge it as soon
as it is green so the later `R` freeze does not wait on release-tooling review.

### 1B. Correct and validate the shipped reviewer model routes

This workstream may proceed in parallel with 1A, but both workstreams and the
1D live acceptance gate must finish before choosing `R`.

1. Select and commit the intended Claude OpenRouter model identifier. The
   current candidate is `anthropic/claude-haiku-4.5`; update
   `ai-review/config/review.yaml` and the exact expected value in its regression
   test together.
2. Add a keyless every-push drift guard that loads the shipped configuration
   with model override variables absent and asserts:
   - each enabled reviewer routed through OpenRouter by the canonical templates
     has the expected provider-qualified default;
   - blank workflow variables do not erase YAML defaults;
   - each adapter forwards or translates that configured value into its
     adapter-specific CLI argument/config representation exactly as intended;
   - the default panel/quorum remains internally valid.
3. The checked exact value is the repository's selected static contract; the
   1D credentialed smoke is the operational acceptance gate. If the smoke
   disproves the selected value, change both configuration and regression test
   before `R`. Do not make a mutable provider response dynamically define the
   unit-test expectation.
4. Update any image-build probe that intentionally uses a native Anthropic
   alias only if it is also exercising the OpenRouter route. Do not conflate
   native-Anthropic CLI argument acceptance with an OpenRouter model smoke.
5. Keep the documented override mechanism; do not hard-code the live operator's
   stronger or more expensive model choices as product defaults.

Before `R`, also update the stable configuration reference to label
`panel.grouping.semantic.*` experimental, default-off, and outside the 1.0
compatibility guarantee. The later stabilize-or-remove decision is not a
release blocker; honest classification of the shipped surface is.

### 1C. Automated validation

Run at minimum:

```bash
make quality
```

Also run the image build/preflight workflow on the PR. A green mock preflight is
not the real default-model test. Confirm the keyless shipped-config and
adapter-forwarding guards run in ordinary CI, not only in a release workflow.

### 1D. Human default-model smoke

In an operator-controlled same-repository PR or GitLab MR:

1. Remove/unset all `AI_REVIEW_<REVIEWER>_MODEL` overrides.
2. Enable the three shipped default OpenRouter reviewers and keep Cursor off.
3. Run prepare, review, critique, consensus, post, and gate.
4. Confirm each reviewer artifact records the exact expected YAML default and
   has an operational success status.
5. Record the accepted model strings with sanitized run/job identifiers and
   outcomes.

**Restart condition:** any default reviewer requiring code/config/dependency
changes returns to Phase 1B, then repeats 1C and 1D.

**Exit gate:** reviewed changes are green, the real no-overrides smoke passes,
and there are no other approved runtime changes waiting to merge.

## Phase 2 — Freeze runtime source commit R

**Owner:** human operator with agent verification.

1. Merge every approved pre-R code, config, schema, dependency, Dockerfile, and
   release-tooling change.
2. Require CI success on the resulting `main` head.
3. Record the full head SHA as `R` and announce a runtime change freeze.
4. The agent verifies that the worktree is clean and that `R` contains the
   default-model fix and release tooling.

```bash
git fetch origin main
git rev-parse origin/main
gh run list --branch main --limit 10
```

**Exit gate:** `R` is immutable by policy. Any later runtime change creates a
new `R` and invalidates Phases 3–5.

## Phase 3 — Build, publish, and verify images from R

**Owner:** human operator. **Agent:** inspect metadata and prepare pin changes.

1. Use the successful main publication run whose `headSha` is exactly `R`.
2. Record the base and reviewer image names, immutable tags, and digests from
   the workflow summary.
3. Pull both images anonymously by digest from a clean environment.
4. Inspect `org.opencontainers.image.revision`; it must equal `R`.
5. Verify both GitHub artifact attestations against this repository.
6. Do not rebuild or overwrite the same immutable SHA tag.

Representative commands:

```bash
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:<digest>
docker pull ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:<digest>
docker image inspect ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:<digest>
gh attestation verify \
  oci://ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:<digest> \
  --repo seanleecoder/code-tribunal
gh attestation verify \
  oci://ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:<digest> \
  --repo seanleecoder/code-tribunal
```

**Exit gate:** two pullable, attested digests both identify `R`.

## Phase 4 — Create release commit P and align the evidence plan

**Owner:** coding agent. **Human:** review exact pins and approve sanitized
evidence.

1. Start from `R`; update every supported template pin together:
   - four base and two reviewer containers in both GitHub workflow copies;
   - GitLab base image, reviewer image, and trusted source SHA.
2. Fill `release/release-inputs.json` with version `1.0.0`, `R`, and both image
   subjects/digests; regenerate deterministic hashes.
3. Replace stale candidate identities in the evidence runbook and pending
   records. Every required run must name `R`, the same two digests, and the
   template commit that will become `P`.
4. Integrate useful partial evidence only as partial evidence. Do not promote a
   record that did not exercise the final RC, stable bot identity, required
   check/pipeline enforcement, or hostile boundary.
5. Sanitize private consumer metadata unless its publication is explicitly
   approved. Prefer aliases such as `gitlab-consumer-A`, `MR-A`, and `pipeline-A`
   while retaining public Code Tribunal SHAs/digests and scoped outcomes.
6. Update the changelog draft and release notes, but do not mark evidence as
   passed or publish the release.
7. Run `make quality`, image-pin/release-input drift tests, and PR image
   preflight.
8. Merge the reviewed pin/evidence-preparation PR. Record its full SHA as `P0`.
   Later evidence-only commits may advance the final `P`; they must continue to
   satisfy the `R..P` allowlist.

**Exit gate:** repository templates and release inputs consistently point to
the images from `R`; no runtime path changed in `R..P0`.

## Phase 5 — Execute the live evidence matrix

**Owner:** human operator. **Agent:** walk the operator through each checklist,
inspect sanitized artifacts, and update records after the operator supplies
results.

Use the procedures and record templates under `docs/history/evidence/`. Run all
four suites against templates from `P0` (or a later evidence-only `P`) and images
from `R`.

### 5A. GitLab hostile-MR boundary

- Verify protected/masked provider and GitLab credentials in project settings.
- Exercise the selected production topology and the trust auditor.
- Attempt template/job/image/config override, variable forwarding, forged gate
  artifact, and the SPEC-31 hostile snapshot cases: relative, absolute,
  parent-escaping, dangling, file, and directory symlinks, including Linux
  `/proc/self/environ` where available.
- Treat the symlink test as positive containment evidence: prepare must fail
  before producing a usable snapshot, and the sentinel value must be absent
  from every trace and downloadable artifact.
- Inspect all traces and downloadable artifacts for the actual secret values
  using a non-disclosing comparison; pattern scans alone are insufficient.
- Record no secret or proprietary content.

### 5B. GitLab lifecycle and blocking

- Use one stable bot identity for prepare and post across every step.
- Enable **Pipelines must succeed**.
- Prove create, unchanged rerun, body update, resolve, reopen, unrelated line
  movement, state persistence, unmappable summary fallback, and an actually
  blocked merge.
- Record discussion/note identities only if approved for publication; otherwise
  use stable aliases.

### 5C. GitHub lifecycle and blocking

- Use a same-repository PR and configure `gate` as a required check.
- Prove inline create/update, summary fallback, commands, state persistence,
  stale-head no-op, and an actually blocked merge.
- Confirm reruns update rather than duplicate machine-owned objects.

### 5D. GitHub revision failure behavior

- Force head movement at every prepared boundary and confirm no mixed bundle.
- Exercise GitHub's oversized raw-diff HTTP 406 behavior and confirm prepare
  emits no reviewable artifact and a useful error.

### Failure classification

| Failure | Action |
|---|---|
| Runtime/config/schema/dependency defect | Stop; fix; return to Phase 1; choose new `R`; rebuild and repeat all affected evidence |
| Template pin or release-input defect only | Fix from `R`; recreate `P0`; repeat affected evidence |
| Consumer/platform setting wrong | Correct setting; rerun affected step; record failed attempt and correction |
| Evidence text or sanitization defect only | Correct record; retain valid run if source/images/settings were unchanged |
| Provider/platform transient failure | Rerun and record both attempts; do not relabel a repeatable product failure as transient |

**Exit gate:** all four matrix rows are scoped passes against `R`, its two
digests, and a release-allowlisted `P`; known unexercised paths are explicit.

## Phase 6 — Finalize, tag, and publish 1.0.0

**Owner:** coding agent prepares; human operator approves and publishes.

1. Merge sanitized final evidence and update the matrix to scoped pass.
2. Convert `CHANGELOG.md` from `Unreleased` to `[1.0.0] - YYYY-MM-DD`; include
   migration, rollback, known limitations, and the container/template-only
   distribution decision.
3. Record the exact validated `v0.4.0` rollback baseline: template source commit,
   base image subject/digest, and reviewer image subject/digest. Release notes
   must explain that 1.0 rollback means re-pinning to this immutable set, not a
   nonexistent earlier 1.0 image.
4. Ensure final release notes link installation, upgrade, security boundaries,
   evidence, the concrete `v0.4.0` rollback procedure, and the manifest
   verification command.
5. Run on final `main`:
   - `make quality`;
   - release-input and changed-path allowlist checks;
   - canonical template parsing/drift checks;
   - anonymous pulls and both attestation verifications.
6. Record final `main` as `P`. Confirm `R..P` contains no runtime-affecting path.
7. Create an annotated or signed `v1.0.0` tag at exactly `P`.
8. Generate `release-manifest.json` from tag `v1.0.0`, `R`, `P`, checked release
   inputs, run IDs, and evidence records. Validate it, compute its SHA-256, and
   attach both files to the GitHub release.
9. Publish release notes and mark 0.x prereleases appropriately; do not mutate
   prior image tags.
10. From a clean third-party context, repeat the documented installation,
    manifest verification, and re-pin from 1.0 to the recorded `v0.4.0` rollback
    set.

Representative publication sequence after approval:

```bash
git tag -s v1.0.0 <P> -m "Code Tribunal 1.0.0"
python scripts/build_release_manifest.py \
  --tag v1.0.0 --runtime-source <R> --release-commit <P> \
  --out /tmp/release-manifest.json
python scripts/check_release_manifest.py /tmp/release-manifest.json
sha256sum /tmp/release-manifest.json > /tmp/release-manifest.json.sha256
git push origin v1.0.0
gh release create v1.0.0 \
  /tmp/release-manifest.json \
  /tmp/release-manifest.json.sha256 \
  --verify-tag --title "Code Tribunal 1.0.0" --notes-file release/1.0.0.md
```

Adapt signing commands to the maintainer's configured signing mechanism. If the
tag or release command fails, stop and inspect remote state before retrying; do
not create a second tag at another commit under the same version.

## Required handoff artifacts

The coding agent hands the human operator:

- PR links and final pre-R test results;
- exact `R`, publication run URL/ID, image subjects, and digests;
- pin/evidence PR and exact `P0`;
- release-input validation output;
- four ready-to-fill evidence records with no stale identifiers;
- explicit commands for the operator's platform topology;
- a list of actions that require credentials or settings and therefore were not
  performed by the agent.

The human operator hands the coding agent:

- sanitized run/job/change-request identifiers;
- confirmation of protected/required settings without secret values;
- expected versus actual outcome for every matrix step;
- approved public aliases for platform objects;
- registry pull, image-label, and attestation results;
- explicit approval or rejection of the final tag/release.

## Final acceptance checklist

- [ ] Shipped default reviewers succeed without model overrides.
- [ ] `make quality` passes on `R` and final `P`.
- [ ] Both image digests are pullable, attested, and labeled with `R`.
- [ ] All GitHub/GitLab template pins and release inputs agree with those images.
- [ ] `R..P` contains only approved release paths.
- [ ] GitLab hostile-MR evidence is a scoped pass with actual secret-value audit.
- [ ] GitLab lifecycle/state/blocking evidence is a scoped pass.
- [ ] GitHub lifecycle/state/required-check evidence is a scoped pass.
- [ ] GitHub revision-race/406 evidence is a scoped pass.
- [ ] Evidence is sanitized and names all unexercised paths.
- [ ] The configuration reference marks semantic grouping experimental,
  default-off, and outside the 1.0 compatibility guarantee.
- [ ] Changelog, release notes, tag, release inputs, and generated manifest agree
  on `1.0.0`, `R`, `P`, and both digests.
- [ ] Release notes name the exact validated `v0.4.0` rollback template commit and
  both immutable image digests.
- [ ] A clean third-party verification of installation and re-pinning from 1.0
  to that `v0.4.0` rollback set succeeds.

Only when every item is checked is the verdict **ready for 1.0.0**.

## Post-1.0 queue (not release blocking)

Keep these out of the finalization critical path unless live evidence proves
they are required for correctness:

- honor bounded `Retry-After` on HTTP 429;
- decide whether to stabilize semantic grouping or remove it from the stable
  configuration surface after shipping it with the required experimental label;
- execute SPEC-39 milestone B posting decomposition;
- improve bot-identity migration only through a separately authenticated design.
