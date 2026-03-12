# Sử dụng phiên bản Python cực nhẹ
FROM python:3.12-slim

# Chuyển vào thư mục làm việc trong Container
WORKDIR /app

# Copy file requirement và cài đặt thư viện
COPY requirement.txt .
RUN pip install --no-cache-dir -r requirement.txt

# Copy toàn bộ mã nguồn dự án của bạn vào Container
COPY . .

# Cấp quyền thực thi cho file shell script
RUN chmod +x entrypoint.sh

# Lệnh chạy mặc định khi Container bật lên
CMD ["./entrypoint.sh"]