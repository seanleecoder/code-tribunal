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
> every release-required row below except image publication must be repeated
> against its two published digests.

| Suite | Required coverage | Status |
|---|---|---|
| Image publication verification | Anonymous digest pulls, OCI revision labels, and GitHub provenance attestations for both images | **Scoped pass** — [15d424f images](record-image-publication-verification.md) |
| GitHub default-model smoke | No model overrides, three shipped OpenRouter defaults operational, Cursor disabled, full panel and gate | **Replacement technical pass** — P0 run `29848500791` reached the expected full panel; actual-secret-value audit pending; [record](record-github-default-model-smoke.md) |
| GitLab hostile MR | Protected variables, direct/child trust audit, symlink attack, artifact/log inspection, no token exposure | **Replacement partial** — credential boundary, forwarding attempt, and five isolated symlink classes passed structurally; remaining overrides/forgery and actual-value audit are listed in the [record](record-gitlab-hostile-mr.md) |
| GitLab current image | Create, update, resolve, reopen, state persistence, blocking gate | **Replacement partial** — full-panel blocking run and direct resolve/reopen passed; remaining lifecycle steps are listed in the [record](record-gitlab-current-image.md) |
| GitHub current image | Inline create/update, summary fallback, commands, state persistence, stale head, required blocking check | **Replacement partial** — classic-token resolve/reopen and owner-command persistence passed; remaining lifecycle/enforcement paths are listed in the [record](record-github-current-image.md) |
| GitHub revision failures | Revision race at prepare boundaries and oversized raw-diff failure | **Replacement partial** — P0 before-diff race passed; remaining boundaries and HTTP 406 are listed in the [record](record-github-revision-failures.md) |

Previous GitHub dogfood runs proved workflow execution, authenticated state, and
some inline posting, but explicitly did not prove a genuinely blocking required
check or all current-image lifecycle paths. Previous GitLab runs proved a real
consumer flow but not the hostile-MR deployment boundary. See
[legacy acceptance](../acceptance/README.md).

Until every partial or outstanding row is complete against the intended release-candidate
source and images, current docs must qualify rather than assert product-wide
“stable,” “credential isolated,” or equivalent deployment claims.

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

## Current-image lifecycle procedure

Publish both images from one reviewed release-candidate commit and verify their
digests. On each platform, create an inline finding, rerun unchanged, change the
body, resolve, reopen, push an unrelated line movement, exercise summary
fallback, and force a blocking finding while platform enforcement is enabled.
Record post/state/gate artifacts and platform object IDs at every step.

## GitHub failure procedure

Force PR head movement at each prepare boundary and verify that no mixed-revision
bundle is produced. Exercise an HTTP 406/too-large raw comparison response and
verify prepare emits no reviewable bundle. These live smokes complement the
SPEC-34 regression tests.
