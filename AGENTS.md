# Coding-agent guide

Keep changes inside Code Tribunal's accepted product envelope. The architectural
decision is [ADR-0003](docs/decisions/0003-product-invariants-and-complexity-envelope.md);
this file is the concise working map, not a replacement for the linked docs.

## Product invariants

- First-party reviewers are Claude, Codex, OpenCode, and Cursor, loaded only from
  the trusted registry. There are no dynamic reviewer plugins.
- The model pipeline has one review round and one critique round on GitHub and
  GitLab.
- The deterministic reducer is the only decision authority. Two independent
  reviewer identities surface an informational finding; Code Tribunal does not
  decide whether a change may merge.
- Surfaced findings become threads, with summary fallback. Persistent state is
  platform-hosted and reconciled with those threads.
- Pinned third-party CLIs are trusted to honor their documented behavior. Code
  Tribunal owns correct invocation, endpoint selection, environment scrubbing,
  and credential isolation.
- There is one canonical GitHub template and one synchronized installation copy.
  Release identity comes from `release/release-inputs.json` and source-bound
  evidence records.

Adding a new product dimension — a reviewer, model stage, platform,
state-backend family, public artifact family, plugin mechanism, decision
implementation, canonical workflow, or release authority — requires a new
accepted ADR first.

## Trust boundaries and entry points

Prepare, consensus, post, protected templates, and shipped images are trusted;
repository/PR content and model output are untrusted. Start with:

- [architecture and module ownership](docs/development/architecture.md)
- [security model](docs/SECURITY_MODEL.md)
- `python -m ai_review.input_bundle`, `python -m ai_review.adapter_runner`,
  `python -m ai_review.consensus`, and `python -m ai_review.post`
- [testing strategy](docs/development/testing.md) and
  [release process](docs/development/release-process.md)

Dependency direction is platform transport → orchestration → pure planning and
policy. Pure state, rendering, grouping, critique, and consensus-policy modules
must not import platform clients; model adapters must not post or receive
platform tokens.

## Authorities and compatibility

Use the [ADR source-of-truth map](docs/decisions/0003-product-invariants-and-complexity-envelope.md#sources-of-truth).
Do not create a parallel registry, schema, normalization seam, decision function,
workflow authority, or release ledger.

The supported public surface is narrow, and private Python seams are not part of
it: see the
[compatibility boundary](docs/development/architecture.md#compatibility-boundary).
Refactor or delete a private seam rather than preserving a wrapper for it.

Register every temporary alias, decoder, tombstone, old/new dual path, or feature
flag in [temporary compatibility](docs/development/temporary-compatibility.md),
and name its ID in a nearby code comment.

## Change and verification rules

Normal pull requests should primarily change one major boundary. If work crosses
boundaries, explain why contract, migration, and deletion cannot be staged.
Separate policy decisions from observational follow-ups. Add an abstraction only
when it immediately removes duplication or implements an ADR boundary.

Run `make quality`. Use the smallest relevant tests while iterating; consult the
[testing guide](docs/development/testing.md) for contract/golden changes and the
[release guide](docs/development/release-process.md) for release work.

When a spec is completed, update or delete its active index entry and delete the
completed spec file; git history retains it. Never keep obsolete implementation
instructions as current product documentation.
