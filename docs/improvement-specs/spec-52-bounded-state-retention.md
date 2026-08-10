# SPEC-52 — Bounded state retention and recoverable state overflow

- **Status:** Proposed (post-1.0; not a current product feature).
- **Severity:** High (an MR that crosses the cap is permanently blocked; every subsequent pipeline on it fails closed and the state can only grow).
- **Effort:** M.
- **Depends on:** nothing. It touches the same retention configuration that [SPEC-47](spec-47-trusted-project-review-config.md) would later deliver from a trusted revision, but it does not require SPEC-47 and must not wait for it.
- **Related work:** [SPEC-42](spec-42-wontfix-gate-semantics.md) owns `wontfix` gate semantics; this specification must not change what a `wontfix` record means, only whether it can be shed under byte pressure. [SPEC-22](spec-22-project-rules-and-learning.md) already assumes a shed-first behavior under `max_state_bytes` pressure that does not exist.

## Rationale

State overflow is currently a terminal, self-perpetuating block, and it has now been
observed in production.

On a private GitLab MR (pipeline `189887`, post job `2634996`), `post` returned
`status: state_overflow` with `state is 52345 bytes, exceeds
state.retention.max_state_bytes (50000)`. The gate job consumed that result and
exited nonzero at `gate.py:46`, which treats `failed`, `partial_failed`, and
`state_overflow` as `block_merge=true` unconditionally. The review itself had
succeeded: consensus produced groups, and `post` returned before any mutation
(`post.py:2008`), so nothing was posted and `created_discussions` was `0`. A
complete, valid review was discarded and the MR was blocked on a storage
measurement.

The persisted state note at the time of failure held 20 records — 9 `wontfix`,
6 `open`, 5 `resolved` — encoding to 33,335 bytes of canonical JSON and 44,447
bytes of the base64 note payload. Two fields dominated: `anchor` (9,330 bytes
across all records) and `aliases` (8,833 bytes). One run added roughly 7.9 KB.

Three properties combine to make this unrecoverable:

1. **Compaction does not bound the growing part.** `compact_state`
   (`memory.py:331`) caps only `resolved` (`keep_resolved_records`, default 5) and
   `stale` (`keep_stale_records`, default 2). `keep_open` and `keep_wontfix` are
   booleans, so open and dismissed records are retained without limit. 15 of the 20
   records were in the unbounded classes.
2. **Per-record size grows monotonically.** Alias families are merged as unions on
   every match (`post.py:551-565`), so `candidate_issue_signatures`,
   `source_finding_ids`, `context_hashes`, and `title_fingerprints` accumulate a new
   32-byte hash per revision that produces a new spelling of the same finding. A
   record never shrinks. `run_history` (`post.py:1319`) is append-only and is never
   trimmed by anything.
3. **There is no shed step and no escape hatch.**
   `state_overflow_reason` (`memory.py:375`) measures the already-compacted state and
   returns a reason string. Nothing then tries to make the state smaller. The
   operator has no in-band remedy: the only recorded guidance is "Repair API/state
   capacity and rerun post/gate" (`docs/operations.md:63`), and no
   `AI_REVIEW_*` override reaches `state.retention` — the env override set in
   `config.py:220-315` does not include any retention key. Manually deleting the
   state note works but discards every dismissal and reposts every open finding as
   new.

The result is that a long-lived MR accumulating dismissed and open findings will
deterministically reach the cap, and from that moment forward every pipeline on
that MR fails closed with no path back except destroying the review history.

This specification makes retention bounded at the source, makes overflow a
recoverable and auditable shed rather than a terminal failure, and reserves the
fail-closed outcome for the case where the only remaining way to fit is to discard
live review state.

## Scope and non-goals

**In scope**

- A deterministic, ordered shed ladder inside compaction that runs only when the
  encoded state exceeds its budget and stops at the first tier that fits.
- Bounding alias-family growth at merge time, so state stops growing without bound
  in the first place.
- `run_history` retention.
- A backend-aware ceiling for `max_state_bytes`, validated at config load.
- Structured, non-truncating shed provenance in `post_result`.
- Separating a successful lossy compaction from a genuine fail-closed overflow, and
  keeping the gate's fail-closed behavior for the latter.
- Configuration, schema/type, documentation, and regression coverage.

**Out of scope**

- Compressing the state payload, changing the encoding, moving state off the
  note/comment backend, or sharding it across multiple notes. Each is a larger
  change to the storage contract and none is needed to stop the block.
- Changing what a `wontfix` record means for the merge gate. That is SPEC-42.
- Changing state matching precedence (`MATCH_PRECEDENCE`), the deterministic
  `issue_id`, absence-based resolution, or the discussion-marker recovery path.
- Any environment override for retention. Retention stays configuration-only,
  consistent with `STATE_RETENTION_KEYS` (`config.py:77`) being a file-schema
  allowlist rather than an override surface.
- Automatically deleting or rewriting an existing oversized state note. Recovery
  from an already-blocked MR is an explicit operator action, documented here.
- Shedding an `open` record, or any record with a live `discussion_id`, under any
  pressure. That is the boundary between lossy compaction and losing review state.

## Exact contract

### 1. Backend-aware byte ceiling

The current default of `50000` (`ai-review/config/review.yaml:130`) is not
arbitrary and must not be raised globally. The state payload is stored as a note or
comment body, and the two supported backends have different hard limits:

| Backend | Platform hard limit on the body | Notes |
| --- | --- | --- |
| `gitlab_mr_state_note` | ~1,000,000 characters | The current default uses 5% of it. |
| `github_pr_comment` | 65,536 characters | A `max_state_bytes` above this produces a platform write rejection, not a clean overflow. |

The implementation must pin both limits as named constants with a documentation
reference, not as inline literals, and must treat them as the backend's hard
ceiling.

`max_state_bytes` becomes backend-derived when unset:

| `state.backend` | Default `max_state_bytes` |
| --- | --- |
| `gitlab_mr_state_note` | `200000` |
| `github_pr_comment` | `50000` |

An explicitly configured `max_state_bytes` above the resolved backend's hard
ceiling is a config-load failure with both values named. It is not clamped
silently. A configured value at or below the ceiling is honored as-is, including a
value below the default.

The measurement stays exactly what it is today: the UTF-8 byte length of the
complete `encode_state_note(state)` output (`memory.py:173-183`), wrapper line and
`state_hash` comment included. No new measurement basis is introduced.

Raising the GitLab default is deliberate headroom, not a fix. At the observed
growth it buys roughly twenty more runs on a busy MR. The shed ladder below is the
fix; the raised default is what keeps a currently-healthy MR from hitting the
ladder for an ordinary reason.

### 2. Bounded alias families at merge time

The alias union in `_merge_aliases` (`post.py:551-565`) is the primary growth
driver and is bounded before any pressure exists.

A new retention key `max_aliases_per_family` (default `8`) caps each of
`candidate_issue_signatures`, `source_finding_ids`, `context_hashes`,
`title_fingerprints`, and `symbols` independently, per record.

Ordering is the hard part and must be handled explicitly rather than assumed. The
stored families are `sorted(set(...))`, so they carry no recency information — a
naive "keep the newest N" is not implementable against the current representation.
The v1 rule is therefore two-part and deterministic:

1. **Always retain the current run's contribution.** Every value contributed by
   this run's `group` / `match_keys` is retained, regardless of the cap. A family
   whose current-run contribution alone exceeds the cap retains all of it and
   records the overflow; the cap never drops a value the current run just proved
   relevant.
2. **Fill the remainder in ascending lexicographic order** from the previously
   stored values, and drop the tail.

Lexicographic order over hashes is arbitrary with respect to age. It is chosen
because it is deterministic, requires no schema change, and is stable across runs —
the same record converges on the same retained set rather than churning. The cost
is recall: a historical spelling of a finding may be dropped and a later run may
fail to match on `source_finding_id` or `context_hash`.

That cost is bounded by matching precedence. `MATCH_PRECEDENCE` tries
`exact_issue_id` first, and the deterministic `issue_id` is not an alias and is
never shed. Alias families are recovery paths for a finding whose identity
changed, so a dropped alias degrades re-match recall for a mutated finding; it
cannot orphan a stable one.

An implementation that instead adds explicit per-alias ordering metadata is
permitted, but must show that the added bytes do not exceed the bytes the cap
saves, and must keep the retained set deterministic.

### 3. `run_history` retention

A new retention key `keep_run_history` (default `20`) trims
`state["run_history"]` to the most recent N entries during compaction, always —
not only under pressure. Ordering is append order, which
`_planned_state_payload` (`post.py:1319-1325`) already produces; the newest
entries are at the tail.

`run_history` was 511 bytes in the observed incident and is not the driver. It is
bounded here because it is the only remaining append-only structure in state, and
leaving one unbounded list defeats the purpose of the ladder.

### 4. The shed ladder

Compaction gains a second phase. Phase one is the existing classification and
`keep_resolved_records` / `keep_stale_records` capping, unchanged. Phase two runs
only if the encoded state still exceeds `max_state_bytes`.

The ladder walks tiers in strictly increasing order of information loss,
re-encoding and re-measuring after each tier, and stops at the first tier that
brings the state within budget:

| Tier | Action | Loss |
| --- | --- | --- |
| 1 | Trim `run_history` to 5 entries. | Diagnostic provenance only. |
| 2 | Reduce `max_aliases_per_family` to 4 across all records, applying the §2 retention rule. | Re-match recall for mutated findings. |
| 3 | Reduce retained `stale` records toward 0, oldest first by `_retention_sort_key` (`memory.py:412`). | Stale-record history. |
| 4 | Reduce retained `resolved` records toward 0, oldest first. | Resolved-record history. |
| 5 | Shed `wontfix` records, oldest first by `_retention_sort_key`, down to a floor of `min_wontfix_records` (default `0`). | **A dismissal is forgotten and the finding will repost.** |

Tier 5 is qualitatively different from tiers 1–4 and must be treated as such: it
discards a durable human decision. It is last, it is separately configurable via
`min_wontfix_records`, and an operator who sets that floor above zero is choosing
to fail closed rather than forget a dismissal.

Records that are never shed at any tier:

- any record with `status == "open"`;
- any record with a non-null `discussion_id`, regardless of status;
- any record whose `human_disposition` is set, other than through tier 5's explicit
  `wontfix` path.

`state_hash` is recomputed after each tier via the existing `attach_state_hash`
path, so a partially-shed state is never encoded with a stale hash.

The ladder must be a pure function of the input state and the retention
configuration. It may not read the clock, the environment, or the platform.

### 5. Successful lossy compaction versus fail-closed overflow

Today every overflow is fail-closed. After this change the two outcomes are
distinct:

**The ladder fits.** `post` proceeds normally: it posts, updates, resolves, and
writes the state note as it would have. `post_result.status` stays `success` (or
whatever it would otherwise have been). The shed is reported through §6 and
through job-visible warnings. The gate is unaffected. A lossy compaction is not a
blocking condition, because the alternative — blocking the MR — loses strictly
more than the shed did.

**The ladder cannot fit.** `post_result.status` stays `state_overflow` and the
gate keeps its current fail-closed behavior at `gate.py:46`. Reaching this state
now means something stronger than before: the state cannot be brought within
budget without discarding open records or live discussions. The result must carry
the complete shed record from §6 plus the final measured size, so the reason is
diagnosable from the artifact alone.

`post` retains its current fail-before-mutation ordering (`post.py:2008`) for the
fail-closed case. No comment, discussion, or state note is mutated on the path
that returns `state_overflow`.

### 6. Shed provenance

`post_result` gains a strict, additive `state_retention` object:

~~~json
{
  "schema_version": "state_retention.v1",
  "max_state_bytes": 200000,
  "encoded_bytes_before": 213480,
  "encoded_bytes_after": 187221,
  "tiers_applied": [1, 2],
  "shed": {
    "run_history_entries": 15,
    "alias_values": 96,
    "stale_records": 0,
    "resolved_records": 0,
    "wontfix_records": []
  },
  "fits": true
}
~~~

`shed.wontfix_records` is a complete list of the shed records' `issue_id` and
`title`, never a count and never truncated — a forgotten dismissal must be
recoverable from the artifact. If the complete record cannot be represented within
an artifact bound, that is a failure, not a truncation.

Every tier that fires also emits a `post_result.warnings` entry naming the tier and
what it dropped, so the shed is visible in normal job output without reading the
artifact. A tier-5 firing emits a warning at the strongest available severity and
names every shed `wontfix` title.

When no tier fires, `state_retention` is still emitted with
`tiers_applied: []` and `fits: true`, so its absence in a fresh artifact is
unambiguous.

### 7. Operator recovery for an already-blocked MR

An MR that is already over the cap under the old images is not fixed by deploying
this change alone — the ladder will run on the next pipeline and, in the observed
case, tier 1 or 2 would resolve it. Documentation must state that case plainly and
must also document the manual reset for a state that the ladder cannot fit:
deleting the state note discards every dismissal and reposts every open finding as
a new discussion, and is a last resort with a named cost, not routine maintenance.

## Failure behavior

| Condition | Required result |
| --- | --- |
| Encoded state is within budget | No tier fires; `state_retention` records `tiers_applied: []`; behavior is byte-identical to today. |
| A tier brings the state within budget | `post` proceeds and mutates normally; status is unchanged; the shed is reported in `state_retention` and warnings. |
| Tier 5 fires | Same as above, plus a strongest-severity warning naming every shed `wontfix` record by `issue_id` and title. |
| The ladder exhausts every tier and still exceeds budget | `state_overflow`, fail closed at the gate as today, with the complete shed record and final size in the artifact. No mutation. |
| `max_state_bytes` exceeds the resolved backend's hard ceiling | Config-load failure naming both values. Never clamped silently. |
| `min_wontfix_records` is set above zero and tier 5 cannot run far enough | `state_overflow`, fail closed. The operator chose this over forgetting a dismissal. |
| A record is `open` or has a live `discussion_id` | Never shed at any tier, even if the alternative is `state_overflow`. |
| Shed provenance cannot be represented completely | Fail rather than truncate the record of what was dropped. |
| An existing state note predates the new keys | Missing retention keys resolve to their defaults; no migration and no rewrite of historical artifacts. |

## Implementation seams

| Seam | Required change |
| --- | --- |
| `ai-review/src/ai_review/memory.py` — `compact_state` (331) | Add the phase-two shed ladder as a pure function over state plus retention. Re-attach `state_hash` after each tier. Preserve phase-one classification exactly. |
| `ai-review/src/ai_review/memory.py` — `state_overflow_reason` (375) | Return a structured result carrying measured bytes and the record-count check, so the ladder and the artifact share one measurement. Keep the `max_records` check first and unchanged. |
| `ai-review/src/ai_review/memory.py` — `normalize_state` (168) | Apply `keep_run_history` trimming on the normalization path so it holds for every writer. |
| `ai-review/src/ai_review/post.py` — `_merge_aliases` (551) | Apply the §2 per-family cap at merge time, retaining the current run's contribution unconditionally. |
| `ai-review/src/ai_review/post.py` — `_planned_state_payload` (1305) | Leave the append; trimming belongs to compaction so every path is covered. |
| `ai-review/src/ai_review/post.py` — `_process_state_for_persistence` (1328) | Return the shed record alongside the state and overflow reason; resolve retention defaults from the backend. |
| `ai-review/src/ai_review/post.py` — status assignment (2008) | Emit `state_overflow` only when the ladder cannot fit. Attach `state_retention` on both paths. Preserve fail-before-mutation. |
| `ai-review/src/ai_review/config.py` — `STATE_RETENTION_KEYS` (77) | Admit `max_aliases_per_family`, `keep_run_history`, and `min_wontfix_records`. Add backend-ceiling validation. Add no environment override. |
| `ai-review/schemas/post_result.schema.json` | Add the strict `state_retention` object. Keep `state_overflow` in the status enum with its narrowed meaning. |
| `ai-review/src/ai_review/types.py` | Type the retention keys and the shed record. |
| `ai-review/src/ai_review/gate.py` (46) | No behavior change. Add a comment recording that `state_overflow` now means "cannot fit without losing live state", so the fail-closed branch is not later softened by someone reading it as a soft capacity warning. |
| `ai-review/config/review.yaml` (124-130) | Document the new keys and the backend-derived default with the same inline-comment style as the existing block. |
| `docs/configuration.md` (209-213) | Document every new key, the backend-derived default, and the hard ceilings. |
| `docs/operations.md` (63) | Replace "Repair API/state capacity and rerun post/gate" with the real semantics: a lossy shed is a warning, a `state_overflow` means live state would be lost, and the manual reset has a named cost. |

## Tests

- **Ladder ordering:** a state that fits after tier 1 does not run tier 2; each
  tier is exercised in isolation by a state that only that tier can fix; tier 5 is
  never reached while an earlier tier suffices.
- **Never-shed invariants:** an `open` record, a record with a live
  `discussion_id`, and a record with `human_disposition` survive a state that
  cannot otherwise fit, and the result is `state_overflow` rather than a shed.
- **Determinism:** the same input state and configuration produce a
  byte-identical shed state and shed record across repeated runs and across
  process restarts; the retained alias set converges rather than churning when the
  ladder runs on consecutive runs.
- **Alias cap:** the current run's contribution is always retained, including when
  it alone exceeds the cap; the remainder fills in ascending lexicographic order;
  an `exact_issue_id` match still succeeds after aliases were shed; a mutated
  finding whose only surviving link was a shed alias is shown to re-post, proving
  the recall cost is real and bounded rather than assumed away.
- **`run_history`:** trimming to `keep_run_history` on the normal path and to 5 at
  tier 1; ordering keeps the newest entries.
- **Backend ceiling:** `github_pr_comment` with `max_state_bytes: 200000` fails at
  config load naming both values; `gitlab_mr_state_note` accepts it; unset resolves
  to the backend default; a value below the default is honored.
- **Status separation:** a fitting shed leaves the status unchanged and posts
  normally; a non-fitting shed returns `state_overflow`, mutates nothing, and the
  gate still exits nonzero; both carry `state_retention`.
- **Provenance:** `state_retention` is schema-valid on both paths, is present with
  `tiers_applied: []` when nothing fired, lists every shed `wontfix` record in
  full, and fails rather than truncating.
- **Regression against the incident:** a fixture reproducing the observed state —
  20 records at 9 `wontfix` / 6 `open` / 5 `resolved`, encoding to 52,345 bytes —
  fits under the new defaults without reaching tier 3, and posts normally.
- **Compatibility:** a persisted state written without the new keys loads, compacts
  under the defaults, and is not rewritten beyond ordinary compaction.
- **Existing coverage:** `test_post_state_overflow_fails_closed_before_mutation`
  (`tests/unit/test_post.py:2641`) keeps its meaning under the narrowed status, and
  the four fixtures asserting `max_state_bytes: 50000` are updated deliberately
  rather than left to drift from the new defaults.

## Acceptance criteria

- A state that exceeds its budget is shed deterministically and the review still
  posts, unless shedding would discard an `open` record or a live discussion.
- `state_overflow` occurs only in that last case, keeps its fail-closed gate
  behavior, mutates nothing, and carries enough provenance to diagnose from the
  artifact alone.
- Alias families and `run_history` are bounded on the normal path, so a
  long-lived MR's state approaches a ceiling instead of growing without bound.
- A shed `wontfix` record is never silent: it is named in the artifact and in a
  strongest-severity job warning, and an operator can set `min_wontfix_records` to
  refuse the tradeoff.
- `max_state_bytes` cannot be configured above the backend's hard write limit, and
  the GitHub backend's default stays within 65,536 characters.
- The observed incident's state fits under the new defaults without reaching a
  record-shedding tier.
- Documentation states the recovery path for an already-blocked MR and the real
  cost of a manual state-note reset.

## Rollout and compatibility

The new keys default to values that preserve current behavior for any state well
inside its budget: no tier fires, `state_retention` reports an empty
`tiers_applied`, and the encoded state is byte-identical to today's. The raised
GitLab default and the merge-time alias cap are the only changes visible to a
healthy MR, and the alias cap changes only how many historical spellings a record
carries forward.

Land the ladder, the merge-time caps, the config validation, the schema/type
changes, and the documentation together. Splitting them is unsafe in one specific
direction: shipping the raised `max_state_bytes` default without the backend
ceiling validation would let a `github_pr_comment` deployment write a comment body
the platform rejects, turning a clean overflow into an API failure. Publish and
attest the base image, then repin both supported templates to that revision.

**Interim operational relief.** Until this ships, a consumer blocked by
`state_overflow` has no configuration remedy — `AI_REVIEW_CONFIG` points at
`/opt/ai-review/config/review.yaml`, baked into the base image at
`ai-review/images/base.Dockerfile:24`, and no environment variable reaches
`state.retention`. Unblocking a specific MR requires either editing
`ai-review/config/review.yaml`, publishing a new base image, and repinning the
consuming template — a full image round-trip for a one-line value — or deleting
the MR's state note and accepting the reposting cost. That the only two options
are "rebuild the image" and "destroy the review history" is itself the argument
for the escape hatch in §5.

## Relationship to SPEC-22 and SPEC-42

SPEC-22 already assumes this behavior. Its learning-signal design specifies
counters that are "bounded and shed-first under `max_state_bytes` pressure"
(`spec-22-project-rules-and-learning.md:640`) and requires overflow to surface
`state_overflow_reason` and `max_state_bytes` (line 118). No shed-first mechanism
exists for those counters to participate in. This specification builds the ladder
they would slot into; a SPEC-22 counter block would enter as a tier between 1 and
2, above alias shedding, because a counter is cheaper to lose than a match key.

SPEC-42 owns whether a `wontfix` record can clear the merge gate. This
specification does not change that and must not be read as doing so. It only
decides whether such a record may be discarded to fit a byte budget, and answers
that it is the last thing shed, is never shed silently, and can be protected
outright by configuration.
