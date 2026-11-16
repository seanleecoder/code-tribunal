from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from ai_review import input_bundle as bundle
from ai_review.platform.gitlab import MergeRequestVersion

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.config_yaml import runtime_config  # noqa: E402
from support.fake_gitlab import FakeGitLabClient  # noqa: E402


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "maintenance.auto=false",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            *args,
        ],
        cwd=repo,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def commit(repo: Path) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "runner with spaces"
    root.mkdir()
    git(root, "init", "-q", "--initial-branch=main")
    (root / "source.txt").write_text("source revision\n")
    (root / ".gitignore").write_text("*.cache\n")
    commit(root)
    return root


@pytest.fixture
def prepare(repo: Path, monkeypatch: pytest.MonkeyPatch):
    fake = FakeGitLabClient(head_sha=git(repo, "rev-parse", "HEAD"), diff_text="")
    client = mock.Mock(wraps=fake)
    monkeypatch.chdir(repo)
    with (
        mock.patch.dict(
            os.environ,
            {
                "PATH": os.environ["PATH"],
                "CI_PROJECT_ID": "1",
                "CI_MERGE_REQUEST_IID": "2",
                "CI_PIPELINE_SOURCE": "merge_request_event",
                "CI_MERGE_REQUEST_EVENT_TYPE": "detached",
                "GITLAB_TOKEN": "platform-fixture-secret",
                "OPENROUTER_API_KEY": "provider-fixture-secret",
            },
            clear=True,
        ),
        mock.patch.object(bundle, "create_runtime_platform", return_value=client),
    ):
        yield fake, client, lambda out="inputs": bundle.prepare_gitlab_bundle(runtime_config(), out)


def synthetic(repo: Path) -> tuple[str, str]:
    git(repo, "checkout", "-qb", "feature")
    (repo / "feature.txt").write_text("MR source\n")
    selected = commit(repo)
    git(repo, "checkout", "-q", "main")
    (repo / "target-only.txt").write_text("target or another MR\n")
    commit(repo)
    git(repo, "merge", "-qm", "synthetic", "--no-ff", "feature")
    return selected, git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("mode", ["detached", "merged_result", "merge_train", "web", "api"])
def test_snapshot_is_from_selected_source_commit(repo: Path, prepare, mode: str) -> None:
    fake, _, run = prepare
    if mode != "detached":
        selected, runner_head = synthetic(repo)
        fake.head_sha = selected
        os.environ["CI_MERGE_REQUEST_SOURCE_BRANCH_SHA"] = selected
        os.environ["CI_MERGE_REQUEST_EVENT_TYPE"] = mode if mode.startswith("merge") else ""
        os.environ["CI_PIPELINE_SOURCE"] = (
            "merge_request_event" if mode.startswith("merge") else mode
        )
    else:
        runner_head = fake.head_sha
    (repo / "ignored.cache").write_text("not source\n")
    (repo / "untracked.txt").write_text("not source\n")
    isolated = []
    copier = bundle.copy_repo_snapshot

    def capture(source, destination, **kwargs):
        isolated.append(source)
        return copier(source, destination, **kwargs)

    with mock.patch.object(bundle, "copy_repo_snapshot", side_effect=capture):
        out = run()
    manifest = json.loads((out / "manifest.json").read_text())
    assert (
        manifest["head_sha"]
        == manifest["checkout_head_sha"]
        == manifest["selected_head_sha"]
        == fake.head_sha
    )
    assert (out / "repo_snapshot/source.txt").read_text() == "source revision\n"
    for filename in ["target-only.txt", "ignored.cache", "untracked.txt"]:
        assert not (out / "repo_snapshot" / filename).exists()
    assert git(repo, "rev-parse", "HEAD") == runner_head
    assert isolated and isolated[0] != repo and not isolated[0].exists()


def test_tracked_inputs_directory_is_preserved(repo: Path, prepare) -> None:
    fake, _, run = prepare
    (repo / "inputs").mkdir()
    (repo / "inputs/user.txt").write_text("user-owned\n")
    fake.head_sha = commit(repo)
    out = run()
    assert (out / "user.txt").read_text() == "user-owned\n"
    assert (out / "repo_snapshot/inputs/user.txt").read_text() == "user-owned\n"


@pytest.mark.parametrize("mode", ["detached", "merged_result", "merge_train", "web"])
def test_invalid_selection_invalidates_old_manifest_before_collection(
    repo: Path, prepare, mode: str
) -> None:
    fake, client, run = prepare
    expected = git(repo, "rev-parse", "HEAD")
    fake.head_sha = "f" * 40
    os.environ["CI_PIPELINE_SOURCE"] = "web" if mode == "web" else "merge_request_event"
    os.environ["CI_MERGE_REQUEST_EVENT_TYPE"] = mode
    os.environ["CI_MERGE_REQUEST_SOURCE_BRANCH_SHA"] = expected
    out = repo / "inputs"
    out.mkdir()
    (out / "manifest.json").write_text("stale")
    (out / "user.txt").write_text("keep")
    with pytest.raises(bundle.BundleError, match=mode):
        run()
    assert not (out / "manifest.json").exists()
    assert (out / "user.txt").read_text() == "keep"
    client.fetch_diff.assert_not_called()
    client.current_user_id.assert_not_called()
    assert not (out / "repo_snapshot").exists()


@pytest.mark.parametrize("field", ["base_sha", "start_sha", "head_sha"])
def test_api_revision_movement_invalidates_bundle(repo: Path, prepare, field: str) -> None:
    fake, client, run = prepare
    version = fake.fetch_version("1", "2")
    values = dict(base_sha=version.base_sha, start_sha=version.start_sha, head_sha=version.head_sha)
    values[field] = "a" * 40
    client.fetch_version.side_effect = [version, MergeRequestVersion(**values)]
    with pytest.raises(bundle.BundleError, match="version changed"):
        run()
    assert not (repo / "inputs/manifest.json").exists()
    assert not (repo / "inputs").exists()


@pytest.mark.parametrize("change", ["tracked", "untracked", "ignored", "head"])
def test_source_movement_during_copy_is_rejected(repo: Path, prepare, change: str) -> None:
    _, _, run = prepare
    copier = bundle.copy_repo_snapshot
    observed = []

    def mutate(source, destination, **kwargs):
        observed.append(source)
        if change == "head":
            git(source, "checkout", "--orphan", "changed")
        else:
            name = {"tracked": "source.txt", "untracked": "extra.txt", "ignored": "extra.cache"}[
                change
            ]
            (source / name).write_text("modified\n")
        return copier(source, destination, **kwargs)

    with (
        mock.patch.object(bundle, "copy_repo_snapshot", side_effect=mutate),
        pytest.raises(bundle.BundleError),
    ):
        run()
    assert not (repo / "inputs/manifest.json").exists()
    assert observed and not observed[0].exists()


def test_filter_expansions_and_runner_git_configuration_are_not_inherited(
    repo: Path, prepare
) -> None:
    fake, _, run = prepare
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "1" * 64 + "\nsize 20\n"
    (repo / ".gitattributes").write_text("asset.bin filter=fixture\n")
    (repo / "asset.bin").write_text(pointer)
    fake.head_sha = commit(repo)
    (repo / "asset.bin").write_text("expanded runner contents")
    assert "M asset.bin" in git(repo, "status", "--porcelain")  # failure-detecting control
    marker = repo.parent / "filter-or-hook-executed"
    # Only fixed harmless fixture paths, not a command derived from repository data.
    git(repo, "config", "filter.fixture.smudge", f"touch '{marker}'; cat")
    git(repo, "config", "filter.fixture.process", f"touch '{marker}'")
    hook = repo / ".git/hooks/post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o700)
    os.environ["GIT_CONFIG_COUNT"] = "1"
    os.environ["GIT_CONFIG_KEY_0"] = "core.hooksPath"
    os.environ["GIT_CONFIG_VALUE_0"] = str(hook.parent)
    commands = []
    actual_run = subprocess.run

    def observe(*args, **kwargs):
        commands.append(kwargs["env"])
        return actual_run(*args, **kwargs)

    with mock.patch.object(bundle.subprocess, "run", side_effect=observe):
        out = run()
    assert (out / "repo_snapshot/asset.bin").read_text() == pointer
    assert (repo / "asset.bin").read_text() == "expanded runner contents"
    assert not marker.exists()
    assert commands
    assert all("GITLAB_TOKEN" not in env and "OPENROUTER_API_KEY" not in env for env in commands)
    assert all("GIT_CONFIG_COUNT" not in env for env in commands)
    assert all(env["GIT_ALLOW_PROTOCOL"] == "" for env in commands)


def test_initialized_submodule_contents_are_not_snapshotted(repo: Path, prepare) -> None:
    fake, _, run = prepare
    module_head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{module_head},vendor")
    git(repo, "commit", "-qm", "gitlink")
    fake.head_sha = git(repo, "rev-parse", "HEAD")
    (repo / "vendor").mkdir()
    (repo / "vendor/foreign.txt").write_text("not superproject source")
    out = run()
    assert not (out / "repo_snapshot/vendor/foreign.txt").exists()
    assert (repo / "vendor/foreign.txt").exists()


def test_shallow_source_needs_no_parent_history(
    repo: Path, prepare, monkeypatch, tmp_path: Path
) -> None:
    fake, _, run = prepare
    (repo / "new.txt").write_text("new")
    head = commit(repo)
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth=1", repo.as_uri(), str(shallow))
    assert (shallow / ".git/shallow").exists()
    fake.head_sha = head
    monkeypatch.chdir(shallow)
    out = run()
    assert (out / "repo_snapshot/new.txt").read_text() == "new"


def test_linked_worktree_uses_common_object_store(
    repo: Path, prepare, monkeypatch, tmp_path: Path
) -> None:
    _, _, run = prepare
    worktree = tmp_path / "worktree"
    git(repo, "worktree", "add", "-q", "--detach", str(worktree), "HEAD")
    assert (worktree / ".git").is_file()
    monkeypatch.chdir(worktree)
    out = run()
    assert (out / "repo_snapshot/source.txt").exists()


def test_source_symlink_is_still_rejected(repo: Path, prepare) -> None:
    fake, _, run = prepare
    (repo / "hostile").symlink_to("/proc/self/environ")
    fake.head_sha = commit(repo)
    with pytest.raises(bundle.BundleError, match="symlink"):
        run()
    assert not (repo / "inputs/manifest.json").exists()


def test_local_source_checkout_is_read_only_toward_runner_objects(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    objects = repo / ".git/objects"
    paths = list(objects.rglob("*")) + [objects]
    try:
        for path in paths:
            path.chmod(0o500 if path.is_dir() else 0o400)
        with bundle._gitlab_source_checkout(repo, head) as source:
            assert bundle._checkout_head(source, repo / "inputs", expected_head_sha=head) == head
            assert (source / "source.txt").read_text() == "source revision\n"
    finally:
        for path in reversed(paths):
            path.chmod(0o700 if path.is_dir() else 0o600)


def test_missing_commit_cleans_up_temporary_source(repo: Path, monkeypatch, tmp_path: Path) -> None:
    created = []
    real_temporary = bundle.tempfile.TemporaryDirectory

    def temporary(**kwargs):
        directory = real_temporary(dir=tmp_path, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(bundle.tempfile, "TemporaryDirectory", temporary)
    with pytest.raises(bundle.BundleError), bundle._gitlab_source_checkout(repo, "f" * 40):
        pytest.fail("missing commit admitted")
    assert created and not created[0].exists()


def test_clean_hydrated_and_synthetic_runners_produce_the_same_snapshot(
    repo: Path, prepare
) -> None:
    fake, _, run = prepare
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "2" * 64 + "\nsize 10\n"
    (repo / ".gitattributes").write_text("asset.bin filter=fixture\n")
    (repo / "asset.bin").write_text(pointer)
    commit(repo)
    selected, merged = synthetic(repo)
    fake.head_sha = selected
    os.environ["CI_PIPELINE_SOURCE"] = "web"
    os.environ["CI_MERGE_REQUEST_EVENT_TYPE"] = ""
    snapshots = []
    for mode in ("clean", "hydrated", "synthetic"):
        if mode == "clean":
            git(repo, "checkout", "--quiet", "--detach", selected)
        elif mode == "hydrated":
            (repo / "asset.bin").write_text("expanded outside the source commit")
        else:
            git(repo, "checkout", "--quiet", "--force", "--detach", merged)
        output = run(repo.parent / mode)
        snapshot = output / "repo_snapshot"
        snapshots.append(
            {p.relative_to(snapshot): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
        )
    assert snapshots[0] == snapshots[1] == snapshots[2]
    assert snapshots[0][Path("asset.bin")] == pointer.encode()
    assert Path("target-only.txt") not in snapshots[0]


def test_sha256_object_store_is_materialized(tmp_path: Path) -> None:
    repo = tmp_path / "sha256"
    repo.mkdir()
    git(repo, "init", "--quiet", "--object-format=sha256")
    (repo / "source.txt").write_text("sha256 source")
    head = commit(repo)
    assert len(head) == 64
    with bundle._gitlab_source_checkout(repo, head) as source:
        assert bundle._checkout_head(source, tmp_path / "out", expected_head_sha=head) == head
        assert (source / "source.txt").read_text() == "sha256 source"
