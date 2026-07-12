from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

from ai_review.platform import ReviewPlatform
from ai_review.platform.gitlab import GitLabReviewPlatform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_gitlab import FakeGitLabClient


def test_fake_gitlab_satisfies_review_platform_protocol() -> None:
    fake = FakeGitLabClient(head_sha="1" * 40, diff_text="diff --git a/a b/a\n")

    assert isinstance(fake, ReviewPlatform)
    platform = cast(ReviewPlatform, fake)
    version = platform.fetch_latest_mr_version("project", 1)
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
    discussion = platform.create_discussion("project", 1, "body", position)
    assert platform.root_note_id_from_discussion(discussion) == 100
    assert platform.current_user_id() == 10


def test_gitlab_adapter_exposes_review_platform_protocol() -> None:
    adapter = GitLabReviewPlatform("https://gitlab.example.com/api/v4", "token")

    assert isinstance(adapter, ReviewPlatform)
