# Contributing

Use the canonical [development setup](docs/development/setup.md) and run:

```bash
make quality
```

That command is the same blocking documentation, lint, test, type, supply-chain,
and compile gate used by CI. The internal Python source is loaded directly from
the checkout for development; it is not an installable supported distribution.

Pull requests should:

- Summarize the change and link the relevant finding/spec when applicable.
- Add or update tests for behavior and contract changes.
- Update the canonical configuration/reference entry for new runtime controls.
- Keep examples immutable and mechanically parseable.
- Avoid exposing platform/provider credentials, CLI session material, prompts,
  proprietary source, or sensitive model output in logs and fixtures.

New reviewer adapters must validate model/endpoint input, sanitize the child
environment, receive only their own credential, enforce the strongest available
read-only/no-shell policy, and produce schema-valid finding and critique
artifacts. Network egress limitations must be documented honestly.

Architecture, testing, and release guidance is indexed under
[docs/development/](docs/development/README.md).
