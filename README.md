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

```bash
pip install fastapi httpx uvicorn

export LOCAL_BASE="http://ollamaIP:11434/v1"           # your Ollama box
export LOCAL_MODEL="qwen3-coder-next:q8_0"
export ANTHROPIC_API_KEY="sk-ant-..."                  # your key
export CLOUD_MODEL="claude-sonnet-5"                    # <-- SET to your CURRENT model string
# optional: current list price for an honest $ tally
export CLOUD_IN_PER_MTOK="3.0"
export CLOUD_OUT_PER_MTOK="15.0"

uvicorn router:app --host 0.0.0.0 --port 8000
```

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

Add an OpenAI-compatible provider in `settings.json` (or use Agent Panel →
`agent: open settings` → LLM Providers → **Add Provider**):
```jsonc
{ "language_models": { "openai_compatible": {
    "InferenceRouter": {
      "api_url": "http://localhost:8000/v1",
      "available_models": [ { "name": "auto", "display_name": "InferenceRouter",
        "max_tokens": 32768, "capabilities": { "tools": true, "images": false, "chat_completions": true } } ]
    }
} } },
  "agent": { "default_model": { "provider": "openai_compatible", "model": "auto" } }
```
Zed prompts for an API key on save — the router ignores auth, so type any dummy value
(e.g. `router`); it's stored in the keychain, not the file. Reload the window if you
hand-edited `settings.json` instead of using the Add Provider flow.

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
