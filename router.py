"""
InferenceRouter — a minimal OpenAI-compatible gateway that routes each request
between an on-prem model (local, private, ~free) and Claude (cloud, frontier)
based on two deterministic axes: data sensitivity and task complexity.

Design goals: small, readable, actually usable. Point any OpenAI-compatible
client (Zed, Cline, curl, the openai SDK) at http://localhost:8000/v1 .

Run:  uvicorn router:app --host 0.0.0.0 --port 8000
Env:  see the CONFIG block below.
"""

import os
import re
import json
import time
from collections import deque

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from pathlib import Path

# ----------------------------------------------------------------------------
# CONFIG (all overridable by environment variables)
# ----------------------------------------------------------------------------
LOCAL_BASE   = os.environ.get("LOCAL_BASE",  "http://ollamaIP:11434/v1")
LOCAL_MODEL  = os.environ.get("LOCAL_MODEL", "qwen3-coder-next:q8_0")

CLOUD_BASE   = os.environ.get("CLOUD_BASE",  "https://api.anthropic.com/v1")
CLOUD_MODEL  = os.environ.get("CLOUD_MODEL", "claude-sonnet-5")  # <-- set to YOUR current model string
CLOUD_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")

# Cloud list price, $/million tokens — set to current rates for an honest tally.
CLOUD_IN_PER_MTOK  = float(os.environ.get("CLOUD_IN_PER_MTOK",  "3.0"))
CLOUD_OUT_PER_MTOK = float(os.environ.get("CLOUD_OUT_PER_MTOK", "15.0"))

COMPLEX_CHAR_THRESHOLD = int(os.environ.get("COMPLEX_CHARS", "700"))

# ----------------------------------------------------------------------------
# CLASSIFIER  (deterministic on purpose — every decision is explainable)
# ----------------------------------------------------------------------------
SECRET_PATTERNS = [
    (re.compile(r"```"),                              "code block"),
    (re.compile(r"\b[\w./-]+\.(py|js|ts|go|rs|java|rb|c|cpp|sql|yaml|yml|env)\b"), "file path"),
    (re.compile(r"(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{16})"), "API key / access key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),      "email address"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),             "SSN pattern"),
]

COMPLEX_CUES = re.compile(
    r"\b(refactor|architect|architecture|design|tradeoff|trade-off|optimi[sz]e|"
    r"root cause|debug|why is|across (these|multiple)|scal(e|ing)|distributed|"
    r"consistency|concurren|migrat)", re.I)


def _text_of(messages):
    parts = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, list):  # OpenAI vision-style content blocks
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
        parts.append(str(c))
    return "\n".join(parts)


def classify(messages):
    """Return dict: route, sensitive, complex, reason."""
    text = _text_of(messages)

    matched = [label for pat, label in SECRET_PATTERNS if pat.search(text)]
    sensitive = bool(matched)

    is_complex = len(text) > COMPLEX_CHAR_THRESHOLD or bool(COMPLEX_CUES.search(text))

    if sensitive:
        return {"route": "local", "sensitive": True, "complex": is_complex,
                "reason": f"sensitive payload ({matched[0]}) — residency lock"}
    if is_complex:
        return {"route": "cloud", "sensitive": False, "complex": True,
                "reason": "complex, no sensitive payload — frontier reasoning"}
    return {"route": "local", "sensitive": False, "complex": False,
            "reason": "simple task — no reason to pay cloud rates"}


# ----------------------------------------------------------------------------
# TELEMETRY  (in-memory; resets on restart — fine for a demo/personal tool)
# ----------------------------------------------------------------------------
def est_tokens(s):  # cheap, honest-enough estimate for a live tally
    return max(1, len(s) // 4)

STATE = {
    "total": 0, "local": 0, "cloud": 0, "sensitive": 0,
    "sensitive_bytes_egressed": 0,        # by design, stays 0
    "cloud_cost": 0.0,                    # actually spent on cloud
    "saved": 0.0,                         # what local requests would've cost on cloud
    "feed": deque(maxlen=40),
}


def record(decision, model, tokens_in, tokens_out):
    STATE["total"] += 1
    STATE[decision["route"]] += 1
    if decision["sensitive"]:
        STATE["sensitive"] += 1
    cloud_cost = (tokens_in / 1e6) * CLOUD_IN_PER_MTOK + (tokens_out / 1e6) * CLOUD_OUT_PER_MTOK
    if decision["route"] == "cloud":
        STATE["cloud_cost"] += cloud_cost
    else:
        STATE["saved"] += cloud_cost  # would have cost this much on cloud
    STATE["feed"].appendleft({
        "t": time.strftime("%H:%M:%S"),
        "route": decision["route"],
        "reason": decision["reason"],
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    })


# ----------------------------------------------------------------------------
# DISPATCH
# ----------------------------------------------------------------------------
def backend_for(route):
    if route == "cloud":
        return (CLOUD_BASE, CLOUD_MODEL, {"Authorization": f"Bearer {CLOUD_KEY}"})
    return (LOCAL_BASE, LOCAL_MODEL, {})


app = FastAPI(title="InferenceRouter")
CONSOLE_HTML = (Path(__file__).parent / "console.html").read_text()


@app.get("/", response_class=HTMLResponse)
async def console():
    return CONSOLE_HTML


@app.get("/stats")
async def stats():
    return JSONResponse({**{k: v for k, v in STATE.items() if k != "feed"},
                         "feed": list(STATE["feed"]),
                         "local_model": LOCAL_MODEL, "cloud_model": CLOUD_MODEL})


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": LOCAL_MODEL, "object": "model", "owned_by": "on-prem"},
        {"id": CLOUD_MODEL, "object": "model", "owned_by": "anthropic"},
    ]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    decision = classify(messages)
    route = decision["route"]

    base, model, auth = backend_for(route)

    # If cloud is chosen but unusable, fail SAFE to local (on-thesis: cloud is
    # optional, on-prem is not). Never hard-crash mid-demo.
    if route == "cloud" and not CLOUD_KEY:
        decision = {**decision, "route": "local",
                    "reason": decision["reason"] + "  |  cloud key missing → failsafe local"}
        route = "local"
        base, model, auth = backend_for("local")

    upstream_body = {**body, "model": model}
    tokens_in = est_tokens(_text_of(messages))
    stream = bool(body.get("stream"))

    url = f"{base}/chat/completions"
    headers = {"Content-Type": "application/json", **auth}

    if stream:
        async def gen():
            collected = []
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("POST", url, json={**upstream_body, "stream": True},
                                             headers=headers) as resp:
                        async for chunk in resp.aiter_bytes():
                            collected.append(chunk)
                            yield chunk
            finally:
                text_out = b"".join(collected).decode("utf-8", "ignore")
                record(decision, model, tokens_in, est_tokens(text_out))
        return StreamingResponse(gen(), media_type="text/event-stream")

    # non-streaming
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=upstream_body, headers=headers)
        data = resp.json()
    except Exception as e:
        # failsafe to local on any cloud error
        if route == "cloud":
            decision = {**decision, "route": "local",
                        "reason": decision["reason"] + f"  |  cloud error → failsafe local"}
            base, model, auth = backend_for("local")
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{base}/chat/completions",
                                         json={**body, "model": model},
                                         headers={"Content-Type": "application/json"})
            data = resp.json()
        else:
            return JSONResponse({"error": str(e)}, status_code=502)

    usage = data.get("usage") or {}
    tin = usage.get("prompt_tokens", tokens_in)
    tout = usage.get("completion_tokens",
                     est_tokens(json.dumps(data.get("choices", []))))
    record(decision, model, tin, tout)
    # surface the routing decision to the client too (handy in-editor)
    data.setdefault("x_inference_router", {})
    data["x_inference_router"] = {"route": decision["route"], "reason": decision["reason"], "model": model}
    return JSONResponse(data)


if __name__ == "__main__":
    import uvicorn
    if CLOUD_MODEL == "claude-sonnet-5":
        print("!! Set CLOUD_MODEL env to your CURRENT Anthropic model string before the demo.")
    if not CLOUD_KEY:
        print("!! ANTHROPIC_API_KEY not set — cloud requests will failsafe to local.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
