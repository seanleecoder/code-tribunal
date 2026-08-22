# Temporary compatibility paths

This is the current register of migration-only behavior. New migration aliases,
schema decoders, retired environment-variable tombstones, duplicated old/new
paths, and temporary feature flags require a row here and a nearby code comment
naming the ID. The reasoning for each path lives next to the code; a row records
who owns it, when it goes, and where to find it.

| ID | Owner | Introduced | Code references | Removal condition | Target release/issue |
|---|---|---|---|---|---|
| COMPAT-001 | Configuration maintainers | `review_config.v2` and `review_config.v3` | [`RETIRED_ENV_OVERRIDES`](../../ai-review/src/ai_review/config.py) | No supported deployment can plausibly still set these persisted CI variables; removal is a breaking migration and must be announced | First major release after a tagged `review_config.v3` deployment |
| COMPAT-002 | Configuration maintainers | `review_config.v3` | [`V3_REMOVED_CONFIG_KEYS` and the schema-version migration diagnostic](../../ai-review/src/ai_review/config.py) | `review_config.v2` has left the supported upgrade window | Release that narrows the supported upgrade window past `review_config.v2` |
| COMPAT-003 | Platform maintainers | Unified GitLab token migration | [`create_runtime_platform`](../../ai-review/src/ai_review/platform/runtime.py) | Supported installations have migrated to `GITLAB_TOKEN`, so removing the named diagnostic cannot turn a formerly effective credential into a silent no-op | Next major product release compatibility review |
| COMPAT-004 | Posting/state maintainers | `render-body.v4` | [`REVIEW_SECTION_BOUNDARIES`](../../ai-review/src/ai_review/notes.py) | A tagged `render-body.v4` release has shipped, closing the window for recovering threads written by the previous renderer | First release after one tagged `render-body.v4` rollout |
| COMPAT-005 | Release/security maintainers | Merge-gate removal | [`RESERVED_DIRECT_JOB_NAMES`](../../scripts/pipeline_trust.py) | A tagged gate-free release has shipped, so no supported consumer still declares the old job name | First release after one tagged gate-free rollout |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.
