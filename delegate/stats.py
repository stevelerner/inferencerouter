"""Shared log reading and aggregation for report.py and console.py (standard library only)."""
import json
import os
import time
from pathlib import Path

DEFAULT_LOG = "~/.local/state/local-delegate/calls.jsonl"


def log_path(arg=None):
    return Path(arg or os.environ.get("DELEGATE_LOG", DEFAULT_LOG)).expanduser()


def load(path, days=None):
    """Return logged calls, oldest first. Unreadable lines are skipped, a missing log is empty."""
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if days:
        rows = [r for r in rows if r.get("ts", 0) >= time.time() - days * 86400]
    return rows


def prices():
    """(input, output) dollars per million Claude tokens from the environment, or None."""
    try:
        return float(os.environ["CLAUDE_IN_PER_MTOK"]), float(os.environ["CLAUDE_OUT_PER_MTOK"])
    except (KeyError, ValueError):
        return None


def summarize(rows):
    """Totals for a list of logged calls. Token counts for Claude-side text are estimates (chars/4)."""
    ok = [r for r in rows if r.get("ok")]
    t = {
        "calls": len(rows),
        "ok": len(ok),
        "failed": len(rows) - len(ok),
        "truncated": sum(bool(r.get("truncated")) for r in ok),
        "notes": sum(bool(r.get("note")) for r in ok),
        "qwen_in": sum(r.get("qwen_in", 0) for r in ok),
        "qwen_out": sum(r.get("qwen_out", 0) for r in ok),
        "secs": round(sum(r.get("secs", 0) for r in ok), 1),
        "not_read": sum(r.get("material_tokens", 0) for r in ok),   # material Claude never ingested
        "sent": sum(r.get("task_tokens", 0) for r in ok),           # Claude OUTPUT tokens (task text)
        "got": sum(r.get("result_tokens", 0) for r in ok),          # Claude INPUT tokens (the answers)
    }
    t["net_input_avoided"] = t["not_read"] - t["got"]
    p = prices()
    t["dollars_saved"] = (round(t["net_input_avoided"] / 1e6 * p[0] - t["sent"] / 1e6 * p[1], 4) if p else None)
    return t
