# source this before ./run.sh   —   navigator: RTX 5090 32GB @ 192.168.68.71
export LOCAL_BASE="http://192.168.68.71:11434/v1"
export LOCAL_MODEL="qwen3.8:27b-mtp-q4_K_M"
export LOCAL_FIM="qwen2.5-coder:1.5b-base"
export LOCAL_EMBED="nomic-embed-text:latest"

export CLOUD_MODEL="claude-opus-5"
export CLOUD_IN_PER_MTOK="5.0"
export CLOUD_OUT_PER_MTOK="25.0"

# Ollama reserves half of num_ctx for generation, so OLLAMA_NUM_CTX=131072
# yields 65538 usable input tokens. Keep these two in sync.
export LOCAL_CTX_TOKENS="65536"

# strict = code/paths stay on-prem | secrets-only = only real secrets pin local
export RESIDENCY="strict"
