"""
Fire a curated set of requests at the router so the console shows a clean,
legible split. Run AFTER the router is up.

    python demo_batch.py

Each prompt is chosen to land in a specific routing cell with an obvious reason,
including the newer axes: context-overflow and the always-local side channels.
"""
import time, httpx

ROUTER = "http://localhost:8000"

CHATS = [
    # real secret -> local, outranks everything
    ("secret",        "Rotate this for me: AKIAIOSFODNN7EXAMPLE"),
    # proprietary source -> local under RESIDENCY=strict
    ("code block",    "Add error handling to this:\n```python\ndef load(p):\n    return open(p).read()\n```"),
    # simple, clean -> local
    ("simple",        "In one sentence, what's the difference between a list and a tuple?"),
    # complex, clean -> cloud
    ("complex",       "Design a testable architecture for a multi-tenant rate limiter and discuss the tradeoffs."),
    # file paths -> local under strict (sensitivity beats complexity)
    ("paths",         "Refactor the auth flow across services/auth.py and services/session.py for testability."),
    # too big for the local window, nothing secret -> cloud for the room
    ("oversize",      "Summarise this log:\n" + ("2026-01-01 INFO request served ok\n" * 30000)),
]


def main():
    with httpx.Client(timeout=600) as c:
        for label, p in CHATS:
            body = {"model": "auto", "messages": [{"role": "user", "content": p}],
                    "max_tokens": 300}
            try:
                r = c.post(f"{ROUTER}/v1/chat/completions", json=body).json()
                d = r.get("x_inference_router", {})
                print(f"[{label:<10}] -> {d.get('route','?'):5}  {d.get('reason','')[:95]}")
            except Exception as e:
                print(f"[{label:<10}] request failed: {e}")
            time.sleep(1.0)

        # Always-local side channels — no routing decision is made for these.
        try:
            e = c.post(f"{ROUTER}/v1/embeddings",
                       json={"input": "def authenticate(user, token): ..."}).json()
            print(f"[{'embeddings':<10}] -> local  {len(e['data'][0]['embedding'])} dims, never egresses")
        except Exception as ex:
            print(f"[embeddings] failed: {ex}")
        try:
            f = c.post(f"{ROUTER}/v1/completions",
                       json={"prompt": "def fib(n):\n    return ", "suffix": "\nprint(fib(5))",
                             "max_tokens": 24}).json()
            print(f"[{'fim':<10}] -> local  {f['choices'][0]['text'].strip()[:50]!r}")
        except Exception as ex:
            print(f"[fim] failed: {ex}")

    s = httpx.get(f"{ROUTER}/stats", timeout=10).json()
    print(f"\nlocal={s['local']} cloud={s['cloud']} sensitive={s['sensitive']} "
          f"truncation_warnings={s['truncation_warnings']} "
          f"saved=${s['saved']:.4f} spent=${s['cloud_cost']:.4f}")


if __name__ == "__main__":
    main()
