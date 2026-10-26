from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from ai_review.canonical import canonical_json_text, sha256_hex
from ai_review.memory import (
    attach_state_hash,
    decode_state_note_body,
    empty_state,
    load_state_from_notes,
    normalize_state_record,
)
from ai_review.notes import SUMMARY_MARKER_RE

from tests.support.repository_script import load_repository_script

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "migrate_state_v2.py"
migration = load_repository_script("migrate_state_v2", SCRIPT)


def legacy_note() -> dict:
    records = []
    for number, status in enumerate(("open", "wontfix", "resolved", "stale_unverified"), start=1):
        record = normalize_state_record(
            {
                "issue_id": f"{number:064x}",
                "title": "Existing finding",
                "category": "correctness",
                "discussion_id": f"thread-{number}",
                "root_note_id": number,
                "status": status,
                "human_disposition": "wontfix" if status == "wontfix" else None,
                "aliases": {"source_finding_ids": ["a" * 64], "context_hashes": ["b" * 64]},
                "last_decision": "drop" if status == "stale_unverified" else "surface",
            }
        )
        record["aliases"].update(candidate_issue_signatures=[], title_fingerprints=[], symbols=[])
        records.append(record)
    state = attach_state_hash(
        dict(
            empty_state(project_id="project", merge_request_iid="7", head_sha="old-head"),
            state_schema_version=1,
            state_note_id=10,
            run_history=[{"run_id": "old"}],
            records=records,
        )
    )
    payload = canonical_json_text(state)
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return {
        "id": 10,
        "author": {"id": 42},
        "body": (f"<!-- ai-review-state:v1 {encoded} state_hash={sha256_hex(payload)} -->"),
    }


def convert(notes: list[dict]) -> tuple[int, list[int], str]:
    return migration.migrate_comments(
        notes,
        bot_author_id=42,
        project_id="project",
        change_id="7",
        posting_mode="github_reviews",
    )


def test_migration_preserves_threads_dispositions_and_visible_summary() -> None:
    summary = {
        "id": 20,
        "user": {"id": 42},
        "body": (
            "**Previous review**\n\n<!-- ai-review-summary:v1 run_id=old body_hash="
            + "0" * 64
            + " -->"
        ),
    }
    forged = dict(legacy_note(), id=99, author={"id": 99})
    target, obsolete, body = convert([legacy_note(), summary, forged])
    assert target == 20
    assert obsolete == [10]
    assert body.startswith("**Previous review**")
    assert len(SUMMARY_MARKER_RE.findall(body)) == 1
    state = decode_state_note_body(body)
    assert state["state_schema_version"] == 2
    assert "state_note_id" not in state
    assert "run_history" not in state
    assert [record["discussion_id"] for record in state["records"]] == [
        f"thread-{n}" for n in range(1, 5)
    ]
    assert state["records"][1]["human_disposition"] == "wontfix"
    assert state["records"][2]["status"] == "resolved"
    assert state["records"][3]["last_decision"] == "fyi"
    assert set(state["records"][0]["aliases"]) == {"source_finding_ids", "context_hashes"}
    loaded, _ = load_state_from_notes(
        [{"id": target, "author": {"id": 42}, "body": body}],
        expected_author_id=42,
        project_id="project",
        change_id="7",
        posting_mode="gitlab_discussions",
    )
    assert loaded == state


def test_migration_without_summary_reuses_the_old_note() -> None:
    target, obsolete, body = convert([legacy_note()])
    assert target == 10 and obsolete == []
    assert len(decode_state_note_body(body)["records"]) == 4


@pytest.mark.parametrize(
    "case",
    ["duplicate", "corrupt", "wrong-change", "foreign", "already-migrated", "incomplete-record"],
)
def test_ambiguous_corrupt_or_inapplicable_exports_are_refused(case: str) -> None:
    note = legacy_note()
    notes = [note]
    if case == "duplicate":
        notes.append(dict(note, id=11))
    elif case == "corrupt":
        note["body"] = note["body"].replace("state_hash=", "state_hash=f")
    elif case in {"wrong-change", "incomplete-record"}:
        state = json.loads(base64.urlsafe_b64decode(note["body"].split()[2] + "=="))
        if case == "wrong-change":
            state["project_id"] = "another-project"
        else:
            state["records"][0].pop("issue_id")
        payload = canonical_json_text(attach_state_hash(state))
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        note["body"] = f"<!-- ai-review-state:v1 {encoded} state_hash={sha256_hex(payload)} -->"
    elif case == "foreign":
        note["author"] = {"id": 99}
    else:
        note["body"] = convert(notes)[2]
    with pytest.raises(ValueError):
        convert(notes)


def test_cli_failure_does_not_write_a_migration_update(tmp_path: Path) -> None:
    source = tmp_path / "notes.json"
    source.write_text(json.dumps([legacy_note(), legacy_note()]))
    output = tmp_path / "update.json"
    with pytest.raises(ValueError):
        migration.cli(
            [
                "--notes",
                str(source),
                "--bot-author-id",
                "42",
                "--project-id",
                "project",
                "--change-id",
                "7",
                "--posting-mode",
                "github_reviews",
                "--out",
                str(output),
            ]
        )
    assert not output.exists()


def test_legacy_summary_without_ledger_points_to_manual_repair() -> None:
    notes = [
        {
            "id": 20,
            "author": {"id": 42},
            "body": (
                "Summary\n\n<!-- ai-review-summary:v1 run_id=old body_hash=" + "0" * 64 + " -->"
            ),
        }
    ]
    with pytest.raises(ValueError, match="missing-ledger-repair"):
        convert(notes)
