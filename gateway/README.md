# Gateway

> One of the routing methods in [InferenceRouter](../README.md). This one is a proxy: your editor talks to it,
> and it picks the model. To make **Claude itself** hand jobs to a local model instead, see [`../delegate`](../delegate/README.md).

A minimal OpenAI-compatible gateway that auto-routes each request between your
on-prem model (local, private, ~free) and Claude (cloud, frontier) on three
deterministic axes: **data sensitivity**, **task complexity**, and **local
context fit**. Autocomplete and embeddings are always on-prem. Point any
OpenAI-compatible client at it; watch the live console.

## Quick Start (2 minutes)

From the repo root, `cd gateway` first.

```bash
# 1. Set your local inference server (Ollama, vLLM, etc.)
export LOCAL_BASE="http://localhost:11434/v1"
export LOCAL_MODEL="llama2:70b"           # use any model you have
export LOCAL_FIM="starcoder:1.3b"         # autocomplete
export LOCAL_EMBED="nomic-embed-text"     # embeddings

# 2. (Optional) Enable cloud routing with Claude
export ANTHROPIC_API_KEY="sk-ant-..."
export CLOUD_MODEL="claude-opus-5"        # or any Claude model

# 3. Run
./run.sh
# or: ./run.sh sk-ant-... (passes key via CLI)

# 4. Open console at http://localhost:8000
# Try the demo: python demo_batch.py
```

No hardcoded model names—choose your own, swap anytime. See `.env.example` for all
options and defaults.

## Routing Logic

Five axes (in priority order):

1. **Secrets detected?** → **local** (even if oversized—flags truncation warning)
   - API keys, private keys, SSNs, card numbers stay in the building

2. **Payload too large?** → **cloud** (if no secrets)
   - Exceeds local context ceiling → only frontier model has the window

3. **Proprietary content?** → **local** (if `RESIDENCY=strict`)
   - Code blocks, file paths treated as sensitive under strict mode

4. **Complex task?** → **cloud**
   - Keywords: refactor, architect, debug, optimize, distributed, etc.

5. **Default** → **local**
   - Simple task, nothing secret—no reason to pay cloud rates

### Residency Modes

- `RESIDENCY=strict` (default): Code blocks and file paths are sensitive. Maximum
  containment, but frontier model won't see coding questions.
- `RESIDENCY=secrets-only`: Only real secrets pin local; code can reach the cloud
  for better answers (use this if you care about answer quality over strict
  containment).

### Cloud is Optional

If `ANTHROPIC_API_KEY` is unset, disabled, or the API fails, the router fails
safe to local. Cloud failures don't break your workflow.

## Endpoints

| Endpoint | Routing | Model |
|---|---|---|
| `POST /v1/chat/completions` | routed (see above) | `LOCAL_MODEL` or `CLOUD_MODEL` |
| `POST /v1/completions` | **always local** | `LOCAL_FIM` — fill-in-middle autocomplete |
| `POST /v1/embeddings` | **always local** | `LOCAL_EMBED` — never egresses |

Embeddings and autocomplete are the highest-volume, most code-revealing traffic
an editor produces, so they are hard-wired on-prem with no routing decision.

## Setup

### Required: Local Inference

The router **requires** a local inference server (Ollama, vLLM, LM Studio, etc.).
Set these three variables:

```bash
export LOCAL_BASE="http://localhost:11434/v1"   # your inference server
export LOCAL_MODEL="llama2:70b"                 # any model you have locally
export LOCAL_EMBED="nomic-embed-text"           # for embeddings (can be same as LOCAL_MODEL)
export LOCAL_FIM="starcoder:1.3b"               # for autocomplete (small, FIM-capable model)
```

### Optional: Cloud Routing

To enable Claude routing (skip if you want local-only):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."  # from https://platform.claude.com → API Keys
export CLOUD_MODEL="claude-opus-5"     # any Claude model (or leave unset for local-only)
```

### Advanced Configuration

All options are in `.env.example`. Key ones:

```bash
export RESIDENCY="strict"              # or "secrets-only"
export LOCAL_CTX_TOKENS="65536"        # usable input budget (roughly half OLLAMA_NUM_CTX)
export CLOUD_IN_PER_MTOK="5.0"         # update if Claude prices change
export CLOUD_OUT_PER_MTOK="25.0"
```

> **`LOCAL_CTX_TOKENS` ≠ `num_ctx`:** Ollama reserves ~half the context for
> generation. So `num_ctx=131072` gives ~65,536 usable input tokens. Set this
> to prevent silent truncation on oversized payloads.

### Launch

```bash
./run.sh                          # uses ANTHROPIC_API_KEY from environment
./run.sh sk-ant-...               # or pass key via CLI
./run.sh --local-only             # local inference only (no cloud routing)
./run.sh --help                   # show all options
```

The router creates a Python venv on first run and starts at **http://localhost:8000**.

### Demo

```bash
python demo_batch.py              # exercise routing with pre-made requests (~20s)
```

## Use it in your editor

Point any OpenAI-compatible client at the router instead of a provider:

- **base URL:** `http://localhost:8000/v1`
- **API key:** anything (ignored by the router)
- **model:** anything (the router picks the backend; the field is ignored)

Every response also carries `x_inference_router: {route, reason, model}` so you
can see the decision from inside the editor.

### Zed

**Use the UI, not a hand-edit** — Zed's `openai_compatible` schema has several
required capability fields that are easy to miss by hand (see below) and the
form fills them in correctly:

1. Command palette (`Cmd+Shift+P`) → `agent: open settings`.
2. Under **LLM Providers**, click **+ Add Provider**.
3. Fill in:
   - **Provider Name:** `InferenceRouter`
   - **API URL:** `http://localhost:8000/v1`
   - **API Key:** any dummy value, e.g. `router` (the router ignores auth)
   - **Add Model** → **Model Name:** `auto`, Max Tokens / Max Output Tokens /
     Max Completion Tokens: `32768` each, **Supports tools** checked.
4. **Save Provider**, then go to the Agent panel's model picker and select
   **InferenceRouter → auto**. It's also worth setting it as your default —
   see `default_model` below.

This is what a saved provider actually looks like in `settings.json`,
confirmed working — reference this if you're troubleshooting a hand-edit:
```jsonc
{
  "language_models": {
    "openai_compatible": {
      "InferenceRouter": {
        "api_url": "http://localhost:8000/v1",
        "available_models": [
          {
            "name": "auto",
            "max_tokens": 32768,
            "max_output_tokens": 32768,
            "max_completion_tokens": 32768,
            "capabilities": {
              "tools": true,
              "images": false,
              "parallel_tool_calls": false,
              "prompt_cache_key": false,
              "chat_completions": true,
              "interleaved_reasoning": false,
              "max_tokens_parameter": false
            }
          }
        ]
      }
    }
  },
  "agent": {
    // ...whatever agent settings you already have...
    "default_model": { "provider": "openai_compatible", "model": "auto" }
  }
}
```
All of `parallel_tool_calls`, `prompt_cache_key`, `interleaved_reasoning`, and
`max_tokens_parameter` are required — a hand-edit missing any of them fails
with `Invalid user settings file: missing field '<name>'` on load, which is
why the UI flow above is the reliable path.

If you do hand-edit and reload the window (command palette → `reload window`)
and the provider still doesn't show up under LLM Providers, the config wasn't
picked up — double check the file path is `~/.config/zed/settings.json` and
try a full quit-and-relaunch of Zed, not just a window reload.

> **`qwen3.8` is a thinking model, and Zed's inline assistant mishandles
> "thinking" tokens.** A low output cap can also be consumed entirely by
> reasoning, returning an empty completion — confirmed on this box. If the
> inline assistant misbehaves, either raise the output cap or point
> `LOCAL_MODEL` at a non-reasoning model for inline edits.

Zed **External Agents** (Claude Code, Codex) use their own auth and endpoint and bypass the router entirely, so
the gateway cannot save their tokens. To make Claude send easy work to a local model, use the
[delegate](../delegate/README.md) method instead.

For autocomplete, point Zed's completion provider at the same base URL; the
router serves `/v1/completions` from `LOCAL_FIM` (`qwen2.5-coder:1.5b-base`)
and never consults the cloud.

### VS Code + Cline

1. Install the **Cline** extension from the VS Code Marketplace.
2. Open the Cline panel → settings (gear icon) → **API Provider** → choose
   **OpenAI Compatible**.
3. Set:
   - **Base URL:** `http://localhost:8000/v1`
   - **API Key:** any non-empty value (ignored by the router)
   - **Model ID:** `auto` (ignored — the router picks the backend)
4. Save. Cline's chat and Plan/Act requests now flow through the router; watch
   the console at `http://localhost:8000` to see them land as on-prem or cloud.

### Any other OpenAI-compatible client

Same three fields — base URL, a dummy key, and any model string — work for the
`openai` Python/JS SDKs, `curl`, Continue, or anything else that speaks the
`/v1/chat/completions` API.

## Testing

```bash
# Install test dependencies
pip install pytest

# Run tests
python -m pytest test_router.py -v

# Run with debug output
ROUTER_DEBUG=1 python -m pytest test_router.py -v
```

Tests cover:
- Secret detection (API keys, SSNs, private keys)
- Residency modes (strict vs. secrets-only)
- Complexity detection (refactor, architect, debug, etc.)
- Token estimation
- Config validation
- Vision content handling
- Oversized payload routing

## Code Quality

- **Type hints** throughout for maintainability and IDE support
- **Structured logging** with `logging` module (JSON-compatible)
- **Config validation** at startup — fails loudly if required vars are missing
- **Token estimation** via `tiktoken` (falls back to char-based if unavailable)
- **No hardcoded models** — bring your own (local and cloud)

## Files
- `router.py` — the gateway (routing, dispatch, telemetry, failsafe)
- `console.html` — live ops console (polls `/stats` every second)
- `demo_batch.py` — curated requests that exercise all routing paths
- `test_router.py` — unit tests for routing logic
- `.env.example` — config template with all options explained
- `run.sh` — startup script with venv management

## Troubleshooting

**"Configuration errors" at startup**
- Check `.env.example` — you're missing a required env var (LOCAL_BASE, LOCAL_MODEL, etc.)
- Validate your endpoint: `curl http://localhost:11434/v1/models`

**All requests route to local, none to cloud**
- Is `ANTHROPIC_API_KEY` set? (unset = local-only mode, which is intentional)
- Check logs: `ROUTER_DEBUG=1 ./run.sh`
- Verify key is valid: `curl -H "Authorization: Bearer $ANTHROPIC_API_KEY" https://api.anthropic.com/v1/models`

**"Local inference failed"**
- Ensure your Ollama/vLLM server is running and reachable
- Check `LOCAL_BASE` URL in env or `.env.example`
- Verify the model exists: `curl http://localhost:11434/v1/models`

**High truncation warnings**
- Increase `LOCAL_CTX_TOKENS` if your local model has a larger context window
- Or reduce payload size (long conversations, large code blocks)

**Slow responses**
- Local inference slower than expected? Check GPU utilization (`nvidia-smi`)
- Cloud routing slower? May be network latency or API backlog

**Can't connect from editor (Zed, VS Code, etc.)**
- Ensure router is running: `curl http://localhost:8000/`
- Check base URL in editor is `http://localhost:8000/v1`
- If editor is on another machine, use your server's IP: `http://<server-ip>:8000/v1`

## Tuning Ollama (measured on an RTX 5090, 32GB)

`qwen3.8:27b-mtp-q4_K_M` weights are 17.5GB and sit 100% on GPU. The KV cache is
allocated **lazily**, so `ollama ps` under-reports memory — trust `nvidia-smi`.

| `num_ctx` | usable input | VRAM in use | gen tok/s | verdict |
|---|---|---|---|---|
| 32768 (old default) | ~16K | ~18GB | ~120 | too small for real code context |
| 131072 | ~65K | ~26.9GB | ~104 | **recommended** — full GPU, room to spare |
| 262144 | ~131K | spills to CPU | ~13 | 68% GPU offload, 8x slower — avoid |

Set it in `/etc/systemd/system/ollama.service.d/override.conf`:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_KEEP_ALIVE=60m"
Environment="OLLAMA_NUM_CTX=131072"
```

then `sudo systemctl daemon-reload && sudo systemctl restart ollama`.

Other notes:
- **Don't run the q8_0 quant.** At 30.4GB it only fits 91% on a 32GB card; the
  9% CPU spill costs ~80% of throughput (26 tok/s vs 130). It also evicts the
  q4 model, so every tier switch pays a ~45s reload. Pin q4 and step up to
  Claude instead.
- **Thinking tokens dominate short replies.** `qwen3.8` is a thinking model; a
  low `num_predict` can be consumed entirely by reasoning, returning an empty
  string. Raise the cap or disable thinking for quick completions.
- `CLOUD_MODEL` must be a valid current Anthropic model string. The router
  strips `temperature`/`top_p`/`top_k` on the cloud path because current Claude
  models reject them with a 400 and most editors send them by default.
