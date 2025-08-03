import mysql.connector
from mysql.connector import Error
import hashlib
from datetime import datetime
import logging
import sys
import time  # Thêm import time

sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(message)s')

class Database:
    def __init__(self):
        self.connection = None
        self.cursor = None
        self._connect_to_mysql()
        if self.connection:
            self._initialize_db()

    def _connect_to_mysql(self):
        try:
            # Đóng kết nối cũ nếu có
            if self.connection and hasattr(self.connection, 'close'):
                try:
                    self.connection.close()
                except:
                    pass
            
            # Thiết lập kết nối mới với timeout dài hơn
            self.connection = mysql.connector.connect(
                host='127.0.0.1',
                port=3306,
                database='SMARTPARKING',
                user='root',
                password='0000',
                charset='utf8mb4',
                collation='utf8mb4_unicode_ci',
                autocommit=False,
                pool_name='parking_pool',
                pool_size=5,
                pool_reset_session=True,
                connection_timeout=20,    # Tăng timeout kết nối
                connect_timeout=20,       # Tăng timeout kết nối TCP
                use_pure=True,            # Sử dụng Python implementation để ổn định hơn
                auth_plugin='mysql_native_password'
            )
            
            if self.connection.is_connected():
                # Đóng cursor cũ nếu có
                if self.cursor:
                    try:
                        self.cursor.close()
                    except:
                        pass
                
                # Tạo cursor mới
                self.cursor = self.connection.cursor(buffered=True)
                
                # Thiết lập session timeout dài hơn
                self.cursor.execute("SET SESSION wait_timeout=28800")  # 8 giờ
                self.cursor.execute("SET SESSION interactive_timeout=28800")  # 8 giờ
                
                db_info = self.connection.server_info
                logging.info(f"[OK] Successfully connected to MySQL Server version {db_info}")
                logging.info(f"[OK] Connected to database: {self.connection.database}")
                return True
                    
        except Error as e:
            logging.error(f"[ERROR] Error connecting to MySQL: {e}. Kiểm tra host/port/user/password/database có đúng không?")
            self.connection = None
            self.cursor = None
            return False
    def _check_connection(self):
        try:
            if self.connection is None or not self.connection.is_connected():
                logging.warning("[WARNING] MySQL connection lost. Attempting to reconnect...")
                self._connect_to_mysql()
                if self.connection and self.connection.is_connected():
                    logging.info("[OK] Successfully reconnected to MySQL")
                    return True
                else:
                    logging.error("[ERROR] Failed to reconnect to MySQL")
                    return False
            return True
        except Exception as e:
            logging.error(f"[ERROR] Error checking MySQL connection: {e}")
            try:
                self._connect_to_mysql()
                return self.connection and self.connection.is_connected()
            except:
                return False

    def execute_query(self, query, params=None):
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                if not self._check_connection():
                    retry_count += 1
                    logging.warning(f"[WARNING] Connection check failed. Retry {retry_count}/{max_retries}")
                    time.sleep(1)
                    continue
                    
                if params:
                    self.cursor.execute(query, params)
                else:
                    self.cursor.execute(query)
                    
                return True
            except Error as e:
                retry_count += 1
                logging.error(f"[ERROR] MySQL query error: {e}. Retry {retry_count}/{max_retries}")
                
                if "2013" in str(e) or "2006" in str(e) or "Lost connection" in str(e):  # Lỗi mất kết nối
                    try:
                        self._connect_to_mysql()
                    except:
                        pass
                
                if retry_count >= max_retries:
                    logging.error(f"[ERROR] Failed to execute query after {max_retries} retries")
                    raise
                
                time.sleep(1)  # Đợi 1 giây trước khi thử lại


    
    def _hash_password(self, password):
        return hashlib.sha256(password.encode('utf-8')).hexdigest()

    def _initialize_db(self):
        try:
            create_users_table = """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                full_name VARCHAR(100) NOT NULL,
                role ENUM('admin', 'user') NOT NULL DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_username (username),
                INDEX idx_role (role)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """
                
            create_parking_history_table = """
            CREATE TABLE IF NOT EXISTS parking_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                license_plate VARCHAR(20) NOT NULL,
                rfid_id VARCHAR(50),
                entry_time DATETIME NOT NULL,
                exit_time DATETIME NULL,
                fee DECIMAL(10, 2) DEFAULT 0.00,
                status ENUM('Trong bãi', 'Đã ra') NOT NULL DEFAULT 'Trong bãi',
                payment_status ENUM('Chưa thanh toán', 'Đã thanh toán') DEFAULT 'Chưa thanh toán',
                entry_image_path VARCHAR(255),
                exit_image_path VARCHAR(255),
                employee_entry VARCHAR(100),
                employee_exit VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_license_plate (license_plate),
                INDEX idx_status (status),
                INDEX idx_entry_time (entry_time),
                INDEX idx_rfid_id (rfid_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """
            
            create_daily_car_count_table = """
            CREATE TABLE IF NOT EXISTS daily_car_count (
                date DATE PRIMARY KEY,
                car_count INT NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """

            self.cursor.execute(create_users_table)
            self.cursor.execute(create_parking_history_table)
            self.cursor.execute(create_daily_car_count_table)
            
            self.cursor.execute("SELECT COUNT(*) FROM users")
            user_count = self.cursor.fetchone()[0]
            
            if user_count == 0:
                default_users = [
                    ("admin", self._hash_password("admin"), "Quản trị viên", "admin"),
                    ("user", self._hash_password("123"), "Nhân Viên A", "user")
                ]
                
                insert_users_query = """
                INSERT INTO users (username, password, full_name, role) 
                VALUES (%s, %s, %s, %s)
                """
                
                self.cursor.executemany(insert_users_query, default_users)
                logging.info("[OK] Created default users")
            
            self.connection.commit()
            logging.info("[OK] Database tables initialized successfully")
            
        except Error as e:
            logging.error(f"[ERROR] Error initializing database: {e}. Kiểm tra quyền user root hoặc database tồn tại chưa?")
            if self.connection:
                self.connection.rollback()
            raise

    def check_user(self, username, password):
        try:
            hashed_password = self._hash_password(password)
            query = """
            SELECT id, full_name, role 
            FROM users 
            WHERE username = %s AND password = %s
            """
            self.cursor.execute(query, (username, hashed_password))
            return self.cursor.fetchone()
            
        except Error as e:
            logging.error(f"[ERROR] Error checking user: {e}")
            return None

    def get_users(self):
        try:
            query = "SELECT id, username, full_name, role FROM users ORDER BY id"
            self.cursor.execute(query)
            return self.cursor.fetchall()
            
        except Error as e:
            logging.error(f"[ERROR] Error getting users: {e}")
            return []

    def add_user(self, username, password, full_name, role):
        try:
            hashed_password = self._hash_password(password)
            query = """
            INSERT INTO users (username, password, full_name, role) 
            VALUES (%s, %s, %s, %s)
            """
            self.cursor.execute(query, (username, hashed_password, full_name, role))
            self.connection.commit()
            logging.info(f"[OK] Added new user: {username}")
            return True
            
        except mysql.connector.IntegrityError:
            logging.warning(f"[WARNING] Username already exists: {username}")
            return False
        except Error as e:
            logging.error(f"[ERROR] Error adding user: {e}")
            self.connection.rollback()
            return False

    def update_user(self, user_id, username, password, full_name, role):
        try:
            if password:
                hashed_password = self._hash_password(password)
                query = """
                UPDATE users 
                SET username=%s, password=%s, full_name=%s, role=%s 
                WHERE id=%s
                """
                params = (username, hashed_password, full_name, role, user_id)
            else:
                query = """
                UPDATE users 
                SET username=%s, full_name=%s, role=%s 
                WHERE id=%s
                """
                params = (username, full_name, role, user_id)
            
            self.cursor.execute(query, params)
            self.connection.commit()
            logging.info(f"[OK] Updated user ID: {user_id}")
            return True
            
        except mysql.connector.IntegrityError:
            logging.warning(f"[WARNING] Username already exists: {username}")
            return False
        except Error as e:
            logging.error(f"[ERROR] Error updating user: {e}")
            self.connection.rollback()
            return False

    def delete_user(self, user_id):
        try:
            query = "DELETE FROM users WHERE id = %s"
            self.cursor.execute(query, (user_id,))
            self.connection.commit()
            logging.info(f"[OK] Deleted user ID: {user_id}")
            
        except Error as e:
            logging.error(f"[ERROR] Error deleting user: {e}")
            self.connection.rollback()

    def log_car_entry(self, plate, rfid, entry_time, image_path, employee_name):
        try:
            query = """
            INSERT INTO parking_history 
            (license_plate, rfid_id, entry_time, status, payment_status, entry_image_path, employee_entry) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            values = (plate, rfid, entry_time, "Trong bãi", "Chưa thanh toán", image_path, employee_name)
            logging.debug(f"Executing INSERT query with values: {values}")
            
            self.execute_query(query, values)
            self.connection.commit()
            
            car_id = self.cursor.lastrowid
            logging.info(f"[OK] Car entry logged: {plate} (ID: {car_id})")
            return car_id
            
        except Error as e:
            logging.error(f"[ERROR] Error logging car entry: {e}")
            self.connection.rollback()
            return None

    def find_active_vehicle(self, plate):
        try:
            query = """
            SELECT id, license_plate, rfid_id, entry_time 
            FROM parking_history 
            WHERE license_plate = %s AND status = 'Trong bãi' 
            ORDER BY entry_time DESC 
            LIMIT 1
            """
            self.cursor.execute(query, (plate,))
            return self.cursor.fetchone()
            
        except Error as e:
            logging.error(f"[ERROR] Error finding active vehicle: {e}")
            return None
    def find_active_vehicle_by_rfid(self, rfid):
        try:
            query = """
            SELECT id, license_plate, rfid_id, entry_time 
            FROM parking_history 
            WHERE rfid_id = %s AND status = 'Trong bãi' 
            ORDER BY entry_time DESC 
            LIMIT 1
            """
            self.execute_query(query, (rfid,))
            return self.cursor.fetchone()
            
        except Error as e:
            logging.error(f"[ERROR] Error finding active vehicle by RFID: {e}")
            return None
        
        
    def log_car_exit(self, record_id, exit_time, fee, image_path, employee_name):
        try:
            query = """
            UPDATE parking_history 
            SET exit_time=%s, fee=%s, status=%s, payment_status=%s, exit_image_path=%s, employee_exit=%s 
            WHERE id=%s
            """
            values = (exit_time, fee, "Đã ra", "Đã thanh toán", image_path, employee_name, record_id)
            logging.debug(f"Executing UPDATE query with values: {values}")
            
            self.execute_query(query, values)
            self.connection.commit()
            logging.info(f"[OK] Car exit logged: Record ID {record_id}")
            
        except Error as e:
            logging.error(f"[ERROR] Error logging car exit: {e}")
            self.connection.rollback()

    def get_history(self, plate_filter=None, date_filter=None):
        try:
            query = """
            SELECT id, license_plate, rfid_id, entry_time, exit_time, fee, 
                   status, payment_status, employee_entry, employee_exit 
            FROM parking_history
            """
            params = []
            conditions = []

            if plate_filter:
                conditions.append("license_plate LIKE %s")
                params.append(f"%{plate_filter}%")
            
            if date_filter:
                conditions.append("DATE(entry_time) = %s")
                params.append(date_filter)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY entry_time DESC"
            
            self.cursor.execute(query, params)
            return self.cursor.fetchall()
            
        except Error as e:
            logging.error(f"[ERROR] Error getting history: {e}")
            return []

    def delete_history(self, record_id):
        try:
            query = "DELETE FROM parking_history WHERE id = %s"
            self.cursor.execute(query, (record_id,))
            self.connection.commit()
            logging.info(f"[OK] Deleted history record ID: {record_id}")
            
        except Error as e:
            logging.error(f"[ERROR] Error deleting history: {e}")
            self.connection.rollback()

    def get_revenue_report(self, start_date, end_date):
        try:
            query = """
            SELECT COALESCE(SUM(fee), 0) 
            FROM parking_history 
            WHERE status = 'Đã ra' AND DATE(exit_time) BETWEEN %s AND %s
            """
            self.cursor.execute(query, (start_date, end_date))
            result = self.cursor.fetchone()
            return float(result[0]) if result[0] else 0.0
            
        except Error as e:
            logging.error(f"[ERROR] Error getting revenue report: {e}")
            return 0.0

    def log_daily_car_count(self, date, car_count):
        try:
            query = """
            INSERT INTO daily_car_count (date, car_count) 
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE 
            car_count = VALUES(car_count), 
            updated_at = CURRENT_TIMESTAMP
            """
            self.cursor.execute(query, (date, car_count))
            self.connection.commit()
            
        except Error as e:
            logging.error(f"[ERROR] Error logging daily car count: {e}")
            self.connection.rollback()

    def get_daily_car_count(self, date):
        try:
            query = "SELECT car_count FROM daily_car_count WHERE date = %s"
            self.cursor.execute(query, (date,))
            result = self.cursor.fetchone()
            return result[0] if result else 0
            
        except Error as e:
            logging.error(f"[ERROR] Error getting daily car count: {e}")
            return 0

    def get_active_vehicles_count(self):
        try:
            query = "SELECT COUNT(*) FROM parking_history WHERE status = 'Trong bãi'"
            self.cursor.execute(query)
            result = self.cursor.fetchone()
            return result[0] if result else 0
            
        except Error as e:
            logging.error(f"[ERROR] Error getting active vehicles count: {e}")
            return 0

    def test_connection(self):
        try:
            if self.connection and self.connection.is_connected():
                self.cursor.execute("SELECT 1")
                result = self.cursor.fetchone()
                logging.info("[OK] Database connection test successful")
                return True
            else:
                logging.error("[ERROR] Database connection test failed")
                return False
                
        except Error as e:
            logging.error(f"[ERROR] Database connection test error: {e}")
            return False

    def close(self):
        try:
            if self.cursor:
                self.cursor.close()
            if self.connection and self.connection.is_connected():
                self.connection.close()
                logging.info("[OK] MySQL connection closed successfully")
                
        except Error as e:
            logging.error(f"[ERROR] Error closing database connection: {e}")

    def __del__(self):
        self.close()