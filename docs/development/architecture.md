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
and caller in its temporary demo branch. No copied template or synchronization
writer remains.

## Compatibility boundary

The supported public surface is versioned configuration, versioned input/output
artifacts, published container and template behavior, documented operator
commands, and current thread/state behavior. Private Python helpers and internal
module paths, logger names and exact log prose, fixture internals, undocumented
environment variables, and orchestration artifacts removed within a release
migration are not compatibility surfaces. Refactor or delete those private seams
instead of preserving wrappers for them.

## Module ownership

JSON schemas own artifact shapes at model, file and platform-state boundaries.
`types.py` supplies internal annotations for Python consumers, not an independent
wire contract: annotations need not enumerate every schema field. Producer tests
validate concrete output against the shipped schemas; admission tests reject
malformed transported artifacts, and state tests exercise encoding and decoding.
Do not add a reflection-based schema/type parity checker.

Dependencies point from platform transport into orchestration and then into pure
planning and policy modules. Guidance, not a checked contract — there are
deliberately no line-count assertions. What *is* enforced lives in
`ai-review/tests/unit/test_import_boundaries.py`: the pure modules cannot import
a platform client or `requests`, and `consensus_errors` cannot import anything
from the package at all.

| Module | Owns | Platform access |
|---|---|---|
| `config.py` | Configuration parsing, defaults, frozen runtime settings, and the full-config digest | no |
| `reviewers.py` | The trusted first-party reviewer registry | no |
| `input_bundle.py` | `python -m ai_review.input_bundle`; revision binding, diff collection, snapshot containment, and prior-state loading for the manifest | yes |
| `schema.py` | Schema loading and validation, outcome construction, and authoring finalization | no |
| `memory.py` | State encoding and decoding, hash and author checks, change binding, and compaction | no |
| `post.py` | `python -m ai_review.post` entry point only | via `posting` |
| `posting.py` | Mutation orchestration: the one network layer for posting | yes |
| `state_plan.py` | Pure state planning and record transitions | **never** |
| `summary_render.py` | Summary-comment body composition and size fitting | **never** |
| `notes.py` | Marker and review-note parsing | **never** |
| `commands.py` | `/ai-review` command collection and author authorization | yes |
| `consensus.py` | `python -m ai_review.consensus`; the deterministic reducer API | no |
| `consensus_policy.py` | The one surface/FYI decision, `apply_group_decision` | no |
| `grouping.py` | Path/category buckets and complete-link grouping by anchor/fingerprint identity | **never** |
| `critique.py` | Critique application and group re-decision | **never** |
| `run_health.py` | Observational run health: job rows, grade, and the job-log rendering; feeds no decision | **never** |
| `consensus_errors.py` | `ConsensusIntegrityError`, nothing else | **never** |
| `adapter_runner.py` | `python -m ai_review.adapter_runner`; `run_adapter` | no |
| `adapter_output.py` | Reviewer output parsing and root normalization | no |
| `adapter_process.py` | Adapter subprocess lifecycle and environment | no |
| `reviewer_setup.py` | Registry-declared CLI arguments, isolated state, snapshot workspaces, schema transport, and Claude's build-time acceptance probe | no |
| `adapter_artifacts.py` | Redacted parse/timeout diagnostic writing | no |
| `outcomes.py` | Shared stage outcome loading and run/config/filename admission | no |
| `opencode_client.py` | The OpenCode seat, launched by the runner as a module: native v2 config, isolated server environment and the authenticated loopback session client | no |

These rules are worth stating because breaking them is silent:

- Trusted prepare resolves image defaults, custom YAML and environment overrides
  once into frozen `ReviewConfig` settings. The complete v4 document travels in
  the manifest; downstream entry points admit it without interpreting their
  environment or reading YAML. Run/config digests still bind evidence at CI
  transport boundaries.
- Artifact versions are not read across image pairs. Producers and consumers run
  from one pinned pair with fresh artifacts from prepare; there is no online
  reader for an older configuration, batch, consensus or state version.
- The reviewer registry selects each seat's launcher (a shell adapter, or a
  module run under the runner's own interpreter) and the output codec for the
  pinned adapter invocation. `_load_adapter_json` decodes that transport and
  passes the explicit stage to the shared root normalizer; no decoder guesses
  another reviewer's transport from stdout. Codex uses its `exec --json` events;
  terminal failures, an unavailable code-mode host (Codex's startup error item,
  or its one timestamped router line in the stderr tail for a session that
  fails to open) and wholly failed operational tool attempts invalidate even a
  well-formed final answer, and a shell command item is a schema error because
  Codex runs without a shell. A repository MCP call that reports a tool error
  (missing file, refused path) is a usable result, like a grep with no match; a
  call whose `error` is set is operational, which includes a search that timed
  out or could not start, because the tool server returns those as JSON-RPC
  errors. Calls to Codex's own MCP resource tools are admitted and do not count
  as repository attempts. No-tool reviews and critiques and recovery after a
  failed tool remain usable.
- Codex runs without a shell. Review reads the snapshot through the trusted
  [`codex_repo_tools`](../../ai-review/src/ai_review/codex_repo_tools.py) MCP
  server that the registry configures; it is standard-library only and imports
  nothing from the runner. See the
  [ADR-0003 amendment](../decisions/0003-product-invariants-and-complexity-envelope.md#amendment-2026-11-17-no-shell-codex)
  and the [image gate](testing.md#codex-image-gate).
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
- Consensus uses only current-run evidence and writes signature-derived group
  IDs in `consensus.v3`. Posting matches each group against the complete latest
  state once, preserves a matched thread's historical ID, and routes ambiguous
  or already-claimed matches to the summary without changing the decision.
- State v2 lives inside the summary; rendering reserves room for the complete
  ledger, supported returned thread-ID sizes, and health section before
  truncating visible findings. Posting uses the current group's prepared anchor
  and context aliases, with no additional diff fetch or scan. The state schema
  validates reads and writes and defines the supported ID bounds used by
  preflight. Change binding lives in `memory.load_state_from_notes`: GitLab
  project IDs remain strict, while GitHub's authenticated PR comment binds the
  repository and its mutable owner/name slug is refreshed on load. The v1
  decoder exists only in the offline converter, which has no platform access.
- Each finding/critique batch is the complete execution outcome. `schema`
  constructs and validates outcomes, with count arithmetic in `validate_outcome`;
  `outcomes` admits transported files for both critique pooling and consensus.
  One pure quality helper derives eligibility and accepted/omitted counts from
  the payload. Health reads no files and remains observational.
- `consensus_errors.py` exists solely to break an import cycle: `critique`
  raises `ConsensusIntegrityError`, and the class is defined partway down
  `consensus.py`, so importing it from there fails on a partially-initialized
  module. `critique` must import it from `consensus_errors`, never from
  `consensus`.

`posting.post_inline` and `posting.finalize_state` mutate the shared
`PostResult` dict and `StatePlan.planned_records` **in place**. That is the
contract callers rely on; converting either to returned deltas is a redesign.
