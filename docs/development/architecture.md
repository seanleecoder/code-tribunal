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
selected credential and runtime controls. GitHub/GitLab posting tokens are held
by trusted prepare/post jobs and are not forwarded to reviewer subprocesses.
Repository snapshots reject symlinks and special files and use descriptor-based
no-follow traversal on supported Unix platforms.

The outer CI job remains trusted and can see CI-provided variables. Reviewer CLI
tool policies and endpoint validation are defense in depth, not a container
network boundary. Container/runner egress enforcement remains an open risk; see
the [security model](../SECURITY_MODEL.md).

State is stored in an author-verified bot note/comment with a checksum. The
checksum detects corruption, not compromise of the bot credential or platform.
Cross-stage run IDs and effective-config digests detect accidental or hostile
artifact mixing within the trusted pipeline contract; they are not signatures
against an artifact writer that already controls the trusted job.

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
| `post.py` | `python -m ai_review.post` entry point only | via `posting` |
| `posting.py` | Mutation orchestration: the one network layer for posting | yes |
| `state_plan.py` | Pure state planning and record transitions | **never** |
| `summary_render.py` | Summary-comment body composition and size fitting | **never** |
| `notes.py` | Marker and review-note parsing | **never** |
| `commands.py` | `/ai-review` command collection and author authorization | yes |
| `consensus.py` | `python -m ai_review.consensus`; the deterministic reducer API | no |
| `grouping.py` | Overlap, similarity, union-find grouping | **never** |
| `critique.py` | Critique application and group re-decision | **never** |
| `consensus_errors.py` | `ConsensusIntegrityError`, nothing else | **never** |
| `adapter_runner.py` | `python -m ai_review.adapter_runner`; `run_adapter` | no |
| `adapter_output.py` | Reviewer output parsing and root normalization | no |
| `adapter_process.py` | Adapter subprocess lifecycle and environment | no |
| `adapter_artifacts.py` | Status and debug artifact writing | no |

Two rules are worth stating because breaking either is silent:

- `_coerce_adapter_root` in `adapter_output.py` is the **single** normalization
  point every reviewer seat funnels through. Do not add a second one.
- `consensus_errors.py` exists solely to break an import cycle: `critique`
  raises `ConsensusIntegrityError`, and the class is defined partway down
  `consensus.py`, so importing it from there fails on a partially-initialized
  module. `critique` must import it from `consensus_errors`, never from
  `consensus`.

`posting.post_inline` and `posting.finalize_state` mutate the shared
`PostResult` dict and `StatePlan.planned_records` **in place**. That is the
contract callers rely on; converting either to returned deltas is a redesign.
