# Live-evidence runbook

The [Candidate Canary](../../CONTRIBUTING.md#candidate-canary) is the release
assurance: it verifies image identity and provenance, then runs the real panel, the
mock lifecycle and, when the trust boundary changes, the hostile-MR probe. Which
campaigns and extra checks a release needs is the
[release pull-request checklist](../development/release-process.md#release-pull-request-checklist).
This page holds the operator mechanics around that: the live checks the canary does
not automate, how to diagnose a failed campaign, and the coverage gaps each release
carries forward.

Spend real tokens only on what genuinely requires a live model or a live platform;
see [live versus regression coverage](README.md#live-versus-regression-coverage).
Reuse the demo consumers described in
[`CONSUMER-PROJECTS.md`](CONSUMER-PROJECTS.md), and run `make demo-preflight` before
and after any campaign.

## Diagnosing a failed campaign

Start from the failed job's log and the campaign's summary artifact
(`candidate-canary-<platform>-summary`, `candidate-canary-<platform>-lifecycle-summary`
or `candidate-canary-gitlab-hostile-summary`). The summaries record each passed
step's observations and the failing step's error.

- **`panel`:** open the demo run that holds the failing job's log and artifacts. The
  summary's `external_run_url` names it only when the demo run succeeded and
  validation failed; when the demo run itself failed it reads `unavailable`, so open
  the summary's `change_url` and take the run from that pull request or merge
  request (on GitLab, the downstream child pipeline). Never re-dispatch `panel` to
  diagnose; it spends a real two-platform panel that a release cannot cite.
- **`lifecycle` or `hostile`:** read the failing step's error in the summary.
  Re-dispatch the campaign, which spends no tokens, only to confirm a concrete fix,
  as [CONTRIBUTING](../../CONTRIBUTING.md#candidate-canary) requires. Drive a step
  by hand with the [manual lifecycle chain](#manual-lifecycle-chain) or the
  [hostile-MR checklist](#gitlab-hostile-mr-checklist) only to probe it
  interactively.

A run without both `panel` and `lifecycle` is diagnostic only. The run a release cites
must be a fresh, complete one that includes `panel` and `lifecycle`
(`check_release.py` rejects anything less), plus `hostile` when the
[release checklist](../development/release-process.md#release-pull-request-checklist)
requires it.

For a `verify-candidate` failure, start from the specific error
[`validate_candidate_identity.py`](../../scripts/validate_candidate_identity.py)
logged. It checks both digests and OCI revision labels, then verifies each
provenance attestation against `refs/heads/main`, source digest `R`, and the
publication workflow's signer identity. Input errors, such as format, tag, or a
source not reachable from `main`, reproduce without pulling anything: from a
checkout with `origin/main` fetched, run

```bash
python scripts/validate_candidate_identity.py --runtime-source "$R" \
  --base-image "$BASE_IMAGE" --reviewer-image "$REVIEWER_IMAGE" \
  --campaigns panel,lifecycle
```

Read revision labels remotely with
`docker buildx imagetools inspect --format '{{json .Image}}' <ref>`. To confirm
anonymous resolution, point `DOCKER_CONFIG` at a fresh directory containing only
`{}` and run `docker manifest inspect --verbose <ref>`; an empty `DOCKER_CONFIG`
also hides CLI plugins, which is why labels are read with the normal configuration.
Add `--verify-images`, after pulling both references by digest, only to reproduce
the local digest and attestation checks.

Image tags (`<series>-<R>`) are mutable pointers; always pull and pin by digest.

## The deterministic mock reviewer

The mock toggles, and why they must be scoped to the review and critique jobs, are
described under [cost controls](../operations.md#cost-controls) and in the
[variable reference](../configuration.md#template-and-internal-runtime-variables).
How each demo consumer enables them, and their resting state, is in
[`CONSUMER-PROJECTS.md`](CONSUMER-PROJECTS.md). A manual mock run needs all of
`AI_REVIEW_LOCAL_MOCK=1`, `AI_REVIEW_ALLOW_LOCAL_MOCK=true`, and every
`AI_REVIEW_REQUIRE_REAL_*=0`: the demos default the require-real controls to `1`,
and a seat with its control at `1` ignores the mock and calls its real CLI.
`AI_REVIEW_MOCK_SCENARIO` selects the finding set:

| Scenario | Emitted finding | Drives |
|---|---|---|
| `blocking` | one blocker/correctness finding | inline create at two independent supporters; `post` exits `0` and nothing blocks the merge |
| `blocking_alt` | same identity as `blocking`, different body | the changed-body in-place update: same discussion, new `body_hash`, nothing created |
| `advisory` | one minor/maintainability finding | the same publication outcome as `blocking`, which is the point |
| `none` | no findings | absence-based resolution of a previously posted finding (not an unchanged rerun) |
| `default` | historical `records[0]` heuristic | the local `make consensus-local` demo |

The mock anchors to the `records[0]`/`data[0]` marker when the diff contains one,
otherwise to the first added line. Give a fixture a stable marker (the shipped
`ai-review/tests/fixtures/diffs/simple.diff` has one): the first-added-line fallback
moves when a line is inserted above it, which changes the finding identity. What the
uniform mock cannot reach is listed under [carried gaps](#carried-coverage-gaps).

Mock variables are sticky. Run any real panel first, delete the mock variables
afterwards, and run `make demo-preflight`, which fails while any is still set.

## Re-driving runs and platform quirks

- **Re-drive a step without a new change request.** On GitHub, `gh run rerun <run-id>`
  re-runs the same commit as a new attempt and picks up changed repository
  variables; fetch one attempt's jobs with
  `gh api repos/<repo>/actions/runs/<id>/attempts/<n>/jobs`. On GitLab,
  `POST /projects/:id/merge_requests/:iid/pipelines` creates a fresh pipeline on the
  same head. Do not use GitLab pipeline *retry*: it re-runs only failed jobs, so it
  does not re-drive prepare through post.
- **Added-file diffs differ by platform.** GitHub renders an added file as
  `--- /dev/null`; GitLab renders `--- a/<path>`. Choose fixtures per platform.
- **Do not force-update a source branch to its base.** The pull request drops to zero
  commits, GitHub closes it, and later pushes fire no `pull_request` event. Open a
  fresh change request instead.

## Leak audit

Audit traces and artifacts with the committed scanner, not an ad-hoc grep:

```bash
python scripts/scan_evidence_leaks.py <artifact-dirs…> <trace-dirs…>
# operator-only; compares against configured secret values without exposing them in argv:
python scripts/scan_evidence_leaks.py <dirs…> --exact-value-file /path/to/secrets
```

## Checks the canary does not automate

### Body-format refresh

Confirm before the repin that a bot thread in the **previous** format, authored by a
released image, is refreshed in place. Use a
[preserved thread](CONSUMER-PROJECTS.md#threads-that-must-not-be-deleted).

1. Capture the old root note, author, creation time, finding identity and footer.
   Keep the fixture's content, anchor and reviewer identities unchanged.
2. Temporarily adopt the current trusted topology and candidate pins on that fixture
   branch, and enable the mock with the scenario that authored the old body. On
   GitLab, update the existing branch through the commits API without
   `start_branch`.
3. Reopen the change and drive one pipeline. Require a successful `post`, exactly one
   updated discussion, none created, no warnings, and the same root note, author,
   creation time and finding identity, with the new footer.
4. Close the change before restoring its CI configuration, remove the mock variables
   and temporary template branches, and keep the refreshed note and fixture branch.
   Run `make demo-preflight`.

### Effort routes and the three-seat default smoke

A green canary `panel` proves every seat succeeded in review and critique, and
consensus rejects configuration drift between stages. It does not prove which model
the provider served: a batch's `model` is copied from the configuration. The canary
clears effort overrides but not model overrides, so compare the models in its
summary artifact with the [shipped defaults](../configuration.md#shipped-defaults).
When they match, the panel is the default-route evidence. When a release changes a
default model, update the demos' model variables before dispatching the canary so
that they match.

Run a separate route panel or three-seat smoke only when the canary cannot cover the
route, such as a non-default effort; read each seat's effort from the prepare
manifest's `effective_config`. For that run:

- Select the roster only in that run's temporary workflow or template; keep the
  demos' resting roster at four seats.
- Leave model and effort overrides missing or blank so the shipped defaults apply,
  except for the route under test. For a model that requires absent effort, see
  [effort inheritance and clearing](../configuration.md#effort-inheritance-and-clearing).
- Keep `AI_REVIEW_LOCAL_MOCK=0` and every `AI_REVIEW_REQUIRE_REAL_*=1`.
- Require successful review and critique on every seat, schema-valid structured
  output, `panel_status: full`, and a posted finding.
- Confirm from the CLI's output or the provider's metadata (for example, the
  OpenRouter activity log) that the requested model was served. A silently
  substituted model does not pass.

Reuse a passing run on the same runtime and image pair rather than repeating it.

### Cursor model-specific check

Optional; Cursor is already a supported opt-in seat. Run it when a deployment needs
evidence for one specific Cursor model, or before adding Cursor to the default
roster.

1. Resolve the exact selector with `cursor-agent --list-models` on the pinned CLI,
   and set it through `AI_REVIEW_CURSOR_MODEL`. `auto` is supported for ordinary
   operation but proves nothing about a particular model. Cursor has no separate
   effort variable; reasoning depth is part of the selector, such as
   `grok-4.7-xhigh`.
2. Run a real review and critique against the candidate pair and confirm the
   selector reached the CLI with no silent fallback.

This proves the route, not the deny policy; see [carried gaps](#carried-coverage-gaps).

### Manual lifecycle chain

The canary's `lifecycle` campaign automates this on both platforms. Use one change
request and one mock finding identity, and capture run, job and platform object IDs
at every step:

1. create (`blocking`): one inline discussion at the mapped line, a successful
   `post`, and a change request that stays mergeable as far as Code Tribunal is
   concerned;
2. rerun unchanged: same discussion, `updated_discussions=0`, `skipped_unchanged>=1`;
3. change body (`blocking_alt`): the same discussion updated in place,
   `updated_discussions=1`, a new `body_hash`, nothing created;
4. resolve with `/ai-review wontfix` (or `/ai-review resolve`, or the platform's
   resolve API), then rerun `blocking`: `resolved_discussions>=1`, and the
   disposition persists on a further unchanged rerun;
5. reopen through the platform API or `/ai-review reopen`, then rerun `blocking`:
   the same discussion is active again;
6. (GitHub) push a new head mid-run: `post` reports `status: stale_head`, writes
   nothing, and exits 0.

### GitLab hostile-MR checklist

The canary's `hostile` campaign automates these probes on the demo, except the
exact-value leak scan in probe 3. Run them by hand before a production rollout on
your own GitLab deployment, or for a fork-based variant.

1. Open a merge request from an **unprotected** source branch or fork, and confirm
   through the branches API that its effective `protected` flag is false, including
   wildcard rules. Protected `OPENROUTER_API_KEY` and `GITLAB_TOKEN` must be
   withheld, prepare must fail closed with every later stage skipped, and no input
   file may be uploaded. Accept an empty archive or one with only empty directories
   under `inputs/`; any file, symlink, unexpected path or corrupt archive fails the
   probe.
2. From a trusted checkout, audit the composition:
   `python scripts/pipeline_trust.py <consumer .gitlab-ci.yml> --mode <direct|child> --template-project <org/template> --template-sha <sha>`.
   For the deprecated child mode's requirements, see
   [hardened child](../getting-started/gitlab.md#deprecated-hardened-child).
3. Attempt template or job replacement, trusted image and configuration overrides,
   and forged `out/consensus/*` or `out/post/*` artifacts. Confirm the trusted
   composition is retained or the pipeline fails closed, and run the
   [leak scanner](#leak-audit) with `--exact-value-file`
   over every trace and artifact.

## Carried coverage gaps

Live-coverage paths no run has exercised yet. The release pull-request checklist's
**Known limitations** item keeps this table current. Claim limits that no live run
can close, such as trusted-image enforcement and protected-ref insiders, belong to
the [security model](../SECURITY_MODEL.md).

| Gap | Why it is unproven | How to close it |
|---|---|---|
| GitLab fork-based merge request | the hostile probe uses an unprotected in-project branch | run the [hostile-MR checklist](#gitlab-hostile-mr-checklist) from a fork |
| Cursor deny policy | a review success says nothing about the pinned CLI honouring the `Shell(*)` and write denies at runtime; only delivery of the policy is tested (see [SUPPLY_CHAIN.md](../../ai-review/images/SUPPLY_CHAIN.md)) | a hostile permission-denial prompt against the pinned CLI; required before Cursor joins the default roster |
| Paths the uniform mock cannot reach: below-quorum FYI, the inline-unmappable summary fallback, a noise-demoted FYI entry (SPEC-67) | the mock emits identical, always-anchorable findings on every seat and no critiques | per-seat, unmappable-anchor and noise-voting mock scenarios; regression tests cover all three meanwhile |
| Confidence-related finding loss | the `0.0` default is unit-tested only; since 2.1.0 the summary's run details post each job's dropped and cap-omitted counts (SPEC-66), but no live run has produced a loss | a mock scenario whose seat omits `confidence` |
| Platform-visible re-anchoring of a moved comment | updating a comment rewrites its body, not its diff position; the internal remap is regression-covered | an optional live push that moves the anchored line |
| Long bodies subject to truncation | no refresh has exercised a truncated body | a fixture with an oversized finding body |
| GitLab body-format refresh | every refresh so far, including 2.1.0's, used the GitHub thread; the preserved GitLab note `3601861614` on MR `!11` ([consumer projects](CONSUMER-PROJECTS.md)) still carries the 1.0.0 format | run the [body-format refresh](#body-format-refresh) on that note |
| Live symlink containment variant | the GitLab commits API cannot create a `120000` tree entry | reuse the existing `evidence/p0-symlink-*` branches; at most one variant is needed |
| Non-default effort routes | only the shipped defaults have live evidence on the current pair | a route panel when a release changes or claims non-default efforts |
