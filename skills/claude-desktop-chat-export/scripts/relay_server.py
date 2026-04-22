#!/usr/bin/env python3
"""
Tiny local HTTP relay for MCP-driven claude.ai exports.

The documented browser-console path uses `a.click()` to trigger downloads to
~/Downloads. When the export is driven via the claude-in-chrome MCP extension,
that download doesn't actually land on disk, and the MCP's javascript_tool
return value filters out cookie-like and base64-looking payloads — so the
conversation JSON can't be returned from JS either.

Workaround: run this relay locally, then have the browser POST the fetched
conversation JSON and each image blob to it. The relay writes files straight
into the export directory.

Usage:
    python3 relay_server.py <output-dir> [--port 8765]

The server runs in the foreground. Ctrl+C to stop. Files land at:
    <output-dir>/<X-Filename header value>

The `X-Filename` header may contain a relative subpath (e.g. `files/abc.webp`);
subdirectories are created as needed. Path traversal (`..`, absolute paths) is
rejected.

CORS: Access-Control-Allow-Origin is set to `*` so claude.ai can POST to it.
Access-Control-Allow-Private-Network is also set for Chrome's PNA checks.

From the browser (in DevTools console on claude.ai):

    const r = await fetch(`/api/organizations/${ORG}/chat_conversations/${CONV}?tree=True&rendering_mode=messages&render_all_tools=true`, {credentials:'include'});
    await fetch('http://127.0.0.1:8765/upload', {
      method: 'POST',
      headers: {'Content-Type':'application/json', 'X-Filename':'conversation.json'},
      body: await r.blob()
    });
"""

from __future__ import annotations

import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    out_dir: str = ""

    def log_message(self, *_args, **_kwargs):  # suppress access log noise
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS, GET")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(b"relay alive")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length) if length else b""

        name = self.headers.get("X-Filename") or "payload.bin"
        target = self._safe_path(name)
        if target is None:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b"invalid X-Filename")
            return

        os.makedirs(os.path.dirname(target) or self.out_dir, exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)

        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(f"saved {len(data)} bytes -> {target}".encode())

    def _safe_path(self, name: str) -> str | None:
        """Resolve `name` under out_dir, refusing escapes."""
        if not name or name.startswith("/") or ".." in name.split("/"):
            return None
        out_abs = os.path.realpath(self.out_dir)
        target = os.path.realpath(os.path.join(self.out_dir, name))
        if not (target == out_abs or target.startswith(out_abs + os.sep)):
            return None
        return target


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Local HTTP relay for MCP-driven claude.ai exports")
    ap.add_argument("out_dir", help="Directory to write uploaded files into")
    ap.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    ap.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    args = ap.parse_args(argv)

    out_dir = os.path.realpath(os.path.expanduser(args.out_dir))
    os.makedirs(out_dir, exist_ok=True)
    _Handler.out_dir = out_dir

    server = HTTPServer((args.host, args.port), _Handler)
    print(f"relay listening on http://{args.host}:{args.port} -> {out_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
