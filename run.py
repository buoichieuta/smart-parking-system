import tkinter as tk
from database import Database
from main_app import MainApplication
from login_flow import LoginFlow
import logging
import threading
import webbrowser
import time
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

NGROK_URL = "https://pegasus-firm-distinctly.ngrok-free.app" # URL Ngrok cho Streamlit

node_server_process = None

def create_templates_folder():
    if not os.path.exists('templates'):
        os.makedirs('templates')
        logging.info("Created templates folder")

def start_node_server():
    global node_server_process
    try:
        # Đường dẫn tới server.js trong folder src
        server_js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "server.js")
        
        # Kiểm tra xem file server.js có tồn tại không
        if not os.path.exists(server_js_path):
            logging.error(f"❌ server.js not found at: {server_js_path}")
            return False
            
        logging.info("🚀 Starting Node.js server (src/server.js)...")
        
        # Sửa lệnh chạy Node.js - dùng "node" thay vì "python -m node"
        node_server_process = subprocess.Popen(
            ["node", server_js_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            text=True
        )
        
        # Đợi một chút và kiểm tra xem process có chạy thành công không
        time.sleep(2)
        
        if node_server_process.poll() is None:
            logging.info("✅ Node.js server (src/server.js) started successfully.")
            return True
        else:
            # Lấy error output nếu có
            stdout, stderr = node_server_process.communicate()
            logging.error(f"❌ Node.js server exited prematurely. Error: {stderr}")
            return False
            
    except FileNotFoundError:
        logging.error("❌ Node.js is not installed or not in PATH. Please install Node.js first.")
        return False
    except Exception as e:
        logging.error(f"❌ Failed to start Node.js server: {e}")
        return False

def stop_node_server():
    global node_server_process
    if node_server_process:
        logging.info("Stopping Node.js server (src/server.js)...")
        try:
            node_server_process.terminate() # Gửi tín hiệu dừng
            node_server_process.wait(timeout=5) # Đợi process dừng
            if node_server_process.poll() is None: # Nếu vẫn chưa dừng
                node_server_process.kill() # Buộc dừng
            logging.info("Node.js server (src/server.js) stopped.")
        except Exception as e:
            logging.error(f"Error stopping Node.js server: {e}")
        finally:
            node_server_process = None

def check_node_installation():
    """Kiểm tra xem Node.js đã được cài đặt chưa"""
    try:
        result = subprocess.run(["node", "--version"], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logging.info(f"✅ Node.js version: {result.stdout.strip()}")
            return True
        else:
            logging.error("❌ Node.js not found")
            return False
    except Exception as e:
        logging.error(f"❌ Cannot check Node.js installation: {e}")
        return False

def start_ngrok_tunnel():
    try:
        logging.info("🚀 Starting ngrok tunnel for streamlit-app...")
        # Đảm bảo ngrok config đúng với tên tunnel 'streamlit-app'
        # Trong ngrok.yml của bạn phải có:
        # tunnels:
        #   streamlit-app:
        #     addr: 5000
        #     proto: http
        subprocess.Popen([
            "ngrok", "start", "streamlit-app"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        logging.info(f"✅ Ngrok tunnel should be running on: {NGROK_URL}")
    except Exception as e:
        logging.error(f"❌ Failed to start ngrok: {e}")

def check_ngrok_connection():
    try:
        import requests
        response = requests.get(f"{NGROK_URL}/health", timeout=5, headers={
            'ngrok-skip-browser-warning': 'true'
        })
        if response.status_code == 200:  # Sửa lỗi: status_status -> status_code
            logging.info("✅ Ngrok connection verified")
            return True
    except Exception as e:
        logging.warning(f"⚠️ Ngrok connection check failed: {e}")
    return False

def main():
    create_templates_folder()
    
    # Kiểm tra Node.js trước khi khởi động
    if not check_node_installation():
        logging.error("❌ Node.js is required but not found. Please install Node.js and try again.")
        input("Press Enter to exit...")
        return
    
    root = tk.Tk()
    root.withdraw()

    # Bắt đầu Node.js server ngay khi ứng dụng khởi chạy
    def start_node_server_thread():
        server_started = start_node_server()
        if not server_started:
            logging.error("❌ Node.js server did not start successfully. Check server.js logs.")
    
    threading.Thread(target=start_node_server_thread, daemon=True).start()

    try:
        db = Database()
        logging.info("✅ Database connection established")
    except Exception as e:
        logging.error(f"❌ Không thể kết nối tới database. Lỗi: {e}")
        root.destroy()
        return
    
    main_app = MainApplication(root, db)

    def start_dashboard_server():
        try:
            from dashboard_server import create_dashboard_server, run_dashboard_server
            logging.info("🔧 Initializing dashboard server...")
            dashboard = create_dashboard_server(db)
            logging.info("🚀 Starting dashboard server on 0.0.0.0:5000")
            run_dashboard_server(host='0.0.0.0', port=5000)
        except Exception as e:
            logging.error(f"❌ Dashboard server error: {e}")

    def open_dashboard():
        time.sleep(5)
        try:
            dashboard_url = f"{NGROK_URL}/"
            if check_ngrok_connection():
                webbrowser.open(dashboard_url)
                logging.info(f"🌐 Dashboard opened: {dashboard_url}")
            else:
                logging.info("🔄 Trying localhost fallback...")
                webbrowser.open('http://localhost:5000')
                logging.info("🌐 Dashboard opened: http://localhost:5000")
        except Exception as e:
            logging.error(f"❌ Could not open browser: {e}")

    def show_startup_info():
        try:
            info_window = tk.Toplevel(root)
            info_window.title("X PARKING - System Info")
            info_window.geometry("500x350")
            info_window.resizable(False, False)
            
            info_text = f"""
🅿️ X PARKING - Smart Parking System
            
🌐 Dashboard URLs:
• Ngrok (Primary): {NGROK_URL}
• Local: http://localhost:5000

📊 System Status:
• Database: Connected ✅
• Node.js Server: Running from src/server.js ✅
• MQTT Broker: 192.168.2.12:1883
• Auto-refresh: 5 seconds

🎛️ Features:
• Real-time monitoring
• MQTT control commands  
• Image gallery
• Activity logging
• Auto-refresh dashboard

Dashboard sẽ tự động mở sau khi đăng nhập thành công!
            """
            
            text_widget = tk.Text(info_window, wrap=tk.WORD, padx=20, pady=20)
            text_widget.insert(tk.END, info_text)
            text_widget.config(state=tk.DISABLED)
            text_widget.pack(fill=tk.BOTH, expand=True)
            
            def close_info():
                info_window.destroy()
            
            close_btn = tk.Button(info_window, text="OK", command=close_info, 
                                bg="#2E86C1", fg="white", font=("Arial", 12, "bold"))
            close_btn.pack(pady=10)
            
            info_window.after(10000, close_info)
            
        except Exception as e:
            logging.error(f"Error showing startup info: {e}")

    def on_login_success(user_info):
        logging.info(f"✅ Đăng nhập thành công với user: {user_info['name']}")
        if login_manager.login_window:
            login_manager.login_window.destroy()
        
        show_startup_info()
        
        threading.Thread(target=start_ngrok_tunnel, daemon=True).start()
        threading.Thread(target=start_dashboard_server, daemon=True).start()
        threading.Thread(target=open_dashboard, daemon=True).start()
        
        main_app.start(user_info)

    login_manager = LoginFlow(
        root=root,
        login_check_callback=db.check_user,
        on_success_callback=on_login_success
    )

    def loading_task(update_progress_callback):
        try:
            update_progress_callback(10)
            main_app.load_models(update_progress_callback)
            update_progress_callback(100)
        except Exception as e:
            logging.error(f"Loading task error: {e}")
            update_progress_callback(100, error=str(e))

    try:
        login_manager.start(loading_task)
        root.mainloop()
    except KeyboardInterrupt:
        logging.info("Application terminated by user")
    except Exception as e:
        logging.error(f"Application error: {e}")
    finally:
        logging.info("🔄 Cleaning up...")
        try:
            if db:
                db.close()
            stop_node_server() 
        except:
            pass

if __name__ == "__main__":
    print("🅿️ X PARKING - Smart Parking System")
    print("=" * 50)
    print(f"🌐 Dashboard will be available at: {NGROK_URL}")
    print("📱 Make sure ngrok is configured for streamlit-app")
    print("🚀 Starting application...")
    print("🔧 Node.js server will run from: src/server.js")
    print("=" * 50)
    
    main()