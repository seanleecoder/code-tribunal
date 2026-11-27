# Temporary compatibility paths

This is the current register of migration-only behavior. New migration aliases,
schema decoders, retired environment-variable tombstones, duplicated old/new
paths, and temporary feature flags require a row here and a nearby code comment
naming the ID. The reasoning for each path lives next to the code; a row records
who owns it, when it goes, and where to find it.

| ID | Owner | Introduced | Code references | Removal condition | Target release/issue |
|---|---|---|---|---|---|
| COMPAT-014 | State maintainers | 2026-11-27, COMPAT-009 closure | `memory._V1_STATE_ERROR`, used by `memory.decode_state_note_body` for a v1 marker and by `memory.load_state_from_notes` for the 2.1.x two-note layout | No reopenable change request still holds a v1 ledger: the closed demo PRs listed in [consumer projects](../evidence/CONSUMER-PROJECTS.md#resting-deployment-adoption-310-2026-11-15) are converted or retired. Then delete the v1 message and the two-note branch; v1 gets the ordinary unsupported-version error | Recheck at each release (decided 2026-11-27) |
| COMPAT-011 | Contributor tooling maintainers | 2026-10-27 | [`MYPY_FLAGS`](../../Makefile) and the explicit Termux profile in [setup](setup.md) | The pinned ast-serialize has an Android artifact or a provisioned Rust build; delete the local parser override recipe | Recheck when changing the mypy pin |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.
