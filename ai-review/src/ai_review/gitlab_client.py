from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote
from typing import Any

from .anchors import gitlab_line_code


class GitLabApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeRequestVersion:
    base_sha: str
    start_sha: str
    head_sha: str


def _line_range_type(anchor: dict[str, Any]) -> str:
    if anchor["side"] == "old":
        return "old"
    return "new"


def _line_range_endpoint(anchor: dict[str, Any], key: str) -> dict[str, Any]:
    line = anchor[key]
    return {
        "type": _line_range_type(anchor),
        "old_line": line.get("old_line"),
        "new_line": line.get("new_line"),
        "line_code": line.get("line_code")
        or gitlab_line_code(anchor["new_path"], line.get("old_line"), line.get("new_line")),
    }


def build_position(
    anchor: dict[str, Any],
    version: MergeRequestVersion,
    *,
    multiline: bool = False,
) -> dict[str, Any]:
    start = anchor["start"]
    position = {
        "position_type": "text",
        "base_sha": version.base_sha,
        "start_sha": version.start_sha,
        "head_sha": version.head_sha,
        "old_path": anchor["old_path"],
        "new_path": anchor["new_path"],
    }
    if anchor["side"] == "new":
        position["new_line"] = start["new_line"]
    elif anchor["side"] == "old":
        position["old_line"] = start["old_line"]
    else:
        position["old_line"] = start["old_line"]
        position["new_line"] = start["new_line"]
    if multiline and anchor.get("start") != anchor.get("end"):
        position["line_range"] = {
            "start": _line_range_endpoint(anchor, "start"),
            "end": _line_range_endpoint(anchor, "end"),
        }
    return position


def line_code_for_position(path: str, old_line: int | None, new_line: int | None) -> str:
    return gitlab_line_code(path, old_line, new_line)


def root_note_id_from_discussion(response: dict[str, Any]) -> int:
    notes = response.get("notes")
    if not isinstance(notes, list) or not notes:
        raise GitLabApiError("GitLab discussion response did not include root note")
    note_id = notes[0].get("id")
    if not isinstance(note_id, int):
        raise GitLabApiError("GitLab discussion root note did not include integer id")
    return note_id


class GitLabClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        token_header: str = "PRIVATE-TOKEN",
        session: Any | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.token_header = token_header
        if session is None:
            import requests  # type: ignore[import-not-found]

            session = requests.Session()
        self.session = session

    def _project(self, project_id_or_path: str | int) -> str:
        return quote(str(project_id_or_path), safe="")

    def _url(self, path: str) -> str:
        if self.api_url.endswith("/api/v4"):
            return self.api_url + path
        return self.api_url + "/api/v4" + path

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers[self.token_header] = self.token
        response = self.session.request(method, self._url(path), headers=headers, **kwargs)
        if response.status_code >= 400:
            raise GitLabApiError(f"GitLab API {method} {path} failed: {response.status_code}")
        if response.status_code == 204 or not getattr(response, "text", ""):
            return None
        return response.json()

    def fetch_latest_mr_version(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
    ) -> MergeRequestVersion:
        versions = self._request(
            "GET",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/versions",
        )
        if not isinstance(versions, list) or not versions:
            raise GitLabApiError("merge request has no versions")
        latest = sorted(versions, key=lambda item: int(item.get("id", 0)), reverse=True)[0]
        return MergeRequestVersion(
            base_sha=str(latest["base_commit_sha"]),
            start_sha=str(latest["start_commit_sha"]),
            head_sha=str(latest["head_commit_sha"]),
        )

    def fetch_mr_diff(self, project_id_or_path: str | int, merge_request_iid: str | int) -> str:
        changes = self._request(
            "GET",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/changes",
        )
        chunks: list[str] = []
        for change in changes.get("changes", []):
            old_path = change.get("old_path") or change.get("new_path")
            new_path = change.get("new_path") or change.get("old_path")
            chunks.append(f"diff --git a/{old_path} b/{new_path}")
            chunks.append(f"--- a/{old_path}")
            chunks.append(f"+++ b/{new_path}")
            chunks.append(str(change.get("diff", "")))
        return "\n".join(chunks).rstrip() + "\n"

    def fetch_current_mr_head_sha(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
    ) -> str:
        mr = self._request(
            "GET",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}",
        )
        if not isinstance(mr, dict):
            raise GitLabApiError("merge request response was not an object")
        head_sha = mr.get("sha") or mr.get("diff_refs", {}).get("head_sha")
        if not head_sha:
            raise GitLabApiError("merge request response did not include current head SHA")
        return str(head_sha)

    def create_discussion(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        body: str,
        position: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/discussions",
            json={"body": body, "position": position},
        )

    def list_mr_discussions(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
    ) -> list[dict[str, Any]]:
        discussions = self._request(
            "GET",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/discussions",
        )
        if not isinstance(discussions, list):
            raise GitLabApiError("GitLab discussions response was not a list")
        return discussions

    def update_discussion_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        discussion_id: str,
        note_id: int,
        body: str,
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}"
            f"/discussions/{discussion_id}/notes/{note_id}",
            json={"body": body},
        )

    def resolve_discussion(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        discussion_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}"
            f"/discussions/{discussion_id}",
            json={"resolved": True},
        )

    def create_mr_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        body: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/notes",
            json={"body": body},
        )

    def update_mr_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        note_id: int,
        body: str,
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/notes/{note_id}",
            json={"body": body},
        )
