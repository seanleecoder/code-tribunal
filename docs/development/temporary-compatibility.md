# Temporary compatibility paths

This is the current register of migration-only behavior. New migration aliases,
schema decoders, retired environment-variable tombstones, duplicated old/new
paths, and temporary feature flags require a row here and a nearby code comment
naming the ID. The reasoning for each path lives next to the code; a row records
who owns it, when it goes, and where to find it.

| ID | Owner | Introduced | Code references | Removal condition | Target release/issue |
|---|---|---|---|---|---|
| COMPAT-008 | Publication maintainers | N12 (2.1.0), for rollback to 2.0.3 | the zero `source=` attribute written by [`_inline_marker`](../../ai-review/src/ai_review/render.py) | No supported rollback target requires `source=`: 2.1.x already parses markers without it | Next major release |
| COMPAT-009 | State maintainers | S14, state v2 | [`scripts/migrate_state_v2.py`](../../scripts/migrate_state_v2.py), an offline v1 decoder only; `memory.decode_state_note_body` redirects unmigrated v1 ledgers to it | Every supported deployment has completed the explicit v1 → v2 summary migration; ordinary runtime has no v1 decoder | End of the first-major upgrade window after 2.1.x |
| COMPAT-010 | Release maintainers | N03, reusable GitHub workflow | [`scripts/check_release.py`](../../scripts/check_release.py), historical canonical-template/copy reads and byte comparison only | Publication no longer retries releases from the pre-N03 template/copy era | End of publication retry support for 2.1.x |
| COMPAT-011 | Contributor tooling maintainers | 2026-10-27 | [`MYPY_FLAGS`](../../Makefile) and the explicit Termux profile in [setup](setup.md) | The pinned ast-serialize has an Android artifact or a provisioned Rust build; delete the local parser override recipe | Recheck when changing the mypy pin |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.
