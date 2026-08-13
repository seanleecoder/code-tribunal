# Code Tribunal

[![CI](https://github.com/seanleecoder/code-tribunal/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![Container Registry](https://img.shields.io/badge/GHCR-ai--review--reviewer-blue.svg)](.github/workflows/publish-ai-review-images.yml)

Code Tribunal is a multi-model code-review pipeline for GitLab merge requests
and GitHub pull requests. Independent reviewers propose structured findings;
deterministic code groups them, applies quorum and severity policy, maintains
finding identity across revisions, posts review threads, and evaluates a merge
gate.

> **LLMs propose. Deterministic code decides.**

The supported distribution is the digest-pinned OCI images together with the
GitLab CI and GitHub Actions templates in this repository. The Python source is
internal container implementation, not a supported Python package or API.

## What is supported

- GitLab merge-request pipelines, either as a direct protected include or a
  hardened mirrored child pipeline.
- GitHub pull-request workflows for same-repository branches. External-fork
  reviews are skipped because provider secrets are deliberately withheld.
- Claude, Codex, OpenCode, and Cursor reviewers through the shipped adapters.
  All four are peer seats; `AI_REVIEW_REVIEWERS` selects which two to four of
  them form the panel, and the rest sit out.
- Deterministic consensus, persistent finding state, inline and summary
  posting, human disposition commands, and advisory or enforcing gates.

## Important limitations

- Repositories containing symlinks are rejected during snapshot preparation.
- Container/runner network egress is not enforced. Adapter endpoint validation
  reduces exposure but does not constrain a compromised reviewer CLI.
- Model output is untrusted and may be wrong. Quorum reduces single-model error;
  it does not make findings authoritative.
- A GitHub gate blocks merging only when the gate job is configured as a
  required check. GitLab requires **Pipelines must succeed**.
- Cursor is a peer reviewer seat that is off in the shipped default roster
  because it has a separate credential and its own egress path. Supplemental real
  runs close the historical real-route and
  artifact-validity subclaim, but Cursor remains disabled until the required
  SPEC-21 enablement gate passes: the exact Composer model, final-image real-key
  run, and hostile permission smoke. This does not block the 1.0.1 tag while
  Cursor remains disabled.
- **Known defect on GitHub:** a pull request that **adds or deletes a file** can
  lose findings and fail the review — anchor resolution rejects the `/dev/null`
  path GitHub uses for added files, so affected findings are dropped and the
  review can stop before the gate runs. GitLab is unaffected. Land file
  additions separately, or re-run once they merge. **Present in the shipped
  1.0.0 runtime; a fix is queued for 1.0.1 and is not released yet.** See
  [`release/1.0.0.md`](release/1.0.0.md) and
  [troubleshooting](docs/TROUBLESHOOTING.md).
- The trusted image pin is **not enforced inside the pipeline**: a consumer CI
  config can substitute the reviewer images. Containment relies on protected
  credentials being withheld from untrusted refs and on running
  `scripts/pipeline_trust.py` against your consumer config.
- The 1.0 live-evidence matrix passed against runtime source `88bc941` and its
  attested image pair, and `v1.0.0` was released. Each row is a **scoped** pass
  with its own recorded limits, and some paths remain regression-covered only.
  Release 1.0.1 uses the checked-in draft release-inputs artifact and needs new
  source/image-bound evidence — read
  [documentation history](docs/history/README.md) before making a maturity or
  security claim.

## Five-minute start

Choose the platform guide:

- [Install on GitHub](docs/getting-started/github.md)
- [Install on GitLab](docs/getting-started/gitlab.md)

Both guides cover prerequisites, least-privilege credentials, immutable pins,
the first run, verification, rollback, and uninstall.

## Local demonstration

The deterministic local path needs Python 3.12 and the development dependencies,
but no provider or platform credentials:

```bash
python3 -m pip install -r requirements-dev.txt
make consensus-local LOCAL_OUT=/tmp/code-tribunal-demo
```

The command builds a contained input bundle from fixtures, runs a mock reviewer,
computes consensus, and validates the result against the shipped schema. Output
is written below the selected `LOCAL_OUT` directory.

For contributor setup and all quality checks, see
[development setup](docs/development/setup.md).

## Documentation

| Goal | Document |
|---|---|
| Install on GitHub | [GitHub getting started](docs/getting-started/github.md) |
| Install on GitLab | [GitLab getting started](docs/getting-started/gitlab.md) |
| Configure reviewers and policy | [Configuration reference](docs/configuration.md) |
| Upgrade, observe, roll back, or respond to incidents | [Operations](docs/operations.md) |
| Diagnose a failed or quiet run | [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Understand trust boundaries and residual risks | [Security model](docs/SECURITY_MODEL.md) and [security policy](SECURITY.md) |
| Inspect CLI, artifact, and platform contracts | [Reference index](docs/reference/README.md) |
| Contribute or understand the implementation | [Development index](docs/development/README.md) |
| Find old specs and acceptance records | [History index](docs/history/README.md) |

## Pipeline at a glance

One logical DAG performs six operations:

1. `prepare` binds the diff, repository snapshot, state, configuration, and
   revision metadata into an input bundle.
2. `review` runs enabled reviewers independently and validates their findings.
3. `critique` optionally asks reviewers to assess anonymized peer findings.
4. `consensus` validates cross-stage integrity, groups findings, applies quorum,
   and decides which findings may block.
5. `post` reconciles prior state and upserts GitLab discussions or GitHub review
   comments.
6. `gate` fails on operational posting/state loss and, when enabled, unresolved
   blocking findings.

See [architecture](docs/development/architecture.md),
[consensus](docs/reference/consensus.md), and
[failure behavior](docs/operations.md#failure-behavior) for the full contract.

## Security reporting

Do not disclose credentials or sensitive model content in a public issue. Follow
the private reporting process in [SECURITY.md](SECURITY.md).

## License

Code Tribunal is licensed under the terms in [LICENSE](LICENSE).
