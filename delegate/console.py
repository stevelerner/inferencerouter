#!/usr/bin/env python3
"""Live savings console for ask_local. Read-only view of the call log (standard library only).

    python3 console.py [--port 8001] [--host 127.0.0.1] [--log PATH]

Open http://localhost:8001. Set CLAUDE_IN_PER_MTOK / CLAUDE_OUT_PER_MTOK to show a dollar estimate.
"""
import argparse
import datetime
import http.server
import json
from pathlib import Path

import stats

HTML = (Path(__file__).parent / "console.html").read_bytes()
FEED = 40


def snapshot(log):
    rows = stats.load(log)
    feed = []
    for r in reversed(rows[-FEED:]):
        feed.append({
            "t": datetime.datetime.fromtimestamp(r.get("ts", 0)).strftime("%H:%M:%S"),
            "ok": bool(r.get("ok")), "files": r.get("n_paths", 0), "secs": r.get("secs"),
            "material": r.get("material_tokens", 0), "answer": r.get("result_tokens", 0),
            "truncated": bool(r.get("truncated")), "note": bool(r.get("note")),
            "err": (r.get("err") or "")[:120] if not r.get("ok") else "",
        })
    return {"summary": stats.summarize(rows), "feed": feed, "log": str(log)}


def make_server(log, host="127.0.0.1", port=8001):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body, ctype = HTML, "text/html; charset=utf-8"
            elif self.path == "/stats":
                body, ctype = json.dumps(snapshot(log)).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    return http.server.ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to view from another machine (no auth)")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--log", default=None)
    a = ap.parse_args()
    srv = make_server(stats.log_path(a.log), a.host, a.port)
    print(f"delegate console on http://{a.host}:{a.port}  (log: {stats.log_path(a.log)})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
