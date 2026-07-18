# Live evidence index and runbook

Live evidence complements executable tests; it does not replace them. Record
only sanitized identifiers, digests, expected/actual outcomes, and audit results.
Never store credentials, CLI session material, proprietary source, or sensitive
model content.

## 1.0 evidence matrix

| Suite | Required coverage | Status |
|---|---|---|
| GitLab hostile MR | Protected variables, direct/child trust audit, symlink attack, artifact/log inspection, no token exposure | **Outstanding** |
| GitLab current image | Create, update, resolve, reopen, state persistence, blocking gate | **Outstanding** |
| GitHub current image | Inline create/update, summary fallback, commands, state persistence, stale head, required blocking check | **Outstanding** |
| GitHub revision failures | Revision race at prepare boundaries and oversized raw-diff failure | **Outstanding** |

Previous GitHub dogfood runs proved workflow execution, authenticated state, and
some inline posting, but explicitly did not prove a genuinely blocking required
check or all current-image lifecycle paths. Previous GitLab runs proved a real
consumer flow but not the hostile-MR deployment boundary. See
[legacy acceptance](../acceptance/README.md).

Until every row is complete against the intended release-candidate source and
images, current docs must qualify rather than assert product-wide “stable,”
“credential isolated,” or equivalent deployment claims.

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
