# Live-evidence runbook

The [Candidate Canary](../../CONTRIBUTING.md#candidate-canary) is the release
assurance: it verifies image identity and provenance, then runs the real panel, the
mock lifecycle and, when the trust boundary changes, the hostile-MR probe. Which
campaigns and extra checks a release needs is the
[release pull-request checklist](../development/release-process.md#release-pull-request-checklist).
This page holds the operator mechanics around that: the live checks the canary does
not automate, how to diagnose a failed campaign by hand, and the coverage gaps each
release carries forward.

No evidence records are written. The release body records the canary verdicts.
Records for releases up to 2.0.3 remain at their tags, for example
[`v2.0.3:docs/evidence/`](https://github.com/seanleecoder/code-tribunal/tree/v2.0.3/docs/evidence).

Spend real tokens only on what genuinely requires a live model or a live platform.
Most logic is proven by the regression suite inside `make quality`; see the
[evidence index](README.md#live-versus-regression-coverage).

## Consumer projects and prerequisites

Reuse the long-lived demo consumers; do not rebuild them.
[`CONSUMER-PROJECTS.md`](CONSUMER-PROJECTS.md) records their current configuration,
branch conventions, the threads that must be preserved, and the per-release setup
checklist. Run `make demo-preflight` before and after any campaign. Its branch
names and history call a real panel *Chain A* and a mock lifecycle chain *Chain B*.

- **GitLab:** every lifecycle merge request needs a **protected** source branch. The
  protected `GITLAB_TOKEN` injects only on protected refs, so from an unprotected
  branch prepare and posting fail outright. Only the hostile probe uses an
  unprotected branch, because the withholding is what it measures.
- **GitHub:** no required status check. Code Tribunal is informational; a leftover
  required `gate` entry leaves every pull request unmergeable. Copy the workflow from
  the new `R` instead of hand-editing pins, because an older copy can carry env keys
  a newer `R` rejects.

## Diagnosing a `verify-candidate` failure

The canary's `verify-candidate` job runs
[`validate_candidate_identity.py`](../../scripts/validate_candidate_identity.py). It
checks both digests and OCI revision labels, then verifies each provenance
attestation against `refs/heads/main`, source digest `R`, and the publication
workflow's signer identity. To reproduce it locally, from a checkout with
`origin/main` fetched, set `R`, `BASE_IMAGE` and `REVIEWER_IMAGE` to the full source
commit and the two digest-pinned references:

```bash
docker pull "$BASE_IMAGE" && docker pull "$REVIEWER_IMAGE" &&
  python scripts/validate_candidate_identity.py --runtime-source "$R" \
    --base-image "$BASE_IMAGE" --reviewer-image "$REVIEWER_IMAGE" --verify-images
```

To confirm anonymous resolution without stored credentials, point `DOCKER_CONFIG`
at a fresh directory containing only `{}` and run
`docker manifest inspect --verbose <ref>`. An empty `DOCKER_CONFIG` also hides CLI
plugins, so read the revision labels with the normal configuration:
`docker buildx imagetools inspect --format '{{json .Image}}' <ref>`.

Image tags (`<series>-<R>`) are mutable pointers; always pull and pin by digest.

## The deterministic mock reviewer

`AI_REVIEW_LOCAL_MOCK=1` with `AI_REVIEW_ALLOW_LOCAL_MOCK=true` makes each seat emit
a canned, schema-valid batch instead of calling a model. An adapter still falls
back to its real CLI while any `AI_REVIEW_REQUIRE_REAL_*` flag is set, so set every
one to `0`. `AI_REVIEW_MOCK_SCENARIO` selects the finding set:

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
moves when a line is inserted above it, which changes the finding identity. The mock
emits identical findings on every seat, so it cannot reach the below-quorum FYI path
or the inline-unmappable summary fallback; both are regression-covered.

These are adapter controls, outside the prepare-stamped configuration digest, so set
them on the review and critique jobs (project-wide is simplest). A
configuration-affecting override (`AI_REVIEW_CRITIQUE_ENABLED`,
`AI_REVIEW_REVIEWERS`, `AI_REVIEW_<SEAT>_MODEL/EFFORT`) must be identical on every
job, or the first later job that sees a different configuration fails closed. Never
edit a production template.

- **GitLab:** set the toggles as **project** CI/CD variables. They apply to
  push-triggered `merge_request_event` pipelines and reach a child pipeline with
  forwarding disabled. Manual "Run pipeline" variables are dropped by any
  push-triggered pipeline, which then runs real. Flip `AI_REVIEW_MOCK_SCENARIO` by
  editing the variable in place; it applies to every open merge request in the
  project.
- **GitHub:** step `env` cannot be overridden by repository variables, so the demo
  workflow maps them with safe defaults (`AI_REVIEW_LOCAL_MOCK: ${{ vars.AI_REVIEW_LOCAL_MOCK || '0' }}`,
  `AI_REVIEW_ALLOW_LOCAL_MOCK: … || 'false'`, each `AI_REVIEW_REQUIRE_REAL_*: … || '1'`,
  and `AI_REVIEW_MOCK_SCENARIO: ${{ vars.AI_REVIEW_MOCK_SCENARIO }}`). Set repository
  variables to drive the mock. Do not commit a per-scenario workflow change: a new
  commit changes the diff and the mock's anchor.

> **Do not let the mock leak into a real run.** Project and repository variables are
> sticky. Run any real panel first, or delete the mock variables before it and
> confirm they are gone. With them absent the defaults restore
> `AI_REVIEW_REQUIRE_REAL_*=1`, so a run that still reaches the mock adapter fails
> closed instead of producing fake "real" results.

## Operator mechanics

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
- **Audit with the committed scanner**, not an ad-hoc grep:

  ```bash
  python scripts/scan_evidence_leaks.py <artifact-dirs…> <trace-dirs…>
  # operator-only; compares against configured secret values without exposing them in argv:
  python scripts/scan_evidence_leaks.py <dirs…> --exact-value-file /path/to/secrets
  ```

## Checks the canary does not automate

### Body-format refresh

When the posted-body format (`render-body.vN`) changes, confirm before the repin
that a bot thread in the **previous** format, authored by a released image, is
refreshed in place. Use a
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

A canary panel supplies route evidence for the three OpenRouter seats when their
resolved models and efforts equal the shipped defaults, including when those come
from operator overrides; the fourth seat does not invalidate them. Check the prepare
manifest's `effective_config` for each seat's model and effort, that the manifest
and every review and critique status file share one configuration digest, and that
each seat succeeded in both stages. A silently substituted model does not pass.

When an effort profile changes, or the canary's routes differ from the defaults,
run one panel on the routes in question. When the default roster or a default model
changes, run one smoke on the exact three-seat roster:

- Select the roster only in that smoke's temporary workflow or template; keep the
  demos' resting roster at four seats.
- Leave model and effort overrides missing or blank so the
  [shipped defaults](../configuration.md#shipped-defaults) apply. For a model that
  requires absent effort, see
  [effort inheritance and clearing](../configuration.md#effort-inheritance-and-clearing).
- Keep `AI_REVIEW_LOCAL_MOCK=0` and every `AI_REVIEW_REQUIRE_REAL_*=1`.
- Require successful review and critique on every seat, schema-valid structured
  output, `panel_status: full`, and a posted finding.

Reuse a passing smoke on the same runtime and image pair rather than repeating it.

### Cursor model-specific check

Optional, and not an enablement gate: Cursor is a supported opt-in seat. Run it when
a deployment needs evidence for one specific Cursor model, or before adding Cursor
to the default roster.

1. Resolve the exact selector with `cursor-agent --list-models` on the pinned CLI,
   and set it through `AI_REVIEW_CURSOR_MODEL`. `auto` is supported for ordinary
   operation but proves nothing about a particular model. Cursor has no separate
   effort variable; reasoning depth is part of the selector, such as
   `grok-4.7-xhigh`.
2. Run a real review and critique against the candidate pair and confirm the
   selector reached the CLI with no silent fallback.
3. Note that this proves the route, not the deny policy; see
   [carried gaps](#carried-coverage-gaps).

### Manual lifecycle chain

The canary's `lifecycle` campaign automates this on both platforms. Run it by hand
only to diagnose a failed campaign. Use one change request and one mock finding
identity, and capture run, job and platform object IDs at every step:

1. create (`blocking`): one inline discussion at the mapped line;
2. rerun unchanged: same discussion, `updated_discussions=0`, `skipped_unchanged>=1`;
3. change body (`blocking_alt`): the same discussion updated in place,
   `updated_discussions=1`, a new `body_hash`, nothing created;
4. resolve with `/ai-review wontfix` (or `/ai-review resolve`, or the platform's
   resolve API), then rerun `blocking`: `resolved_discussions>=1`, and the
   disposition persists on a further unchanged rerun;
5. reopen through the platform API or `/ai-review reopen`, then rerun `blocking`:
   the same discussion is active again;
6. (GitHub) push a new head mid-run: `post` reports `status: stale_head`, writes
   nothing, and exits 0;
7. with `blocking` posted, `post_result.status` is `success`, the job is green, and
   the change request is mergeable as far as Code Tribunal is concerned.

A real panel's finding has a model-authored identity, so never continue it with the
mock; that opens a new discussion instead of updating one.

### GitLab hostile-MR checklist

The canary's `hostile` campaign automates probes 1–3 on the demo. Run them by hand
before a production rollout on your own GitLab deployment, for a fork-based variant,
or to diagnose a failed campaign. The probe fails closed in prepare and spends no
tokens.

1. Open a merge request from an **unprotected** source branch or fork, and confirm
   through the branches API that its effective `protected` flag is false, including
   wildcard rules. Protected `OPENROUTER_API_KEY` and `GITLAB_TOKEN` must be
   withheld, prepare must fail closed with every later stage skipped, and no input
   file may be uploaded. Accept an empty archive or one with only empty directories
   under `inputs/`; any file, symlink, unexpected path or corrupt archive fails the
   probe.
2. From a trusted checkout, audit the composition:
   `python scripts/pipeline_trust.py <consumer .gitlab-ci.yml> --mode <direct|child> --template-project <org/template> --template-sha <sha>`.
   Child mode must use exactly two same-project, same-SHA includes with inheritance
   and both forwarding flags disabled.
3. Attempt template or job replacement, trusted image and configuration overrides,
   and forged `out/consensus/*` or `out/post/*` artifacts. Confirm the trusted
   composition is retained or the pipeline fails closed, and scan every trace and
   artifact for credential values.

The symlink classes and forged-artifact integrity are regression-covered; confirm at
most one symlink variant live, reusing the `evidence/p0-symlink-*` branches.

## Carried coverage gaps

Paths no live run has exercised. **Updating this table is part of every release:**
remove what the release closed and add what it discovered, and copy any gap that
limits a release claim into its CHANGELOG section.

| Gap | Why it is unproven | How to close it |
|---|---|---|
| Trusted-image enforcement | a consumer `.gitlab-ci.yml` can substitute the pinned images through its own `variables:` and forwarding; no stage verifies the running image | none in-pipeline; containment is credential withholding plus the out-of-band trust auditor. Never claim trusted-image enforcement (see the [security model](../SECURITY_MODEL.md)) |
| Credential isolation beyond the recorded scope | proven only for an unprotected-ref merge request in the hardened child topology; egress is unenforced at the container boundary | claim "credential isolated" only in that scoped sense |
| GitLab fork-based merge request | the hostile probe uses an unprotected in-project branch | open the probe from a fork |
| Protected-ref insider | not attempted | out of scope unless the threat model changes |
| Cursor deny policy | a review success says nothing about the pinned CLI honouring the `Shell(*)` and write denies at runtime; only delivery of the policy is tested (see [SUPPLY_CHAIN.md](../../ai-review/images/SUPPLY_CHAIN.md)) | a hostile permission-denial prompt against the pinned CLI; required before Cursor joins the default roster |
| Confidence-related finding loss | the `0.0` default is unit-tested only, and no posted loss counters exist | a mock scenario whose seat omits `confidence`; posted counters come with SPEC-66 |
| Below-quorum FYI / summary comment | the mock emits identical findings on every seat | a per-seat mock scenario |
| Inline-unmappable summary fallback | the mock always anchors | a mock scenario with a deliberately unmappable anchor |
| Noise-demoted FYI entry (SPEC-67) | the mock critique stage emits no critiques | a mock critique scenario that votes noise; unit and golden tests cover it meanwhile |
| Platform-visible re-anchoring of a moved comment | updating a comment rewrites its body, not its diff position; the internal remap is regression-covered | an optional live push that moves the anchored line |
| Long bodies subject to truncation | no refresh has exercised a truncated body | a fixture with an oversized finding body |
| Live symlink containment variant | the GitLab commits API cannot create a `120000` tree entry | reuse the existing `evidence/p0-symlink-*` branches |
| Non-default effort routes | only the shipped defaults have live evidence on the current pair | a route panel when a release changes or claims non-default efforts |
| OpenRouter token and cost | no artifact carries usage; usage capture was dropped from SPEC-66 | read the per-key OpenRouter dashboard |
