# run_kafka_all.ps1
# Starts the full Kafka pipeline + API server in one command.
#
# Usage:
#   .\run_kafka_all.ps1                          # Binance BTC/USDT (default)
#   .\run_kafka_all.ps1 -Source binance -Symbol ETH/USDT
#   .\run_kafka_all.ps1 -Source simulator
#
# Prerequisites:
#   docker compose up -d        (Kafka + Zookeeper must already be running)
#   pip install -r requirement.txt

param (
    [string]$Source = "binance",
    [string]$Symbol = "BTC/USDT"
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Root

Write-Host ""
Write-Host "=== Micro Trading Platform — Kafka mode ===" -ForegroundColor Cyan
Write-Host "Source : $Source"
Write-Host "Symbol : $Symbol"
Write-Host ""

# ---------------------------------------------------------------------------
# Step 1: Run DB migration so all columns exist before any worker starts
# ---------------------------------------------------------------------------
Write-Host "[1/5] Running DB migration..." -ForegroundColor Yellow
python -c "from database.db import init_db; init_db(); print('  DB ready.')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: DB migration failed. Aborting." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Step 2: Verify Kafka is reachable
# ---------------------------------------------------------------------------
Write-Host "[2/5] Checking Kafka on localhost:9092..." -ForegroundColor Yellow
$kafkaCheck = python -c "
from kafka.admin import KafkaAdminClient
try:
    c = KafkaAdminClient(bootstrap_servers='localhost:9092', request_timeout_ms=3000)
    c.close()
    print('  Kafka reachable.')
except Exception as e:
    print(f'  ERROR: {e}')
    exit(1)
"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Kafka broker not reachable. Run: docker compose up -d" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Step 3: Launch kafka-cep worker in a new PowerShell window
# ---------------------------------------------------------------------------
Write-Host "[3/5] Starting kafka-cep worker..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "Set-Location '$Root'; Write-Host 'CEP Worker' -ForegroundColor Green; python main.py --mode kafka-cep"

Start-Sleep -Seconds 1

# ---------------------------------------------------------------------------
# Step 4: Launch kafka-strategy worker in a new PowerShell window
# ---------------------------------------------------------------------------
Write-Host "[4/5] Starting kafka-strategy worker..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "Set-Location '$Root'; Write-Host 'Strategy Worker' -ForegroundColor Green; python main.py --mode kafka-strategy"

Start-Sleep -Seconds 1

# ---------------------------------------------------------------------------
# Step 5: Launch kafka-publisher in a new PowerShell window
# ---------------------------------------------------------------------------
Write-Host "[5/5] Starting kafka-publisher ($Source $Symbol)..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "Set-Location '$Root'; Write-Host 'Publisher' -ForegroundColor Green; python main.py --mode kafka-publisher --source $Source --symbol '$Symbol'"

Start-Sleep -Seconds 2

# ---------------------------------------------------------------------------
# API server runs in THIS window so Ctrl+C cleanly stops everything
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "All workers started. Launching API server..." -ForegroundColor Cyan
Write-Host "Dashboard -> http://localhost:8000"
Write-Host "Kafka UI  -> http://localhost:8080"
Write-Host "Press Ctrl+C to stop the API server (worker windows stay open)."
Write-Host ""

uvicorn api.api_server:app --host 0.0.0.0 --port 8000
