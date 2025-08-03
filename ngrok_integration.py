import requests
import json
import logging
from datetime import datetime

class NgrokIntegration:
    def __init__(self, ngrok_url):
        self.ngrok_url = ngrok_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'ngrok-skip-browser-warning': 'true',
            'Content-Type': 'application/json'
        })
    
    def test_connection(self):
        try:
            response = self.session.get(f"{self.ngrok_url}/health", timeout=5)
            return response.status_code == 200
        except Exception as e:
            logging.error(f"Ngrok connection test failed: {e}")
            return False
    
    def get_parking_status(self):
        try:
            response = self.session.get(f"{self.ngrok_url}/api/parking/status", timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logging.error(f"Error getting parking status: {e}")
        return None
    
    def get_history(self, plate_filter=None, date_filter=None):
        try:
            params = {}
            if plate_filter:
                params['plate'] = plate_filter
            if date_filter:
                params['date'] = date_filter
            
            response = self.session.get(f"{self.ngrok_url}/api/parking/history", 
                                      params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logging.error(f"Error getting history: {e}")
        return []
    
    def send_control_command(self, command, data=None):
        try:
            payload = {'command': command}
            if data:
                payload.update(data)
            
            response = self.session.post(f"{self.ngrok_url}/api/parking/control", 
                                       json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logging.error(f"Error sending control command: {e}")
            return False
    
    def get_analytics_data(self):
        try:
            response = self.session.get(f"{self.ngrok_url}/api/parking/analytics", timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logging.error(f"Error getting analytics: {e}")
        return None
    
    def upload_image(self, image_path, metadata):
        try:
            with open(image_path, 'rb') as f:
                files = {'image': f}
                data = {'metadata': json.dumps(metadata)}
                response = self.session.post(f"{self.ngrok_url}/api/parking/upload", 
                                           files=files, data=data, timeout=30)
                return response.status_code == 200
        except Exception as e:
            logging.error(f"Error uploading image: {e}")
            return False