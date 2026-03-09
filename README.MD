Data Simulator → CEP Engine → Trading Rule → Output

Mô phỏng data trước
CEP engine xử lý:
+ moving average
+ spike detection
+ anomaly detection

Sau khi pipeline chạy ổn, thay simulator bằng producer gửi vào Kafka.


Việc đầu tiên của cả nhóm (trong 1–2 ngày)
+ Tạo prototype chạy local:
+ price stream → CEP → trading rule → log output

Chưa cần: Kafka, API, UI (copy của Opensource Trading Platform) - TradingView Charting Library


Task:
+ A: tìm dataset crypto + viết market data simulator
+ B: CEP engine: implement các rule moving average, price spike detection, volume anomaly
+ C: Trading Engine: rule-based trading, order simulation, position tracking
+ D: nối pipeline, log output, chuẩn bị API



Tìm hiểu thêm phải có Rules của hệ thống nữa (ví dụ đòn bẩy, hết tiền không cho đặt, các chức năng thật của hệ thống stock trading ...)


keyword: CEP, Directly Access Memory

3) Come up with a program structure, data capture? data storage? data cleaning? processing? event processing? signal generation? order generation? order confirmation? real time p/l? accounting?