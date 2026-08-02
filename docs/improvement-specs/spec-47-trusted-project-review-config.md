# SPEC-47 — Trusted per-project review configuration

- **Status:** Proposed (post-1.0; not a current product feature).
- **Severity:** High (an MR-controlled configuration must not steer a secret-bearing review).
- **Effort:** L.
- **Depends on:** the shipped revision-bound input bundle and effective-config binding (SPEC-31–35 history), plus the pinned runtime distribution contract. It does not replace SPEC-43's in-pipeline trusted-image work.
- **Enables:** [SPEC-48](spec-48-auditable-review-scope-exclusions.md) is the first planned consumer of this trusted project-policy channel.

## Rationale

Code Tribunal deliberately runs reviewer adapters, prompts, rules, and credential
handling from the pinned image rather than the change request checkout. That is the
right default: a contributor must not be able to change the model, adapter command,
or credential path in the same change that receives protected credentials.

It also means an adopter currently cannot deliberately set a repository-specific
limit, model policy, or review-scope policy without asking the image publisher for a
new image. Copying a consumer's checked-out review.yaml into prepare would solve the
convenience problem by reopening the security problem: the PR/MR head could change
the configuration that controls a secret-bearing process.

This specification adds one narrow authority boundary. A project maintainer may
commit a complete policy file at ai-review/config/review.yaml, but a PR/MR run reads
that file only from the immutable target/base revision selected for that run. The
change under review can add or edit the file; its edit becomes effective only after it
is merged and is therefore eligible for the next change request. The image continues
to own executable identity and all secret-bearing plumbing.

The result must be evidence, not an ambient convention. Prepare records which source
was selected, materializes one resolved policy artifact, and binds every later stage
to the resulting effective policy. A later job that sees a different environment or
config artifact must fail before it spends provider credentials or changes review
state.

## Scope and non-goals

**In scope**

- A complete, optional project policy at the fixed repository path
  ai-review/config/review.yaml.
- GitHub and GitLab retrieval from the immutable target/base SHA, including
  revalidation of the PR/MR revision around retrieval.
- Strict policy ownership: project policy may alter review policy, models, limits,
  and future review-scope settings, while the image retains reviewer identity,
  adapters, credential mappings, prompts, rules, and runtime paths.
- A resolved inputs/config.review.yaml artifact, config-source provenance, and
  effective-config binding for prepare, review, critique, consensus, post, and gate.
- Continued support for the existing documented policy environment overrides, subject
  to a uniform cross-stage visibility and digest check.
- Schemas, templates, tests, configuration/reference documentation, and supply-chain
  rollout work needed to make that boundary observable.

**Out of scope**

- Loading any executable, prompt, rule, adapter, binary, container image, workflow,
  or credential declaration from the consumer repository.
- A partial overlay file, a path-selecting environment variable, a local include, or
  a second configuration source. The project file is complete or it is absent.
- Reading project policy from a PR/MR head, a source fork, a moving branch name,
  CI_COMMIT_SHA, or the checkout merely because that path happens to exist there.
- Changing how the templates establish image trust. This specification assumes the
  pinned runtime-root contract and must be read alongside SPEC-43 before claiming
  that a substituted image is impossible.
- Making ordinary push, schedule, or arbitrary-ref jobs a project-config delivery
  path. This feature is for the existing PR/MR review flow. The explicit local
  fixture command remains a developer-controlled local interface, not a
  secret-bearing CI exception.
- Defining generated/lockfile exclusions themselves. That policy and its coverage
  semantics are specified by SPEC-48 and must not ship first.

## Exact contract

### 1. Fixed source and immutable revision

The only project-policy location is the exact, repository-relative regular-file path
ai-review/config/review.yaml. It is not configurable by an environment variable,
workflow input, YAML include, symlink, or repository metadata. Prepare obtains its
bytes through the platform API; it does not read the file from the checked-out head
tree or from repo_snapshot.

Prepare first resolves the same immutable review revision it uses for the diff:

| Platform | Project whose file is read | Immutable config revision |
| --- | --- | --- |
| GitHub pull request | The target repository, never the head repository/fork | PR base.sha |
| GitLab merge request | CI_PROJECT_ID, never CI_MERGE_REQUEST_SOURCE_PROJECT_ID | The selected MR version's base_sha |

For GitLab, base_sha means the base/target SHA from the selected immutable
MergeRequestVersion. It is not the mutable target branch name, start_sha, head_sha,
or the child pipeline checkout SHA. The normal GitLab version tuple
(base_sha, start_sha, head_sha) must be identical before and after all config and
diff retrieval. GitHub must similarly re-read PR metadata and prove the same
(base.sha, head.sha) pair before finalizing the bundle. A changed revision aborts the
run; prepare may not combine a policy from one revision with a diff or snapshot from
another.

The platform adapters provide one explicit operation, conceptually:

~~~text
fetch_file_at_revision(target_project, "ai-review/config/review.yaml", base_sha)
  -> absent | UTF-8 bytes | typed retrieval error
~~~

The GitHub implementation reads target-repository content at ref=base_sha. The
GitLab implementation reads the repository-file raw endpoint at ref=base_sha with
the path percent encoded. Both implementations first prove that the authenticated
token can read the resolved target revision. A 404 is interpreted as absent only
after that capability proof and only for the requested file. A platform that
intentionally obscures authorization as a 404 must report an ambiguous or
unauthorized read failure, not silently select the image fallback.

The selected project bytes must be valid UTF-8, contain no NUL, and fit the bounded
configuration reader. The implementation documents the byte ceiling next to the
reader and tests it; it is a denial-of-service bound, not a policy knob. The
immutable in-image baseline is always read from
AI_REVIEW_TRUSTED_ROOT/config/review.yaml. The trusted root is template/image-owned;
a project file cannot redirect it.

### 2. Complete file, fallback, and ownership registry

There are exactly two source outcomes:

1. A proved-absent project file selects the complete in-image baseline.
2. A present project file must be a complete review_config.v1 document and must pass
   all structural, policy, and sealed-field validation. It then selects the project
   source.

There is no per-key fallback and no merge of a partial project document into the
image. A missing required field, a YAML syntax error, an alias/type error, or an
unknown key is invalid rather than an invitation to fill that field from the image.
This avoids a policy that looks accepted while silently inheriting security-sensitive
defaults the maintainer did not review.

The loader owns an explicit field-ownership registry. Today, the following invariants
are sealed to the baseline:

| Baseline-owned value | Required project-file rule | Why |
| --- | --- | --- |
| schema_version | Must be the supported exact schema version. | A project cannot opt into a looser parser. |
| Reviewer identity | The reviewer key set must equal the baseline key set exactly; no addition, deletion, rename, or alias. | Fan-out, consensus expectations, and credential exposure stay image-owned. |
| reviewers.<name>.adapter | Must equal the baseline byte-for-byte after config normalization. | A policy file cannot select a command or executable. |
| reviewers.<name>.credential_variable | Must equal the baseline. | A project cannot remap provider credentials or induce a different secret to be passed. |
| Adapter resolution | No project field may name an adapter path; all adapter scripts and binaries resolve below the immutable trusted runtime root. | inputs/config.review.yaml must never make its parent directory executable authority. |
| Prompts and rules | No project field may name or replace them; prepare copies only trusted-root prompts and rules. | Target-branch policy is data, not instruction authority. |
| Runtime/image paths and provider endpoint mapping | No project field or supported override can change trusted root, image identity, adapter environment allowlists, or credential-to-provider mapping. | These are part of the secret-bearing runtime boundary. |

Every other admitted configuration field is policy-owned, subject to the normal
strict validator and cross-field checks. In particular, a project may set
reviewer enabled/model/effort/timeout/max-findings policy; panel, severity, critique,
posting, gate, state, limits, and security policy; and the review_scope field
introduced by SPEC-48. A policy value that is incompatible with the selected platform
still fails normal validation. This permission does not make a provider endpoint,
adapter, or credential variable project-controlled.

Future config keys are sealed by default. A release that adds a key must place it in
the ownership registry, document whether it is policy-owned or baseline-owned, add
comparison coverage, and include the resulting value in the effective-config
summary if it can change a run. It must not become project-overridable merely because
the generic YAML parser accepts it.

### 3. Resolved-policy artifact and provenance

Prepare validates the selected source without applying environment overrides, then
writes one deterministic, complete resolved source-policy document to:

~~~text
inputs/config.review.yaml
~~~

This file is an artifact, not a pointer back to the checkout. It contains the
selected image or project policy after sealed-field validation and normalization, but
before environment overlays. It has no symlinks and no executable/path references.
All later stages use this artifact as their only configuration file. They must not
fall back to /opt/ai-review/config/review.yaml, a current checkout path, or a
repository snapshot path.

The manifest retains the existing config_sha256 as the digest of the resolved
artifact and adds an additive config_provenance object with at least:

~~~json
{
  "schema_version": "config_provenance.v1",
  "source": "image",
  "requested_path": "ai-review/config/review.yaml",
  "target_base_sha": "<immutable base SHA>",
  "image_config_sha256": "<sha256>",
  "project_config_sha256": "<sha256 or null when absent>",
  "resolved_config_sha256": "<sha256>"
}
~~~

The source field is the exact enum image or project. The hashes cover bytes, while
the effective-config digest covers consequential normalized policy. This distinction
preserves source provenance even when two equivalent YAML spellings normalize to the
same policy. The manifest also continues
to bind the checked-out head snapshot and the complete selected diff to their own
revision fields; project policy provenance does not replace those bindings.

The effective_config summary and its SHA-256 must grow to include:

- the resolved-policy digest and source/provenance identity;
- every policy value that can alter prepare, model execution, consensus, posting,
  gate behavior, or review coverage, including limits and review_scope;
- the exact supported policy-override keys present in the environment and their
  non-secret effective values.

Credential values, access tokens, and arbitrary environment variables are never
part of that summary. Only the closed, existing policy override set is eligible.

### 4. Environment overrides remain a cross-stage contract

Existing documented AI_REVIEW policy overrides remain supported. They are applied
after loading inputs/config.review.yaml in every stage, exactly as they are today.
They do not bypass sealed-field comparison and cannot select a config path, adapter,
credential variable, prompt, rule, trusted root, image, or provider endpoint.

Prepare records the canonical effective summary, digest, and explicit supported
override map. Review, critique, consensus, post, and gate each reconstruct the
effective policy from the bundle artifact and their own environment, then validate it
against that record before doing consequential work. The templates must therefore
scope supported overrides uniformly to every job in the DAG: workflow-level on
GitHub and the protected child-pipeline scope on GitLab, never an individual reviewer
or post job.

The check is intentionally stronger than comparing a model string after a model call:

- review and critique validate before rendering a prompt or spawning an adapter;
- consensus validates before reading reviewer results into a decision;
- post validates before creating a platform client, loading/planning state, or
  writing a comment;
- gate validates before evaluating a result.

A missing, added, or changed supported override in a later job is an
effective-config mismatch even if a coincidental default would otherwise yield a
similar-looking configuration. This makes the deployment/scoping error visible and
prevents a secret-bearing stage from silently running a different policy.

### 5. Trusted runtime-root handling

Moving the policy artifact into inputs must not make config-relative resolution an
escape hatch. The implementation separates policy loading from runtime asset lookup:

- adapter_runner resolves an already baseline-verified adapter identity under the
  immutable trusted root, not relative to inputs/config.review.yaml;
- prompt_render reads the trusted prompt copy prepared from the image; it never
  accepts a project prompt path;
- prepare copies only trusted rules and prompts, and repo_snapshot remains
  untrusted review data;
- all command construction and credential allowlists remain in trusted image code.

The templates may set AI_REVIEW_CONFIG to inputs/config.review.yaml after prepare,
but must retain a separate immutable trusted-root value for runtime assets. A
consumer cannot supply either value through its project policy. This distinction is
also why the feature needs a newly published, immutable image/template pair rather
than a consumer-side copied YAML file.

## Failure behavior

Prepare builds the bundle in a temporary staging directory and publishes inputs only
after config selection, revision revalidation, diff collection, snapshot containment,
and manifest construction all succeed. An error produces no usable manifest or
partially trusted bundle for a downstream job to consume.

| Condition | Required result |
| --- | --- |
| Project file is proved absent at the immutable target/base SHA | Select image policy; record source=image; continue normally. |
| Project file is malformed, incomplete, too large, invalid UTF-8, has unknown keys, or fails policy validation | Fail prepare with project_config_invalid; do not fall back. |
| Project file changes reviewer names, adapter identity/path, credential mapping, prompt/rule source, or any other sealed value | Fail prepare with project_config_unauthorized; do not fall back. |
| Token/read capability is denied, a concealed/ambiguous 404 cannot be proved absent, or retrieval returns any non-404/platform/network error | Fail prepare with project_config_fetch_failed; do not fall back. |
| PR/MR revision changes while config or diff is collected | Fail prepare with project_config_revision_mismatch; publish no bundle. |
| The project file is newly added or changed only on the PR/MR head | Ignore that head copy; use the base version or baseline fallback. |
| Resolved config artifact, source provenance, environment override map, or effective digest differs in a later stage | Fail that stage with effective_config_mismatch before model invocation or platform/state mutation. |
| A historical bundle lacks the new provenance/binding fields | Refuse it as an incompatible input; require a fresh prepare with this release. |

The errors above are terminal prepare/configuration errors, not reviewer
degradation. Templates must not retry consensus, post, or gate against a missing
manifest. Reviewer allow_failure remains relevant only to a valid, already-bound
input bundle and cannot convert a config-selection failure into an incomplete review.

## Implementation seams

| Seam | Required change |
| --- | --- |
| ai-review/src/ai_review/config.py | Add the source-policy loader, ownership registry, sealed-field comparison, deterministic resolved-policy writer, and expanded effective-config summary/digest. Keep strict YAML validation and make future-key ownership explicit. |
| ai-review/src/ai_review/input_bundle.py | Resolve the immutable PR/MR version first; obtain the project bytes from the platform; stage and atomically publish the resolved artifact, provenance, diff, snapshot, and manifest. Apply environment overlays only when calculating effective policy, not when deciding source ownership. |
| ai-review/src/ai_review/platform/base.py, platform/github.py, platform/gitlab.py, and gitlab_client.py | Add typed target-revision file retrieval and read-capability proof. Keep GitHub comparison diffs and GitLab complete-diff recovery independent of config retrieval. |
| Snapshot copying | Preserve existing no-follow containment. The copied policy must never originate from repo_snapshot or a project symlink; SPEC-48 later adds scope-aware snapshot filtering. |
| adapter_runner.py and prompt_render.py | Load only the bundle policy after prepare, validate the binding before prompt/provider work, and resolve runtime assets from the trusted root rather than config.parent. |
| consensus.py, post.py, and gate.py | Validate the same artifact/provenance/effective binding before consuming findings, platform state, comments, or gate inputs. Retain the existing successful-batch config-digest checks as defense in depth. |
| Schemas and types | Add an input-manifest schema/type or equivalent strict validator for config_provenance and the expanded effective summary. Extend artifact provenance only additively where historical read compatibility is safe. |
| GitHub and GitLab templates | Prepare begins with the baked baseline path; later jobs use inputs/config.review.yaml and share the same allowed policy overrides. Ensure prepare failure prevents every downstream consumer of inputs. |
| Configuration, artifact, security, and installation docs | Document the fixed target-branch path, source precedence, sealed fields, migration, missing-file fallback, and the fact that this remains proposed until released. Update the artifact reference when implementation lands. |
| Images and supply chain | Ship loader, schemas, prompt/adapter path hardening, and templates in one immutable runtime revision. Publish/pin the new base and reviewer images together, update trusted-root/image attestations, and retain SPEC-43 as the separate proof that the pipeline actually used those images. |

## Tests

Add focused unit, contract, template, and platform-harness coverage. At minimum:

- GitHub and GitLab prepare read ai-review/config/review.yaml from the selected
  base SHA of the target project, including a moving PR/MR revision rejection.
- A valid base config is used when a PR/MR head adds a malicious config, changes a
  model, or attempts to add its own config path; no head content reaches the selected
  resolved policy.
- A genuine target-revision 404 falls back to the image config and records
  image_or_project=image. A denied, ambiguous, malformed, oversized, and 500/timeout
  read each fail closed rather than taking that fallback.
- A complete valid project config can alter permitted model, panel, limit, and policy
  fields; a partial file is rejected rather than merged.
- Attempts to add/remove/rename reviewers, substitute an adapter, change a
  credential_variable, make an adapter path relative to inputs, or redirect prompts,
  rules, trusted root, or provider mapping fail before a bundle exists.
- Baseline and project byte hashes, resolved-policy hash, and source provenance are
  deterministic and validate through the manifest reader/schema.
- Each supported environment override works when visible identically to prepare,
  reviewer, critique, consensus, post, and gate; a missing/changed/extra later-stage
  override fails before provider invocation and before post/state mutation.
- Tampering with inputs/config.review.yaml or config provenance after prepare causes
  a binding failure. A successful finding/critique batch whose digest differs remains
  rejected by consensus.
- Adapter and prompt lookup tests prove that a project config and its artifact parent
  cannot supply a shell script, prompt, rule, or credential mapping.
- Existing image-only consumers and existing deterministic local fixtures continue
  to produce their expected effective policy when no project file exists.

## Acceptance criteria

- A PR/MR whose head changes ai-review/config/review.yaml cannot change the
  configuration, adapter identity, credential mapping, prompt, rule, or runtime
  asset used by that run; the manifest proves the selected base SHA and source.
- A maintainer-reviewed complete config at the target/base SHA changes only
  policy-owned behavior on the next run and produces a deterministic
  inputs/config.review.yaml plus complete config provenance.
- Missing is the sole fallback condition. Invalid, unauthorized, ambiguous, and
  non-404 retrieval outcomes fail prepare before a usable input bundle, model call,
  or downstream mutation.
- Every later stage loads the bundle artifact and proves an identical effective
  policy before its consequential work. Cross-job override drift fails loudly.
- No project-supplied value can select reviewer identities, adapters, credential
  variables, prompts, rules, trusted runtime paths, images, or provider mappings.
- The GitHub and GitLab controlled integration tests prove the same source-selection
  and isolation semantics, and the image/template pair used for those tests is
  immutable and recorded.
- SPEC-48 is not enabled until these acceptance criteria are met; it has no
  alternate configuration channel.

## Rollout and compatibility

This is an additive feature with image fallback. Existing consumers that do not
create ai-review/config/review.yaml retain the image policy and behavior, with only
additive provenance fields in newly built artifacts. Historical artifacts continue
to be readable where their schemas permit it, but must not be supplied to a new stage
that requires the new binding; rerun prepare instead.

Migration is intentionally simple:

1. Start from the exact configuration in the image version pinned by the consumer.
2. Commit a complete ai-review/config/review.yaml on the protected target branch.
3. Change only policy-owned fields and review that normal change as configuration
   policy.
4. Observe the following PR/MR's manifest source/provenance and effective digest.

A first PR that adds the file is reviewed under the old base policy. That is a
security property, not a usability bug. If a project needs the new policy to review
the file that introduces it, merge the policy change separately and then open the
dependent change request.

Implementation rolls out in this order: land the loader/schema/runtime-root changes;
build and attest the matching immutable images; pin the matching GitHub and GitLab
templates; run controlled base-vs-head isolation smoke tests on both platforms; then
document the feature as current. Do not enable consumer review_scope policy during a
mixed image/template deployment. SPEC-48 can follow only after this source-selection
and binding contract is shipped and evidenced.

## Relationship to SPEC-48

SPEC-48 relies on this specification for all authority and provenance. Its
exclusions are project policy only when they arrive in the base-revision-resolved
inputs/config.review.yaml, are included in the effective-config digest, and are
reported under the same manifest provenance. A head-only exclusion, an environment
shortcut, or an unbound config artifact is not an auditable coverage decision and
must never reduce review scope.
