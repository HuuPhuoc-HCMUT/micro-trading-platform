#!/bin/bash
# entrypoint.sh — starts the full Kafka pipeline inside Docker.
#
# Environment variables (set in docker-compose.yml):
#   SOURCE                  : binance | coinbase | kraken | simulator  (default: binance)
#   SYMBOL                  : trading pair                              (default: BTC/USDT)
#   KAFKA_BOOTSTRAP_SERVERS : broker address                           (default: kafka:29092)

set -e

SOURCE="${SOURCE:-binance}"
SYMBOL="${SYMBOL:-BTC/USDT}"
KAFKA_BOOTSTRAP_SERVERS="${KAFKA_BOOTSTRAP_SERVERS:-kafka:29092}"
KAFKA_HOST="${KAFKA_BOOTSTRAP_SERVERS%%:*}"
KAFKA_PORT="${KAFKA_BOOTSTRAP_SERVERS##*:}"

echo "=== Micro Trading Platform — Kafka mode ==="
echo "Source  : $SOURCE"
echo "Symbol  : $SYMBOL"
echo "Broker  : $KAFKA_BOOTSTRAP_SERVERS"

# ---------------------------------------------------------------------------
# Step 1: Wait for Kafka broker to be reachable (max 60 s)
# ---------------------------------------------------------------------------
echo "[1/5] Waiting for Kafka broker at $KAFKA_HOST:$KAFKA_PORT..."
RETRIES=0
until python -c "
import socket, sys
try:
    s = socket.create_connection(('$KAFKA_HOST', $KAFKA_PORT), timeout=2)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    RETRIES=$((RETRIES + 1))
    if [ $RETRIES -ge 30 ]; then
        echo "ERROR: Kafka not reachable after 60 s. Aborting."
        exit 1
    fi
    sleep 2
done
echo "  Kafka is up."

# ---------------------------------------------------------------------------
# Step 2: DB migration
# ---------------------------------------------------------------------------
echo "[2/5] Running DB migration..."
python -c "from database.db import init_db; init_db(); print('  DB ready.')"

# ---------------------------------------------------------------------------
# Step 3: Start Kafka workers as background processes
# ---------------------------------------------------------------------------
echo "[3/5] Starting kafka-cep worker..."
python main.py --mode kafka-cep &
CEP_PID=$!

echo "[4/5] Starting kafka-strategy worker..."
python main.py --mode kafka-strategy &
STRATEGY_PID=$!

echo "[5/5] Starting kafka-publisher ($SOURCE $SYMBOL)..."
python main.py --mode kafka-publisher --source "$SOURCE" --symbol "$SYMBOL" &
PUBLISHER_PID=$!

# ---------------------------------------------------------------------------
# Monitor: if any worker exits, log it (container keeps running via uvicorn)
# ---------------------------------------------------------------------------
monitor_workers() {
    while true; do
        sleep 10
        for NAME_PID in "kafka-cep:$CEP_PID" "kafka-strategy:$STRATEGY_PID" "kafka-publisher:$PUBLISHER_PID"; do
            NAME="${NAME_PID%%:*}"
            PID="${NAME_PID##*:}"
            if ! kill -0 "$PID" 2>/dev/null; then
                echo "WARNING: $NAME (PID $PID) exited. Restarting..."
                case "$NAME" in
                    kafka-cep)
                        python main.py --mode kafka-cep &
                        CEP_PID=$!
                        ;;
                    kafka-strategy)
                        python main.py --mode kafka-strategy &
                        STRATEGY_PID=$!
                        ;;
                    kafka-publisher)
                        python main.py --mode kafka-publisher --source "$SOURCE" --symbol "$SYMBOL" &
                        PUBLISHER_PID=$!
                        ;;
                esac
            fi
        done
    done
}
monitor_workers &

# ---------------------------------------------------------------------------
# API server runs in the foreground — keeps the container alive
# ---------------------------------------------------------------------------
echo "Starting API server on 0.0.0.0:8000..."
exec uvicorn api.api_server:app --host 0.0.0.0 --port 8000