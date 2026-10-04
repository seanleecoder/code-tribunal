# Evidence record: model refresh / 2026-10-04

Status: passed

Scoped candidate-config evidence on existing published images. This record does
not certify a final image pair containing the refreshed shipped YAML and is not
selected in release inputs. Historical evidence records remain unchanged.

## Runtime and configuration

- Runtime source: `e1313b55e566e384d916da0cdc294969dc700fdf`.
- Publication run: `37202713222`.
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:e92ea908a185f56ab03138b610153a195c81ca1d88dc93c2a089ef37ba79c8c4`.
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:ff91e876dd6d411060c5f9109b02ca7cb0f6decbeb8cd2603165e29de2f21cbf`.
- Both image attestations verified against that source, `refs/heads/main`, the
  canonical publisher identity, and the SLSA provenance predicate.
- CLI pins: Claude `2.1.283`, Codex `0.157.1`, OpenCode `1.18.32`, Cursor
  `2026.09.26-dd393fe`.
- Each stage applied the same candidate model defaults to the published config;
  adapters, schemas, routing, credentials, timeouts, and consensus were unchanged.
  Model/effort overrides were blank, and `AI_REVIEW_OPENCODE_EFFORT` was absent.
  Mock mode was off and all real-provider guards were on.
- Used the public demo's established prefix-membership defect and a trusted
  smoke-only rule requiring read/search tool calls before review output. Hidden
  demo credentials stayed inside GitHub Actions; no credential values were read
  into the local workspace.

## Shipped three-seat candidate

- [Demo PR #35](https://github.com/seanleecoder/code-tribunal-demo/pull/35), head
  `a640f8a65b91cbc59bf820c5332efbc416200c26`.
- [Run 37210076322](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/37210076322),
  14:39:39–14:42:45 UTC, conclusion `success`.
- Artifact run ID: `gh-37210076322-1`; effective config digest
  `854ecc519b613836f65a59caa4136d99c02e4b94644f9e9458c719321a193955`.
  The current YAML resolves to this digest with the workflow's GitHub posting mode.

| Seat | Model / effort | Review | raw / accepted / dropped | Critique |
|---|---|---|---|---|
| Claude | `anthropic/claude-haiku-4.5` / `medium` | success, usable | 1 / 1 / 0 | success, 4 assessments |
| Codex | `openai/gpt-6-luna` / `low` | success, usable | 1 / 1 / 0 | success, 4 assessments |
| OpenCode | `xiaomi/mimo-v2.6-flash` / unset | success, usable | 3 / 2 / 1 | success, 4 assessments |
| Cursor | `composer-2.5`, disabled | skipped | 0 / 0 / 0 | skipped |

MiMo Flash completed `glob`, `grep`, `read`, `grep`, `read`, and
`StructuredOutput`. The native session recorded two assistant messages with
`providerID: openrouter` and `modelID: xiaomi/mimo-v2.6-flash`; critique recorded
the same route and completed `StructuredOutput`. Both adapter logs confirmed
structured-output transport. No model substitution or mock fallback was observed.
One review finding was dropped because its location range crossed a diff hunk;
the seat remained usable for resolution.

Consensus input validation passed independently on the downloaded review and
critique artifacts. The panel was full with all three enabled seats eligible and
no failed reviewers. Posting succeeded, created three discussions, and reported
no warnings.

## Cursor selector inspection

The pinned CLI's authenticated `--list-models` confirmed `composer-2.5`,
`composer-2.5-fast`, and these Grok selectors:

```text
grok-4.7-low
grok-4.7-low-fast
grok-4.7-medium
grok-4.7-medium-fast
grok-4.7-high
grok-4.7-high-fast
grok-4.7-xhigh
grok-4.7-xhigh-fast
```

It did not list bare `grok-4.7`. Value / Balance therefore uses
`grok-4.7-medium`; quality escalation uses `grok-4.7-xhigh`. Selector availability
alone does not prove review/critique compatibility for every variant.

## Supplemental MiMo Pro and Grok canary

- [Demo PR #36](https://github.com/seanleecoder/code-tribunal-demo/pull/36), head
  `c71fca8d832aac446be23839b05e0edae20e5196`.
- [Run 37210473861](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/37210473861),
  14:46:01–15:03:51 UTC, conclusion `success`, using the same image pair.
- Artifact run ID: `gh-37210473861-1`; effective config digest
  `8654fba9754ade76b955b80b7161385be30c58aa17af6d6a2323b8b29fe0daa7`.
- This candidate changed OpenCode to `xiaomi/mimo-v2.6-pro` / unset and enabled
  Cursor with `grok-4.7-medium`. Claude and Codex retained the smoke models.

| Seat | Review raw / accepted / dropped | Critique |
|---|---|---|
| Claude | 3 / 3 / 0 | success, 8 assessments |
| Codex | 1 / 1 / 0 | success, 8 assessments |
| OpenCode MiMo Pro | 3 / 3 / 0 | success, 8 assessments |
| Cursor Grok Medium | 1 / 1 / 0 | success, 8 assessments |

MiMo Pro preserved `providerID: openrouter` and `modelID: xiaomi/mimo-v2.6-pro`
across twelve review assistant turns. It exercised read, glob, grep, sequential
tool calls, recovery from three tool errors, and successful `StructuredOutput`.
Critique completed `StructuredOutput` on the same model. The review job took
12m47s and critique took 3m49s; this is one observed run, not a latency benchmark.

Grok Medium completed real review and critique using the confirmed exact selector.
Its result-text JSON passed shared schema and consensus-integrity validation.
No model substitution or mock fallback was observed. `grok-4.7-xhigh` and all
`-fast` variants were listed by the CLI but were not canaried.

Independent consensus-input validation passed. The full four-seat panel had no
failed reviewers, all four seats were eligible for resolution, and posting
created three discussions without warnings. Both temporary smoke PRs were
closed after their successful runs; their branches and findings were preserved.

## Audit and scope

The pattern/entropy credential scan passed on 79 retained files (1.6 MB, ten
detectors): 37 Flash artifacts, 38 Pro/Grok artifacts, and four job/workflow logs.
An exact-value scan was not performed because the demo keys are hidden. Provider
billing/token counts are unasserted. This campaign proves the GitHub candidate
config and its recorded runtime; it does not prove a
new final-image release, GitLab execution, or the stronger Claude/Codex production
models.

The subsequent dependency upgrade merged in `46c8c46fd0df7a0174906bba960a49f7fbb8722c`
pins Claude `2.1.289`, Codex `0.160.0`, OpenCode `1.18.34`, and Cursor
`2026.10.01-e373342`. Those newer CLIs were not exercised by these canaries;
repeat the final-image smoke before claiming release validation on them.
