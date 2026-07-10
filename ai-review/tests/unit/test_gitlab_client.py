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
    def __init__(
        self, payload: Any, status_code: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = "x"
        self.headers = headers or {}

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
        if url.endswith("/notes"):
            return FakeResponse([])
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

    def test_gitlab_state_and_resolution_methods(self) -> None:
        session = FakeSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        client.list_mr_notes("group/project", 1)
        client.resolve_discussion("group/project", 1, "discussion", False)
        client.current_user()
        client.project_member_access_level("group/project", 99)
        methods = [call[0] for call in session.calls]
        self.assertIn("GET", methods)
        self.assertIn("PUT", methods)
        self.assertTrue(any("/notes" in call[1] for call in session.calls))
        self.assertTrue(
            any(call[2].get("json", {}).get("resolved") is False for call in session.calls)
        )

    def test_list_mr_discussions_follows_pagination(self) -> None:
        # The summary note lives on a later page of a busy MR; without pagination the
        # upsert never sees it and posts a duplicate summary each run.
        class PagedSession:
            def __init__(self) -> None:
                self.pages: dict[int, FakeResponse] = {
                    1: FakeResponse(
                        [{"id": "d1"}, {"id": "d2"}], headers={"X-Next-Page": "2"}
                    ),
                    2: FakeResponse([{"id": "summary-note"}], headers={"X-Next-Page": ""}),
                }
                self.requested_pages: list[int] = []

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                page = int(kwargs["params"]["page"])
                self.requested_pages.append(page)
                return self.pages[page]

        session = PagedSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        discussions = client.list_mr_discussions("group/project", 1)
        self.assertEqual([d["id"] for d in discussions], ["d1", "d2", "summary-note"])
        self.assertEqual(session.requested_pages, [1, 2])

    def test_pagination_stops_on_short_page_without_headers(self) -> None:
        # Mocked responses may omit pagination headers; a short page must end the loop
        # rather than requesting forever.
        class ShortPageSession:
            def __init__(self) -> None:
                self.count = 0

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                self.count += 1
                return FakeResponse([{"id": "only"}])

        session = ShortPageSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        self.assertEqual(len(client.list_mr_discussions("group/project", 1)), 1)
        self.assertEqual(session.count, 1)

    def test_pagination_cap_warns_on_truncation(self) -> None:
        # A server that always advertises a next page must stop at the 100-page cap and
        # warn about truncation rather than looping forever or truncating silently.
        class RunawaySession:
            def __init__(self) -> None:
                self.count = 0

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                self.count += 1
                return FakeResponse([{"id": self.count}], headers={"X-Next-Page": "999"})

        import contextlib
        import io

        session = RunawaySession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            items = client.list_mr_discussions("group/project", 1)
        self.assertEqual(session.count, 100)
        self.assertEqual(len(items), 100)
        self.assertIn("pagination cap reached", stderr.getvalue())

    def test_fetch_mr_diff_degrades_on_empty_response(self) -> None:
        # Bug #8: a 204/empty /changes response makes _request return None; fetch_mr_diff
        # must degrade to an empty diff instead of raising AttributeError.
        class EmptyChangesSession:
            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                response = FakeResponse(None, status_code=204)
                response.text = ""
                return response

        client = GitLabClient(
            "https://gitlab.example.com/api/v4", "token", session=EmptyChangesSession()
        )
        self.assertEqual(client.fetch_mr_diff("group/project", 1), "\n")


if __name__ == "__main__":
    unittest.main()
