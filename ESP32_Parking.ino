#include <SPI.h>
#include <MFRC522.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#define SS_PIN 5
#define RST_PIN 27
#define IR_IN_PIN 34
#define IR_OUT_PIN 35
#define BUZZER_PIN 15
#define SMOKE_PIN 32
#define OLED_ADDR 0x3C
const int slotPins[3] = {25, 26, 33}; 

const char* ssid = "MUOI CA PHE CN2";
const char* password = "68686868";
const char* mqtt_server = "192.168.1.80";
const int mqtt_port = 1883;
WiFiClient espClient;
PubSubClient client(espClient);
MFRC522 rfid(SS_PIN, RST_PIN);
Adafruit_SSD1306 display(128, 64, &Wire, -1);

// Global variables
bool slotsOccupied[3] = {false, false, false};
int occupiedSlots = 0;
String currentRfidTag = "";
unsigned long lastTime = 0;
unsigned long barrierOpenTime = 0;
bool smokeDetected = false;
bool emergency = false;
bool irInDetected = false;
bool irInLast = false;
bool irOutDetected = false;
bool irOutLast = false;
bool barrierInCurrentlyOpen = false;
bool barrierOutCurrentlyOpen = false;
unsigned long buzzStartTime = 0;
bool isBuzzing = false;
int buzzCount = 0;
bool buzzerState = false;

const int SMOKE_THRESHOLD = 2600;
unsigned long lastSmokeCheck = 0;
const int SMOKE_CHECK_INTERVAL = 1000;
const int BARRIER_AUTO_CLOSE_DELAY = 1000;

enum State {
  IDLE,
  SCAN_RFID_IN,
  WAIT_PLATE_IN,
  BARRIER_OPEN_IN,
  SCAN_RFID_OUT,
  WAIT_PLATE_OUT,
  WAIT_PAYMENT,
  BARRIER_OPEN_OUT,
  ERROR_STATE,
  FULL,
  ALERT
};

State currentState = IDLE;

void setup() {
  Serial.begin(115200);
  Serial2.begin(9600, SERIAL_8N1, 16, 17);
  
  Serial.print("Connecting to WiFi ");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
  Serial.println("IP Address: " + WiFi.localIP().toString());
  
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(mqttCallback);
  reconnectMQTT();

  pinMode(IR_IN_PIN, INPUT);
  pinMode(IR_OUT_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(SMOKE_PIN, INPUT);
  
  for (int i = 0; i < 3; i++) {
    pinMode(slotPins[i], INPUT);
  }

  SPI.begin();
  rfid.PCD_Init();

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println(F("SSD1306 allocation failed"));
    for (;;);
  }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  updateOLED("KHOI DONG...", "", true);
  delay(2000);
  updateOLED("X PARKING", "", true);
  currentState = IDLE;
  
  Serial.println("ESP32 initialized with smoke sensor. Smoke threshold: " + String(SMOKE_THRESHOLD));
}

void loop() {
  if (!client.connected()) reconnectMQTT();
  client.loop();

  checkSerialFromArduino();
  checkIRSensors();
  checkSmokeSensor();
  updateSlots(); // Always monitor slots
  handleBuzz();
  
  switch (currentState) {
    case IDLE: handleIdleState(); break;
    case SCAN_RFID_IN: handleScanRfidInState(); break;
    case WAIT_PLATE_IN: handleWaitPlateInState(); break;
    case BARRIER_OPEN_IN: handleBarrierOpenInState(); break;
    case SCAN_RFID_OUT: handleScanRfidOutState(); break;
    case WAIT_PLATE_OUT: handleWaitPlateOutState(); break;
    case WAIT_PAYMENT: handleWaitPaymentState(); break;
    case BARRIER_OPEN_OUT: handleBarrierOpenOutState(); break;
    case ERROR_STATE: handleErrorState(); break;
    case ALERT: handleAlertState(); break;
    case FULL: handleFullState(); break;
  }
  delay(2);
}

void checkSmokeSensor() {
  if (millis() - lastSmokeCheck >= SMOKE_CHECK_INTERVAL) {
    int smokeValue = analogRead(SMOKE_PIN);
    
    if (millis() % 5000 < SMOKE_CHECK_INTERVAL) {
      Serial.println("Smoke sensor value: " + String(smokeValue));
    }
    
    if (smokeValue > SMOKE_THRESHOLD && !smokeDetected) {
      smokeDetected = true;
      emergency = true;
      currentState = ALERT;
      Serial.println("🔥 SMOKE DETECTED! Value: " + String(smokeValue) + " (Threshold: " + String(SMOKE_THRESHOLD) + ")");
      updateOLED("CANH BAO", "KHOI PHAT HIEN", true);
      publishAlert("SMOKE_DETECTED");
      publishSmokeData(smokeValue);
      sendUnoCommand("EMERGENCY_ON");
      buzz(5);
      Serial.println("Emergency mode activated due to smoke detection");
    } else if (smokeValue <= (SMOKE_THRESHOLD - 100) && smokeDetected) {
      smokeDetected = false;
      Serial.println("🌬️ Smoke cleared. Value: " + String(smokeValue));
      if (!emergency) {
        currentState = IDLE;
        updateOLED("X PARKING", "", true);
      }
      publishEvent("SMOKE_CLEARED");
      publishSmokeData(smokeValue);
    }
    
    lastSmokeCheck = millis();
  }
}

void publishSmokeData(int smokeValue) {
  StaticJsonDocument<200> doc;
  doc["event"] = "SMOKE_SENSOR_DATA";
  doc["value"] = smokeValue;
  doc["threshold"] = SMOKE_THRESHOLD;
  doc["status"] = smokeValue > SMOKE_THRESHOLD ? "DETECTED" : "NORMAL";
  doc["timestamp"] = millis();
  
  char buffer[256];
  serializeJson(doc, buffer);
  client.publish("parking/sensor", buffer);
}

void checkSerialFromArduino() {
  while (Serial2.available()) {
    String message = Serial2.readStringUntil('\n');
    message.trim();

    Serial.println("📨 Received from UNO: " + message);

    if (message == "EMERGENCY_AUTO_ON") {
      emergency = true;
      currentState = ALERT;
      updateOLED("KHAN CAP", "AUTO", true);
      publishAlert("EMERGENCY_AUTO");
    } else if (message == "EMERGENCY_MANUAL_ON") {
      emergency = true;
      currentState = ALERT;
      updateOLED("KHAN CAP", "MANUAL", true);
      publishAlert("EMERGENCY_MANUAL");
    } else if (message == "EMERGENCY_OFF") {
      emergency = false;
      smokeDetected = false;
      if (currentState == ALERT) {
        currentState = IDLE;
        updateOLED("X PARKING", "", true);
      }
      publishEvent("EMERGENCY_OFF");
    } else if (message == "BARRIER_IN_OPENED") {
      barrierInCurrentlyOpen = true;
      barrierOpenTime = millis();
      publishEvent("BARRIER_IN_OPENED");
      Serial.println("✅ Barrier IN is now OPEN");
    } else if (message == "BARRIER_OUT_OPENED") {
      barrierOutCurrentlyOpen = true;
      barrierOpenTime = millis();
      publishEvent("BARRIER_OUT_OPENED");
      Serial.println("✅ Barrier OUT is now OPEN");
    } else if (message == "BARRIER_IN_CLOSED") {
      barrierInCurrentlyOpen = false;
      publishEvent("BARRIER_IN_CLOSED");
      Serial.println("✅ Barrier IN is now CLOSED");
      if (currentState == BARRIER_OPEN_IN) {
        currentState = IDLE;
        updateOLED("XE DA VAO", "THANH CONG", true);
        delay(2000);
        updateOLED("X PARKING", "", true);
        publishEvent("CAR_IN_COMPLETE");
        currentRfidTag = "";
      }
    } else if (message == "BARRIER_OUT_CLOSED") {
      barrierOutCurrentlyOpen = false;
      publishEvent("BARRIER_OUT_CLOSED");
      Serial.println("✅ Barrier OUT is now CLOSED");
      if (currentState == BARRIER_OPEN_OUT) {
        currentState = IDLE;
        updateOLED("XE DA RA", "THANH CONG", true);
        delay(2000);
        updateOLED("X PARKING", "", true);
        publishEvent("CAR_OUT_COMPLETE");
        currentRfidTag = "";
      }
    } else if (message.startsWith("ACK:")) {
      String ackMsg = message.substring(4);
      Serial.println("👍 UNO Acknowledged: " + ackMsg);
      publishEvent("ACK:" + ackMsg);
      if (ackMsg == "BARRIER_IN_OPENED") {
        updateOLED("BARRIER VAO", "DA MO", true);
      } else if (ackMsg == "BARRIER_OUT_OPENED") {
        updateOLED("BARRIER RA", "DA MO", true);
      }
    } else if (message.startsWith("NACK:")) {
      Serial.println("❌ UNO Negative Acknowledged: " + message.substring(5));
      publishEvent("NACK:" + message.substring(5));
    }
  }
}

void handleBuzz() {
  static unsigned long lastToggle = 0;
  if (isBuzzing && buzzCount > 0) {
    if (millis() - lastToggle >= 200) {
      buzzerState = !buzzerState;
      digitalWrite(BUZZER_PIN, buzzerState);
      lastToggle = millis();
      buzzCount--;
    }
  } else {
    digitalWrite(BUZZER_PIN, LOW);
    isBuzzing = false;
  }
}

void buzz(int times) {
  buzzCount = times * 2;
  buzzStartTime = millis();
  isBuzzing = true;
  buzzerState = false;
}

void checkIRSensors() {
  bool currentIrIn = digitalRead(IR_IN_PIN) == LOW;
  bool currentIrOut = digitalRead(IR_OUT_PIN) == LOW;

  // Handle IR IN sensor (entrance)
  if (currentIrIn != irInLast) {
    irInLast = currentIrIn;
    if (currentIrIn && !irInDetected) {
      irInDetected = true;
      Serial.println("🚗 IR_IN: Car detected");
      
      if (occupiedSlots < 3 && !emergency && currentState == IDLE) {
        updateOLED("QUET THE", "RFID", true);
        currentState = SCAN_RFID_IN;
        publishEvent("CAR_DETECT_IN");
        Serial.println("Car detected at entrance - Ready for RFID scan");
        lastTime = millis();
      } else {
        String reason = "";
        if (occupiedSlots >= 3) {
          updateOLED("BAI FULL", "KHONG THE VAO", true);
          reason = "FULL";
        } else if (emergency) {
          updateOLED("CHE DO", "KHAN CAP", true);
          reason = "EMERGENCY";
        } else {
          updateOLED("HE THONG", "DANG BAN", true);
          reason = "BUSY";
        }
        publishEvent("IGNORE_CAR_IN_" + reason);
        buzz(2);
        delay(2000);
        updateOLED("X PARKING", occupiedSlots >= 3 ? "FULL" : "", true);
      }
    } else if (!currentIrIn && irInDetected) {
      irInDetected = false;
      Serial.println("🚗 IR_IN: Car left sensor area");
      
      if (currentState == SCAN_RFID_IN || currentState == WAIT_PLATE_IN) {
        updateOLED("QUET THE", "BI HUY", true);
        buzz(1);
        delay(1500);
        currentState = IDLE;
        updateOLED("X PARKING", "", true);
        currentRfidTag = "";
        publishEvent("CAR_LEFT_IN_DURING_ENTRY");
      } else if (currentState == BARRIER_OPEN_IN && barrierInCurrentlyOpen) {
        // If car passed through barrier, wait 3 seconds then close
        Serial.println("Car has passed through entrance barrier, waiting to close");
        delay(BARRIER_AUTO_CLOSE_DELAY);
        if (!irInDetected) { // Double check car isn't back
          sendUnoCommand("CLOSE_BARRIER_IN");
        }
      }
    }
  }

  // Handle IR OUT sensor (exit)
  if (currentIrOut != irOutLast) {
    irOutLast = currentIrOut;
    if (currentIrOut && !irOutDetected) {
      irOutDetected = true;
      Serial.println("🚗 IR_OUT: Car detected at exit");
      
      if (occupiedSlots > 0 && !emergency && 
          (currentState == IDLE || currentState == FULL)) {
        updateOLED("QUET THE", "RFID", true);
        currentState = SCAN_RFID_OUT;
        publishEvent("CAR_DETECT_OUT");
        Serial.println("Car detected at EXIT - Please scan RFID card");
        lastTime = millis();
      } else if (occupiedSlots == 0) {
        updateOLED("BAI TRONG", "KHONG CO XE", true);
        delay(2000);
        updateOLED("X PARKING", "", true);
        publishEvent("EXIT_NO_CARS");
      } else if (emergency) {
        updateOLED("CHE DO", "KHAN CAP", true);
        delay(2000);
        updateOLED("X PARKING", "", true);
      }
    } else if (!currentIrOut && irOutDetected) {
      irOutDetected = false;
      Serial.println("🚗 IR_OUT: Car left exit sensor area");
      
      if (currentState == SCAN_RFID_OUT || currentState == WAIT_PLATE_OUT || currentState == WAIT_PAYMENT) {
        updateOLED("QUET THE", "BI HUY", true);
        buzz(1);
        delay(1500);
        currentState = IDLE;
        updateOLED("X PARKING", "", true);
        currentRfidTag = "";
        publishEvent("CAR_LEFT_OUT_DURING_EXIT");
      } else if (currentState == BARRIER_OPEN_OUT && barrierOutCurrentlyOpen) {
        // If car passed through exit barrier, wait 3 seconds then close
        Serial.println("Car has passed through exit barrier, waiting to close");
        delay(BARRIER_AUTO_CLOSE_DELAY);
        if (!irOutDetected) { // Double check car isn't back
          sendUnoCommand("CLOSE_BARRIER_OUT");
          occupiedSlots--; // Decrement occupied slots when car has exited
          if (occupiedSlots < 0) occupiedSlots = 0;
          publishSlotsUpdate();
        }
      }
    }
  }
}

void updateSlots() {
  bool slotChanged = false;
  int newOccupiedSlots = 0;
  
  for (int i = 0; i < 3; i++) {
    bool occupied = (digitalRead(slotPins[i]) == LOW);
    
    if (slotsOccupied[i] != occupied) {
      slotChanged = true;
      slotsOccupied[i] = occupied;
      
      if (occupied) {
        Serial.println("Car detected in slot " + String(i+1));
        newOccupiedSlots++;
        
        if (currentState == BARRIER_OPEN_IN && barrierInCurrentlyOpen) {
          sendUnoCommand("CLOSE_BARRIER_IN");
          currentState = IDLE;
          updateOLED("XE DA VAO", "SLOT " + String(i+1), true);
          delay(2000);
          updateOLED("X PARKING", "", true);
          publishEvent("CAR_PARKED_SLOT_" + String(i+1));
          currentRfidTag = "";
        }
      } else {
        Serial.println("Car removed from slot " + String(i+1));
      }
    } else if (occupied) {
      newOccupiedSlots++;
    }
  }
  
  if (newOccupiedSlots != occupiedSlots || slotChanged) {
    occupiedSlots = newOccupiedSlots;
    if (occupiedSlots < 0) occupiedSlots = 0;
    if (occupiedSlots > 3) occupiedSlots = 3;
    
    Serial.println("Occupied slots: " + String(occupiedSlots));
    publishSlotsUpdate();
    
    // Update state if parking is now full or no longer full
    if (occupiedSlots >= 3 && currentState == IDLE) {
      currentState = FULL;
      updateOLED("X PARKING", "FULL", true);
      publishEvent("PARKING_FULL");
    } else if (occupiedSlots < 3 && currentState == FULL) {
      currentState = IDLE;
      updateOLED("X PARKING", "", true);
      publishEvent("PARKING_AVAILABLE");
    }
  }
}

void publishSlotsUpdate() {
  StaticJsonDocument<300> doc;
  doc["event"] = "SLOTS_UPDATE";
  doc["occupied"] = occupiedSlots;
  doc["available"] = 3 - occupiedSlots;
  
  JsonArray activeSlots = doc.createNestedArray("occupied_slots");
  for (int i = 0; i < 3; i++) {
    if (slotsOccupied[i]) {
      activeSlots.add(i+1);
    }
  }
  
  char buffer[400];
  serializeJson(doc, buffer);
  client.publish("parking/data", buffer);
}

void handleIdleState() {
  // Check if parking is full
  if (occupiedSlots >= 3 && currentState != FULL) {
    currentState = FULL;
    updateOLED("X PARKING", "FULL", true);
    publishEvent("PARKING_FULL");
  }
}

void handleScanRfidInState() {
  if (scanRFID()) {
    updateOLED("RFID OK", currentRfidTag, true);
    publishRfid("RFID_IN_SUCCESS", currentRfidTag);
    delay(1000);
    updateOLED("DANG QUET", "BIEN SO XE", true);
    currentState = WAIT_PLATE_IN;
    lastTime = millis();
    Serial.println("RFID OK, requesting automatic plate scan IN for: " + currentRfidTag);
  } else if (millis() - lastTime > 10000) {
    currentState = ERROR_STATE;
    updateOLED("QUET RFID", "AGAIN", true);
    Serial.println("RFID scan timeout, moving to ERROR_STATE");
    lastTime = millis();
  }
}

void handleWaitPlateInState() {
  if (millis() - lastTime > 15000) {
    currentState = ERROR_STATE;
    updateOLED("BSX", "TIMEOUT", true);
    Serial.println("Plate recognition IN timeout.");
    publishEvent("PLATE_TIMEOUT_IN");
    lastTime = millis();
  }
}

void handleBarrierOpenInState() {
  if (barrierInCurrentlyOpen) {
    // Barrier is open, waiting for car to pass through
    if (!irInDetected && millis() - barrierOpenTime > BARRIER_AUTO_CLOSE_DELAY) {
      Serial.println("No car detected, auto-closing barrier IN...");
      sendUnoCommand("CLOSE_BARRIER_IN");
      currentState = IDLE;
      updateOLED("KHONG CO XE", "BARRIER DONG", true);
      delay(2000);
      updateOLED("X PARKING", "", true);
      publishEvent("NO_CAR_BARRIER_IN");
    } else if (millis() - barrierOpenTime > 30000 && irInDetected) {
      Serial.println("Car stuck at IN barrier for too long!");
      updateOLED("CANH BAO", "XE BI KET", true);
      publishAlert("CAR_STUCK_IN_BARRIER");
    }
  } else {
    if (millis() - lastTime > 10000) {
      Serial.println("Barrier IN failed to open!");
      updateOLED("LOI", "BARRIER", true);
      currentState = ERROR_STATE;
      lastTime = millis();
    }
  }
}

void handleScanRfidOutState() {
  if (scanRFID()) {
    updateOLED("RFID OK", currentRfidTag, true);
    publishRfid("RFID_OUT_SUCCESS", currentRfidTag);
    delay(1000);
    updateOLED("DANG QUET", "BIEN SO XE", true);
    currentState = WAIT_PLATE_OUT;
    lastTime = millis();
    Serial.println("RFID OK, requesting automatic plate scan OUT for: " + currentRfidTag);
  } else if (millis() - lastTime > 10000) {
    if (occupiedSlots == 0) {
      currentState = IDLE;
      updateOLED("BAI TRONG", "HUY BO", true);
      delay(2000);
      updateOLED("X PARKING", "", true);
      Serial.println("RFID scan OUT cancelled - Parking empty.");
    } else {
      currentState = ERROR_STATE;
      updateOLED("QUET RFID", "LAI", true);
      Serial.println("RFID scan OUT timeout, moving to ERROR_STATE.");
    }
    lastTime = millis();
  }
}

void handleWaitPlateOutState() {
  if (millis() - lastTime > 15000) {
    currentState = ERROR_STATE;
    updateOLED("BSX", "TIMEOUT", true);
    Serial.println("Plate recognition OUT timeout.");
    publishEvent("PLATE_TIMEOUT_OUT");
    lastTime = millis();
  }
}

void handleWaitPaymentState() {
  updateOLED("THANH TOAN", "...", true);
}

void handleBarrierOpenOutState() {
  if (barrierOutCurrentlyOpen) {
    // Wait for car to pass through
    if (!irOutDetected && millis() - barrierOpenTime > BARRIER_AUTO_CLOSE_DELAY) {
      Serial.println("Car passed through OUT barrier. Auto-closing barrier OUT...");
      sendUnoCommand("CLOSE_BARRIER_OUT");
    } else if (millis() - barrierOpenTime > 30000 && irOutDetected) {
      Serial.println("Car stuck at OUT barrier for too long!");
      updateOLED("CANH BAO", "XE BI KET", true);
      publishAlert("CAR_STUCK_OUT_BARRIER");
    }
  } else {
    if (millis() - lastTime > 10000) {
      Serial.println("Barrier OUT failed to open!");
      updateOLED("LOI", "BARRIER", true);
      currentState = ERROR_STATE;
      lastTime = millis();
    }
  }
}

void handleErrorState() {
  if (millis() - lastTime > 5000) {
    Serial.println("Recovering from ERROR state.");
    currentRfidTag = "";
    if (occupiedSlots >= 3) {
      currentState = FULL;
      updateOLED("X PARKING", "FULL", true);
      publishEvent("PARKING_FULL");
      Serial.println("Recovered to FULL state.");
    } else {
      currentState = IDLE;
      if (occupiedSlots == 0) {
        updateOLED("X PARKING", "TRONG", true);
      } else {
        updateOLED("X PARKING", String(occupiedSlots) + "/3 XE", true);
      }
      publishEvent("PARKING_AVAILABLE");
      Serial.println("Recovered to IDLE state.");
    }
  }
}

void handleAlertState() {
  if (!smokeDetected && !emergency) {
    currentState = IDLE;
    updateOLED("X PARKING", "", true);
    Serial.println("Exited ALERT state.");
  }
  static unsigned long lastAlertDisplay = 0;
  if (millis() - lastAlertDisplay > 1000) {
    if ((millis() / 500) % 2 == 0) {
      if (smokeDetected) {
        updateOLED("CANH BAO!", "KHOI PHAT HIEN!", true);
      } else {
        updateOLED("CANH BAO!", "KHAN CAP!", true);
      }
    } else {
      display.clearDisplay();
      display.display();
    }
    lastAlertDisplay = millis();
  }
}

void handleFullState() {
  if (occupiedSlots < 3) {
    currentState = IDLE;
    updateOLED("X PARKING", "", true);
    publishEvent("PARKING_AVAILABLE");
    Serial.println("Parking is no longer FULL, returning to IDLE.");
  }
  static unsigned long lastFullDisplay = 0;
  if (millis() - lastFullDisplay > 5000) {
    updateOLED("X PARKING", "FULL", true);
    lastFullDisplay = millis();
  }
}

void reconnectMQTT() {
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection...");
    if (client.connect("ESP32SmartParking")) {
      Serial.println("connected");
      client.subscribe("parking/command");
      client.subscribe("parking/manual");
      client.publish("parking/status", "ESP32_CONNECTED", true);
      StaticJsonDocument<100> doc;
      doc["event"] = "ESP32_RECONNECTED";
      char buffer[128];
      serializeJson(doc, buffer);
      client.publish("parking/data", buffer);
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 5 seconds");
      delay(5000);
    }
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.println("MQTT message received on topic " + String(topic) + ": " + message);

  if (String(topic) == "parking/command") {
    if (message == "PLATE_SCAN_SUCCESS_IN" && currentState == WAIT_PLATE_IN) {
      Serial.println("✓ Plate recognition SUCCESS for IN - Opening barrier");
      updateOLED("BSX OK", "MO BARRIER", false);
      sendUnoCommand("OPEN_BARRIER_IN");
      currentState = BARRIER_OPEN_IN;
      lastTime = millis();
    } else if (message == "PLATE_SCAN_FAIL_IN" && currentState == WAIT_PLATE_IN) {
      currentState = ERROR_STATE;
      updateOLED("BSX LOI", "THU LAI", false);
      buzz(2);
      Serial.println("✗ Plate recognition FAILED for IN");
      lastTime = millis();
    } else if (message == "PLATE_SCAN_SUCCESS_OUT" && currentState == WAIT_PLATE_OUT) {
      currentState = WAIT_PAYMENT;
      updateOLED("THANH TOAN", "QR CODE", false);
      Serial.println("✓ Plate recognition SUCCESS for OUT - Waiting payment");
    } else if (message == "PLATE_SCAN_FAIL_OUT" && currentState == WAIT_PLATE_OUT) {
      currentState = ERROR_STATE;
      updateOLED("BSX LOI", "THU LAI", false);
      Serial.println("✗ Plate recognition FAILED for OUT");
      lastTime = millis();
    } else if (message == "RFID_MISMATCH_OUT") {
      if (currentState == WAIT_PLATE_OUT) {
        currentState = ERROR_STATE;
        updateOLED("RFID SAI", "QUET LAI THE", false);
        Serial.println("❌ RFID mismatch - Card doesn't match vehicle");
        lastTime = millis();
      }
    } else if (message == "VEHICLE_NOT_FOUND_OUT") {
      if (currentState == WAIT_PLATE_OUT) {
        currentState = ERROR_STATE;
        updateOLED("XE KHONG", "TON TAI DB", false);
        Serial.println("❌ Vehicle not found in database");
        lastTime = millis();
      }
    } else if (message == "NO_CARS_TO_EXIT") {
      if (currentState == SCAN_RFID_OUT || currentState == WAIT_PLATE_OUT) {
        currentState = IDLE;
        updateOLED("KHONG CO XE", "DE RA", false);
        Serial.println("Python confirmed: No cars to exit. Resetting state.");
        delay(3000);
        updateOLED("X PARKING", "", true);
      }
    } else if (message.startsWith("PAYMENT_SUCCESS:") && currentState == WAIT_PAYMENT) {
      String fee = message.substring(16);
      currentState = BARRIER_OPEN_OUT;
      lastTime = millis();
      barrierOpenTime = millis();
      updateOLED("DA TT", fee + " VND", false);
      Serial.println("Sending OPEN_BARRIER_OUT command to Arduino UNO...");
      sendUnoCommand("OPEN_BARRIER_OUT");
      Serial.println("Payment successful: " + fee);
    } else if (message == "PAYMENT_FAIL" && currentState == WAIT_PAYMENT) {
      currentState = ERROR_STATE;
      updateOLED("THANH TOAN", "THAT BAI", false);
      Serial.println("Payment failed.");
      lastTime = millis();
    } else if (message == "PARKING_FULL_ENTRY") {
      if (currentState == SCAN_RFID_IN || currentState == WAIT_PLATE_IN) {
        currentState = FULL;
        updateOLED("BAI FULL", "KHONG THE VAO", false);
        Serial.println("Python reported parking is full for entry attempt.");
        delay(3000);
        updateOLED("X PARKING", "FULL", true);
      }
    }
  } else if (String(topic) == "parking/manual") {
    if (message == "BARRIER_IN_OPEN") {
      sendUnoCommand("OPEN_BARRIER_IN");
      Serial.println("Manual command: Open barrier IN");
    } else if (message == "BARRIER_IN_CLOSE") {
      sendUnoCommand("CLOSE_BARRIER_IN");
      Serial.println("Manual command: Close barrier IN");
    } else if (message == "BARRIER_OUT_OPEN") {
      sendUnoCommand("OPEN_BARRIER_OUT");
      Serial.println("Manual command: Open barrier OUT");
    } else if (message == "BARRIER_OUT_CLOSE") {
      sendUnoCommand("CLOSE_BARRIER_OUT");
      Serial.println("Manual command: Close barrier OUT");
    } else if (message == "EMERGENCY_ON") {
      emergency = true;
      currentState = ALERT;
      publishAlert("EMERGENCY_MANUAL");
      buzz(5);
      sendUnoCommand("EMERGENCY_ON");
      updateOLED("KHAN CAP", "MANUAL", true);
      Serial.println("Emergency mode ON (Manual from Python)");
    } else if (message == "EMERGENCY_OFF") {
      emergency = false;
      smokeDetected = false;
      currentState = IDLE;
      sendUnoCommand("EMERGENCY_OFF");
      updateOLED("X PARKING", "", true);
      Serial.println("Emergency mode OFF (from Python)");
    } else if (message == "GET_STATUS") {
      publishStatus();
      Serial.println("Status request received from Python.");
    }
  }
}

void sendUnoCommand(String command) {
  Serial2.println(command);
  Serial.println("📤 Sent to UNO: " + command);
}

void publishEvent(String event) {
  StaticJsonDocument<200> doc;
  doc["event"] = event;
  doc["timestamp"] = millis();
  char buffer[256];
  serializeJson(doc, buffer);
  client.publish("parking/data", buffer);
}

void publishRfid(String event, String rfid) {
  StaticJsonDocument<200> doc;
  doc["event"] = event;
  doc["rfid"] = rfid;
  doc["timestamp"] = millis();
  char buffer[256];
  serializeJson(doc, buffer);
  client.publish("parking/data", buffer);
}

void publishAlert(String type) {
  StaticJsonDocument<200> doc;
  doc["event"] = "ALERT";
  doc["type"] = type;
  doc["timestamp"] = millis();
  char buffer[256];
  serializeJson(doc, buffer);
  client.publish("parking/alert", buffer);
}

void publishStatus() {
  StaticJsonDocument<300> doc;
  doc["event"] = "STATUS";
  doc["state"] = String(currentState);
  doc["emergency"] = emergency;
  doc["smoke"] = smokeDetected;
  doc["occupied_slots"] = occupiedSlots;
  doc["available_slots"] = 3 - occupiedSlots;
  doc["current_rfid"] = currentRfidTag;
  doc["barrier_in_open"] = barrierInCurrentlyOpen;
  doc["barrier_out_open"] = barrierOutCurrentlyOpen;
  doc["smoke_threshold"] = SMOKE_THRESHOLD;
  char buffer[400];
  serializeJson(doc, buffer);
  client.publish("parking/status", buffer);
}

bool scanRFID() {
  if (!rfid.PICC_IsNewCardPresent()) return false;
  if (!rfid.PICC_ReadCardSerial()) return false;

  currentRfidTag = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    currentRfidTag += (rfid.uid.uidByte[i] < 0x10 ? "0" : "");
    currentRfidTag += String(rfid.uid.uidByte[i], HEX);
  }
  currentRfidTag.toUpperCase();
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
  return true;
}

void updateOLED(String line1, String line2, bool center) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  
  if (center) {
    int x1 = (128 - line1.length() * 6) / 2;
    display.setCursor(x1, 20);
    display.println(line1);
    if (line2.length() > 0) {
      int x2 = (128 - line2.length() * 6) / 2;
      display.setCursor(x2, 35);
      display.println(line2);
    }
  } else {
    display.setCursor(0, 20);
    display.println(line1);
    if (line2.length() > 0) {
      display.setCursor(0, 35);
      display.println(line2);
    }
  }
  display.display();
}