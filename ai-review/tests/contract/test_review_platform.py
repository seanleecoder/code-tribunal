"""One behavior contract, run against both platform fakes.

GitHub and GitLab have incompatible wire payloads, and this file is deliberately
not about those payloads -- each platform module keeps its own transport tests for
them. What lives here is the product contract every platform must satisfy
identically, asserted once and parameterized over the fakes, so a new platform is
a new entry in ``_FAKES`` rather than a parallel suite that can drift.

The contract:

* fetch version, diff, and current head;
* build single- and multiline positions;
* create and update a thread;
* resolve and reopen a thread;
* create and update the owned state note;
* map platform failures to ``ReviewPlatformError``;
* preserve author identity needed for state and command authorization.

The two human-command cases at the end stay per platform because the mechanics
genuinely differ -- a GitLab reply is a note appended to a discussion, a GitHub
reply is a comment carrying ``in_reply_to_id`` -- and command authorization reads
the author identity out of each shape.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest
from ai_review.commands import collect_human_commands
from ai_review.memory import encode_state_note
from ai_review.platform import ReviewPlatform, ReviewPlatformError
from ai_review.platform.github import GitHubReviewPlatform
from ai_review.platform.gitlab import GitLabReviewPlatform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_github import FakeGitHubClient
from support.fake_gitlab import FakeGitLabClient

ISSUE_ID = "1" * 64
MARKER = (
    f"<!-- ai-review:v1 issue_id={ISSUE_ID} run_id=1 "
    f"body_hash={'a' * 64} source={'b' * 64} -->"
)
DIFF_TEXT = "diff --git a/a.py b/a.py\n"


class _NoopSession:
    def request(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("contract protocol test must not issue HTTP requests")


class _RefusingSession:
    """A session that answers every request with a non-retryable failure.

    403 rather than 500: a retryable status would burn the bounded backoff in
    ``http_retry`` and make this contract slow for no added coverage. Retry and
    backoff behavior has its own suite (`unit/test_http_retry.py`).
    """

    status_code = 403
    text = '{"message": "Forbidden"}'

    def request(self, *args: object, **kwargs: object) -> _RefusingSession:
        return self

    def json(self) -> dict[str, str]:
        return {"message": "Forbidden"}


def _gitlab_fake(**kwargs: Any) -> FakeGitLabClient:
    return FakeGitLabClient(head_sha="1" * 40, diff_text=DIFF_TEXT, **kwargs)


def _github_fake(**kwargs: Any) -> FakeGitHubClient:
    return FakeGitHubClient(head_sha="2" * 40, diff_text=DIFF_TEXT, **kwargs)


# (label, fake factory, project id, adapter factory). The project id differs
# because each platform names its own; nothing in the contract depends on which.
_FAKES = (
    ("gitlab", _gitlab_fake, "project"),
    ("github", _github_fake, "octo/repo"),
)
_ADAPTERS = (
    (
        "gitlab",
        lambda session: GitLabReviewPlatform(
            "https://gitlab.example.com/api/v4", "token", session=session
        ),
    ),
    (
        "github",
        lambda session: GitHubReviewPlatform(
            "https://api.github.com", "token", bot_login="bot", session=session
        ),
    ),
)

_ANCHOR = {
    "old_path": "a.py",
    "new_path": "a.py",
    "side": "new",
    "start": {"new_line": 2},
    "end": {"new_line": 2},
}
_SPANNING_ANCHOR = {
    "old_path": "a.py",
    "new_path": "a.py",
    "side": "new",
    "start": {"new_line": 2},
    "end": {"new_line": 4},
}


def _platform(fake: object) -> ReviewPlatform:
    assert isinstance(fake, ReviewPlatform)
    return cast(ReviewPlatform, fake)


def _open_thread(platform: ReviewPlatform, project: str, body: str = "body") -> dict[str, Any]:
    version = platform.fetch_version(project, 1)
    position = platform.build_position(_ANCHOR, version)
    return dict(platform.create_inline_comment(project, 1, body, position))


@pytest.mark.parametrize(("label", "factory", "project"), _FAKES)
def test_fake_satisfies_the_review_platform_protocol(
    label: str, factory: Any, project: str
) -> None:
    assert isinstance(factory(), ReviewPlatform)


@pytest.mark.parametrize(("label", "adapter_factory"), _ADAPTERS)
def test_adapter_exposes_the_review_platform_protocol(label: str, adapter_factory: Any) -> None:
    assert isinstance(adapter_factory(_NoopSession()), ReviewPlatform)


@pytest.mark.parametrize(("label", "factory", "project"), _FAKES)
def test_platform_fetches_version_diff_and_current_head(
    label: str, factory: Any, project: str
) -> None:
    fake = factory()
    platform = _platform(fake)

    version = platform.fetch_version(project, 1)

    assert version is not None
    assert platform.fetch_diff(project, 1) == DIFF_TEXT
    assert platform.fetch_current_head_sha(project, 1) == fake.head_sha


@pytest.mark.parametrize(("label", "factory", "project"), _FAKES)
def test_platform_builds_single_and_multiline_positions(
    label: str, factory: Any, project: str
) -> None:
    """A multiline position must be retryable as a single line, and collapse cleanly.

    Posting falls back to a single-line comment when a platform rejects the range,
    so `can_retry_as_single_line` and `single_line_position` are the pair that makes
    the fallback possible -- and the collapsed position must no longer look
    retryable, or the fallback would loop.
    """
    platform = _platform(factory())
    version = platform.fetch_version(project, 1)

    single = platform.build_position(_ANCHOR, version)
    assert not platform.can_retry_as_single_line(single)

    ranged = platform.build_position(_SPANNING_ANCHOR, version, multiline=True)
    assert platform.can_retry_as_single_line(ranged)

    collapsed = platform.single_line_position(ranged)
    assert not platform.can_retry_as_single_line(collapsed)
    # Collapsing drops only the range; the location it anchors to is unchanged.
    assert {k: v for k, v in collapsed.items()} == {
        k: v for k, v in ranged.items() if k in collapsed
    }


@pytest.mark.parametrize(("label", "factory", "project"), _FAKES)
def test_platform_creates_and_updates_a_thread(label: str, factory: Any, project: str) -> None:
    platform = _platform(factory())
    thread = _open_thread(platform, project, "first body")

    root_note_id = platform.root_note_id_from_thread(thread)
    assert root_note_id == thread["notes"][0]["id"]

    platform.update_comment(project, 1, str(thread["id"]), root_note_id, "second body")

    threads = platform.list_threads(project, 1)
    updated = next(item for item in threads if str(item["id"]) == str(thread["id"]))
    assert updated["notes"][0]["body"] == "second body"


@pytest.mark.parametrize(("label", "factory", "project"), _FAKES)
def test_platform_resolves_and_reopens_a_thread(label: str, factory: Any, project: str) -> None:
    """Reopen is the other direction of the same call, and it is load-bearing.

    A human `wontfix` resolves a thread and a later reappearance reopens it, so a
    platform that acknowledged `resolved=False` without applying it would strand
    the finding as settled.
    """
    platform = _platform(factory())
    thread = _open_thread(platform, project)
    thread_id = str(thread["id"])

    assert platform.resolve_thread(project, 1, thread_id, True)["resolved"] is True
    assert platform.resolve_thread(project, 1, thread_id, False)["resolved"] is False

    reopened = next(
        item for item in platform.list_threads(project, 1) if str(item["id"]) == thread_id
    )
    assert reopened["resolved"] is False


@pytest.mark.parametrize(("label", "factory", "project"), _FAKES)
def test_platform_creates_and_updates_the_owned_state_note(
    label: str, factory: Any, project: str
) -> None:
    """One machine-owned note per change, updated in place rather than appended.

    A second state note is not a cosmetic duplicate: state recovery has to pick
    one, so creating instead of updating is how a run loses its own history.
    """
    fake = factory()
    platform = _platform(fake)
    assert platform.list_state_notes(project, 1) == []

    created = platform.create_state_note(project, 1, _state_note_body("first-run"))
    assert len(platform.list_state_notes(project, 1)) == 1

    second_body = _state_note_body("second-run")
    platform.update_state_note(project, 1, int(created["id"]), second_body)

    notes = platform.list_state_notes(project, 1)
    assert len(notes) == 1
    assert int(notes[0]["id"]) == int(created["id"])
    assert second_body in str(notes[0]["body"])


@pytest.mark.parametrize(("label", "adapter_factory"), _ADAPTERS)
def test_adapter_maps_platform_failures_to_review_platform_error(
    label: str, adapter_factory: Any
) -> None:
    """Posting handles one error type, so neither adapter may leak its own.

    `ReviewPlatformError` is what `post` catches to report a mutation failure
    instead of crashing; a raw transport or platform-specific exception escaping
    here would surface as an unhandled traceback with no post_result.
    """
    platform = adapter_factory(_RefusingSession())

    with pytest.raises(ReviewPlatformError):
        platform.fetch_diff("project", 1)


@pytest.mark.parametrize(("label", "factory", "project"), _FAKES)
def test_platform_preserves_author_identity_for_state_and_authorization(
    label: str, factory: Any, project: str
) -> None:
    """Two decisions read identity, so both halves of it must survive the boundary.

    State authenticity checks that the machine-owned note was written by the
    posting identity, and command authorization checks the *commenter's* access
    level. Threads must therefore carry a per-note author, and the platform must
    report both its own identity and an arbitrary user's access level.
    """
    fake = factory()
    platform = _platform(fake)

    identity = platform.current_user()
    assert identity["id"] == platform.current_user_id()
    assert platform.member_access_level(project, identity["id"]) == 40

    thread = _open_thread(platform, project)
    author = thread["notes"][0]["author"]
    assert author["id"] == platform.current_user_id()
    assert author["username"]


def _state_note_body(pipeline_id: str) -> str:
    """A real encoded state note.

    Encoded through the production writer rather than hand-written: each platform
    recognizes its own state note by the marker that writer emits, so a
    hand-assembled body would be filtered out and the case would pass by writing
    nothing.
    """
    return encode_state_note(
        {
            "state_schema_version": 1,
            "project_id": "1",
            "merge_request_iid": "2",
            "last_head_sha": "0" * 40,
            "state_note_id": None,
            "written_by_pipeline_id": pipeline_id,
            "updated_at": "2026-06-29T00:00:00Z",
            "records": [],
        }
    )


def test_fake_gitlab_human_command_contract() -> None:
    """A GitLab command arrives as a note appended to the discussion."""
    for access_level, expected in ((40, {ISSUE_ID: "resolve"}), (20, {})):
        fake = FakeGitLabClient(
            head_sha="1" * 40,
            diff_text="",
            access_level=access_level,
        )
        thread = fake.create_inline_comment(
            "project",
            1,
            MARKER,
            {
                "position_type": "text",
                "new_path": "a.py",
                "new_line": 1,
            },
        )
        fake.discussions[0]["notes"].append(
            {
                "id": 101,
                "body": "/ai-review resolve",
                "author": {"id": 7, "username": "reviewer"},
                "created_at": "2026-07-17T00:00:01Z",
            }
        )

        commands = collect_human_commands(fake, "project", fake.list_threads("project", 1))

        assert thread["notes"][0]["body"] == MARKER
        assert commands == expected


def test_fake_github_human_command_contract() -> None:
    """A GitHub command arrives as a reply comment carrying in_reply_to_id."""
    fake = FakeGitHubClient(
        head_sha="2" * 40,
        diff_text="",
        user_permissions={7: 40, 8: 10},
    )
    root = fake.create_inline_comment(
        "octo/repo",
        1,
        MARKER,
        {"path": "a.py", "line": 1, "side": "RIGHT", "commit_id": "2" * 40},
    )
    root_id = int(root["notes"][0]["id"])
    fake.add_reply(root_id, "/ai-review resolve", author_id=8, author_login="reader")
    fake.add_reply(root_id, "/ai-review wontfix", author_id=7, author_login="writer")

    commands = collect_human_commands(fake, "octo/repo", fake.list_threads("octo/repo", 1))

    assert commands == {ISSUE_ID: "wontfix"}
