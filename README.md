# InferenceRouter

Ways to send the easy work to a cheap local model and keep the expensive model for what needs it.

There is more than one way to do that, and they suit different tools. This repo holds two working
methods, a guide to choosing between them, and a shared config so you can run either or both.

| | [**Gateway**](gateway/) | [**Delegate**](delegate/) |
|---|---|---|
| **Idea** | A proxy in front of your editor. Every request is routed local or cloud by rule. | The main model stays in charge and hands bulk jobs to the local model through a tool. |
| **Who decides** | Deterministic rules (secrets, size, keywords). | The main model, mid-task. |
| **Granularity** | Whole request. | Subtask (one job inside a bigger session). |
| **Works with** | Anything OpenAI-compatible: Zed's own agent, Cline, Continue, the OpenAI SDKs. | Agents that support MCP tools: Claude Code, Zed's Claude agent, other MCP clients. |
| **Cloud model access** | Needs an API key, called by the gateway. | None. The agent keeps its own login. |
| **Result checked by** | Nobody. | The main model, which sees the answer. |
| **Also gives you** | Local-only autocomplete and embeddings, secrets and residency rules, live console. | Savings log, report, and live console. |
| **Setup** | Run a server, point your client at it. | Register one MCP server, add a short rule file. |
| **Main risk** | Keyword routing can guess wrong. | The model may not delegate, or delegate too much. Tool definition adds ~250 tokens/turn. |

## Which one do I want?

- **I use Claude Code, or Claude inside Zed, and want to spend fewer Claude tokens.** Use **Delegate**.
  The gateway cannot sit in front of those agents, which talk to Anthropic directly with their own login.
- **I use an OpenAI-compatible tool and want requests split between a local model and Claude automatically,
  with privacy rules** (keys and private code never leave the machine). Use **Gateway**.
- **I want local autocomplete and embeddings through one endpoint.** **Gateway**.
- **Both situations apply.** Run both. They do not conflict, and share the same `LOCAL_BASE` and `LOCAL_MODEL`.
- **Neither.** Pointing your tool straight at Ollama is a perfectly good setup if you only use the local model.

## Requirements

- A local model server. **Ollama** is the tested one; the gateway accepts any OpenAI-compatible server
  (vLLM, LM Studio), the delegate currently needs Ollama's native API.
- Python 3. The delegate uses only the standard library. The gateway needs `pip install -r gateway/requirements.txt`
  (its `run.sh` does this in a virtualenv for you).
- Both were tested on Python 3.12 against Ollama.

## Shared configuration

Both methods read the same two variables, so you configure your model once:

| Variable | Meaning | Example |
|---|---|---|
| `LOCAL_BASE` | Your local server. A trailing `/v1` is fine for both. | `http://localhost:11434/v1` |
| `LOCAL_MODEL` | A model tag you have pulled. **Required**, there is no default. | `qwen3:14b` |

Everything else is specific to one method and documented in its folder.

## Quick start

**Gateway** (details: [`gateway/README.md`](gateway/README.md))

```bash
cd gateway
export LOCAL_BASE=http://localhost:11434/v1 LOCAL_MODEL=<your-model> \
       LOCAL_FIM=<small-autocomplete-model> LOCAL_EMBED=nomic-embed-text
./run.sh --local-only          # or ./run.sh sk-ant-...  to allow routing hard tasks to Claude
# console: http://localhost:8000      editor base URL: http://localhost:8000/v1
```

**Delegate** (details: [`delegate/README.md`](delegate/README.md))

```bash
# 1. Register the tool with Claude Code (use absolute paths):
claude mcp add local-delegate --scope user \
  -e LOCAL_BASE=http://localhost:11434 -e LOCAL_MODEL=<your-model> \
  -- python3 /absolute/path/to/inferencerouter/delegate/server.py
# 2. Tell Claude when to use it: paste delegate/CLAUDE.snippet.md into ~/.claude/CLAUDE.md
# 3. Later, see what it saved:
python3 delegate/report.py           # or: python3 delegate/console.py  ->  http://localhost:8001
```

For Zed, the same server is registered in `settings.json` instead; see [`delegate/README.md`](delegate/README.md).

## Layout

```
gateway/    the OpenAI-compatible routing proxy, its console, demo and tests
delegate/   the MCP tool that lets one model hand jobs to another, the implement loop (local model writes code, a test judges), its bench harness, report, console and tests
```

## Tests

```bash
cd gateway  && pip install -r requirements.txt && python -m pytest test_router.py -q
cd delegate && python3 -m unittest test_server test_stats test_implement -v   # no dependencies
```

## Not built (ideas only)

- **Cascade:** try the local model first and escalate to the cloud model if the answer looks weak.
- **Delegate backends other than Ollama** (vLLM, LM Studio, any OpenAI-compatible server).

Contributions that add a new routing method are welcome: give it a folder, a README with install steps and
honest limits, tests, and a row in the table above.
