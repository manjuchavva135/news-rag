"""A stand-in for `vllm serve` of indic-ocr's text model (installed as <venv>/bin/vllm).

- Records its argv to $FAKE_OCR_DIR/vllm-start-<pid>.json and every chat request (prompt,
  max_tokens, image size) as a line of $FAKE_OCR_DIR/vllm-requests.jsonl.
- Replies `<table><tr><td>1</td></tr></table>` to the table prompt and ` తెలుగు ` to the text
  prompt; with FAKE_OCR_VLLM_MODE=nfd, decomposed (NFD) Telugu instead.
"""

import base64
import json
import os
import signal
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

argv = sys.argv[1:]
out = Path(os.environ["FAKE_OCR_DIR"])
out.mkdir(parents=True, exist_ok=True)
(out / f"vllm-start-{os.getpid()}.json").write_text(json.dumps({"argv": argv}))
port = int(argv[argv.index("--port") + 1])
name = argv[argv.index("--served-model-name") + 1]
write_lock = threading.Lock()
NFD_TELUGU = "కై"


def png_size(data: bytes) -> list[int]:
    return list(struct.unpack(">II", data[16:24]))


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
        parts = body["messages"][-1]["content"]
        prompt = " ".join(p["text"] for p in parts if p.get("type") == "text")
        url = next(p["image_url"]["url"] for p in parts if p.get("type") == "image_url")
        size = png_size(base64.b64decode(url.split(",", 1)[1]))
        with write_lock, open(out / "vllm-requests.jsonl", "a", encoding="utf-8") as fh:
            record = {"prompt": prompt, "max_tokens": body.get("max_tokens"), "size": size}
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if "HTML" in prompt:
            text = "<table><tr><td>1</td></tr></table>"
        elif os.environ.get("FAKE_OCR_VLLM_MODE") == "nfd":
            text = NFD_TELUGU
        else:
            text = " తెలుగు "
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
                        "message": {"role": "assistant", "content": text},
                    }
                ],
            }
        )


signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))

print(f"fake vllm serving {name} on {port}", flush=True)
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
