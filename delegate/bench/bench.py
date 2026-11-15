#!/usr/bin/env python3
"""A/B harness: Sonnet 5.5 alone (arm A) vs Sonnet 5.5 + local Qwen via delegate.py (arm B).

    bench.py list
    bench.py check  TASK            # base must FAIL the done-command, gold must PASS it (no model calls)
    bench.py run    TASK A|B [-n 1] [--budget 3.0] [--keep]
    bench.py report [TASK]

Each run: fresh detached git worktree of the pinned tinyolly commit + the task overlay (spec, tests) committed,
a headless `claude -p` session, then an independent re-run of the done-command and regression command.
Results append to results.jsonl. Stdlib only.
"""
import argparse, json, os, shutil, statistics, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("BENCH_REPO", "/Volumes/external/code/tinyolly"))
DELEGATE = HERE.parent / "implement.py"
WORK = Path("/tmp/bench")
RESULTS = HERE / "results.jsonl"
MODEL, EFFORT = "claude-sonnet-5-5", "medium"

PROMPT_A = """You are in a git repo. Read TASK.md and complete the task.
The task is done when its 'Done when' command exits 0. Edit only files in 'May change'. Never edit the tests or check scripts.
Be economical: read only what you need. When done, reply with one sentence."""

PROMPT_B = """You are in a git repo. Read TASK.md. Your job is to get the task done by delegating the coding to a local model, then reviewing it.
Do NOT write or edit implementation code yourself unless step 4 applies.
1. Run: IMPLEMENT_LOG={log} python3 {delegate} TASK.md --repo . --in-place
   It edits this working tree directly and runs the 'Done when' command itself.
2. Read the output and `git diff`. Judge it like a code reviewer (correct, minimal, in scope, follows the spec in TASK.md).
3. If it passed and the diff is fine, stop. Reply with one sentence.
4. If it failed or the diff is wrong: you may sharpen the Constraints section of TASK.md and run step 1 once more. Only if that also fails, fix the code by hand.
Never edit the tests or check scripts. Edit only files in 'May change'. Be economical."""


def sh(cmd, cwd=None, timeout=1800, env=None):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return p.returncode, p.stdout + p.stderr


def load_task(tid):
    d = HERE / "tasks" / tid
    if not d.is_dir():
        sys.exit(f"unknown task {tid}; have: {[p.name for p in (HERE / 'tasks').iterdir() if p.is_dir()]}")
    return {**json.loads((d / "task.json").read_text()), "dir": d}


def make_worktree(task, name):
    wt = WORK / name
    WORK.mkdir(exist_ok=True)
    code, out = sh(["git", "worktree", "add", "--detach", str(wt), task["base"]], REPO)
    if code:
        sys.exit(out)
    shutil.copytree(task["dir"] / "overlay", wt, dirs_exist_ok=True)
    sh("git add -A && git -c user.name=bench -c user.email=bench@localhost commit -qm 'bench base'", wt)
    return wt


def drop_worktree(wt):
    sh(["git", "worktree", "remove", "--force", str(wt)], REPO)


def gold_into(task, wt):
    g = task["dir"]
    if (g / "gold.diff").exists():
        code, out = sh(["git", "apply", str(g / "gold.diff")], wt)
        assert code == 0, out
    elif (g / "gold").is_dir():
        shutil.copytree(g / "gold", wt / "k8s-istio-demo", dirs_exist_ok=True)


def cmd_check(a):
    t = load_task(a.task)
    wt = make_worktree(t, f"check-{t['id']}")
    try:
        code, out = sh(t["done"], wt)
        print(f"base : done-command exit {code} (want non-zero)  {out.strip().splitlines()[-1] if out.strip() else ''}")
        ok = code != 0
        gold_into(t, wt)
        code, out = sh(t["done"], wt)
        print(f"gold : done-command exit {code} (want 0)         {out.strip().splitlines()[-1] if out.strip() else ''}")
        ok = ok and code == 0
        code, out = sh(t["regression"], wt)
        print(f"gold : regression exit {code} (want 0)")
        sys.exit(0 if ok and code == 0 else 1)
    finally:
        drop_worktree(wt)


def run_once(t, arm, budget, keep):
    run_id = f"{t['id']}-{arm}-{time.strftime('%H%M%S')}"
    wt = make_worktree(t, run_id)
    log = WORK / f"{run_id}.delegate.jsonl"
    prompt = PROMPT_A if arm == "A" else PROMPT_B.format(log=log, delegate=DELEGATE)
    cmd = ["claude", "-p", prompt, "--model", MODEL, "--effort", EFFORT, "--output-format", "json",
           "--max-budget-usd", str(budget), "--permission-mode", "bypassPermissions", "--no-session-persistence",
           "--strict-mcp-config"]
    t0 = time.time()
    try:
        code, out = sh(cmd, wt, timeout=3600, env={"IMPLEMENT_LOG": str(log)})
    except subprocess.TimeoutExpired:
        code, out = 124, ""
    wall = round(time.time() - t0, 1)
    try:
        j = json.loads(next(l for l in reversed(out.splitlines()) if l.startswith("{")))
    except Exception:
        j = {"is_error": True, "result": out[-500:]}
    u = j.get("usage") or {}

    pass_code, pass_out = sh(t["done"], wt)
    reg_code, _ = sh(t["regression"], wt)
    _, changed = sh("git add -N . && git diff --name-only HEAD", wt)
    changed = [c for c in changed.split("\n") if c]
    tampered = [p for p in t["protected"] if p in changed]
    may = [l.strip() for l in (wt / "TASK.md").read_text().split("## May change")[1].split("##")[0].splitlines() if l.strip()]
    out_of_scope = [c for c in changed if c not in may and c != "TASK.md"]
    _, stat = sh("git diff --stat HEAD", wt)
    _, diff = sh("git diff HEAD", wt)
    qwen = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    delegate_diff = next(iter((Path("/tmp/implement")).glob(f"{wt.name}-*.diff")), None) if arm == "B" else None
    claude_edited = None
    if arm == "B" and delegate_diff:
        claude_edited = delegate_diff.read_text().strip() != diff.strip()
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run": run_id, "task": t["id"], "arm": arm, "model": MODEL, "effort": EFFORT,
        "passed": pass_code == 0 and not tampered and not out_of_scope, "out_of_scope": out_of_scope, "regression_ok": reg_code == 0, "tampered": tampered,
        "claude_cost_usd": j.get("total_cost_usd"), "claude_turns": j.get("num_turns"), "claude_error": bool(j.get("is_error")),
        "in_tokens": u.get("input_tokens"), "cache_read_tokens": u.get("cache_read_input_tokens"),
        "cache_write_tokens": u.get("cache_creation_input_tokens"), "out_tokens": u.get("output_tokens"),
        "wall_s": wall, "delegate_calls": len(qwen), "qwen_rounds": sum(q.get("rounds", 0) for q in qwen),
        "qwen_prompt_tokens": sum(q.get("prompt_tokens", 0) for q in qwen),
        "qwen_completion_tokens": sum(q.get("completion_tokens", 0) for q in qwen),
        "qwen_seconds": round(sum(q.get("seconds", 0) for q in qwen), 1), "claude_edited_after_delegate": claude_edited,
        "diff_lines": len(diff.splitlines()), "files": changed, "summary": (j.get("result") or "")[:300],
    }
    RESULTS.open("a").write(json.dumps(row) + "\n")
    (WORK / f"{run_id}.diff").write_text(diff)
    print(f"{run_id}: {'PASS' if row['passed'] else 'FAIL'} cost=${row['claude_cost_usd']} out={row['out_tokens']} "
          f"cache_read={row['cache_read_tokens']} turns={row['claude_turns']} wall={wall}s qwen_calls={row['delegate_calls']} "
          f"diff={WORK / (run_id + '.diff')}")
    if keep:
        print(f"kept worktree {wt}")
    else:
        drop_worktree(wt)
    return row


def cmd_run(a):
    t = load_task(a.task)
    for _ in range(a.n):
        run_once(t, a.arm, a.budget, a.keep)


def med(xs):
    xs = [x for x in xs if x is not None]
    return (statistics.median(xs), min(xs), max(xs)) if xs else (None, None, None)


def fmt(m, money=False):
    if m[0] is None:
        return "-"
    f = (lambda v: f"${v:.3f}") if money else (lambda v: f"{v:,.0f}")
    return f"{f(m[0])} ({f(m[1])}-{f(m[2])})"


def cmd_report(a):
    rows = [json.loads(l) for l in RESULTS.read_text().splitlines()] if RESULTS.exists() else []
    if a.task:
        rows = [r for r in rows if r["task"] == a.task]
    if not rows:
        sys.exit("no results yet")
    grades = {}
    if GRADES.exists():
        for l in GRADES.read_text().splitlines():
            g = json.loads(l)
            grades[g["run"]] = g["score"]
    print(f"date {time.strftime('%Y-%m-%d')}  model {MODEL} effort {EFFORT}; median (min-max). Cache reads are cheap; compare cost.\n")
    for task in sorted({r["task"] for r in rows}):
        print(f"## {task}")
        print("| arm | n | pass | regress | claude cost | out tokens | cache read | turns | wall s | qwen calls | qwen tok | claude hand-edited | quality (0-2) |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        stats = {}
        for arm in "AB":
            rs = [r for r in rows if r["task"] == task and r["arm"] == arm]
            if not rs:
                continue
            stats[arm] = med([r["claude_cost_usd"] for r in rs])
            q = med([r["qwen_prompt_tokens"] + r["qwen_completion_tokens"] for r in rs])
            ed = sum(1 for r in rs if r["claude_edited_after_delegate"])
            print(f"| {arm} | {len(rs)} | {sum(r['passed'] for r in rs)}/{len(rs)} | {sum(r['regression_ok'] for r in rs)}/{len(rs)} | "
                  f"{fmt(stats[arm], True)} | {fmt(med([r['out_tokens'] for r in rs]))} | {fmt(med([r['cache_read_tokens'] for r in rs]))} | "
                  f"{fmt(med([r['claude_turns'] for r in rs]))} | {fmt(med([r['wall_s'] for r in rs]))} | "
                  f"{fmt(med([r['delegate_calls'] for r in rs]))} | {fmt(q)} | {ed if arm == 'B' else '-'} | "
                  f"{fmt(med([grades.get(r['run']) for r in rs]))} |")
        if "A" in stats and "B" in stats and stats["A"][0] and stats["B"][0]:
            s = stats["A"][0] - stats["B"][0]
            print(f"\nSavings on Claude cost: ${s:.3f} per task ({s / stats['A'][0] * 100:.0f}%)\n")
        else:
            print()


GRADES = HERE / "grades.jsonl"


def cmd_grade(a):
    """Quality grade after reading the saved diff: 2 = would merge as is, 1 = merge after small fixes, 0 = rewrite."""
    GRADES.open("a").write(json.dumps({"run": a.run, "score": a.score, "note": a.note}) + "\n")
    print("saved")


def cmd_list(a):
    for p in sorted((HERE / "tasks").iterdir()):
        if p.is_dir():
            print(p.name, json.loads((p / "task.json").read_text())["kind"])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(f=cmd_list)
    c = sub.add_parser("check"); c.add_argument("task"); c.set_defaults(f=cmd_check)
    r = sub.add_parser("run"); r.add_argument("task"); r.add_argument("arm", choices=["A", "B"])
    r.add_argument("-n", type=int, default=1); r.add_argument("--budget", type=float, default=3.0)
    r.add_argument("--keep", action="store_true"); r.set_defaults(f=cmd_run)
    g = sub.add_parser("grade"); g.add_argument("run"); g.add_argument("score", type=int, choices=[0, 1, 2])
    g.add_argument("note", nargs="?", default=""); g.set_defaults(f=cmd_grade)
    p = sub.add_parser("report"); p.add_argument("task", nargs="?"); p.set_defaults(f=cmd_report)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
