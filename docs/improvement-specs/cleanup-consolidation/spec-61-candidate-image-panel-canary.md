# SPEC-61 - Candidate-image four-seat panel canary

- **Status:** Ready as a release-verification follow-up
- **Severity:** High release confidence for reviewer-path changes
- **Effort:** M
- **Depends on:** SPEC-54 through SPEC-57; may adopt SPEC-60 reporting when available

## Objective

Verify the actual candidate reviewer image across all four first-party adapters and both
model stages before that image is promoted, published as a release input, or repinned into
consumer templates.

The canary validates Code Tribunal's integration. It is not a recurring conformance test of
third-party model or permission implementations.

## Why

A repository pull-request workflow that points at previously released image digests cannot
validate runtime changes made in the current checkout. Mock tests and image preflight prove
local contracts, but they do not prove that the candidate image can authenticate, invoke,
parse, and finalize output from every real provider route.

## Trigger policy

Run the canary from a trusted branch or protected `workflow_dispatch` after candidate images
have been built from the exact source commit.

It is required before release promotion when any of these change:

- `ai-review/adapters/**`;
- adapter runner, process, output, artifact, prompt-render, or registry code;
- review or critique prompts;
- finding, critique, or consensus schemas;
- default reviewer models or effort routing;
- reviewer CLI pins or reviewer image packaging;
- credential or endpoint routing;
- critique pooling or finalization.

It is not required for unrelated documentation, platform posting, or pure state changes.

## Trust and secret handling

- Run only from reviewed same-repository source.
- Use a protected environment with the four required provider credentials.
- Do not run on untrusted pull-request code.
- Use candidate images addressed by immutable digest.
- Record the source commit and both candidate image digests.
- Never upload raw prompts, proprietary source snapshots, credentials, provider session data,
  or unrestricted model streams.
- Apply existing redaction before saving diagnostics.

## Canary matrix

Run:

```text
4 reviewer seats x review
4 reviewer seats x critique
1 deterministic consensus
1 posting dry run
```

Seats:

- Claude;
- Codex;
- OpenCode;
- Cursor.

Stages:

- `review`;
- `critique`.

The critique stage uses the finalized review findings from the same candidate run.

All four seats are required. Note that the existing image-publish preflight smokes only
Claude, Codex, and OpenCode, even though the reviewer image build does probe `cursor-agent`.
Cursor is therefore the one seat with no pre-promotion functional check today, which is
precisely the gap this canary closes; do not model the canary matrix on that preflight.

## Input

Use a small, public, sanitized fixture repository and diff that:

- contains multiple files and a valid line anchor;
- contains no secrets or proprietary code;
- is stable across runs;
- is complex enough for a reviewer to produce either findings or a valid empty batch;
- can be posted in dry-run mode without platform mutation.

Do not assert that a nondeterministic model must find a particular bug. The canary validates
transport and contract health, not model quality scoring.

## Required checks per seat

For review:

- process exits successfully within the configured timeout;
- finalized finding batch validates;
- run ID and effective-config digest match the input manifest;
- reviewer identity and model are correct;
- quality counters are internally consistent;
- a valid empty batch is acceptable.

For critique:

- process exits successfully within the configured timeout;
- finalized critique batch validates;
- critic identity, run ID, and config digest match;
- every critique target belongs to the candidate finding pool;
- a valid empty batch is acceptable.

For the aggregate run:

- consensus validates under the current schema;
- support decisions are deterministic for the finalized artifacts;
- posting dry run validates the post result and performs no network mutation;
- no artifact contains a detected credential or known secret pattern.

## Cursor scope

The Cursor canary proves:

- the pinned CLI launches in the candidate image;
- authentication works;
- review and critique invocations complete;
- Code Tribunal parses and finalizes the result;
- the dedicated credential route is functional.

It does not attempt to prove that Cursor internally enforces every configured write or shell
permission. Cursor remains a trusted pinned dependency as defined by SPEC-56 and ADR-0003.

## Workflow design

Add a dedicated trusted workflow rather than expanding normal pull-request CI.

The workflow should:

1. accept candidate base and reviewer image digests, or consume artifacts from the protected
   build workflow;
2. verify OCI source labels match the requested commit;
3. prepare the public fixture once;
4. run the review matrix;
5. merge finalized findings;
6. run the critique matrix;
7. run consensus and posting dry run;
8. produce a sanitized machine-readable canary summary;
9. fail when a required seat or stage fails;
10. publish no image and mutate no pull request.

Do not silently skip a required seat because a secret is absent. A release canary with missing
credentials must fail with a clear prerequisite error.

## Evidence output

Produce a compact artifact containing:

- schema version;
- source commit;
- candidate image digests;
- workflow run ID;
- reviewer and critic status per seat;
- models used;
- duration and redacted error class;
- consensus schema/status summary;
- post dry-run status;
- overall pass/fail.

This artifact may be cited by a release evidence record. It must not contain raw model output
unless explicitly sanitized and approved for public evidence.

## Tests

Provider-free tests must cover:

- workflow matrix contains all four registry seats and both stages;
- candidate digest/source label mismatch fails;
- missing required secret fails rather than skips;
- one review failure prevents a passing canary;
- one critique failure prevents a passing canary;
- malformed artifacts fail before consensus;
- posting is dry-run only;
- output summary redacts secrets;
- Cursor has no special permission-conformance assertion.

## Acceptance criteria

- Candidate images, not previously released pins, are exercised.
- All four seats complete both stages against the same run.
- Consensus and posting dry run complete from real finalized artifacts.
- The workflow is protected from untrusted source.
- Missing secrets and failed seats are explicit failures.
- Evidence is source- and digest-bound and safe to retain.
- The canary is required by release process only when reviewer-path changes warrant it.
- Normal pull-request CI remains provider-free.

## Non-goals

- Do not benchmark model quality or compare providers.
- Do not require exact findings from nondeterministic models.
- Do not post to a real pull request.
- Do not test third-party permission enforcement.
- Do not make this a universal per-commit gate.
