#!/usr/bin/env bash
# Launch InferenceRouter.
set -euo pipefail
cd "$(dirname "$0")"

usage() {
  cat <<'EOF'
Usage: ./run.sh <ANTHROPIC_API_KEY>
       ./run.sh --local-only
       ./run.sh --help

  <ANTHROPIC_API_KEY>  Your Claude API key (sk-ant-...). Enables cloud routing.
  --local-only         Run on-prem only. Cloud routing fails safe to local.
  --help, -h           Show this help.

If ANTHROPIC_API_KEY is already exported in your shell, running with no
arguments picks it up automatically.

Other config is read from the environment (defaults live in router.py):
  LOCAL_BASE, LOCAL_MODEL, CLOUD_BASE, CLOUD_MODEL,
  CLOUD_IN_PER_MTOK, CLOUD_OUT_PER_MTOK
See README.md for details.
EOF
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  --local-only)
    unset ANTHROPIC_API_KEY 2>/dev/null || true
    ;;
  "")
    if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
      usage
      exit 0
    fi
    ;;
  *)
    export ANTHROPIC_API_KEY="$1"
    ;;
esac

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/uvicorn router:app --host 0.0.0.0 --port 8000
