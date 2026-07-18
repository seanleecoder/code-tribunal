# SPEC-38 — Replace milestone narration with task-oriented 1.0 documentation and evidence

- **Severity:** Medium (adoption risk / unsupported security claims) · **Effort:** L · **ROI rank:** 8 (pre-1.0 content gate)
- **Depends on:** SPEC-31 through SPEC-36 for final behavior; evidence collection can begin earlier.

## Why

The top-level README is nearly 600 lines and the subsystem README repeats concepts,
configuration, image publication, phase history, and workflow guidance. Improvement
status says SPEC-23–30 are not started although those changes are on `main`.
`critique.max_rounds` contradicts the claim that every active config key is
consumed. Security identifiers C1/H1 are not actionable, and required hostile-MR
GitLab deployment evidence remains outstanding.

First-time adopters need a minimal working installation and operational failure
guidance; maintainers need history without presenting it as current product docs.

## Target information architecture

1. `README.md` — concise value proposition, limitations, supported platforms,
   five-minute GitHub/GitLab pointers, minimal local demo, documentation map.
2. `docs/getting-started/github.md` — prerequisites, install workflow, secrets,
   branch protection, first run, verification, uninstall.
3. `docs/getting-started/gitlab.md` — direct vs hardened child setup, protected
   template/pins, variables/settings, first run, verification, uninstall.
4. `docs/configuration.md` — exhaustive active YAML and environment reference,
   defaults, stage visibility, validation, examples; mechanically checked/generated.
5. `docs/operations.md` — upgrades, state migrations, concurrency, observability,
   cost controls, artifact retention, rollback, incident response.
6. `docs/TROUBLESHOOTING.md` — symptom → likely cause → evidence → action.
7. `docs/SECURITY_MODEL.md` plus root `SECURITY.md` — trust boundaries, credential
   scopes, symlink policy, forks, egress limitation, threat assumptions, reporting.
8. `docs/reference/` — CLI/exit codes, schemas/artifacts, consensus, revision
   lifecycle, platform differences.
9. `docs/development/` — contributor setup, architecture, tests, release process.
10. `docs/history/` — acceptance evidence, completed specs, old design plans.

## Implementation

1. Inventory every current statement and assign one canonical destination. Delete
   duplicates rather than copying them into the new tree.
2. Rewrite README around successful first use; keep architecture theory and phase
   history out of the landing page.
3. Provide copy/paste-minimal examples using immutable placeholders or generated
   current pins. Each example must be exercised in CI or a fixture parser.
4. Document all supported CLI modules/entry points, artifacts, schemas, environment
   variables, exit codes, and GitHub/GitLab differences. Link to source schemas
   instead of reproducing large structures.
5. Add practical upgrade guidance from 0.4.x to 1.0, including breaking config,
   state/body-hash migration, image pin rotation, rollback, and cleanup.
6. Explain fail-open/fail-closed behavior by failure class, including advisory mode.
7. Replace vague C1/H1 labels with current mitigations, residual risk, and evidence.
   Keep H2 explicit until container/runner egress enforcement exists.
8. Update improvement-spec status from repository evidence; archive completed
   implementation plans. Do not advertise proposed SPEC-20/22 features as product.
9. Add link checking, heading/anchor checking, YAML/example parsing, environment-key
   inventory comparison, and image-pin drift checks to CI.
10. Remove stale private registry version examples (`1_1` vs `1_0`) or derive them
    from one release variable.

## Required live evidence before “stable” 1.0 claims

- GitLab hostile-MR scratch run covering protected variables, child/direct trust
  audit, symlink attack, artifact/log inspection, and no token exposure.
- GitLab current-image run covering create/update/resolve/reopen/state/gate.
- GitHub current-image run covering inline create/update, summary fallback, commands,
  state persistence, stale head, and a genuinely blocking required check.
- Large/multipage GitHub diff smoke from SPEC-34.
- Record pipeline/run IDs, source/image digests, expected/actual results, and known
  unexercised paths. Never store credentials or sensitive model content.

## Acceptance criteria

- A new adopter can reach a verified first review without reading architecture or
  historical specs.
- Every configuration/environment key in production has exactly one current
  reference entry; inert/proposed keys are absent.
- All local links/examples/pins pass automated checks.
- “Stable”, “fail closed”, “idempotent”, and “credential isolated” claims point to
  executable tests or current live evidence.
- Historical material is searchable but clearly non-normative.

## Risk / rollback

Large doc moves can break external links. Add redirect/index stubs for high-value
old paths for one release, and use link checks before deleting them. Do not retain
duplicated normative text merely to preserve headings.
