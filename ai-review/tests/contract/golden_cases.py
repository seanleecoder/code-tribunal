from __future__ import annotations

from ai_review.consensus import build_consensus


def _config(
    *,
    semantic_enabled: bool,
    critique_enabled: bool = False,
    allow_severity_downgrade: bool = False,
) -> dict:
    return {
        "critique": {
            "enabled": critique_enabled,
            "rounds": 1 if critique_enabled else 0,
            "blind_reviewer_identity": True,
            "can_add_quorum_votes": False,
            "allow_advisory_escalation": False,
            "allow_severity_downgrade": allow_severity_downgrade,
        },
        "reviewers": {
            "opencode": {"enabled": True},
            "claude": {"enabled": True},
            "codex": {"enabled": True},
        },
        "panel": {
            "min_successful_reviewers_for_blocking": 2,
            "quorum": {"mode": "absolute", "votes_required": 2},
            "grouping": {"semantic": {"enabled": semantic_enabled, "threshold": 0.2}},
        },
        "severity_policy": {
            "single_reviewer_blocker": {
                "categories": ["security", "correctness"],
                "post": True,
                "block_merge": False,
                "human_ack_recommended": True,
            },
            "quorum_blocker": {"post": True, "block_merge": True},
        },
    }


def _manifest() -> dict:
    return {
        "run_id": "run",
        "project_id": "1",
        "merge_request_iid": "2",
        "head_sha": "h" * 40,
    }


def _anchor(context_hash: str, *, line: int = 10) -> dict:
    return {
        "new_path": "src/foo.py",
        "old_path": "src/foo.py",
        "side": "new",
        "start": {"old_line": None, "new_line": line, "line_code": None},
        "end": {"old_line": None, "new_line": line, "line_code": None},
        "hunk_header": "@@ -1,1 +1,2 @@",
        "context_hash": context_hash,
        "symbol": None,
    }


def _finding(
    reviewer: str,
    source_id: str,
    *,
    title: str,
    body: str,
    context_hash: str,
    title_fingerprint: str,
    evidence_fingerprint: str,
    line: int = 10,
) -> dict:
    return {
        "source_finding_id": source_id,
        "run_local_id": f"{reviewer}-1",
        "anchor": _anchor(context_hash, line=line),
        "severity": "major",
        "category": "correctness",
        "title": title,
        "body": body,
        "evidence": ["config['required']"],
        "suggestion": None,
        "confidence": 0.8,
        "fingerprints": {
            "title_fingerprint": title_fingerprint,
            "evidence_fingerprint": evidence_fingerprint,
        },
        "candidate_issue_signature": {
            "path_key": "src/foo.py",
            "category": "correctness",
            "side": "new",
            "context_hash": context_hash,
            "title_fingerprint": title_fingerprint,
            "symbol": None,
        },
    }


def _batch(reviewer: str, finding: dict) -> dict:
    findings = [finding]
    return {
        "schema_version": "finding_batch.v1",
        "run_id": "run",
        "reviewer": reviewer,
        "adapter_status": "success",
        "model": "model",
        "started_at": "2026-06-29T00:00:00Z",
        "completed_at": "2026-06-29T00:00:01Z",
        "raw_finding_count": len(findings),
        "accepted_finding_count": len(findings),
        "dropped_finding_count": 0,
        "usable_for_resolution": True,
        "effective_config_sha256": "0" * 64,
        "findings": findings,
    }


def _critique(
    critic: str,
    target: str,
    verdict: str,
    *,
    rationale: str,
    duplicate_of: str | None = None,
    adjusted_severity: str | None = None,
) -> dict:
    return {
        "target_source_finding_id": target,
        "critic": critic,
        "verdict": verdict,
        "duplicate_of_source_finding_id": duplicate_of,
        "rationale": rationale,
        "adjusted_severity": adjusted_severity,
        "confidence": 0.8,
    }


def _critique_batch(critic: str, critiques: list[dict]) -> dict:
    return {
        "schema_version": "critique_batch.v1",
        "run_id": "run",
        "critic": critic,
        "adapter_status": "success",
        "effective_config_sha256": "0" * 64,
        "critiques": critiques,
    }


def _build(
    finding_batches: list[dict],
    config: dict,
    critique_batches: list[dict] | None = None,
    *,
    reverse: bool = False,
) -> dict:
    """Build one case, optionally with every input list reversed.

    Reversal covers both axes an adapter can vary: the batch order the CLI reads
    from ``sorted(glob(...))`` and the critique order inside a batch.
    """

    if reverse:
        finding_batches = list(reversed(finding_batches))
        critique_batches = [
            {**batch, "critiques": list(reversed(batch["critiques"]))}
            for batch in reversed(critique_batches or [])
        ] or None
    return build_consensus(_manifest(), finding_batches, config, critique_batches=critique_batches)


def _guard_finding(reviewer: str, source_id: str, *, line: int = 10, seed: str = "1") -> dict:
    return _finding(
        reviewer,
        source_id,
        title=f"Config lookup lacks a guard at line {line}",
        body="The config lookup raises KeyError when required values are absent.",
        context_hash=seed * 64,
        title_fingerprint=seed * 64,
        evidence_fingerprint=seed * 64,
        line=line,
    )


def valid_duplicate_consensus(*, reverse: bool = False) -> dict:
    """A third-party duplicate link merges two findings that would not group."""

    first = _guard_finding("claude", "1" * 64, line=10, seed="1")
    second = _guard_finding("codex", "2" * 64, line=100, seed="2")
    return _build(
        [_batch("claude", first), _batch("codex", second)],
        _config(semantic_enabled=False, critique_enabled=True),
        [
            _critique_batch(
                "opencode",
                [
                    _critique(
                        "opencode",
                        "1" * 64,
                        "duplicate",
                        duplicate_of="2" * 64,
                        rationale="Both reports describe the same absent-value lookup.",
                    )
                ],
            )
        ],
        reverse=reverse,
    )


def invalid_duplicate_consensus(*, reverse: bool = False) -> dict:
    """An unresolvable duplicate link is retained as an effective dispute."""

    return _build(
        [_batch("claude", _guard_finding("claude", "1" * 64))],
        _config(semantic_enabled=False, critique_enabled=True, allow_severity_downgrade=True),
        [
            _critique_batch(
                "opencode",
                [
                    _critique(
                        "opencode",
                        "1" * 64,
                        "duplicate",
                        duplicate_of="f" * 64,
                        adjusted_severity="minor",
                        rationale="Already covered by a report that is not in this run.",
                    )
                ],
            )
        ],
        reverse=reverse,
    )


def ordinary_noise_consensus(*, reverse: bool = False) -> dict:
    """One noise critique out of two eligible critics leaves the group standing."""

    return _build(
        [_batch("claude", _guard_finding("claude", "1" * 64))],
        _config(semantic_enabled=False, critique_enabled=True),
        [
            _critique_batch(
                "codex",
                [
                    _critique(
                        "codex",
                        "1" * 64,
                        "noise",
                        rationale="Style preference, not actionable here. The guard is fine.",
                    )
                ],
            ),
            _critique_batch(
                "opencode",
                [_critique("opencode", "1" * 64, "agree", rationale="Reproduced from the diff.")],
            ),
        ],
        reverse=reverse,
    )


def majority_noise_consensus(*, reverse: bool = False) -> dict:
    """A strict majority of eligible critics suppresses the group."""

    return _build(
        [_batch("claude", _guard_finding("claude", "1" * 64))],
        _config(semantic_enabled=False, critique_enabled=True),
        [
            _critique_batch(
                "codex",
                [_critique("codex", "1" * 64, "noise", rationale="Not worth a maintainer's time.")],
            ),
            _critique_batch(
                "opencode",
                [_critique("opencode", "1" * 64, "noise", rationale="Agreed, this is noise.")],
            ),
        ],
        reverse=reverse,
    )


def semantic_consensus() -> dict:
    first = _finding(
        "claude",
        "1" * 64,
        title="Missing None guard before config lookup",
        body="The config lookup raises KeyError when required values are absent.",
        context_hash="1" * 64,
        title_fingerprint="a" * 64,
        evidence_fingerprint="b" * 64,
    )
    second = _finding(
        "codex",
        "2" * 64,
        title="Config lookup lacks guard for absent values",
        body="Required values that are absent make the config lookup raise KeyError.",
        context_hash="2" * 64,
        title_fingerprint="c" * 64,
        evidence_fingerprint="d" * 64,
    )
    return build_consensus(
        _manifest(),
        [_batch("claude", first), _batch("codex", second)],
        _config(semantic_enabled=True),
    )


def default_transitive_split_consensus() -> dict:
    hub = _finding(
        "claude",
        "1" * 64,
        title="Config lookup hub",
        body="This middle finding overlaps both neighbors but should not join unrelated endpoints.",
        context_hash="1" * 64,
        title_fingerprint="a" * 64,
        evidence_fingerprint="b" * 64,
        line=12,
    )
    left = _finding(
        "codex",
        "2" * 64,
        title="Null config access crashes",
        body="The config lookup raises KeyError for missing required values.",
        context_hash="2" * 64,
        title_fingerprint="a" * 64,
        evidence_fingerprint="c" * 64,
        line=10,
    )
    right = _finding(
        "opencode",
        "3" * 64,
        title="SQL query builds raw user input",
        body="The database query concatenates untrusted user input into SQL text.",
        context_hash="3" * 64,
        title_fingerprint="d" * 64,
        evidence_fingerprint="b" * 64,
        line=14,
    )
    return build_consensus(
        _manifest(),
        [_batch("claude", hub), _batch("codex", left), _batch("opencode", right)],
        _config(semantic_enabled=False),
    )


GOLDEN_CASES = {
    "semantic_consensus.json": semantic_consensus,
    "default_transitive_split_consensus.json": default_transitive_split_consensus,
    "valid_duplicate_consensus.json": valid_duplicate_consensus,
    "invalid_duplicate_consensus.json": invalid_duplicate_consensus,
    "ordinary_noise_consensus.json": ordinary_noise_consensus,
    "majority_noise_consensus.json": majority_noise_consensus,
}

# Cases whose inputs are re-fed in a different batch/critique order to assert that
# canonical output does not depend on adapter file enumeration or response order.
ORDER_INDEPENDENCE_CASES = {
    "valid_duplicate_consensus": valid_duplicate_consensus,
    "invalid_duplicate_consensus": invalid_duplicate_consensus,
    "ordinary_noise_consensus": ordinary_noise_consensus,
    "majority_noise_consensus": majority_noise_consensus,
}
