# SPEC-68 — Posting and state correctness fixes for 2.0.3

- **Severity:** Medium. Each defect can silently resolve a thread, block posting, or change behaviour without any configuration change being visible. · **Effort:** S (four independent fixes of about 10 lines each, plus tests) · **ROI rank:** post-2.0.2, step 1
- **Vehicle:** 2.0.3. All four fixes are compatible: no schema, template, or artifact change.
- **Depends on:** nothing. Fix 3 lands in the same pull request as
  [SPEC-47](spec-47-trusted-project-review-config.md), because SPEC-47 already adds a
  pre-work binding check to post.
- **Found by:** the 2026-10-13 simplification review of the decision, state, and
  posting layers. Fixes 1, 2 and 4 were confirmed by reading the code at `da35afa`.
  Fix 1 was also reproduced in a planning dry run.

Land each fix as its own pull request.

## Fix 1 — an ambiguous match auto-resolves its candidate threads

**Defect.**

- When consensus cannot choose between several persisted records for one group, it
  sets `issue_id = None` and `issue_id_source = "ambiguous_unassigned"`
  (`consensus.py`, the state-alias matching block).
- Post planning then builds `all_current_groups` only from groups whose `issue_id`
  is a string (`plan_state` in `state_plan.py`). The ambiguous group is skipped
  before matching, so its candidate records are never added to
  `protected_issue_ids`.
- `_plan_stale_records` then treats those records as absent. With the resolution
  quorum, it marks every candidate `resolved`. A reproduction with two open
  records, one ambiguous FYI group, and a quorum of two produced
  `[('aa', 'resolved'), ('bb', 'resolved')]`.

The policy in `decide_group` ("an ambiguous match … cannot safely choose which
historical thread to mutate") is therefore contradicted by the stage after it:
nothing is mutated as an update, but every candidate is resolved.

**Fix.**

- In `plan_state`, run `find_matching_record` for groups whose `issue_id` is
  `None` as well.
- On an `ambiguous` or `matched` result, protect the candidates exactly as the
  existing ambiguous branch does: status `stale`, `remap_status: "ambiguous"`,
  and the candidate ID added to `planned_issue_ids`.
- Never create a new record for an ID-less group.

**Tests (`test_state_plan.py`).**

- An ambiguous group with two open candidate records leaves both `stale` and
  `ambiguous`, never `resolved`, with and without the resolution quorum.
- A later run that matches one candidate unambiguously updates that one only.

## Fix 2 — the summary comment has no ownership check

**Defect.** `find_summary_note` (`notes.py`) returns the first discussion whose root
note carries an `ai-review-summary:v1` marker, from any author.
`upsert_summary_comment` (`posting.py`) then reacts to whichever note it found:

- **Plant a marker.** Any participant who posts a marker breaks every later run.
  Post tries to update a note it does not own; the platform rejects that, and the
  failure escapes before state or `post_result` is written.
- **Copy the hash.** A participant who copies the bot's current `body_hash` makes
  post report `unchanged`, so the real summary is never updated.

State notes already avoid this: `state_note_candidates` filters on
`expected_author_id`, using `client.current_user_id()`.

**Fix.**

- `find_summary_note` takes `expected_author_id` and ignores notes from other
  authors, recording one warning per ignored note.
- `upsert_summary_comment` passes the bot ID that post already fetches. Dry runs
  keep today's behaviour.

**Tests.**

- `test_post.py`: a non-bot note carrying the marker is ignored. The bot creates
  its own summary, and the warning is recorded.
- `test_post.py`: a non-bot note carrying a copy of the current hash does not
  suppress the bot's update.
- Fakes for both platforms: a non-bot note with a marker leaves the bot's
  summary unchanged.

## Fix 3 — post does not bind its consensus artifact to this run

**Defect.** `post.cli` validates the consensus artifact against its schema and
nothing else. The artifact's `run_id`, `head_sha` and `input_manifest_sha256` are
never compared with the manifest post reads from `--inputs`. Nothing stops artifacts
from different runs being mixed: for example, a retried job that downloads another
run's consensus, or an operator replay. Consensus already writes
`input_manifest_sha256` for exactly this check.

**Fix.** Before creating the platform client, post rejects the artifact unless all
of these hold:

- `consensus.run_id == manifest.run_id`;
- `consensus.head_sha == manifest.head_sha`;
- `consensus.input_manifest_sha256` equals the canonical SHA-256 of `manifest.json`,
  computed exactly as consensus computes it.

Exit with the operational-failure code and a message naming the mismatched field.

This prevents mixing artifacts across runs; it is **not** tamper protection.
ADR-0002's note that inter-stage integrity is unaddressed still stands. Say so in a
code comment and in the [security model](../SECURITY_MODEL.md).

**Tests (`test_post.py`).**

- Each mismatched field fails before any platform call.
- A matching artifact posts as today.

## Fix 4 — configuration defaults disagree between the YAML and the code

**Defect.**

- `post_consensus` (`posting.py`) falls back to `inline_multiline = False` and
  `v1_inline_sides = ["new"]`. The shipped
  [`review.yaml`](../../ai-review/config/review.yaml) and the configuration docs say
  `true` and `[new, old, unchanged]`. A custom configuration that omits either key
  silently gets the narrower behaviour.
- Any `posting.fyi_mode` other than `summary_comment` silently discards every FYI
  finding. The value is never validated: `fyi_mode` is only a known key name in
  `config.py`.

**Fix.**

- `validate_config` sets the documented defaults for `inline_multiline`,
  `v1_inline_sides` and `fyi_mode`, and rejects a `fyi_mode` value other than
  `summary_comment`.
- Consumers read the validated value without their own fallback.
- The typed configuration object planned for 2.1.x (simplification item N05) later
  removes this whole class of drift.

**Tests (`test_config_env_overrides.py` or the config validation tests).**

- Omitted keys resolve to the documented defaults.
- An unknown `fyi_mode` is rejected with a clear message.

## Acceptance

- No planning path resolves a record that an ambiguous group matched.
- Only bot-authored summary notes are read or updated.
- Post refuses a consensus artifact from another run before any platform call.
- The documented defaults are the effective defaults, and an invalid `fyi_mode` is
  rejected.
- `make quality` passes, and the CHANGELOG lists each fix under 2.0.3.
- Delete this file and its index row when all four fixes have merged.
