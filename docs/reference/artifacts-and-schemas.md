# Artifacts and schemas

Every stage passes files, not in-memory model decisions, to the next stage.
Artifacts from different runs or effective configurations must never be mixed.

| Path | Producer | Consumer | Schema or contract |
|---|---|---|---|
| `inputs/manifest.json` | prepare | all later stages | revision, run ID, diff digest, and effective-config binding |
| `inputs/mr.diff` | prepare | prompt rendering, anchoring, post | complete bounded diff for one verified revision; no standalone schema |
| `inputs/repo_snapshot/` | prepare | reviewers | contained regular files/directories; no symlinks or special files |
| `inputs/prior_decisions.json` | prepare | prompt rendering | compact settled/open history derived from authenticated state; `prior_decisions.v1` inline contract |
| `inputs/prompts/` | prepare | prompt rendering | immutable copy of trusted review/critique prompt templates; directory digest is indirectly bound by the trusted image/source |
| `inputs/rules/` | prepare | prompt rendering | immutable trusted rule files; `rules_sha256` is recorded in the manifest |
| `inputs/state_aliases.json` | prepare | consensus/post | [`state_aliases.schema.json`](../../ai-review/schemas/state_aliases.schema.json) |
| `inputs/config.review.yaml` | prepare | all later stages/audit | immutable copy of loaded configuration; validated against the executable config contract |
| `out/status/<reviewer>.json` | reviewer adapter | consensus/operator | [`adapter_status.schema.json`](../../ai-review/schemas/adapter_status.schema.json) |
| `out/status/critique-<reviewer>.json` | critic adapter | consensus/operator | [`adapter_status.schema.json`](../../ai-review/schemas/adapter_status.schema.json) |
| `out/status/<stage>-<reviewer>-parse-debug.txt` | adapter runner on parse/validation failure | operator | redacted bounded head/tail previews; diagnostic text, no schema |
| `out/findings/<reviewer>.json` | reviewer adapter | consensus/critique | [`finding_batch.schema.json`](../../ai-review/schemas/finding_batch.schema.json) |
| `out/pooled_findings/<reviewer>.json` | critique preparation | critic | anonymized finding pool contract |
| `out/critiques/<reviewer>.json` | critic adapter | consensus | [`critique_batch.schema.json`](../../ai-review/schemas/critique_batch.schema.json) |
| `out/consensus/consensus.json` | consensus | post/gate/operator | [`consensus.schema.json`](../../ai-review/schemas/consensus.schema.json) |
| `out/post/post_result.json` | post | gate/operator | [`post_result.schema.json`](../../ai-review/schemas/post_result.schema.json) |
| `out/gate/gate_result.json` | gate | CI/operator | [`gate_result.schema.json`](../../ai-review/schemas/gate_result.schema.json) |
| encoded state note/comment | post | later prepare/post | [`state.schema.json`](../../ai-review/schemas/state.schema.json) plus author verification and checksum |

Raw model output is normalized through
[`raw_finding_batch.schema.json`](../../ai-review/schemas/raw_finding_batch.schema.json)
before it can become a finding batch. Schema validity alone does not make a
reviewer resolution-eligible: the batch-quality and effective-config fields are
also evaluated by consensus.

In the canonical GitLab template, prepare/review/critique artifacts expire after
seven days; consensus/post/gate evidence expires after 30 days. GitHub retention
follows the repository or organization Actions setting because the workflow does
not override it. Persisted review state lives in a bot-owned GitLab note or
GitHub PR comment, not in expiring CI artifacts.
