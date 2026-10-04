# Evidence record: GitHub / MiMo Pro and Cursor Grok Medium / 2026-10-04

Status: passed

Supplemental route evidence, unselected in release inputs. This record covers the
older runtime below and does not certify final-image release validation.

## Identity

- Platform and version: GitHub.com / GitHub Actions, hosted Linux container jobs.
- Date/time and timezone: 2026-10-04, 14:46:01–15:03:51 UTC.
- Deployment topology: canonical prepare → four-seat review → critique → consensus
  → post, using candidate YAML and hidden demo credentials inside Actions.
- Consumer/template project: `seanleecoder/code-tribunal-demo`; canonical product
  workflow with smoke-only config/rule/trace steps.
- Change request: [PR #36](https://github.com/seanleecoder/code-tribunal-demo/pull/36),
  head `c71fca8d832aac446be23839b05e0edae20e5196`.
- Pipeline/workflow run: [37210473861](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/37210473861),
  attempt 1, conclusion `success`; artifact run ID `gh-37210473861-1`.
- Relevant job IDs: prepare `111460449896`; review Claude `111460513409`, Codex
  `111460513426`, OpenCode `111460513429`, Cursor `111460513468`; critique Claude
  `111462854690`, Codex `111462854762`, OpenCode `111462854732`, Cursor
  `111462854706`; consensus `111463593775`; post `111463647553`.
- Source commit (image runtime): `e1313b55e566e384d916da0cdc294969dc700fdf`;
  publication run `37202713222`.
- Template/workflow commit: demo `c71fca8d832aac446be23839b05e0edae20e5196`;
  canonical workflow copied from runtime source above, then scoped for this canary.
- Base image tag and digest: tag `2.0-e1313b55e566e384d916da0cdc294969dc700fdf`,
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:e92ea908a185f56ab03138b610153a195c81ca1d88dc93c2a089ef37ba79c8c4`.
- Reviewer image tag and digest: same source tag,
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:ff91e876dd6d411060c5f9109b02ca7cb0f6decbeb8cd2603165e29de2f21cbf`.
- CLI pins: Claude `2.1.283`, Codex `0.157.1`, OpenCode `1.18.32`, Cursor
  `2026.09.26-dd393fe`.

## Preconditions

- Both image attestations verified against the runtime source, protected `main`,
  canonical publisher identity, and SLSA provenance predicate.
- Demo secret configuration: provider and resolve credentials remained hidden in
  Actions; no credential values were read locally.
- Pipeline trigger: automatic `pull_request` on a temporary fixture branch;
  smoke workflow bypassed manual mode. No Code Tribunal merge gate was used.
- Candidate YAML selected MiMo Pro with absent effort and enabled Cursor with
  `grok-4.7-medium`; control seats retained Haiku / medium and GPT-6 Luna / low.
  `AI_REVIEW_OPENCODE_EFFORT` was omitted from this custom workflow. Mock mode
  was off and real-provider guards were on.
- Authenticated selector inspection in the preceding
  [Flash run](record-model-refresh-2026-10-04.md) used Cursor
  `2026.09.26-dd393fe`. It listed `composer-2.5`, `composer-2.5-fast`, and
  `grok-4.7-{low,medium,high,xhigh}` with each corresponding `-fast` variant;
  it did not list bare `grok-4.7`.
- Expected behavior: real review and critique on those exact routes, sequential
  read/glob/grep and structured output for Pro, schema-valid artifacts usable by
  consensus, successful posting, and no observed model/mock fallback.

## Actual result

Effective config digest: `8654fba9754ade76b955b80b7161385be30c58aa17af6d6a2323b8b29fe0daa7`.

| Seat | Review raw / accepted / dropped | Critique |
|---|---|---|
| Claude Haiku / medium | 3 / 3 / 0 | success, 8 assessments |
| Codex GPT-6 Luna / low | 1 / 1 / 0 | success, 8 assessments |
| OpenCode MiMo Pro / absent | 3 / 3 / 0 | success, 8 assessments |
| Cursor Grok Medium | 1 / 1 / 0 | success, 8 assessments |

Pro preserved `providerID: openrouter` and `modelID: xiaomi/mimo-v2.6-pro`
across twelve review assistant turns. It exercised read, glob, grep, sequential
calls, recovery from three tool errors, and successful `StructuredOutput`.
Critique completed `StructuredOutput` on the same route. Review took 12m47s and
critique 3m49s; these observations are not a latency benchmark.

Cursor completed real review and critique with `grok-4.7-medium`; result-text JSON
passed shared schema and consensus-integrity validation. No model substitution
or mock fallback was observed. Independent consensus-input validation passed;
the full four-seat panel had no failed reviewers and all seats were eligible for
resolution. Posting created three discussions without warnings. The temporary
PR was closed; its branch and findings were preserved.

## Audit

- Artifacts inspected: manifest/config/diff/snapshot/rules; all finding, critique,
  pooled-finding, and status JSON; sanitized native OpenCode model/tool metadata;
  consensus and post-result JSON.
- Logs inspected: stage outcomes, OpenCode review/critique transport logs, Cursor
  critique output, and authenticated selector output from the preceding smoke.
- Credential values absent: pattern/entropy scan passed for 38 Pro/Grok files;
  the original combined scan passed for 79 retained files, including Flash and
  four job/workflow logs. No exact-value scan: demo secrets are hidden.
- Sensitive model content omitted: no prompts, model prose, or CLI session material
  are reproduced in this record.
- Known unexercised paths: GitLab, final-image release validation, stronger
  Claude/Codex recommendations, billing/token counts, Grok xhigh/fast variants,
  and real Composer review/critique. Selector listing alone proves no variant's
  review/critique compatibility or availability across every customer plan.

## Verdict

Passed for MiMo Pro / absent effort and Cursor Grok Medium on this recorded
GitHub runtime and image pair, with full consensus and successful posting. These
results predate `46c8c46fd0df7a0174906bba960a49f7fbb8722c` and do not validate its
newer CLI pins. They certify no final release image pair.
