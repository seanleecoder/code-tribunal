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

## Candidate canary

The manually dispatched `Candidate Canary` workflow validates a source-bound
image pair against both public demo consumers before promotion or repinning:

| Platform | Demo consumer | Template source |
|---|---|---|
| GitHub | `seanleecoder/code-tribunal-demo` | protected `main` in this repository |
| GitLab | `seanleecoder/code-tribunal-demo`, project `84667714` | `seanleecoder/code-tribunal-ci-template`, project `84667707` |

Dispatch it from protected `main` with a full `runtime_source` commit and the
digest-pinned base and reviewer image references. The workflow rejects a source
that is not reachable from protected `main`, verifies both digests, OCI revision
labels, and provenance, and runs orchestration only from the protected checkout.
The candidate source is exercised only as an isolated same-repository demo
branch. The GitLab source branch is protected before its merge request opens.

Configure the `candidate-canary` GitHub environment with a required reviewer and
a deployment-branch policy that admits only `main`, then add two secrets:

- `CANDIDATE_CANARY_GITHUB_TOKEN`: a fine-grained personal access token whose
  repository access is only the GitHub demo, with read/write Actions, Contents,
  Pull requests, and Workflows permissions. The orchestrator authenticates its
  demo-branch push with this token through `gh`'s git credential helper.
- `CANDIDATE_CANARY_GITLAB_TOKEN`: a GitLab personal access token with `api`
  scope. It must reach both the demo and the template project, and GitLab cannot
  restrict a personal token to named projects, so it reaches every project its
  owner can.

Give both tokens an expiry of at most one year and rotate them before it lapses;
an expired token fails the campaign at its first demo call.

Provider credentials stay in the consumers, not in the orchestrator; both demo
consumers must provide `OPENROUTER_API_KEY` and `CURSOR_API_KEY` to their trusted review templates.
The GitHub demo must also define the repository variable `AI_REVIEW_REVIEWERS`
with a roster that includes `cursor`; the canonical workflow consults that
trusted variable before exposing `CURSOR_API_KEY` to the Cursor seat. GitLab has
no equivalent credential gate.

One dispatch runs exactly one GitHub and one GitLab campaign. Each enables
Claude, Codex, OpenCode, and Cursor with shipped default effort, one review and
one critique per seat. A pass requires all eight stage results on each platform,
four resolution-eligible review seats, a full panel, a successfully posted
thread, and successful cleanup. Only redacted summaries are retained; model
bodies and credentials are never uploaded by the orchestration workflow. The
demo PR and MR are closed and temporary branches removed after diagnostics are
collected; their closed discussions and external run URLs remain available.

Do not rerun a green campaign for extra evidence. A failed campaign may be rerun
only after its diagnostics produce a concrete fix. This canary is a candidate
promotion gate, not an ordinary pull-request gate.

## Compatibility boundary

The supported public surface, and the private seams that are deliberately not
compatibility surfaces, are defined in the
[architecture guide](docs/development/architecture.md#compatibility-boundary).
Do not add a wrapper to preserve a private seam.
