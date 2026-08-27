# InferenceRouter

A small OpenAI-compatible gateway that auto-routes each request between your
on-prem model (local, private, ~free) and Claude (cloud, frontier) on three
deterministic axes: **data sensitivity**, **task complexity**, and **local
context fit**. It also serves autocomplete and embeddings entirely on-prem.
Point any OpenAI-compatible client at it; watch the live console.

## Routing policy

Three deterministic axes: **data sensitivity**, **task complexity**, and whether
the payload still **fits the local context window**.

| Condition | Routes to | Why |
|---|---|---|
| real secret (API key, private key, SSN, card no.) | **on-prem** | never leaves, no exceptions |
| proprietary source/paths *and* `RESIDENCY=strict` | **on-prem** | your code stays in the building |
| doesn't fit local context, nothing secret | **cloud** | only the frontier model has the window |
| complex, nothing sensitive | **cloud** | frontier reasoning |
| everything else | **on-prem** | no reason to pay cloud rates |

Secrets outrank size: a secret-bearing payload too large for the local window
stays local and is flagged as a **truncation warning** on the console rather
than being shipped to the cloud.

### Residency policy

`RESIDENCY=strict` (default) treats code blocks and file paths as sensitive —
maximum containment, but it means the frontier model never sees a hard *coding*
question. `RESIDENCY=secrets-only` pins only real secrets on-prem and lets
source code reach the cloud, which is usually what you want if the goal is
better answers rather than strict data residency.

Cloud is **optional**; on-prem is not — if the cloud key is missing, the cloud
call errors, or a cloud *stream* fails mid-flight, it **fails safe to local**.

## Endpoints

| Endpoint | Routing | Model |
|---|---|---|
| `POST /v1/chat/completions` | routed (see above) | `LOCAL_MODEL` or `CLOUD_MODEL` |
| `POST /v1/completions` | **always local** | `LOCAL_FIM` — fill-in-middle autocomplete |
| `POST /v1/embeddings` | **always local** | `LOCAL_EMBED` — never egresses |

Embeddings and autocomplete are the highest-volume, most code-revealing traffic
an editor produces, so they are hard-wired on-prem with no routing decision.

## Run (2 minutes)

Cloud routing needs a Claude API key (skip this if you only want the on-prem
path — the router fails safe to local without one):
1. Sign in at [platform.claude.com](https://platform.claude.com) (or
   console.anthropic.com).
2. Go to **API Keys** → **Create Key**.
3. Copy the `sk-ant-...` value — you won't be able to view it again later.

Set this to your actual Ollama box — `LOCAL_BASE` has no working default, so if
you skip this the router falls back to an unresolvable placeholder host and
every on-prem request fails:
```bash
export LOCAL_BASE="http://<your-ollama-ip>:11434/v1"
```

Then the rest of the config:
```bash
export LOCAL_MODEL="qwen3.8:27b-mtp-q4_K_M"
export LOCAL_FIM="qwen2.5-coder:1.5b-base"      # autocomplete (base model, FIM-capable)
export LOCAL_EMBED="nomic-embed-text:latest"    # embeddings
export CLOUD_MODEL="claude-opus-5"
export RESIDENCY="strict"                       # or "secrets-only"
export LOCAL_CTX_TOKENS="65536"                 # USABLE INPUT tokens — see note below
export CLOUD_IN_PER_MTOK="5.0"
export CLOUD_OUT_PER_MTOK="25.0"
```

> **`LOCAL_CTX_TOKENS` is the usable *input* budget, not `num_ctx`.** Ollama
> reserves roughly half the context window for generation, so `num_ctx=131072`
> yields only ~65,538 tokens of prompt before it **silently truncates**. Set
> this to about half your `OLLAMA_NUM_CTX` or the router's oversize guard will
> not fire when it should.

And finally, launch it (env vars only take effect at process start — re-run
this after changing any of the above):
```bash
./run.sh sk-ant-...     # the key from step 3 — creates the venv on first run
```

`run.sh --help` shows all options. Running it with no arguments (and no
`ANTHROPIC_API_KEY` already exported) prints that same help instead of
guessing what you want. To run on-prem only, use `./run.sh --local-only`.

Open the console at **http://localhost:8000** .

Try it with the curated batch of requests, which produces a clean on-prem/cloud
split in ~20s so you can see the console in action:
```bash
python demo_batch.py
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

Don't use Zed **External Agents** (Claude Code/Codex) here — they use their own
auth/endpoint and bypass the router entirely.

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

## Files
- `router.py` — the gateway (routing, dispatch, telemetry, failsafe)
- `sl-setup.sh` — my box's env (navigator, 192.168.68.71)
- `console.html` — live ops console (polls `/stats` every second)
- `demo_batch.py` — curated requests that exercise all six routing cells

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
