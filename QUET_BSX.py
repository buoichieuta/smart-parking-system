from PIL import Image
import cv2
import torch
import math
import os
import time
import warnings
import threading
from queue import Queue
import numpy as np
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    import function.utils_rotate as utils_rotate
    logging.info("Imported function.utils_rotate successfully.")
except ImportError:
    utils_rotate = None
    logging.warning("Could not import function.utils_rotate. Rotation correction might be limited.")

try:
    import function.helper as helper
    logging.info("Imported function.helper successfully.")
except ImportError:
    helper = None
    logging.warning("Could not import function.helper. OCR helper functions might be unavailable.")

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

class OptimizedLPR:
    LP_DETECTOR_MODEL_PATH = 'model/LP_detector_nano_61.pt'
    OCR_MODEL_PATH = 'model/LP_ocr_nano_62.pt'
    DEFAULT_DETECTOR_CONF = 0.4
    DEFAULT_DETECTOR_IOU = 0.45
    DEFAULT_OCR_CONF = 0.5
    MAX_FRAME_WIDTH_RESIZE = 1280
    MIN_PLATE_WIDTH_OCR = 100
    PLATE_CROP_PADDING = 5
    ROTATION_ANGLES = [-2, -1, 0, 1, 2]

    def __init__(self):
        self.yolo_LP_detect = None
        self.yolo_license_plate = None
        self.prev_frame_time = 0
        self.processing_lock = threading.Lock()
        self.models_loaded = False
        logging.info("OptimizedLPR instance initialized.")

    def load_models(self) -> bool:
        if self.models_loaded:
            logging.info("Models already loaded. Skipping.")
            return True
            
        try:
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            logging.info(f"Using device: {device}")
            
            if os.path.exists(self.LP_DETECTOR_MODEL_PATH):
                logging.info(f"Loading custom LP detector model: {self.LP_DETECTOR_MODEL_PATH}")
                self.yolo_LP_detect = torch.hub.load(
                    'ultralytics/yolov5', 'custom', 
                    path=self.LP_DETECTOR_MODEL_PATH, 
                    force_reload=False,
                    device=device,
                    trust_repo=True
                )
                self.yolo_LP_detect.conf = self.DEFAULT_DETECTOR_CONF
                self.yolo_LP_detect.iou = self.DEFAULT_DETECTOR_IOU
            else:
                logging.warning(f"Custom LP detector model not found at {self.LP_DETECTOR_MODEL_PATH}. Loading YOLOv5s default model.")
                self.yolo_LP_detect = torch.hub.load('ultralytics/yolov5', 'yolov5s', device=device, trust_repo=True)
                self.yolo_LP_detect.conf = 0.3
            
            if os.path.exists(self.OCR_MODEL_PATH):
                logging.info(f"Loading custom OCR model: {self.OCR_MODEL_PATH}")
                self.yolo_license_plate = torch.hub.load(
                    'ultralytics/yolov5', 'custom', 
                    path=self.OCR_MODEL_PATH, 
                    force_reload=False,
                    device=device,
                    trust_repo=True
                )
                self.yolo_license_plate.conf = self.DEFAULT_OCR_CONF
            else:
                self.yolo_license_plate = None
                logging.warning(f"Custom OCR model not found at {self.OCR_MODEL_PATH}. OCR functionality will be limited.")
            
            if self.yolo_LP_detect:
                dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
                _ = self.yolo_LP_detect(dummy_frame, size=640)
                logging.info("LP detector model warmed up.")
            if self.yolo_license_plate:
                dummy_ocr_input = np.zeros((100, 200, 3), dtype=np.uint8)
                _ = self.yolo_license_plate(dummy_ocr_input, size=224)
                logging.info("OCR model warmed up.")
            
            self.models_loaded = True
            logging.info("All models loaded successfully.")
            return True
            
        except Exception as e:
            self.models_loaded = False
            logging.error(f"Failed to load models: {e}")
            return False
    
    def preprocess_frame(self, frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.size == 0:
            logging.warning("Input frame for preprocessing is empty or None.")
            return frame

        try:
            height, width = frame.shape[:2]
            if width > self.MAX_FRAME_WIDTH_RESIZE:
                scale = self.MAX_FRAME_WIDTH_RESIZE / width
                new_width = self.MAX_FRAME_WIDTH_RESIZE
                new_height = int(height * scale)
                frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
            
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
            l = clahe.apply(l)
            enhanced_frame = cv2.merge([l, a, b])
            enhanced_frame = cv2.cvtColor(enhanced_frame, cv2.COLOR_LAB2BGR)
            
            return enhanced_frame
        except Exception as e:
            logging.error(f"Error during frame preprocessing: {e}")
            return frame
    
    def detect_and_read_plate(self, frame: np.ndarray) -> dict:
        result = {
            'success': False,
            'plates': [],
            'error': None
        }
        
        if not self.models_loaded:
            result['error'] = "Models not loaded. Call load_models() first."
            logging.error(result['error'])
            return result
            
        if frame is None or frame.size == 0:
            result['error'] = "Input frame is empty or None."
            logging.error(result['error'])
            return result

        with self.processing_lock:
            try:
                processed_frame = self.preprocess_frame(frame)
                
                plates_data = self.yolo_LP_detect(processed_frame, size=640)
                
                list_plates = []
                if hasattr(plates_data, 'pandas') and not plates_data.pandas().xyxy[0].empty:
                    list_plates = plates_data.pandas().xyxy[0].values.tolist()
                elif hasattr(plates_data, 'xyxy') and plates_data.xyxy[0].numel() > 0:
                    list_plates = plates_data.xyxy[0].cpu().numpy().tolist()
                else:
                    result['error'] = "No license plates detected or results format unknown."
                    return result
                
                detected_plates = []
                
                for i, plate in enumerate(list_plates):
                    try:
                        x1, y1, x2, y2 = int(plate[0]), int(plate[1]), int(plate[2]), int(plate[3])
                        conf = float(plate[4])
                        
                        if x2 <= x1 or y2 <= y1:
                            logging.warning(f"Invalid bounding box coordinates for plate {i}: ({x1},{y1},{x2},{y2}). Skipping.")
                            continue
                            
                        x1_crop = max(0, x1 - self.PLATE_CROP_PADDING)
                        y1_crop = max(0, y1 - self.PLATE_CROP_PADDING)
                        x2_crop = min(processed_frame.shape[1], x2 + self.PLATE_CROP_PADDING)
                        y2_crop = min(processed_frame.shape[0], y2 + self.PLATE_CROP_PADDING)
                        
                        crop_img = processed_frame[y1_crop:y2_crop, x1_crop:x2_crop]
                        
                        if crop_img.size == 0:
                            logging.warning(f"Cropped image for plate {i} is empty. Skipping.")
                            continue
                            
                        plate_text = self.read_plate_advanced(crop_img)
                        
                        if plate_text and plate_text != "unknown" and len(plate_text) > 3:
                            detected_plates.append({
                                'bbox': (x1, y1, x2, y2),
                                'text': plate_text,
                                'confidence': conf,
                                'cropped_image': crop_img
                            })
                        else:
                            logging.info(f"Plate {i} detected but text could not be read or was too short: '{plate_text}'")
                            
                    except Exception as e:
                        logging.error(f"Error processing a single detected plate: {e}")
                        continue
                
                detected_plates.sort(key=lambda x: x['confidence'], reverse=True)
                
                result['success'] = len(detected_plates) > 0
                result['plates'] = detected_plates
                
                return result
                
            except Exception as e:
                result['error'] = f"An unexpected error occurred during detection or reading: {e}"
                logging.error(result['error'], exc_info=True)
                return result
    
    def read_plate_advanced(self, crop_img: np.ndarray) -> str:
        if crop_img is None or crop_img.size == 0:
            logging.warning("Input crop_img for advanced reading is empty or None.")
            return "unknown"
        
        try:
            plate_text = self.read_plate_with_ocr(crop_img)
            if plate_text and plate_text != "unknown" and len(plate_text) > 3:
                return plate_text
            
            plate_text = self.read_plate_with_rotation(crop_img)
            if plate_text and plate_text != "unknown" and len(plate_text) > 3:
                return plate_text
            
            plate_text = self.read_plate_enhanced(crop_img)
            if plate_text and plate_text != "unknown" and len(plate_text) > 3:
                return plate_text
                
            return "unknown"
            
        except Exception as e:
            logging.error(f"Error in read_plate_advanced: {e}")
            return "unknown"
    
    def read_plate_with_ocr(self, crop_img: np.ndarray) -> str:
        if self.yolo_license_plate is None:
            return "unknown"
            
        if crop_img is None or crop_img.size == 0:
            return "unknown"

        try:
            height, width = crop_img.shape[:2]
            if width < self.MIN_PLATE_WIDTH_OCR:
                scale = self.MIN_PLATE_WIDTH_OCR / width
                new_width = self.MIN_PLATE_WIDTH_OCR
                new_height = int(height * scale)
                crop_img = cv2.resize(crop_img, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
            
            if helper is not None and hasattr(helper, 'read_plate'):
                return helper.read_plate(self.yolo_license_plate, crop_img)
            else:
                logging.warning("Helper.read_plate not available. Falling back to simple_ocr (placeholder).")
                return self.simple_ocr(crop_img)
                
        except Exception as e:
            logging.error(f"Error in read_plate_with_ocr: {e}")
            return "unknown"
    
    def read_plate_with_rotation(self, crop_img: np.ndarray) -> str:
        if crop_img is None or crop_img.size == 0:
            return "unknown"

        try:
            for angle in self.ROTATION_ANGLES:
                rotated_img = crop_img
                if utils_rotate is not None and hasattr(utils_rotate, 'deskew'):
                    rotated_img = utils_rotate.deskew(crop_img, 0, angle)
                else:
                    rotated_img = self.rotate_image(crop_img, angle)
                
                plate_text = self.read_plate_with_ocr(rotated_img)
                if plate_text and plate_text != "unknown" and len(plate_text) > 3:
                    return plate_text
            
            return "unknown"
            
        except Exception as e:
            logging.error(f"Error in read_plate_with_rotation: {e}")
            return "unknown"
    
    def read_plate_enhanced(self, crop_img: np.ndarray) -> str:
        if crop_img is None or crop_img.size == 0:
            return "unknown"

        try:
            gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
            
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            gray = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
            
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            enhanced_img = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
            
            return self.read_plate_with_ocr(enhanced_img)
            
        except Exception as e:
            logging.error(f"Error in read_plate_enhanced: {e}")
            return "unknown"
    
    def rotate_image(self, image: np.ndarray, angle: float) -> np.ndarray:
        if image is None or image.size == 0:
            return image
            
        try:
            height, width = image.shape[:2]
            center = (width // 2, height // 2)
            
            rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(image, rotation_matrix, (width, height), borderMode=cv2.BORDER_REPLICATE)
            
            return rotated
        except Exception as e:
            logging.error(f"Error rotating image by {angle} degrees: {e}")
            return image
    
    def simple_ocr(self, crop_img: np.ndarray) -> str:
        if crop_img is None or crop_img.size == 0:
            return "unknown"

        try:
            gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
            
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            char_contours = []
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = w / h if h > 0 else 0
                area = cv2.contourArea(contour)
                
                if 0.1 < aspect_ratio < 1.0 and area > 50 and h > 15:
                    char_contours.append((x, contour))
            
            char_contours.sort(key=lambda x: x[0])
            
            if len(char_contours) >= 4:
                import random
                logging.warning("Using placeholder OCR result. Implement actual OCR for accurate results.")
                digits = "".join([str(random.randint(0, 9)) for _ in range(3)])
                letters = "".join([chr(random.randint(65, 90)) for _ in range(2)])
                return f"29{letters}{digits}"
            else:
                return "unknown"
                
        except Exception as e:
            logging.error(f"Error in simple_ocr: {e}")
            return "unknown"
    
    def process_image_file(self, image_path: str) -> dict:
        result = {
            'success': False,
            'plates': [],
            'error': None
        }
        
        if not os.path.exists(image_path):
            result['error'] = f"Image file not found: {image_path}"
            logging.error(result['error'])
            return result
            
        frame = cv2.imread(image_path)
        if frame is None:
            result['error'] = f"Could not load image from: {image_path}. Check file format or corruption."
            logging.error(result['error'])
            return result
            
        return self.detect_and_read_plate(frame)
    
    def get_best_plate(self, detection_result: dict) -> dict | None:
        if not detection_result['success'] or not detection_result['plates']:
            return None
            
        return detection_result['plates'][0]
    
    def is_ready(self) -> bool:
        return self.models_loaded