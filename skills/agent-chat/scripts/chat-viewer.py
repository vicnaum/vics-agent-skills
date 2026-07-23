#!/usr/bin/env python3
"""agent-chat web viewer — read-only local UI over ~/.claude/agent-chat.

Serves chat-viewer.html plus a single /api/state endpoint. Stdlib only.
Binds 127.0.0.1 (local data). Never writes: cursors are not advanced.

Status shown is the cheap tier (hook stamps + TTL) — no AppleScript per
refresh. Semantics mirror agent-chat's _combine_state fallback: idle stamps
are trusted at any age; busy/waiting older than TTL are shown as stale.
"""

import json
import argparse
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path.home() / ".claude" / "agent-chat"
HTML = Path(__file__).resolve().parent / "chat-viewer.html"
STATUS_TTL = 180


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return out


def agent_status(name):
    p = BASE / "status" / name
    try:
        state, epoch = p.read_text().split()
        age = int(time.time()) - int(epoch)
    except (OSError, ValueError):
        return {"state": "unknown", "age": None}
    if state == "idle":
        return {"state": "idle", "age": age}
    if age <= STATUS_TTL:
        return {"state": state, "age": age}
    return {"state": "stale", "age": age}


def cursor(name, room):
    try:
        return int((BASE / "cursors" / f"{name}@{room}").read_text().strip())
    except (OSError, ValueError):
        return 0


def addressed_to(msg, name):
    return msg.get("from") != name and msg.get("to") in ("*", name)


def build_state(room_filter, limit):
    rooms, agents, messages = [], [], []
    room_msgs = {}

    for f in sorted((BASE / "rooms").glob("*.jsonl")):
        rname = f.stem
        msgs = read_jsonl(f)
        room_msgs[rname] = msgs
        rooms.append({
            "name": rname,
            "count": len(msgs),
            "last_ts": msgs[-1].get("ts") if msgs else None,
        })

    for f in sorted((BASE / "registry").glob("*.json")):
        try:
            j = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        name = j.get("name", f.stem)
        member_rooms = j.get("rooms", [])
        unread = 0
        for r in member_rooms:
            msgs = room_msgs.get(r, [])
            unread += sum(1 for m in msgs[cursor(name, r):] if addressed_to(m, name))
        agents.append({
            "name": name,
            "cli": j.get("cli", "claude"),
            "home_room": j.get("home_room"),
            "rooms": member_rooms,
            "cwd": j.get("cwd"),
            "backend": j.get("term_backend"),
            "status": agent_status(name),
            "unread": unread,
        })

    if room_filter and room_filter in room_msgs:
        messages = [dict(m, room=room_filter) for m in room_msgs[room_filter]]
    else:
        for rname, msgs in room_msgs.items():
            messages.extend(dict(m, room=rname) for m in msgs)
        messages.sort(key=lambda m: m.get("ts") or "")
    messages = messages[-limit:]

    return {"rooms": rooms, "agents": agents, "messages": messages,
            "room": room_filter, "now": int(time.time())}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            try:
                self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"chat-viewer.html not found", "text/plain")
        elif u.path == "/api/state":
            q = parse_qs(u.query)
            room = q.get("room", [None])[0]
            limit = min(int(q.get("limit", ["300"])[0]), 2000)
            body = json.dumps(build_state(room, limit)).encode()
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"agent-chat viewer: {url}  (ctrl-c to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
