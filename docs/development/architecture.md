# Architecture and trust boundaries

Code Tribunal separates model-controlled proposal generation from deterministic
policy and platform mutation.

| Operation | Trust level | Responsibility |
|---|---|---|
| Prepare | trusted deterministic container | Bind revision, diff, config, prior state, and contained snapshot |
| Review/critique | untrusted model output in reviewer container | Produce schema-constrained proposals and assessments |
| Consensus | trusted deterministic container | Validate evidence identity/integrity, group, vote, and decide policy |
| Post | trusted deterministic container | Reconcile state, mutate platform discussions/comments, and report publication health as the terminal job's exit status |

Reviewer subprocesses receive an allowlisted environment containing only their
selected credential and runtime controls; platform tokens stay in the trusted
prepare and post jobs. Snapshot containment, state authenticity, cross-stage
bindings, and the open egress risk are described in the
[security model](../SECURITY_MODEL.md).

GitHub has one reusable implementation in
[`.github/workflows/ai-review-reusable.yml`](../../.github/workflows/ai-review-reusable.yml).
Consumers call it at a release SHA; this repository's small caller uses the same
commit. The canary installs a candidate-pinned reusable workflow
and caller in its temporary demo branch. Only publication retries read the old
template/copy layout (COMPAT-010); no synchronization writer remains.

## Compatibility boundary

The supported public surface is versioned configuration, versioned input/output
artifacts, published container and template behavior, documented operator
commands, and current thread/state behavior. Private Python helpers and internal
module paths, logger names and exact log prose, fixture internals, undocumented
environment variables, and orchestration artifacts removed within a release
migration are not compatibility surfaces. Refactor or delete those private seams
instead of preserving wrappers for them.

## Module ownership

Dependencies point from platform transport into orchestration and then into pure
planning and policy modules. Guidance, not a checked contract — there are
deliberately no line-count assertions. What *is* enforced lives in
`ai-review/tests/unit/test_import_boundaries.py`: the pure modules cannot import
a platform client or `requests`, and `consensus_errors` cannot import anything
from the package at all.

| Module | Owns | Platform access |
|---|---|---|
| `config.py` | Configuration parsing, defaults, frozen runtime settings, and the full-config digest | no |
| `post.py` | `python -m ai_review.post` entry point only | via `posting` |
| `posting.py` | Mutation orchestration: the one network layer for posting | yes |
| `state_plan.py` | Pure state planning and record transitions | **never** |
| `summary_render.py` | Summary-comment body composition and size fitting | **never** |
| `notes.py` | Marker and review-note parsing | **never** |
| `commands.py` | `/ai-review` command collection and author authorization | yes |
| `consensus.py` | `python -m ai_review.consensus`; the deterministic reducer API | no |
| `grouping.py` | Path/category buckets and complete-link grouping by anchor/fingerprint identity | **never** |
| `critique.py` | Critique application and group re-decision | **never** |
| `run_health.py` | Observational run health: status-file loading, job rows, grade, and the job-log rendering; feeds no decision | **never** |
| `consensus_errors.py` | `ConsensusIntegrityError`, nothing else | **never** |
| `adapter_runner.py` | `python -m ai_review.adapter_runner`; `run_adapter` | no |
| `adapter_output.py` | Reviewer output parsing and root normalization | no |
| `adapter_process.py` | Adapter subprocess lifecycle and environment | no |
| `reviewer_setup.py` | Registry-declared CLI arguments, isolated state, snapshot workspaces, schema transport, and Claude's build-time acceptance probe | no |
| `adapter_artifacts.py` | Status and debug artifact writing | no |
| `opencode_client.py` | The OpenCode seat, launched by the runner as a module: generated config, server environment and the loopback server client | no |

These rules are worth stating because breaking them is silent:

- Each entry point resolves YAML and environment overrides once into
  `ReviewConfig`. Runtime consumers read its frozen sections and reviewer roster;
  only manifest serialization creates a mutable copy. Each job still checks its
  resolved config against prepare before consequential work, so overrides scoped
  to only some jobs fail as configuration drift.
- The reviewer registry selects each seat's launcher (a shell adapter, or a
  module run under the runner's own interpreter) and the output codec for the
  pinned adapter invocation. `_load_adapter_json` decodes that transport and
  passes the explicit stage to the shared root normalizer; no decoder guesses
  another reviewer's transport from stdout.
- The same registry declares CLI arguments, effort flags, schema transport,
  isolated state directories and environment settings. `reviewer_setup` prepares
  them for the shell seats and OpenCode; review receives a copied snapshot and
  critique an empty working root. The shell adapters only check prerequisites
  and invoke the CLI. Claude's image-build acceptance probe uses the runtime
  argument builder, so it cannot drift to a different flag or schema combination.
- `_coerce_adapter_root` in `adapter_output.py` is the **single** normalization
  point every reviewer seat funnels through. Do not add a second one.
- Model authoring contracts contain finding content and diff coordinates, or
  short critique references. `schema` projects and validates authoring fields,
  resolves locations against the prepared diff, and supplies runtime metadata.
  The critique pool is validated before rendering and retained in runner memory;
  its on-disk copy is diagnostic, never an authority after the model runs.
  S05 changes the authoring schemas, finalizers and their consumers together:
  staging them separately would require an old/new contract path. Finding and
  critique batches move to v2 without changing consensus or persisted state.
- Consensus uses only current-run evidence and writes signature-derived group
  IDs in `consensus.v3`. Posting matches each group against the complete latest
  state once, preserves a matched thread's historical ID, and routes ambiguous
  or already-claimed matches to the summary without changing the decision.
  N11 changes prepare, the consensus schema/producer, and posting together:
  staging the alias-artifact deletion or consumers separately would require a
  temporary old/new contract path.
- S14 stores state v2 inside the summary; rendering reserves room for the complete
  ledger, supported returned thread-ID sizes, and health section before
  truncating visible findings. The explicit
  offline v1 migration preserves record identities without an online decoder.
  Posting uses the current group's prepared anchor and context aliases (N14),
  with no additional diff fetch or scan. Prepare, the state schema, publication,
  and GitHub comment transport change together because an old/new runtime path
  would otherwise be needed. The offline converter has no platform access.
  The state schema validates reads and writes and defines the supported ID
  bounds used by preflight. Change binding lives in `memory.load_state_from_notes`:
  GitLab project IDs remain strict, while GitHub's authenticated PR comment binds
  the repository and its mutable owner/name slug is refreshed on load.
- `consensus_errors.py` exists solely to break an import cycle: `critique`
  raises `ConsensusIntegrityError`, and the class is defined partway down
  `consensus.py`, so importing it from there fails on a partially-initialized
  module. `critique` must import it from `consensus_errors`, never from
  `consensus`.

`posting.post_inline` and `posting.finalize_state` mutate the shared
`PostResult` dict and `StatePlan.planned_records` **in place**. That is the
contract callers rely on; converting either to returned deltas is a redesign.
