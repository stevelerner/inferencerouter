# Development Guide

## Setup

```bash
# Clone and enter directory
cd inferencerouter

# Create venv and install dependencies (including dev)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Testing

```bash
# Run all tests
python -m pytest test_router.py -v

# Run with debug output
ROUTER_DEBUG=1 python -m pytest test_router.py -v

# Run a single test
python -m pytest test_router.py::TestClassify::test_api_key_pins_local -v
```

## Local Development

1. **Copy the env template:**
   ```bash
   cp .env.example .env
   # Edit .env with your local inference server URL and (optionally) Claude API key
   ```

2. **Run the router:**
   ```bash
   source .env
   ./run.sh
   # or: ./run.sh --local-only
   ```

3. **Test the API:**
   ```bash
   curl -X POST http://localhost:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"messages": [{"role": "user", "content": "Hello"}]}'
   ```

4. **Watch the console:**
   Open http://localhost:8000 in your browser to see routing decisions in real-time.

## Code Style

- **Type hints:** Use throughout. Example:
  ```python
  def classify(messages: List[Dict[str, Any]]) -> RoutingDecision:
  ```
- **Logging:** Use the `log` object (already configured):
  ```python
  log.info("request routed to cloud")
  log.debug("detailed info")
  ```
- **No hardcoded values:** Config via environment variables only.

## Key Files

- `router.py` — core routing logic, endpoints, telemetry
- `test_router.py` — unit tests (run with `pytest`)
- `.env.example` — configuration template
- `console.html` — live dashboard (auto-updated by `/stats` endpoint)
- `demo_batch.py` — demonstration requests

## Adding New Features

1. **New routing axis?** Add logic to `classify()` and update tests in `test_router.py`
2. **New telemetry?** Add to `STATE` dict and `record()` function
3. **New endpoint?** Add to `router.py` and document in README
4. **Config change?** Update `.env.example` and add validation in `_validate_config()`

## Common Issues

- **Import errors?** Ensure you've run `pip install -r requirements.txt`
- **Config errors?** Check `.env.example` for required variables
- **Tests failing?** Verify env vars are set and Ollama/Claude endpoints are reachable
