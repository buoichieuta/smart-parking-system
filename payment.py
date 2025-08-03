import requests
import urllib.parse
import threading
import time
import uuid
import json
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

class PaymentManager:
    def __init__(self, config):
        self.config = config
        self.active_sessions = {}
        self._lock = threading.Lock()

    def generate_unique_description(self, license_plate, hours):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        clean_license_plate = license_plate.replace("-", "")
        return f"BSX{clean_license_plate}{hours}H{timestamp}S{unique_id}"

    def generate_vietqr_url(self, amount, description, account_name):
        bank_id = self.config['bank_id']
        account_no = self.config['account_no']
        encoded_description = urllib.parse.quote(description)
        encoded_account_name = urllib.parse.quote(account_name)
        return f"https://img.vietqr.io/image/{bank_id}-{account_no}-print.png?amount={amount}&addInfo={encoded_description}&accountName={encoded_account_name}"

    def check_payment_status(self, amount, description):
        try:
            # Sử dụng SePay API endpoint
            url = self.config['sepay_api_url']
            
            logging.info(f"Checking payment with SePay API: {description}, amount: {amount}")
            
            # Gọi SePay API để lấy lịch sử giao dịch
            response = requests.get(url, timeout=15)
            
            logging.info(f"SePay API Response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                
                # Kiểm tra cấu trúc response
                if 'metadata' not in data:
                    logging.error("SePay API response missing 'metadata' field")
                    return False, {}
                
                transactions = data['metadata']
                logging.info(f"Found {len(transactions)} transactions from SePay API")
                
                # Tìm giao dịch khớp
                for transaction in transactions:
                    try:
                        # Kiểm tra số tiền khớp
                        transaction_amount = float(transaction.get('amount_in', '0'))
                        expected_amount = float(amount)
                        
                        # Kiểm tra nội dung giao dịch chứa description
                        transaction_content = transaction.get('transaction_content', '').upper()
                        search_description = description.upper()
                        
                        logging.debug(f"Comparing transaction: Amount={transaction_amount} vs {expected_amount}, Content contains '{search_description}': {search_description in transaction_content}")
                        
                        # Kiểm tra điều kiện khớp
                        if (transaction_amount == expected_amount and 
                            search_description in transaction_content):
                            
                            logging.info(f"✅ Payment found! Transaction ID: {transaction.get('id')}")
                            
                            # Trả về thông tin giao dịch
                            transaction_data = {
                                'transaction_id': transaction.get('id'),
                                'amount': transaction_amount,
                                'transaction_date': transaction.get('transaction_date'),
                                'transaction_content': transaction.get('transaction_content'),
                                'reference_number': transaction.get('reference_number'),
                                'bank_brand_name': transaction.get('bank_brand_name'),
                                'account_number': transaction.get('account_number')
                            }
                            
                            return True, transaction_data
                            
                    except (ValueError, TypeError) as e:
                        logging.warning(f"Error processing transaction {transaction.get('id', 'unknown')}: {e}")
                        continue
                
                logging.info("❌ No matching payment found in SePay API response")
                return False, {}
                
            else:
                logging.error(f"SePay API HTTP Error: {response.status_code} - {response.text}")
                return False, {}
                
        except requests.exceptions.Timeout:
            logging.error("SePay API request timeout")
            return False, {}
        except requests.exceptions.RequestException as e:
            logging.error(f"SePay API request error: {e}")
            return False, {}
        except json.JSONDecodeError as e:
            logging.error(f"SePay API JSON decode error: {e}")
            return False, {}
        except Exception as e:
            logging.error(f"Lỗi kiểm tra thanh toán SePay: {e}")
            return False, {}

    def _payment_check_thread(self, session_id, amount, description, on_success, on_timeout):
        waited = 0
        check_interval = 3  # 3 giây (tăng từ 2 giây để giảm tải API)
        max_wait = self.config.get('max_wait_time', 300)

        logging.info(f"Starting SePay payment check for session: {session_id}")
        
        while session_id in self.active_sessions and waited < max_wait:
            found, transaction_data = self.check_payment_status(amount, description)
            
            if found:
                logging.info(f"✅ SePay payment found for session: {session_id}")
                with self._lock:
                    if session_id in self.active_sessions:
                        del self.active_sessions[session_id]
                on_success(transaction_data)
                return
            
            logging.info(f"⏳ SePay payment not found yet. Waited: {waited}s/{max_wait}s")
            time.sleep(check_interval)
            waited += check_interval

        # Timeout
        if waited >= max_wait:
            logging.info(f"⌛ SePay payment timeout for session: {session_id}")
            with self._lock:
                if session_id in self.active_sessions:
                    del self.active_sessions[session_id]
            on_timeout()

    def start_payment_flow(self, vehicle_data, total_fee, on_success, on_timeout):
        license_plate = vehicle_data.get('license_plate', '')
        hours = vehicle_data.get('hours', 0)
        description = self.generate_unique_description(license_plate, hours)
        qr_url = self.generate_vietqr_url(total_fee, description, self.config['account_name'])

        session_id = str(uuid.uuid4())

        logging.info(f"🚀 Starting SePay payment flow: {description}")

        with self._lock:
            self.active_sessions[session_id] = {
                'description': description,
                'amount': total_fee,
                'vehicle_data': vehicle_data,
                'start_time': datetime.now()  # Thêm start_time để track session
            }

        thread = threading.Thread(
            target=self._payment_check_thread,
            args=(session_id, total_fee, description, on_success, on_timeout),
            daemon=True
        )
        thread.start()

        return {
            'session_id': session_id,
            'qr_url': qr_url,
            'description': description,
            'amount': total_fee
        }

    def cancel_payment(self, session_id):
        with self._lock:
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
                logging.info(f"❌ SePay payment cancelled: {session_id}")
                return True
        return False

    def get_active_sessions(self):
        with self._lock:
            return list(self.active_sessions.keys())

    def cleanup_expired_sessions(self):
        current_time = datetime.now()
        expired_sessions = []

        with self._lock:
            for session_id, session_data in self.active_sessions.items():
                session_start = session_data.get('start_time', datetime.now())
                if (current_time - session_start).seconds > self.config.get('max_wait_time', 300):
                    expired_sessions.append(session_id)

            for session_id in expired_sessions:
                del self.active_sessions[session_id]
                logging.info(f"🧹 Cleaned up expired SePay session: {session_id}")

        return len(expired_sessions)

    