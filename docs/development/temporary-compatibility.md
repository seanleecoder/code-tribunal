# Temporary compatibility paths

This is the current register of migration-only behavior. New migration aliases,
schema decoders, retired environment-variable tombstones, duplicated old/new
paths, and temporary feature flags require a row here and a nearby code comment
naming the ID. The reasoning for each path lives next to the code; a row records
who owns it, when it goes, and where to find it.

| ID | Owner | Introduced | Code references | Removal condition | Target release/issue |
|---|---|---|---|---|---|
| COMPAT-009 | State maintainers | S14, state v2 | [`scripts/migrate_state_v2.py`](../../scripts/migrate_state_v2.py), an offline v1 decoder only; `memory.decode_state_note_body` redirects unmigrated v1 ledgers to it | Every supported deployment has completed the explicit v1 → v2 summary migration; ordinary runtime has no v1 decoder | 3.0.0 opens the upgrade window; explicit maintainer closure after supported v1 deployments migrate |
| COMPAT-010 | Release maintainers | N03, reusable GitHub workflow | [`scripts/check_release.py`](../../scripts/check_release.py), historical canonical-template/copy reads and byte comparison only | Publication no longer retries releases from the pre-N03 template/copy era | End of publication retry support for 2.1.x |
| COMPAT-011 | Contributor tooling maintainers | 2026-10-27 | [`MYPY_FLAGS`](../../Makefile) and the explicit Termux profile in [setup](setup.md) | The pinned ast-serialize has an Android artifact or a provisioned Rust build; delete the local parser override recipe | Recheck when changing the mypy pin |

Rows are not permanent promises. When a target arrives, delete the compatibility
path or move the target explicitly with a recorded rationale.

## Next-major target audit (2026-11-01)

- COMPAT-008's target has arrived: the next-major writer omits `source=`.
  Historical markers remain permanently readable. Rollback to 2.0.3 is no longer
  supported; 2.1.x can read the shorter marker, but requires restored v1 state.
- COMPAT-009 remains: 3.0.0 opens the v1 → v2 upgrade window. Supported v1
  deployments still need the offline converter. Maintainers must explicitly
  close that window before deletion; no automatic date or online decoder is added.
- COMPAT-010 remains: publication retry support for pre-N03 releases has not
  ended. This is distinct from whether new 2.1.x patches are made.
- COMPAT-011 remains after rechecking the mypy pin: the local Android parser
  installation still requires unavailable native build prerequisites. The explicit
  developer profile is retained; supported CI continues using the native parser.
