# Artifacts and schemas

Every stage passes files, not in-memory model decisions, to the next stage.
Artifacts from different runs or effective configurations must never be mixed.

| Path | Producer | Consumer | Schema or contract |
|---|---|---|---|
| `inputs/manifest.json` | prepare | all later stages | revision, run ID, diff digest, and effective-config binding |
| `inputs/diff.patch` | prepare | prompt rendering and anchoring | complete bounded diff for one verified revision |
| `inputs/repo_snapshot/` | prepare | reviewers | contained regular files/directories; no symlinks or special files |
| `inputs/state_aliases.json` | prepare | consensus/post | [`state_aliases.schema.json`](../../ai-review/schemas/state_aliases.schema.json) |
| `inputs/config.review.yaml` | prepare | audit/debug | immutable copy of loaded configuration |
| `out/status/<reviewer>.json` | reviewer adapter | consensus/operator | [`adapter_status.schema.json`](../../ai-review/schemas/adapter_status.schema.json) |
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

GitLab artifacts expire after seven days in the canonical template. GitHub
retention follows the repository or organization Actions setting because the
workflow does not override it. Persisted review state lives in a bot-owned
GitLab note or GitHub PR comment, not in expiring CI artifacts.
