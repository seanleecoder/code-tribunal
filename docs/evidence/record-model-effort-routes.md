# Evidence record: cross-platform model-effort routes / pending

Status: pending

<!-- This is a separate record for effort-route validation. Do not append these
     checks to the historical default-model smoke record. When the checks run,
     replace each pending value with the same frozen source/image pair and
     complete every platform row below. This record is not a release binding
     until live validation is complete. -->

Release-runtime-source: `<40-character-runtime-source-sha>`
Release-base-digest: `sha256:<64-character-base-image-digest>`
Release-reviewer-digest: `sha256:<64-character-reviewer-image-digest>`

This record covers the first real-provider route checks for the non-default
effort settings: Codex with requested effort `max` and OpenCode with requested
effort `xhigh`. Run the checks independently on GitHub Actions and GitLab CI
against the same rebuilt base/reviewer image pair. The historical
[default-model smoke record](record-github-default-model-smoke.md) is not
evidence for these routes and must remain unchanged.

## Required capture

For every row, record the platform workflow/pipeline ID, relevant job IDs or
run identifier, the frozen runtime source, both image digests, the actual model
identifier, the requested and observed effort values, and the outcome. Capture
provider acceptance or rejection explicitly; a rejected requested effort is a
failed validation, not a successful default-effort substitution.

| Platform | Workflow / pipeline / run ID | Reviewer | Actual model | Requested effort | Observed effort | Outcome |
|---|---|---|---|---|---|---|
| GitHub Actions | pending | Codex | pending | `max` | pending | pending |
| GitHub Actions | pending | OpenCode | pending | `xhigh` | pending | pending |
| GitLab CI | pending | Codex | pending | `max` | pending | pending |
| GitLab CI | pending | OpenCode | pending | `xhigh` | pending | pending |

## Preconditions

- `AI_REVIEW_LOCAL_MOCK=0` and the platform's real-provider requirements were
  enabled for every check.
- The runtime source and both image digests above were verified before running;
  no historical default-model run was reused.
- Model/effort overrides and the effective adapter configuration were captured
  from the run, without recording credentials or sensitive model content.

## Actual result

Pending. For each platform and reviewer, record the exact model and effective
effort reported by the adapter, the provider response, and whether the route
completed with the requested effort.

## Audit

Pending. Record the inspected job logs and artifacts, the source/image binding,
and the non-disclosing credential audit result for all four checks.

## Verdict

Pending. Replace with a scoped cross-platform pass/fail statement covering only
the recorded runtime source, image pair, model identifiers, effort values, and
platform topologies.
