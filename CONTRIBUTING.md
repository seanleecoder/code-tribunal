# Contributing

Use the canonical [development setup](docs/development/setup.md) and run:

```bash
make quality
```

That command is the same blocking documentation, lint, test, type, supply-chain,
and compile gate used by CI. The internal Python source is loaded directly from
the checkout for development; it is not an installable supported distribution.

Coding agents working in this repository should start from
[`AGENTS.md`](AGENTS.md).

Pull requests should:

- Summarize the change and link the relevant finding/spec when applicable.
- Primarily change one major boundary: input preparation, adapter execution,
  critique, consensus, posting/state, platform transport, release/supply chain,
  or documentation/tooling. If a change crosses boundaries, explain why it
  cannot be staged as contract, migration, and deletion.
- Separate policy decisions in behavior-changing cleanup from observational
  follow-ups. Add an abstraction only when it immediately removes duplication or
  implements an accepted ADR boundary.
- Add or update tests for behavior and contract changes.
- Update the canonical configuration/reference entry for new runtime controls.
- Keep examples immutable and mechanically parseable.
- Avoid exposing platform/provider credentials, CLI session material, prompts,
  proprietary source, or sensitive model output in logs and fixtures.
- Give temporary dual paths a deletion condition and register them in
  [temporary compatibility](docs/development/temporary-compatibility.md).
- Delete completed specs from the active spec directory after updating its
  index; git history retains them.

New reviewer adapters must validate model/endpoint input, sanitize the child
environment, receive only their own credential, enforce the strongest available
read-only/no-shell policy, and produce schema-valid finding and critique
artifacts. Network egress limitations must be documented honestly.

Architecture, testing, and release guidance is indexed under
[docs/development/](docs/development/README.md).

## Compatibility boundary

The supported public surface, and the private seams that are deliberately not
compatibility surfaces, are defined in the
[architecture guide](docs/development/architecture.md#compatibility-boundary).
Do not add a wrapper to preserve a private seam.
