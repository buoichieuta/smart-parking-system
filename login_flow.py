import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import json
import os

class LoginFlow:
    def __init__(self, root, login_check_callback, on_success_callback):
        self.root = root
        self.login_check_callback = login_check_callback
        self.on_success_callback = on_success_callback
        
        self.splash_window = None
        self.login_window = None
        self.progress_bar = None
        self.progress_label = None
        self.username_entry = None
        self.password_entry = None
        self.remember_var = None
        
        self.config_file = "user_config.json"
        self.loading_messages = [
            "Đang khởi tạo hệ thống...",
            "Đang kết nối cơ sở dữ liệu...",
            "Đang chuẩn bị giao diện...",
            "Sắp hoàn thành..."
        ]

    def start(self, loading_task):
        self.show_splash_screen()
        threading.Thread(target=self._safe_loading_wrapper, args=(loading_task,), daemon=True).start()

    def _safe_loading_wrapper(self, loading_task):
        try:
            loading_task(self.update_progress)
        except Exception as e:
            self.root.after(0, lambda: self.update_progress(100, error=str(e)))
        finally:
            self.root.after(500, self.splash_window.destroy)

    def show_splash_screen(self):
        self.splash_window = tk.Toplevel(self.root)
        self.splash_window.title("Loading")
        self.splash_window.overrideredirect(True)
        self.splash_window.configure(bg="white", bd=2, relief=tk.RAISED)

        width, height = 500, 250
        screen_width = self.splash_window.winfo_screenwidth()
        screen_height = self.splash_window.winfo_screenheight()
        x = int((screen_width - width) / 2)
        y = int((screen_height - height) / 2)
        self.splash_window.geometry(f'{width}x{height}+{x}+{y}')

        main_frame = tk.Frame(self.splash_window, bg="white")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        title_label = tk.Label(main_frame, text="HỆ THỐNG BÃI XE THÔNG MINH", 
                                font=("Arial", 18, "bold"), bg="white", fg="#2E86C1")
        title_label.pack(pady=(20, 15))

        self.progress_label = tk.Label(main_frame, text="Đang khởi tạo hệ thống...", 
                                        font=("Arial", 11), bg="white", fg="#666")
        self.progress_label.pack(pady=(0, 10))

        self.progress_bar = ttk.Progressbar(main_frame, orient=tk.HORIZONTAL, 
                                            length=400, mode='determinate')
        self.progress_bar.pack(pady=10)

        self.splash_window.lift()
        self.splash_window.bind("<Destroy>", self.on_splash_destroyed)

    def on_splash_destroyed(self, event):
        if event.widget == self.splash_window:
            self.root.after(100, self.show_login_window)

    def update_progress(self, value, error=None):
        if self.splash_window and self.splash_window.winfo_exists():
            try:
                self.progress_bar['value'] = value
                
                if error:
                    self.progress_label.config(text=f"Lỗi: {error}", fg="red")
                else:
                    message_index = min(int(value / (100 / len(self.loading_messages))), len(self.loading_messages) - 1)
                    self.progress_label.config(text=self.loading_messages[message_index], fg="#666")
                
                if value >= 100 and not error:
                    self.progress_label.config(text="Hoàn thành!", fg="green")
                    
            except tk.TclError:
                pass

    def load_user_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except:
            pass
        return {}

    def save_user_config(self, username, remember):
        try:
            if remember and username:
                config = {'last_username': username}
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
            elif os.path.exists(self.config_file):
                os.remove(self.config_file)
        except:
            pass

    def show_login_window(self):
        self.login_window = tk.Toplevel(self.root)
        self.login_window.title("Đăng nhập Hệ thống")
        self.login_window.state('zoomed')
        self.login_window.configure(bg="#f0f4f7")
        self.login_window.resizable(False, False)

        main_container = tk.Frame(self.login_window, bg="#f0f4f7")
        main_container.pack(fill=tk.BOTH, expand=True)
        main_container.grid_rowconfigure(0, weight=1)
        main_container.grid_columnconfigure(0, weight=1)

        center_frame = tk.Frame(main_container, bg="#f0f4f7")
        center_frame.grid(row=0, column=0)

        title_label = tk.Label(center_frame, text="HỆ THỐNG BÃI XE THÔNG MINH", 
                                font=("Arial", 24, "bold"), bg="#f0f4f7", fg="#2E86C1")
        title_label.grid(row=0, column=0, pady=(0, 30))

        login_frame = tk.LabelFrame(center_frame, text="ĐĂNG NHẬP HỆ THỐNG", 
                                     font=("Arial", 14, "bold"), bg="#ffffff", 
                                     fg="#333", bd=2, relief=tk.GROOVE, padx=30, pady=20)
        login_frame.grid(row=1, column=0, padx=20, pady=20)

        tk.Label(login_frame, text="Tên đăng nhập:", font=("Arial", 12, "bold"), 
                 bg="#ffffff", fg="#333").grid(row=0, column=0, sticky='w', padx=(0, 10), pady=10)
        
        self.username_entry = ttk.Entry(login_frame, font=("Arial", 12), width=25)
        self.username_entry.grid(row=0, column=1, pady=10)

        tk.Label(login_frame, text="Mật khẩu:", font=("Arial", 12, "bold"), 
                 bg="#ffffff", fg="#333").grid(row=1, column=0, sticky='w', padx=(0, 10), pady=10)
        
        self.password_entry = ttk.Entry(login_frame, font=("Arial", 12), width=25, show="*")
        self.password_entry.grid(row=1, column=1, pady=10)

        self.remember_var = tk.BooleanVar()
        remember_check = tk.Checkbutton(login_frame, text="Ghi nhớ tên đăng nhập", 
                                         variable=self.remember_var, font=("Arial", 10), 
                                         bg="#ffffff", activebackground="#ffffff")
        remember_check.grid(row=2, column=0, columnspan=2, sticky='w', pady=10)

        button_frame = tk.Frame(login_frame, bg="#ffffff")
        button_frame.grid(row=3, column=0, columnspan=2, pady=15)

        self.login_btn = ttk.Button(button_frame, text="ĐĂNG NHẬP", 
                                    command=self.handle_login)
        self.login_btn.pack(side=tk.LEFT, padx=10)

        exit_btn = ttk.Button(button_frame, text="THOÁT", 
                              command=self.root.destroy)
        exit_btn.pack(side=tk.LEFT, padx=10)

        config = self.load_user_config()
        if 'last_username' in config:
            self.username_entry.insert(0, config['last_username'])
            self.remember_var.set(True)
            self.password_entry.focus()
        else:
            self.username_entry.focus()

        self.password_entry.bind("<Return>", lambda e: self.handle_login())
        self.username_entry.bind("<Return>", lambda e: self.password_entry.focus())
        
        self.login_window.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.login_window.lift()

    def handle_login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        if not username:
            messagebox.showwarning("Thiếu thông tin", "Vui lòng nhập tên đăng nhập.", 
                                   parent=self.login_window)
            self.username_entry.focus()
            return

        if not password:
            messagebox.showwarning("Thiếu thông tin", "Vui lòng nhập mật khẩu.", 
                                   parent=self.login_window)
            self.password_entry.focus()
            return

        self.login_btn.config(state='disabled')
        
        def check_login_thread():
            try:
                user_info_tuple = self.login_check_callback(username, password)
                
                if user_info_tuple:
                    self.save_user_config(username, self.remember_var.get())
                    user_info_dict = {
                        'id': user_info_tuple[0], 
                        'name': user_info_tuple[1], 
                        'role': user_info_tuple[2]
                    }
                    self.root.after(0, lambda: self.on_success_callback(user_info_dict))
                else:
                    self.root.after(0, lambda: messagebox.showerror("Lỗi đăng nhập", 
                                    "Tên đăng nhập hoặc mật khẩu không đúng.", 
                                    parent=self.login_window))
                    self.root.after(0, self.clear_password_field)
                    
            except Exception as e:
                error_msg = f"Lỗi kết nối: {str(e)}"
                self.root.after(0, lambda: messagebox.showerror("Lỗi hệ thống", error_msg, 
                                parent=self.login_window))
            finally:
                self.root.after(100, self.enable_login_button)

        threading.Thread(target=check_login_thread, daemon=True).start()

    def enable_login_button(self):
        if self.login_btn and self.login_btn.winfo_exists():
            self.login_btn.config(state='normal')

    def clear_password_field(self):
        if self.password_entry and self.password_entry.winfo_exists():
            self.password_entry.delete(0, tk.END)
            self.password_entry.focus()


if __name__ == "__main__":
    def mock_login_check(username, password):
        time.sleep(1)
        if username == "admin" and password == "123":
            return (1, "Quản trị viên", "admin")
        elif username == "user" and password == "password":
            return (2, "Người dùng", "user")
        return None

    def on_login_success(user_info):
        messagebox.showinfo("Đăng nhập thành công", 
                           f"Chào mừng, {user_info['name']}!", 
                           parent=app.login_window)
        
        if app.login_window and app.login_window.winfo_exists():
            app.login_window.destroy() 
        
        main_window = tk.Toplevel(app.root)
        main_window.title("Ứng dụng Chính")
        main_window.state('zoomed')
        main_window.configure(bg="#e0e8ed")
        
        tk.Label(main_window, 
                 text=f"Chào mừng {user_info['name']}!",
                 font=("Arial", 20, "bold"), 
                 bg="#e0e8ed", fg="#2E86C1").pack(pady=50)
        
        main_window.protocol("WM_DELETE_WINDOW", app.root.destroy)

    def mock_loading_task(update_progress_callback):
        for i in range(10):
            time.sleep(0.3)
            progress = int((i + 1) / 10 * 100)
            update_progress_callback(progress)

    root = tk.Tk()
    root.withdraw()

    app = LoginFlow(root, mock_login_check, on_login_success)
    app.start(mock_loading_task)

    root.mainloop()