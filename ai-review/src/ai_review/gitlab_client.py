from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .anchors import gitlab_line_code
from .http_retry import send_with_retries


class GitLabApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class MergeRequestVersion:
    base_sha: str
    start_sha: str
    head_sha: str


def _diff_identity(change: dict[str, Any]) -> tuple[str, str] | None:
    old_path = change.get("old_path")
    new_path = change.get("new_path")
    if not isinstance(old_path, str) or not isinstance(new_path, str):
        return None
    return old_path, new_path


def _diff_path(change: dict[str, Any]) -> str:
    path = change.get("new_path") or change.get("old_path")
    return path if isinstance(path, str) else "<unknown>"


def _line_range_type(anchor: dict[str, Any]) -> str:
    if anchor["side"] == "old":
        return "old"
    return "new"


def _line_range_endpoint(anchor: dict[str, Any], key: str) -> dict[str, Any]:
    line = anchor[key]
    path = anchor["old_path"] if anchor["side"] == "old" else anchor["new_path"]
    return {
        "type": _line_range_type(anchor),
        "old_line": line.get("old_line"),
        "new_line": line.get("new_line"),
        "line_code": line.get("line_code")
        or gitlab_line_code(path, line.get("old_line"), line.get("new_line")),
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


def current_user_id(client: Any) -> int | None:
    current_user_fn = getattr(client, "current_user", None)
    if not callable(current_user_fn):
        return None
    try:
        current_user = current_user_fn()
    except Exception:
        return None
    user_id = current_user.get("id") if isinstance(current_user, dict) else None
    return user_id if isinstance(user_id, int) else None


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
            session = importlib.import_module("requests").Session()
        self.session = session

    def _project(self, project_id_or_path: str | int) -> str:
        return quote(str(project_id_or_path), safe="")

    def _url(self, path: str) -> str:
        if self.api_url.endswith("/api/v4"):
            return self.api_url + path
        return self.api_url + "/api/v4" + path

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers[self.token_header] = self.token
        url = self._url(path)
        # kwargs are captured once for every attempt. Callers must not pass
        # one-shot streaming bodies (e.g. data=<file>) that cannot be re-read.
        return send_with_retries(
            method=method,
            do_request=lambda: self.session.request(method, url, headers=headers, **kwargs),
            get_status=lambda response: int(response.status_code),
            make_http_error=lambda status: GitLabApiError(
                f"GitLab API {method} {path} failed: {status}"
            ),
            make_connection_error=lambda exc: GitLabApiError(
                f"GitLab API {method} {path} failed: connection error: {exc}"
            ),
        )

    @staticmethod
    def _parse(response: Any) -> Any:
        if getattr(response, "status_code", None) == 204 or not getattr(response, "text", ""):
            return None
        return response.json()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._parse(self._send(method, path, **kwargs))

    def _request_object(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        parsed = self._request(method, path, **kwargs)
        if not isinstance(parsed, dict):
            raise GitLabApiError(f"GitLab API {method} {path} response was not an object")
        return dict(parsed)

    @staticmethod
    def _next_page(response: Any) -> int | None:
        """Return GitLab's next page from the X-Next-Page header, or None if absent.

        A present-but-empty header means "last page" and yields 0. When the header is
        missing entirely (e.g. a mocked response) we return None so the caller can fall
        back to a page-length heuristic instead.
        """
        headers = getattr(response, "headers", None)
        if headers is None or not hasattr(headers, "get"):
            return None
        raw = headers.get("X-Next-Page")
        if raw is None:
            return None
        text = str(raw).strip()
        return int(text) if text.isdigit() else 0

    def _get_all_pages(self, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        params = dict(kwargs.pop("params", {}))
        per_page = int(params.pop("per_page", 100))
        items: list[dict[str, Any]] = []
        page = 1
        # Bounded loop guards against a server that ignores paging and always returns
        # a full page; 100 pages * per_page covers any realistic MR. The for/else warns
        # only on true truncation (loop exhausted without a natural break).
        for _ in range(100):
            response = self._send(
                "GET", path, params={**params, "per_page": per_page, "page": page}, **kwargs
            )
            batch = self._parse(response)
            if not batch:
                break
            if not isinstance(batch, list):
                raise GitLabApiError(f"GitLab paginated GET {path} returned a non-list page")
            for item in batch:
                if not isinstance(item, dict):
                    raise GitLabApiError(f"GitLab paginated GET {path} returned a non-object item")
                items.append(dict(item))
            next_page = self._next_page(response)
            if next_page is not None:
                if next_page == 0:
                    break
                page = next_page
            elif len(batch) < per_page:
                break
            else:
                page += 1
        else:
            sys.stderr.write(
                f"ai-review: GitLab pagination cap reached for {path}; "
                f"results truncated at {len(items)} items\n"
            )
        return items

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
        # Prefer the paginated /diffs endpoint over deprecated /changes. GitLab may
        # collapse an otherwise reviewable file in the database-backed response; in
        # that case, recover only the affected entries from Gitaly's raw-diff path.
        change_list = self._get_all_pages(
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/diffs"
        )
        incomplete_indexes = [
            index
            for index, change in enumerate(change_list)
            if change.get("collapsed") or change.get("too_large")
        ]
        if incomplete_indexes:
            primary_identity_counts: dict[tuple[str, str], int] = {}
            for change in change_list:
                identity = _diff_identity(change)
                if identity is not None:
                    primary_identity_counts[identity] = (
                        primary_identity_counts.get(identity, 0) + 1
                    )
            for index in incomplete_indexes:
                incomplete = change_list[index]
                path_name = _diff_path(incomplete)
                identity = _diff_identity(incomplete)
                if identity is None:
                    raise GitLabApiError(
                        f"merge request diff is truncated or collapsed for {path_name}; "
                        "primary diff response did not include text old/new paths"
                    )
                if primary_identity_counts[identity] != 1:
                    raise GitLabApiError(
                        f"merge request diff is truncated or collapsed for {path_name}; "
                        "primary diff response returned duplicate matching changes"
                    )

            changes_path = (
                f"/projects/{self._project(project_id_or_path)}"
                f"/merge_requests/{merge_request_iid}/changes"
            )
            raw_response = self._request(
                "GET", changes_path, params={"access_raw_diffs": "true"}
            )
            first_incomplete = change_list[incomplete_indexes[0]]
            first_path = _diff_path(first_incomplete)
            if not isinstance(raw_response, dict):
                raise GitLabApiError(
                    f"merge request diff is truncated or collapsed for {first_path}; "
                    "raw-diff fallback returned a non-object response"
                )
            # GitLab documents top-level overflow plus per-file collapsed/too_large
            # as the completeness signals. Raw access bypasses database limits, but
            # Gitaly limits can still make the response incomplete.
            if raw_response.get("overflow") is not False:
                raise GitLabApiError(
                    f"merge request diff is truncated or collapsed for {first_path}; "
                    "raw-diff fallback did not prove a non-overflowing response"
                )
            raw_changes = raw_response.get("changes")
            if not isinstance(raw_changes, list) or not all(
                isinstance(change, dict) for change in raw_changes
            ):
                raise GitLabApiError(
                    f"merge request diff is truncated or collapsed for {first_path}; "
                    "raw-diff fallback returned malformed changes"
                )

            raw_by_paths: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for raw_change in raw_changes:
                identity = _diff_identity(raw_change)
                if identity is None:
                    raise GitLabApiError(
                        f"merge request diff is truncated or collapsed for {first_path}; "
                        "raw-diff fallback returned malformed change paths"
                    )
                raw_by_paths.setdefault(identity, []).append(raw_change)

            recovered_paths: list[str] = []
            for index in incomplete_indexes:
                incomplete = change_list[index]
                path_name = _diff_path(incomplete)
                identity = _diff_identity(incomplete)
                if identity is None:
                    raise GitLabApiError(
                        f"merge request diff is truncated or collapsed for {path_name}; "
                        "primary diff response did not include text old/new paths"
                    )
                candidates = raw_by_paths.get(identity, [])
                if not candidates:
                    raise GitLabApiError(
                        f"merge request diff is truncated or collapsed for {path_name}; "
                        "raw-diff fallback returned no matching change"
                    )
                if len(candidates) > 1:
                    raise GitLabApiError(
                        f"merge request diff is truncated or collapsed for {path_name}; "
                        "raw-diff fallback returned multiple matching changes"
                    )
                recovered = candidates[0]
                if recovered.get("collapsed") or recovered.get("too_large"):
                    raise GitLabApiError(
                        f"merge request diff is truncated or collapsed for {path_name}; "
                        "raw-diff fallback remained incomplete"
                    )
                # Empty diff text is valid for binary or metadata-only changes. GitLab's
                # overflow and per-file flags, rather than non-empty text, prove that the
                # raw response is complete enough to accept.
                if not isinstance(recovered.get("diff"), str):
                    raise GitLabApiError(
                        f"merge request diff is truncated or collapsed for {path_name}; "
                        "raw-diff fallback did not return text diff content"
                    )
                change_list[index] = dict(recovered)
                recovered_paths.append(path_name)

            rendered_paths = ", ".join(repr(path) for path in recovered_paths)
            sys.stderr.write(
                f"ai-review: recovered {len(recovered_paths)} GitLab raw diff(s): "
                f"{rendered_paths}\n"
            )

        chunks: list[str] = []
        for change in change_list:
            # This remains a final invariant check after any recovery attempt.
            if change.get("collapsed") or change.get("too_large"):
                path_name = _diff_path(change)
                raise GitLabApiError(
                    f"merge request diff is truncated or collapsed for {path_name}; "
                    "refusing to review an incomplete diff"
                )
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
        return self._request_object(
            "POST",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/discussions",
            json={"body": body, "position": position},
        )

    def list_mr_discussions(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
    ) -> list[dict[str, Any]]:
        return self._get_all_pages(
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/discussions",
        )

    def update_discussion_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        discussion_id: str,
        note_id: int,
        body: str,
    ) -> dict[str, Any]:
        return self._request_object(
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
        resolved: bool = True,
    ) -> dict[str, Any]:
        return self._request_object(
            "PUT",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}"
            f"/discussions/{discussion_id}",
            json={"resolved": resolved},
        )

    def list_mr_notes(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
    ) -> list[dict[str, Any]]:
        return self._get_all_pages(
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/notes",
        )

    def create_mr_note(
        self,
        project_id_or_path: str | int,
        merge_request_iid: str | int,
        body: str,
    ) -> dict[str, Any]:
        return self._request_object(
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
        return self._request_object(
            "PUT",
            f"/projects/{self._project(project_id_or_path)}/merge_requests/{merge_request_iid}/notes/{note_id}",
            json={"body": body},
        )

    def current_user(self) -> dict[str, Any]:
        user = self._request("GET", "/user")
        if not isinstance(user, dict):
            raise GitLabApiError("GitLab current user response was not an object")
        return user

    def project_member_access_level(
        self,
        project_id_or_path: str | int,
        user_id: str | int,
    ) -> int | None:
        member = self._request(
            "GET",
            f"/projects/{self._project(project_id_or_path)}/members/all/{user_id}",
        )
        if not isinstance(member, dict):
            return None
        access_level = member.get("access_level")
        return int(access_level) if isinstance(access_level, int) else None
