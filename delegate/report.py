#!/usr/bin/env python3
"""Savings report from the ask_local call log. Read-only.

    python3 report.py [--days N] [--log PATH]

Set CLAUDE_IN_PER_MTOK / CLAUDE_OUT_PER_MTOK (dollars per million tokens) to also print a dollar estimate.
"""
import argparse

import stats

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=float, default=None, help="only calls from the last N days")
ap.add_argument("--log", default=None, help="call log (default: $DELEGATE_LOG or the standard location)")
a = ap.parse_args()

path = stats.log_path(a.log)
rows = stats.load(path, a.days)
if not rows:
    raise SystemExit(f"no calls logged in {path}")
t = stats.summarize(rows)

print(f"calls: {t['calls']}  ok: {t['ok']}  failed: {t['failed']}  truncated: {t['truncated']}  with NOTE: {t['notes']}")
print(f"qwen: {t['qwen_in']:,} tokens in, {t['qwen_out']:,} out, {t['secs']:.0f}s total (free, local)")
print(f"Claude input not read:   ~{t['not_read']:,} tokens")
print(f"Claude spent instead:    ~{t['sent']:,} output (task text) + ~{t['got']:,} input (answers)")
print(f"net input avoided:       ~{t['net_input_avoided']:,} tokens, at the cost of ~{t['sent']:,} output tokens")
if t["dollars_saved"] is not None:
    print(f"estimated saving:        ${t['dollars_saved']:.4f}")
print("Assumes Claude would otherwise have read the same material once; excludes the tool schema "
      "(~250 tokens/turn while the server is enabled).")
