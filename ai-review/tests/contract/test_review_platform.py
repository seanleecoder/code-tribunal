from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

from ai_review.platform import ReviewPlatform
from ai_review.platform.github import GitHubReviewPlatform
from ai_review.platform.gitlab import GitLabReviewPlatform
from ai_review.post import collect_human_commands

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_github import FakeGitHubClient
from support.fake_gitlab import FakeGitLabClient


class _NoopSession:
    def request(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("contract protocol test must not issue HTTP requests")


ISSUE_ID = "1" * 64
MARKER = (
    f"<!-- ai-review:v1 issue_id={ISSUE_ID} run_id=1 "
    f"body_hash={'a' * 64} source={'b' * 64} -->"
)


def test_fake_gitlab_satisfies_review_platform_protocol() -> None:
    fake = FakeGitLabClient(head_sha="1" * 40, diff_text="diff --git a/a b/a\n")

    assert isinstance(fake, ReviewPlatform)
    platform = cast(ReviewPlatform, fake)
    version = platform.fetch_version("project", 1)
    position = platform.build_position(
        {
            "old_path": "a.py",
            "new_path": "a.py",
            "side": "new",
            "start": {"new_line": 2},
            "end": {"new_line": 2},
        },
        version,
    )
    assert platform.can_retry_as_single_line({"line_range": {}})
    assert "line_range" not in platform.single_line_position({"line_range": {}, "new_line": 2})
    discussion = platform.create_inline_comment("project", 1, "body", position)
    assert platform.root_note_id_from_thread(discussion) == 100
    assert platform.current_user_id() == 10
    assert platform.member_access_level("project", 10) == 40
    assert platform.list_threads("project", 1)


def test_gitlab_adapter_exposes_review_platform_protocol() -> None:
    adapter = GitLabReviewPlatform(
        "https://gitlab.example.com/api/v4", "token", session=_NoopSession()
    )

    assert isinstance(adapter, ReviewPlatform)


def test_fake_github_satisfies_review_platform_protocol() -> None:
    fake = FakeGitHubClient(head_sha="2" * 40, diff_text="diff --git a/a b/a\n")

    assert isinstance(fake, ReviewPlatform)
    platform = cast(ReviewPlatform, fake)
    version = platform.fetch_version("octo/repo", 1)
    position = platform.build_position(
        {
            "old_path": "a.py",
            "new_path": "a.py",
            "side": "new",
            "start": {"new_line": 2},
            "end": {"new_line": 2},
        },
        version,
    )
    discussion = platform.create_inline_comment("octo/repo", 1, "body", position)
    assert platform.root_note_id_from_thread(discussion) == 1001
    assert platform.current_user_id() == 42
    assert platform.list_threads("octo/repo", 1)


def test_github_adapter_exposes_review_platform_protocol() -> None:
    adapter = GitHubReviewPlatform(
        "https://api.github.com", "token", bot_login="bot", session=_NoopSession()
    )

    assert isinstance(adapter, ReviewPlatform)


def test_fake_gitlab_human_command_contract() -> None:
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
