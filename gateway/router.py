"""
InferenceRouter — a minimal OpenAI-compatible gateway that routes each request
between an on-prem model (local, private, ~free) and Claude (cloud, frontier)
based on three deterministic axes: data sensitivity, task complexity, and
whether the payload still fits the local context window.

Design goals: small, readable, actually usable. Point any OpenAI-compatible
client (Zed, Cline, curl, the openai SDK) at http://localhost:8000/v1 .

Endpoints:
  POST /v1/chat/completions  — routed (local | cloud)
  POST /v1/completions       — always local (fill-in-middle autocomplete)
  POST /v1/embeddings        — always local (never leaves the building)

Run:  uvicorn router:app --host 0.0.0.0 --port 8000
Env:  see the CONFIG block below or .env.example
"""

import os
import re
import json
import time
import logging
from collections import deque
from pathlib import Path
from typing import TypedDict, List, Tuple, Optional, Any, Dict

import httpx
import tiktoken
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse

# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

# Cloud list price, $/million tokens — set to current rates for an honest tally.
CLOUD_IN_PER_MTOK  = float(os.environ.get("CLOUD_IN_PER_MTOK",  "5.0"))
CLOUD_OUT_PER_MTOK = float(os.environ.get("CLOUD_OUT_PER_MTOK", "25.0"))

# Sampling params current Claude models reject outright (400). Editors send
# these by default, so strip them on the cloud path rather than 400-ing.
CLOUD_STRIP_PARAMS = ("temperature", "top_p", "top_k")

# Routing behavior
COMPLEX_CHAR_THRESHOLD = int(os.environ.get("COMPLEX_CHARS", "700"))
LOCAL_CTX_TOKENS = int(os.environ.get("LOCAL_CTX_TOKENS", "65536"))  # usable input budget
CTX_SAFETY = float(os.environ.get("CTX_SAFETY", "0.85"))  # leave room for the reply

# Residency policy: "strict" (code/paths sensitive) or "secrets-only" (only real secrets)
RESIDENCY = os.environ.get("RESIDENCY", "strict")

# ---- Model Configuration (all overridable by environment variables) ----
# These intentionally have no hardcoded model names; you pick your own.
# See .env.example for suggestions.

LOCAL_BASE = os.environ.get("LOCAL_BASE", "")
LOCAL_MODEL = os.environ.get("LOCAL_MODEL", "")
LOCAL_EMBED = os.environ.get("LOCAL_EMBED", "")
LOCAL_FIM = os.environ.get("LOCAL_FIM", "")

CLOUD_BASE = os.environ.get("CLOUD_BASE", "https://api.anthropic.com/v1")
CLOUD_MODEL = os.environ.get("CLOUD_MODEL", "")
CLOUD_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Validate required config at startup
def _validate_config():
    """Ensure required configuration is present."""
    errors = []

    if not LOCAL_BASE:
        errors.append("LOCAL_BASE is required (e.g., http://localhost:11434/v1)")
    if not LOCAL_MODEL:
        errors.append("LOCAL_MODEL is required (e.g., llama2:70b)")
    if not LOCAL_EMBED:
        errors.append("LOCAL_EMBED is required (e.g., nomic-embed-text)")
    if not LOCAL_FIM:
        errors.append("LOCAL_FIM is required for autocomplete (e.g., starcoder:1.3b)")

    if CLOUD_KEY and not CLOUD_MODEL:
        errors.append("CLOUD_MODEL is required when CLOUD_KEY is set")

    if RESIDENCY not in ("strict", "secrets-only"):
        errors.append(f"RESIDENCY must be 'strict' or 'secrets-only', got '{RESIDENCY}'")

    if errors:
        msg = "Configuration errors:\n" + "\n".join(f"  • {e}" for e in errors)
        log.error(msg)
        raise RuntimeError(msg)

    log.info(f"Config loaded: local={LOCAL_MODEL}, cloud={CLOUD_MODEL or '(disabled)'}, residency={RESIDENCY}")

_validate_config()

# ----------------------------------------------------------------------------
# CLASSIFIER  (deterministic on purpose — every decision is explainable)
# ----------------------------------------------------------------------------
# Hard secrets: these pin on-prem under EVERY policy. Non-negotiable.
SECRET_PATTERNS = [
    (re.compile(r"(\bsk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{16,})"), "API key / access key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),             "SSN pattern"),
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"),           "card-number pattern"),
]

# Proprietary but not secret: your source, your paths. Sensitive only under
# RESIDENCY=strict. Under secrets-only these are allowed to reach the frontier
# model — which is the whole point of having one.
PROPRIETARY_PATTERNS = [
    (re.compile(r"```"),                              "code block"),
    (re.compile(r"\b[\w./-]+\.(py|js|ts|go|rs|java|rb|c|cpp|sql|yaml|yml|env)\b"), "file path"),
]

COMPLEX_CUES = re.compile(
    r"\b(refactor|architect|architecture|design|tradeoff|trade-off|optimi[sz]e|"
    r"root cause|debug|why is|across (these|multiple)|scal(e|ing)|distributed|"
    r"consistency|concurren|migrat)", re.I)


class RoutingDecision(TypedDict):
    """The output of the routing classifier."""
    route: str  # "local" or "cloud"
    sensitive: bool
    complex: bool
    oversize: bool
    reason: str


def _text_of(messages: List[Dict[str, Any]]) -> str:
    """Extract plain text from OpenAI-compatible message format."""
    parts = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, list):  # vision-style content blocks
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
        parts.append(str(c))
    return "\n".join(parts)


def est_tokens(s: str) -> int:
    """Estimate token count using tiktoken (cl100k_base for Claude).
    Falls back to character-based estimate if tiktoken fails."""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(s)))
    except Exception:
        # Fallback: rough estimate (usually 4 chars per token)
        return max(1, len(s) // 4)


DEBUG_CLASSIFY = os.environ.get("ROUTER_DEBUG") == "1"


def classify(messages: List[Dict[str, Any]]) -> RoutingDecision:
    """Classify a request into routing decision: local, cloud, or error.

    Axes:
    1. Secrets → always local, even if oversized (truncate with warning)
    2. Oversized → cloud if no secrets (only cloud has the window)
    3. Proprietary (under strict residency) → local
    4. Complex → cloud (frontier reasoning)
    5. Default → local (no reason to pay cloud rates)
    """
    text = _text_of(messages)
    tokens = est_tokens(text)

    secrets = [lbl for pat, lbl in SECRET_PATTERNS if pat.search(text)]
    proprietary = [lbl for pat, lbl in PROPRIETARY_PATTERNS if pat.search(text)]

    # Under strict residency, proprietary content is sensitive too.
    matched = secrets + (proprietary if RESIDENCY == "strict" else [])
    sensitive = bool(matched)

    is_complex = tokens * 4 > COMPLEX_CHAR_THRESHOLD or bool(COMPLEX_CUES.search(text))
    oversize = tokens > LOCAL_CTX_TOKENS * CTX_SAFETY

    if DEBUG_CLASSIFY:
        log.debug(f"{len(messages)} msg(s), ~{tokens} tok | "
                  f"secrets={secrets} proprietary={proprietary} "
                  f"complex={is_complex} oversize={oversize}")

    def out(route: str, reason: str) -> RoutingDecision:
        return RoutingDecision(
            route=route, sensitive=sensitive, complex=is_complex,
            oversize=oversize, reason=reason
        )

    # 1. Real secrets pin local no matter what — even if they don't fit.
    if secrets:
        r = f"secret in payload ({secrets[0]}) — residency lock"
        if oversize:
            r += f" [WARNING: ~{tokens} tok > local ctx, will truncate]"
        return out("local", r)

    # 2. Too big for the local window, nothing secret in it — cloud has the room.
    if oversize:
        return out("cloud", f"~{tokens} tok exceeds local ctx — cloud for the window")

    # 3. Proprietary source under strict residency.
    if sensitive:
        return out("local", f"proprietary payload ({matched[0]}) — residency lock [{RESIDENCY}]")

    if is_complex:
        return out("cloud", "complex, nothing sensitive — frontier reasoning")
    return out("local", "simple task — no reason to pay cloud rates")


# ----------------------------------------------------------------------------
# TELEMETRY  (in-memory; resets on restart — fine for a demo/personal tool)
# ----------------------------------------------------------------------------
STATE = {
    "total": 0, "local": 0, "cloud": 0, "sensitive": 0,
    "sensitive_bytes_egressed": 0,        # by design, stays 0
    "cloud_cost": 0.0,                    # actually spent on cloud
    "saved": 0.0,                         # what local requests would've cost on cloud
    "truncation_warnings": 0,             # secret payloads too big for local ctx
    "feed": deque(maxlen=40),
}


def record(decision: RoutingDecision, model: str, tokens_in: int, tokens_out: int) -> None:
    """Record telemetry for a routed request."""
    STATE["total"] += 1
    STATE[decision["route"]] += 1
    if decision["sensitive"]:
        STATE["sensitive"] += 1
    if decision.get("oversize") and decision["route"] == "local":
        STATE["truncation_warnings"] += 1

    cloud_cost = (tokens_in / 1e6) * CLOUD_IN_PER_MTOK + (tokens_out / 1e6) * CLOUD_OUT_PER_MTOK
    if decision["route"] == "cloud":
        STATE["cloud_cost"] += cloud_cost
    else:
        STATE["saved"] += cloud_cost  # would have cost this much on cloud

    log.info(f"route={decision['route']} model={model} tokens_in={tokens_in} "
             f"tokens_out={tokens_out} cost=${cloud_cost:.4f} "
             f"reason={decision['reason'][:50]}...")

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
def backend_for(route: str) -> Tuple[str, str, Dict[str, str]]:
    """Return (base_url, model, headers) for the routed backend."""
    if route == "cloud":
        return (CLOUD_BASE, CLOUD_MODEL, {"Authorization": f"Bearer {CLOUD_KEY}"})
    return (LOCAL_BASE, LOCAL_MODEL, {})


def _native_base() -> str:
    """Ollama's native API root — strip the OpenAI-compat /v1 suffix if present."""
    return LOCAL_BASE.rstrip("/").removesuffix("/v1").rstrip("/")


def upstream_body_for(route: str, body: Dict, model: str) -> Dict:
    """Build the upstream payload, stripping params the backend rejects."""
    out = {**body, "model": model}
    if route == "cloud":
        for p in CLOUD_STRIP_PARAMS:
            out.pop(p, None)
    return out


def _sse_text_and_usage(raw: bytes) -> Tuple[str, Optional[Dict]]:
    """Pull assistant text + usage out of a captured SSE stream.

    Counts actual output text, not wire format (data: envelopes, JSON keys).
    """
    text: List[str] = []
    usage: Optional[Dict] = None
    for line in raw.decode("utf-8", "ignore").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
        for ch in obj.get("choices", []):
            delta = ch.get("delta") or {}
            if isinstance(delta.get("content"), str):
                text.append(delta["content"])
    return "".join(text), usage


app = FastAPI(title="InferenceRouter")
CONSOLE_HTML = (Path(__file__).parent / "console.html").read_text()


@app.get("/", response_class=HTMLResponse)
async def console():
    return CONSOLE_HTML


@app.get("/stats")
async def stats():
    return JSONResponse({**{k: v for k, v in STATE.items() if k != "feed"},
                         "feed": list(STATE["feed"]),
                         "local_model": LOCAL_MODEL, "cloud_model": CLOUD_MODEL,
                         "residency": RESIDENCY, "local_ctx": LOCAL_CTX_TOKENS})


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": LOCAL_MODEL, "object": "model", "owned_by": "on-prem"},
        {"id": LOCAL_FIM,   "object": "model", "owned_by": "on-prem"},
        {"id": LOCAL_EMBED, "object": "model", "owned_by": "on-prem"},
        {"id": CLOUD_MODEL, "object": "model", "owned_by": "anthropic"},
    ]}


# ----------------------------------------------------------------------------
# /v1/embeddings — ALWAYS local. Embedding a codebase is the highest-volume,
# most code-revealing traffic there is; it never touches the cloud path.
# ----------------------------------------------------------------------------
@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    payload = {**body, "model": LOCAL_EMBED}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{LOCAL_BASE}/embeddings", json=payload,
                                     headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return JSONResponse({"error": f"local embeddings failed: {e}"}, status_code=502)

    inp = body.get("input", "")
    tin = est_tokens(inp if isinstance(inp, str) else " ".join(map(str, inp)))
    record({"route": "local", "sensitive": True, "complex": False, "oversize": False,
            "reason": "embeddings — always on-prem"}, LOCAL_EMBED, tin, 0)
    return JSONResponse(data)


# ----------------------------------------------------------------------------
# /v1/completions — ALWAYS local, served by the small base model. This is the
# fill-in-middle autocomplete path; latency matters far more than depth.
# ----------------------------------------------------------------------------
@app.post("/v1/completions")
async def completions(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "") or ""
    suffix = body.get("suffix")
    tin = est_tokens(str(prompt) + str(suffix or ""))

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            if suffix:
                # True FIM needs Ollama's native endpoint — the OpenAI-compatible
                # /v1/completions shape has nowhere to put the suffix.
                resp = await client.post(f"{_native_base()}/api/generate", json={
                    "model": LOCAL_FIM, "prompt": prompt, "suffix": suffix,
                    "stream": False,
                    "options": {"num_predict": body.get("max_tokens", 128)},
                })
                resp.raise_for_status()
                text = resp.json().get("response", "")
                data = {"id": "cmpl-fim", "object": "text_completion",
                        "created": int(time.time()), "model": LOCAL_FIM,
                        "choices": [{"text": text, "index": 0, "finish_reason": "stop"}]}
            else:
                resp = await client.post(f"{LOCAL_BASE}/completions",
                                         json={**body, "model": LOCAL_FIM},
                                         headers={"Content-Type": "application/json"})
                resp.raise_for_status()
                data = resp.json()
                text = (data.get("choices") or [{}])[0].get("text", "")
    except Exception as e:
        return JSONResponse({"error": f"local completion failed: {e}"}, status_code=502)

    record({"route": "local", "sensitive": True, "complex": False, "oversize": False,
            "reason": "autocomplete (FIM) — always on-prem"}, LOCAL_FIM, tin, est_tokens(text))
    return JSONResponse(data)


# ----------------------------------------------------------------------------
# /v1/chat/completions — the routed path
# ----------------------------------------------------------------------------
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    decision = classify(messages)
    route = decision["route"]

    # If cloud is chosen but unusable, fail SAFE to local (on-thesis: cloud is
    # optional, on-prem is not). Never hard-crash mid-demo.
    if route == "cloud" and not CLOUD_KEY:
        decision = {**decision, "route": "local",
                    "reason": decision["reason"] + "  |  cloud key missing → failsafe local"}
        route = "local"

    base, model, auth = backend_for(route)
    tokens_in = est_tokens(_text_of(messages))
    stream = bool(body.get("stream"))

    if stream:
        return StreamingResponse(_stream(body, decision, route, base, model, auth, tokens_in),
                                 media_type="text/event-stream")

    # ---- non-streaming -----------------------------------------------------
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{base}/chat/completions",
                                     json=upstream_body_for(route, body, model),
                                     headers={"Content-Type": "application/json", **auth})
            resp.raise_for_status()   # <-- without this, a 4xx body parsed fine
            data = resp.json()        #     and the failsafe below was dead code
    except Exception as e:
        if route != "cloud":
            return JSONResponse({"error": str(e)}, status_code=502)
        decision = {**decision, "route": "local",
                    "reason": decision["reason"] + f"  |  cloud error ({type(e).__name__}) → failsafe local"}
        route = "local"
        base, model, auth = backend_for("local")
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{base}/chat/completions",
                                         json=upstream_body_for("local", body, model),
                                         headers={"Content-Type": "application/json"})
                resp.raise_for_status()
                data = resp.json()
        except Exception as e2:
            return JSONResponse({"error": f"cloud and local both failed: {e2}"}, status_code=502)

    usage = data.get("usage") or {}
    tin = usage.get("prompt_tokens", tokens_in)
    tout = usage.get("completion_tokens",
                     est_tokens(_text_of([c.get("message", {}) for c in data.get("choices", [])])))
    record(decision, model, tin, tout)
    data["x_inference_router"] = {"route": decision["route"], "reason": decision["reason"],
                                  "model": model}
    return JSONResponse(data)


async def _stream(body, decision, route, base, model, auth, tokens_in):
    """Stream from upstream, failing safe to local if the cloud leg errors.

    The old version opened the stream blind: an upstream 404/401 was forwarded
    to the editor as though it were valid SSE, and there was no failsafe at all.
    """
    async with httpx.AsyncClient(timeout=None) as client:
        for attempt in ("primary", "failsafe"):
            payload = {**upstream_body_for(route, body, model), "stream": True}
            req = client.build_request("POST", f"{base}/chat/completions", json=payload,
                                       headers={"Content-Type": "application/json", **auth})
            try:
                resp = await client.send(req, stream=True)
            except Exception as e:
                resp = None
                err = f"{type(e).__name__}: {e}"

            if resp is not None and resp.status_code < 400:
                collected = []
                try:
                    async for chunk in resp.aiter_bytes():
                        collected.append(chunk)
                        yield chunk
                finally:
                    await resp.aclose()
                    text_out, usage = _sse_text_and_usage(b"".join(collected))
                    tin = (usage or {}).get("prompt_tokens", tokens_in)
                    tout = (usage or {}).get("completion_tokens", est_tokens(text_out))
                    record(decision, model, tin, tout)
                return

            # Upstream refused. Only the cloud leg gets a second chance.
            if resp is not None:
                err = f"HTTP {resp.status_code}"
                await resp.aclose()
            if attempt == "failsafe" or route != "cloud":
                record(decision, model, tokens_in, 0)
                yield (f"data: {json.dumps({'error': {'message': f'upstream failed: {err}'}})}\n\n"
                       "data: [DONE]\n\n").encode()
                return

            decision = {**decision, "route": "local",
                        "reason": decision["reason"] + f"  |  cloud stream error ({err}) → failsafe local"}
            route = "local"
            base, model, auth = backend_for("local")


if __name__ == "__main__":
    import uvicorn
    if not CLOUD_KEY:
        print("!! ANTHROPIC_API_KEY not set — cloud requests will failsafe to local.")
    print(f"   residency={RESIDENCY}  local_ctx={LOCAL_CTX_TOKENS}  local={LOCAL_MODEL}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
