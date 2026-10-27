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
| COMPAT-004 | GitLab integration maintainers | 2.0.3 (SPEC-43), deprecating the hardened child | [`review-child.gitlab-ci.yml`](../../ai-review/ci/review-child.gitlab-ci.yml), [`_validate_child_mode` and `--mode child`](../../scripts/pipeline_trust.py), [`gitlab-child.yml`](../getting-started/examples/gitlab-child.yml), the [deprecated child section](../getting-started/gitlab.md#deprecated-hardened-child) and child mentions in [`SECURITY_MODEL.md`](../SECURITY_MODEL.md), the README, [`ai-review/README.md`](../../ai-review/README.md), and the [getting-started index](../getting-started/README.md), the child-mode tests, and the hostile canary's child composition | GitLab supports the direct include only; the hostile canary composes a direct include | Next major release (N02) |
| COMPAT-008 | Publication maintainers | N12 (2.1.0), for rollback to 2.0.3 | the zero `source=` attribute written by [`_inline_marker`](../../ai-review/src/ai_review/render.py) | No supported rollback target requires `source=`: 2.1.x already parses markers without it | Next major release |
| COMPAT-009 | State maintainers | S14, state v2 | [`scripts/migrate_state_v2.py`](../../scripts/migrate_state_v2.py), an offline v1 decoder only; `memory.decode_state_note_body` redirects unmigrated v1 ledgers to it | Every supported deployment has completed the explicit v1 → v2 summary migration; ordinary runtime has no v1 decoder | End of the first-major upgrade window after 2.1.x |
| COMPAT-011 | Contributor tooling maintainers | 2026-10-27 | [`MYPY_FLAGS`](../../Makefile), the explicit Termux profile in [setup](setup.md), [`check_dev_tools.py`](../../scripts/check_dev_tools.py) | The pinned ast-serialize has an Android artifact or a provisioned Rust build; delete the local parser override recipe | Recheck when changing the mypy pin |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.
