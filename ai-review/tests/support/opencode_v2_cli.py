"""Provisional fake of the tagged v2 HTTP contract; real-image CI must substantiate it."""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

trace = Path(os.environ["OPENCODE_CONFIG_DIR"])
trace.mkdir(parents=True, exist_ok=True)
(trace / "cli.args").write_text(" ".join(sys.argv))
(trace / "cli.env").write_text("".join(f"{k}={v}\n" for k, v in sorted(os.environ.items())))
(trace / "cli.key").write_text(os.environ["OPENROUTER_API_KEY"])
(trace / "opencode_config.json").write_text(os.environ["OPENCODE_CONFIG_CONTENT"])
password = os.environ.pop("OPENCODE_PASSWORD")
authorization = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
state: dict[str, object] = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def reply(self, status: int, value: object = None) -> None:
        encoded = b"" if status == 204 else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def authorized(self) -> bool:
        if self.headers.get("Authorization") != authorization:
            self.reply(401, {"type": "UnauthorizedError"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self.authorized():
            return
        path = urlsplit(self.path).path
        if path == "/api/info":
            self.reply(200, {"version": "2.2.5", "pid": os.getpid()})
        elif path == "/api/session/ses_fake/message":
            batch = {"critiques": []} if state.get("critique") else {"findings": []}
            self.reply(
                200,
                {
                    "data": [
                        {"id": "msg_idle", "type": "idle", "outcome": "succeeded"},
                        {
                            "id": "msg_answer",
                            "type": "assistant",
                            "finish": "stop",
                            "time": {"created": 1, "completed": 2},
                            "content": [
                                {"type": "reasoning", "text": "private thought"},
                                {"type": "text", "text": json.dumps(batch)},
                            ],
                        },
                        {"id": "msg_input", "type": "user"},
                    ],
                    "cursor": {"next": None, "previous": None},
                },
            )
        else:
            self.reply(404, {"type": "NotFound"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.authorized():
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        path = urlsplit(self.path).path
        if path == "/api/session":
            unknown = set(body) - {"title", "agent", "model", "location", "permissions"}
            if unknown:
                self.reply(400, {"type": "InvalidRequestError", "keys": sorted(unknown)})
                return
            state["directory"] = body["location"]["directory"]
        with (trace / "requests.ndjson").open("a") as handle:
            handle.write(
                json.dumps({"path": self.path, "directory": state.get("directory"), "body": body})
                + "\n"
            )
        if path == "/api/session":
            self.reply(200, {"data": {"id": "ses_fake", **body}})
        elif path == "/api/session/ses_fake/prompt":
            if set(body) != {"text"}:
                self.reply(400, {"type": "InvalidRequestError"})
                return
            directory = Path(str(state["directory"]))
            entries = sorted(
                f"{p.relative_to(directory)}{'/' if p.is_dir() else ''}"
                for p in directory.rglob("*")
                if not p.is_relative_to(directory / ".git")
            )
            (trace / "workspace.json").write_text(json.dumps(entries))
            state["critique"] = "<POOLED_FINDINGS_JSON>" in body["text"]
            self.reply(200, {"data": {"id": "msg_input", "sessionID": "ses_fake"}})
        elif path == "/api/experimental/session/ses_fake/wait":
            self.reply(204)
        else:
            self.reply(404, {"type": "NotFound"})


server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[sys.argv.index("--port") + 1])), Handler)
print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
sys.stdin.read()
server.shutdown()
server.server_close()
thread.join()
