# Deterministic consensus

Consensus consumes schema-valid, run-bound reviewer evidence and produces one
deterministic `consensus.v2` artifact. Its output is informational: nothing in
it decides whether a change may merge.

## Inputs and eligibility

Each successful finding batch identifies its reviewer/model, run ID,
effective-config digest, accepted and dropped finding counts, and whether the
batch is usable for absence-based resolution. Consensus rejects wrong-run,
duplicate, disabled, malformed, or identity-spoofed evidence. A syntactically
successful batch whose findings were all dropped is not an operational panel
seat and cannot resolve an older finding.

## Grouping and independent support

Findings are grouped on identity that survives rewording — path, category,
anchor side, context hash, fingerprints, and symbol. Grouping carries no
text-similarity signal, and exact grouping is deterministic.

A group's supporters are the unique reviewer identities that back it
independently: its direct contributing reviewers, plus critics whose effective
verdict is `agree` and who contributed no direct finding to the group. One
reviewer contributes at most one support vote however many findings or critiques
it emitted, and no reviewer can corroborate itself by critiquing its own group.

The decision follows one ordered rule, and severity changes none of it:

| Condition | Decision |
|---|---|
| The cross-run state match is ambiguous | `fyi` |
| A majority of eligible independent critics call it noise | `drop` |
| Two or more unique identities support it | `surface` |
| Otherwise | `fyi` |

The order is precedence, not preference. An ambiguous state match stays `fyi`
whatever the support, because no historical thread can be safely chosen to
mutate; majority noise then drops the group before the support threshold is ever
consulted.

A `dispute` is displayed with the group but subtracts no support: surfacing must
never erase dissent. A `duplicate` link affects grouping only. Where one critic
holds several critiques against one final group they collapse to a single
effective verdict by the precedence `noise > dispute > duplicate > agree`, so a
critic that voiced any stronger objection is never counted as an unqualified
supporter. Critique severity downgrades are disabled by default and never cross
the blocker boundary.

## Panel degradation

Panel status reports execution health only. It does not change the support
threshold in either direction: a degraded panel still surfaces a two-supported
finding, and a full panel does not promote a finding only one identity supports.
Absence-based resolution is governed solely by
`panel.min_successful_reviewers_for_resolution` against the resolution-eligible
seats, never by panel status.

| Panel status | Meaning |
|---|---|
| `full` | Every enabled review seat produced a usable batch |
| `degraded` | At least one, but not all, enabled seats produced a usable batch |
| `failed` | No usable operational seat; consensus exits 3 |

Output is stable across input-file ordering. Golden contract cases and unit
tests pin that behavior directly: the shuffled-batch case in
[`test_consensus_state_matching.py`](../../ai-review/tests/unit/test_consensus_state_matching.py),
the reversed-finding case in
[`test_grouping.py`](../../ai-review/tests/unit/test_grouping.py), and the
serialized contract fixtures in
[`test_golden_consensus.py`](../../ai-review/tests/contract/test_golden_consensus.py).
