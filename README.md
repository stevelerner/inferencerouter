# InferenceRouter

A ~200-line OpenAI-compatible gateway that auto-routes each request between your
on-prem model (local, private, ~free) and Claude (cloud, frontier) on two
deterministic axes: **data sensitivity** and **task complexity**. Point any
OpenAI-compatible client at it; watch the live console.

## Routing policy

| Sensitive payload? | Complex? | Routes to | Why |
|---|---|---|---|
| yes | any | **on-prem** | source/PII must not leave the building |
| no | no | **on-prem** | cheap task, no reason to pay cloud rates |
| no | yes | **cloud** | frontier reasoning, nothing sensitive to leak |

Sensitivity = code blocks, file paths, keys, emails, SSNs. Cloud is **optional**;
on-prem is not — if the cloud key is missing or a cloud call fails, it **fails
safe to local**.

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
export LOCAL_MODEL="qwen3-coder-next:q4_K_M"
export CLOUD_MODEL="claude-sonnet-5"                    # <-- SET to your CURRENT model string
# optional: current list price for an honest $ tally
export CLOUD_IN_PER_MTOK="3.0"
export CLOUD_OUT_PER_MTOK="15.0"
```

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

Keep the local model **qwen3-coder-next (non-reasoning)** to avoid a Zed
inline-assistant bug with "thinking" tokens, and don't use Zed **External Agents**
(Claude Code/Codex) here — they use their own auth/endpoint and bypass the router.

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
- `console.html` — live ops console (polls `/stats` every second)
- `demo_batch.py` — curated requests that exercise all six routing cells

## Tips
- Warm the on-prem model ahead of time: `OLLAMA_KEEP_ALIVE=60m`, and run one
  throwaway prompt so the first real request isn't slow.
- `CLOUD_MODEL` must be a valid current Anthropic model string — a wrong one
  404s, which fails safe to local rather than erroring.
