import subprocess
import platform
import time

def open_terminal_and_run(command: str, title: str):
    """Mở một cửa sổ Terminal mới và thực thi lệnh tùy theo Hệ điều hành."""
    system = platform.system()
    
    if system == "Windows":
        # Windows: Dùng lệnh 'start' để mở cửa sổ cmd mới, '/k' để giữ cửa sổ không bị tắt khi lỗi
        windows_cmd = f'start "{title}" cmd /k "{command}"'
        subprocess.Popen(windows_cmd, shell=True)
        
    elif system == "Darwin":
        # macOS: Dùng AppleScript để gọi ứng dụng Terminal
        apple_script = f'tell application "Terminal" to do script "{command}"'
        subprocess.Popen(["osascript", "-e", apple_script])
        
    elif system == "Linux":
        # Linux (Desktop): Thử mở bằng gnome-terminal
        try:
            linux_cmd = ["gnome-terminal", "--title", title, "--", "bash", "-c", f"{command}; exec bash"]
            subprocess.Popen(linux_cmd)
        except FileNotFoundError:
            print("❌ Không tìm thấy gnome-terminal (Có thể bạn đang chạy qua SSH không có UI).")
            print(f"Hãy chạy ngầm lệnh sau: {command}")
    else:
        print(f"Hệ điều hành {system} chưa được hỗ trợ mở terminal popup.")

if __name__ == "__main__":
    print("🚀 Đang khởi động Micro Trading Platform...")
    
    # 1. Mở Terminal và chạy Subscriber (Đứng im lắng nghe)
    print("🎧 Đang mở cửa sổ cho Subscriber (UDP 9999)...")
    open_terminal_and_run("python main.py --mode subscriber", "MicroTrading - Subscriber")
    
    # Đợi 2 giây để đảm bảo Subscriber đã bind cổng mạng thành công
    time.sleep(2)
    
    # 2. Mở Terminal và chạy Publisher (Mặc định lấy Binance)
    print("📈 Đang mở cửa sổ cho Publisher (Binance Stream)...")
    open_terminal_and_run("python main.py --mode publisher --source binance", "MicroTrading - Publisher")
    
    print("✅ Hoàn tất! Hai cửa sổ Terminal đã được bật lên.")