# Evidence record: GitHub / MiMo Flash candidate-config smoke / 2026-10-04

Status: passed

Supplemental candidate-config evidence, unselected in release inputs. This record
covers the older runtime below; it does not certify final-image release validation.
The separate [Pro/Grok record](record-mimo-pro-grok-2026-10-04.md) retains that
canary's outcomes.

## Identity

- Platform and version: GitHub.com / GitHub Actions, hosted Linux container jobs.
- Date/time and timezone: 2026-10-04, 14:39:39–14:42:45 UTC.
- Deployment topology: canonical prepare → review → critique → consensus → post,
  using candidate YAML in every job and hidden demo credentials inside Actions.
- Consumer/template project: `seanleecoder/code-tribunal-demo`; canonical product
  workflow from `seanleecoder/code-tribunal` with smoke-only config/rule/trace steps.
- Change request: [PR #35](https://github.com/seanleecoder/code-tribunal-demo/pull/35),
  head `a640f8a65b91cbc59bf820c5332efbc416200c26`.
- Pipeline/workflow run: [37210076322](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/37210076322),
  attempt 1, conclusion `success`; artifact run ID `gh-37210076322-1`.
- Relevant job IDs: prepare `111459295010`; review Claude `111459355288`, Codex
  `111459355266`, OpenCode `111459355267`, Cursor `111459355257`; critique Claude
  `111459570404`, Codex `111459570482`, OpenCode `111459570435`, Cursor
  `111459570428`; consensus `111459722464`; post `111459794411`.
- Source commit (image runtime): `e1313b55e566e384d916da0cdc294969dc700fdf`;
  publication run `37202713222`.
- Template/workflow commit: demo `a640f8a65b91cbc59bf820c5332efbc416200c26`;
  canonical workflow copied from runtime source above, then scoped for this smoke.
- Base image tag and digest: tag `2.0-e1313b55e566e384d916da0cdc294969dc700fdf`,
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:e92ea908a185f56ab03138b610153a195c81ca1d88dc93c2a089ef37ba79c8c4`.
- Reviewer image tag and digest: same source tag,
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:ff91e876dd6d411060c5f9109b02ca7cb0f6decbeb8cd2603165e29de2f21cbf`.
- CLI pins: Claude `2.1.283`, Codex `0.157.1`, OpenCode `1.18.32`, Cursor
  `2026.09.26-dd393fe`.

## Preconditions

- Both image attestations verified against the runtime source, `refs/heads/main`,
  canonical publisher identity, and SLSA provenance predicate.
- Demo secret configuration: `OPENROUTER_API_KEY`, `CURSOR_API_KEY`, and the resolve
  token remained hidden inside Actions. No credential values were read locally.
- Pipeline trigger: automatic `pull_request` on the temporary fixture branch;
  smoke workflow bypassed demo manual mode. No Code Tribunal merge gate was used.
- Every stage applied the same candidate model defaults to published YAML.
  Model/effort overrides were blank; `AI_REVIEW_OPENCODE_EFFORT` was absent from
  this custom workflow. That recorded absence describes this run, rather than a
  general requirement to omit the environment variable.
- Mock mode off, real-provider guards on. Exact three-seat roster; Cursor disabled.
- Expected behavior: the public prefix-membership defect supplies a reviewable
  finding. A trusted smoke-only rule requires sequential glob/grep/read calls.
  All enabled reviews and critiques must succeed, produce schema-valid artifacts
  usable by consensus, and post a finding without observed model/mock fallback.

## Actual result

Effective config digest: `854ecc519b613836f65a59caa4136d99c02e4b94644f9e9458c719321a193955`.
The refreshed YAML resolves to this digest with GitHub posting mode.

| Seat | Model / effort | Review | raw / accepted / dropped | Critique |
|---|---|---|---|---|
| Claude | `anthropic/claude-haiku-4.5` / `medium` | success, usable | 1 / 1 / 0 | success, 4 assessments |
| Codex | `openai/gpt-6-luna` / `low` | success, usable | 1 / 1 / 0 | success, 4 assessments |
| OpenCode | `xiaomi/mimo-v2.6-flash` / absent | success, usable | 3 / 2 / 1 | success, 4 assessments |
| Cursor | `composer-2.5`, disabled | skipped | 0 / 0 / 0 | skipped |

MiMo Flash completed `glob`, `grep`, `read`, `grep`, `read`, and
`StructuredOutput`. Native metadata recorded two review assistant messages with
`providerID: openrouter` and `modelID: xiaomi/mimo-v2.6-flash`; critique recorded
the same route and completed `StructuredOutput`. Both adapter logs confirmed
structured-output transport. No model substitution or mock fallback was observed.
One review finding crossed a diff hunk and was dropped; the seat remained usable.

Independent consensus-input validation passed. The panel was full; all three
enabled seats were eligible for resolution, with no failed reviewers. Posting
created discussions `4178105065`, `4178105098`, and `4178105121` without warnings.
The temporary PR was closed; its branch and findings were preserved.

## Audit

- Artifacts inspected: prepared manifest/config/diff/snapshot/rules; each seat's
  finding, critique, pooled-finding, and status JSON; sanitized OpenCode native
  model/tool metadata; consensus and post-result JSON.
- Logs inspected: workflow/stage outcomes and OpenCode review/critique transport
  logs; CLI selector output was collected in the disabled Cursor job.
- Credential values absent: pattern/entropy scan passed for the 37 Flash files.
  The original combined audit passed for 79 retained files (1.6 MB, ten detectors):
  37 Flash files, 38 Pro/Grok files, and four job/workflow logs. No exact-value scan
  was performed because demo secrets are hidden.
- Sensitive model content omitted: only route names, identifiers, counts, and
  outcomes are retained here; no prompts, model prose, or CLI session material.
- Known unexercised paths: GitLab, provider billing/token counts, stronger
  Claude/Codex recommendations, and final-image release validation. This run did
  not exercise the newer CLI pins introduced in `46c8c46fd0df7a0174906bba960a49f7fbb8722c`.

## Verdict

Passed for the three-seat candidate config on this GitHub runtime and immutable
image pair: real review/critique, MiMo sequential tools and structured output,
usable full consensus, and successful posting. It supplies historical route and
roster evidence only for these coordinates. Repeat the smoke on final published
images before claiming release validation.
