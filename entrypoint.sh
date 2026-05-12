#!/bin/bash
# entrypoint.sh — starts the full Kafka pipeline inside Docker.
#
# Environment variables (set in docker-compose.yml):
#   SOURCE                  : binance | coinbase | kraken | simulator  (default: binance)
#   SYMBOLS                 : comma-separated trading pairs             (default: BTC/USDT,ETH/USDT,SOL/USDT)
#   KAFKA_BOOTSTRAP_SERVERS : broker address                           (default: kafka:29092)

set -e

SOURCE="${SOURCE:-binance}"
SYMBOLS="${SYMBOLS:-BTC/USDT,ETH/USDT,SOL/USDT}"
KAFKA_BOOTSTRAP_SERVERS="${KAFKA_BOOTSTRAP_SERVERS:-kafka:29092}"
KAFKA_HOST="${KAFKA_BOOTSTRAP_SERVERS%%:*}"
KAFKA_PORT="${KAFKA_BOOTSTRAP_SERVERS##*:}"

echo "=== Micro Trading Platform — Kafka mode ==="
echo "Source  : $SOURCE"
echo "Symbols : $SYMBOLS"
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
echo "[2/6] Running DB migration..."
python -c "from database.db import init_db; init_db(); print('  DB ready.')"

# ---------------------------------------------------------------------------
# Step 3: Pre-create Kafka topics
# ---------------------------------------------------------------------------
# replication_factor:
#   development → 1  (single local broker)
#   production  → 3  (Aiven standard — requires RF ≥ number of brokers)
# num_partitions:
#   Aiven free/startup plan: max 2 partitions per topic → default 1
#   Override by setting KAFKA_NUM_PARTITIONS in .env (e.g. =2 for paid plans)
REPLICATION_FACTOR=1
if [ "${KAFKA_ENV:-development}" = "production" ]; then
    REPLICATION_FACTOR=2
fi
KAFKA_NUM_PARTITIONS="${KAFKA_NUM_PARTITIONS:-1}"
echo "[3/6] Creating Kafka topics (RF=${REPLICATION_FACTOR}, partitions=${KAFKA_NUM_PARTITIONS})..."
# Prefix-notation exports these as env vars to the Python subprocess
REPLICATION_FACTOR=$REPLICATION_FACTOR KAFKA_NUM_PARTITIONS=$KAFKA_NUM_PARTITIONS \
python -c "
import os, sys, traceback
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError
from streaming.kafka_config import get_kafka_ssl_kwargs

rf  = int(os.environ.get('REPLICATION_FACTOR', 1))
np_ = int(os.environ.get('KAFKA_NUM_PARTITIONS', 1))
ssl_kwargs = get_kafka_ssl_kwargs()
try:
    client = KafkaAdminClient(bootstrap_servers='$KAFKA_BOOTSTRAP_SERVERS', **ssl_kwargs)
except Exception as e:
    print(f'  ERROR connecting to broker for topic creation: {e}', file=sys.stderr)
    sys.exit(1)

topics = [
    NewTopic(name='market.price_events', num_partitions=np_, replication_factor=rf),
    NewTopic(name='market.alerts',       num_partitions=np_, replication_factor=rf),
    NewTopic(name='market.orders',       num_partitions=np_, replication_factor=rf),
]
try:
    client.create_topics(topics)
    print('  Topics created successfully.')
except TopicAlreadyExistsError:
    print('  Topics already exist — skipping.')
except Exception as e:
    traceback.print_exc()
    print(f'  ERROR: topic creation failed: {e}', file=sys.stderr)
    sys.exit(1)
finally:
    client.close()
"

# ---------------------------------------------------------------------------
# Step 4: Start Kafka workers as background processes
# ---------------------------------------------------------------------------
echo "[4/6] Starting kafka-cep worker..."
python main.py --mode kafka-cep &
CEP_PID=$!

echo "[5/6] Starting kafka-strategy workers (3 instances, group=strategy-group)..."
python main.py --mode kafka-strategy &
STRATEGY1_PID=$!
python main.py --mode kafka-strategy &
STRATEGY2_PID=$!
python main.py --mode kafka-strategy &
STRATEGY3_PID=$!

echo "[5/6] Starting kafka-db-writer..."
python main.py --mode kafka-db-writer &
DBWRITER_PID=$!

echo "[6/6] Starting kafka-publisher ($SOURCE $SYMBOLS)..."
python main.py --mode kafka-publisher --source "$SOURCE" --symbols "$SYMBOLS" &
PUBLISHER_PID=$!

# ---------------------------------------------------------------------------
# Monitor: if any worker exits, log it (container keeps running via uvicorn)
# ---------------------------------------------------------------------------
monitor_workers() {
    while true; do
        sleep 10
        for NAME_PID in \
            "kafka-cep:$CEP_PID" \
            "kafka-strategy-1:$STRATEGY1_PID" \
            "kafka-strategy-2:$STRATEGY2_PID" \
            "kafka-strategy-3:$STRATEGY3_PID" \
            "kafka-db-writer:$DBWRITER_PID" \
            "kafka-publisher:$PUBLISHER_PID"; do
            NAME="${NAME_PID%%:*}"
            PID="${NAME_PID##*:}"
            if ! kill -0 "$PID" 2>/dev/null; then
                echo "WARNING: $NAME (PID $PID) exited. Restarting..."
                case "$NAME" in
                    kafka-cep)
                        python main.py --mode kafka-cep &
                        CEP_PID=$!
                        ;;
                    kafka-strategy-1|kafka-strategy-2|kafka-strategy-3)
                        python main.py --mode kafka-strategy &
                        ;;
                    kafka-db-writer)
                        python main.py --mode kafka-db-writer &
                        DBWRITER_PID=$!
                        ;;
                    kafka-publisher)
                        python main.py --mode kafka-publisher --source "$SOURCE" --symbols "$SYMBOLS" &
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