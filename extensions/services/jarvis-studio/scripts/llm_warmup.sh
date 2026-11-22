#!/usr/bin/env bash
# Pre-load Hermes's system prompt into llama-server's prompt cache after every
# llama-server start, so the operator's first question doesn't pay for reading
# ~16K tokens of instructions (about 7.5 min on the Iris Xe).
set -u
LLM="${JARVIS_LLM_URL:-http://127.0.0.1:11434}"
LOCK="${XDG_RUNTIME_DIR:-/tmp}/jarvis-llm-warmup.lock"

warm() {
    exec 9>"$LOCK"
    flock -n 9 || { echo "warm-up already running"; return; }
    for _ in $(seq 120); do   # wait up to 10 min for the model to load
        curl -sf --max-time 3 "$LLM/health" >/dev/null && break
        sleep 5
    done
    for _ in $(seq 60); do
        [ "$(docker inspect -f '{{.State.Running}}' ods-hermes 2>/dev/null)" = true ] && break
        sleep 5
    done
    echo "$(date '+%F %T') warming Hermes prompt"
    local s=$SECONDS
    timeout 1800 docker exec ods-hermes hermes -z "Warm-up check. Reply with just: OK." >/dev/null 2>&1
    echo "$(date '+%F %T') warm-up done (rc=$?) in $((SECONDS - s)) s"
}

warm &
docker events --filter container=ods-llama-server --filter event=start --format '{{.Action}}' |
while read -r _; do
    echo "$(date '+%F %T') llama-server started"
    warm &
done
