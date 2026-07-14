# Worked Example: A Complete Pipeline Run

This is a concrete, artifact-backed walkthrough of one real GitLab CI pipeline run, showing exactly how Code Tribunal's 6 stages ([README.md](../README.md#6-stage-ci-pipeline-execution-lifecycle)) turn three independent model opinions into a single merge-gate decision. All data below was pulled directly from the pipeline's job artifacts and traces (no simulation).

- **Pipeline**: [`179684`](https://gitlab.example.internal/example-org/downstream-app/-/pipelines/179684)
- **MR**: `!3144`, "Revert \"remove fake bad code\"", source branch `ai-review-smoke-throw-away`
- **Head SHA**: `53f832b0c2ccb201ba2f529247060f1a14c49517`
- **Run ID**: `gl-179684-2529360`
- **Trusted config at run time**: `critique.enabled=true`, `critique.rounds=1`, `allow_advisory_escalation=true`, `allow_severity_downgrade=true` (the permanently-enabled critique config; see [PHASE_5_ACCEPTANCE.md](docs/acceptance/PHASE_5_ACCEPTANCE.md))

## The probe file

The MR adds a single file, `src/foo.py`, deliberately engineered with five labeled traps to exercise every branch of the consensus engine in one pass:

```python
def multi_issue_probe(request, records, db):
      name = request.GET["name"]
      safe_name = db.escape(name)

      # Real blocker
      db.execute("SELECT * FROM users WHERE name = '" + name + "'")

      # False-positive trap: looks similar, but escaped
      db.execute("SELECT * FROM audit WHERE name = '" + safe_name + "'")

      # Ambiguous severity
      sorted_records = sorted(records, key=lambda row: row.get("score", 0))
      best = sorted_records[0] if sorted_records else None

      # Noise bait
      unused_debug_label = "temporary"

      # Duplicate-prone performance issue
      return [db.fetch_user(name) for _ in range(1000)], best
```

## Stage 2 — Independent review (blind fan-out)

`review_claude`, `review_codex`, and `review_opencode` ran in parallel against the same immutable input bundle, each with no visibility into the others' output.

| Job | Reviewer | Model | Findings emitted |
|---|---|---|---|
| `2529361` | claude | `claude-haiku-4.5` | SQL injection (line 6, `blocker`, confidence 0.99) — explicitly noted line 3's `safe_name` escape and that line 9 uses it correctly, but line 6 reuses the unescaped `name`. Repeated `db.fetch_user(name)` in a 1000-iteration list comprehension (line 19, `minor`, confidence 0.75). |
| `2529362` | codex | `openai/gpt-5.4-mini` | SQL string concatenation with untrusted request data (line 6, `blocker`, confidence 0.99). Full sort of `records` just to read the first element (lines 12-13, `minor`, confidence 0.83). |
| `2529363` | opencode | `google/gemini-3.1-flash-lite` | SQL injection vulnerability (line 7, `blocker`, confidence 1.0). Unused variable `unused_debug_label` (line 17, `minor`, confidence 1.0). |

All three reviewers independently caught the real blocker. **None flagged the escaped `audit` query** — the false-positive trap held. Each reviewer's *second* finding covered a different one of the three remaining traps, so the union of all three raw batches (6 findings total) covers every trap in the file except the false positive.

## Stage 3 — Blind cross-examination (critique)

`critique_claude` (job `2529364`), `critique_codex` (`2529365`), and `critique_opencode` (`2529366`) each received the pooled findings from all three reviewers with identities stripped and relabeled `reviewer_A`/`reviewer_B`/`reviewer_C` (`blind_reviewer_identity: true`), then judged every pooled finding — including their own — as `agree`, `disagree`, `duplicate`, or `noise`.

Decoding the anonymized labels against `source_finding_id`:

| Critic | Verdicts issued |
|---|---|
| **claude** | agree → opencode's SQL finding; **noise** → opencode's unused-variable finding ("stylistic cleanup with no concrete defect... below the bar per rules"); agree → its own perf finding; duplicate (of opencode's) → its own SQL finding; duplicate (of opencode's) → codex's SQL finding; agree → codex's sort finding. |
| **codex** | duplicate (of claude's) → opencode's SQL finding; agree → opencode's unused-variable finding; agree → claude's perf finding; agree → claude's SQL finding; duplicate (of claude's) → its own SQL finding. |
| **opencode** | duplicate (of claude's) → its own SQL finding; agree → its own unused-variable finding; agree → claude's perf finding; agree → claude's SQL finding; duplicate (of claude's) → codex's SQL finding; agree → codex's sort finding. |

The only real disagreement in the whole panel: **claude called the unused-variable finding `noise`; codex and opencode called it `agree`.**

## Stage 4 — Consensus (deduplication, voting, and a non-obvious rule)

`consensus_ai_review` (job `2529367`) grouped the 6 raw findings into **4 distinct issues** via canonical context/body hashing (`canonical.py`), then applied quorum voting (`panel.quorum.votes_required: 2`) and the critique verdicts.

There's a rule in `consensus.py` (`_apply_critiques`, line ~375) worth calling out explicitly, because the numbers don't make sense without it: **a critic's verdict on a group is only counted if that critic did not already independently find the same issue.** A reviewer can't cross-examine a defect it discovered itself.

| Issue | Contributing reviewers | Vote count | Critique summary (excludes critics who are contributing reviewers) | Final severity | Decision | `block_merge` |
|---|---|---|---|---|---|---|
| SQL injection | claude, codex, opencode (all 3) | 3 | `agree=0, duplicate=0, noise=0` — **zero because all three reviewers already found it themselves; no outside critic remained to score it** | `blocker` | surface | **true** |
| Repeated `fetch_user` calls | claude | 1 | `agree=2` (codex + opencode) | `minor` | surface | false |
| Full sort for one element | codex | 1 | `agree=2` (claude + opencode) | `minor` | surface | false |
| Unused variable | opencode | 1 | `agree=1` (codex), `noise=1` (claude) | `minor` | surface | false |

The unused-variable group survived rather than being dropped because the drop rule requires `critique_noise_count > len(eligible_critics) / 2`; with 2 eligible critics and only 1 `noise` vote, `1 > 1.0` is false.

The SQL-injection issue reached 3/3 votes against a quorum requirement of 2, so `severity_policy.quorum_blocker.block_merge: true` fired. The three `minor` findings never entered the blocking path at all — their severity alone rules that out regardless of vote count.

Consensus summary: `surface_count=4`, `fyi_count=0`, `drop_count=0`, `block_merge=true`.

## Stage 5 — Post

`post_ai_review` (job `2529368`) upserted one GitLab inline discussion per consensus issue — 4 `created`, 0 `updated`, 0 `skipped_unchanged` (fresh MR run). No summary/FYI comment was needed since `fyi_count=0`.

## Stage 6 — Gate

`ai_review_gate` (job `2529369`) read `consensus.json` and **failed** with `status=failed_blocking_findings`, `reason=blocking_consensus`. This is the correct, intended outcome: the trusted config now runs critique permanently enabled, and this smoke test confirms a real 3/3-quorum blocker still fails the pipeline exactly as before critique was turned on — cross-examination changes *evidence*, not the final merge-gate contract.

## Takeaways

- The panel's value came from *coverage*, not agreement — each reviewer's second finding was different, and only the union across all three caught every planted (non-false-positive) trap.
- The false-positive trap worked: an escaped/safe SQL call sitting one line below the real vulnerability was correctly ignored by all three reviewers.
- Critique's self-exclusion rule means a finding with full 3/3 reviewer consensus gets *zero* critique signal by construction — critique only adds information on findings that a minority of the panel caught.
- The only genuine model disagreement (noise vs. real feedback on an unused variable) did not change the outcome, but it demonstrates the drop-threshold math working as designed rather than dropping on a single dissent.
