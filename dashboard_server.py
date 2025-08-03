from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit
import threading
import time
import json
import os
import base64
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt
import mysql.connector
from mysql.connector import Error
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'smart_parking_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

class DashboardServer:
    def __init__(self, db_connection):
        self.db = db_connection
        self.mqtt_client = None
        self.mqtt_connected = False
        self.recent_activities = []
        self.parking_stats = {
            'current_vehicles': 0,
            'today_total': 0,
            'today_revenue': 0,
            'occupied_slots': 0,
            'available_slots': 3,
            'barrier_in_open': False,  
            'barrier_out_open': False
        }
        self.setup_mqtt()
        self.update_stats_timer()
        
    def setup_mqtt(self):
        def mqtt_thread():
            while True:
                try:
                    self.mqtt_client = mqtt.Client(client_id="dashboard_client", clean_session=True)
                    self.mqtt_client.on_connect = self.on_mqtt_connect
                    self.mqtt_client.on_message = self.on_mqtt_message
                    self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
                    
                    logging.info("Attempting MQTT connection to 192.168.1.138:1883")
                    self.mqtt_client.connect("192.168.1.80", 1883, 60)
                    self.mqtt_client.loop_forever()
                except Exception as e:
                    logging.error(f"MQTT connection error: {e}")
                    time.sleep(5)
                    
        threading.Thread(target=mqtt_thread, daemon=True).start()
    
    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.mqtt_connected = True
            logging.info("✅ Dashboard connected to MQTT broker")
            client.subscribe("parking/data", qos=1)
            client.subscribe("parking/alert", qos=1) 
            client.subscribe("parking/status", qos=1)
            client.subscribe("parking/sensor", qos=1)
            self.add_activity("MQTT", "Dashboard kết nối MQTT thành công")
            socketio.emit('mqtt_status', {'connected': True})
        else:
            self.mqtt_connected = False
            logging.error(f"MQTT connection failed with code {rc}")
            
    def on_mqtt_disconnect(self, client, userdata, rc):
        self.mqtt_connected = False
        logging.warning("MQTT disconnected")
        socketio.emit('mqtt_status', {'connected': False})
    
    def on_mqtt_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode()
            logging.info(f"📨 MQTT received: {topic} = {payload}")
            
            try:
                data = json.loads(payload)
                self.handle_mqtt_data(data, topic)
            except json.JSONDecodeError:
                self.add_activity("MQTT", f"Raw message: {payload}")
                
        except Exception as e:
            logging.error(f"MQTT message error: {e}")
    
    def handle_mqtt_data(self, data, topic):
        event = data.get('event', '')
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        if event == "CAR_DETECT_IN":
            self.add_activity("Sensor", "🚗 Phát hiện xe vào")
            
        elif event == "RFID_IN_SUCCESS":
            rfid = data.get('rfid', 'Unknown')
            self.add_activity("RFID", f"🏷️ Quét thẻ vào: {rfid}")
            
        elif event == "RFID_OUT_SUCCESS":
            rfid = data.get('rfid', 'Unknown')
            self.add_activity("RFID", f"🏷️ Quét thẻ ra: {rfid}")
            
        elif event == "BARRIER_IN_OPENED":
            self.parking_stats['barrier_in_open'] = True  
            self.add_activity("Barrier", "🚪 Barrier vào đã mở")
            # Phát sự kiện riêng cho barrier
            socketio.emit('barrier_event', {
                'event': event, 
                'barrier': 'in', 
                'state': 'open',
                'timestamp': timestamp
            })
            
        elif event == "BARRIER_OUT_OPENED":
            self.parking_stats['barrier_out_open'] = True  
            self.add_activity("Barrier", "🚪 Barrier ra đã mở")
            # Phát sự kiện riêng cho barrier
            socketio.emit('barrier_event', {
                'event': event, 
                'barrier': 'out', 
                'state': 'open',
                'timestamp': timestamp
            })
            
        elif event == "BARRIER_IN_CLOSED":
            self.parking_stats['barrier_in_open'] = False  
            self.add_activity("Barrier", "🚪 Barrier vào đã đóng")
            # Phát sự kiện riêng cho barrier
            socketio.emit('barrier_event', {
                'event': event, 
                'barrier': 'in', 
                'state': 'closed',
                'timestamp': timestamp
            })
            
        elif event == "BARRIER_OUT_CLOSED":
            self.parking_stats['barrier_out_open'] = False 
            self.add_activity("Barrier", "🚪 Barrier ra đã đóng")
            # Phát sự kiện riêng cho barrier
            socketio.emit('barrier_event', {
                'event': event, 
                'barrier': 'out', 
                'state': 'closed',
                'timestamp': timestamp
            })
        
        elif event == "SLOTS_UPDATE":
            occupied = data.get('occupied', 0)
            self.parking_stats['occupied_slots'] = occupied
            self.parking_stats['available_slots'] = 3 - occupied
            self.add_activity("Parking", f"🅿️ Slots cập nhật: {occupied}/3 xe")
            
        elif event == "SMOKE_DETECTED":
            smoke_value = data.get('smoke_value', 'Unknown')
            self.add_activity("Alert", f"🔥 Phát hiện khói: {smoke_value}")
            
        elif event == "SMOKE_CLEARED":
            self.add_activity("Alert", "✅ Khói đã hết")
            
        elif event == "RFID_MISMATCH_OUT":
            self.add_activity("Error", "❌ RFID không khớp khi ra")
            
        elif event == "VEHICLE_NOT_FOUND_OUT":
            self.add_activity("Error", "❌ Xe không tồn tại trong hệ thống")
        
        # Cập nhật stats và broadcast
        self.update_parking_stats()
        self.broadcast_updates()
    
    def add_activity(self, category, message):
        activity = {
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'category': category,
            'message': message
        }
        self.recent_activities.insert(0, activity)
        if len(self.recent_activities) > 100:
            self.recent_activities = self.recent_activities[:100]
        
        socketio.emit('new_activity', activity)
    
    def update_parking_stats(self):
        try:
            if not self.db or not self.db.connection:
                return
                
            current_vehicles = self.db.get_active_vehicles_count()
            
            today = datetime.now().date()
            today_revenue = self.db.get_revenue_report(today, today)
            
            query = """
            SELECT COUNT(*) FROM parking_history 
            WHERE DATE(entry_time) = %s
            """
            self.db.cursor.execute(query, (today,))
            today_total = self.db.cursor.fetchone()[0] or 0
            
            self.parking_stats.update({
                'current_vehicles': current_vehicles,
                'today_total': today_total,
                'today_revenue': today_revenue,
                'occupied_slots': current_vehicles,
                'available_slots': 3 - current_vehicles
            })
            
        except Exception as e:
            logging.error(f"Error updating parking stats: {e}")
    
    def update_stats_timer(self):
        def timer_update():
            while True:
                self.update_parking_stats()
                self.broadcast_updates()
                time.sleep(10)
        
        threading.Thread(target=timer_update, daemon=True).start()
    
    def broadcast_updates(self):
        socketio.emit('dashboard_update', {
            'stats': self.parking_stats,
            'activities': self.recent_activities[:20],
            'mqtt_connected': self.mqtt_connected,
            'timestamp': datetime.now().isoformat()
        })
    
    def send_mqtt_command(self, topic, payload):
        """
        Gửi lệnh MQTT với retry mechanism
        """
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            if self.mqtt_client and self.mqtt_connected:
                try:
                    result = self.mqtt_client.publish(topic, payload, qos=1)
                    if result.rc == mqtt.MQTT_ERR_SUCCESS:
                        logging.info(f"📤 MQTT sent (attempt {attempt + 1}): {topic} = {payload}")
                        return True
                    else:
                        logging.warning(f"MQTT publish failed on attempt {attempt + 1}: {result.rc}")
                except Exception as e:
                    logging.error(f"MQTT send error on attempt {attempt + 1}: {e}")
            else:
                logging.error(f"MQTT not connected on attempt {attempt + 1}")
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
        
        logging.error(f"Failed to send MQTT command after {max_retries} attempts")
        return False

dashboard_server = None

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/health')
def health():
    return jsonify({
        'status': 'ok', 
        'timestamp': datetime.now().isoformat(),
        'mqtt_connected': dashboard_server.mqtt_connected if dashboard_server else False,
        'db_connected': bool(dashboard_server and dashboard_server.db and dashboard_server.db.connection)
    })

@app.route('/api/stats')
def get_stats():
    if dashboard_server:
        dashboard_server.update_parking_stats()
        return jsonify({
            **dashboard_server.parking_stats,
            'mqtt_connected': dashboard_server.mqtt_connected,
            'timestamp': datetime.now().isoformat()
        })
    return jsonify({'error': 'Server not initialized'}), 500

@app.route('/api/activities')
def get_activities():
    if dashboard_server:
        return jsonify(dashboard_server.recent_activities[:50])
    return jsonify([])

@app.route('/api/history')
def get_history():
    if not dashboard_server or not dashboard_server.db:
        return jsonify([])
    
    plate_filter = request.args.get('plate')
    date_filter = request.args.get('date')
    
    try:
        history = dashboard_server.db.get_history(plate_filter, date_filter)
        result = []
        
        for record in history:
            item = {
                'id': record[0],
                'license_plate': record[1],
                'rfid_id': record[2],
                'entry_time': record[3].strftime('%Y-%m-%d %H:%M:%S') if record[3] else '',
                'exit_time': record[4].strftime('%Y-%m-%d %H:%M:%S') if record[4] else '',
                'fee': record[5] if record[5] else 0,
                'status': record[6],
                'payment_status': record[7],
                'employee_entry': record[8],
                'employee_exit': record[9],
                'has_entry_image': False,
                'has_exit_image': False,
                'entry_image': '',
                'exit_image': ''
            }
            
            # Kiểm tra hình ảnh
            if os.path.exists('anh'):
                for filename in os.listdir('anh'):
                    if record[1] and record[1].replace('.', '') in filename:
                        if 'VAO_' in filename:
                            item['has_entry_image'] = True
                            item['entry_image'] = filename
                        elif 'RA_' in filename:
                            item['has_exit_image'] = True
                            item['exit_image'] = filename
            
            result.append(item)
        
        return jsonify(result)
    except Exception as e:
        logging.error(f"Error getting history: {e}")
        return jsonify([])

@app.route('/api/image/<filename>')
def get_image(filename):
    try:
        return send_from_directory('anh', filename)
    except:
        return '', 404

@app.route('/api/chart/hourly')
def get_hourly_chart():
    if not dashboard_server or not dashboard_server.db:
        return jsonify({'labels': [], 'data': []})
    
    try:
        today = datetime.now().date()
        labels = [f"{i:02d}:00" for i in range(24)]
        data = [0] * 24
        
        query = """
        SELECT HOUR(entry_time) as hour, COUNT(*) as count 
        FROM parking_history 
        WHERE DATE(entry_time) = %s 
        GROUP BY HOUR(entry_time)
        """
        dashboard_server.db.cursor.execute(query, (today,))
        results = dashboard_server.db.cursor.fetchall()
        
        for hour, count in results:
            if 0 <= hour < 24:
                data[hour] = count
        
        return jsonify({'labels': labels, 'data': data})
    except Exception as e:
        logging.error(f"Error getting hourly chart: {e}")
        return jsonify({'labels': [], 'data': []})

@app.route('/api/chart/revenue')
def get_revenue_chart():
    if not dashboard_server or not dashboard_server.db:
        return jsonify({'labels': [], 'data': []})
    
    try:
        labels = []
        data = []
        
        for i in range(7):
            date = datetime.now().date() - timedelta(days=6-i)
            revenue = dashboard_server.db.get_revenue_report(date, date)
            labels.append(date.strftime('%m/%d'))
            data.append(revenue)
        
        return jsonify({'labels': labels, 'data': data})
    except Exception as e:
        logging.error(f"Error getting revenue chart: {e}")
        return jsonify({'labels': [], 'data': []})

@app.route('/api/control/barrier_in', methods=['POST'])
def control_barrier_in():
    """
    Điều khiển barrier vào - Hỗ trợ cả mở và đóng
    """
    try:
        data = request.json or {}
        action = data.get('action', 'open').upper()
        
        # Xác định lệnh MQTT
        if action == 'OPEN':
            mqtt_command = "BARRIER_IN_OPEN"
            expected_response = "BARRIER_IN_OPENED"
            action_text = "mở"
        elif action == 'CLOSE':
            mqtt_command = "BARRIER_IN_CLOSE"
            expected_response = "BARRIER_IN_CLOSED"
            action_text = "đóng"
        else:
            return jsonify({'success': False, 'message': 'Action không hợp lệ. Chỉ chấp nhận "open" hoặc "close"'}), 400
        
        # Kiểm tra kết nối MQTT
        if not dashboard_server:
            return jsonify({'success': False, 'message': 'Dashboard server chưa khởi tạo'}), 500
            
        if not dashboard_server.mqtt_client or not dashboard_server.mqtt_connected:
            return jsonify({'success': False, 'message': 'MQTT không kết nối'}), 503
        
        # Gửi lệnh MQTT
        success = dashboard_server.send_mqtt_command("parking/manual", mqtt_command)
        
        if success:
            # Log activity
            dashboard_server.add_activity("Control", f"🎛️ Lệnh {action_text} barrier vào từ dashboard")
            
            # Cập nhật trạng thái dự kiến (sẽ được xác nhận bởi MQTT response)
            if action == 'OPEN':
                dashboard_server.parking_stats['barrier_in_open'] = True
            else:
                dashboard_server.parking_stats['barrier_in_open'] = False
            
            # Broadcast update
            dashboard_server.broadcast_updates()
            
            return jsonify({
                'success': True, 
                'message': f'Lệnh {action_text} barrier vào đã gửi thành công',
                'command': mqtt_command,
                'expected_response': expected_response,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False, 
                'message': f'Không thể gửi lệnh {action_text} barrier vào. Kiểm tra kết nối MQTT.'
            }), 500
            
    except Exception as e:
        logging.error(f"Control barrier in error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi hệ thống: {str(e)}'}), 500

@app.route('/api/control/barrier_out', methods=['POST'])
def control_barrier_out():
    """
    Điều khiển barrier ra - Hỗ trợ cả mở và đóng
    """
    try:
        data = request.json or {}
        action = data.get('action', 'open').upper()
        
        # Xác định lệnh MQTT
        if action == 'OPEN':
            mqtt_command = "BARRIER_OUT_OPEN"
            expected_response = "BARRIER_OUT_OPENED"
            action_text = "mở"
        elif action == 'CLOSE':
            mqtt_command = "BARRIER_OUT_CLOSE"
            expected_response = "BARRIER_OUT_CLOSED"
            action_text = "đóng"
        else:
            return jsonify({'success': False, 'message': 'Action không hợp lệ. Chỉ chấp nhận "open" hoặc "close"'}), 400
        
        # Kiểm tra kết nối MQTT
        if not dashboard_server:
            return jsonify({'success': False, 'message': 'Dashboard server chưa khởi tạo'}), 500
            
        if not dashboard_server.mqtt_client or not dashboard_server.mqtt_connected:
            return jsonify({'success': False, 'message': 'MQTT không kết nối'}), 503
        
        # Gửi lệnh MQTT
        success = dashboard_server.send_mqtt_command("parking/manual", mqtt_command)
        
        if success:
            # Log activity
            dashboard_server.add_activity("Control", f"🎛️ Lệnh {action_text} barrier ra từ dashboard")
            
            # Cập nhật trạng thái dự kiến (sẽ được xác nhận bởi MQTT response)
            if action == 'OPEN':
                dashboard_server.parking_stats['barrier_out_open'] = True
            else:
                dashboard_server.parking_stats['barrier_out_open'] = False
            
            # Broadcast update
            dashboard_server.broadcast_updates()
            
            return jsonify({
                'success': True, 
                'message': f'Lệnh {action_text} barrier ra đã gửi thành công',
                'command': mqtt_command,
                'expected_response': expected_response,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False, 
                'message': f'Không thể gửi lệnh {action_text} barrier ra. Kiểm tra kết nối MQTT.'
            }), 500
            
    except Exception as e:
        logging.error(f"Control barrier out error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi hệ thống: {str(e)}'}), 500

@app.route('/api/control/emergency', methods=['POST'])
def control_emergency():
    """
    Kích hoạt chế độ khẩn cấp
    """
    try:
        if not dashboard_server:
            return jsonify({'success': False, 'message': 'Dashboard server chưa khởi tạo'}), 500
            
        if not dashboard_server.mqtt_client or not dashboard_server.mqtt_connected:
            return jsonify({'success': False, 'message': 'MQTT không kết nối'}), 503
        
        # Gửi lệnh khẩn cấp
        success = dashboard_server.send_mqtt_command("parking/manual", "EMERGENCY_ON")
        
        if success:
            dashboard_server.add_activity("Control", "🚨 Kích hoạt chế độ khẩn cấp từ dashboard")
            
            # Cập nhật trạng thái - mở tất cả barrier trong trường hợp khẩn cấp
            dashboard_server.parking_stats['barrier_in_open'] = True
            dashboard_server.parking_stats['barrier_out_open'] = True
            dashboard_server.broadcast_updates()
            
            return jsonify({
                'success': True, 
                'message': 'Chế độ khẩn cấp đã được kích hoạt',
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False, 
                'message': 'Không thể kích hoạt chế độ khẩn cấp. Kiểm tra kết nối MQTT.'
            }), 500
            
    except Exception as e:
        logging.error(f"Control emergency error: {e}")
        return jsonify({'success': False, 'message': f'Lỗi hệ thống: {str(e)}'}), 500

@app.route('/api/barrier/status')
def get_barrier_status():
    """
    Lấy trạng thái hiện tại của barrier
    """
    if dashboard_server:
        return jsonify({
            'barrier_in_open': dashboard_server.parking_stats.get('barrier_in_open', False),
            'barrier_out_open': dashboard_server.parking_stats.get('barrier_out_open', False),
            'mqtt_connected': dashboard_server.mqtt_connected,
            'current_vehicles': dashboard_server.parking_stats.get('current_vehicles', 0),
            'available_slots': dashboard_server.parking_stats.get('available_slots', 3),
            'timestamp': datetime.now().isoformat()
        })
    return jsonify({'error': 'Server not initialized'}), 500

@socketio.on('connect')
def handle_connect():
    logging.info("Client connected to dashboard")
    if dashboard_server:
        dashboard_server.update_parking_stats()
        emit('dashboard_update', {
            'stats': dashboard_server.parking_stats,
            'activities': dashboard_server.recent_activities[:20],
            'mqtt_connected': dashboard_server.mqtt_connected,
            'timestamp': datetime.now().isoformat()
        })

@socketio.on('disconnect')
def handle_disconnect():
    logging.info("Client disconnected from dashboard")

@socketio.on('request_update')
def handle_request_update():
    if dashboard_server:
        dashboard_server.update_parking_stats()
        dashboard_server.broadcast_updates()

@socketio.on('manual_barrier_control')
def handle_manual_barrier_control(data):
    """
    Xử lý điều khiển barrier từ client qua SocketIO
    """
    try:
        barrier_type = data.get('barrier')  # 'in' or 'out'
        action = data.get('action')  # 'open' or 'close'
        
        if not dashboard_server or not dashboard_server.mqtt_connected:
            emit('barrier_control_response', {
                'success': False,
                'message': 'MQTT không kết nối'
            })
            return
        
        if barrier_type == 'in':
            if action == 'open':
                mqtt_command = "BARRIER_IN_OPEN"
            elif action == 'close':
                mqtt_command = "BARRIER_IN_CLOSE"
            else:
                emit('barrier_control_response', {'success': False, 'message': 'Action không hợp lệ'})
                return
        elif barrier_type == 'out':
            if action == 'open':
                mqtt_command = "BARRIER_OUT_OPEN"
            elif action == 'close':
                mqtt_command = "BARRIER_OUT_CLOSE"
            else:
                emit('barrier_control_response', {'success': False, 'message': 'Action không hợp lệ'})
                return
        else:
            emit('barrier_control_response', {'success': False, 'message': 'Barrier type không hợp lệ'})
            return
        
        # Gửi lệnh MQTT
        success = dashboard_server.send_mqtt_command("parking/manual", mqtt_command)
        
        if success:
            dashboard_server.add_activity("Control", f"🎛️ Lệnh {action} barrier {barrier_type} từ client")
            emit('barrier_control_response', {
                'success': True,
                'message': f'Lệnh {action} barrier {barrier_type} đã gửi',
                'command': mqtt_command
            })
        else:
            emit('barrier_control_response', {
                'success': False,
                'message': 'Không thể gửi lệnh MQTT'
            })
            
    except Exception as e:
        logging.error(f"Manual barrier control error: {e}")
        emit('barrier_control_response', {
            'success': False,
            'message': f'Lỗi: {str(e)}'
        })

def create_dashboard_server(db_connection):
    global dashboard_server
    dashboard_server = DashboardServer(db_connection)
    return dashboard_server

def run_dashboard_server(host='0.0.0.0', port=5000):
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)