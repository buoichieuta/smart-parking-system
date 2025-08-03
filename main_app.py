import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from PIL import Image, ImageTk
import cv2
import threading
import time
import os
import logging
from datetime import datetime
import math
from tkcalendar import DateEntry
from payment import PaymentManager
import urllib.request
import paho.mqtt.client as mqtt
import json
import smtplib
from email.mime.text import MIMEText

LPR_AVAILABLE = False
try:
    from QUET_BSX import OptimizedLPR
    LPR_AVAILABLE = True
except ImportError:
    logging.warning("Thư viện QUET_BSX không tìm thấy. Chức năng nhận dạng biển số sẽ bị vô hiệu hóa.")

class MainApplication:
    def __init__(self, root, db_connection):
        self.root = root
        self.db = db_connection
        self.lpr_system = None
        if LPR_AVAILABLE:
            self.lpr_system = OptimizedLPR()
        self.current_user = None
        self.current_screen = None
        self.vid_in, self.vid_out = None, None
        self.latest_frame_in = None
        self.latest_frame_out = None
        self.frame_lock_in = threading.Lock()
        self.frame_lock_out = threading.Lock()
        self.camera_thread_in = None
        self.camera_thread_out = None
        self.is_running = False
        self.current_frame_in, self.current_frame_out = None, None
        self._camera_update_id = None
        self.active_vehicle_id = None
        self.payment_config = {
            #'script_url': 'https://script.google.com/macros/s/AKfycbxchRS9MGnEfn2SC_56vLhX04Hz_5BsN0VDQs4P8bN07dzOyd2S5rqHO9efTJcPbisi/exec',
            'sepay_api_url': 'http://localhost:3000/api/lsgd',
            'bank_id': 'MB',
            'account_no': '0396032433',
            'account_name': 'NGO VAN CHIEU',
            'max_wait_time': 300
        }
        self.payment_manager = PaymentManager(self.payment_config)
        def test_sepay_async():
            if hasattr(self.payment_manager, 'test_sepay_connection'):
                if self.payment_manager.test_sepay_connection():
                    logging.info("✅ SePay API connection verified!")
                else:
                    logging.warning("⚠️ SePay API connection failed - payments may not work")
        
        threading.Thread(target=test_sepay_async, daemon=True).start()
        self.mqtt_client = mqtt.Client(client_id="XParking", clean_session=True)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
        
        self.mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)
        
        threading.Thread(target=self.start_mqtt_client, daemon=True).start()
        
        self.email_var = tk.StringVar(value="athanhphuc7102005@gmail.com")
        
        if not os.path.exists('anh'):
            os.makedirs('anh')
            
        logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(message)s')
        
        self.parking_status = {
            "slots_total": 3,
            "slots_occupied": 0,
            "current_operation": "idle",
            "is_full": False,
            "barrier_in_open": False,
            "barrier_out_open": False
        }
        
        self.mqtt_command_queue = []
        self.mqtt_queue_lock = threading.Lock()
        self.mqtt_last_publish_time = 0
        
        threading.Thread(target=self.process_mqtt_command_queue, daemon=True).start()
    
    def start_mqtt_client(self):
        while True:
            try:
                self.mqtt_client.connect("192.168.1.80", 1883, 60)
                self.mqtt_client.loop_forever()
            except Exception as e:
                logging.error(f"MQTT connection error: {e}. Retrying in 5 seconds...")
                time.sleep(5)
    
    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logging.info("Connected to MQTT Broker!")
            client.subscribe("parking/data", qos=1)
            client.subscribe("parking/alert", qos=1)
            client.publish("parking/status", "APP_CONNECTED", qos=1, retain=True)
        else:
            logging.error(f"Failed to connect to MQTT broker with result code {rc}")
    
    def on_mqtt_disconnect(self, client, userdata, rc):
        logging.warning(f"Disconnected from MQTT broker with result code {rc}")
    
    def publish_mqtt_command(self, topic, payload, qos=1, retain=False):
        with self.mqtt_queue_lock:
            self.mqtt_command_queue.append((topic, payload, qos, retain))
    
    def process_mqtt_command_queue(self):
        min_interval = 0.1
        
        while True:
            command = None
            with self.mqtt_queue_lock:
                if self.mqtt_command_queue and time.time() - self.mqtt_last_publish_time >= min_interval:
                    command = self.mqtt_command_queue.pop(0)
                    self.mqtt_last_publish_time = time.time()
            
            if command:
                topic, payload, qos, retain = command
                try:
                    self.mqtt_client.publish(topic, payload, qos=qos, retain=retain)
                    logging.debug(f"Published to {topic}: {payload}")
                except Exception as e:
                    logging.error(f"Error publishing to MQTT: {e}")
                    with self.mqtt_queue_lock:
                        self.mqtt_command_queue.insert(0, command)
            
            time.sleep(0.05)
    
    def on_mqtt_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            event = data.get('event')
            
            if not event:
                logging.warning(f"Received MQTT message without event field: {data}")
                return
                
            logging.info(f"MQTT message received: {event}")
            
            if event == "RFID_IN_SUCCESS":
                rfid = data.get('rfid')
                if rfid:
                    self.root.after(0, lambda: self.info_vars["ID Thẻ:"].set(rfid))
                    self.root.after(0, self.start_plate_recognition_in)
                    
            elif event == "RFID_OUT_SUCCESS":
                rfid = data.get('rfid')
                if rfid:
                    self.root.after(0, lambda: self.info_vars["ID Thẻ:"].set(rfid))
                    self.root.after(0, self.validate_rfid_for_exit, rfid)  # Kiểm tra RFID trước khi chụp BSX
                    
            elif event == "BARRIER_IN_OPENED":
                self.parking_status["barrier_in_open"] = True
                self.root.after(0, lambda: self.btn_barrier_in.config(
                    text="ĐÓNG BARRIER VÀO", 
                    style='Danger.TButton', 
                    state=tk.NORMAL
                ))
                
            elif event == "BARRIER_OUT_OPENED":
                self.parking_status["barrier_out_open"] = True
                self.root.after(0, lambda: self.btn_barrier_out.config(
                    text="ĐÓNG BARRIER RA", 
                    style='Danger.TButton', 
                    state=tk.NORMAL
                ))
                
            elif event == "BARRIER_IN_CLOSED":
                self.parking_status["barrier_in_open"] = False
                self.root.after(0, lambda: self.btn_barrier_in.config(
                    text="MỞ BARRIER VÀO", 
                    style='Success.TButton', 
                    state=tk.NORMAL
                ))
                
            elif event == "BARRIER_OUT_CLOSED":
                self.parking_status["barrier_out_open"] = False
                self.root.after(0, lambda: self.btn_barrier_out.config(
                    text="MỞ BARRIER RA", 
                    style='Primary.TButton', 
                    state=tk.NORMAL
                ))
                
            elif event == "ALERT":
                alert_type = data.get('type', event)
                # Gửi email cảnh báo với thông tin chi tiết
                if alert_type == "SMOKE_DETECTED":
                    smoke_value = data.get('smoke_value', 'Unknown')
                    email_subject = f"🔥 [CẢNH BÁO KHÓI] X PARKING"
                    email_body = f"""
CẢNH BÁO KHÓI PHÁT HIỆN!

Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Giá trị cảm biến: {smoke_value}
Ngưỡng cảnh báo: 900
Loại cảnh báo: {alert_type}
Trạng thái: KHẨN CẤP

Vui lòng kiểm tra hệ thống ngay lập tức!

---
Hệ thống bãi xe thông minh X PARKING
                    """
                else:
                    email_subject = f"⚠️ [CẢNH BÁO] X PARKING"
                    email_body = f"""
CẢNH BÁO HỆ THỐNG!

Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Loại cảnh báo: {alert_type}
Trạng thái: KHẨN CẤP

Vui lòng kiểm tra hệ thống ngay lập tức!

---
Hệ thống bãi xe thông minh X PARKING
                    """
                    
                self.send_email(email_subject, email_body)
                self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set(f"🚨 CẢNH BÁO: {alert_type}"))
                
            elif event == "SMOKE_SENSOR_DATA":
                # Xử lý dữ liệu cảm biến khói
                smoke_value = data.get('value', 0)
                smoke_status = data.get('status', 'NORMAL')
                threshold = data.get('threshold', 900)
                
                logging.info(f"Smoke sensor: {smoke_value} (Threshold: {threshold}) - Status: {smoke_status}")
                
                # Cập nhật giao diện với thông tin cảm biến khói
                if smoke_status == "DETECTED":
                    self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set(f"🔥 KHÓI: {smoke_value}"))
                else:
                    # Chỉ cập nhật nếu không có cảnh báo khác
                    current_status = self.info_vars["Trạng Thái:"].get()
                    if "🔥" in current_status or "🚨" not in current_status:
                        self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set("SẴN SÀNG"))
                
            elif event == "SMOKE_CLEARED":
                logging.info("Smoke cleared - returning to normal operation")
                self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set("SẴN SÀNG - Khói đã hết"))
                
                # Gửi email thông báo khói đã hết
                email_subject = "✅ [THÔNG BÁO] Khói đã hết - X PARKING"
                email_body = f"""
THÔNG BÁO: TÌNH TRẠNG KHÓI ĐÃ KẾT THÚC

Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Trạng thái: Bình thường
Cảm biến khói: Đã trở về mức an toàn

Hệ thống đã trở lại hoạt động bình thường.

---
Hệ thống bãi xe thông minh X PARKING
                """
                self.send_email(email_subject, email_body)
                
            elif event == "RFID_MISMATCH_OUT":
                self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set("❌ RFID không khớp"))
                logging.warning("RFID mismatch detected during car exit")
                
            elif event == "VEHICLE_NOT_FOUND_OUT":
                self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set("❌ Xe không tồn tại trong hệ thống"))
                logging.warning("Vehicle not found in database")

        except json.JSONDecodeError:
            logging.error(f"Failed to decode MQTT message: {msg.payload}")
        except Exception as e:
            logging.error(f"MQTT message error: {e}")

    def send_email(self, subject, body):
        def send_async():
            try:
                smtp_server = "smtp.gmail.com"
                smtp_port = 587
                sender_email = "Acc13422@gmail.com"
                sender_password = "dvdultkxshztqwth"
                recipient_email = self.email_var.get()
                
                if not recipient_email or recipient_email == "....." or "@" not in recipient_email:
                    logging.warning("No valid email recipient configured or recipient email is default placeholder.")
                    return
                    
                msg = MIMEText(body)
                msg['Subject'] = subject
                msg['From'] = sender_email
                msg['To'] = recipient_email
                
                with smtplib.SMTP(smtp_server, smtp_port) as server:
                    server.starttls()
                    server.login(sender_email, sender_password)
                    server.sendmail(sender_email, recipient_email, msg.as_string())
                    
                logging.info(f"Email sent successfully: {subject}")
            except Exception as e:
                logging.error(f"Failed to send email: {e}")
        
        threading.Thread(target=send_async, daemon=True).start()

    def validate_rfid_for_exit(self, rfid):
        logging.debug(f"Validating RFID for exit: {rfid}")
        
        # Kiểm tra kết nối database
        if not self.db or not hasattr(self.db, 'connection') or not self.db.connection:
            logging.error("Database connection is not available")
            messagebox.showerror("Lỗi kết nối", "Mất kết nối đến cơ sở dữ liệu. Vui lòng khởi động lại ứng dụng.")
            return
        
        try:
            # Tìm thông tin xe theo RFID
            vehicle_data = None
            
            # Thử sử dụng phương thức find_active_vehicle_by_rfid nếu có
            if hasattr(self.db, 'find_active_vehicle_by_rfid'):
                vehicle_data = self.db.find_active_vehicle_by_rfid(rfid.upper())
            
            # Nếu không tìm thấy hoặc phương thức không tồn tại, thử truy vấn trực tiếp
            if not vehicle_data:
                try:
                    # Đảm bảo kết nối
                    if hasattr(self.db, '_check_connection'):
                        self.db._check_connection()
                    
                    # Truy vấn trực tiếp
                    query = """
                    SELECT id, license_plate, rfid_id, entry_time 
                    FROM parking_history 
                    WHERE rfid_id = %s AND status = 'Trong bãi' 
                    ORDER BY entry_time DESC 
                    LIMIT 1
                    """
                    self.db.cursor.execute(query, (rfid.upper(),))
                    vehicle_data = self.db.cursor.fetchone()
                except Exception as e:
                    logging.error(f"Error with direct query: {e}")
            
            # Kiểm tra nếu không tìm thấy xe
            if not vehicle_data:
                logging.warning(f"RFID {rfid} not found in active vehicles")
                messagebox.showerror("Lỗi", f"RFID {rfid} chưa có thông tin xe vào trong hệ thống.")
                self.publish_mqtt_command("parking/command", "VEHICLE_NOT_FOUND_OUT")
                self.reset_info_panel()
                return
            
            # Lưu thông tin xe
            self.active_vehicle_id = vehicle_data[0]
            db_plate = vehicle_data[1]
            db_rfid = vehicle_data[2]
            entry_time_str = vehicle_data[3]
            logging.debug(f"Found vehicle for RFID {rfid}: Plate={db_plate}, Entry={entry_time_str}")
            
            # Hiển thị thông tin xe
            self.info_vars["ID Thẻ:"].set(db_rfid)
            self.info_vars["Biển Số Xe:"].set(db_plate)
            self.info_vars["Trạng Thái:"].set("Đang quét biển số ra...")
            
            # Bắt đầu quét biển số
            self.start_plate_recognition_out()
            
        except Exception as e:
            logging.error(f"Error in validate_rfid_for_exit: {e}")
            messagebox.showerror("Lỗi", "Xảy ra lỗi khi xác thực RFID. Vui lòng thử lại.")
            self.reset_info_panel()

    def start_plate_recognition_in(self):
        self.info_vars["Trạng Thái:"].set("Đang quét biển số xe vào...")
        
        frame = self.current_frame_in
        if frame is None:
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_IN")
            messagebox.showwarning("Lỗi", "Không có hình ảnh từ camera vào.")
            self.reset_info_panel()
            return
        
        threading.Thread(target=self._process_car_entry_thread, args=(frame,), daemon=True).start()

    def start_plate_recognition_out(self):
        self.info_vars["Trạng Thái:"].set("Đang quét biển số xe ra...")
        
        frame = self.current_frame_out
        if frame is None:
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_OUT")
            messagebox.showwarning("Lỗi", "Không có hình ảnh từ camera ra.")
            self.reset_info_panel()
            return
        
        threading.Thread(target=self._process_car_exit_thread, args=(frame,), daemon=True).start()
        
    def _process_car_entry_thread(self, frame):
        if self.parking_status["current_operation"] != "idle":
            logging.warning("Already processing another operation, skipping car entry LPR.")
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_IN")
            return

        self.parking_status["current_operation"] = "entry"
        self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set("Đang nhận dạng biển số vào..."))

        plate_text = self.detect_license_plate(frame, self.plate_in_canvas, self.plate_in_var)
        
        if plate_text:
            self.root.after(0, self.finalize_car_entry, plate_text, frame)
        else:
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_IN")
            self.root.after(0, lambda: messagebox.showerror("Lỗi nhận dạng", "Không nhận dạng được biển số xe. Vui lòng thử lại."))
            
        self.parking_status["current_operation"] = "idle"

    def finalize_car_entry(self, plate_text, frame):
        existing_vehicle = self.db.find_active_vehicle(plate_text.upper())
        if existing_vehicle:
            messagebox.showerror("Lỗi", f"Xe {plate_text.upper()} đã có trong bãi!")
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_IN")
            self.reset_info_panel()
            return
            
        if self.parking_status["is_full"]:
            messagebox.showerror("Bãi đầy", "Bãi xe đã đầy, không thể tiếp nhận thêm xe!")
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_IN")
            self.reset_info_panel()
            return
        
        plate_text = plate_text.upper()
        entry_time = datetime.now()
        
        image_name = f"VAO_{plate_text.replace('.', '')}_{entry_time.strftime('%Y%m%d%H%M%S')}.jpg"
        image_path = os.path.join('anh', image_name)
        try:
            cv2.imwrite(image_path, frame)
            logging.info(f"Entry image saved successfully at: {image_path}")
        except Exception as e:
            logging.error(f"Failed to save entry image: {e}")
            image_path = ""
        
        rfid = self.info_vars["ID Thẻ:"].get()
        if rfid == "....." or not rfid:
            rfid = f"RFID_{int(time.time())}"
            self.info_vars["ID Thẻ:"].set(rfid)
        
        try:
            self.active_vehicle_id = self.db.log_car_entry(plate_text, rfid, entry_time, image_path, self.current_user['name'])
            if self.active_vehicle_id is None:
                raise ValueError("log_car_entry returned None - DB insert failed")
            logging.info(f"DB entry logged successfully for vehicle {plate_text} with ID: {self.active_vehicle_id}")
        except Exception as e:
            logging.error(f"Database error during car entry: {e}")
            messagebox.showerror("Lỗi cơ sở dữ liệu", "Không thể lưu thông tin xe vào cơ sở dữ liệu! Kiểm tra kết nối MySQL hoặc log lỗi.")
            self.reset_info_panel()
            return
        
        self.info_vars["Biển Số Xe:"].set(plate_text)
        self.info_vars["Trạng Thái:"].set("Xe vào")
        self.info_vars["Thời gian:"].set(entry_time.strftime('%Y-%m-%d %H:%M:%S'))
        self.info_vars["Tổng giờ gửi:"].set("...")
        self.info_vars["Tổng Phí:"].set("...")
        self.info_vars["Trạng Thái Thanh Toán:"].set("...")
        
        self.publish_mqtt_command("parking/command", "PLATE_SCAN_SUCCESS_IN")
        
        logging.info(f"Car entry processed: {plate_text}, RFID: {rfid}")
        self.root.after(2000, self.reset_info_panel)

    def _process_car_exit_thread(self, frame):
        if self.parking_status["current_operation"] != "idle":
            logging.warning("Already processing another operation, skipping car exit LPR.")
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_OUT")
            return
        
        self.parking_status["current_operation"] = "exit"
        self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set("Đang nhận dạng biển số ra..."))

        plate_text = self.detect_license_plate(frame, self.plate_out_canvas, self.plate_out_var)
        
        if plate_text:
            self.root.after(0, self.finalize_car_exit, plate_text, frame)
        else:
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_OUT")
            self.root.after(0, lambda: messagebox.showerror("Lỗi nhận dạng", "Không nhận dạng được biển số xe. Vui lòng thử lại."))
            self.parking_status["current_operation"] = "idle"

    def finalize_car_exit(self, plate_text, frame):
        logging.debug(f"Starting finalize_car_exit for plate: {plate_text}")
        
        # Kiểm tra RFID đã được quét chưa
        current_rfid = self.info_vars["ID Thẻ:"].get()
        if not current_rfid or current_rfid == "....." or current_rfid.strip() == "":
            logging.warning("No RFID info - cancelling exit")
            messagebox.showerror("Lỗi", "Không có thông tin RFID. Vui lòng quét thẻ RFID trước.")
            self.publish_mqtt_command("parking/command", "PLATE_SCAN_FAIL_OUT")
            self.parking_status["current_operation"] = "idle"
            self.reset_info_panel()
            return
        
        # Lấy thông tin xe từ RFID
        vehicle_data = self.db.find_active_vehicle_by_rfid(current_rfid.upper())
        if not vehicle_data:
            logging.error(f"RFID {current_rfid} not found in active vehicles")
            messagebox.showerror("Lỗi", f"RFID {current_rfid} chưa có thông tin xe vào trong hệ thống.")
            self.publish_mqtt_command("parking/command", "VEHICLE_NOT_FOUND_OUT")
            self.parking_status["current_operation"] = "idle"
            self.reset_info_panel()
            return
        
        # Lấy các thông tin của xe từ database
        self.active_vehicle_id = vehicle_data[0]
        db_plate = vehicle_data[1]
        db_rfid = vehicle_data[2]
        entry_time = vehicle_data[3]

        logging.debug(f"Found vehicle data: ID={self.active_vehicle_id}, Plate={db_plate}, RFID={db_rfid}, Entry={entry_time}")

        # So sánh biển số quét được với biển số trong database
        if plate_text.upper() != db_plate.upper():
            logging.error(f"Plate mismatch: Scanned={plate_text.upper()}, DB={db_plate.upper()} for RFID {current_rfid}")
            messagebox.showerror(
                "Lỗi biển số không khớp", 
                f"Biển số quét ra: {plate_text.upper()}\n"
                f"Biển số trong hệ thống: {db_plate.upper()}\n"
                f"RFID: {current_rfid}\n\n"
                f"Biển số không khớp với xe này!\n"
                f"Vui lòng kiểm tra lại xe."
            )
            self.publish_mqtt_command("parking/command", "RFID_MISMATCH_OUT")
            self.parking_status["current_operation"] = "idle"
            self.info_vars["Biển Số Xe:"].set(".....")
            self.info_vars["Trạng Thái:"].set("SẴN SÀNG - Biển số không khớp")
            return

        # Kiểm tra entry_time có đúng kiểu datetime không
        if not isinstance(entry_time, datetime):
            logging.error(f"Unexpected entry_time type: {type(entry_time)}")
            messagebox.showerror("Lỗi", "Lỗi dữ liệu thời gian vào từ cơ sở dữ liệu. Vui lòng liên hệ admin.")
            self.parking_status["current_operation"] = "idle"
            self.reset_info_panel()
            return

        # Tính thời gian và phí
        exit_time = datetime.now()
        duration = exit_time - entry_time

        # Tính toán thời gian đỗ xe dưới dạng human-readable
        days = duration.days
        hours, remainder = divmod(duration.seconds, 3600)
        minutes, _ = divmod(remainder, 60)

        duration_str = ""
        if days > 0: duration_str += f"{days} ngày "
        if hours > 0: duration_str += f"{hours} giờ "
        duration_str += f"{minutes} phút"

        # Tính phí theo giờ (làm tròn lên)
        total_seconds_raw = duration.total_seconds()
        charged_hours = math.ceil(total_seconds_raw / 3600) if total_seconds_raw > 0 else 1
        fee = charged_hours * 10000
        logging.debug(f"Calculated fee: {fee} VNĐ for duration {duration_str}")

        # Cập nhật thông tin hiển thị
        self.info_vars["Biển Số Xe:"].set(db_plate)
        self.info_vars["ID Thẻ:"].set(db_rfid)
        self.info_vars["Trạng Thái:"].set("✓ RFID & BSX khớp - Chờ thanh toán")
        self.info_vars["Thời gian:"].set(exit_time.strftime('%Y-%m-%d %H:%M:%S'))
        self.info_vars["Tổng giờ gửi:"].set(duration_str.strip())
        self.info_vars["Tổng Phí:"].set(f"{int(fee):,} VNĐ")
        self.info_vars["Trạng Thái Thanh Toán:"].set("Chưa thanh toán")

        # Vô hiệu hóa các nút barrier khi đang trong quá trình thanh toán
        if hasattr(self, 'btn_barrier_in'):
            self.btn_barrier_in.config(state=tk.DISABLED)
        if hasattr(self, 'btn_barrier_out'):
            self.btn_barrier_out.config(state=tk.DISABLED)

        # Báo cho ESP32 biết đã quét biển số thành công, đang chờ thanh toán
        self.publish_mqtt_command("parking/command", "PLATE_SCAN_SUCCESS_OUT")

        logging.info(f"✓ RFID-BSX validated: RFID={db_rfid}, Plate={db_plate}, Fee={fee:,} VNĐ")

        # Bắt đầu quy trình thanh toán
        try:
            # Lưu hình ảnh
            image_name = f"RA_{plate_text.replace('.', '')}_{exit_time.strftime('%Y%m%d%H%M%S')}.jpg"
            image_path = os.path.join('anh', image_name)
            try:
                if frame is not None:
                    cv2.imwrite(image_path, frame)
                    logging.info(f"Exit image saved successfully at: {image_path}")
            except Exception as e:
                logging.error(f"Failed to save exit image: {e}")
                image_path = ""
            
            # Gọi hàm thanh toán
            self._start_payment_flow(plate_text, fee, exit_time, frame)
            
        except Exception as e:
            logging.error(f"Error starting payment flow: {e}")
            messagebox.showerror("Lỗi", "Không thể khởi tạo thanh toán. Vui lòng thử lại.")
            self.parking_status["current_operation"] = "idle"  # Thêm dòng này
            self.on_payment_cancel()


    def _start_payment_flow(self, plate_text, fee, exit_time, frame):
        logging.debug(f"Starting payment flow for plate {plate_text}, fee {fee}")
        
        # Tạo dữ liệu xe để truyền vào hệ thống thanh toán
        vehicle_data_dict = {
            'license_plate': plate_text,
            'hours': math.ceil(fee / 10000)
        }
        
        # Hàm callback khi thanh toán thành công (dùng chung cho cả QR và cash)
        def on_payment_success(transaction_data):
            logging.info(f"🎉 Thanh toán thành công: {transaction_data}")
            
            # Thông báo cho ESP32 biết đã thanh toán thành công
            self.publish_mqtt_command("parking/command", f"PAYMENT_SUCCESS:{fee}")
            
            # Lưu thông tin ra vào cơ sở dữ liệu
            image_name = f"RA_{plate_text.replace('.', '')}_{exit_time.strftime('%Y%m%d%H%M%S')}.jpg"
            image_path = os.path.join('anh', image_name)
            try:
                if frame is not None and not os.path.exists(image_path):
                    cv2.imwrite(image_path, frame)
                    logging.info(f"Exit image saved successfully at: {image_path}")
            except Exception as e:
                logging.error(f"Failed to save exit image: {e}")
                image_path = ""
            
            try:
                # Cập nhật thông tin xe ra vào database
                self.db.log_car_exit(self.active_vehicle_id, exit_time, fee, image_path, self.current_user['name'])
                logging.info(f"DB exit updated successfully for record ID {self.active_vehicle_id}")
                
                # Giảm số lượng xe trong bãi
                if self.parking_status["slots_occupied"] > 0:
                    self.parking_status["slots_occupied"] -= 1
            except Exception as e:
                logging.error(f"Database error during exit: {e}")
                messagebox.showerror("Lỗi cơ sở dữ liệu", "Không thể cập nhật thông tin xe ra! Kiểm tra kết nối MySQL hoặc log lỗi.")
                return
            
            # Cập nhật giao diện
            self.root.after(0, lambda: self.info_vars["Trạng Thái:"].set("Đã rời bãi"))
            self.root.after(0, lambda: self.info_vars["Trạng Thái Thanh Toán:"].set("Đã thanh toán"))
            
            # Kích hoạt lại các nút barrier
            self.root.after(0, lambda: self.btn_barrier_in.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.btn_barrier_out.config(state=tk.NORMAL))
            self.parking_status["current_operation"] = "idle"
            
            # Thông báo thành công
            payment_method = transaction_data.get('payment_method', 'QR')
            success_msg = f"Xe {plate_text} đã ra khỏi bãi.\nPhí: {fee:,} VNĐ\nPhương thức: {payment_method.upper()}"
            self.root.after(0, lambda: messagebox.showinfo("Thành công", success_msg))
            self.root.after(2000, self.reset_info_panel)
        
        # Hàm callback khi hết thời gian chờ thanh toán
        def on_payment_timeout():
            logging.warning("⌛ Thời gian chờ thanh toán hết hạn")
            self.root.after(0, lambda: messagebox.showwarning("Hết thời gian", "Thời gian chờ thanh toán đã hết. Vui lòng quét lại thẻ RFID."))
            self.publish_mqtt_command("parking/command", "PAYMENT_FAIL")
            self.root.after(0, self.on_payment_cancel)
        
        try:
            # Bắt đầu quy trình thanh toán
            payment_data = self.payment_manager.start_payment_flow(
                vehicle_data_dict, 
                fee, 
                on_payment_success,  # Callback sẽ được wrapped trong show_qr_payment
                on_payment_timeout
            )
            logging.debug(f"Payment data: {payment_data}")
            
            # Hiển thị QR thanh toán và nhận wrapped callback
            wrapped_callback = self.show_qr_payment(
                payment_data['qr_url'], 
                payment_data['session_id'], 
                payment_data['amount'], 
                payment_data['description'], 
                on_payment_success  # Callback gốc, sẽ được wrap trong show_qr_payment
            )
            
            logging.info("💳 QR Payment window opened with synchronized callbacks")
            
        except Exception as e:
            logging.error(f"Payment flow error: {e}")
            messagebox.showerror("Lỗi thanh toán", "Không thể khởi tạo thanh toán. Vui lòng quét lại thẻ RFID.")
            self.publish_mqtt_command("parking/command", "PAYMENT_FAIL")
            self.parking_status["current_operation"] = "idle"
            self.on_payment_cancel()

    def show_qr_payment(self, qr_url, session_id, amount, description, on_payment_success):
        logging.debug(f"Showing QR payment: URL={qr_url}, Amount={amount}, Desc={description}")
        
        # Tạo cửa sổ QR mới
        qr_window = tk.Toplevel(self.root)
        qr_window.title("Thanh toán qua QR")
        qr_window.geometry('400x650')
        qr_window.transient(self.root)
        qr_window.grab_set()
        qr_window.resizable(False, False)
        
        # Căn giữa cửa sổ
        qr_window.update_idletasks()
        x = (qr_window.winfo_screenwidth() // 2) - (400 // 2)
        y = (qr_window.winfo_screenheight() // 2) - (600 // 2)
        qr_window.geometry(f"400x650+{x}+{y}")
        
        # Biến để tracking trạng thái thanh toán
        payment_completed = {'status': False}
        countdown_active = {'status': True}
        
        # Tiêu đề
        title_label = ttk.Label(qr_window, text="THANH TOÁN QUA QR CODE", 
                            font=('Arial', 16, 'bold'))
        title_label.pack(pady=10)
        
        # Canvas cho mã QR
        qr_canvas = tk.Canvas(qr_window, width=300, height=300, bg='white')
        qr_canvas.pack(pady=10)
        
        # Hàm tải mã QR
        def load_qr():
            try:
                with urllib.request.urlopen(qr_url, timeout=10) as response:
                    qr_img = Image.open(response).resize((300, 300), Image.Resampling.LANCZOS)
                    qr_photo = ImageTk.PhotoImage(image=qr_img)
                    qr_canvas.create_image(0, 0, image=qr_photo, anchor='nw')
                    qr_canvas.image = qr_photo
                    logging.debug("QR image loaded successfully")
            except Exception as e:
                logging.error(f"Lỗi tải mã QR: {e}")
                qr_canvas.create_text(150, 150, text="Lỗi tải mã QR\nVui lòng thử lại", 
                                    fill="red", font=("Arial", 14), justify='center')
        
        # Tải mã QR trong thread riêng
        threading.Thread(target=load_qr, daemon=True).start()
        
        # Khung thông tin chi tiết thanh toán
        details_frame = ttk.LabelFrame(qr_window, text="Chi tiết thanh toán")
        details_frame.pack(pady=10, padx=20, fill='x')
        
        ttk.Label(details_frame, text=f"Số tiền: {amount:,} VNĐ", 
                font=('Arial', 14, 'bold')).pack(pady=5)
        ttk.Label(details_frame, text=f"Nội dung: {description}").pack(pady=5)
        
        # Đồng hồ đếm ngược
        countdown_var = tk.StringVar(value="Còn lại: 05:00")
        countdown_label = ttk.Label(qr_window, textvariable=countdown_var, 
                                font=('Arial', 12), foreground='red')
        countdown_label.pack(pady=10)
        
        # Status label để hiển thị trạng thái
        status_var = tk.StringVar(value="Đang chờ thanh toán...")
        status_label = ttk.Label(qr_window, textvariable=status_var, 
                            font=('Arial', 12, 'bold'), foreground='blue')
        status_label.pack(pady=5)
        
        # Hàm đóng cửa sổ an toàn
        def close_window_safely():
            try:
                if qr_window.winfo_exists():
                    qr_window.destroy()
                    logging.info("🔒 QR payment window closed")
            except tk.TclError:
                pass  # Window already destroyed
        
        # Wrapper cho on_payment_success để đóng cửa sổ
        original_on_success = on_payment_success
        def wrapped_on_success(transaction_data):
            if not payment_completed['status']:
                payment_completed['status'] = True
                countdown_active['status'] = False
                
                # Thêm logging chi tiết hơn
                payment_method = transaction_data.get('payment_method', 'QR')
                logging.info(f"🎉 {payment_method.upper()} Payment successful - closing window")
                
                # Cập nhật UI trước khi đóng
                try:
                    status_var.set("✅ Thanh toán thành công!")
                    countdown_var.set("Hoàn tất!")
                    qr_window.update()
                except tk.TclError:
                    pass
                
                # Đóng cửa sổ sau 1.5 giây (tăng từ 1 giây để user thấy rõ message)
                qr_window.after(1500, close_window_safely)
                
                # Gọi callback gốc
                original_on_success(transaction_data)
            else:
                logging.warning("⚠️ Payment already completed, ignoring duplicate callback")

        
        # Hàm đếm ngược
        remaining_time = 300
        def update_countdown():
            nonlocal remaining_time
            
            if not countdown_active['status'] or payment_completed['status']:
                return  # Dừng countdown nếu thanh toán thành công
                
            if remaining_time > 0:
                mins, secs = divmod(remaining_time, 60)
                countdown_var.set(f"Còn lại: {mins:02d}:{secs:02d}")
                remaining_time -= 1
                qr_window.after(1000, update_countdown)
            else:
                countdown_var.set("Hết thời gian!")
                if not payment_completed['status']:
                    close_window_safely()
                    self.on_payment_cancel()
        
        update_countdown()
        
        # Các nút điều khiển
        btn_frame = ttk.Frame(qr_window)
        btn_frame.pack(pady=20)
        
        # Xử lý thanh toán tiền mặt
        def handle_cash_payment():
            if payment_completed['status']:
                logging.warning("⚠️ Payment already completed, ignoring cash payment")
                return  # Đã thanh toán rồi
                
            result = messagebox.askyesno("Xác nhận", 
                f"Xác nhận đã thu tiền mặt {amount:,} VNĐ?")
            if result:
                payment_completed['status'] = True
                countdown_active['status'] = False
                
                # Hủy session QR payment
                self.payment_manager.cancel_payment(session_id)
                self.publish_mqtt_command("parking/command", f"PAYMENT_SUCCESS:{amount}")
                
                # Cập nhật UI ngay lập tức
                try:
                    status_var.set("✅ Thu tiền mặt thành công!")
                    countdown_var.set("Hoàn tất!")
                except tk.TclError:
                    pass
                
                # Gọi callback với dữ liệu tiền mặt
                wrapped_on_success({
                    'payment_method': 'cash',
                    'amount': amount,
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'operator': self.current_user['name']
                })
                
                logging.info("💵 Cash payment processed - QR session cancelled")
        
        # Nút thu tiền mặt
        cash_btn = ttk.Button(btn_frame, text="Thu tiền mặt", 
                command=handle_cash_payment, 
                style='Success.TButton')
        cash_btn.pack(side=tk.LEFT, padx=10)
        
        # Xử lý hủy thanh toán
        def handle_cancel():
            if payment_completed['status']:
                return  # Đã thanh toán rồi
                
            payment_completed['status'] = True
            countdown_active['status'] = False
            
            self.payment_manager.cancel_payment(session_id)
            self.publish_mqtt_command("parking/command", "PAYMENT_FAIL")
            
            close_window_safely()
            self.on_payment_cancel()
            
            logging.info("❌ Payment cancelled by user")
        
        # Nút hủy
        cancel_btn = ttk.Button(btn_frame, text="Hủy", 
                command=handle_cancel, 
                style='Danger.TButton')
        cancel_btn.pack(side=tk.LEFT, padx=10)
        
        # Thêm hướng dẫn
        guide_text = "- Quét mã QR bằng ứng dụng ngân hàng\n- Thanh toán đúng số tiền và nội dung\n- Hệ thống sẽ tự động xác nhận"
        ttk.Label(qr_window, text=guide_text, justify=tk.LEFT).pack(pady=10)
        
        # Xử lý khi đóng cửa sổ bằng X
        def on_window_close():
            handle_cancel()
        
        qr_window.protocol("WM_DELETE_WINDOW", on_window_close)
        
        # Cập nhật callback trong payment flow để sử dụng wrapped version
        # (Điều này đảm bảo khi QR thành công, cửa sổ sẽ đóng)
        return wrapped_on_success

    def on_payment_cancel(self):
        logging.info("Payment cancelled, returning to RFID scanning state")
        self.parking_status["current_operation"] = "idle"
        self.info_vars["Trạng Thái:"].set("QUÉT THẺ RFID")
        self.info_vars["Biển Số Xe:"].set(".....")
        self.info_vars["ID Thẻ:"].set(".....")
        self.info_vars["Thời gian:"].set(".....")
        self.info_vars["Tổng giờ gửi:"].set(".....")
        self.info_vars["Tổng Phí:"].set("0 VNĐ")
        self.info_vars["Trạng Thái Thanh Toán:"].set(".....")
        self.plate_out_var.set(".....")
        self.clear_plate_image(self.plate_out_canvas)
        if hasattr(self, 'btn_barrier_in'):
            self.btn_barrier_in.config(state=tk.NORMAL)
        if hasattr(self, 'btn_barrier_out'):
            self.btn_barrier_out.config(state=tk.NORMAL)
        messagebox.showinfo("Hủy thanh toán", "Thanh toán đã bị hủy. Vui lòng quét lại thẻ RFID.")

    def load_models(self, update_progress_callback=None):
        def update(value):
            if update_progress_callback:
                self.root.after(0, update_progress_callback, value)
        
        logging.info("Bắt đầu tải mô hình LPR...")
        update(20)
        
        if self.lpr_system:
            success = self.lpr_system.load_models()
            if not success:
                logging.error("Không thể tải mô hình LPR.")
                self.root.after(0, self.show_model_load_error)
        
        update(80)
        time.sleep(1)
        logging.info("Tải mô hình hoàn tất.")
        update(100)

    def show_model_load_error(self):
        if messagebox.askretrycancel("Lỗi tải mô hình", "Không thể tải mô hình LPR. Thử lại?"):
            self.load_models()

    def start(self, user_info):
        self.current_user = user_info
        self.root.title("HỆ THỐNG BÃI XE THÔNG MINH")
        self.root.geometry('1200x650')
        self.root.resizable(True, True)
        self.root.configure(bg='#dcdcdc')
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        self.setup_styles()
        self.create_main_container()
        self.show_main_screen()
        
        self.root.update_idletasks()
        self.root.deiconify()

    def create_main_container(self):
        self.main_container = ttk.Frame(self.root, style='Main.TFrame')
        self.main_container.pack(fill=tk.BOTH, expand=True)
        
        self.header_frame = ttk.Frame(self.main_container, height=60)
        self.header_frame.pack(fill=tk.X)
        self.header_frame.grid_columnconfigure(0, weight=1)
        self.header_frame.grid_columnconfigure(1, weight=1)
        self.header_frame.grid_columnconfigure(2, weight=1)
        
        self.left_header_frame = ttk.Frame(self.header_frame)
        self.left_header_frame.grid(row=0, column=0, sticky='w', padx=10)
        
        self.center_header_frame = ttk.Frame(self.header_frame)
        self.center_header_frame.grid(row=0, column=1, sticky='ew')
        
        self.right_header_frame = ttk.Frame(self.header_frame)
        self.right_header_frame.grid(row=0, column=2, sticky='e', padx=10)
        
        self.title_label = ttk.Label(self.center_header_frame, text="HỆ THỐNG BÃI XE THÔNG MINH", style='Title.TLabel')
        self.title_label.pack(pady=12)
        
        try:
            self.logo_img_pil = Image.open("logo.png").resize((100, 80), Image.Resampling.LANCZOS)
            self.logo_img = ImageTk.PhotoImage(self.logo_img_pil)
            ttk.Label(self.right_header_frame, image=self.logo_img).pack()
        except FileNotFoundError:
            ttk.Label(self.right_header_frame, text="Logo not found").pack()
        
        self.content_frame = ttk.Frame(self.main_container, style='Content.TFrame')
        self.content_frame.pack(fill=tk.BOTH, expand=True)

    def show_main_screen(self):
        self.current_screen = "main"
        self.clear_content()
        self.show_user_menu()
        self.init_cameras()
        
        cam_in_frame = ttk.LabelFrame(self.content_frame, text="CAMERA VÀO")
        cam_in_frame.place(relx=0.02, rely=0.02, relwidth=0.3, relheight=0.4)
        self.camera_in_canvas = tk.Canvas(cam_in_frame, bg='grey')
        self.camera_in_canvas.pack(fill=tk.BOTH, expand=True)
        
        cam_out_frame = ttk.LabelFrame(self.content_frame, text="CAMERA RA")
        cam_out_frame.place(relx=0.33, rely=0.02, relwidth=0.3, relheight=0.4)
        self.camera_out_canvas = tk.Canvas(cam_out_frame, bg='black')
        self.camera_out_canvas.pack(fill=tk.BOTH, expand=True)
        
        plate_in_img_frame = ttk.LabelFrame(self.content_frame, text="ẢNH CẮT BIỂN SỐ")
        plate_in_img_frame.place(relx=0.02, rely=0.45, relwidth=0.3, relheight=0.12)
        self.plate_in_canvas = tk.Canvas(plate_in_img_frame, bg='lightgrey')
        self.plate_in_canvas.pack(fill=tk.BOTH, expand=True)
        
        plate_out_img_frame = ttk.LabelFrame(self.content_frame, text="ẢNH CẮT BIỂN SỐ")
        plate_out_img_frame.place(relx=0.33, rely=0.45, relwidth=0.3, relheight=0.12)
        self.plate_out_canvas = tk.Canvas(plate_out_img_frame, bg='lightgrey')
        self.plate_out_canvas.pack(fill=tk.BOTH, expand=True)
        
        plate_in_text_frame = ttk.LabelFrame(self.content_frame, text="BIỂN SỐ PHÁT HIỆN")
        plate_in_text_frame.place(relx=0.02, rely=0.6, relwidth=0.3, relheight=0.13)
        self.plate_in_var = tk.StringVar(value=".....")
        plate_in_label = ttk.Label(plate_in_text_frame, textvariable=self.plate_in_var, font=('Arial', 22, 'bold'), foreground=self.colors['primary'], anchor='center')
        plate_in_label.pack(fill=tk.BOTH, expand=True)
        
        plate_out_text_frame = ttk.LabelFrame(self.content_frame, text="BIỂN SỐ PHÁT HIỆN")
        plate_out_text_frame.place(relx=0.33, rely=0.6, relwidth=0.3, relheight=0.13)
        self.plate_out_var = tk.StringVar(value=".....")
        plate_out_label = ttk.Label(plate_out_text_frame, textvariable=self.plate_out_var, font=('Arial', 22, 'bold'), foreground=self.colors['primary'], anchor='center')
        plate_out_label.pack(fill=tk.BOTH, expand=True)
        
        info_frame = ttk.LabelFrame(self.content_frame, text="THÔNG TIN XE")
        info_frame.place(relx=0.65, rely=0.02, relwidth=0.33, relheight=0.4)
        info_frame.columnconfigure(1, weight=1)
        
        # Đưa "Trạng Thái:" lên đầu danh sách
        labels = ["Trạng Thái:", "Biển Số Xe:", "ID Thẻ:", "Thời gian:", "Tổng giờ gửi:", "Tổng Phí:", "Trạng Thái Thanh Toán:"]
        self.info_vars = {label: tk.StringVar(value=".....") for label in labels}
        
        for i, txt in enumerate(labels):
            ttk.Label(info_frame, text=txt, style='Info.TLabel').grid(row=i, column=0, sticky='w', pady=4, padx=10)
            lbl = ttk.Label(info_frame, textvariable=self.info_vars[txt], font=('Arial', 12, 'bold'))
            lbl.grid(row=i, column=1, sticky='w', padx=10)
            
            if txt == "Tổng Phí:": 
                lbl.config(foreground=self.colors['danger'])
            if txt == "Trạng Thái Thanh Toán:": 
                lbl.config(foreground=self.colors['warning'])
            if txt == "Trạng Thái:": 
                lbl.config(foreground=self.colors['secondary'])
        
        control_frame = ttk.LabelFrame(self.content_frame, text="ĐIỀU KHIỂN")
        control_frame.place(relx=0.65, rely=0.45, relwidth=0.33, relheight=0.25)
        
        btn_container = ttk.Frame(control_frame)
        btn_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.btn_barrier_in = ttk.Button(btn_container, text="MỞ BARRIER VÀO", style='Success.TButton', command=self.toggle_barrier_in)
        self.btn_barrier_in.pack(fill=tk.X, expand=True, pady=3)
        
        self.btn_barrier_out = ttk.Button(btn_container, text="MỞ BARRIER RA", style='Primary.TButton', command=self.toggle_barrier_out)
        self.btn_barrier_out.pack(fill=tk.X, expand=True, pady=3)      
        self.reset_info_panel()
        
        if self._camera_update_id:
            self.root.after_cancel(self._camera_update_id)
        self.update_cameras()

    def toggle_barrier_in(self):
        current_text = self.btn_barrier_in.cget("text")
        
        if "MỞ" in current_text:
            self.publish_mqtt_command("parking/manual", "BARRIER_IN_OPEN")
            self.btn_barrier_in.config(text="ĐANG MỞ...", state=tk.DISABLED)
            logging.info("Manual command sent: BARRIER_IN_OPEN")
        else:
            self.publish_mqtt_command("parking/manual", "BARRIER_IN_CLOSE")
            self.btn_barrier_in.config(text="ĐANG ĐÓNG...", state=tk.DISABLED)
            logging.info("Manual command sent: BARRIER_IN_CLOSE")
        
        self.root.after(2000, lambda: self.btn_barrier_in.config(state=tk.NORMAL))

    def toggle_barrier_out(self):
        current_text = self.btn_barrier_out.cget("text")
        
        if "MỞ" in current_text:
            self.publish_mqtt_command("parking/manual", "BARRIER_OUT_OPEN")
            self.btn_barrier_out.config(text="ĐANG MỞ...", state=tk.DISABLED)
            logging.info("Manual command sent: BARRIER_OUT_OPEN")
        else:
            self.publish_mqtt_command("parking/manual", "BARRIER_OUT_CLOSE") 
            self.btn_barrier_out.config(text="ĐANG ĐÓNG...", state=tk.DISABLED)
            logging.info("Manual command sent: BARRIER_OUT_CLOSE")
        
        self.root.after(2000, lambda: self.btn_barrier_out.config(state=tk.NORMAL))

    def _on_closing(self):
        if messagebox.askokcancel("Thoát", "Bạn có chắc chắn muốn thoát ứng dụng?"):
            self.payment_manager.cleanup_expired_sessions()
            self.release_cameras()
            
            try:
                self.mqtt_client.publish("parking/status", "APP_DISCONNECTED", qos=1, retain=True)
                self.mqtt_client.disconnect()
                time.sleep(0.5)
            except Exception as e:
                logging.error(f"Error during MQTT disconnect: {e}")
                
            self.root.destroy()

    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.colors = {
            'primary': '#2E86C1', 
            'secondary': '#28B463', 
            'danger': '#E74C3C', 
            'warning': '#F39C12', 
            'dark': "#1B3249", 
            'light': '#ECF0F1', 
            'white': '#FFFFFF'
        }
        
        self.style.configure('Main.TFrame', background='#dcdcdc')
        self.style.configure('Content.TFrame', background='#dcdcdc')
        self.style.configure('TLabel', background='#dcdcdc')
        self.style.configure('TLabelframe', background='#dcdcdc')
        self.style.configure('TLabelframe.Label', background='#dcdcdc')
        self.style.configure('Title.TLabel', font=('Arial', 24, 'bold'), foreground=self.colors['dark'])
        self.style.configure('Heading.TLabel', font=('Arial', 16, 'bold'), foreground=self.colors['primary'])
        self.style.configure('Info.TLabel', font=('Arial', 11))
        
        for btn_style, color in [('Primary', 'primary'), ('Success', 'secondary'), ('Danger', 'danger')]:
            self.style.configure(
                f'{btn_style}.TButton', 
                font=('Arial', 12, 'bold'), 
                foreground='white', 
                background=self.colors[color], 
                padding=10
            )
            self.style.map(
                f'{btn_style}.TButton', 
                background=[('active', self.colors[color])]
            )

    def clear_content(self, clear_header=False):
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        
        if clear_header:
            for widget in self.left_header_frame.winfo_children():
                widget.destroy()

    def show_user_menu(self):
        for widget in self.left_header_frame.winfo_children():
            widget.destroy()
        
        if self.current_user:
            role_map = {'admin': 'Admin', 'user': 'Nhân viên'}
            user_display_text = f"[{role_map.get(self.current_user['role'], 'User')}]: {self.current_user['name']}"
            
            menu_btn = ttk.Menubutton(self.left_header_frame, text=user_display_text, style='Primary.TButton')
            menu_btn.pack(side=tk.LEFT, padx=5)
            
            menu = tk.Menu(menu_btn, tearoff=0)
            menu_btn.config(menu=menu)
            
            menu.add_command(label="TRANG CHÍNH", command=self.show_main_screen)
            menu.add_separator()
            
            if self.current_user['role'] == 'admin':
                menu.add_command(label="LỊCH SỬ XE", command=self.show_history)
                menu.add_command(label="QUẢN LÝ NHÂN SỰ", command=self.show_staff_management)
                menu.add_command(label="BÁO CÁO DOANH THU", command=self.show_revenue_report)
                menu.add_separator()
                
            menu.add_command(label="ĐĂNG XUẤT", command=self.logout)

    def logout(self):
        if messagebox.askyesno("Đăng xuất", "Bạn có chắc chắn muốn đăng xuất và thoát chương trình?"):
            self._on_closing()

    def _camera_reader_thread(self, vid_capture, frame_storage_attr, lock):
        while self.is_running:
            if vid_capture and vid_capture.isOpened():
                ret, frame = vid_capture.read()
                if ret:
                    with lock:
                        setattr(self, frame_storage_attr, frame)
                else:
                    logging.warning(f"Không thể đọc frame từ camera của {frame_storage_attr}.")
            else:
                logging.error(f"Camera cho {frame_storage_attr} không mở hoặc không tồn tại.")
            time.sleep(1/30)
        
        logging.info(f"Luồng đọc camera cho {frame_storage_attr} đã dừng.")

    def init_cameras(self):
        self.release_cameras()
        
        self.vid_in = cv2.VideoCapture(1)
        self.vid_out = cv2.VideoCapture(0)
        
        if not self.vid_in.isOpened() or not self.vid_out.isOpened():
            logging.error("Failed to open one or both cameras")
            messagebox.showwarning("Camera Error", "Không thể mở một hoặc cả hai camera. Kiểm tra kết nối của bạn.")
        
        self.is_running = True
        
        self.camera_thread_in = threading.Thread(
            target=self._camera_reader_thread, 
            args=(self.vid_in, 'latest_frame_in', self.frame_lock_in), 
            daemon=True
        )
        self.camera_thread_out = threading.Thread(
            target=self._camera_reader_thread, 
            args=(self.vid_out, 'latest_frame_out', self.frame_lock_out), 
            daemon=True
        )
        
        self.camera_thread_in.start()
        self.camera_thread_out.start()
        
        logging.info("Các luồng đọc camera đã bắt đầu.")

    def update_cameras(self):
        if self.current_screen != "main":
            return
        
        with self.frame_lock_in:
            frame_in = self.latest_frame_in.copy() if self.latest_frame_in is not None else None
            
        with self.frame_lock_out:
            frame_out = self.latest_frame_out.copy() if self.latest_frame_out is not None else None
        
        self.current_frame_in = frame_in
        self.current_frame_out = frame_out
        
        self.update_single_camera_display(self.current_frame_in, self.camera_in_canvas, 'grey')
        self.update_single_camera_display(self.current_frame_out, self.camera_out_canvas, 'black')
        
        self._camera_update_id = self.root.after(30, self.update_cameras)

    def update_single_camera_display(self, frame, canvas, error_bg_color):
        if frame is not None:
            canvas_w = canvas.winfo_width()
            canvas_h = canvas.winfo_height()
            
            if canvas_w > 1 and canvas_h > 1:
                try:
                    frame_resized = cv2.resize(frame, (canvas_w, canvas_h))
                    rgb_frame = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(rgb_frame)
                    photo = ImageTk.PhotoImage(image=img)
                    canvas.create_image(0, 0, image=photo, anchor=tk.NW)
                    canvas.image = photo
                    return
                except Exception as e:
                    logging.error(f"Lỗi khi hiển thị frame: {e}")
                    self.create_no_camera_display(canvas, "LỖI HIỂN THỊ", error_bg_color)
                    return
                
        self.create_no_camera_display(canvas, "LỖI CAMERA", error_bg_color)

    def release_cameras(self):
        self.is_running = False
        
        if self._camera_update_id:
            self.root.after_cancel(self._camera_update_id)
            self._camera_update_id = None
        
        if self.camera_thread_in and self.camera_thread_in.is_alive():
            self.camera_thread_in.join(timeout=1.0)
            
        if self.camera_thread_out and self.camera_thread_out.is_alive():
            self.camera_thread_out.join(timeout=1.0)
        
        if self.vid_in:  
            self.vid_in.release()
            
        if self.vid_out:  
            self.vid_out.release()
            
        self.vid_in, self.vid_out = None, None
        logging.info("Cameras and reader threads released.")

    def create_no_camera_display(self, canvas, message, bg_color):
        canvas_w, canvas_h = canvas.winfo_width(), canvas.winfo_height()
        canvas.delete("all")
        
        if canvas_w > 1 and canvas_h > 1:
            canvas.create_rectangle(0, 0, canvas_w, canvas_h, fill=bg_color)
            canvas.create_text(canvas_w/2, canvas_h/2, text=message, fill="white", font=("Arial", 20, "bold"))

    def detect_license_plate(self, frame, canvas, output_widget):
        if not (self.lpr_system and self.lpr_system.is_ready()):
            self.set_widget_text(output_widget, "LPR Lỗi")
            return None
        
        result = self.lpr_system.detect_and_read_plate(frame)
        
        if result['success'] and result['plates']:
            best_plate = self.lpr_system.get_best_plate(result)
            if best_plate:
                self.root.after(0, lambda: self.display_plate_image(best_plate['cropped_image'], canvas))
                self.set_widget_text(output_widget, best_plate['text'])
                return best_plate['text']
        
        self.set_widget_text(output_widget, "Không thấy")
        self.root.after(0, lambda: self.clear_plate_image(canvas))
        return None

    def set_widget_text(self, widget, text):
        def command():
            if isinstance(widget, tk.StringVar):
                widget.set(text)
            elif isinstance(widget, ttk.Entry):
                widget.delete(0, tk.END)
                widget.insert(0, text)
        self.root.after(0, command)

    def display_plate_image(self, plate_img, canvas):
        canvas.delete("all")
        canvas_w, canvas_h = canvas.winfo_width(), canvas.winfo_height()
        
        if canvas_w < 2 or canvas_h < 2:
            self.root.after(50, lambda: self.display_plate_image(plate_img, canvas))
            return
        
        plate_resized = cv2.resize(plate_img, (canvas_w, canvas_h), interpolation=cv2.INTER_AREA)
        rgb_img = cv2.cvtColor(plate_resized, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb_img)
        photo = ImageTk.PhotoImage(image=img)
        canvas.create_image(0, 0, image=photo, anchor=tk.NW)
        canvas.image = photo

    def clear_plate_image(self, canvas):
        canvas.delete("all")

    def reset_info_panel(self):
        self.active_vehicle_id = None
        
        for key in self.info_vars:
            self.info_vars[key].set(".....")
            
        if self.db.get_active_vehicles_count() == 0:
             self.parking_status["slots_occupied"] = 0
        
        if self.parking_status["slots_occupied"] == 0:
            self.info_vars["Trạng Thái:"].set("SẴN SÀNG - Bãi trống")
        elif self.parking_status["is_full"]:
            self.info_vars["Trạng Thái:"].set("BÃI FULL")
        else:
            self.info_vars["Trạng Thái:"].set(f"SẴN SÀNG - {self.parking_status['slots_occupied']}/{self.parking_status['slots_total']} xe")
        
        self.info_vars["Tổng Phí:"].set("0 VNĐ")
        self.plate_in_var.set(".....")
        self.plate_out_var.set(".....")
        
        self.clear_plate_image(self.plate_in_canvas)
        self.clear_plate_image(self.plate_out_canvas)

        if hasattr(self, 'btn_barrier_in'):
            if not self.parking_status["barrier_in_open"]:
                self.btn_barrier_in.config(text="MỞ BARRIER VÀO", style='Success.TButton', state=tk.NORMAL)
            else:
                self.btn_barrier_in.config(text="ĐÓNG BARRIER VÀO", style='Danger.TButton', state=tk.NORMAL)
            
        if hasattr(self, 'btn_barrier_out'):
            if not self.parking_status["barrier_out_open"]:
                self.btn_barrier_out.config(text="MỞ BARRIER RA", style='Primary.TButton', state=tk.NORMAL)
            else:
                self.btn_barrier_out.config(text="ĐÓNG BARRIER RA", style='Danger.TButton', state=tk.NORMAL)

    def show_history(self):
        self.current_screen = "history"
        self.clear_content()
        self.show_user_menu()
        self.release_cameras()
        
        frame = ttk.Frame(self.content_frame)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        filter_frame = ttk.LabelFrame(frame, text="Bộ lọc")
        filter_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(filter_frame, text="Biển số:").pack(side=tk.LEFT, padx=5)
        self.hist_plate_entry = ttk.Entry(filter_frame)
        self.hist_plate_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(filter_frame, text="Ngày vào:").pack(side=tk.LEFT, padx=5)
        self.hist_date_entry = DateEntry(filter_frame, date_pattern='y-mm-dd')
        self.hist_date_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(filter_frame, text="Lọc", command=self.load_history_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="Xóa Lọc", command=self.clear_history_filter).pack(side=tk.LEFT, padx=5)
        
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        cols = ('ID', 'Biển số', 'RFID', 'Vào', 'Ra', 'Phí', 'Trạng thái', 'Payment status', 'NV Vào', 'NV Ra')
        self.history_tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
        
        for col in cols:
            self.history_tree.heading(col, text=col)
            self.history_tree.column(col, stretch=tk.YES)
        
        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.history_tree.yview)
        self.history_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        ttk.Button(frame, text="Xóa mục đã chọn", style='Danger.TButton', command=self.delete_history_record).pack(pady=10)
        
        self.load_history_data()

    def load_history_data(self):
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        
        plate = self.hist_plate_entry.get()
        date_val = self.hist_date_entry.get()
        date = self.hist_date_entry.get_date().strftime('%Y-%m-%d') if date_val else None
        
        data = self.db.get_history(plate, date)
        for row in data:
            self.history_tree.insert('', tk.END, values=row)

    def clear_history_filter(self):
        self.hist_plate_entry.delete(0, tk.END)
        self.hist_date_entry.set_date(None)
        self.load_history_data()

    def delete_history_record(self):
        selected_item = self.history_tree.focus()
        if not selected_item:
            messagebox.showwarning("Cảnh báo", "Vui lòng chọn một mục để xóa.")
            return
        
        item_data = self.history_tree.item(selected_item)
        record_id = item_data['values'][0]
        
        if messagebox.askyesno("Xác nhận", f"Bạn có chắc chắn muốn xóa bản ghi ID {record_id}?"):
            self.db.delete_history(record_id)
            self.load_history_data()

    def show_staff_management(self):
        self.current_screen = "staff"
        self.clear_content()
        self.show_user_menu()
        self.release_cameras()
        
        frame = ttk.LabelFrame(self.content_frame, text="Quản lý nhân sự")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        cols = ('ID', 'Username', 'Full name', 'Role')
        self.staff_tree = ttk.Treeview(tree_frame, columns=cols, show='headings')
        
        for col in cols:
            self.staff_tree.heading(col, text=col)
            self.staff_tree.column(col, stretch=tk.YES)
        
        self.staff_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.staff_tree.yview)
        self.staff_tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=10)
        
        ttk.Button(btn_frame, text="Add", command=self.add_staff).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Edit", command=self.edit_staff).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Delete", command=self.delete_staff).pack(side=tk.LEFT, padx=5)
        
        self.load_staff_data()

    def load_staff_data(self):
        for item in self.staff_tree.get_children():
            self.staff_tree.delete(item)
            
        for user in self.db.get_users():
            self.staff_tree.insert('', tk.END, values=user)

    def _staff_dialog(self, title, record=None):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        
        ttk.Label(dialog, text="Username:").grid(row=0, column=0, padx=5, pady=5)
        user_entry = ttk.Entry(dialog)
        user_entry.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(dialog, text="Password:").grid(row=1, column=0, padx=5, pady=5)
        pass_entry = ttk.Entry(dialog, show="*")
        pass_entry.grid(row=1, column=1, padx=5, pady=5)
        
        if record:
            ttk.Label(dialog, text="(Leave blank if no change)").grid(row=1, column=2)
        
        ttk.Label(dialog, text="Full name:").grid(row=2, column=0, padx=5, pady=5)
        name_entry = ttk.Entry(dialog)
        name_entry.grid(row=2, column=1, padx=5, pady=5)
        
        ttk.Label(dialog, text="Role:").grid(row=3, column=0, padx=5, pady=5)
        role_combo = ttk.Combobox(dialog, values=['admin', 'user'], state='readonly')
        role_combo.grid(row=3, column=1, padx=5, pady=5)
        
        if record:
            user_entry.insert(0, record[1])
            name_entry.insert(0, record[2])
            role_combo.set(record[3])
        
        result = {}
        
        def on_ok():
            result['username'] = user_entry.get()
            result['password'] = pass_entry.get()
            result['full_name'] = name_entry.get()
            result['role'] = role_combo.get()
            dialog.destroy()
            
        ok_btn = ttk.Button(dialog, text="OK", command=on_ok)
        ok_btn.grid(row=4, column=0, columnspan=2, pady=10)
        
        dialog.wait_window(dialog)
        return result

    def add_staff(self):
        data = self._staff_dialog("Thêm nhân viên")
        
        if data and all(data.values()):
            if self.db.add_user(**data):
                self.load_staff_data()
            else:
                messagebox.showerror("Lỗi", "Tên đăng nhập đã tồn tại.")
        elif data:
            messagebox.showwarning("Cảnh báo", "Vui lòng điền đủ thông tin.")

    def edit_staff(self):
        selected_item = self.staff_tree.focus()
        if not selected_item:
            return
            
        record = self.staff_tree.item(selected_item)['values']
        user_id = record[0]
        
        data = self._staff_dialog("Sửa nhân viên", record)
        
        if data and data['username'] and data['full_name'] and data['role']:
            if self.db.update_user(user_id, **data):
                self.load_staff_data()
            else:
                messagebox.showerror("Lỗi", "Tên đăng nhập đã tồn tại.")
        elif data:
            messagebox.showwarning("Cảnh báo", "Tên, họ tên và vai trò không được để trống.")

    def delete_staff(self):
        selected_item = self.staff_tree.focus()
        if not selected_item:
            return
            
        record = self.staff_tree.item(selected_item)['values']
        user_id, username = record[0], record[1]
        
        if username == self.current_user['name'] or username == 'admin':
            messagebox.showerror("Lỗi", "Không thể xóa tài khoản admin hoặc chính bạn.")
            return
            
        if messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn xóa nhân viên {username}?"):
            self.db.delete_user(user_id)
            self.load_staff_data()

    def show_revenue_report(self):
        self.current_screen = "revenue"
        self.clear_content()
        self.show_user_menu()
        self.release_cameras()
        
        frame = ttk.LabelFrame(self.content_frame, text="Báo cáo doanh thu")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        filter_frame = ttk.Frame(frame)
        filter_frame.pack(pady=20)
        
        ttk.Label(filter_frame, text="Từ ngày:").pack(side=tk.LEFT, padx=5)
        self.start_date_entry = DateEntry(filter_frame, date_pattern='y-mm-dd')
        self.start_date_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(filter_frame, text="Đến ngày:").pack(side=tk.LEFT, padx=5)
        self.end_date_entry = DateEntry(filter_frame, date_pattern='y-mm-dd')
        self.end_date_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(filter_frame, text="Xem Báo Cáo", command=self.generate_revenue_report).pack(side=tk.LEFT, padx=10)
        
        self.revenue_var = tk.StringVar(value="Tổng doanh thu: 0 VNĐ")
        ttk.Label(frame, textvariable=self.revenue_var, font=('Arial', 24, 'bold'), foreground=self.colors['danger']).pack(pady=30)

    def generate_revenue_report(self):
        start_date = self.start_date_entry.get_date().strftime('%Y-%m-%d')
        end_date = self.end_date_entry.get_date().strftime('%Y-%m-%d')
        total = self.db.get_revenue_report(start_date, end_date)
        self.revenue_var.set(f"Tổng doanh thu: {total:,} VNĐ")