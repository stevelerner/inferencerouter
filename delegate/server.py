#!/usr/bin/env python3
"""MCP server (stdio) exposing one tool, `ask_local`, that hands a bounded task to local Ollama.

Claude passes file *paths*; this process reads them and feeds qwen, so Claude never spends
tokens reading the material. Standard library only. Read-only: it never writes files.

Env (same names as the gateway): LOCAL_BASE (default http://127.0.0.1:11434; a trailing /v1 is ignored),
     LOCAL_MODEL (required). Optional: DELEGATE_LOG, DELEGATE_TIMEOUT. Needs Ollama (uses /api/chat).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = (os.environ.get("LOCAL_BASE") or "http://127.0.0.1:11434").rstrip("/")
BASE = BASE[:-3] if BASE.endswith("/v1") else BASE        # Ollama's native API lives above /v1
MODEL = os.environ.get("LOCAL_MODEL", "")
LOG = Path(os.environ.get("DELEGATE_LOG", "~/.local/state/local-delegate/calls.jsonl")).expanduser()
TIMEOUT = int(os.environ.get("DELEGATE_TIMEOUT", "300"))

MAX_FILE_BYTES = 256 * 1024
MAX_MATERIAL_TOKENS = 120_000            # leaves room in the 196k context for the answer
DEFAULT_OUT, MAX_OUT = 700, 4000
# Never feed these to the model (and so back to Claude), whatever Claude asks for.
DENY = re.compile(r"(^|/)(\.ssh|\.gnupg|\.aws|\.docker|\.kube|\.config/vastai|\.password-store)(/|$)"
                  r"|(^|/)(\.env[^/]*|id_(rsa|ed25519|ecdsa)[^/]*|[^/]*\.(pem|key|p12|kdbx))$", re.I)

SYSTEM = ("You are a local worker model assisting a stronger model. Do exactly the task, using only the "
          "material given; never invent file contents. Be terse: no preamble, no restating the task, no "
          "bold or filler, short plain sentences, and finish within the length asked for. "
          "Always give your best answer. If something is ambiguous, contradictory or missing from the "
          "material, add one final line starting 'NOTE:' saying what, in a few words. Never refuse.")

TOOL = {
    "name": "ask_local",
    "description": (
        "Offload a bounded, low-risk task to local qwen (free, private). Pass file paths, not pasted "
        "content, so you never read them. Fits: summarize/extract from big files or logs, docstrings, "
        "boilerplate, commit messages, format conversion, 'does X appear'. Returns text only, edits "
        "nothing, can be wrong: verify it, and heed any NOTE: line."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Self-contained; qwen sees no conversation."},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "Files to read (absolute or ~). Lines are numbered."},
            "text": {"type": "string", "description": "Inline material if not in a file."},
            "max_tokens": {"type": "integer", "description": f"Output cap (default {DEFAULT_OUT}, max {MAX_OUT})."},
            "think": {"type": "boolean", "description": "Reason first; slower."},
        },
        "required": ["task"],
    },
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}

SUPPORTED = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]


def est_tokens(s: str) -> int:
    return len(s) // 4


class ToolError(Exception):
    pass


def read_materials(paths, text):
    parts, used = [], 0
    for p in paths or []:
        path = Path(p).expanduser()
        try:
            path = path.resolve(strict=True)
        except OSError:
            raise ToolError(f"not found: {p}")
        if DENY.search(str(path)):
            raise ToolError(f"refused (sensitive path): {p}")
        if not path.is_file():
            raise ToolError(f"not a file: {p}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(f"too large (> {MAX_FILE_BYTES // 1024} KB): {p} - pass a narrower file or use text")
        body = path.read_bytes()
        if b"\0" in body[:4096]:
            raise ToolError(f"binary file: {p}")
        content = body.decode("utf-8", errors="replace")
        numbered = "\n".join(f"{i:>5}| {ln}" for i, ln in enumerate(content.split("\n"), 1))
        parts.append(f"=== {path} (line numbers are prefixes, not file content) ===\n{numbered}")
        used += est_tokens(content)
    if text:
        parts.append(f"=== inline text ===\n{text}")
        used += est_tokens(text)
    if used > MAX_MATERIAL_TOKENS:
        raise ToolError(f"material is ~{used} tokens, over the {MAX_MATERIAL_TOKENS} limit - split it")
    return "\n\n".join(parts), used


def log_call(rec):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError as e:
        print(f"log failed: {e}", file=sys.stderr)


def ask_local(args):
    if not MODEL:
        raise ToolError("LOCAL_MODEL is not set in this MCP server's environment (e.g. qwen3:14b)")
    task = (args.get("task") or "").strip()
    if not task:
        raise ToolError("task is required")
    material, mat_tokens = read_materials(args.get("paths"), args.get("text"))
    out_cap = max(1, min(int(args.get("max_tokens") or DEFAULT_OUT), MAX_OUT))
    user = task + ("\n\n" + material if material else "")
    body = json.dumps({
        "model": MODEL, "stream": False, "think": bool(args.get("think", False)),
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "options": {"temperature": 0.2, "num_predict": out_cap},
    }).encode()
    req = urllib.request.Request(f"{BASE}/api/chat", body, {"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            d = json.load(r)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log_call({"ts": time.time(), "ok": False, "err": str(e)[:200]})
        raise ToolError(f"local model unavailable ({e}); do the task yourself")
    secs = time.time() - t0
    reply = (d.get("message") or {}).get("content", "").strip()
    truncated = d.get("done_reason") == "length"
    if truncated:
        reply += "\n[TRUNCATED at max_tokens]"
    log_call({
        "ts": time.time(), "ok": True, "secs": round(secs, 2), "model": MODEL,
        "n_paths": len(args.get("paths") or []), "material_tokens": mat_tokens,
        "qwen_in": d.get("prompt_eval_count", 0), "qwen_out": d.get("eval_count", 0),
        "task_tokens": est_tokens(task), "result_tokens": est_tokens(reply),
        "truncated": truncated, "note": "\nNOTE:" in "\n" + reply,
    })
    return reply or "NOTE: local model returned nothing - do the task yourself"


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion")
        return {"protocolVersion": want if want in SUPPORTED else SUPPORTED[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "local-delegate", "version": "0.1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        p = msg.get("params") or {}
        if p.get("name") != TOOL["name"]:
            raise KeyError(f"unknown tool: {p.get('name')}")
        try:
            return {"content": [{"type": "text", "text": ask_local(p.get("arguments") or {})}], "isError": False}
        except ToolError as e:
            return {"content": [{"type": "text", "text": f"ask_local error: {e}"}], "isError": True}
    if mid is None:                      # notification we do not handle (e.g. notifications/initialized)
        return None
    raise LookupError(f"method not found: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        try:
            result = handle(msg)
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": str(e)}}
        except KeyError as e:
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": str(e)}}
        except Exception as e:           # never crash the transport
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"internal: {e}"}}
        else:
            if mid is None or result is None:
                continue
            reply = {"jsonrpc": "2.0", "id": mid, "result": result}
        if mid is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
