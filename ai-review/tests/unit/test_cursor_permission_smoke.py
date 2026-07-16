from __future__ import annotations

import os
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

workspace=''
cursor_home=''
probe_tmp=''
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
    esac
  fi
  shift
done

if [ "$count" -eq 1 ]; then
  printf '%s\n' '{"result":"cursor-permission-read-probe"}'
  exit "${FAKE_DOCKER_READ_STATUS:-0}"
fi

case "${FAKE_DOCKER_MUTATE:-}" in
  workspace) printf '%s\n' mutated > "$workspace/fixture.txt" ;;
  workspace-sentinel) : > "$workspace/cursor-write-sentinel" ;;
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
            return result

    def test_success_requires_read_control_then_hostile_probe(self) -> None:
        result = self._run_smoke()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.invocation_count, 2)
        self.assertEqual(result.invocations.count("--mode ask"), 2)
        self.assertIn("permission smoke passed", result.stdout)

    def test_read_control_failure_is_diagnosed(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_READ_STATUS="7")

        self.assertEqual(result.returncode, 1)
        self.assertIn("read probe execution failure", result.stderr)
        self.assertEqual(result.invocation_count, 1)

    def test_hostile_probe_execution_failure_is_diagnosed(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_HOSTILE_STATUS="9")

        self.assertEqual(result.returncode, 1)
        self.assertIn("hostile probe execution failure", result.stderr)

    def test_filesystem_mutation_fails_closed(self) -> None:
        result = self._run_smoke(FAKE_DOCKER_MUTATE="workspace")

        self.assertEqual(result.returncode, 1)
        self.assertIn("security failure: workspace content changed", result.stderr)


if __name__ == "__main__":
    unittest.main()
