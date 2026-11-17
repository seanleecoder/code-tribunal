# Temporary compatibility paths

This is the current register of migration-only behavior. New migration aliases,
schema decoders, retired environment-variable tombstones, duplicated old/new
paths, and temporary feature flags require a row here and a nearby code comment
naming the ID. The reasoning for each path lives next to the code; a row records
who owns it, when it goes, and where to find it.

| ID | Owner | Introduced | Code references | Removal condition | Target release/issue |
|---|---|---|---|---|---|
| COMPAT-009 | State maintainers | S14, state v2 | [`scripts/migrate_state_v2.py`](../../scripts/migrate_state_v2.py), an offline v1 decoder only; `memory.decode_state_note_body` redirects unmigrated v1 ledgers to it | One further release has shipped since both demo consumers, the only known pre-3.0 deployments, moved to 3.1.0 on 2026-11-15; ordinary runtime has no v1 decoder | Delete after the next release ships (decided 2026-11-15) |
| COMPAT-011 | Contributor tooling maintainers | 2026-10-27 | [`MYPY_FLAGS`](../../Makefile) and the explicit Termux profile in [setup](setup.md) | The pinned ast-serialize has an Android artifact or a provisioned Rust build; delete the local parser override recipe | Recheck when changing the mypy pin |
| COMPAT-012 | Adapter execution maintainers | 2026-11-17, PR #304 | [`diagnose_codex_sandbox.py`](../../scripts/diagnose_codex_sandbox.py), the `--diagnostic-read` subset in [`smoke_codex_batch_output.py`](../../scripts/smoke_codex_batch_output.py), and the failed-gate follow-up in [image CI](../../.github/workflows/publish-ai-review-images.yml) | Capture the four-way Linux evidence, then delete the diagnostic script, subset and workflow step before shipping a separately accepted launch policy | PR #304 investigation |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.
