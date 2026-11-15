#!/usr/bin/env python3
"""Hand a coding task to the local Qwen on Ollama; get back a reviewed-by-tests diff.

    implement.py TASK.md [--repo DIR] [--rounds 3] [--in-place] [--cleanup]

Creates a git worktree on branch qwen/<task>, asks the model for file edits, applies them (allow-list enforced),
runs the task's test command, feeds failures back up to --rounds times, then prints a short report.
Nothing is committed. Review with: git -C <worktree> diff. Stdlib only.
Env (same names as server.py): LOCAL_BASE (a trailing /v1 is ignored), LOCAL_MODEL (both required).
Optional: IMPLEMENT_NUM_CTX (131072), IMPLEMENT_THINK (false), IMPLEMENT_MAX_PROMPT_TOKENS (90000), IMPLEMENT_LOG.
"""
import argparse, fnmatch, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST = (os.environ.get("LOCAL_BASE") or "").rstrip("/")
HOST = HOST[:-3] if HOST.endswith("/v1") else HOST
MODEL = os.environ.get("LOCAL_MODEL", "")
NUM_CTX = int(os.environ.get("IMPLEMENT_NUM_CTX", "131072"))
THINK = {"false": False, "true": True}.get(os.environ.get("IMPLEMENT_THINK", "false"), os.environ.get("IMPLEMENT_THINK"))
LOG = Path(os.environ.get("IMPLEMENT_LOG", "~/.local/state/local-delegate/implement.jsonl")).expanduser()
MAX_PROMPT_TOKENS = int(os.environ.get("IMPLEMENT_MAX_PROMPT_TOKENS", "90000"))


def sh(cmd, cwd, timeout=600):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def parse_task(path: Path) -> dict:
    sections, cur = {}, None
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            cur = line[3:].strip().lower()
            sections[cur] = []
        elif cur is not None:
            sections[cur].append(line)
    lines = lambda k: [l.strip() for l in sections.get(k, []) if l.strip() and not l.strip().startswith("<")]
    text = lambda k: "\n".join(sections.get(k, [])).strip()
    task = {"name": re.sub(r"[^a-z0-9-]+", "-", path.stem.lower()).strip("-"), "goal": text("goal"), "read": lines("read"),
            "may": lines("may change"), "constraints": (text("constraints") + ("\n- Shape: " + text("shape") if text("shape") and not text("shape").startswith("<") else "")).strip(), "done": "\n".join(lines("done when"))}
    for k in ("goal", "may", "done"):
        if not task[k]:
            sys.exit(f"task file is missing '## {k}' content")
    return task


def chat(messages):
    body = {"model": MODEL, "messages": messages, "stream": False, "think": THINK,
            "options": {"num_ctx": NUM_CTX, "temperature": 0.2}}
    req = urllib.request.Request(f"{HOST}/api/chat", json.dumps(body).encode(), {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)


FILE_RE = re.compile(r"^=== (FILE|EDIT): (.+?) ===\n(.*?)^=== END ===", re.S | re.M)
EDIT_RE = re.compile(r"<<<<<<< SEARCH\n(.*?)\n?=======\n(.*?)\n?>>>>>>> REPLACE", re.S)


def apply_reply(text: str, wt: Path, allowed: list[str]) -> tuple[list[str], list[str]]:
    """Return (changed paths, errors). Applies nothing if any path is outside the allow-list."""
    blocks = [(k, p.strip(), body) for k, p, body in FILE_RE.findall(text)]
    errors = []
    for _, p, _ in blocks:
        target = (wt / p).resolve()
        if not str(target).startswith(str(wt.resolve()) + os.sep) or not any(fnmatch.fnmatch(p, a) or p == a for a in allowed):
            errors.append(f"path not allowed: {p}")
    if errors or not blocks:
        return [], errors or ["no === FILE/EDIT === blocks found in the reply"]
    changed = []
    for kind, p, body in blocks:
        target = wt / p
        if kind == "FILE":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body if body.endswith("\n") else body + "\n")
            if body.startswith("#!"):
                target.chmod(target.stat().st_mode | 0o111)
        else:
            if not target.exists():
                errors.append(f"EDIT on missing file: {p}")
                continue
            src, bad = target.read_text(), False
            for old, new in EDIT_RE.findall(body):
                if src.count(old) != 1:
                    errors.append(f"SEARCH text in {p} matched {src.count(old)} times (need exactly 1): {old[:60]!r}")
                    bad = True
                    break
                src = src.replace(old, new)
            if bad:
                continue
            target.write_text(src)
        changed.append(p)
    return changed, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--in-place", action="store_true", help="edit --repo directly (no worktree); the caller owns isolation")
    ap.add_argument("--cleanup", action="store_true", help="remove the worktree and branch afterwards")
    a = ap.parse_args()

    if not HOST or not MODEL:
        sys.exit("set LOCAL_BASE and LOCAL_MODEL (same variables as server.py), e.g. http://localhost:11434 and qwen3:14b")
    t = parse_task(Path(a.task))
    code, out = sh("git rev-parse --show-toplevel", a.repo)
    if code:
        sys.exit("not a git repo: " + out)
    repo = Path(out.strip())
    branch = f"qwen/{t['name']}"
    if a.in_place:
        wt = repo
    else:
        wt = Path("/tmp/implement") / f"{repo.name}-{t['name']}"
        if wt.exists():
            sys.exit(f"worktree {wt} exists; remove it first (git worktree remove --force {wt}; git branch -D {branch})")
        wt.parent.mkdir(parents=True, exist_ok=True)
        code, out = sh(["git", "worktree", "add", "-b", branch, str(wt), "HEAD"], repo)
        if code:
            sys.exit(out)

    code, out = sh(t["done"], wt)
    if re.search(r"No module named|command not found|not found|ModuleNotFoundError|No such file", out) and not re.search(r"assert|Error:|FAILED|failed", out.replace("ModuleNotFoundError", "")):
        if not a.in_place:
            sh(["git", "worktree", "remove", "--force", str(wt)], repo)
            sh(["git", "branch", "-D", branch], repo)
        sys.exit("preflight: the 'Done when' command cannot run here (environment problem, not a code failure):\n" + out[-800:])
    if code == 0:
        print("note: the check already passes before any change")

    files = []
    for p in t["read"]:
        fp = wt / p
        files.append(f"--- {p} ---\n{fp.read_text()}" if fp.is_file() else f"--- {p} --- (does not exist yet)")
    user = (f"# Task: {t['name']}\n\n## Goal\n{t['goal']}\n\n## May change\n" + "\n".join(t["may"]) +
            (f"\n\n## Constraints\n{t['constraints']}" if t["constraints"] else "") +
            f"\n\n## Done when this passes (run from the repo root)\n{t['done']}\n\n## Files\n" + "\n\n".join(files))
    system = (HERE / "implement-rules.md").read_text()
    est = (len(system) + len(user)) // 3
    if est > MAX_PROMPT_TOKENS:
        sys.exit(f"prompt ~{est} tokens exceeds {MAX_PROMPT_TOKENS}; trim the Read list")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    started, ptok, ctok, passed, last_out, notes = time.time(), 0, 0, False, "", []
    rounds, touched = 0, set()
    for rounds in range(1, a.rounds + 1):
        r = chat(messages)
        ptok += r.get("prompt_eval_count", 0)
        ctok += r.get("eval_count", 0)
        reply = r["message"]["content"]
        if r.get("prompt_eval_count", NUM_CTX) < est * 0.5:
            notes.append(f"WARNING: prompt_eval_count {r.get('prompt_eval_count')} is far below the ~{est} tokens sent: Ollama may have truncated the prompt")
        notes += [l for l in reply.splitlines() if l.startswith("NOTE:")]
        changed, errors = apply_reply(reply, wt, t["may"])
        touched.update(changed)
        messages.append({"role": "assistant", "content": reply})
        if errors:
            last_out = "could not apply the reply:\n" + "\n".join(errors)
            cur = "".join(f"\n--- current {p} ---\n{(wt / p).read_text()}" for p in {e.split(' in ')[1].split(' matched')[0] for e in errors if ' in ' in e and (wt / e.split(' in ')[1].split(' matched')[0]).is_file()})
            messages.append({"role": "user", "content": last_out + "\nNothing from that file was applied. Reply again using the required format and only allowed paths." + cur})
            continue
        code, out = sh(t["done"], wt)
        last_out = out[-3000:]
        if code == 0:
            passed = True
            break
        messages.append({"role": "user", "content": f"The check failed (exit {code}). Last output:\n{last_out}\nFix the code and reply in the same format."})

    secs = round(time.time() - started, 1)
    sh(["git", "add", "-N", "--", *sorted(touched)] if touched else "true", wt)
    _, stat = sh("git diff --stat", wt)
    diff_path = Path("/tmp/implement") / f"{wt.name}-{t['name']}.diff"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    _, diff = sh("git diff", wt)
    diff_path.write_text(diff)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.open("a").write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": t["name"], "passed": passed, "rounds": rounds,
                                    "prompt_tokens": ptok, "completion_tokens": ctok, "seconds": secs, "model": MODEL}) + "\n")
    print(f"{'PASS' if passed else 'FAIL'}  task={t['name']} rounds={rounds} qwen_tokens={ptok}+{ctok} {secs}s")
    print(f"worktree: {wt}\nbranch:   {'(in place)' if a.in_place else branch}\ndiff:     {diff_path} ({len(diff.splitlines())} lines)\n{stat.strip()}")
    for n in notes:
        print(n)
    if not passed:
        print("--- last failure ---\n" + last_out[-1500:])
    if a.cleanup and not a.in_place:
        sh(["git", "worktree", "remove", "--force", str(wt)], repo)
        sh(["git", "branch", "-D", branch], repo)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
