# AI Review

Phase 0 plus local Phase 1 scaffolding for the v1.1 multi-agent consensus review spec in
`../specs/ai-review-implementation-ready-spec.md`.

## Local Phase 0 Harness

Run the deterministic local Claude adapter against the fixture diff:

```sh
make review-local REVIEWER=claude \
  DIFF=ai-review/tests/fixtures/diffs/simple.diff \
  REPO=ai-review/tests/fixtures/repos/simple
```

Validate the generated finding artifact:

```sh
make validate-local
```

Run local consensus validation:

```sh
make consensus-local
```

Run tests and lint checks:

```sh
make test
make lint
```

The local harness writes only under `.ai-review-local/` unless `LOCAL_OUT` is
overridden. Provider CLIs are not required for Phase 0 validation; the adapter
uses a deterministic local reviewer when `AI_REVIEW_LOCAL_MOCK=1`.

The GitLab CI template includes the v1.1 `post` and `gate` stages. Track
private GitLab MR smoke evidence and Phase 1 acceptance status in
`PHASE_1_ACCEPTANCE.md`.
