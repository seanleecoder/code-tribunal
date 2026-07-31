# Deterministic consensus

Consensus consumes schema-valid, run-bound reviewer evidence and produces one
deterministic `consensus.v1` artifact. Models do not decide whether a merge is
blocked.

## Inputs and eligibility

Each successful finding batch identifies its reviewer/model, run ID,
effective-config digest, accepted and dropped finding counts, and whether the
batch is usable for absence-based resolution. Consensus rejects wrong-run,
duplicate, disabled, malformed, or identity-spoofed evidence. A syntactically
successful batch whose findings were all dropped is not an operational panel
seat and cannot resolve an older finding.

## Grouping and voting

Findings are normalized by path, category, anchor context, and rendered body.
Exact grouping is deterministic. Optional semantic grouping uses a deterministic
Jaccard threshold and is disabled by default pending corpus calibration.

Quorum policy then determines surfaced, FYI, and blocking groups. Critique may
add supporting or dissenting evidence but cannot add quorum votes in v1.
Critique severity downgrades are disabled by default and never cross the blocker
boundary.

## Critique provenance

Consensus decides, the artifact records, the renderer reads. Each group retains
every selected effective non-agree critique in `critique_observations`: the
critic, the *effective* verdict, the rationale in full, any requested severity
adjustment, and — for a validated duplicate only — the canonical source finding
that justified the merge. A duplicate whose link fails validation is retained as
a dispute and keeps no link. `critique_disputes` is exactly the effective-dispute
projection of that array, not an independent data path.

Retention is unconditional and complete, which is where an audit should look.
Display is separately budgeted, because value per rendered line differs sharply
by verdict; see [revision lifecycle](revision-lifecycle.md).

A group suppressed because a strict majority of eligible critics called it noise
records `drop_reason: "critique_majority_noise"`. The reason is persisted rather
than re-derived because the predicate depends on the set of eligible critics,
which is internal to consensus and never written to the artifact — so no
downstream consumer could recompute it. A missing `drop_reason` means "unknown",
never majority noise.

## Panel degradation

| Panel status | Meaning | Blocking | Absence-based resolution |
|---|---|---:|---:|
| `full` | All enabled operational seats succeeded | yes | yes |
| `degraded` | At least the configured blocking/resolution minimum succeeded | yes, if quorum is met | yes, if resolution minimum is met |
| `advisory_only` | Some evidence exists but blocking minimum is not met | no | no |
| `failed` | No usable operational seat | consensus exits 3 | no |

Output is stable across input-file ordering. Golden contract cases and unit
tests pin that behavior directly: the shuffled-batch case in
[`test_consensus_state_matching.py`](../../ai-review/tests/unit/test_consensus_state_matching.py),
the reversed-finding case in
[`test_grouping.py`](../../ai-review/tests/unit/test_grouping.py), and the
serialized contract fixtures in
[`test_golden_consensus.py`](../../ai-review/tests/contract/test_golden_consensus.py).
