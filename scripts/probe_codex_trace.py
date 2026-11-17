"""COMPAT-012: inspect a pinned debug trace, exporting only fixed aggregates.

This is temporary diagnostic evidence, never adapter admission. Raw files stay
in the container's private temporary directory and are removed before exit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def summarize_trace(root: Path) -> dict:
    summary = {
        "trace_bundles": 0,
        "nested_exec_started": 0,
        "nested_exec_completed": 0,
        "nested_exec_failed": 0,
        "nested_exec_other_status": 0,
        "nested_exec_nonzero_result": 0,
        "nested_exec_bwrap_failure": 0,
        "validation_errors": 0,
        "recording_completed": True,
        "complete": False,
    }
    for trace in root.glob("*/trace.jsonl"):
        summary["trace_bundles"] += 1
        try:
            if trace.is_symlink() or trace.parent.is_symlink():
                raise ValueError("trace link")
            events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
            if not events or any(event["schema_version"] != 2 for event in events):
                raise ValueError("unknown trace schema")
            payloads = [event["payload"] for event in events]
            summary["recording_completed"] &= any(
                item["type"] == "recording_completed" for item in payloads
            )
            calls = {
                item["tool_call_id"]
                for item in payloads
                if item["type"] == "tool_call_started"
                and item.get("requester", {}).get("type") == "code_cell"
                and item.get("summary", {}).get("label")
                in ("exec_command", "functions.exec_command")
            }
            summary["nested_exec_started"] += len(calls)
            ended = set()
            for item in payloads:
                if item["type"] != "tool_call_ended" or item["tool_call_id"] not in calls:
                    continue
                if item["tool_call_id"] in ended:
                    raise ValueError("duplicate end")
                ended.add(item["tool_call_id"])
                status = item.get("status")
                counter = status if status in ("completed", "failed") else "other_status"
                summary[f"nested_exec_{counter}"] += 1
                relative = item["result_payload"]["path"]
                if not re.fullmatch(r"payloads/[0-9]+\.json", relative):
                    raise ValueError("unexpected payload reference")
                path = trace.parent / relative
                if path.is_symlink() or path.parent.is_symlink():
                    raise ValueError("payload link")
                result = json.loads(path.read_text())
                if (
                    not isinstance(result, dict)
                    or result.get("type") != "code_mode_response"
                    or not isinstance(result.get("value"), dict)
                ):
                    raise ValueError("unexpected result shape")
                value = result["value"]
                nonzero = type(value.get("exit_code")) is int and value["exit_code"] != 0
                summary["nested_exec_nonzero_result"] += int(nonzero)
                output = value.get("output")
                summary["nested_exec_bwrap_failure"] += int(
                    nonzero and isinstance(output, str) and output.lstrip().startswith("bwrap:")
                )
            if calls != ended:
                raise ValueError("unfinished nested dispatch")
        except OSError, ValueError, KeyError, TypeError:
            # No exception text, paths, IDs, arguments, or tool output leave the
            # private trace directory, including malformed-payload diagnostics.
            summary["validation_errors"] += 1
    summary["recording_completed"] &= summary["trace_bundles"] > 0
    summary["complete"] = (
        summary["trace_bundles"] == 1
        and summary["recording_completed"]
        and summary["validation_errors"] == 0
        and summary["nested_exec_started"] > 0
    )
    return summary
