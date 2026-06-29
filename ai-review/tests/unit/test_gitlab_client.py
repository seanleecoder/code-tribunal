from __future__ import annotations

import unittest
from typing import Any

from ai_review.gitlab_client import (
    GitLabClient,
    MergeRequestVersion,
    build_position,
    root_note_id_from_discussion,
)


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = "x"

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if url.endswith("/versions"):
            return FakeResponse(
                [
                    {
                        "id": 1,
                        "base_commit_sha": "base-old",
                        "start_commit_sha": "start-old",
                        "head_commit_sha": "head-old",
                    },
                    {
                        "id": 2,
                        "base_commit_sha": "base",
                        "start_commit_sha": "start",
                        "head_commit_sha": "head",
                    },
                ]
            )
        if "/merge_requests/" in url and method == "GET":
            return FakeResponse({"sha": "head"})
        return FakeResponse({"id": "discussion", "notes": [{"id": 123}]})


class GitLabClientTests(unittest.TestCase):
    def _anchor(self, side: str) -> dict[str, Any]:
        start = {
            "old_line": 10 if side in {"old", "unchanged"} else None,
            "new_line": 12 if side in {"new", "unchanged"} else None,
            "line_code": "line-start",
        }
        end = {
            "old_line": 11 if side in {"old", "unchanged"} else None,
            "new_line": 13 if side in {"new", "unchanged"} else None,
            "line_code": "line-end",
        }
        return {
            "old_path": "src/foo.py",
            "new_path": "src/foo.py",
            "side": side,
            "start": start,
            "end": end,
        }

    def test_build_position_line_rules(self) -> None:
        version = MergeRequestVersion("base", "start", "head")
        self.assertEqual(build_position(self._anchor("new"), version)["new_line"], 12)
        self.assertNotIn("old_line", build_position(self._anchor("new"), version))
        self.assertEqual(build_position(self._anchor("old"), version)["old_line"], 10)
        self.assertNotIn("new_line", build_position(self._anchor("old"), version))
        unchanged = build_position(self._anchor("unchanged"), version)
        self.assertEqual(unchanged["old_line"], 10)
        self.assertEqual(unchanged["new_line"], 12)

    def test_build_position_multiline_adds_line_range(self) -> None:
        position = build_position(self._anchor("new"), MergeRequestVersion("b", "s", "h"), multiline=True)
        self.assertEqual(position["line_range"]["start"]["line_code"], "line-start")
        self.assertEqual(position["line_range"]["end"]["line_code"], "line-end")

    def test_fetch_latest_version_uses_highest_id(self) -> None:
        session = FakeSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        version = client.fetch_latest_mr_version("group/project", 1)
        self.assertEqual(version, MergeRequestVersion("base", "start", "head"))
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertIn("group%2Fproject", url)
        self.assertEqual(kwargs["headers"]["PRIVATE-TOKEN"], "token")

    def test_fetch_current_mr_head_sha(self) -> None:
        session = FakeSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        self.assertEqual(client.fetch_current_mr_head_sha("group/project", 1), "head")

    def test_root_note_id_from_discussion(self) -> None:
        self.assertEqual(root_note_id_from_discussion({"notes": [{"id": 123}]}), 123)


if __name__ == "__main__":
    unittest.main()
