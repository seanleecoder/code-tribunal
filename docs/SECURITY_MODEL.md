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

GitHub consumers pin the reusable workflow to a reviewed release commit. Its
`github` and `vars` contexts belong to the caller, and checkout still fetches the
caller's PR revision. The caller grants token permissions and forwards provider
and resolution secrets; the reusable workflow maps each secret only to the
existing stage and seat that needs it. Caller workflow `env` is not inherited.
The called workflow retains the same-repository PR guard and rejects unsupported
events, including `pull_request_target`, before prepare. See the
[GitHub installation guide](getting-started/github.md) for caller configuration.

Executable coverage includes
[`test_adapter_runner.py`](../ai-review/tests/unit/test_adapter_runner.py),
[`test_openrouter_adapters.py`](../ai-review/tests/unit/test_openrouter_adapters.py),
and the canonical-template contract tests.

Residual risk: deployment protection settings are external state and must be
proved per deployment. The Candidate Canary's `hostile` campaign targets a merge
request from an **unprotected** ref with a direct include. Earlier releases
recorded the hardened mirrored child topology; that evidence does not prove the
new composition. The first N02 release must run the direct-include probe. Its
assertions are listed under
[Candidate Canary](../CONTRIBUTING.md#candidate-canary), and the body of each
release that ran it records the run.

It does not scan every job trace and artifact for exact credential values. So
“credential isolated” means that scope plus the tested subprocess
allowlist and template design — not a completed claim about every installation.
Fork-based merge requests and protected-ref insiders are untested.

That same probe established a limit worth stating plainly here: **the trusted
image pin is not enforced inside the pipeline.** A consumer `.gitlab-ci.yml` that
declares the image variables in its own top-level `variables:` can run the stages
on an arbitrary image, and no stage verifies what it is running. Containment
comes from credential withholding. `scripts/pipeline_trust.py` rejects reserved
job replacements but runs out-of-band; it does not audit arbitrary variable
values or every unrelated include. Do not read “digest-pinned templates” as a
runtime guarantee against a hostile consumer config.

No in-pipeline check can close this. The consumer CI file composes the pipeline,
so the edit that swaps the image can also delete a verification job, a required
audit stage, or the variable a check compares against, and a substituted image
will not run a check that lives in the trusted image. The boundary is outside
the consumer CI file: the composition must come from a project merge-request
authors cannot edit, and no author-written job may run in a pipeline that receives protected
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
credentials. Restrict pipeline variables even after protecting the CI file.

[Protect the CI composition](getting-started/gitlab.md#protect-the-ci-composition)
gives the settings and schedules `pipeline_trust.py` so that composition drift is
detected without an operator remembering to run it.

### Repository snapshot containment

GitHub automatic runs check out the event's head SHA; manual runs check out the
selected PR's head ref and pass its checked-out SHA to prepare. Checkout fetches
files without running repository code and does not persist credentials. Prepare
runs with `python -P` and the trusted image's `PYTHONPATH`, so Python cannot import
a checkout's top-level `ai_review` package before validation. It keeps the
checkout as its working directory to validate the PR number, same-repository
origin, API revision and clean checkout before collecting the diff or snapshot.
It fetches the diff by immutable base/head SHAs and rechecks the API revision and
checkout before publishing the manifest. The staged bundle is published only
after those checks pass.

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

State v2 is accepted only from the expected bot author and carries a mandatory
payload checksum inside the summary comment. Inaccessible, corrupt, duplicate,
or unmigrated state stops prepare/post. The v1 decoder exists only in the
explicit offline migration command.
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

Consensus uses current-run evidence only. Posting matches groups to the latest
authenticated state; ambiguous or already-claimed matches use summary fallback
and protect candidate threads from automatic resolution. Authorized commands
naming a candidate still apply.

Adapter status files (`out/status/`) are untrusted input to consensus. The
trusted runner writes them, but in a job that also runs model CLIs with tool
access. Consensus uses a status file only if it passes its schema, names the
seat and stage its filename names, and carries the manifest's `run_id`. Anything
else counts as absent. Status files feed only the observational `run_health`
record. Finding and critique batches remain the only decision input, so a forged
status file can mislead only the health section of the summary comment. Every
model ID, reviewer ID and error message in that section is rendered literally.
Consensus redacts error text itself before truncating it, because a rewritten
status file need not honor its `error_message_redacted` field name. Text over
8192 characters is omitted rather than cut, since a cut can split a secret past
recognition. A status file
with text that cannot be encoded as UTF-8 counts as absent. The consensus job log
prints each value on one line after renderer-owned text and breaks up GitHub's
legacy `##[` command prefix, so status text cannot run a workflow command in the
trusted job.

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
integrity, and posting unit/integration tests. Each release body records the
Candidate Canary run that proved it live, and unexercised paths are the
[carried coverage gaps](evidence/RUNBOOK.md#carried-coverage-gaps).

## Reporting

Use the private process in [SECURITY.md](../SECURITY.md). Do not open a public
issue containing exploit details, credentials, proprietary source, or model
content.
