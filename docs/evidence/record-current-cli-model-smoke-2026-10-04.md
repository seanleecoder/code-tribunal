# Evidence record: GitHub / current-CLI default smoke and Composer / 2026-10-04

Status: passed

Supplemental candidate-config evidence, unselected in release inputs. These runs
exercise the current CLI pins on the existing published runtime below, with
refreshed candidate YAML applied in each job. They do not certify final images
containing PR #174's config/override implementation or a release campaign.

## Identity

- Platform and version: GitHub.com / GitHub Actions, hosted Linux container jobs.
- Date/time and timezone: default smoke 2026-10-04 19:47:25–19:50:12 UTC;
  Composer canary 19:47:31–19:50:51 UTC.
- Deployment topology: canonical prepare → review → critique → consensus → post,
  candidate YAML applied before each stage; provider credentials stay in Actions.
- Consumer/template project: `seanleecoder/code-tribunal-demo`; canonical workflow
  copied from product commit `0ac84363454f46820d49c331cdf543405c3b4c80`, with only
  temporary image/config/rule, CLI-version, and sanitized tool-metadata steps.
- Change requests and workflow commits: default [PR #37](https://github.com/seanleecoder/code-tribunal-demo/pull/37),
  head/workflow `b12b8bed443746d549cc20fa4ae22f091139c9b4`; Composer
  [PR #38](https://github.com/seanleecoder/code-tribunal-demo/pull/38), head/workflow
  `33463a06a4c0e8b5f8b492ab1afb9f69ca599f84`.
- Pipeline/workflow runs: default [37229611088](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/37229611088)
  (`gh-37229611088-1`), Composer [37229616128](https://github.com/seanleecoder/code-tribunal-demo/actions/runs/37229616128)
  (`gh-37229616128-1`); both attempt 1, conclusion `success`.
- Source commit (image runtime): `46c8c46fd0df7a0174906bba960a49f7fbb8722c`;
  successful image publication run `37206979549`.
- Base image tag and digest: tag `2.0-46c8c46fd0df7a0174906bba960a49f7fbb8722c`,
  `ghcr.io/seanleecoder/code-tribunal/ai-review-base@sha256:0df16be357b66636e4298ea168adbeb266ec0c71db4cdbf11501c94652378486`.
- Reviewer image tag and digest: same source tag,
  `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer@sha256:fe61f8fe654b0a1729f790b65ba8f8a390edf68132aa568b45e5e5117fc93d34`.
- CLI versions captured in every review job: Claude `2.1.289`, Codex `0.160.0`,
  OpenCode `1.18.34`, Cursor `2026.10.01-e373342`; all match current pins.

| Job | Default-smoke ID | Composer-canary ID |
|---|---|---|
| prepare | `111516317997` | `111516334907` |
| review Claude | `111516380599` | `111516395150` |
| review Codex | `111516380627` | `111516395236` |
| review OpenCode | `111516380634` | `111516395103` |
| review Cursor | `111516380591` | `111516395126` |
| critique Claude | `111516584599` | `111516646268` |
| critique Codex | `111516584519` | `111516646277` |
| critique OpenCode | `111516584536` | `111516646265` |
| critique Cursor | `111516584575` | `111516646258` |
| consensus | `111516704308` | `111516840589` |
| post | `111516767830` | `111516906495` |

## Preconditions

- Both image attestations verified with `gh attestation verify`: exact subject,
  source digest `46c8c46…`, source ref `refs/heads/main`, SLSA v1 predicate, and
  certificate identity for the canonical `publish-ai-review-images.yml` on main.
- Read-only `make demo-preflight` passed before both runs and after cleanup:
  credentials configured, automatic mode, no required retired check or sticky
  mock variables, and the persisted demo roster still includes all four seats.
  No credential values were retrieved or written locally.
- Trigger: automatic `pull_request` on each temporary branch. Each copied workflow
  bypassed manual mode and set mock off and all real-provider guards on.
- Exact default smoke: model and effort overrides blank, with
  `AI_REVIEW_OPENCODE_EFFORT` omitted; candidate YAML enabled Claude/Codex/OpenCode
  and disabled Cursor. Only its temporary workflow selected this three-seat roster.
- Composer canary: same three default routes, plus enabled `composer-2.5`;
  temporary workflow explicitly selected the four seats. Same absent MiMo effort.
- Expected behavior: the public prefix-membership defect yields a finding. The
  smoke-only rule requires read/glob/grep and sequential calls before output.
  Require real review/critique, structured schema-valid output, full usable
  consensus, successful posting, and no observed model/mock fallback. Authenticated
  `cursor-agent --list-models` must confirm the named Composer selector.

## Actual result

The default smoke resolved digest
`854ecc519b613836f65a59caa4136d99c02e4b94644f9e9458c719321a193955`, matching
PR #174's three-seat YAML with GitHub posting mode. Each enabled reviewer produced
one accepted finding, none dropped, and was usable for resolution. Each critic
produced three assessments; Cursor's review/critique artifacts were skipped.

The Composer canary resolved digest
`59bb96ede4d5f813a1563d07b9ee12f4d261aa10530a35c9cbbc34c06562dd59`.

| Seat | Model / effort | Composer-run raw / accepted / dropped | Critique |
|---|---|---|---|
| Claude | `anthropic/claude-haiku-4.5` / `medium` | 2 / 1 / 1 | success, 6 assessments |
| Codex | `openai/gpt-6-luna` / `low` | 1 / 1 / 0 | success, 6 assessments |
| OpenCode | `xiaomi/mimo-v2.6-flash` / absent | 1 / 1 / 0 | success, 6 assessments |
| Cursor | `composer-2.5` | 3 / 3 / 0 | success, 6 assessments |

All four reviews succeeded and remained usable. Both runs independently passed
`validate_consensus_inputs` on downloaded artifacts; panels were full, no reviewers
failed, and all enabled seats were eligible for resolution. Default posting created
`4179066959`; Composer posting created `4179071273`, `4179071334`, and `4179071372`.
Both post results had exact head bindings and no warnings.

Native OpenCode metadata in both review and critique stages recorded only
`providerID: openrouter`, `modelID: xiaomi/mimo-v2.6-flash`. Both reviews completed
`glob`, `grep`, `read`, `grep`, `read`, then `StructuredOutput`; both critiques
completed `StructuredOutput`. In the three-seat run the first glob/grep/read calls
were sequential, while the following grep overlapped the preceding read. The
four-seat run completed all five requested calls sequentially, then additional
searches, recovered from one read error, and produced structured output. Together
these runs establish the tool loop and its reuse across the exact default roster.

Authenticated selector inspection on Cursor `2026.10.01-e373342` listed
`composer-2.5`, `composer-2.5-fast`, and `grok-4.7-{low,medium,high,xhigh}` with
corresponding `-fast` variants; bare `grok-4.7` was absent. Composer's exact selector
produced real review/critique artifacts accepted by schema and integrity checks.
No model substitution or mock fallback was observed in the available metadata,
artifacts, or logs. The named CLI invocation and artifacts establish the Cursor
selector; they do not expose an independent backend model identity.

Both temporary PRs were closed without merging. Their branches and findings
remain available; the persisted demo roster and credentials were unchanged.

## Audit

- Artifacts inspected: both prepared manifests/configs/diffs/snapshots/rules; all
  finding, critique, pooled-finding, and status JSON; CLI versions and authenticated
  Cursor selector lists; sanitized native OpenCode model/tool metadata; consensus
  and post results. Independent validation checked run/config/model bindings.
- Logs inspected: all stage logs for both successful workflows, including provider
  transport outcomes and real-provider guard settings.
- Credential values absent: pattern/entropy scan passed for 107 artifact/log files
  (1.5 MB, ten detectors). Exact-value scanning was unavailable because Actions
  secrets are hidden; no credentials or native session databases were downloaded.
- Sensitive model content omitted: this record retains identifiers, route/tool
  names, counts, and outcomes; no prompts or model prose.
- Known unexercised paths: GitLab, final PR #174 images, live explicit effort
  clearing, MiMo Pro/Grok on newer pins, stronger Claude/Codex recommendations,
  Composer fast, other Grok variants, billing/token counts, and availability across
  customer plans. The [older Pro/Grok record](record-mimo-pro-grok-2026-10-04.md)
  stays scoped to its original runtime. Unit tests cover explicit effort clearing.

## Verdict

Passed for the exact default three-seat candidate config and real Composer 2.5
review/critique on the recorded GitHub runtime, image pair, CLI pins, and account.
These are current-CLI route/roster checks; final-image release validation still
requires a smoke against the final published pair.
