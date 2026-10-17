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
| COMPAT-004 | GitLab integration maintainers | 2.0.3 (SPEC-43), deprecating the hardened child | [`review-child.gitlab-ci.yml`](../../ai-review/ci/review-child.gitlab-ci.yml), [`_validate_child_mode` and `--mode child`](../../scripts/pipeline_trust.py), [`gitlab-child.yml`](../getting-started/examples/gitlab-child.yml), the child-mode tests, and the hostile canary's child composition | GitLab supports the direct include only; the hostile canary composes a direct include | Next major release (N02) |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.
