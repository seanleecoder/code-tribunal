"""The one implementation of the surface/FYI policy.

Pure and dependency-free on purpose: both pre-critique group construction and
post-critique application assign ``decision`` through ``apply_group_decision``,
so the policy has exactly one author.

A finding is informational. Severity communicates estimated impact and has no
bearing on whether a group surfaces.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

# Product invariant, not an operator setting: a grouped issue surfaces when two
# unique reviewer identities independently support it across direct review and
# critique. Deliberately not a configuration key — an operator lowering it to one
# would turn deterministic consensus back into a passthrough of a single model's
# output, and raising it above the smallest supported panel would make surfacing
# unreachable.
SUPPORT_REQUIRED = 2


def decide_group(
    *, support_count: int, majority_noise: bool, ambiguous: bool
) -> Literal["surface", "fyi"]:
    """Decide one group. The branches are ordered by precedence, not by taste.

    An ambiguous cross-run state match stays ``fyi`` whatever the support: Code
    Tribunal cannot safely choose which historical thread to mutate, and
    ``consensus.schema.json`` pins ``decision`` to ``fyi`` for an unassigned
    group, so any other answer fails validation against its own artifact schema.
    Majority independent noise then demotes the group to ``fyi``: the critics
    judged it low value, not wrong, so it stays visible in the summary without a
    thread. Only after both does the support threshold apply.
    """
    if ambiguous or majority_noise:
        return "fyi"
    return "surface" if support_count >= SUPPORT_REQUIRED else "fyi"


def demoted_by_noise(group: Mapping[str, Any]) -> bool:
    """Whether majority noise alone made this group ``fyi``.

    The inverse of ``decide_group``, kept beside it so the rule has one author:
    the group is ``fyi`` but would have surfaced without the noise vote. The
    artifact carries no field for this, so the summary derives it here.
    """
    return group["decision"] == "fyi" and (
        decide_group(
            support_count=group["support_count"],
            majority_noise=False,
            ambiguous=group["issue_id_source"] == "ambiguous_unassigned",
        )
        == "surface"
    )


def apply_group_decision(
    group: dict[str, Any],
    *,
    agreeing_critics: Iterable[str] = (),
    majority_noise: bool = False,
) -> None:
    """Write ``agreeing_critics``, ``support_count``, and ``decision`` on a group.

    The single assignment site for ``decision``. Callers supply only evidence;
    they never choose an outcome.
    """
    critics = sorted({str(critic) for critic in agreeing_critics})
    supporters = {str(reviewer) for reviewer in group.get("contributing_reviewers", [])} | set(
        critics
    )
    group["agreeing_critics"] = critics
    group["support_count"] = len(supporters)
    group["decision"] = decide_group(
        support_count=len(supporters),
        majority_noise=majority_noise,
        ambiguous=group.get("issue_id_source") == "ambiguous_unassigned",
    )
