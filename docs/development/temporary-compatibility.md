# Temporary compatibility paths

This is the current register of migration-only behavior. New migration aliases,
schema decoders, retired environment-variable tombstones, duplicated old/new
paths, and temporary feature flags require a row here and a nearby code comment
naming the ID. The reasoning for each path lives next to the code; a row records
who owns it, when it goes, and where to find it.

| ID | Owner | Introduced | Code references | Removal condition | Target release/issue |
|---|---|---|---|---|---|
| COMPAT-001 | Configuration maintainers | `review_config.v2` and `review_config.v3` | [`RETIRED_ENV_OVERRIDES`](../../ai-review/src/ai_review/config.py) | No supported deployment can plausibly still set these persisted CI variables; removal is a breaking migration and must be announced | First major release after 2.0.0, the first tagged `review_config.v3` deployment |
| COMPAT-002 | Configuration maintainers | `review_config.v3`; extended to v1 for 2.0.0 | [`V3_REMOVED_CONFIG_KEYS` and the schema-version migration diagnostic](../../ai-review/src/ai_review/config.py) | `review_config.v1` (shipped by every 1.x release) and the never-released `review_config.v2` have left the supported upgrade window | Release that ends support for upgrading directly from 1.x |
| COMPAT-003 | Consensus maintainers | SPEC-67 (2.0.3), within `consensus.v2` | [`Decision`](../../ai-review/src/ai_review/types.py), [`summary.drop_count`](../../ai-review/src/ai_review/consensus.py), and the `drop` value and `drop_count` field in [`consensus.schema.json`](../../ai-review/schemas/consensus.schema.json) | Consensus moves to `consensus.v3`, which removes the `drop` decision and `summary.drop_count` | Next major release (consensus cutover, N11) |
| COMPAT-004 | GitLab integration maintainers | 2.0.3 (SPEC-43), deprecating the hardened child | [`review-child.gitlab-ci.yml`](../../ai-review/ci/review-child.gitlab-ci.yml), [`_validate_child_mode` and `--mode child`](../../scripts/pipeline_trust.py), [`gitlab-child.yml`](../getting-started/examples/gitlab-child.yml), the [deprecated child section](../getting-started/gitlab.md#deprecated-hardened-child) and child mentions in [`SECURITY_MODEL.md`](../SECURITY_MODEL.md), the README, [`ai-review/README.md`](../../ai-review/README.md), and the [getting-started index](../getting-started/README.md), the child-mode tests, and the hostile canary's child composition | GitLab supports the direct include only; the hostile canary composes a direct include | Next major release (N02) |
| COMPAT-005 | State maintainers | 2.0.3 (N13), within `state_schema_version: 1` | [`State.run_history` and `StateAliases.candidate_issue_signatures`](../../ai-review/src/ai_review/types.py), [`normalize_state` and `normalize_state_record`](../../ai-review/src/ai_review/memory.py), and the `run_history` property and required `candidate_issue_signatures` key in [`state.schema.json`](../../ai-review/schemas/state.schema.json) | The state format is migrated once and no supported release reads a note that carries `run_history` or a candidate-signature alias | Next major release (S14 state migration) |
| COMPAT-006 | Consensus maintainers | SPEC-66 (2.1.0), within `consensus.v2` | [`Consensus.run_health`](../../ai-review/src/ai_review/types.py), the missing-`run_health` refusal in [`post.py`](../../ai-review/src/ai_review/post.py), and the optional `run_health` property in [`consensus.schema.json`](../../ai-review/schemas/consensus.schema.json) | Consensus moves to `consensus.v3`, which makes `run_health` required; post then relies on the schema | Next major release (consensus cutover, N11) |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.
