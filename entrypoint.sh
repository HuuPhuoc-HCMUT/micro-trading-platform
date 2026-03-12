#!/bin/bash

echo "🎧 Khởi động Subscriber (Lắng nghe UDP & Kết nối Database)..."
python main.py --mode subscriber &
sleep 2

echo "📈 Bật luồng dữ liệu Binance (Publisher)..."
python main.py --mode publisher --source binance &

echo "🚀 Khởi động API Server ở cổng 8000..."
# Phải thêm --host 0.0.0.0 để API có thể giao tiếp ra ngoài Docker
uvicorn api.api_server:app --host 0.0.0.0 --port 8000