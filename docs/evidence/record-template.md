# Evidence record: PLATFORM / SCENARIO / DATE

Status: pending

<!-- For release-inputs status=active, Status must be exactly "passed" and the
     three Release-* fields below must match release/release-inputs.json, OR
     set Release-evidence-waived: <reason> and register the same reason under
     verification.evidence_waivers in release/release-inputs.json. -->

Release-runtime-source: `<40-character-runtime-source-sha>`
Release-base-digest: `sha256:<64-character-base-image-digest>`
Release-reviewer-digest: `sha256:<64-character-reviewer-image-digest>`

## Identity

- Platform and version:
- Date/time and timezone:
- Deployment topology:
- Consumer/template project:
- Change request:
- Pipeline/workflow run:
- Relevant job IDs:
- Source commit:
- Template/workflow commit:
- Base image tag and digest:
- Reviewer image tag and digest:

## Preconditions

- Protected/masked variables or GitHub secret configuration verified:
- Required pipeline/check configuration verified:
- Expected behavior:

## Actual result

- Stage outcomes:
- Platform objects created/updated/resolved:
- Consensus/post/gate summary:
- Attack or failure result:

## Audit

- Artifacts inspected:
- Logs inspected:
- Credential values absent:
- Sensitive model content omitted from this record:
- Known unexercised paths:

## Verdict

Pending. Replace with a scoped pass/fail statement that names exactly what this
run proves; do not generalize beyond the recorded topology, source, and images.
