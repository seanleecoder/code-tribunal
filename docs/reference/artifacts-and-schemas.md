# Artifacts and schemas

Every stage passes files, not in-memory model decisions, to the next stage.
Artifacts from different runs or effective configurations must never be mixed.

| Path | Producer | Consumer | Schema or contract |
|---|---|---|---|
| `inputs/manifest.json` | prepare | all later stages | revision, run ID, and effective-config binding: the whole validated config as `effective_config` and its digest as `effective_config_sha256`, which every later stage must reproduce |
| `inputs/mr.diff` | prepare | prompt rendering, anchoring | complete bounded diff for one verified revision; no standalone schema |
| `inputs/repo_snapshot/` | prepare | reviewers | contained regular files/directories; no symlinks or special files |
| `inputs/prior_decisions.json` | prepare | prompt rendering | compact settled/open history derived from authenticated state; `prior_decisions.v1` inline contract |
| `out/status/<reviewer>.json` | reviewer adapter | consensus/operator | [`adapter_status.schema.json`](../../ai-review/schemas/adapter_status.schema.json) |
| `out/status/critique-<reviewer>.json` | critic adapter | consensus/operator | [`adapter_status.schema.json`](../../ai-review/schemas/adapter_status.schema.json) |
| `out/status/<stage>-<reviewer>-parse-debug.txt` | adapter runner on parse/validation failure | operator | redacted bounded head/tail previews, newline structure preserved; diagnostic text, no schema |
| `out/status/<stage>-<reviewer>-parse-raw-stdout.txt` | adapter runner on parse/validation failure | operator | complete redacted adapter stdout, bounded at 2 MiB with an explicit truncation marker; diagnostic text, no schema |
| `out/findings/<reviewer>.json` | reviewer adapter | consensus/critique | [`finding_batch.schema.json`](../../ai-review/schemas/finding_batch.schema.json) |
| `out/pooled_findings/<reviewer>.json` | critique preparation | operator | [`pooled_findings.schema.json`](../../ai-review/schemas/pooled_findings.schema.json); audit copy of the pool and short-ID mapping held in runner memory |
| `out/critiques/<reviewer>.json` | critic adapter | consensus | [`critique_batch.schema.json`](../../ai-review/schemas/critique_batch.schema.json) |
| `out/consensus/consensus.json` | consensus | post/operator | [`consensus.schema.json`](../../ai-review/schemas/consensus.schema.json); `consensus.v3` contains state-free groups, `surface`/`fyi` decisions and required `run_health` |
| `out/post/post_result.json` | post | operator | [`post_result.schema.json`](../../ai-review/schemas/post_result.schema.json) |
| encoded summary comment | post | later prepare/post | [`state.schema.json`](../../ai-review/schemas/state.schema.json) plus author verification and the embedded `state_hash` |

The bundle carries no prompt, rule, or configuration copy. Review and critique
render prompts and rules from `prompts/` and `rules/` below the image's trusted
runtime root, never from the bundle or from wherever `AI_REVIEW_CONFIG` points.
Every stage loads `AI_REVIEW_CONFIG` itself and must reproduce the manifest's
`effective_config_sha256`.

`consensus.json` carries `run_health`, an observational record of the run: one
job row per enabled review seat (and per critic when critique is enabled), with
model, effort, stage timeout, outcome, duration, finding counts and the redacted
error, plus a `full`, `degraded` or `rerun_recommended` grade. Consensus builds
it from the finding and critique batches and the status files it is given with
`--status-dir`. It never changes a group decision, `panel_status`,
`failed_reviewers`, resolution eligibility, or an exit code. Post renders it as
the first section of the summary comment and refuses an artifact without it.

`consensus.v3` removes `issue_id_source`, `state_match`, `group.body_hash`,
`summary.drop_count` and the `drop` decision. Every group has a signature-derived
issue ID; posting alone matches it to historical state and chooses a reusable
thread. Prepare carries no `state_aliases.json`, and consensus accepts no
`--state` argument. Version 2 consensus artifacts are rejected. Producer and
consumer must run from one pinned image pair with fresh artifacts from prepare.
Persisted state uses version 2 inside the summary comment. The v1 ledger is
converted explicitly before switching images; see the
[state migration](../operations.md#state-v2-migration).

`post_result.json` is the terminal artifact: nothing downstream reads it, and
`post` validates it against its schema on write. It is operator diagnostics plus
the job's exit status, not an input to another stage.

Raw model output is normalized through
[`raw_finding_batch.schema.json`](../../ai-review/schemas/raw_finding_batch.schema.json)
or [`raw_critique_batch.schema.json`](../../ai-review/schemas/raw_critique_batch.schema.json)
before it can become a `finding_batch.v2` or `critique_batch.v2`. Reviewers author
`location` (path, side, start/end line and symbol) and finding content. Trusted
finalization resolves the location against the prepared diff and derives the
anchor metadata, hashes and IDs. An unresolvable location drops only that finding.
Every candidate is checked before the per-seat cap; malformed drops and valid
cap omissions remain distinct in the existing quality counts. Neither findings
nor critiques carry numeric confidence.

Critics see anonymized finding content with short IDs such as `F001`, not the
pool's internal source-ID mapping or run/config bookkeeping. Finalization maps
those references through the exact `pooled_findings.v2` pool used to render the
prompt, held in runner memory and bound to the run, critic and effective config.
The audit file is never reloaded after the model runs. An unknown target or
duplicate reference fails the critique batch. Version 1 finding/critique batches
are rejected; run every stage from one image pair and generate fresh artifacts.
Schema validity alone does not make a reviewer resolution-eligible: consensus
uses the runner's batch-quality flag and validates the effective-config binding.

In the canonical GitLab template, prepare/review/critique artifacts expire after
seven days; consensus/post evidence expires after 30 days. GitHub retention
follows the repository or organization Actions setting because the workflow does
not override it. Persisted review state lives inside the bot-owned summary comment on
GitLab or GitHub, not in expiring CI artifacts.
