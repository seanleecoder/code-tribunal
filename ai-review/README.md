# AI review implementation tree

This directory contains Code Tribunal's internal container implementation and
the supported CI/template artifacts. It is a source-tree index, not a separate
operator guide. Start with the repository [README](../README.md), then use the
[GitHub](../docs/getting-started/github.md) or
[GitLab](../docs/getting-started/gitlab.md) installation guide.

The supported artifacts are:

- [`ci/review.gitlab-ci.yml`](ci/review.gitlab-ci.yml) — canonical GitLab DAG.
- [`ci/review-child.gitlab-ci.yml`](ci/review-child.gitlab-ci.yml) — hardened
  child-pipeline stage wrapper.
- [`ci/review.github-actions.yml`](ci/review.github-actions.yml) — canonical
  GitHub workflow; the installed repository copy must remain byte-identical.
- [`config/review.yaml`](config/review.yaml) — shipped `review_config.v1`
  defaults.
- [`schemas/`](schemas/) — JSON Schema contracts for stage artifacts.
- [`images/`](images/) — base and reviewer container build inputs.

Internal implementation directories:

- [`src/ai_review/`](src/ai_review/) — prepare, consensus, posting, gate, state,
  platform, and schema code.
- [`adapters/`](adapters/) — reviewer process wrappers and environment
  isolation.
- [`prompts/`](prompts/) — review and critique prompts.
- [`tests/`](tests/) — unit, contract, integration, and security tests.

The Python modules are loaded from `/opt/ai-review/src` inside the supported
containers. Direct imports from a checkout are supported only for development
and tests; there is no stable public Python API.

Further documentation:

- [Configuration](../docs/configuration.md)
- [Operations](../docs/operations.md)
- [CLI and exit codes](../docs/reference/cli-and-exit-codes.md)
- [Artifacts and schemas](../docs/reference/artifacts-and-schemas.md)
- [Development](../docs/development/README.md)
- [Historical acceptance records](../docs/history/README.md)
