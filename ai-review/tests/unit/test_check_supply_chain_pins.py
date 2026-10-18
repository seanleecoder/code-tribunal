from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_supply_chain_pins.py"

spec = importlib.util.spec_from_file_location("check_supply_chain_pins", SCRIPT)
assert spec is not None and spec.loader is not None
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)

DIGEST = "@sha256:" + "a" * 64


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SupplyChainPinCheckTests(unittest.TestCase):
    def test_current_tree_passes(self) -> None:
        self.assertEqual(pins.issues(), [])

    def test_every_from_needs_a_digest_directly_or_through_its_arg_default(self) -> None:
        pinned = (
            f"ARG BASE=python:3.14-slim{DIGEST}\n"
            f"FROM debian:trixie-slim{DIGEST} AS builder\n"
            "FROM builder AS second\n"
            "FROM ${BASE}\n"
        )
        self.assertEqual(pins._dockerfile_issues("x", pinned), [])
        for mutated in (
            pinned.replace(f"debian:trixie-slim{DIGEST}", "debian:trixie-slim"),
            pinned.replace(f"ARG BASE=python:3.14-slim{DIGEST}", "ARG BASE=python:3.14-slim"),
            pinned.replace("FROM ${BASE}", "FROM $UNDECLARED"),
            pinned.replace("FROM builder AS second", "FROM later AS second"),
        ):
            with self.subTest(mutated=mutated):
                self.assertEqual(len(pins._dockerfile_issues("x", mutated)), 1)

    def test_dockerfile_formatting_cannot_hide_a_mutable_from(self) -> None:
        mutable = "node:26-trixie-slim"
        for text in (
            f"FROM {mutable} AS reviewer-clis \n",
            f"  FROM {mutable}\n",
            f"FROM\t{mutable}\n",
            f"FROM \\\n  {mutable}\n",
            f"from {mutable} as x\n",
            f"FROM --platform=linux/amd64 {mutable} AS x\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    pins._dockerfile_issues("x", text),
                    [f"x: FROM {mutable} is not pinned by sha256 digest"],
                )
        self.assertEqual(
            pins._dockerfile_issues("x", f"FROM {mutable}{DIGEST} AS x extra\n"),
            [f"x: cannot parse 'FROM {mutable}{DIGEST} AS x extra'"],
        )

    def test_only_global_args_resolve_a_from(self) -> None:
        # Docker resolves FROM against the ARGs declared before the first FROM; a
        # later pinned redeclaration does not change the image it builds from.
        text = (
            "ARG NODE_BUILDER=node:26-trixie-slim\n"
            "FROM ${NODE_BUILDER} AS reviewer-clis\n"
            f"ARG NODE_BUILDER=node:26{DIGEST}\n"
            "FROM ${NODE_BUILDER}\n"
        )
        self.assertEqual(
            pins._dockerfile_issues("x", text),
            ["x: FROM ${NODE_BUILDER} is not pinned by sha256 digest"] * 2,
        )
        quoted = f'ARG BASE="python:3.14{DIGEST}"\nFROM $BASE\n'
        self.assertEqual(pins._dockerfile_issues("x", quoted), [])

    def test_arg_words_follow_buildkit(self) -> None:
        # A quoted value is one word: NOTE's text must not invent a BASE assignment.
        smuggled = f'ARG BASE=alpine:latest\nARG NOTE="unused BASE=alpine{DIGEST}"\nFROM $BASE\n'
        self.assertEqual(
            pins._dockerfile_issues("x", smuggled), ["x: FROM $BASE is not pinned by sha256 digest"]
        )
        # A bare redeclaration keeps the earlier default (BuildKit's buildMetaArgs).
        redeclared = f"ARG BASE=python{DIGEST}\nARG BASE\nFROM $BASE\n"
        self.assertEqual(pins._dockerfile_issues("x", redeclared), [])
        reviewer = redeclared.replace("BASE", "AI_REVIEW_BASE_IMAGE")
        self.assertEqual(pins._base_image_issues(f"FROM python{DIGEST}\n", reviewer), [])
        unbalanced = f'ARG BASE="python{DIGEST}\nFROM $BASE\n'
        self.assertIn(
            f"x: cannot parse 'ARG BASE=\"python{DIGEST}'", pins._dockerfile_issues("x", unbalanced)
        )

    def test_continuations_follow_buildkit(self) -> None:
        pinned = f"FROM node:26{DIGEST}\n"
        # An escaped final backslash ends the RUN, so the next FROM is an instruction.
        self.assertEqual(
            pins._dockerfile_issues("x", pinned + "RUN echo \\\\\nFROM alpine:latest\n"),
            ["x: FROM alpine:latest is not pinned by sha256 digest"],
        )
        # A comment line inside a continuation is dropped without ending it.
        self.assertEqual(
            pins._dockerfile_issues("x", pinned + "RUN a \\\n  # note\n  b\nFROM alpine:latest\n"),
            ["x: FROM alpine:latest is not pinned by sha256 digest"],
        )
        # A genuine continuation makes the text a RUN argument, which Docker never builds.
        self.assertEqual(pins._dockerfile_issues("x", pinned + "RUN echo \\\nFROM alpine\n"), [])
        self.assertEqual(
            pins._dockerfile_issues("x", "# escape=`\n" + pinned),
            ["x: cannot parse '# escape=`'"],
        )

    def test_shipped_dockerfiles_pin_every_stage(self) -> None:
        reviewer = _read(pins.REVIEWER_DOCKERFILE)
        # The debian stages were pinned but unchecked before the generic FROM rule.
        self.assertEqual(reviewer.count("FROM debian:trixie-slim@sha256:"), 2)
        mutated = reviewer.replace("FROM debian:trixie-slim@sha256:", "FROM debian:trixie-slim#", 1)
        self.assertTrue(pins._dockerfile_issues("reviewer.Dockerfile", mutated))

    def test_reviewer_base_default_must_match_the_base_image(self) -> None:
        base = _read(pins.BASE_DOCKERFILE)
        reviewer = _read(pins.REVIEWER_DOCKERFILE)
        self.assertEqual(pins._base_image_issues(base, reviewer), [])
        drifted = re.sub(r"(?m)^(FROM \S+@sha256:)[0-9a-f]{64}$", r"\g<1>" + "b" * 64, base)
        self.assertEqual(
            pins._base_image_issues(drifted, reviewer),
            ["reviewer.Dockerfile AI_REVIEW_BASE_IMAGE default must match base.Dockerfile"],
        )

    def test_image_pins_agree_across_platforms_and_with_the_trusted_sha(self) -> None:
        gitlab = _read(pins.GITLAB_REVIEW_TEMPLATE)
        github = _read(pins.GITHUB_REVIEW_WORKFLOW)
        self.assertEqual(pins._image_pin_issues(gitlab, github), [])
        trusted = gitlab.split('AI_REVIEW_TRUSTED_IMAGE_SHA: "', 1)[1][:40]
        base = gitlab.split('AI_REVIEW_BASE_IMAGE: "', 1)[1].split('"', 1)[0]
        base_line = f'  AI_REVIEW_BASE_IMAGE: "{base}"\n'
        cases = {
            "one GitHub container drifts": (gitlab, github.replace(base, base[:-1] + "0", 1)),
            "GitLab rotates alone": (gitlab.replace(base, base[:-1] + "0"), github),
            "trusted SHA differs": (gitlab.replace(f'"{trusted}"', '"' + "0" * 40 + '"'), github),
            "GitLab pin duplicated": (
                gitlab.replace(base_line, base_line * 2),
                github,
            ),
            "tag without digest": (gitlab.replace(base, base.split("@", 1)[0]), github),
            "GitHub job loses its container": (
                gitlab,
                github.replace(f"    container: {base}\n", "", 1),
            ),
        }
        for label, (mutated_gitlab, mutated_github) in cases.items():
            with self.subTest(label):
                self.assertTrue(pins._image_pin_issues(mutated_gitlab, mutated_github))

    def test_requirements_need_exact_pins_and_may_carry_markers(self) -> None:
        text = '-c constraints.txt\n# tools\nruff==1.0\nzizmor==1.0; sys_platform != "android"\n'
        self.assertEqual(pins._exact_requirement_issues("r", text), [])
        for loose in ("ruff>=1.0", "ruff", "ruff~=1.0"):
            with self.subTest(loose=loose):
                self.assertEqual(
                    pins._exact_requirement_issues("r", text.replace("ruff==1.0", loose)),
                    [f"r must use exact == pins only, got {loose!r}"],
                )

    def test_pip_installs_pass_the_constraints_and_name_only_pinned_packages(self) -> None:
        constraints = "pip==26.0\nPyYAML==6.0\nrequests==2.0\n"
        base = (
            "RUN python -m pip install --constraint /tmp/c.txt --upgrade pip \\\n"
            "    && python -m pip install --no-cache-dir \\\n"
            "      --constraint /tmp/c.txt pyyaml requests\n"
            "RUN apt-get install -y git\n"
        )
        self.assertEqual(pins._pip_install_issues(base, constraints), [])
        self.assertEqual(
            pins._pip_install_issues(base.replace("requests", "requests jsonschema"), constraints),
            ["base.Dockerfile installs unconstrained jsonschema"],
        )
        unconstrained = base.replace("--constraint /tmp/c.txt --upgrade", "--upgrade")
        self.assertEqual(len(pins._pip_install_issues(unconstrained, constraints)), 1)

    def test_reviewer_clis_install_from_the_lockfile_at_exact_versions(self) -> None:
        reviewer = _read(pins.REVIEWER_DOCKERFILE)
        package = json.loads(_read(pins.PACKAGE_JSON))
        self.assertEqual(pins._npm_issues(reviewer, package), [])
        self.assertTrue(pins._npm_issues(reviewer.replace("npm ci", "npm install -g"), package))
        package["dependencies"]["opencode-ai"] = "^1.0.0"
        self.assertTrue(pins._npm_issues(reviewer, package))

    def test_pin_files_reject_placeholders_and_missing_fields(self) -> None:
        opencode = json.loads(_read(pins.PACKAGE_JSON))["dependencies"]["opencode-ai"]
        for name, path in (
            ("cursor-agent.pin", pins.CURSOR_AGENT_PIN),
            ("ripgrep.pin", pins.RIPGREP_PIN),
        ):
            text = _read(path)
            self.assertEqual(pins._pin_file_issues(name, text, opencode), [])
            values = dict(
                line.split("=", 1) for line in text.splitlines() if line and line[0] != "#"
            )
            mutations = [
                text.replace(f"sha256={values['sha256']}", "sha256=" + "0" * 64),
                text.replace(f"sha256={values['sha256']}", "sha256=" + values["sha256"].upper()),
                text.replace(f"url={values['url']}", "url=https://example.invalid/latest.tar.gz"),
                text.replace(f"version={values['version']}\n", ""),
            ]
            for mutated in mutations:
                with self.subTest(name=name, mutated=mutated[-200:]):
                    self.assertTrue(pins._pin_file_issues(name, mutated, opencode))

    def test_ripgrep_pin_binary_digest_and_opencode_binding(self) -> None:
        text = _read(pins.RIPGREP_PIN)
        opencode = json.loads(_read(pins.PACKAGE_JSON))["dependencies"]["opencode-ai"]
        tarball = text.split("\nsha256=", 1)[1].split("\n", 1)[0]
        binary = text.split("\nbinary_sha256=", 1)[1].split("\n", 1)[0]
        self.assertEqual(
            pins._pin_file_issues("ripgrep.pin", text.replace(binary, tarball), opencode),
            ["ripgrep.pin binary_sha256 must be the extracted rg, not the tarball"],
        )
        # A Renovate opencode-ai bump must fail until ripgrep.pin is re-observed.
        issues = pins._pin_file_issues("ripgrep.pin", text, "99.0.0")
        self.assertEqual(len(issues), 1)
        self.assertIn("opencode_version must equal package.json opencode-ai 99.0.0", issues[0])

    def test_script_imports_only_the_standard_library(self) -> None:
        """The image-publishing workflow runs it before any dependency is installed."""
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                roots.add(f".{node.module or ''}" if node.level else str(node.module).split(".")[0])
        self.assertEqual(sorted(root for root in roots if root not in sys.stdlib_module_names), [])


if __name__ == "__main__":
    unittest.main()
