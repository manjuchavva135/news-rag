"""The stdin/stdout loop both OCR workers share. Stdlib only: copied into neither venv, it is
imported by the worker scripts from their own directory, so it must never import `newsrag`.

The worker keeps the real stdout for protocol lines only; everything else written to fd 1 (a
library's print, a C extension's log) goes to stderr instead, so it cannot break the protocol.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from typing import Any

Handler = Callable[[dict[str, Any]], dict[str, Any]]


def protocol_stdout():
    """Detach the real stdout for protocol lines; point fd 1 and sys.stdout at stderr."""
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return proto


def serve(load: Callable[[], Handler]) -> None:
    """Load the model once, report the load time on stderr, then answer one line per line."""
    out = protocol_stdout()
    started = time.monotonic()
    handle = load()
    print(f"model loaded in {time.monotonic() - started:.1f} s", file=sys.stderr, flush=True)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise TypeError("request is not a JSON object")
            reply = handle(request)
        except Exception as exc:  # noqa: BLE001 - one bad request never stops the worker
            traceback.print_exc(file=sys.stderr)
            reply = {"error": f"{type(exc).__name__}: {exc}"}
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        out.flush()
