# Delegate: let one model hand easy work to another

An MCP tool, `ask_local`, that lets your main agent (Claude Code, or Claude inside Zed) send bounded jobs
to a model running on your own hardware. The agent passes **file paths**, and this server reads the files and
gives them to the local model, so the agent never spends tokens reading the bulk material. It sends a
one-line task and gets a short answer back.

```
Agent (Claude)  ──ask_local(task, paths)──▶  server.py  ──reads files──▶  local model (Ollama, free)
      ▲                                          │
      └──────────── short answer (+ NOTE:) ◀─────┘          every call is logged → report.py / console.py
```

It is read-only: it never edits files and never decides for the agent. See the
[top-level README](../README.md) for how this compares with the gateway.

## Requirements

- Python 3 (standard library only, no `pip install`).
- Ollama with a model pulled. The server uses Ollama's native `/api/chat`; other servers are not supported yet.
- An MCP-capable agent: Claude Code, Zed, or any client that can launch a stdio MCP server.

## Install

### 1. Register the server

**Claude Code** (works for the CLI and the IDE extensions). Use absolute paths and your own model tag:

```bash
claude mcp add local-delegate --scope user \
  -e LOCAL_BASE=http://localhost:11434 -e LOCAL_MODEL=<your-model> \
  -- python3 /absolute/path/to/inferencerouter/delegate/server.py
```

**Zed.** Merge [`zed.fragment.json`](zed.fragment.json) into `~/.config/zed/settings.json` (it is JSONC),
editing the path and model. Then open the agent panel settings and look for a green dot beside `local-delegate`.
If you use Zed's Claude agent and the tool does not appear, register it with the Claude Code command above instead.

### 2. Tell the agent when to use it

The tool alone is not enough. The agent needs a short rule. Paste [`CLAUDE.snippet.md`](CLAUDE.snippet.md)
into `~/.claude/CLAUDE.md` (all projects) or a project's `CLAUDE.md`. Restart the agent afterwards.

### 3. Check it works

Ask the agent: *"What tools do you have called ask_local?"* Then: *"Use ask_local to summarize
`<some large file>` in 5 bullets."* The answer should arrive in a few seconds, and a line should appear in the log.

## See what it saved

```bash
python3 report.py                 # totals in the terminal;  --days 7 for a week
python3 console.py                # live page at http://localhost:8001  (--port, --host)
```

For a dollar estimate set your real Claude prices (per million tokens) before running either:
`CLAUDE_IN_PER_MTOK=3 CLAUDE_OUT_PER_MTOK=15`. Figures are estimates: they assume the agent would otherwise have
read the same material once, and they exclude the tool definition the agent carries each turn (~250 tokens).
The console has no login; keep it on `127.0.0.1` unless you trust your network.

## Configuration

Set these in the MCP server's environment (the `-e` flags or the `env` block above).

| Variable | Default | Meaning |
|---|---|---|
| `LOCAL_BASE` | `http://127.0.0.1:11434` | Ollama address. A trailing `/v1` is ignored, so the gateway's value works. |
| `LOCAL_MODEL` | none, **required** | Model tag to use. |
| `DELEGATE_LOG` | `~/.local/state/local-delegate/calls.jsonl` | Where calls are recorded. |
| `DELEGATE_TIMEOUT` | `300` | Seconds to wait for the model. |

The tool takes `task`, optional `paths` and `text`, `max_tokens` (default 700, max 4000) and `think`.
Line numbers are added to files so the model can answer "which lines" questions.

## Safety

- Read-only. Paths under `.ssh`, `.gnupg`, `.aws`, `.kube`, `.docker`, `.env*`, private keys and `.pem`/`.key` files are refused.
- Files over 256 KB, binary files, and material over ~120k tokens are refused with a clear error.
- If Ollama is down or `LOCAL_MODEL` is unset, the tool returns an error and the agent does the job itself.
- The model's answer is untrusted text. The agent is told to check it; do not let it drive destructive actions.

## Limits (measured, not hoped)

- **It is not free of overhead.** The tool definition costs the agent about 250 tokens per turn while enabled.
  It pays off for real bulk work (large files, logs, diffs), not small files.
- **The local model is weaker.** It summarized and reformatted well, and answered a "which lines mention X"
  question exactly, but it listed duplicates when asked for "distinct" values. Give it mechanical, easily
  checked jobs.
- **Asking it to abstain backfires.** An "answer only if confident" rule made the test model refuse whenever a
  document contained any contradiction. It now always answers and adds a `NOTE:` line for gaps or ambiguity.
- The agent may forget to delegate or delegate too much. The rule file is a nudge, not a guarantee. The log
  and console show whether it is actually being used.
- Calls run one at a time. The first call after the model has been idle loads it into memory (a few seconds).

## Files

| File | Purpose |
|---|---|
| `server.py` | The MCP server and the `ask_local` tool. |
| `CLAUDE.snippet.md` | The rule that tells the agent when to delegate. |
| `zed.fragment.json` | Zed registration. |
| `stats.py`, `report.py`, `console.py`, `console.html` | Log reading, terminal report, live console. |
| `test_server.py`, `test_stats.py` | Tests, using a fake Ollama. |

## Test

```bash
python3 -m unittest test_server test_stats -v
```

## Implement: let the local model write code, with a test as the judge (`implement.py`)

`server.py` reads and answers; `implement.py` writes. You give it a task file (goal, files to read, the files it may change,
a one-line shape hint, and a shell command that exits 0 when done). It asks the local model for edits, applies them only to
the allowed files, runs your command, and feeds failures back for up to 3 rounds. It never commits.

```bash
export LOCAL_BASE=http://localhost:11434 LOCAL_MODEL=<your-model>
python3 implement.py path/to/TASK.md --repo . --in-place     # or omit --in-place to work in a git worktree
```

- Task format: [`TASK-TEMPLATE.md`](TASK-TEMPLATE.md). Rules the worker follows: [`implement-rules.md`](implement-rules.md).
- Log: `~/.local/state/local-delegate/implement.jsonl` (rounds, tokens, seconds). Optional: `IMPLEMENT_NUM_CTX` (default 131072).
- How we use it with Claude, the routing rule and the improvement loop: [`WORKFLOW.md`](WORKFLOW.md).
- Measured savings (Claude alone vs Claude + local model, same tasks): [`EXPERIMENT.md`](EXPERIMENT.md), harness in [`bench/`](bench/), running notes in [`IMPROVEMENTS.md`](IMPROVEMENTS.md).
- Tests: `python3 -m unittest test_implement -v`.
