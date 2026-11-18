"""The no-shell Codex tool server must stay inside the snapshot and fail closed."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest
from ai_review import codex_repo_tools as tools
from ai_review.reviewer_setup import prepare_shell_adapter
from ai_review.reviewers import get_reviewer_definition

RG = shutil.which("rg")
needs_rg = pytest.mark.skipif(RG is None, reason="ripgrep is not installed")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    if RG is not None:
        monkeypatch.setattr(tools, "RG", RG)
    root = tmp_path / "snapshot"
    (root / "pkg").mkdir(parents=True)
    (root / ".github").mkdir()
    (root / ".github/ci.yml").write_text("run: other\n")
    (root / "sentinel.txt").write_text("snapshot-only-value\n")
    (root / "pkg/module.py").write_text("value = 1\nother = 2\n")
    (tmp_path / "outside.txt").write_text("outside-secret\n")
    return root


def call(root: Path, name: str, **arguments) -> tuple[str, bool]:
    result = tools.call_tool(root, {"name": name, "arguments": arguments})
    return result["content"][0]["text"], result["isError"]


def test_read_and_list_inside_the_snapshot(repo: Path) -> None:
    assert call(repo, "read_file", path="sentinel.txt") == ("1\tsnapshot-only-value", False)
    assert call(repo, "read_file", path="pkg/module.py", start_line=2) == ("2\tother = 2", False)
    assert call(repo, "list_dir") == (".github/\npkg/\nsentinel.txt", False)
    assert call(repo, "read_file", path="missing.txt") == ("not a file: missing.txt", True)
    assert call(repo, "read_file", path="sentinel.txt", max_lines=0)[1] is True
    assert call(repo, "write_file", path="sentinel.txt") == ("unknown tool", True)


@needs_rg
def test_grep_uses_rg_including_hidden_files(repo: Path) -> None:
    text, is_error = call(repo, "grep", pattern="other")
    assert not is_error
    assert sorted(text.splitlines()) == [".github/ci.yml:1:run: other", "pkg/module.py:2:other = 2"]
    assert call(repo, "grep", pattern="other", path="pkg") == ("pkg/module.py:2:other = 2\n", False)
    assert call(repo, "grep", pattern="no-such-match") == ("", False)
    assert call(repo, "grep", pattern="(")[1] is True
    assert call(repo, "grep", pattern="x" * 513)[1] is True
    assert call(repo, "grep", pattern="a\x00b") == ("pattern must not contain NUL", True)


@needs_rg
def test_grep_searches_a_path_named_dash(repo: Path) -> None:
    (repo / "-").write_text("dash-only\n")
    # The same name as a repository-wide search shows.
    assert call(repo, "grep", pattern="dash-only", path="-") == ("-:1:dash-only\n", False)
    assert call(repo, "grep", pattern="dash-only") == ("-:1:dash-only\n", False)
    # The name grep shows opens the file.
    assert call(repo, "read_file", path="-") == ("1\tdash-only", False)
    (repo / "-").unlink()
    (repo / "-").mkdir()
    (repo / "-/inner.txt").write_text("dash-only\n")
    assert call(repo, "grep", pattern="dash-only", path="-") == ("-/inner.txt:1:dash-only\n", False)


@needs_rg
def test_grep_caps_dense_files_in_a_directory_search_and_says_so(repo: Path) -> None:
    cap = tools.MAX_MATCHES_PER_FILE
    (repo / "dense.txt").write_text("hit\n" * (cap + 10))
    (repo / "exact.txt").write_text("hit\n" * cap)
    (repo / "pkg/sparse.py").write_text("hit\n")
    lines = call(repo, "grep", pattern="hit")[0].splitlines()
    dense = [line for line in lines if line.startswith("dense.txt:")]
    assert dense == [f"dense.txt:{n}:hit" for n in range(1, cap + 1)]
    note = f"[dense.txt: more than {cap} matching lines; grep this path to lift the per-file limit]"
    assert note in lines
    # A file with exactly the cap is complete, so it gets no note.
    assert len([line for line in lines if line.startswith("exact.txt:")]) == cap
    assert not any(line.startswith("[exact.txt") for line in lines)
    assert "pkg/sparse.py:1:hit" in lines
    # A search of that file by path is not capped.
    text, is_error = call(repo, "grep", pattern="hit", path="dense.txt")
    assert not is_error and len(text.splitlines()) == cap + 10
    description = tools.TOOLS[2]["description"]
    assert f"more than {cap} matching lines" in description
    # A path search lifts only the per-file cap, not the output limit.
    assert "for all of them" not in description and "total output limit" in description


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 names")
@needs_rg
def test_grep_cap_note_for_a_name_that_cannot_be_a_path(repo: Path) -> None:
    cap = tools.MAX_MATCHES_PER_FILE
    Path(os.fsdecode(os.fsencode(repo) + b"/ev\xff.txt")).write_text("hit\n" * (cap + 1))
    lines = call(repo, "grep", pattern="hit")[0].splitlines()
    note = (
        f'["ev\\\\xff.txt"{tools._UNADDRESSABLE}: more than {cap} matching lines; '
        "a narrower pattern may show more, but repeated lines can stay hidden]"
    )
    assert note in lines
    assert not any("grep this path" in line for line in lines)


@needs_rg
def test_grep_reports_files_read_file_serves(repo: Path) -> None:
    (repo / "large.txt").write_text("x" * 63 + "\n")
    with (repo / "large.txt").open("a") as handle:
        handle.write(("y" * 63 + "\n") * 32768 + "late-needle\n")
    assert (repo / "large.txt").stat().st_size > 2 * 1024 * 1024
    assert call(repo, "grep", pattern="late-needle") == ("large.txt:32770:late-needle\n", False)
    with (repo / "too-large.txt").open("w") as handle:
        handle.write("z" * tools.MAX_FILE_BYTES + "\nlate-needle\n")
    assert "too-large.txt" not in call(repo, "grep", pattern="late-needle")[0]
    # rg would search a file named as its operand whatever its size.
    assert call(repo, "grep", pattern="late-needle", path="too-large.txt") == (
        "file larger than 4 MiB is not searched: too-large.txt",
        True,
    )
    assert "4 MiB are not searched" in tools.TOOLS[2]["description"]


@needs_rg
def test_grep_keeps_a_carriage_return_without_a_line_feed(repo: Path) -> None:
    (repo / "cr.txt").write_bytes(b"needle\r\nneedle\r")
    assert call(repo, "grep", pattern="needle", path="cr.txt") == (
        "cr.txt:1:needle\ncr.txt:2!:needle\\r\n",
        False,
    )


@pytest.mark.parametrize(
    "path,refusal",
    [
        ("../outside.txt", "outside the repository snapshot"),
        ("pkg/../../outside.txt", "outside the repository snapshot"),
        ("/etc/hostname", "must be repository-relative"),
        ("escape.txt", "outside the repository snapshot"),
        ("escape-dir/outside.txt", "outside the repository snapshot"),
    ],
)
def test_paths_cannot_leave_the_snapshot(repo: Path, path: str, refusal: str) -> None:
    (repo / "escape.txt").symlink_to(repo.parent / "outside.txt")
    (repo / "escape-dir").symlink_to(repo.parent)
    for name in ("read_file", "list_dir", "grep"):
        arguments = {"path": path, **({"pattern": "x"} if name == "grep" else {})}
        text, is_error = call(repo, name, **arguments)
        assert is_error and refusal in text


@needs_rg
def test_search_never_follows_symlinks_out(repo: Path) -> None:
    (repo / "escape.txt").symlink_to(repo.parent / "outside.txt")
    (repo / "escape-dir").symlink_to(repo.parent)
    assert "outside-secret" not in call(repo, "grep", pattern="outside-secret")[0]


def test_read_file_numbers_lines_as_git_does(repo: Path) -> None:
    (repo / "ff.py").write_bytes("a\r\n\x0cb\nc\x1cd\u2028e\n".encode())
    assert call(repo, "read_file", path="ff.py") == (
        "1\ta\n2!\t\\fb\n3!\tc\\u001cd\\u2028e",
        False,
    )
    (repo / "empty.txt").write_bytes(b"")
    assert call(repo, "read_file", path="empty.txt") == ("", False)
    (repo / "no-eol.txt").write_bytes(b"x\n\ny")
    assert call(repo, "read_file", path="no-eol.txt") == ("1\tx\n2\t\n3\ty", False)
    # Only a CRLF terminator loses its "\r"; git counts a lone final "\r" as a line.
    (repo / "cr-eof.txt").write_bytes(b"a\nb\r")
    assert call(repo, "read_file", path="cr-eof.txt") == ("1\ta\n2!\tb\\r", False)
    (repo / "cr-only.txt").write_bytes(b"a\n\r")
    assert call(repo, "read_file", path="cr-only.txt") == ("1\ta\n2!\t\\r", False)


def test_read_limit_between_cr_and_lf_ends_a_crlf_line(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(tools, "MAX_FILE_BYTES", 4)
    (repo / "crlf.txt").write_bytes(b"abc\r\nd\r\n")
    assert call(repo, "read_file", path="crlf.txt") == (
        "1\tabc\n[file truncated at 4 bytes]",
        False,
    )
    (repo / "cr.txt").write_bytes(b"abc\rd\n")
    assert call(repo, "read_file", path="cr.txt") == (
        "1!\tabc\\r\n[file truncated at 4 bytes]",
        False,
    )


def test_read_past_the_end_says_where_the_file_ends(repo: Path, monkeypatch) -> None:
    (repo / "three.txt").write_bytes(b"a\nb\nc\n")
    assert call(repo, "read_file", path="three.txt", start_line=50) == (
        "[start_line 50 is past the end of the file, which has 3 lines]",
        False,
    )
    (repo / "one.txt").write_bytes(b"a")
    assert call(repo, "read_file", path="one.txt", start_line=2)[0].endswith("has 1 line]")
    (repo / "empty.txt").write_bytes(b"")
    assert call(repo, "read_file", path="empty.txt") == ("", False)
    assert call(repo, "read_file", path="empty.txt", start_line=2)[0].endswith("has 0 lines]")
    monkeypatch.setattr(tools, "MAX_FILE_BYTES", 4)
    assert call(repo, "read_file", path="three.txt", start_line=9) == (
        "[start_line 9 is past line 2, the last line within the read limit]\n"
        "[file truncated at 4 bytes]",
        False,
    )


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 names")
def test_non_utf8_names_are_json_safe(repo: Path) -> None:
    os.mkdir(os.fsencode(repo / "pkg") + b"/bad\xff")
    reply = tools.respond(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_dir", "arguments": {"path": "pkg"}}},
        repo,
    )
    wire = json.dumps(reply)
    assert "\\udc" not in wire
    assert json.loads(wire)["result"]["content"][0]["text"] == (
        '"bad\\\\xff/" [non-UTF-8 name; cannot be opened by path]\nmodule.py'
    )


MARKED = '"ev\\\\xff.py" [non-UTF-8 name; cannot be opened by path]'


# _plain quotes more than the listed characters: a leading "[", line and
# paragraph separators, and other category C characters such as private use.
# Shared by the list_dir and grep tests, and escaped so each code point shows.
QUOTED_NAMES = ("[notes].txt", "a\u2028b.txt", "pua\ue000.txt")


def test_every_quoted_name_is_described_and_opens_decoded(repo: Path) -> None:
    for name in QUOTED_NAMES:
        (repo / name).write_text("quoted\n")
    listing = call(repo, "list_dir")[0].splitlines()
    for name in QUOTED_NAMES:
        assert json.dumps(name) in listing
        assert call(repo, "read_file", path=name) == ("1\tquoted", False)
    for tool in tools.TOOLS:
        if tool["name"] != "read_file":
            assert "A name shown in double quotes is a JSON string" in tool["description"]
            # The rule itself excepts a marked name: its quoted text is no path.
            assert f"unless the quoted text is followed by '{tools._UNADDRESSABLE.strip()}'" in (
                tool["description"]
            )
    assert "when its name can be passed as a path" in tools.TOOLS[2]["description"]


@needs_rg
def test_grep_shows_quoted_names_as_list_dir_does(repo: Path) -> None:
    # grep renders names through _match_line and _rg_name, not list_dir's path.
    for name in QUOTED_NAMES:
        (repo / name).write_text("quoted-hit\n")
    lines = call(repo, "grep", pattern="quoted-hit")[0].splitlines()
    assert sorted(lines) == sorted(f"{json.dumps(name)}:1:quoted-hit" for name in QUOTED_NAMES)


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 names")
@needs_rg
def test_no_crafted_name_is_shown_like_another(repo: Path) -> None:
    # Each decoy imitates the raw-byte file, the marker, another file, or a
    # path:line prefix; every name must still be shown distinctly.
    (Path(os.fsdecode(os.fsencode(repo) + b"/ev\xff.py"))).write_text("hit = 'raw'\n")
    (repo / "ev\\xff.py").write_text("hit = 'backslash'\n")
    (repo / f"ev\\xff.py{tools._UNADDRESSABLE}").write_text("hit = 'marker'\n")
    (repo / MARKED).write_text("hit = 'quoted marker'\n")
    (repo / "x\npkg").mkdir()
    (repo / "x\npkg/module.py").write_text("hit = 'newline'\n")
    (repo / "pkg/module.py:9:x = 'colon'").write_text("hit\n")
    listing = call(repo, "list_dir")[0].splitlines()
    assert len(listing) == len(set(listing))
    assert MARKED in listing
    assert '"x\\npkg/"' in listing
    text, is_error = call(repo, "grep", pattern="hit")
    assert not is_error
    assert sorted(text.splitlines()) == sorted([
        f"{MARKED}:1:hit = 'raw'",
        '"ev\\\\xff.py":1:hit = \'backslash\'',
        f'"ev\\\\xff.py{tools._UNADDRESSABLE}":1:hit = \'marker\'',
        f"{json.dumps(MARKED)}:1:hit = 'quoted marker'",
        '"x\\npkg/module.py":1:hit = \'newline\'',
        '"pkg/module.py:9:x = \'colon\'":1:hit',
    ])
    # A quoted name decodes back to a path the tools open.
    assert call(repo, "read_file", path="x\npkg/module.py") == ("1\thit = 'newline'", False)


@needs_rg
def test_grep_shows_matches_only_and_bounds_long_lines(repo: Path) -> None:
    (repo / "blob.bin").write_bytes(b"hit\n" + b"a" * 100_000 + b"\n\0tail\n")
    (repo / "min.js").write_text("x" * 600 + "hit\n")
    text, is_error = call(repo, "grep", pattern="hit")
    assert not is_error
    lines = sorted(text.splitlines())
    assert lines[0] == "blob.bin:1:hit"  # No binary-file notice follows it.
    assert lines[1] == "min.js:1+:" + "x" * 500  # Cut: marked in the prefix, never in the text.
    assert len(lines) == 2


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 names")
def test_non_utf8_names_with_backslashes_stay_distinct(repo: Path) -> None:
    # Without doubling literal backslashes, both would show as ev\xff\xfe.py.
    for raw in (b"ev\xff\\xfe.py", b"ev\\xff\xfe.py"):
        Path(os.fsdecode(os.fsencode(repo) + b"/" + raw)).write_text("x\n")
    listing = call(repo, "list_dir")[0].splitlines()
    assert '"ev\\\\xff\\\\\\\\xfe.py"' + tools._UNADDRESSABLE in listing
    assert '"ev\\\\\\\\xff\\\\xfe.py"' + tools._UNADDRESSABLE in listing


@needs_rg
def test_names_cannot_pose_as_tool_notes(repo: Path) -> None:
    (repo / "[truncated at 1000 entries]").write_text("hit\n")
    assert '"[truncated at 1000 entries]"' in call(repo, "list_dir")[0].splitlines()
    assert call(repo, "grep", pattern="hit") == ('"[truncated at 1000 entries]":1:hit\n', False)


@needs_rg
def test_match_text_cannot_pose_as_another_match(repo: Path) -> None:
    (repo / "notes.txt").write_text(
        "hit\rpkg/auth.py:12:a\x0bb\x0cc\x85d e f\tg\n", newline=""
    )
    assert call(repo, "grep", pattern="hit", path="notes.txt") == (
        "notes.txt:1!:hit\\rpkg/auth.py:12:a\\u000bb\\fc\\u0085d\\u2028e\\u2029f\\tg\n",
        False,
    )


@needs_rg
def test_escaped_text_is_distinct_from_literal_escape_text(repo: Path) -> None:
    # A real CR and the two characters backslash-r look alike once escaped.
    # The line holding the real one is marked in the tool-written prefix, where
    # content cannot reach, and shown as a JSON string body; other lines are
    # shown exactly.
    (repo / "esc.py").write_text('real = "a\rb"  # hit\nliteral = "a\\rb"  # hit\n', newline="")
    assert call(repo, "grep", pattern="hit", path="esc.py") == (
        'esc.py:1!:real = \\"a\\rb\\"  # hit\nesc.py:2:literal = "a\\rb"  # hit\n',
        False,
    )
    assert call(repo, "read_file", path="esc.py") == (
        '1!\treal = \\"a\\rb\\"  # hit\n2\tliteral = "a\\rb"  # hit',
        False,
    )


@needs_rg
def test_no_two_lines_are_shown_alike(repo: Path) -> None:
    # Each pair once rendered identically: literal escape text beside a forged
    # note, and controls next to literal backslashes. Every line must decode to
    # exactly its own text.
    lines = [
        "\x01 hit",
        "\\u0001 [control characters shown escaped] hit",
        "\r\\u0001 hit",
        "\\r\x01 hit",
        "\\\\r hit",
        'tab\tand "quote" \x01 hit',
    ]
    (repo / "pairs.txt").write_text("".join(f"{line}\n" for line in lines), newline="")
    read = call(repo, "read_file", path="pairs.txt")[0].splitlines()
    found = call(repo, "grep", pattern="hit", path="pairs.txt")[0].splitlines()
    assert len(set(read)) == len(set(found)) == len(lines)
    for shown, line in zip(read, lines, strict=True):
        number, _, text = shown.partition("\t")
        assert (json.loads(f'"{text}"') if number.endswith("!") else text) == line
    for shown, line in zip(sorted(found, key=lambda m: int(m.split(":")[1].rstrip("!"))),
                           lines, strict=True):
        _, number, text = shown.split(":", 2)
        assert (json.loads(f'"{text}"') if number.endswith("!") else text) == line


def test_read_file_resumes_by_file_columns_through_escapes(repo: Path, monkeypatch) -> None:
    # Each control character is shown as six characters but advances one column.
    raw = "\x01" * 40 + "END"
    (repo / "ctl.txt").write_text(raw + "\n")
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 100)
    seen, column = "", 1
    for _ in range(20):
        text, is_error = call(repo, "read_file", path="ctl.txt", start_column=column)
        assert not is_error
        body, _, rest = text.partition("\n")
        seen += json.loads('"' + body.removeprefix("1!\t") + '"')
        if not rest:
            break
        column = int(rest.rsplit("start_column=", 1)[1].rstrip("]"))
    assert seen == raw


def test_paging_a_long_line_renders_each_page_only(repo: Path, monkeypatch) -> None:
    # Rendering is bounded by the page, not by the rest of the line, so paging
    # through a control-dense line costs time in proportion to its length.
    raw = "\x01" * 20_000
    (repo / "ctl.txt").write_text(raw + "\n")
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 600)
    rendered = []
    encode = tools._json_body
    monkeypatch.setattr(
        tools, "_json_body", lambda text: rendered.append(len(text)) or encode(text)
    )
    seen, column, pages = "", 1, 0
    while True:
        pages += 1
        text, _ = call(repo, "read_file", path="ctl.txt", start_column=column)
        body, _, rest = text.partition("\n")
        seen += json.loads('"' + body.removeprefix("1!\t") + '"')
        if not rest:
            break
        column = int(rest.rsplit("start_column=", 1)[1].rstrip("]"))
    assert seen == raw and pages > 100
    # Each page renders at most one budget of characters, never the whole rest;
    # the longest-fit search renders a few such prefixes per page.
    assert max(rendered) <= 600 and sum(rendered) <= 32 * len(raw)


@needs_rg
def test_one_match_dense_line_does_not_hide_other_results(repo: Path, monkeypatch) -> None:
    # rg --json lists every submatch, so this one line is a record far larger
    # than its text; it must not swallow the rest of the search.
    monkeypatch.setattr(tools, "MAX_GREP_RECORD_BYTES", 16384)
    (repo / "min.js").write_text("value;" * 2000 + "\n")
    text, is_error = call(repo, "grep", pattern="value")
    assert not is_error
    lines = sorted(text.splitlines())
    assert lines[0] == "min.js:1+:" + ("value;" * 2000)[: tools.MAX_MATCH_CHARS]
    assert lines[1:] == ["pkg/module.py:1:value = 1", "sentinel.txt:1:snapshot-only-value"]
    # A line whose text alone overflows the record budget is still reported.
    monkeypatch.setattr(tools, "MAX_GREP_RECORD_BYTES", 256)
    text, is_error = call(repo, "grep", pattern="value", path="min.js")
    assert (text, is_error) == ("min.js:?+:\n", False)


@needs_rg
def test_failed_search_is_an_error_and_partial_failure_is_reported(repo: Path) -> None:
    assert call(repo, "grep", pattern="value", path="missing") == (
        "search failed; check the pattern and path",
        True,
    )
    if os.geteuid() == 0:
        pytest.skip("root can read files without permission")
    locked = repo / "locked.txt"
    locked.write_text("value\n")
    locked.chmod(0)
    try:
        text, is_error = call(repo, "grep", pattern="value = 1")
    finally:
        locked.chmod(0o644)
    assert (text, is_error) == (
        "pkg/module.py:1:value = 1\n[search incomplete: some files could not be searched]\n",
        False,
    )


@needs_rg
def test_grep_ignores_repository_ignore_files(repo: Path) -> None:
    for name in (".ignore", ".rgignore", ".gitignore"):
        (repo / name).write_text("pkg/\n")
    (repo.parent / ".ignore").write_text("sentinel.txt\n")
    text, is_error = call(repo, "grep", pattern="value")
    assert not is_error
    assert sorted(text.splitlines()) == [
        "pkg/module.py:1:value = 1",
        "sentinel.txt:1:snapshot-only-value",
    ]


def test_output_is_bounded(repo: Path, monkeypatch) -> None:
    lines = call(repo, "read_file", path="pkg/module.py", max_lines=1)[0]
    assert lines == "1\tvalue = 1\n[1 more lines; continue with start_line]"
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 12)
    text, _ = call(repo, "read_file", path="sentinel.txt")
    assert text == (
        "1\tsnapshot-\n[output limit reached; continue with start_line=1 start_column=10]"
    )
    assert call(repo, "read_file", path="sentinel.txt", start_column=10) == (
        "1\tonly-valu\n[output limit reached; continue with start_line=1 start_column=19]",
        False,
    )


def test_one_long_line_stays_reachable(repo: Path, monkeypatch) -> None:
    # Git counts a CR-only file as one line; paging by column reaches its end.
    (repo / "cr.txt").write_bytes(b"x\r" * 40 + b"END\r")
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 80)
    seen, column = "", 1
    for _ in range(10):
        text, is_error = call(repo, "read_file", path="cr.txt", start_column=column)
        assert not is_error
        body, _, note = text.partition("\n")
        assert body.startswith("1!\t")
        seen += json.loads('"' + body.removeprefix("1!\t") + '"')
        if not note:
            break
        column = int(note.rsplit("start_column=", 1)[1].rstrip("]"))
    # Every CR is shown escaped: without a following LF, none is a terminator.
    assert seen == "x\r" * 40 + "END\r"


def test_blank_lines_never_overrun_the_page_budget(repo: Path, monkeypatch) -> None:
    # A nearly full page followed by blank lines must still end with an exact
    # continuation note, not be cut by the final output cap.
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 40)
    (repo / "blank.txt").write_text("x" * 30 + "\n" * 400 + "tail\n")
    text, is_error = call(repo, "read_file", path="blank.txt")
    assert not is_error
    assert len(text.encode()) <= tools.MAX_OUTPUT_BYTES
    rows, note = text.rsplit("\n", 1)
    resume = int(note.removeprefix("[output limit reached; continue with start_line=").split()[0])
    assert rows.splitlines()[-1] == f"{resume - 1}\t"
    rows_seen = len(rows.splitlines())
    while "start_line=" in note:
        page, _ = call(repo, "read_file", path="blank.txt", start_line=resume)
        assert len(page.encode()) <= tools.MAX_OUTPUT_BYTES
        rows, _, note = page.rpartition("\n") if "start_line=" in page else (page, "", "")
        rows_seen += len(rows.splitlines())
        if note:
            resume = int(note.split("start_line=")[1].split()[0])
    assert rows.splitlines()[-1] == "401\ttail" and rows_seen == 401


@pytest.mark.parametrize(
    ("text", "room", "expected"),
    [
        # The control in the lookahead is not part of what fits: show "a" exactly.
        ("a\r", 1, ("a", 1, False)),
        ("abc\rdef", 3, ("abc", 3, False)),
        # Room for the escaped prefix, the mark included: escape it.
        ("a\rb", 6, ("a\\rb", 3, True)),
        ("a\rb", 4, ("a\\r", 2, True)),
        # Not even the escaped control fits: stop before it, unmarked.
        ("ab\rc", 3, ("ab", 2, False)),
        ("", 0, ("", 0, False)),
    ],
)
def test_fit_escapes_only_a_prefix_that_holds_a_control(text, room, expected) -> None:
    assert tools._fit(text, room) == expected


def test_a_page_ending_before_a_control_is_shown_exactly(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 12)
    # Nine characters fit; the CR is the tenth, inside the lookahead window.
    (repo / "late.txt").write_text("abcdefghi\rk\n")
    text, _ = call(repo, "read_file", path="late.txt")
    assert text.splitlines()[0] == "1\tabcdefghi"


def test_an_escaped_line_that_fits_is_encoded_once(monkeypatch) -> None:
    # Most escaped lines fit whole; only a line that must be cut needs the
    # longest-fit search.
    encoded = []
    encode = tools._json_body
    monkeypatch.setattr(tools, "_json_body", lambda text: encoded.append(text) or encode(text))
    line = ("\x01" + "q" * 99) * 300
    assert tools._fit(line, 1_000_000) == (encode(line), len(line), True)
    assert encoded == [line]


@needs_rg
def test_cut_and_capped_matches_stay_decodable(repo: Path, monkeypatch) -> None:
    # A marked, cut match decodes to exactly its first MAX_MATCH_CHARS
    # characters; no note shares the text field.
    raw = "hit\r" + "y\"\\" * 300
    (repo / "long.py").write_text(raw + "\n")
    text, is_error = call(repo, "grep", pattern="hit", path="long.py")
    assert not is_error
    prefix, body = text.removesuffix("\n").split(":1!+:", 1)
    assert prefix == "long.py"
    assert json.loads(f'"{body}"') == raw[: tools.MAX_MATCH_CHARS]
    # The overall cap stops between complete records and keeps its note.
    for index in range(40):
        (repo / f"m{index:02}.py").write_text(f"hit = '\\x01{index}'\n" + "hit\x01\n")
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 300)
    text, is_error = call(repo, "grep", pattern="hit")
    assert not is_error and len(text.encode()) <= tools.MAX_OUTPUT_BYTES
    *records, note = text.removesuffix("\n").split("\n")
    assert note == "[search output limit reached; narrow the path or pattern]"
    for record in records:
        name, number, shown = record.split(":", 2)
        if number.endswith("!"):
            json.loads(f'"{shown}"')  # Never cut inside an escape.


def test_binary_and_non_string_paths_are_refused(repo: Path) -> None:
    (repo / "blob.bin").write_bytes(b"\x00\x01")
    assert call(repo, "read_file", path="blob.bin") == ("binary file: blob.bin", True)
    assert call(repo, "read_file", path=["x"]) == ("path must be a string", True)


@needs_rg
def test_grep_times_out_and_kills_the_search(repo: Path, monkeypatch, tmp_path: Path) -> None:
    slow = tmp_path / "slow-rg"
    slow.write_text("#!/bin/sh\nexec sleep 30\n")
    slow.chmod(0o755)
    monkeypatch.setattr(tools, "RG", str(slow))
    monkeypatch.setattr(tools, "GREP_TIMEOUT_SECONDS", 0.2)
    assert call(repo, "grep", pattern="x") == ("search timed out after 0.2 seconds", True)


@needs_rg
def test_drained_records_count_toward_the_input_budget(repo: Path, monkeypatch) -> None:
    # Skipped remainders of oversized records are bounded too, so match-dense
    # files end the search with partial results rather than the timeout.
    monkeypatch.setattr(tools, "MAX_GREP_RECORD_BYTES", 4096)
    monkeypatch.setattr(tools, "MAX_GREP_INPUT_BYTES", 65536)
    for index in range(20):
        (repo / f"dense{index}.js").write_text("value;" * 2000 + "\n")
    text, is_error = call(repo, "grep", pattern="value")
    assert not is_error
    lines = text.splitlines()
    assert lines[-1] == (
        "[search stopped after reading 65536 bytes of results; narrow the path or pattern]"
    )
    assert 0 < len(lines) - 1 < 20


@needs_rg
def test_grep_signals_its_group_before_reaping_rg(repo: Path, monkeypatch) -> None:
    # Once rg is reaped its PID, and so its group ID, can be reused; the group
    # kill must reach rg while it is still an unreaped child.
    signalled = []
    killpg = os.killpg

    def checked_killpg(pgid: int, sig: int) -> None:
        state = os.waitid(os.P_PID, pgid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        signalled.append(state is not None)  # Raises ChildProcessError if reaped.
        killpg(pgid, sig)

    monkeypatch.setattr(tools.os, "killpg", checked_killpg)
    assert call(repo, "grep", pattern="value = 1") == ("pkg/module.py:1:value = 1\n", False)
    assert signalled == [True]


@needs_rg
def test_a_running_timeout_cannot_signal_after_grep_returns(repo: Path, monkeypatch) -> None:
    # cancel() cannot stop a timeout callback that has already started. grep
    # must wait for it, or the callback could signal a reaped, reused group.
    signals: list[str] = []
    monkeypatch.setattr(tools.os, "killpg", lambda pgid, sig: signals.append("kill"))

    class EnteredTimer(threading.Thread):
        """A timer whose callback starts as the search finishes, then lags."""

        def __init__(self, interval: float, function) -> None:
            super().__init__(daemon=True)
            self.function, self.entered = function, threading.Event()

        def run(self) -> None:
            self.entered.wait()
            time.sleep(0.2)
            self.function()

        def cancel(self) -> None:
            self.entered.set()

    timers: list[EnteredTimer] = []
    monkeypatch.setattr(
        tools.threading, "Timer", lambda *args: timers.append(EnteredTimer(*args)) or timers[-1]
    )
    # The search completed before the timeout decided anything: its result stands.
    assert call(repo, "grep", pattern="value = 1") == ("pkg/module.py:1:value = 1\n", False)
    returned = len(signals)
    timers[0].join(5)
    assert signals[returned:] == []



class _DeadlineAtReturn:
    """A timer that fires its callback just as the reader returns, then is spent."""

    def __init__(self, interval: float, function) -> None:
        self.function = function

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass


def _fire_deadline_after_reading(monkeypatch, *, wait_for_exit: bool) -> None:
    timers: list[_DeadlineAtReturn] = []

    def recorded_timer(*args) -> _DeadlineAtReturn:
        timers.append(_DeadlineAtReturn(*args))
        return timers[-1]

    monkeypatch.setattr(tools.threading, "Timer", recorded_timer)
    pids: list[int] = []
    popen = tools.subprocess.Popen

    def recorded_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(tools.subprocess, "Popen", recorded_popen)
    read = tools._read_matches

    def read_then_expire(stream, matches, **options):
        cut = read(stream, matches, **options)
        if wait_for_exit:  # Let rg exit by itself before the deadline lands.
            os.waitid(os.P_PID, pids[0], os.WEXITED | os.WNOWAIT)
        timers[0].function()
        return cut

    monkeypatch.setattr(tools, "_read_matches", read_then_expire)


@needs_rg
def test_a_deadline_at_completion_keeps_a_finished_search(repo: Path, monkeypatch) -> None:
    # rg wrote every record and exited by itself; a deadline that lands before
    # grep records that must not turn the whole result into a timeout.
    _fire_deadline_after_reading(monkeypatch, wait_for_exit=True)
    assert call(repo, "grep", pattern="value = 1") == ("pkg/module.py:1:value = 1\n", False)


@needs_rg
def test_a_deadline_after_the_output_cap_keeps_the_capped_result(repo: Path, monkeypatch) -> None:
    # The output budget had already ended the result when the deadline landed.
    for index in range(40):
        (repo / f"m{index:02}.py").write_text("hit\n")
    monkeypatch.setattr(tools, "MAX_OUTPUT_BYTES", tools._NOTE_RESERVE_BYTES + 60)
    _fire_deadline_after_reading(monkeypatch, wait_for_exit=False)
    text, is_error = call(repo, "grep", pattern="hit")
    assert not is_error
    assert text.splitlines()[-1] == "[search output limit reached; narrow the path or pattern]"


def test_server_refuses_a_credential_bearing_environment(repo: Path) -> None:
    script = Path(tools.__file__)
    clean = {"PATH": os.environ["PATH"], "HOME": str(repo), "LC_CTYPE": "C.UTF-8"}
    run = lambda env: subprocess.run(  # noqa: E731
        [sys.executable, "-I", str(script), "--root", str(repo)],
        input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    ok = run(clean)
    assert ok.returncode == 0 and json.loads(ok.stdout)["result"] == {}
    leaked = run({**clean, "OPENROUTER_API_KEY": "credential-value"})
    assert leaked.returncode == 2 and not leaked.stdout
    assert "OPENROUTER_API_KEY" in leaked.stderr and "credential-value" not in leaked.stderr


def test_stdio_protocol_offers_only_read_only_tools(repo: Path) -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "sentinel.txt"}},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "resources/write"},
    ]
    out = io.StringIO()
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages) + "\nnot json\n")
    tools.serve(repo, stdin, out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, 2, 3, 4, None]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    offered = replies[1]["result"]["tools"]
    assert {t["name"] for t in offered} == {"read_file", "list_dir", "grep"}
    assert all(t["annotations"]["readOnlyHint"] for t in offered)
    assert replies[2]["result"]["content"][0]["text"] == "1\tsnapshot-only-value"
    assert replies[3]["error"]["code"] == -32601 and replies[4]["error"]["code"] == -32700


def test_a_nul_pattern_cannot_end_the_server(repo: Path) -> None:
    grep = {"name": "grep", "arguments": {"pattern": "a\x00b"}}
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": grep},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ]
    out = io.StringIO()
    tools.serve(repo, io.StringIO("".join(json.dumps(m) + "\n" for m in messages)), out)
    first, second = (json.loads(line) for line in out.getvalue().splitlines())
    assert first["result"]["isError"] is True and second == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {},
    }


def test_module_is_standard_library_only() -> None:
    source = Path(tools.__file__).read_text()
    assert "ai_review" not in source.split('"""', 2)[2].split("def ", 1)[0]


def merge(target: dict, update: dict) -> None:
    for key, value in update.items():
        if isinstance(value, dict):
            merge(target.setdefault(key, {}), value)
        else:
            target[key] = value


@pytest.mark.parametrize("stage", ["review", "critique"])
# A non-BMP character, which JSON would escape as a surrogate pair, and DEL,
# which TOML requires escaped.
@pytest.mark.parametrize("output_name", ['out "dir"', "out \\ \U0001f600 \x7f"])
def test_registry_renders_parseable_no_shell_configuration(
    tmp_path: Path, stage: str, output_name: str
) -> None:
    inputs = tmp_path / 'in\'puts "q"'
    (inputs / "repo_snapshot").mkdir(parents=True)
    env = {
        "AI_REVIEW_INPUT_DIR": str(inputs),
        "AI_REVIEW_OUTPUT_DIR": str(tmp_path / output_name),
        "AI_REVIEW_MODEL": "openai/gpt-6.1-sol",
        "OPENROUTER_BASE_URL": "https://openrouter.example/api/v1",
    }
    arguments, root = prepare_shell_adapter(get_reviewer_definition("codex"), stage, env)
    overrides: dict = {}
    for index, argument in enumerate(arguments):
        if argument.startswith("--config="):
            value = argument.removeprefix("--config=")
        elif argument == "--config":
            value = arguments[index + 1]
        else:
            continue
        merge(overrides, tomllib.loads(value))
    assert overrides["features"]["shell_tool"] is False
    assert overrides["features"]["view_image"] is False
    assert overrides["features"]["multi_agent_v2"] == {
        "enabled": False,
        "disable_native_tools": True,
    }
    if stage == "critique":
        assert "mcp_servers" not in overrides
        return
    server = overrides["mcp_servers"]["repo"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-I", tools.__file__, "--root", str(root)]
    assert server["required"] is True and server["default_tools_approval_mode"] == "approve"
    assert "env" not in server


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses non-UTF-8 names")
def test_registry_refuses_a_path_toml_cannot_represent(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    (inputs / "repo_snapshot").mkdir(parents=True)
    output = Path(os.fsdecode(bytes(tmp_path) + b"/out-\xff"))
    env = {
        "AI_REVIEW_INPUT_DIR": str(inputs),
        "AI_REVIEW_OUTPUT_DIR": str(output),
        "AI_REVIEW_MODEL": "openai/gpt-6.1-sol",
        "OPENROUTER_BASE_URL": "https://openrouter.example/api/v1",
    }
    with pytest.raises(ValueError, match="cannot be passed to the reviewer CLI as TOML"):
        prepare_shell_adapter(get_reviewer_definition("codex"), "review", dict(env))
    # The refusal comes before the snapshot copy, so nothing is left behind.
    assert not output.exists()
    # Seats whose arguments never pass these paths as TOML still launch.
    for reviewer, stage in (("claude", "review"), ("cursor", "review"), ("codex", "critique")):
        arguments, root = prepare_shell_adapter(get_reviewer_definition(reviewer), stage, dict(env))
        assert arguments and root.is_dir()
