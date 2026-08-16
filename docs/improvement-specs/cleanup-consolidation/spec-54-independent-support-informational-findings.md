# SPEC-54 - Independent-support informational findings

- **Status:** Implemented; retained pending merge to `main`
- **Severity:** High product-policy simplification
- **Effort:** L
- **Historical implementation coordination:** The work was based on `main` at `451472d2` and
  delivered with SPEC-55. It could not land as a standalone change because the former merge gate
  consumed `summary.block_merge`, which this spec deleted while SPEC-55 removed the consumer.
- **Contract changes:** `review_config.v3`, `consensus.v2`

## Objective

Replace the current mixture of direct-review quorum, single-reviewer blocker exceptions,
critique escalation, and merge-blocking policy with one deterministic rule:

> After duplicate grouping, critique-verdict filtering, and safe state-identity handling, a
> finding is eligible to surface when at least two unique reviewer identities independently
> support the grouped issue across direct review and critique.

The two-support threshold is necessary for `surface`, but the two existing hard safety/quality
exceptions remain authoritative and are evaluated first: an ambiguous cross-run state match stays
`fyi` because Code Tribunal cannot safely choose which historical thread to mutate, and majority
independent `noise` drops a group. Ambiguity outranks noise. That order is not a preference — it is
what the current reducer does and what `consensus.schema.json` already asserts by pinning
`decision` to `fyi` for an unassigned group.

All surfaced findings are informational. Severity communicates estimated impact but has no
merge consequence.

## Why

The current reducer decides the same policy twice:

- `decision_for_group()` applies direct-review quorum and a special single-reviewer blocker
  path before critique;
- `_recompute_group_decision()` repeats quorum and severity policy after critique and adds
  an advisory-escalation path.

It also writes `decision` from two further places that neither function owns: the ambiguous
state-match override in the group-construction loop, and the majority-noise drop in critique
application, which sets the decision and skips the recompute entirely. Counting those, a group's
decision has four possible authors. Consolidation has to absorb all four, not the two named
functions.

This creates multiple ways for a finding to become `surface`, several configuration keys
that only exist to tune merge behavior, and consensus fields whose only downstream purpose
is the separate gate.

The desired product is simpler: review and critique jointly determine whether a concern is
well-supported enough to discuss. Maintainers and downstream agents determine whether it
requires a change.

## Final decision

### Independent support

For each final grouped issue:

```text
direct_supporters = unique contributing reviewers in the group
agreeing_critics = unique successful critics whose effective verdict is "agree"
                   and who are not direct contributors to the group
supporters = direct_supporters union agreeing_critics
support_count = size(supporters)
```

Rules:

1. A reviewer contributes at most one direct support vote to a group even when that reviewer
   emitted multiple findings that grouped together.
2. A critic contributes at most one effective critique verdict, and therefore at most one
   critique support vote, to a group.
3. A direct contributor cannot add another vote by critiquing the same group.
4. Only finalized critique batches with `adapter_status == "success"` are eligible. The
   critic's review-stage batch does **not** also need to have succeeded. A reviewer whose
   review-stage batch failed may still provide one independent critique vote when its critique
   batch itself is successful, provenance-valid, and the reviewer is not a direct contributor
   to that group. Review-stage and critique-stage health remain separate evidence.
   This rule **preserves** current behavior rather than introducing it: eligibility is already
   keyed off the critique batch alone, and the shipped CI templates run the critique job even
   when a review seat failed. Keep the behavior and add the regression test that currently
   does not exist.
5. `duplicate` links affect grouping but do not independently count as support.
6. `dispute` is retained and displayed but does not subtract a support vote. Every effective
   minority `dispute` MUST remain attached to the group with its critic identity, rationale,
   and optional adjusted severity even when the group reaches `support_count >= 2` and
   surfaces. Surfacing must never erase dissent.
7. `noise` does not subtract votes one at a time. Existing majority-noise behavior remains:
   when more than half of the eligible independent successful critics classify a group as
   noise, the group is dropped. Define the denominator explicitly, because "eligible" is
   otherwise ambiguous between critics who *could* critique the group and critics who *did*:

   ```text
   eligible_critics = reviewers with a successful finalized critique batch
                      minus the group's direct contributors
   drop when eligible_critics is non-empty
            and effective_noise_count > len(eligible_critics) / 2
   ```

   A critic that never critiqued this group still counts in the denominator. This is the
   current denominator and the current strict inequality; keep both.
8. An ambiguous cross-run state match remains `fyi` regardless of support count, because a
   stable thread identity cannot be assigned safely. Ambiguous groups are excluded from the
   majority-noise disposition entirely, exactly as they are today: a group whose identity
   cannot be assigned is never dropped on critique evidence.
9. The support threshold of two is therefore a necessary but not universally sufficient
   condition for `surface`. Ambiguous-state handling takes precedence over everything, and
   majority-noise takes precedence over the support threshold.

### Decision table

| Condition | Decision |
|---|---|
| State match is ambiguous/unassigned | `fyi` |
| Majority independent critique says noise | `drop` |
| `support_count >= 2` | `surface` |
| Otherwise | `fyi` |

Severity does not alter this table. A single `blocker` finding with no independent support
is `fyi`. A two-support `minor` finding is `surface`.

The table is ordered by precedence. Do not implement it as independent flags evaluated in an
unspecified order. In particular, `support_count >= 2` does not override majority-noise or an
ambiguous state match.

The ambiguous row is first for a contract reason, not a stylistic one.
`ai-review/schemas/consensus.schema.json` already constrains `decision` to the constant `fyi`
whenever `issue_id_source == "ambiguous_unassigned"`, and `consensus.v2` keeps that conditional
unchanged. An implementation that dropped an ambiguous group would fail validation against its
own artifact schema.

### Multiple critiques from one critic in one final group

Grouping can combine multiple source findings after a critic has emitted separate critiques for
them. The current implementation chooses one critique per `(group, critic)` using incidental
sort order. SPEC-54 replaces that with a semantic collapse rule.

For each eligible critic and final group:

1. collect every finalized, valid critique from that critic whose target belongs to the group;
2. normalize invalid `duplicate` verdicts using the existing behavior (they become `dispute`);
3. choose one **effective verdict** using this precedence:

```text
noise > dispute > duplicate > agree
```

This precedence is intentionally conservative. A critic that expresses any stronger objection
to the grouped issue must not simultaneously be counted as an unqualified `agree` supporter
merely because another source finding in the same group received `agree`.

When more than one critique from the same critic has the winning effective verdict, choose one
representative deterministically using the existing stable critique sort key. The representative
provides the rationale and optional adjusted severity for that critic's effective verdict.

Consequences:

- one critic still contributes at most one support identity;
- only an effective `agree` contributes that identity to `agreeing_critics`;
- an effective `dispute` MUST be retained in `critique_disputes` and rendered as dissent;
- an effective `noise` participates once in majority-noise calculation;
- a valid effective `duplicate` participates in grouping semantics but adds no support.

Do not use lexical ordering of verdict strings, rationale text, or source finding IDs as an
implicit policy decision.

### Minority dissent preservation

Dissent is a first-class output, not a failed vote that disappears after consensus. For every
effective `dispute` from an eligible independent critic:

- retain `critic`, `rationale`, and optional `adjusted_severity` in `critique_disputes`;
- preserve deterministic ordering by critic and existing stable tie-breakers;
- render the dissent with the finding wherever the current product renders critique dissent;
- in the coordinated SPEC-55 thread renderer, every surfaced finding MUST continue to show its
  minority dissent section even when the finding has two or more supporters;
- do not reduce `support_count` because of dissent. The human or downstream agent must see both
  the supporting evidence and the minority argument.

One boundary case must be stated, because the current implementation already handles it and the
acceptance criteria below would otherwise contradict the code. An effective `dispute` whose critic
or rationale is blank still suppresses that critic's support and still counts once in
`critique_summary.dispute`, but contributes **no** `critique_disputes` entry. "Retain the
rationale" must not be read as "synthesize a rationale that the critic did not emit".

Example:

```text
Claude   direct finding
Codex    direct finding
OpenCode critique agree
Cursor   critique dispute: "The guard at X already prevents this path"

=> direct supporters: [claude, codex]
=> agreeing critics: [opencode]
=> support_count: 3
=> decision: surface
=> Cursor dissent remains attached and rendered
```

### Panel status

Panel status reports execution health only:

- `full`: every enabled review seat produced a usable review batch;
- `degraded`: at least one, but not all, enabled review seats produced a usable batch;
- `failed`: no enabled review seat produced a usable batch.

Remove `advisory_only`. There is no separate below-blocking-quorum mode after merge policy is
removed.

Panel status does not alter the support threshold. A degraded panel can still surface a finding
when two independent identities support it. Conversely, a full panel does not promote a finding
with only one supporter.

Removing `advisory_only` does **not** loosen absence-based cross-run resolution. Resolution
eligibility is decided solely by `panel.min_successful_reviewers_for_resolution` against
`resolution_eligible_reviewers` in the state-planning module, and has never read `panel_status`.
The panel table in `docs/reference/consensus.md` currently implies the opposite by listing a
resolution column per panel status; correct that table as part of this spec so the collapse of
`advisory_only` into `degraded` is not misread as a policy change.

### Minimum enabled panel size

`review_config.v3` requires at least **three enabled reviewer seats** for every configuration,
including configurations authored directly in YAML.

Two reasons, and the second is why the floor is three rather than two:

1. All critique seats come from the same enabled reviewer roster and self-critique cannot
   corroborate a direct finding. With one enabled seat, `support_count >= 2` is unreachable by
   design, so accepting the configuration would create a panel that can never surface a finding.
2. With exactly two enabled seats, the panel has **zero fault tolerance**: one failed or silently
   degraded seat makes `support_count >= 2` unreachable for every finding, and the run reports an
   empty review that is indistinguishable from a clean one. Silent seat loss is not hypothetical —
   SPEC-41 records a still-open defect where a reviewer that omits `confidence` loses every
   finding. A floor of three keeps a two-support path reachable after one seat is lost.

The shipped configuration already enables exactly three seats (claude, codex, opencode, with
cursor off by default), so this floor does not change the shipped default. It **does** reject
two-seat deployments that are valid today, which is a breaking change for operators and must be
named explicitly in the migration message and the changelog.

This validation applies equally to:

- the shipped YAML configuration;
- `AI_REVIEW_REVIEWERS`;
- per-seat `AI_REVIEW_<REVIEWER>_ENABLED` overrides after all overrides are applied.

Reject a final enabled roster smaller than three with one clear migration/configuration error. Do
not silently lower the support threshold and do not make self-critique count.

The current one-seat compatibility behavior is **three** separate relaxations in the configuration
module, not one. All three must go:

- the roster-size check that is skipped entirely when the document configures fewer than
  `_MINIMUM_PANEL_REVIEWERS` reviewers, which lets a one-name `AI_REVIEW_REVIEWERS` pass silently;
- the threshold clamp floor that collapses to the configured reviewer count instead of the panel
  minimum;
- the explicit `panel.quorum.votes_required` minimum of one when only one seat is enabled. This
  third one disappears with `panel.quorum` itself, so it needs deletion rather than a change.

Raise `_MINIMUM_PANEL_REVIEWERS` from 2 to 3 and re-derive the remaining clamp against it.

## Behavior deltas from current `main`

This spec reads like a rewrite of the surfacing policy, but against the shipped defaults
(`critique.allow_advisory_escalation: true`, `panel.quorum.votes_required: 2`) most decisions are
unchanged: two direct reviewers already surface, and one direct reviewer plus one agreeing critic
already surfaces through advisory escalation. Enumerating the real deltas keeps golden-fixture
churn and the recorded live evidence reviewable instead of unexplained.

1. **Single-reviewer blockers stop surfacing.** A lone `blocker` in `security` or `correctness`
   surfaces today with `human_ack_recommended`; under this spec it is `fyi`. This is the only
   delta that reduces what maintainers see, and it is deliberate.
2. **Thin panels can now surface.** A panel below the old blocking minimum was forced to `fyi`
   under `advisory_only`; after this spec a single successful review seat plus one independent
   agreeing critic reaches `support_count == 2` and surfaces.
3. **Mixed verdicts from one critic change meaning.** Today the surviving critique is chosen by an
   incidental sort key, under which `agree` beats `noise` on the same target; after semantic
   collapse the strongest objection wins.
4. **Severity no longer affects any decision**, in either direction.

Everything else that moves in the fixtures is field removal or footer text, not policy.

## Data contract

### `consensus.v2` group fields

Keep:

- `issue_id` and state-match metadata;
- `decision`;
- `final_severity`;
- `category`, title, body, evidence, suggestion, anchors, and fingerprints;
- `contributing_reviewers` for direct review contributors;
- `critique_summary` and `critique_disputes`; `critique_summary` counts effective per-critic
  verdicts after semantic collapse, not raw critique rows. Today's counters are already one per
  `(group, critic)`, so the totals do not change — only which verdict is counted does;
- source finding and candidate signature identities.

Add:

- `agreeing_critics: string[]`, sorted and unique;
- `support_count: integer`, equal to the size of
  `set(contributing_reviewers) | set(agreeing_critics)`.

Remove:

- `vote_count`;
- `critique_support_count`;
- `critique_noise_count` (available as `critique_summary.noise`);
- `block_merge`;
- `human_ack_recommended`.

Only one of these has a behavioral consumer outside the reducer: `summary.block_merge` drives the
merge gate, which SPEC-55 deletes. `critique_noise_count` is used only inside the majority-noise
calculation, `panel_convergence` is written and never read anywhere in the runtime, and
`vote_count`, `critique_support_count`, and `human_ack_recommended` are read only by the consensus
footer in the body renderer. Removing the rest is an artifact and rendering change, not a
behavioral one — say so in the changelog, because the field list alone reads far more alarming
than the actual effect.

### `consensus.v2` summary

Keep only:

- `surface_count`;
- `fyi_count`;
- `drop_count`.

Remove:

- `block_merge`;
- `panel_convergence`.

The old convergence value was based on direct-review quorum and no longer has a clear
meaning when direct and critique support are intentionally combined.

### Types and schemas

Update together:

- `ai-review/schemas/consensus.schema.json` to `consensus.v2`;
- `ai-review/src/ai_review/types.py`;
- schema/type alignment tests;
- golden consensus fixtures;
- artifact reference documentation.

Do not make the removed fields optional compatibility fields in `consensus.v2`.

`consensus.v2` keeps the `ambiguous_unassigned` conditional in `$defs.group` intact, minus its
`block_merge` clause. Note that the schema/type alignment test does not descend into `allOf`, so a
stale reference to a removed field left inside that conditional would pass every existing check.
Add an explicit assertion that the conditional names only fields that still exist in
`properties`.

## Configuration contract

SPEC-54 through SPEC-57 jointly define `review_config.v3`. SPEC-54 alone defines `consensus.v2`.

Remove from configuration:

- the entire `severity_policy` object;
- `panel.min_successful_reviewers_for_blocking`;
- `panel.quorum`;
- `critique.allow_advisory_escalation`.

Keep:

- `panel.min_successful_reviewers_for_resolution`, because it controls absence-based
  cross-run thread resolution rather than surfacing;
- `critique.enabled`;
- `critique.blind_reviewer_identity`;
- `critique.allow_severity_downgrade`, because severity remains useful information.

Of the three kept `critique` keys, only `enabled` is type-validated today;
`blind_reviewer_identity` and `allow_severity_downgrade` accept any truthy value, so the string
`"false"` silently enables both. v3 is the cheap moment to add the two missing boolean checks
beside the ones already being edited.

The support threshold of two is a product invariant, not an operator setting. Define it once
as a named internal constant in the pure decision module. Do not expose a new
`support_required` configuration key.

After environment overrides are applied, require at least three enabled reviewers. Delete the
legacy validation exception for explicitly authored one-seat configurations; v3 is the breaking
contract boundary where that compatibility ends.

### Migration message ownership

Reject `review_config.v2` with one migration message. Do not accept both v2 and v3 shapes
indefinitely.

The message must eventually enumerate every v3 removal across SPEC-54 through SPEC-57, but SPEC-54
lands first and cannot name removals that do not exist yet. Assign the work rather than leaving it
implied:

- SPEC-54 replaces the existing `review_config.v1` rejection branch with a `review_config.v2`
  branch and contributes its own entries (`severity_policy`, `panel.quorum`,
  `panel.min_successful_reviewers_for_blocking`, `critique.allow_advisory_escalation`, and the
  three-seat floor);
- each later spec in the series appends its own entries to that message;
- SPEC-57, as the last config-changing spec, owns the final consolidated text;
- a test asserts the message names every key removed between v2 and v3, so an appended removal
  without an appended message line fails.

Reuse the mechanism that already exists rather than inventing one: the v1 branch pairs a single
`ConfigError` with a `| v1 key | v2 | Why |` migration table in `CHANGELOG.md` and points at it by
name. Add the v2-to-v3 table the same way; `CHANGELOG.md` is part of this change, not a follow-up.

Two details an implementer will otherwise hit:

- environment overrides are applied **before** config validation, so a document that still sets a
  retired variable reports the env-var error rather than the migration message. That ordering is
  correct; state it so the test expectations are written for it.
- `RETIRED_ENV_OVERRIDES` is documented in-code as a migration aid removable at the next major
  release. v3 is that boundary. Decide explicitly whether the three v1-era entries are dropped or
  carried; do not leave it to whoever edits the file next.

## Implementation requirements

### One pure decision function

Create one pure function, owned by the reducer policy layer, conceptually equivalent to:

```python
def decide_group(*, support_count: int, majority_noise: bool, ambiguous: bool) -> str:
    if ambiguous:
        return "fyi"
    if majority_noise:
        return "drop"
    return "surface" if support_count >= 2 else "fyi"
```

The exact module may be `consensus_policy.py` or an existing pure reducer module. The key
requirement is that both pre-critique construction and post-critique application call the
same function. Delete `decision_for_group()` and `_recompute_group_decision()` after callers
migrate, and fold the two direct-write sites — the ambiguous override and the majority-noise
drop — into the same call. "One implementation of the policy" means one place that assigns
`decision`, not one function that two of four writers happen to use.

### Critique application order

Use this deterministic order:

1. Validate review and critique provenance.
2. Build valid critique duplicate links.
3. Group direct findings.
4. Resolve each group's cross-run state match and set `issue_id` and `issue_id_source`. This step
   is missing from no implementation but was missing from this list: `ambiguous` is an input to
   the decision function, so the point at which it becomes known has to be pinned. An
   `ambiguous_unassigned` group is excluded from steps 6 and 7 and is decided `fyi`.
5. For each `(group, critic)`, semantically collapse all valid critiques targeting source
   findings in that group using `noise > dispute > duplicate > agree`; choose a deterministic
   representative within the winning verdict.
6. Record effective disputes and their rationale before decision calculation so dissent cannot
   be lost when the group surfaces.
7. Compute majority-noise disposition from effective per-critic verdicts.
8. Apply permitted severity adjustment from effective disputes. Only the representative
   critique's `adjusted_severity` participates for a given critic; collapsing multiple critiques
   to one effective verdict also collapses them to one severity request.
9. Compute `agreeing_critics` and `support_count` from effective `agree` verdicts.
10. Call the one decision function.
11. Render body hashes after the final decision, support metadata, and dissent metadata are
    stable.

Critique eligibility depends on the critique batch's own successful finalized state, not on the
same reviewer's review-stage success.

### Sorting and identity

- Sort reviewer IDs lexicographically before writing artifacts.
- Preserve deterministic group and issue identity behavior.
- Critique support must not alter `issue_id` except through already-valid duplicate grouping.
- Golden output must be byte-stable across repeated runs.

### Rendering and body-hash migration

The consensus footer in `render.py` reads `vote_count`, `critique_support_count`, `block_merge`,
and `human_ack_recommended` — every one of them through `group.get(field, default)`. Deleting the
fields therefore does **not** raise: the footer keeps rendering, printing `Direct votes: 0` and
`Blocking: no` on every thread. The renderer must be edited, not merely fed a `consensus.v2` group.

SPEC-55 owns the replacement footer text. SPEC-54 owns the requirement that no defaulted read of a
removed field survives, and must name `render.py` in its own impact list rather than relying on
SPEC-55 to notice.

Two consequences to record in the spec and the changelog:

- **Bump `RENDER_BODY_VERSION` from `render-body.v3` to `render-body.v4`.** The body-hash input
  already includes this version string, and changing the footer without bumping it makes an
  intentional format change indistinguishable from drift.
- **Expect one-time thread churn.** `compute_body_hash` covers the footer, so on the first v3 run
  every pre-existing thread's stored `last_posted_body_hash` compares unequal and the thread is
  updated instead of reported `skipped_unchanged`. This is cosmetic, not corruption: finding
  identity lives in `issue_id` and the alias chain, and the persisted state schema carries none of
  the removed fields, so no state migration is required. Say this in the release notes so an
  operator seeing every thread touched on one run knows it was expected.

## File-level impact

At minimum inspect and update:

- `ai-review/src/ai_review/consensus.py`;
- `ai-review/src/ai_review/critique.py`;
- `ai-review/src/ai_review/grouping.py` only where support metadata intersects grouping;
- `ai-review/src/ai_review/render.py` — the consensus footer and `RENDER_BODY_VERSION`. Omitting
  this file is the one way to ship a silently wrong artifact, because every footer read of a
  removed field is defaulted rather than required;
- `ai-review/src/ai_review/gate.py` and `ai-review/schemas/gate_result.schema.json` — both consume
  `summary.block_merge`, which this spec deleted. SPEC-55 removed them outright. During
  implementation, that coupling required SPEC-54 and SPEC-55 to ship as one change series;
- `ai-review/src/ai_review/types.py`;
- `ai-review/src/ai_review/config.py`;
- `ai-review/src/ai_review/mock_reviewer.py` — its scenario documentation is written in
  quorum/blocking terms, and the `blocking` scenario stops surfacing under delta 1 above, which
  changes the `make consensus-local` output documented in the repository README;
- `ai-review/config/review.yaml`;
- `ai-review/schemas/consensus.schema.json`;
- all consensus, critique, schema, golden, and state-matching tests;
- the one-seat test fixtures invalidated by the three-seat floor. These live outside the consensus
  suites — the adapter-runner, schema-validation, critique-prompt, consensus-integrity, and
  consensus-CLI modules all author single-enabled-seat configurations for convenience;
- `ai-review/tests/contract/golden_cases.py`, whose `_config()` still authors pre-v2
  `severity_policy` and `quorum.mode` keys and bypasses config validation entirely;
- `ai-review/tests/fixtures/golden/render_body_hostile.json`, which is hand-maintained —
  `make update-golden` does **not** regenerate it;
- `docs/configuration.md`. Its key table is enforced: the docs check requires exactly one
  canonical row per leaf key in `review.yaml` and flags documented-but-inert keys, and
  `make quality` runs that check first. Removing YAML keys without removing rows fails the build
  before any test runs;
- `docs/reference/consensus.md`, whose panel-degradation table ties absence-based resolution to
  panel status;
- `README.md`, which describes the panel as "two to four" seats and describes consensus as
  applying quorum and severity policy;
- `CHANGELOG.md`, for the v2-to-v3 migration table the configuration error points at.

## Tests

Add table-driven tests for at least:

| Direct contributors | Agreeing critics | Other critique | Expected |
|---:|---:|---|---|
| 2 | 0 | none | `surface` |
| 1 | 1 | none | `surface` |
| 1 | 0 | none | `fyi` |
| 1 | same reviewer as contributor | none | `fyi` |
| 3 | 0 | majority noise | `drop` even though support is >= 2 |
| 2 | 0 | one dispute | `surface` with dissent retained and rendered |
| 2 | 1 | one different critic disputes | `surface`; agreeing critic counts, minority dissent remains |
| 1 | 1 | `blocker` severity | `surface`, informational only |
| 1 | 0 | `blocker` severity | `fyi` |
| 2 | 0 | ambiguous state match | `fyi` even though support is 2 |
| 3 | 0 | ambiguous state match **and** majority noise | `fyi`; ambiguity outranks noise |
| 1 | 1 critic whose review batch failed | successful critique batch | `surface` |

The existing suite expresses tables as `unittest` loops over tuples with `subTest`. Use that
idiom; do not introduce a second parameterization mechanism for one table.

Also test:

- duplicate grouping cannot count a critic twice;
- multiple direct findings from one reviewer count once;
- unsuccessful critique batches add no support;
- a successful critique batch may contribute support even when that critic's review batch
  failed, provided the critic is independent of the group;
- a direct contributor's critique is excluded even when its critique batch succeeds;
- one critic with both `agree` and `dispute` critiques inside the same final group has effective
  verdict `dispute`, contributes no support, and retains its dissent rationale;
- one critic with `agree` and `noise` inside the same final group has effective verdict `noise`
  and participates once in majority-noise calculation;
- one critic with valid `duplicate` and `agree` inside the same final group follows the declared
  precedence and never gains two effects as two votes;
- tie-breaking among multiple critiques with the same winning verdict is deterministic;
- an effective dispute with a blank rationale suppresses that critic's support and is counted in
  `critique_summary`, but produces no `critique_disputes` entry;
- a final configuration with fewer than three enabled reviewer seats is rejected for YAML, roster,
  and per-seat override paths. Cover one seat **and** two seats: two is the case that is valid
  today and is being taken away;
- the majority-noise denominator includes eligible critics that did not critique the group;
- support threshold never overrides majority-noise or ambiguous state identity, and majority-noise
  never overrides ambiguous state identity;
- every effective dispute survives artifact creation and thread rendering for surfaced groups;
- output order and hashes are deterministic;
- `consensus.v1` is rejected where v2 is required;
- `review_config.v2` is rejected with the migration message, and the message names every key
  removed between v2 and v3. There is no test today asserting that `review_config.v1` is rejected;
  do not repeat that gap;
- the `ambiguous_unassigned` schema conditional references only fields that still exist, since the
  schema/type alignment test does not descend into `allOf`;
- removed configuration keys produce the v3 migration error.

## Acceptance criteria

- There is one implementation of the surface/FYI/drop policy.
- No severity or category changes a surfacing decision.
- No group or summary artifact contains merge-blocking or human-acknowledgement fields.
- A direct vote and an independent agreeing critique can jointly surface a finding.
- One reviewer cannot self-corroborate through critique.
- Majority-noise suppression remains deterministic and takes precedence over the support
  threshold.
- Ambiguous state identity remains FYI and takes precedence over both the support threshold and
  majority-noise suppression, and the artifact still validates against the unchanged
  `ambiguous_unassigned` schema conditional.
- Multiple critiques from one critic in one final group collapse by the documented semantic
  precedence, never by incidental lexical ordering.
- Every effective minority dispute retains critic identity, rationale, and optional adjusted
  severity and remains visible when the group surfaces.
- A successful independent critique may count even when that critic's review-stage batch failed.
- Every final v3 configuration has at least three enabled reviewer seats; none of the three
  one-seat compatibility relaxations remains.
- No renderer, gate, or posting path reads a removed field, whether required or defaulted, and
  `RENDER_BODY_VERSION` is bumped.
- The v2 rejection message names every key removed between v2 and v3, and `CHANGELOG.md` carries
  the matching migration table.
- `make update-golden` and repeated consensus runs produce byte-identical output. The
  hand-maintained hostile-rendering fixture is updated by hand, because `make update-golden` does
  not regenerate it.
- `make quality` passes, including the documentation key-inventory check that runs before the
  tests.

## Non-goals

- Do not add critique-quality status in this spec; that is SPEC-60.
- Do not fix silent reviewer degradation here; that remains SPEC-41. The three-seat floor is a
  tolerance measure against it, not a substitute for it.
- Do not change absence-based cross-run resolution policy. Only the documentation that wrongly
  couples it to panel status changes.
- Do not rename the `blocker` severity. It remains the highest informational impact label.
- Do not change persistent state matching or human commands except for removed artifact fields.
- Do not add unanchored advisory support.
- Do not change provider prompts beyond fields that must be removed from expected output or
  explanatory policy text.
