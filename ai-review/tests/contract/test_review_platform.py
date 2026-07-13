from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

from ai_review.platform import ReviewPlatform
from ai_review.platform.github import GitHubReviewPlatform
from ai_review.platform.gitlab import GitLabReviewPlatform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_github import FakeGitHubClient
from support.fake_gitlab import FakeGitLabClient


class _NoopSession:
    def request(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("contract protocol test must not issue HTTP requests")


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
