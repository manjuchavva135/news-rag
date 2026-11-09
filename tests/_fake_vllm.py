"""A stand-in for `vllm serve` used by the CPU tests (installed as <venv>/bin/vllm).

- Records its argv to $FAKE_VLLM_DIR/start-<pid>.json.
- FAKE_VLLM_MODE=exit: prints `boom: fake vllm failed` and exits 3 at once.
- Otherwise serves GET /v1/models (the --served-model-name) and POST /v1/chat/completions
  (records the body to $FAKE_VLLM_DIR/req-<n>.json, replies `echo: <last text>`).
"""

import itertools
import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

argv = sys.argv[1:]
out = Path(os.environ.get("FAKE_VLLM_DIR", "."))
out.mkdir(parents=True, exist_ok=True)
(out / f"start-{os.getpid()}.json").write_text(json.dumps({"argv": argv, "pgid": os.getpgrp()}))
if os.environ.get("FAKE_VLLM_MODE") == "exit":
    print("boom: fake vllm failed", flush=True)
    sys.exit(3)

port = int(argv[argv.index("--port") + 1])
name = argv[argv.index("--served-model-name") + 1]
counter = itertools.count()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send({"object": "list", "data": [{"id": name, "object": "model"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        (out / f"req-{next(counter)}.json").write_text(json.dumps(body))
        content = body["messages"][-1]["content"]
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        self._send(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": f"echo: {content}"},
                    }
                ],
            }
        )


signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
print(f"fake vllm serving {name} on {port}", flush=True)
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
