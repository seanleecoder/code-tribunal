# Evidence record: GitHub / shipped default-model panel / 2026-10-04

Status: passed

Release-runtime-source: 3db908f06d141d23de99e7578ae5b5482ebaa498
Release-base-digest: sha256:2f3ffaba159e067825d72e889872ac6479bb31461943b6d86516a266e1faf3e9
Release-reviewer-digest: sha256:7c59447733e5b6640f7ff759ab879c4682310054de130515841e91389a9b2447

> Sanitized final-pair evidence. No credentials, provider sessions, source bodies,
> or sensitive model content are reproduced here.

## Identity

- Platform: GitHub.com / GitHub Actions hosted Linux container jobs.
- Date/time: 2026-10-04T20:59:10Z–2026-10-04T21:03:07Z (UTC).
- Consumer: `seanleecoder/code-tribunal-demo`, temporary
  [PR #39](https://github.com/seanleecoder/code-tribunal-demo/pull/39).
- Workflow run: [37234212093](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/37234212093),
  attempt 1, conclusion `success`; artifact run `gh-37234212093-1`.
- Consumer head and temporary workflow commit: `17d8ad8ec5ae60d54846d5839b90376e9224653f`.
- Runtime source `R`: `3db908f06d141d23de99e7578ae5b5482ebaa498`.
- Protected-main image publication: [37233827865](https://github.com/seanleecoder/code-tribunal/actions/runs/37233827865).
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:2.0-3db908f06d141d23de99e7578ae5b5482ebaa498@sha256:2f3ffaba159e067825d72e889872ac6479bb31461943b6d86516a266e1faf3e9`.
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:2.0-3db908f06d141d23de99e7578ae5b5482ebaa498@sha256:7c59447733e5b6640f7ff759ab879c4682310054de130515841e91389a9b2447`.
- Resolved configuration digest: `854ecc519b613836f65a59caa4136d99c02e4b94644f9e9458c719321a193955`.
- Canonical workflow copied from `R`, with the final pair, blank model/effort and
  roster overrides, explicit real-provider guards, a trusted smoke-only tool rule,
  and sanitized native OpenCode metadata capture. Packaged YAML was not replaced.

| Job | ID | Result |
|---|---|---|
| prepare | `111530000494` | success |
| review (codex) | `111530055776` | success |
| review (cursor) | `111530055805` | success |
| review (claude) | `111530055821` | success |
| review (opencode) | `111530055841` | success |
| critique (cursor) | `111530341241` | success |
| critique (codex) | `111530341296` | success |
| critique (opencode) | `111530341316` | success |
| critique (claude) | `111530341350` | success |
| consensus | `111530645386` | success |
| post | `111530713117` | success |

## Preconditions

Exact-pair identity and source-/signer-constrained provenance passed in
`verify-candidate` of [Candidate Canary 37234182540](https://github.com/seanleecoder/code-tribunal/actions/runs/37234182540).
Demo preflight passed before the campaign and after cleanup. The public prefix
membership fixture supplies a real defect. Claude, Codex, and OpenCode are enabled;
Cursor is disabled by the shipped YAML. Model/effort/roster overrides are blank,
MiMo's effort override is absent, mock mode is off, and real-provider guards are on.
The persisted demo roster remains four seats; only this temporary workflow selects
the packaged default roster.

## Actual result

| Seat | Model / effort | Review raw / accepted / dropped | Critique |
|---|---|---|---|
| claude | `anthropic/claude-haiku-4.5` / medium | 3 / 3 / 0 | success, 5 assessments |
| codex | `openai/gpt-6-luna` / low | 1 / 1 / 0 | success, 5 assessments |
| opencode | `xiaomi/mimo-v2.6-flash` / absent | 1 / 1 / 0 | success, 5 assessments |
| cursor | `composer-2.5` / unsupported | 0 / 0 / 0 | skipped, 0 assessments |

All three enabled reviews and critiques succeeded and were resolution-eligible.
Restored `finding_batch.v1` and `critique_batch.v1` artifacts passed schema and
`validate_consensus_inputs` checks against the preparation run and configuration.
`consensus.v2` reported `panel_status: full`, exactly Claude/Codex/OpenCode
successful and eligible, and no failed reviewers. Post succeeded, created three
threads (`4179308663`, `4179308701`, `4179308742`), had no warnings,
and bound exactly to the prepared consumer head.

Native OpenCode metadata recorded only `openrouter` / `xiaomi/mimo-v2.6-flash`
in review and critique. Review completed `glob`, `grep`, `read`, `grep`, `read`,
then `StructuredOutput`; critique completed `StructuredOutput`. The first
`glob` → `grep` → `read` calls were sequential. The fourth call overlapped the
third read, and the fifth read started after both completed. This establishes
multiple sequential tool calls and successful structured output, without claiming
that every call was serialized.

The temporary PR was closed and its branch deleted. The fully successful canary
also proved the real four-seat panel and critique on both platforms, both
lifecycles, hostile containment, and teardown on this same pair.

## Audit

Downloaded and inspected preparation, all four review/critique batch and status
artifacts (including skipped Cursor), consensus, post, and sanitized OpenCode
model/tool metadata: 35 files, approximately 0.1 MB. The existing evidence scanner
reported no credential material with ten pattern/entropy detectors. No exact-value
scan was performed; no credential values were retrieved. Model/source bodies and
provider session identifiers are omitted from this record.

OpenRouter billed token/cost and provider-internal reasoning behavior were not
observed. This smoke does not certify MiMo Pro, Grok, non-default efforts, Cursor
deny-policy enforcement, GitLab forks, or network egress enforcement. Earlier
newer-contract model smokes are supplemental, not evidence for the restored pair.

## Verdict

Scoped final-pair pass for the shipped three-seat roster, restored 2.0 artifact
contract, MiMo sequential tools and structured review/critique, full consensus,
posting, and cleanup. SPEC-41 remains open; this pass does not remove the required
confidence field or its finding-loss limitation.
