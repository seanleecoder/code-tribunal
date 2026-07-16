from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

_SMOKE = Path(__file__).resolve().parents[3] / "scripts" / "smoke_cursor_permissions.sh"


class CursorPermissionSmokeTests(unittest.TestCase):
    def _write_executable(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run_smoke(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            state_dir = root / "state"
            bin_dir.mkdir()
            state_dir.mkdir()
            self._write_executable(
                bin_dir / "timeout",
                "#!/bin/sh\nshift\nexec \"$@\"\n",
            )
            self._write_executable(
                bin_dir / "rm",
                """#!/bin/sh
set -eu
marker="$FAKE_DOCKER_STATE/rm-failed-once"
if [ "${1:-}" = "-rf" ]; then
  if [ "${FAKE_RM_FAIL_ALWAYS:-}" = "true" ]; then
    exit 1
  fi
  if [ "${FAKE_RM_FAIL_FIRST:-}" = "true" ] && [ ! -e "$marker" ]; then
    : > "$marker"
    exit 1
  fi
fi
exec /bin/rm "$@"
""",
            )
            self._write_executable(
                bin_dir / "docker",
                """#!/bin/sh
set -eu
state_dir="$FAKE_DOCKER_STATE"
count_file="$state_dir/count"
count=0
if [ -f "$count_file" ]; then count="$(cat "$count_file")"; fi
count=$((count + 1))
printf '%s\n' "$count" > "$count_file"
printf '%s\n' "$*" >> "$state_dir/invocations"
all_args="$*"

workspace=''
cursor_home=''
probe_tmp=''
cleanup_root=''
transcripts_root=''
write_normalized_config() {
  printf '%s\n' \
    '{"permissions":{"allow":["Read(**)"],'\
'"deny":["Write(**)","Write(/**)","Shell(*)"]},'\
'"version":1,"approvalMode":"allowlist","sandbox":{"mode":"disabled"}}' \
    > "$cursor_home/.cursor/cli-config.json"
}
write_config_without_denies() {
  printf '%s\n' \
    '{"permissions":{"allow":["Read(**)"],"deny":["Write(**)"]}}' \
    > "$cursor_home/.cursor/cli-config.json"
}
write_config_with_extra_allow() {
  printf '%s\n' \
    '{"permissions":{"allow":["Read(**)","Shell(*)"],'\
'"deny":["Write(**)","Write(/**)","Shell(*)"]}}' \
    > "$cursor_home/.cursor/cli-config.json"
}
write_config_with_extra_bucket() {
  printf '%s\n' \
    '{"permissions":{"allow":["Read(**)"],'\
'"deny":["Write(**)","Write(/**)","Shell(*)"],"ask":["Shell(*)"]}}' \
    > "$cursor_home/.cursor/cli-config.json"
}
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--mount" ]; then
    shift
    spec="$1"
    src="${spec#*src=}"
    src="${src%%,dst=*}"
    dst="${spec##*,dst=}"
    case "$dst" in
      /workspace) workspace="$src" ;;
      /cursor-home) cursor_home="$src" ;;
      /permission-tmp) probe_tmp="$src" ;;
      /smoke) cleanup_root="$src" ;;
      /transcripts) transcripts_root="$src" ;;
    esac
  fi
  shift
done

if [ -n "$cleanup_root" ]; then
  printf '%s\n' "$cleanup_root" > "$state_dir/cleanup-root"
  exit "${FAKE_DOCKER_CLEANUP_STATUS:-0}"
fi

if [ -n "$transcripts_root" ]; then
  case "$all_args" in
    *'"name":"Read"'*)
      printf '%s\n' "$transcripts_root" >> "$state_dir/transcript-matches"
      if [ "${FAKE_TRANSCRIPT_READ_ATTEMPTED:-true}" = "true" ]; then
        exit 0
      fi
      exit 1
      ;;
    *"grep -Fiq"*)
      printf '%s\n' "$transcripts_root" >> "$state_dir/transcript-denial-checks"
      if [ "${FAKE_TRANSCRIPT_DENIED:-false}" = "true" ]; then
        exit 0
      fi
      exit 1
      ;;
    *"grep -Fq"*)
      printf '%s\n' "$transcripts_root" >> "$state_dir/transcript-matches"
      if [ "${FAKE_TRANSCRIPT_NONCE:-false}" = "true" ]; then
        exit 0
      fi
      exit 1
      ;;
  esac
  printf '%s\n' "$transcripts_root" >> "$state_dir/transcript-dumps"
  echo '==== /transcripts/.cursor/projects/workspace/agent-transcripts/fake/fake.jsonl ===='
  echo '{"type":"tool_use","name":"Read","status":"fake"}'
  exit 0
fi

if [ "$count" -eq 1 ]; then
  read_value="$(cat "$workspace/fixture.txt")"
  case "${FAKE_DOCKER_READ_MUTATE:-}" in
    workspace) printf '%s\n' mutated > "$workspace/fixture.txt" ;;
    workspace-sentinel) : > "$workspace/cursor-write-sentinel" ;;
    config-normalize) write_normalized_config ;;
    config) printf '%s\n' tampered > "$cursor_home/.cursor/cli-config.json" ;;
    config-drop-deny) write_config_without_denies ;;
    config-add-allow) write_config_with_extra_allow ;;
    config-add-bucket) write_config_with_extra_bucket ;;
    config-delete) rm -f "$cursor_home/.cursor/cli-config.json" ;;
    home) : > "$cursor_home/cursor-home-sentinel" ;;
    tmp) : > "$probe_tmp/cursor-tmp-sentinel" ;;
  esac
  if [ "${FAKE_DOCKER_READ_IS_ERROR:-}" = "true" ]; then
    printf '%s\n' '{"type":"result","is_error":true,"result":"Invalid API key"}'
  elif [ "${FAKE_DOCKER_READ_FABRICATE:-}" = "true" ]; then
    printf '%s\n' '{"type":"result","is_error":false,"result":"fixture contents for read test"}'
  else
    printf '{"type":"result","is_error":false,"result":"%s"}\n' "$read_value"
  fi
  exit "${FAKE_DOCKER_READ_STATUS:-0}"
fi

case "${FAKE_DOCKER_MUTATE:-}" in
  workspace) printf '%s\n' mutated > "$workspace/fixture.txt" ;;
  workspace-sentinel) : > "$workspace/cursor-write-sentinel" ;;
  config-normalize) write_normalized_config ;;
  config) printf '%s\n' tampered > "$cursor_home/.cursor/cli-config.json" ;;
  config-drop-deny) write_config_without_denies ;;
  config-add-allow) write_config_with_extra_allow ;;
  config-add-bucket) write_config_with_extra_bucket ;;
  config-delete) rm -f "$cursor_home/.cursor/cli-config.json" ;;
  home) : > "$cursor_home/cursor-home-sentinel" ;;
  tmp) : > "$probe_tmp/cursor-tmp-sentinel" ;;
esac
printf '%s\n' '{"result":"write and shell requests denied"}'
exit "${FAKE_DOCKER_HOSTILE_STATUS:-0}"
""",
            )
            env = os.environ.copy()
            env.update(
                {
                    "CURSOR_API_KEY": "cursor-test-key",
                    "FAKE_DOCKER_STATE": str(state_dir),
                    "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
                    **overrides,
                }
            )
            result = subprocess.run(
                [str(_SMOKE), "reviewer:test"],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            invocations = state_dir / "invocations"
            result.invocations = (
                invocations.read_text(encoding="utf-8") if invocations.exists() else ""
            )
            count_file = state_dir / "count"
            result.invocation_count = (
                int(count_file.read_text(encoding="utf-8")) if count_file.exists() else 0
            )
            cleanup_root_file = state_dir / "cleanup-root"
            if cleanup_root_file.exists():
                cleanup_root = Path(cleanup_root_file.read_text(encoding="utf-8").strip())
                shutil.rmtree(cleanup_root, ignore_errors=True)
            return result

    def test_success_requires_read_control_then_hostile_probe(self) -> None:
        result = self._run_smoke()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.invocation_count, 3)
        self.assertEqual(result.invocations.count("--mode ask"), 2)
        self.assertEqual(result.invocations.count("--sandbox disabled"), 2)
        self.assertEqual(result.invocations.count("--trust"), 2)
        self.assertIn("returned the fixture nonce", result.stdout)
        self.assertIn("permission smoke passed", result.stdout)

    def test_cursor_config_normalization_preserving_policy_is_allowed(self) -> None:
        result = self._run_smoke(
            FAKE_DOCKER_READ_MUTATE="config-normalize",
            FAKE_DOCKER_MUTATE="config-normalize",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.invocation_count, 3)

    def test_cleanup_falls_back_to_reviewer_container(self) -> None:
        result = self._run_smoke(FAKE_RM_FAIL_FIRST="true")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.invocation_count, 4)
        self.assertIn("dst=/smoke", result.invocations)

    def test_exhausted_cleanup_warns_without_changing_probe_status(self) -> None:
        # A hostile-probe failure also dumps the agent transcripts, so the
        # failing subtest expects one extra docker invocation.
        for hostile_status, expected_status, expected_count in (
            ("0", 0, 4),
            ("9", 1, 5),
        ):
            with self.subTest(
                hostile_status=hostile_status, expected_status=expected_status
            ):
                result = self._run_smoke(
                    FAKE_RM_FAIL_ALWAYS="true",
                    FAKE_DOCKER_CLEANUP_STATUS="8",
                    FAKE_DOCKER_HOSTILE_STATUS=hostile_status,
                )

                self.assertEqual(result.returncode, expected_status, result.stderr)
                self.assertEqual(result.invocation_count, expected_count)
                self.assertIn("dst=/smoke", result.invocations)
                self.assertIn("cleanup warning", result.stderr)

    def test_read_control_failure_is_diagnosed(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_READ_STATUS="7")

        self.assertEqual(result.returncode, 1)
        self.assertIn("read probe execution failure", result.stderr)
        self.assertIn("read-probe agent transcripts follow", result.stderr)
        self.assertIn("dst=/transcripts", result.invocations)
        self.assertEqual(result.invocation_count, 2)

    def test_fabricated_read_response_passes_with_ask_mode_notice(self) -> None:
        # Headless ask mode records the Read tool_use but never executes it,
        # so a fabricated response with a real Read attempt — and no denial
        # recorded — is the expected pinned-CLI behavior: report it without
        # failing the smoke.
        result = self._run_smoke(FAKE_DOCKER_READ_FABRICATE="true")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("without returning the fixture nonce", result.stdout)
        self.assertIn("permission smoke passed", result.stdout)
        self.assertEqual(result.invocation_count, 5)

    def test_error_result_envelope_fails_and_dumps_transcripts(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_READ_IS_ERROR="true")

        self.assertEqual(result.returncode, 1)
        self.assertIn("result envelope reported an error", result.stderr)
        self.assertIn("read-probe agent transcripts follow", result.stderr)
        self.assertEqual(result.invocation_count, 2)

    def test_transcript_denial_fails_and_dumps_transcripts(self) -> None:
        result = self._run_smoke(
            FAKE_DOCKER_READ_FABRICATE="true",
            FAKE_TRANSCRIPT_DENIED="true",
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("records the Read tool as denied", result.stderr)
        self.assertIn("read-probe agent transcripts follow", result.stderr)
        self.assertEqual(result.invocation_count, 5)

    def test_nonce_in_transcript_counts_as_executed_read(self) -> None:
        result = self._run_smoke(
            FAKE_DOCKER_READ_FABRICATE="true",
            FAKE_TRANSCRIPT_NONCE="true",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("the pinned CLI executed the read", result.stdout)
        self.assertIn("permission smoke passed", result.stdout)
        self.assertEqual(result.invocation_count, 4)

    def test_missing_read_tool_attempt_fails_and_dumps_transcripts(self) -> None:
        result = self._run_smoke(FAKE_TRANSCRIPT_READ_ATTEMPTED="false")

        self.assertEqual(result.returncode, 1)
        self.assertIn("no Read tool call was attempted", result.stderr)
        self.assertIn("read-probe agent transcripts follow", result.stderr)
        self.assertEqual(result.invocation_count, 3)

    def test_hostile_probe_execution_failure_is_diagnosed(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_HOSTILE_STATUS="9")

        self.assertEqual(result.returncode, 1)
        self.assertIn("hostile probe execution failure", result.stderr)
        self.assertIn("hostile-probe agent transcripts follow", result.stderr)
        self.assertIn("dst=/transcripts", result.invocations)

    def test_filesystem_mutation_fails_closed(self) -> None:
        for mutation in (
            "workspace",
            "workspace-sentinel",
            "home",
            "tmp",
            "config",
            "config-drop-deny",
            "config-add-allow",
            "config-add-bucket",
            "config-delete",
        ):
            with self.subTest(mutation=mutation):
                result = self._run_smoke(FAKE_DOCKER_MUTATE=mutation)

                self.assertEqual(result.returncode, 1)
                self.assertIn("security failure", result.stderr)

    def test_read_probe_workspace_mutation_fails_closed(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_READ_MUTATE="workspace")

        self.assertEqual(result.returncode, 1)
        self.assertIn("read-probe security failure: workspace content changed", result.stderr)
        self.assertEqual(result.invocation_count, 2)

    def test_read_probe_config_tampering_fails_closed(self) -> None:
        expected_details = {
            "config": "invalid JSON",
            "config-drop-deny": "permissions.deny is missing",
            "config-add-allow": "permissions.allow changed",
            "config-add-bucket": "permissions has unexpected keys",
            "config-delete": "is missing",
        }
        for mutation, expected_detail in expected_details.items():
            with self.subTest(mutation=mutation, expected_detail=expected_detail):
                result = self._run_smoke(FAKE_DOCKER_READ_MUTATE=mutation)

                self.assertEqual(result.returncode, 1)
                self.assertIn("read-probe security failure: cli-config.json", result.stderr)
                self.assertIn(expected_detail, result.stderr)
                self.assertEqual(result.invocation_count, 2)

    def test_hostile_probe_config_tampering_reports_specific_reason(self) -> None:
        expected_details = {
            "config": "invalid JSON",
            "config-drop-deny": "permissions.deny is missing",
            "config-add-allow": "permissions.allow changed",
            "config-add-bucket": "permissions has unexpected keys",
            "config-delete": "is missing",
        }
        for mutation, expected_detail in expected_details.items():
            with self.subTest(mutation=mutation, expected_detail=expected_detail):
                result = self._run_smoke(FAKE_DOCKER_MUTATE=mutation)

                self.assertEqual(result.returncode, 1)
                self.assertIn("hostile-probe security failure: cli-config.json", result.stderr)
                self.assertIn(expected_detail, result.stderr)
                self.assertEqual(result.invocation_count, 4)

    def test_read_probe_home_mutation_fails_closed(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_READ_MUTATE="home")

        self.assertEqual(result.returncode, 1)
        self.assertIn("read-probe security failure", result.stderr)
        self.assertIn("cursor-home-sentinel", result.stderr)
        self.assertEqual(result.invocation_count, 2)

    def test_read_probe_tmp_mutation_fails_closed(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_READ_MUTATE="tmp")

        self.assertEqual(result.returncode, 1)
        self.assertIn("read-probe security failure", result.stderr)
        self.assertIn("cursor-tmp-sentinel", result.stderr)
        self.assertEqual(result.invocation_count, 2)


if __name__ == "__main__":
    unittest.main()
