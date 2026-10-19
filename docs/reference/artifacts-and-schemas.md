# Artifacts and schemas

Every stage passes files, not in-memory model decisions, to the next stage.
Artifacts from different runs or effective configurations must never be mixed.

| Path | Producer | Consumer | Schema or contract |
|---|---|---|---|
| `inputs/manifest.json` | prepare | all later stages | revision, run ID, diff digest, and effective-config binding: the whole validated config as `effective_config` and its digest as `effective_config_sha256`, which every later stage must reproduce |
| `inputs/mr.diff` | prepare | prompt rendering, anchoring, post | complete bounded diff for one verified revision; no standalone schema |
| `inputs/repo_snapshot/` | prepare | reviewers | contained regular files/directories; no symlinks or special files |
| `inputs/prior_decisions.json` | prepare | prompt rendering | compact settled/open history derived from authenticated state; `prior_decisions.v1` inline contract |
| `inputs/state_aliases.json` | prepare | consensus/post | [`state_aliases.schema.json`](../../ai-review/schemas/state_aliases.schema.json) |
| `out/status/<reviewer>.json` | reviewer adapter | consensus/operator | [`adapter_status.schema.json`](../../ai-review/schemas/adapter_status.schema.json) |
| `out/status/critique-<reviewer>.json` | critic adapter | consensus/operator | [`adapter_status.schema.json`](../../ai-review/schemas/adapter_status.schema.json) |
| `out/status/<stage>-<reviewer>-parse-debug.txt` | adapter runner on parse/validation failure | operator | redacted bounded head/tail previews, newline structure preserved; diagnostic text, no schema |
| `out/status/<stage>-<reviewer>-parse-raw-stdout.txt` | adapter runner on parse/validation failure | operator | complete redacted adapter stdout, bounded at 2 MiB with an explicit truncation marker; diagnostic text, no schema |
| `out/findings/<reviewer>.json` | reviewer adapter | consensus/critique | [`finding_batch.schema.json`](../../ai-review/schemas/finding_batch.schema.json) |
| `out/pooled_findings/<reviewer>.json` | critique preparation | critic | anonymized finding pool contract |
| `out/critiques/<reviewer>.json` | critic adapter | consensus | [`critique_batch.schema.json`](../../ai-review/schemas/critique_batch.schema.json) |
| `out/consensus/consensus.json` | consensus | post/operator | [`consensus.schema.json`](../../ai-review/schemas/consensus.schema.json); the `drop` decision and `summary.drop_count` are retained for compatibility, and `drop_count` is always `0`. `run_health` is always written and post requires it |
| `out/post/post_result.json` | post | operator | [`post_result.schema.json`](../../ai-review/schemas/post_result.schema.json) |
| encoded state note/comment | post | later prepare/post | [`state.schema.json`](../../ai-review/schemas/state.schema.json) plus author verification and checksum |

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

`run_health` is an optional property of `consensus.v2`, so a consensus artifact
from 2.0.x still validates. Adding an optional property under the same schema
identifier is a minor change. That is sound only because producer and consumer
always run from one pinned image pair and artifacts are never mixed across runs.

`post_result.json` is the terminal artifact: nothing downstream reads it, and
`post` validates it against its schema on write. It is operator diagnostics plus
the job's exit status, not an input to another stage.

Raw model output is normalized through
[`raw_finding_batch.schema.json`](../../ai-review/schemas/raw_finding_batch.schema.json)
before it can become a finding batch. Schema validity alone does not make a
reviewer resolution-eligible: the batch-quality and effective-config fields are
also evaluated by consensus.

In the canonical GitLab template, prepare/review/critique artifacts expire after
seven days; consensus/post evidence expires after 30 days. GitHub retention
follows the repository or organization Actions setting because the workflow does
not override it. Persisted review state lives in a bot-owned GitLab note or
GitHub PR comment, not in expiring CI artifacts.
