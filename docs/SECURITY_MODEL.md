# Security model

## Assets and trust boundaries

Protected assets include provider credentials, GitHub/GitLab mutation tokens,
repository content, model prompts/output, persisted finding state, and trusted
configuration. Code Tribunal makes no merge decision, so there is no verdict for
an attacker to forge into a merge; whether a change may merge is decided by
repository policy, outside this boundary.

Merge-request/pull-request content and all model output are untrusted. Prepare,
consensus, post, the protected templates, and their container images are
trusted deterministic components. The outer CI job is trusted and can see
CI-provided variables; isolation claims apply to reviewer subprocesses, not to a
hostile replacement of the trusted job definition.

## Current mitigations and residual risks

### Trusted CI delivery and reviewer credentials

Mitigations include protected immutable GitLab includes, a protected external
CI configuration or pipeline policy for the consumer composition, consumer trust
auditing, GitHub `pull_request` execution,
digest-pinned images, reviewer-specific credential selection, scrubbed
subprocess environments, read-only/no-shell reviewer policies, model/endpoint
validation, and secret redaction.

Executable coverage includes
[`test_adapter_runner.py`](../ai-review/tests/unit/test_adapter_runner.py),
[`test_openrouter_adapters.py`](../ai-review/tests/unit/test_openrouter_adapters.py),
and the canonical-template contract tests.

Residual risk: deployment protection settings are external state and must be
proved per deployment. The Candidate Canary's `hostile` campaign proves one scoped
topology: a merge request from an **unprotected** ref in the hardened mirrored
child. It runs whenever a release changes the trust boundary, and that release's
body records the run. It asserts four things:

- GitLab withholds every protected credential: the hostile job sees each one as
  absent.
- Prepare fails closed with an empty input bundle, and every later stage is
  skipped.
- The forged publication artifacts go unconsumed.
- `scripts/pipeline_trust.py` rejects the hostile composition and accepts the
  demo's own.

It does not scan every job trace and artifact for exact credential values. So
“credential isolated” means that scope plus the tested subprocess
allowlist and template design — not a completed claim about every installation.
Fork-based merge requests and protected-ref insiders are untested.

That same probe established a limit worth stating plainly here: **the trusted
image pin is not enforced inside the pipeline.** A consumer `.gitlab-ci.yml` that
declares the image variables in its own top-level `variables:` and enables
variable forwarding can run the stages on an arbitrary image, and no stage
verifies what it is running. Containment came from credential withholding and
from `scripts/pipeline_trust.py`, which rejects such a composition but
runs out-of-band. Do not read “digest-pinned templates” as a runtime guarantee
against a hostile consumer config.

No in-pipeline check can close this. The consumer CI file composes the pipeline,
so the edit that swaps the image can also delete a verification job, a required
audit stage, or the variable a check compares against, and a substituted image
will not run a check that lives in the trusted image. The hardened child's
`inherit.variables: false` and `forward.*: false` settings hold only while
merge-request authors cannot edit the consumer CI file. The boundary is outside
that file: the composition must come from a project merge-request authors cannot
edit, and no author-written job may run in a pipeline that receives protected
credentials. A protected external CI configuration file or an
`override_project_ci` pipeline execution policy meets that bar. An
`inject_policy` policy does not, because project jobs still run beside the
injected ones.

Pipeline variables are a second route to the image pins. The template runs on
`web` and `api` pipelines whose caller supplies `CI_MERGE_REQUEST_IID`, and a
pipeline variable overrides the template's image variables. A user allowed to
run pipelines on a protected branch can therefore substitute the image without
editing any CI file, so restricting pipeline variables is part of the boundary.
The same route reaches fork content: such a pipeline may name a fork merge
request, and it carries no `CI_MERGE_REQUEST_SOURCE_PROJECT_ID`, so prepare's
fork guard does not fire and every seat reviews the fork's diff with protected
credentials. The deprecated child's `forward.pipeline_variables: false` blocked
this route.

[Protect the CI composition](getting-started/gitlab.md#protect-the-ci-composition)
gives the settings and schedules `pipeline_trust.py` so that composition drift is
detected without an operator remembering to run it.

### Repository snapshot containment

Prepare rejects symlinks and FIFO/socket/device nodes. On Linux and macOS it
uses descriptor-relative `O_NOFOLLOW`/`O_DIRECTORY` traversal and fails closed
where those primitives are unavailable. Directory depth is capped. A hostile
checkout cannot copy a symlink target such as `/proc/self/environ` into the
uploaded bundle.

Prepare also leaves out, at every depth, the files a reviewer CLI would read from
its working tree as instructions or project configuration, such as `AGENTS.md`,
`CLAUDE.md`, and each CLI's own config files and directories. The list is
`_REVIEWER_AGENT_FILE_NAMES` in
[`input_bundle.py`](../ai-review/src/ai_review/input_bundle.py). A merge request
cannot steer a reviewer through them. The diff still shows changes to these
files, but no reviewer can read their full contents. Because they are never
opened, a symlinked `CLAUDE.md` is left out instead of rejecting the snapshot.

The hostile-symlink and no-follow behavior is exercised in
[`test_input_bundle.py`](../ai-review/tests/unit/test_input_bundle.py).

Residual risk: intentionally tracked symlinks are unsupported. Hard links to
outside same-filesystem content are not explicitly checked, although ordinary
Git checkouts cannot create them.

### State authenticity and integrity

State is accepted only from the expected bot author and carries a checksum.
Post reads and updates only a summary comment the bot authored; another
participant's note carrying the summary marker is ignored with a warning.
Human commands require platform authorization. Cross-stage run/config bindings
prevent accidental evidence mixing: review, critique, consensus, and post each
verify that their effective config reproduces the manifest's
`effective_config_sha256` before consequential work, and post refuses a
consensus artifact whose `run_id`, `head_sha`, or `input_manifest_sha256` does
not match the manifest. These bindings detect misconfiguration and mixed runs;
they are not tamper protection, because whoever writes an artifact also chooses
the values it records.

Author and hostile-state behavior is covered by
[`test_state_note_authenticity.py`](../ai-review/tests/security/test_state_note_authenticity.py)
and platform contract tests.

Residual risk: the checksum is not a signature. Compromise of the bot/platform
credential or trusted post job can create apparently authentic state. Token
rotation changes GitLab bot identity and causes old state to be distrusted.

### Network egress — open risk

Adapter validation pins supported provider endpoints, but the container or
runner does not enforce a network allowlist. A compromised or misbehaving
reviewer CLI may reach other destinations. Operators needing a hard egress
boundary must enforce it at the runner/network layer. This is the open H2 risk
and must not be described as implemented container isolation.

### Forks and untrusted workflow changes

Canonical GitHub workflows skip external forks. GitLab protected variables are
withheld from unprotected refs; beyond that, a GitLab install is safe only with
the controls in
[Trusted CI delivery](#trusted-ci-delivery-and-reviewer-credentials).

## Failure policy

Failure behavior is not globally open or closed. See the executable
[failure matrix](operations.md#failure-behavior). Findings are not a failure
class: severity is an impact label and no finding causes a nonzero exit. Post and
state loss do fail the `post` job.

## Evidence

Executable security coverage lives under `ai-review/tests/security/` and in
snapshot, trust-template, adapter-environment, state-authenticity, artifact
integrity, and posting unit/integration tests. Deployment evidence is indexed under
[docs/evidence](evidence/README.md), and unexercised paths are its
[carried coverage gaps](evidence/RUNBOOK.md#carried-coverage-gaps).

## Reporting

Use the private process in [SECURITY.md](../SECURITY.md). Do not open a public
issue containing exploit details, credentials, proprietary source, or model
content.
