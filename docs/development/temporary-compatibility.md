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
| COMPAT-005 | Release/security maintainers | Merge-gate removal | [`RESERVED_DIRECT_JOB_NAMES`](../../scripts/pipeline_trust.py) | A tagged gate-free release has shipped, so no supported consumer still declares the old job name | First release after 2.0.0, the first tagged gate-free release |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.
