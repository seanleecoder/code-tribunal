from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str = "budget_backend_none"


def acquire(
    project_id: str,
    mr_iid: str,
    reviewer: str,
    estimated_cost: float,
    *,
    backend: str = "none",
) -> BudgetDecision:
    if backend == "none":
        return BudgetDecision(True)
    return BudgetDecision(False, "budget_backend_not_implemented")


def release(project_id: str, mr_iid: str, reviewer: str, *, backend: str = "none") -> None:
    return None
