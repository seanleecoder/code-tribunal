from __future__ import annotations

import json
from typing import Any

from ai_review.memory import encode_state_note
from ai_review.platform.github import STATE_MARKER, GitHubReviewPlatform


class Response:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self) -> Any:
        return self._payload


class Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append((method, url, kwargs))
        if url.endswith("/user"):
            return Response(200, {"id": 42, "login": "bot"})
        if url.endswith("/users/github-actions%5Bbot%5D"):
            return Response(200, {"id": 41898282, "login": "github-actions[bot]"})
        if url.endswith("/issues/7/comments"):
            state = encode_state_note(
                {
                    "state_schema_version": 1,
                    "project_id": "octo/repo",
                    "merge_request_iid": "7",
                    "last_head_sha": "h" * 40,
                    "records": [],
                }
            )
            return Response(
                200,
                [
                    {
                        "id": 1,
                        "body": f"{state}\n\n{STATE_MARKER}",
                        "user": {"id": 42, "login": "bot"},
                    },
                    {
                        "id": 2,
                        "body": f"{state}\n\n{STATE_MARKER}",
                        "user": {"id": 99, "login": "alice"},
                    },
                    {"id": 3, "body": "summary", "user": {"id": 42, "login": "bot"}},
                ],
            )
        if url.endswith("/collaborators/alice/permission"):
            return Response(200, {"permission": "write"})
        raise AssertionError(f"unexpected request: {method} {url}")


class DiffSession:
    def __init__(self, diff: str) -> None:
        self.diff = diff
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append((method, url, kwargs))
        response = Response(200, None)
        response.text = self.diff
        return response


def test_state_notes_are_author_verified_and_normalized() -> None:
    session = Session()
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    notes = platform.list_state_notes("octo/repo", 7)

    assert len(notes) == 1
    assert notes[0]["id"] == 1
    assert notes[0]["author"]["id"] == 42
    assert notes[0]["author"]["username"] == "bot"


def test_member_access_level_maps_github_write_permissions() -> None:
    session = Session()
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    assert platform.member_access_level("octo/repo", "alice") == 40
    assert platform.member_access_level("octo/repo", 99) is None


def test_current_user_uses_configured_bot_login_for_installation_token() -> None:
    session = Session()
    platform = GitHubReviewPlatform(
        "https://api.github.test",
        "token",
        bot_login="github-actions[bot]",
        session=session,
    )

    assert platform.current_user_id() == 41898282
    assert session.calls[-1][1].endswith("/users/github-actions%5Bbot%5D")


def test_fetch_diff_returns_raw_patch_text() -> None:
    diff = "diff --git a/a.py b/a.py\n+print('ok')\n"
    session = DiffSession(diff)
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    assert platform.fetch_diff("octo/repo", 7) == diff
    _, _, kwargs = session.calls[0]
    assert kwargs["headers"]["Accept"] == "application/vnd.github.v3.diff"
