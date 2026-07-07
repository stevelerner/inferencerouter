"""
Fire a curated set of requests at the router so the console shows a clean,
legible split. Run AFTER `uvicorn router:app` is up.

    python demo_batch.py

Each prompt is chosen to land in a specific routing cell with an obvious reason.
"""
import time, httpx

ROUTER = "http://localhost:8000/v1/chat/completions"

PROMPTS = [
    # sensitive (code block) -> local
    "Add error handling to this:\n```python\ndef load(p):\n    return open(p).read()\n```",
    # simple, non-sensitive -> local
    "In one sentence, what's the difference between a list and a tuple?",
    # complex, non-sensitive -> cloud
    "Design a testable architecture for a multi-tenant rate limiter and discuss the tradeoffs.",
    # sensitive (file path) -> local  (sensitivity beats complexity)
    "Refactor the auth flow across services/auth.py and services/session.py for testability.",
    # complex, non-sensitive -> cloud
    "Explain the consistency tradeoffs when choosing a datastore for an event-sourced system.",
    # simple, non-sensitive -> local
    "What does HTTP status 429 mean?",
]

def main():
    with httpx.Client(timeout=120) as c:
        for i, p in enumerate(PROMPTS, 1):
            body = {"model": "auto", "messages": [{"role": "user", "content": p}],
                    "max_tokens": 200}
            try:
                r = c.post(ROUTER, json=body).json()
                d = r.get("x_inference_router", {})
                print(f"[{i}] -> {d.get('route','?'):5}  {d.get('reason','')}")
            except Exception as e:
                print(f"[{i}] request failed: {e}")
            time.sleep(1.2)  # let the console breathe between rows

if __name__ == "__main__":
    main()
