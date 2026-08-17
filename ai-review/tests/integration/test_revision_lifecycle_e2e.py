from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest import mock

from ai_review import mock_reviewer
from ai_review.config import load_config
from ai_review.consensus import build_consensus, validate_consensus_inputs
from ai_review.input_bundle import prepare_local_bundle
from ai_review.memory import decode_state_note_body
from ai_review.posting import post_consensus
from ai_review.schema import finalize_finding_batch, load_json_file, validate_instance

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
FakeGitHubClient = importlib.import_module("support.fake_github").FakeGitHubClient
_e2e_bundle = importlib.import_module("support.e2e_bundle")
AI_REVIEW_ROOT = _e2e_bundle.AI_REVIEW_ROOT
FIXTURE_ROOT = _e2e_bundle.FIXTURE_ROOT
prepare_simple_bundle = _e2e_bundle.prepare_simple_bundle


class MockScenarioLifecycleTests(unittest.TestCase):
    """End-to-end proof that the deterministic mock scenarios drive the real
    posting path: `blocking` creates one discussion, an unchanged `blocking`
    rerun is idempotent, and `blocking_alt` (same identity, different body)
    updates that same discussion in place. This locks the changed-body lifecycle
    guarantee the `blocking_alt` scenario exists for — token-free, no model."""

    REVIEWERS = ("claude", "codex", "opencode")

    # Posting/state/critique are driven through the documented AI_REVIEW_* env
    # overrides so `prepare` stamps them into the manifest's effective_config. That
    # keeps the manifest and the consensus-time config in agreement, so
    # `validate_consensus_inputs` passes on inputs that could actually come from a
    # real prepare→consensus pipeline — rather than mutating the config after prepare
    # (which diverges the effective_config digest and only survives because
    # build_consensus is called directly).
    CONSENSUS_ENV = {
        # One variable selects the platform: posting.mode also selects the
        # adapter that stores persistent state, and AI_REVIEW_STATE_BACKEND is
        # retired and now rejected outright.
        "AI_REVIEW_POSTING_MODE": "github_reviews",
        "AI_REVIEW_CRITIQUE_ENABLED": "false",
    }

    def _bundle(self, tmp: Path) -> tuple[dict[str, Any], dict[str, Any], str, Path]:
        bundle = prepare_simple_bundle(tmp)
        config = load_config(bundle / "config.review.yaml")
        manifest = dict(
            load_json_file(bundle / "manifest.json"),
            project_id="octo/repo",
            merge_request_iid="7",
        )
        diff_text = (bundle / "mr.diff").read_text(encoding="utf-8")
        return config, manifest, diff_text, bundle

    def _batches(
        self, scenario: str, input_dir: Path, manifest: dict[str, Any], config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        reviewers_cfg = config["reviewers"]
        with mock.patch.dict("os.environ", {"AI_REVIEW_MOCK_SCENARIO": scenario}):
            batches = []
            for reviewer in self.REVIEWERS:
                raw = mock_reviewer.review_batch(reviewer, input_dir)
                batches.append(
                    finalize_finding_batch(
                        raw,
                        reviewer=reviewer,
                        # Stamp the seat's configured model so the batch matches the
                        # effective config validate_consensus_inputs checks against.
                        model=str(reviewers_cfg[reviewer]["model"]),
                        run_id=manifest["run_id"],
                        started_at="2026-07-23T00:00:00Z",
                        effective_config_sha256=manifest["effective_config_sha256"],
                        input_dir=input_dir,
                    )
                )
        return batches

    @staticmethod
    def _marker_diff(*, unrelated_lines: int) -> str:
        """A src/foo.py diff whose `records[0]` marker sits mid-file with 8 stable
        context lines on each side. `unrelated_lines` inserts that many added lines
        in a separate earlier hunk, shifting the marker's line number while leaving
        its ±6 context window (and therefore its context_hash / identity) intact."""
        ctx_above = [f" ctx_above_{i}" for i in range(1, 9)]
        ctx_below = [f" ctx_below_{i}" for i in range(1, 9)]
        marker = "+    return records[0]"
        lines = [
            "diff --git a/src/foo.py b/src/foo.py",
            "--- a/src/foo.py",
            "+++ b/src/foo.py",
        ]
        if unrelated_lines:
            lines.append(f"@@ -0,0 +1,{unrelated_lines} @@")
            lines += [f"+# unrelated_{i}" for i in range(1, unrelated_lines + 1)]
            new_start = unrelated_lines + 1
        else:
            new_start = 1
        lines.append(f"@@ -1,16 +{new_start},17 @@")
        lines += ctx_above + [marker] + ctx_below
        return "\n".join(lines) + "\n"

    def _marker_bundle(
        self, tmp: Path, name: str, diff_text: str
    ) -> tuple[Path, dict[str, Any]]:
        """Build a full input bundle from `diff_text` via prepare_local_bundle so the
        manifest carries provenance (diff_sha256, run_id) consistent with the served
        diff — modelling a distinct revision rather than reusing one manifest with a
        swapped diff. Returns the bundle dir and its manifest (project/MR patched to
        the GitHub fake's coordinates)."""
        repo = tmp / f"{name}-repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "foo.py").write_text(
            "def extract_name(records):\n    return records[0]\n", encoding="utf-8"
        )
        diff_file = tmp / f"{name}.diff"
        diff_file.write_text(diff_text, encoding="utf-8")
        bundle = prepare_local_bundle(
            AI_REVIEW_ROOT / "config" / "review.yaml",
            diff_file,
            repo,
            tmp / f"{name}-bundle",
        )
        manifest = dict(
            load_json_file(bundle / "manifest.json"),
            project_id="octo/repo",
            merge_request_iid="7",
        )
        return bundle, manifest

    def _source_ids(self, batch: dict[str, Any]) -> set[str]:
        return {f["source_finding_id"] for f in batch["findings"]}

    def test_blocking_alt_updates_same_discussion_body_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", self.CONSENSUS_ENV
        ):
            config, manifest, diff_text, bundle = self._bundle(Path(tmp))
            client = FakeGitHubClient(head_sha=manifest["head_sha"], diff_text=diff_text)

            blocking = self._batches("blocking", bundle, manifest, config)
            blocking_alt = self._batches("blocking_alt", bundle, manifest, config)

            # blocking and blocking_alt share finding identity (body is excluded).
            self.assertEqual(self._source_ids(blocking[0]), self._source_ids(blocking_alt[0]))

            # Inputs satisfy the real prepare→consensus integrity contract.
            validate_consensus_inputs(
                config=config, manifest=manifest, finding_batches=blocking, critique_batches=[]
            )
            validate_consensus_inputs(
                config=config, manifest=manifest, finding_batches=blocking_alt, critique_batches=[]
            )

            c_create = build_consensus(manifest, blocking, config)
            p_create = post_consensus(client, config, manifest, c_create, diff_text=diff_text)
            body_after_create = self._inline_body(client)
            c_unchanged = build_consensus(manifest, blocking, config)
            p_unchanged = post_consensus(client, config, manifest, c_unchanged, diff_text=diff_text)
            c_body = build_consensus(manifest, blocking_alt, config)
            p_body = post_consensus(client, config, manifest, c_body, diff_text=diff_text)
            body_after_change = self._inline_body(client)

        for result in (p_create, p_unchanged, p_body):
            validate_instance(result, "post_result.schema.json")
        self.assertEqual(c_create["summary"]["surface_count"], 1)
        self.assertEqual(c_body["summary"]["surface_count"], 1)
        # create
        self.assertEqual(p_create["created_discussions"], 1)
        # unchanged rerun: idempotent, no new discussion
        self.assertEqual(p_unchanged["created_discussions"], 0)
        self.assertGreaterEqual(p_unchanged["skipped_unchanged"], 1)
        # changed body: in-place update on the SAME discussion
        self.assertEqual(p_body["created_discussions"], 0)
        self.assertEqual(p_body["updated_discussions"], 1)
        self.assertEqual(client.review_comment_count(), 1)
        # the single inline comment's body actually changed (content lock, not just
        # the updated_discussions counter)
        self.assertNotEqual(body_after_create, body_after_change)

    def test_line_movement_across_revisions_remaps_to_same_discussion(self) -> None:
        # Models a real cross-revision push, not just a diff swap: two INDEPENDENT
        # bundles are prepared (one per revision) so each manifest carries its own
        # provenance — distinct diff_sha256 and run_id — matching the diff it serves.
        # Revision 1 posts a blocking finding; revision 2 advances the head SHA and
        # serves a diff that inserts unrelated lines above the marker, outside its ±6
        # context window. Identity is head/diff-digest-independent (source_finding_id
        # is context_hash-based), so the stored anchor remaps to the marker's new line
        # and the existing discussion updates in place instead of a second opening.
        #
        # Revision 2 uses `blocking_alt` (same identity, different body) so the second
        # post is an OBSERVABLE update, not a same-body skip: without a body change the
        # run could report created=0 merely by recognising the existing discussion and
        # skipping it, never re-persisting the anchor. Forcing updated_discussions=1
        # exercises the update path and lets us assert the stored anchor actually moved
        # to revision 2's marker line via a "remapped" remap.
        #
        # SCOPE: this proves the *internal* remap contract — finding identity is
        # preserved and the persisted state anchor follows the marker, so the run
        # updates the one existing discussion rather than opening a duplicate. It does
        # NOT prove *platform-visible* re-anchoring: updating an existing GitHub/GitLab
        # comment rewrites its body, not its original diff position, and post.py marks
        # visible placement as requiring separate live validation. That visible
        # placement remains a documented, live-optional confirmation, not something
        # this test asserts.
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", self.CONSENSUS_ENV
        ):
            tmpp = Path(tmp)
            diff1 = self._marker_diff(unrelated_lines=0)
            diff2 = self._marker_diff(unrelated_lines=3)
            bundle1, manifest1 = self._marker_bundle(tmpp, "r1", diff1)
            bundle2, manifest2_raw = self._marker_bundle(tmpp, "r2", diff2)
            config = load_config(bundle1 / "config.review.yaml")
            head2 = "0123456789abcdef0123456789abcdef01234567"
            manifest2 = dict(manifest2_raw, head_sha=head2)
            # A genuine second revision: new head SHA, and — because each bundle was
            # prepared from its own diff — a different input digest and run_id too.
            self.assertNotEqual(manifest1["head_sha"], manifest2["head_sha"])
            self.assertNotEqual(manifest1["diff_sha256"], manifest2["diff_sha256"])
            self.assertNotEqual(manifest1["run_id"], manifest2["run_id"])
            client = FakeGitHubClient(head_sha=manifest1["head_sha"], diff_text=diff1)

            b1 = self._batches("blocking", bundle1, manifest1, config)
            b2 = self._batches("blocking_alt", bundle2, manifest2, config)
            marker_line_1 = b1[0]["findings"][0]["anchor"]["start"]["new_line"]
            marker_line_2 = b2[0]["findings"][0]["anchor"]["start"]["new_line"]
            # The marker moved to a different line, but identity is stable.
            self.assertNotEqual(marker_line_1, marker_line_2)
            self.assertEqual(self._source_ids(b1[0]), self._source_ids(b2[0]))

            # Both revisions satisfy the real prepare→consensus integrity contract
            # (effective_config, run_id, per-seat model) before we build consensus.
            validate_consensus_inputs(
                config=config, manifest=manifest1, finding_batches=b1, critique_batches=[]
            )
            validate_consensus_inputs(
                config=config, manifest=manifest2, finding_batches=b2, critique_batches=[]
            )

            # revision 1
            c1 = build_consensus(manifest1, b1, config)
            p1 = post_consensus(client, config, manifest1, c1, diff_text=diff1)
            # revision 2: the branch head advanced and the served diff moved the marker
            client.head_sha = head2
            client.diff_text = diff2
            c2 = build_consensus(manifest2, b2, config)
            p2 = post_consensus(client, config, manifest2, c2, diff_text=diff2)
            record = self._sole_state_record(client)

        validate_instance(p1, "post_result.schema.json")
        validate_instance(p2, "post_result.schema.json")
        self.assertEqual(p1["created_discussions"], 1)
        # remapped across the revision onto the existing discussion, not a new one
        self.assertEqual(p2["created_discussions"], 0)
        # blocking_alt's body change makes rev 2 an observable in-place update
        self.assertEqual(p2["updated_discussions"], 1)
        self.assertEqual(client.review_comment_count(), 1)
        # the persisted anchor actually followed the marker to revision 2's line,
        # and the move was recorded as a deterministic remap (not an exact match)
        self.assertEqual(record["remap_status"], "remapped")
        self.assertEqual(record["anchor"]["start"]["new_line"], marker_line_2)
        self.assertNotEqual(record["anchor"]["start"]["new_line"], marker_line_1)

    @staticmethod
    def _inline_body(client: FakeGitHubClient) -> str:
        bodies = client.inline_comment_bodies()
        assert len(bodies) == 1, f"expected one inline comment, got {len(bodies)}"
        return bodies[0]

    @staticmethod
    def _sole_state_record(client: FakeGitHubClient) -> dict[str, Any]:
        notes = client.list_state_notes("octo/repo", "7")
        assert len(notes) == 1, f"expected one state note, got {len(notes)}"
        state = decode_state_note_body(notes[0]["body"])
        records = state["records"]
        assert len(records) == 1, f"expected one state record, got {len(records)}"
        return cast(dict[str, Any], records[0])


if __name__ == "__main__":
    unittest.main()
