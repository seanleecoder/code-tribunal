from __future__ import annotations

from ai_review.consensus import build_consensus
from ai_review.schema import adapter_status_artifact, empty_critique_batch, empty_finding_batch


def _config() -> dict:
    return {
        "reviewers": {
            "opencode": {"enabled": True, "model": "model", "timeout_seconds": 600},
            "claude": {"enabled": True, "model": "model", "timeout_seconds": 600},
            "codex": {"enabled": True, "model": "model", "timeout_seconds": 600},
        },
        # The reducer reads nothing else from config on this path. Surfacing is
        # decided by independent support, which is not configurable.
        "panel": {"min_successful_reviewers_for_resolution": 2},
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
        _config(),
    )


def _status(reviewer: str, stage: str, status: str, duration_ms: int, **fields: object) -> dict:
    return adapter_status_artifact(
        reviewer,
        stage,
        status,
        "2026-06-29T00:00:00Z",
        "2026-06-29T00:30:00Z",
        duration_ms,
        f"{stage}/{reviewer}.json",
        run_id="run",
        **fields,  # type: ignore[arg-type]
    )


def degraded_run_health_inputs() -> tuple[list[dict], list[dict], dict]:
    """A degraded panel: one review timeout, one critique without a status."""
    claude = _batch(
        "claude",
        _finding(
            "claude",
            "1" * 64,
            title="Config lookup crashes",
            body="The config lookup raises KeyError for missing required values.",
            context_hash="1" * 64,
            title_fingerprint="a" * 64,
            evidence_fingerprint="b" * 64,
        ),
    )
    claude.update(raw_finding_count=3, dropped_finding_count=1)
    codex = _batch(
        "codex",
        _finding(
            "codex",
            "2" * 64,
            title="Missing config key raises",
            body="A missing required key raises KeyError.",
            context_hash="2" * 64,
            title_fingerprint="a" * 64,
            evidence_fingerprint="c" * 64,
        ),
    )
    opencode = empty_finding_batch(
        "opencode",
        "timeout",
        run_id="run",
        model="model",
        started_at="2026-06-29T00:00:00Z",
        completed_at="2026-06-29T00:00:01Z",
        effective_config_sha256="0" * 64,
    )
    critiques = [
        empty_critique_batch(
            critic,
            "success",
            run_id="run",
            started_at="2026-06-29T00:00:00Z",
            effective_config_sha256="0" * 64,
        )
        for critic in ("claude", "codex")
    ]
    statuses = {
        ("review", "claude"): _status("claude", "review", "success", 724_000),
        ("review", "codex"): _status("codex", "review", "success", 521_000),
        ("review", "opencode"): _status(
            "opencode",
            "review",
            "timeout",
            1_795_012,
            error_class="TimeoutExpired",
            error_message_redacted="reviewer exceeded 1795s",
        ),
        ("critique", "claude"): _status("claude", "critique", "success", 242_000),
        ("critique", "codex"): _status("codex", "critique", "success", 370_000),
    }
    return [claude, codex, opencode], critiques, statuses


def degraded_run_health_config() -> dict:
    config = _config()
    for reviewer in config["reviewers"].values():
        reviewer["timeout_seconds"] = 1800
    config["reviewers"]["claude"]["effort"] = "medium"
    config["critique"] = {
        "enabled": True,
        "blind_reviewer_identity": True,
        "allow_severity_downgrade": False,
    }
    return config


def degraded_run_health_consensus(
    inputs: tuple[list[dict], list[dict], dict] | None = None,
) -> dict:
    """The degraded golden; ``inputs`` lets a test reorder the same artifacts."""
    batches, critiques, statuses = inputs or degraded_run_health_inputs()
    return build_consensus(
        _manifest(),
        batches,
        degraded_run_health_config(),
        critique_batches=critiques,
        statuses=statuses,
    )


GOLDEN_CASES = {
    "default_transitive_split_consensus.json": default_transitive_split_consensus,
    "degraded_run_health_consensus.json": degraded_run_health_consensus,
}
