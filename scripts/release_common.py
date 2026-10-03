"""Shared release identity, Git, and canonical-workflow helpers."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, overload

ROOT = Path(__file__).resolve().parents[1]
RELEASE_INPUTS = ROOT / "release/release-inputs.json"


class ReleaseValidationError(ValueError):
    """Raised when release metadata violates its checked contract."""


# Canonical template -> installed copy. GitHub only executes workflows that are
# real files under .github/workflows, so the installed copy must stay a byte
# duplicate of the canonical template rather than a symlink to it.
WORKFLOW_PAIRS: tuple[tuple[str, str], ...] = (
    ("ai-review/ci/review.github-actions.yml", ".github/workflows/ai-review.yml"),
)


def sync_workflows(*, check: bool, root: Path = ROOT) -> tuple[str, ...]:
    """Copy canonical workflows over their installed copies, or report drift.

    In check mode nothing is written and the installed paths that differ from
    their canonical template are returned. In write mode each mismatching
    installed copy is overwritten and the paths that changed are returned. An
    empty tuple therefore means "already in sync" in both modes.

    The comparison is byte-exact, and deliberately so. GitHub executes the
    installed file verbatim, so a line-ending difference is real drift rather
    than an equivalent encoding. Path.read_text() enables universal newlines and
    translates \r\n to \n, which would report a CRLF installed copy as identical
    to an LF template and then decline to repair it; Path.write_text() is the
    mirror problem, translating \n to os.linesep. Reading and writing bytes is
    what makes the byte-duplicate contract above literally true.

    This is the only implementation of the comparison. Copies previously lived in
    check_supply_chain_pins.py, check_release_inputs.py, and test_ci_template.py;
    none of them could repair the drift they reported, and the one in
    check_supply_chain_pins.py ran inside the base image, where .github/ does not
    exist and it therefore always passed. Draft quality uses `make workflow-parity`; active
    release inputs also call this comparison.
    """
    changed: list[str] = []
    for canonical_rel, installed_rel in WORKFLOW_PAIRS:
        canonical_path = root / canonical_rel
        installed_path = root / installed_rel
        if not canonical_path.is_file():
            raise ReleaseValidationError(f"canonical workflow template is missing: {canonical_rel}")
        canonical_bytes = canonical_path.read_bytes()
        installed_bytes = installed_path.read_bytes() if installed_path.is_file() else None
        if installed_bytes == canonical_bytes:
            continue
        changed.append(installed_rel)
        if not check:
            installed_path.parent.mkdir(parents=True, exist_ok=True)
            installed_path.write_bytes(canonical_bytes)
    return tuple(changed)


# Current tooling has one contract; historical releases retain validators at their tags.
RELEASE_INPUTS_SCHEMA_VERSION = "code_tribunal.release_inputs.v3"

# Image tag series: images are tagged `<series>-<runtime_source>`. It must equal
# IMAGE_VERSION in .github/workflows/publish-ai-review-images.yml.
IMAGE_TAG_SERIES = "2.0"

ALLOWED_RELEASE_PATHS = (
    ".github/workflows/ai-review.yml",
    "ai-review/ci/review.github-actions.yml",
    "ai-review/ci/review.gitlab-ci.yml",
    "CHANGELOG.md",
    "docs/evidence/",
    "docs/improvement-specs/",
    "release/",
)

FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
RELEASE_VERSION_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
IMAGE_NAME_RE = re.compile(r"ghcr\.io/[a-z0-9._/-]+/ai-review-(?:base|reviewer)")
PLACEHOLDER_RE = re.compile(
    r"(?<![A-Za-z])(?:TODO|TBD|REPLACE(?:[-_]ME)?|sha256:replace-me)(?![A-Za-z])", re.I | re.A
)


def without_fenced_code(text: str) -> str:
    """Mask CommonMark fences, retaining offsets and line endings for callers."""
    output: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        if fence is None:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if opening is None:
                output.append(line)
                continue
            fence = opening.group(1)
        elif re.fullmatch(
            rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", line.rstrip("\r\n")
        ):
            fence = None
        output.append(re.sub(r"[^\r\n]", " ", line))
    return "".join(output)


def mask_markdown(text: str) -> str:
    """Mask fences before comments, preserving every offset and line ending."""
    text = without_fenced_code(text)
    return re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\r\n]", " ", m[0]), text, flags=re.S)


def markdown_headings(masked: str) -> list[tuple[str, int, int]]:
    """Return H2 headings and original offsets from already-masked Markdown."""
    return [
        (match[1], match.start(), match.end())
        for match in re.finditer(
            r"(?m)^ {0,3}##[ \t]+([^\r\n]+?)(?:[ \t]+#+)?[ \t]*(?:\r?\n|$)", masked
        )
    ]


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def load_json(path: Path) -> dict[str, Any]:
    # Identity/workflow helpers also run standalone without the runtime import path.
    from ai_review.canonical import json_loads_no_duplicates

    try:
        value = json_loads_no_duplicates(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseValidationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseValidationError(f"{path} must contain a JSON object")
    return value


def gh(*args: str, error_type: type[Exception] = ReleaseValidationError) -> str:
    """Run GitHub CLI once, preserving the caller's error boundary."""
    completed = subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
    if completed.returncode:
        raise error_type(completed.stderr.strip() or f"gh {args[0]} failed")
    return completed.stdout


def successful_run(source: str, workflow: str, *, repository: str) -> str:
    """Require a successful canonical main push run for exactly this commit."""
    from ai_review.canonical import json_loads_no_duplicates

    response = gh(
        "run",
        "list",
        "--repo",
        repository,
        "--workflow",
        workflow,
        "--commit",
        source,
        "--branch",
        "main",
        "--event",
        "push",
        "--limit",
        "1",
        "--json",
        "databaseId,headSha,headBranch,event,status,conclusion",
    )
    try:
        runs = json_loads_no_duplicates(response)
    except ValueError as exc:
        raise ReleaseValidationError(f"invalid {workflow} run response: {exc}") from exc
    if not isinstance(runs, list) or len(runs) != 1:
        raise ReleaseValidationError(f"no canonical {workflow} push run found for {source}")
    run = runs[0]
    if not isinstance(run, dict) or (
        run.get("headSha") != source
        or run.get("headBranch") != "main"
        or run.get("event") != "push"
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or type(run.get("databaseId")) is not int
        or run["databaseId"] <= 0
    ):
        raise ReleaseValidationError(
            f"{workflow} requires completed successful canonical push CI for exactly {source}"
        )
    return str(run["databaseId"])


def image_ref(image: dict[str, Any], runtime_source: str) -> str:
    return f"{image['name']}:{IMAGE_TAG_SERIES}-{runtime_source}@{image['digest']}"


def _diff_paths(root: Path, *revisions: str, cached: bool = False) -> list[str]:
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--no-renames",
            "--name-only",
            "-z",
            "--diff-filter=ACDMRTUXB",
            *(["--cached"] if cached else []),
            *revisions,
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode:
        raise ReleaseValidationError(completed.stderr.strip() or "git diff failed")
    return sorted(filter(None, completed.stdout.split("\0")))


def git_changed_paths(runtime_source: str, release_commit: str, root: Path = ROOT) -> list[str]:
    return _diff_paths(root, runtime_source, release_commit)


def git_is_ancestor(runtime_source: str, release_commit: str, root: Path = ROOT) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", runtime_source, release_commit],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise ReleaseValidationError(completed.stderr.strip() or "git merge-base failed")


def tag_exists(tag: str, root: Path = ROOT) -> bool:
    """Whether `tag` resolves in this checkout.

    Deliberately distinct from "can I read a path at that tag". A release note is
    frozen because its tag exists; a `git show <tag>:<path>` failure could equally
    mean the tag is missing *or* that the note was never in it, and collapsing the
    two is what let an untagged note escape both the link check and the byte check.
    """
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


def any_tags_resolvable(root: Path = ROOT) -> bool:
    """Whether tag lookups mean anything here at all.

    False in a `--no-tags` or shallow clone, and where git is unavailable. Callers
    use it to tell "this note has no tag" apart from "this checkout knows about no
    tags", which are opposite situations: the first is a note still being drafted,
    the second is an environment that cannot answer the question.
    """
    completed = subprocess.run(
        ["git", "-C", str(root), "tag", "--list"],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def validate_release_version(value: object) -> str:
    if not isinstance(value, str) or not RELEASE_VERSION_RE.fullmatch(value):
        raise ReleaseValidationError(
            "release_version must be a semantic version in MAJOR.MINOR.PATCH format "
            "with an optional prerelease suffix such as 1.0.1-rc.1; build metadata "
            "is not supported"
        )
    prerelease = value.partition("-")[2]
    if any(
        part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")
    ):
        raise ReleaseValidationError(
            "numeric prerelease identifiers must not contain leading zeros"
        )
    return value


def compare_release_versions(left: str, right: str) -> int:
    """Compare accepted versions by SemVer precedence."""
    left_core, _, left_pre = validate_release_version(left).partition("-")
    right_core, _, right_pre = validate_release_version(right).partition("-")
    left_numbers = tuple(map(int, left_core.split(".")))
    right_numbers = tuple(map(int, right_core.split(".")))
    if left_numbers != right_numbers:
        return (left_numbers > right_numbers) - (left_numbers < right_numbers)
    if not left_pre or not right_pre:
        return bool(right_pre) - bool(left_pre)
    left_parts, right_parts = left_pre.split("."), right_pre.split(".")
    for left_part, right_part in zip(left_parts, right_parts, strict=False):
        if left_part.isdigit() and right_part.isdigit():
            left_value, right_value = int(left_part), int(right_part)
        elif left_part.isdigit() != right_part.isdigit():
            return -1 if left_part.isdigit() else 1
        else:
            left_value, right_value = left_part, right_part
        if left_value != right_value:
            return (left_value > right_value) - (left_value < right_value)
    return (len(left_parts) > len(right_parts)) - (len(left_parts) < len(right_parts))


def disallowed_release_paths(paths: list[str]) -> list[str]:
    def allowed(path: str) -> bool:
        return any(
            path == item or (item.endswith("/") and path.startswith(item))
            for item in ALLOWED_RELEASE_PATHS
        )

    return [path for path in paths if not allowed(path)]


def validate_release_paths(paths: list[str]) -> None:
    if forbidden := disallowed_release_paths(paths):
        raise ReleaseValidationError("release contains disallowed paths: " + ", ".join(forbidden))


@overload
def git(root: Path, *args: str, strip: bool = True, text: Literal[True] = True) -> str: ...


@overload
def git(root: Path, *args: str, strip: bool = True, text: Literal[False]) -> bytes: ...


def git(root: Path, *args: str, strip: bool = True, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=text, check=False
    )
    if result.returncode:
        error = result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        raise ReleaseValidationError(error.strip() or f"git {args[0]} failed")
    if not text:
        return result.stdout
    return result.stdout.strip() if strip else result.stdout


def working_tree_paths(root: Path, runtime_source: str) -> list[str]:
    """Include both rename sides, staged changes, and non-ignored untracked paths."""
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z", strip=False)
    return sorted(
        set(_diff_paths(root, runtime_source))
        | set(_diff_paths(root, runtime_source, cached=True))
        | set(filter(None, untracked.split("\0")))
    )


def release_tag_version(tag: str) -> str:
    if not tag.startswith("v"):
        raise ReleaseValidationError("release tag must be v-prefixed with a valid release version")
    return validate_release_version(tag[1:])


@dataclass(frozen=True)
class TaggedRelease:
    root: Path
    tag_object: str
    release_commit: str
    inputs: dict[str, Any]
    regular_files: frozenset[bytes]

    def file_exists(self, relative: str) -> bool:
        return relative.encode("utf-8") in self.regular_files

    def read_file(self, relative: str) -> bytes:
        if not self.file_exists(relative):
            raise ReleaseValidationError(f"tagged release requires a regular file: {relative}")
        return git(self.root, "show", f"{self.release_commit}:{relative}", text=False)


def tagged_release(root: Path, tag: str) -> TaggedRelease:
    """Capture a release tree authorized by current protected-main SSH signers."""
    from ai_review.canonical import json_loads_no_duplicates

    version = release_tag_version(tag)
    tag_object = git(root, "rev-parse", "--verify", f"refs/tags/{tag}")
    if not FULL_SHA_RE.fullmatch(tag_object) or git(root, "cat-file", "-t", tag_object) != "tag":
        raise ReleaseValidationError("release requires an annotated signed tag")
    annotation = git(root, "cat-file", "tag", tag_object, text=False)
    names = [line[4:] for line in annotation.partition(b"\n\n")[0].splitlines()
             if line.startswith(b"tag ")]
    if names != [tag.encode("utf-8")]:
        raise ReleaseValidationError(
            f"signed tag header must name requested release tag exactly once: {tag}"
        )
    if not re.search(
        rb"\n-----BEGIN SSH SIGNATURE-----\n[A-Za-z0-9+/=\n]+-----END SSH SIGNATURE-----\n?\Z",
        annotation,
    ):
        raise ReleaseValidationError("release requires an SSH-signed annotated tag")
    release_commit = git(root, "rev-parse", f"{tag_object}^{{commit}}")
    main = git(root, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}")
    if not git_is_ancestor(release_commit, main, root):
        raise ReleaseValidationError(
            "release commit P is not reachable from protected origin/main"
        )
    signers = git(root, "show", f"{main}:.github/allowed_signers", text=False)
    if not any(
        line.strip() and not line.lstrip().startswith(b"#") for line in signers.splitlines()
    ):
        raise ReleaseValidationError("protected main has no allowed release signers")
    with tempfile.TemporaryDirectory() as temporary:
        trust = Path(temporary) / "allowed_signers"
        trust.write_bytes(signers)
        git(
            root, "-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={trust}",
            "verify-tag", tag_object,
        )
    tree = git(
        root, "ls-tree", "-r", "-z", "--full-tree", release_commit, "--",
        "release/release-inputs.json", f"release/{version}.md", "docs/evidence/", text=False,
    )
    files = frozenset(
        entry.split(b"\t", 1)[1] for entry in tree.split(b"\0")
        if entry.startswith((b"100644 blob ", b"100755 blob "))
    )
    relative = "release/release-inputs.json"
    if relative.encode() not in files:
        raise ReleaseValidationError(f"tagged release requires a regular file: {relative}")
    inputs = json_loads_no_duplicates(
        git(root, "show", f"{release_commit}:{relative}", text=False).decode("utf-8")
    )
    if not isinstance(inputs, dict) or inputs.get("release_version") != version:
        raise ReleaseValidationError("tag must match the tagged release inputs")
    if inputs.get("status") != "active":
        raise ReleaseValidationError("tag requires active release inputs")
    return TaggedRelease(root, tag_object, release_commit, inputs, files)
