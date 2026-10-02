# Evidence record: PLATFORM / SCENARIO / DATE

Status: pending

<!-- Passing records selected by active release inputs must have Status exactly
     "passed" and matching source/digest bindings below. To waive a record, remove
     its ID from verification.evidence_record_ids and register its nonempty reason
     only under verification.evidence_waivers in release/release-inputs.json.
     Preserve the record's historical status and bindings; no record-side waiver
     marker is required, and a waiver does not certify the current candidate. -->

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
- Pipeline trigger and branch-protection configuration verified:
- Expected behavior:

## Actual result

- Stage outcomes:
- Platform objects created/updated/resolved:
- Consensus/post summary:
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
