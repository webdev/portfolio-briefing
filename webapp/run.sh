#!/bin/bash
# Pick the first free port from a high-port candidate list
for p in 17776 17777 17888 23456 42069 31415 9999 8888; do
    if ! lsof -i :$p > /dev/null 2>&1; then
        PORT=$p
        break
    fi
done
[ -z "$PORT" ] && { echo "No free port found"; exit 1; }
echo "▶ Briefing dashboard: http://localhost:$PORT"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port $PORT --reload
