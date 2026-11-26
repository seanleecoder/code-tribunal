# SPEC-69: Repository-tool health admission for every reviewer seat

Status: open, accepted 2026-11-26. This is a requirement document, not current
product documentation. Delete it when the last pull request below merges.

## Problem

A reviewer seat's repository-read tools can fail at runtime while the seat still
returns schema-valid JSON. The runner then records `success`, and consensus
counts a diff-only review as an independent identity. The v3.1.0 Codex canary
showed this; #304 and #329 addressed it for Codex alone. The other seats have the
same gap:

- **Claude.** `_load_claude_stream` ignores `tool_result` events and
  `permission_denials`. On the consumer reviewer digest (Claude Code 2.1.325), a
  probe forced a verified `Read` failure:
  `EACCES: permission denied, open '<path>'`, with `is_error: true`. The CLI
  exited 0 and the runner recorded `success`
  ([run 45807372796](https://github.com/seanleecoder/code-tribunal/actions/runs/45807372796)).
  The same run shows that `is_error` alone cannot separate this failure from a
  benign missing file. A no-match `Grep` result carries no `is_error`.
  [Run 45805403141](https://github.com/seanleecoder/code-tribunal/actions/runs/45805403141)
  showed that Claude's `Grep` works without the image's `/usr/local/bin/rg`.
- **Codex.** `codex_repo_tools.py` reports every `OSError`, and every error that
  `Path.is_file()`/`is_dir()` swallow, as a tool-reported result. The codec counts
  such results as benign.
- **OpenCode.** `opencode_client` discards tool parts from the session history
  and emits only the final batch.
- **Cursor.** Text output carries no tool telemetry, and the pinned CLI exposes
  no supported custom endpoint for a loopback gate.
- **All seats except Codex.** `adapter_runner` rejects a nonzero exit only when
  stdout is empty.

## Rule

One rule for every seat, applied before the single `_coerce_adapter_root`
normalization seam. A rejection uses the existing `model_error` outcome.

1. Any nonzero launcher exit is `model_error`, even when stdout holds valid JSON.
2. A definite infrastructure failure is `model_error` even with zero tool calls.
   Examples: a missing Codex code-mode host, or a Claude review `system/init`
   inventory that lacks the configured repository tools.
3. When at least one repository-tool attempt was made, reject the review if
   every attempt failed operationally, ended without a result, or returned an
   error shape the pinned transport cannot classify.
4. A successful result or a captured benign lookup keeps the run eligible.
   Benign lookups are a missing path and a no-match search. An invalid argument
   or a deliberate scope refusal also counts, once its trace has been captured
   on the pinned CLI. Zero-attempt reviews stay eligible.

Classification reads only the tool's own result record. It never reads file
contents, assistant prose or the answer.

## Pull requests

Each item is one pull request. They are listed in merge order; 4 can land
before 3.

1. **Runner exit rule and shared predicate.**
   - The runner rejects every nonzero exit.
   - One internal predicate in `adapter_output.py` replaces Codex's inline
     accounting with no semantic change.
   - Claude's preceding-answer fallback still applies at exit 0.
2. **Codex filesystem classification.**
   - Operational `OSError`s take the existing JSON-RPC error path.
   - Error-preserving `os.stat` checks replace the swallowing `is_file`/`is_dir`
     predicates, with stat, traversal and open regressions.
   - Ripgrep gets an explicit, tested rule for exit 2 with partial output. A
     benign bad-pattern control and a post-launch I/O failure are both
     acceptance criteria.
   - The Codex image gate adds a verified read fault.
3. **Claude admission and image gate.**
   - The codec correlates `Read`/`Grep`/`Glob` calls with their results and
     `permission_denials`, and applies the review-only init check.
   - Benign shapes are an allowlist of captured traces, with fixtures under
     `tests/fixtures/claude/`.
   - The classifier is reproduced on the candidate's Claude 2.1.332 pin before it
     is finalized.
   - New `scripts/smoke_claude_batch_output.{py,sh}` gate, run in the image
     workflow:
     - stock and capability-matched read controls;
     - a mode-000 fault without `DAC_OVERRIDE`/`DAC_READ_SEARCH`, with a
       path-specific `EACCES` witness, expecting `model_error`;
     - missing-file, no-match and recovered runs expecting `success`.
4. **OpenCode admission.**
   - `_session_answer` classifies `read`/`grep`/`glob` tool parts across the
     prompt-bound history from captured 2.4.2 shapes.
   - A rejection exits 1 without emitting a batch.
   - Tests add a pagination regression and an image-gate fault in a session that
     still completes.
5. **Candidate Canary witness for Claude, Codex and OpenCode.**
   - The Codex witness is generalized to per-seat witness files, bound to seat,
     platform, image, fixture, run and configuration.
   - OpenCode returns its observation through runner-owned scratch that stays out
     of the server environment.
   - `canary_github.py`, `canary_gitlab.py`, `validate_candidate_canary.py` and
     `check_release.py` change together, with negative tests: missing, false,
     integer `1`, stale, cross-seat, wrong image and wrong job.
   - Release cutover: every tag through v3.1.0 is published, so nothing is
     stranded today. Re-check immediately before merging. If a patch tag citing a
     Codex-only campaign is signed first, tie the legacy check to that release
     and register it in
     [temporary compatibility](../development/temporary-compatibility.md).

Cursor gets the exit rule only. Panel summaries report its repository reads as
`unverified`, and the supply-chain and testing guides document the gap. A
follow-up may adopt a supported structured output mode once it is verified for
healthy, benign and failed tool outcomes.

## Verification

- Each pull request runs `make quality` and focused fixture tests: healthy,
  benign-only, mixed, zero-tool, all-failed and nonzero-exit. Repository text
  resembling an error must not trigger rejection.
- The image gates run against a built reviewer image.
- The release runs a Candidate Canary on the exact digest on GitHub and GitLab,
  verified by `check_release.py`.

The probe branch `probe/claude-repo-tool` is evidence only and is not merged.
