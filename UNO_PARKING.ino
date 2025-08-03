#include <Servo.h>
#include <SoftwareSerial.h>

#define ESP_RX_PIN 12
#define ESP_TX_PIN 13
#define SERVO_IN_PIN 9
#define SERVO_OUT_PIN 10
#define BTN_IN_PIN 2
#define BTN_OUT_PIN 3
#define BTN_EMER_PIN 4

Servo servoIn;
Servo servoOut;

bool btnInPressed = false;
bool btnOutPressed = false;
bool btnEmerPressed = false;
bool barrierInOpen = false;
bool barrierOutOpen = false;
bool emergencyMode = false;

const int BARRIER_CLOSED_ANGLE = 0;
const int BARRIER_OPENED_ANGLE = 90;
SoftwareSerial espSerial(ESP_RX_PIN, ESP_TX_PIN);

void setup() {
  Serial.begin(9600);
  espSerial.begin(9600);
  
  servoIn.attach(SERVO_IN_PIN);
  servoOut.attach(SERVO_OUT_PIN);
  
  pinMode(BTN_IN_PIN, INPUT_PULLUP);
  pinMode(BTN_OUT_PIN, INPUT_PULLUP);
  pinMode(BTN_EMER_PIN, INPUT_PULLUP);
  
  closeBarrierIn();
  closeBarrierOut();
  
  Serial.println("UNO_READY - Smoke sensor removed, ESP32 handles smoke detection");
}

void loop() {
  checkButtons();
  checkSerialCommands();
}

void checkButtons() {
  bool currentBtnIn = digitalRead(BTN_IN_PIN) == LOW;
  bool currentBtnOut = digitalRead(BTN_OUT_PIN) == LOW;
  bool currentBtnEmer = digitalRead(BTN_EMER_PIN) == LOW;

  if (currentBtnIn && !btnInPressed) {
    btnInPressed = true;
    if (!emergencyMode) {
      toggleBarrierIn();
    } else {
      Serial.println("BARRIER_IN_BUTTON_BLOCKED_EMERGENCY");
    }
    delay(200);
  } else if (!currentBtnIn) {
    btnInPressed = false;
  }

  if (currentBtnOut && !btnOutPressed) {
    btnOutPressed = true;
    if (!emergencyMode) {
      toggleBarrierOut();
    } else {
      Serial.println("BARRIER_OUT_BUTTON_BLOCKED_EMERGENCY");
    }
    delay(200);
  } else if (!currentBtnOut) {
    btnOutPressed = false;
  }

  if (currentBtnEmer && !btnEmerPressed) {
    btnEmerPressed = true;
    emergencyMode = !emergencyMode;

    if (emergencyMode) {
      Serial.println("EMERGENCY_MANUAL_ON");
      espSerial.println("EMERGENCY_MANUAL_ON");
      activateEmergencySequence();
    } else {
      Serial.println("EMERGENCY_OFF");
      espSerial.println("EMERGENCY_OFF");
      closeBarrierIn();
      closeBarrierOut();
    }
    delay(200);
  } else if (!currentBtnEmer) {
    btnEmerPressed = false;
  }
}

void activateEmergencySequence() {
  Serial.println("EMERGENCY_SEQUENCE_ACTIVATED");
  openBarrierIn();
  delay(300);
  openBarrierOut();
  Serial.println("BARRIERS_OPENED_FOR_EMERGENCY");
}

void checkSerialCommands() {
  if (espSerial.available()) {
    String command = espSerial.readStringUntil('\n');
    command.trim();

    Serial.println("Command received from ESP32: " + command);

    if (command == "OPEN_BARRIER_IN") {
      openBarrierIn();
      espSerial.println("ACK:BARRIER_IN_OPENED");
      Serial.println("Barrier IN opened via ESP32 command");
    } else if (command == "CLOSE_BARRIER_IN") {
      closeBarrierIn();
      espSerial.println("ACK:BARRIER_IN_CLOSED");
      Serial.println("Barrier IN closed via ESP32 command");
    } else if (command == "OPEN_BARRIER_OUT") {
      openBarrierOut();
      espSerial.println("ACK:BARRIER_OUT_OPENED");
      Serial.println("Barrier OUT opened via ESP32 command");
    } else if (command == "CLOSE_BARRIER_OUT") {
      closeBarrierOut();
      espSerial.println("ACK:BARRIER_OUT_CLOSED");
      Serial.println("Barrier OUT closed via ESP32 command");
    } else if (command == "EMERGENCY_ON") {
      emergencyMode = true;
      activateEmergencySequence();
      espSerial.println("ACK:EMERGENCY_ON_OK");
      Serial.println("Emergency mode activated via ESP32 command");
    } else if (command == "EMERGENCY_OFF") {
      emergencyMode = false;
      closeBarrierIn();
      closeBarrierOut();
      Serial.println("EMERGENCY_OFF");
      espSerial.println("EMERGENCY_OFF");
      espSerial.println("ACK:EMERGENCY_OFF_OK");
      Serial.println("Emergency mode deactivated via ESP32 command");
    } else if (command == "GET_STATUS") {
      espSerial.print("STATUS:EMERGENCY:");
      espSerial.print(emergencyMode ? "ON" : "OFF");
      espSerial.print(":BARRIER_IN:");
      espSerial.print(barrierInOpen ? "OPEN" : "CLOSED");
      espSerial.print(":BARRIER_OUT:");
      espSerial.println(barrierOutOpen ? "OPEN" : "CLOSED");
      Serial.println("Status sent to ESP32");
    } else {
      espSerial.println("NACK:UNKNOWN_COMMAND");
      Serial.println("Unknown command: " + command);
    }
  }
}

void toggleBarrierIn() {
  if (barrierInOpen) {
    closeBarrierIn();
  } else {
    openBarrierIn();
  }
}

void toggleBarrierOut() {
  if (barrierOutOpen) {
    closeBarrierOut();
  } else {
    openBarrierOut();
  }
}

void openBarrierIn() {
  if (!emergencyMode || (emergencyMode && !barrierInOpen)) {
    servoIn.write(BARRIER_OPENED_ANGLE);
    if (!barrierInOpen) {
      barrierInOpen = true;
      Serial.println("BARRIER_IN_OPENED");
      espSerial.println("BARRIER_IN_OPENED");
    }
  } else {
    Serial.println("BARRIER_IN_OPEN_BLOCKED_EMERGENCY");
  }
}

void closeBarrierIn() {
  if (!emergencyMode) {
    servoIn.write(BARRIER_CLOSED_ANGLE);
    if (barrierInOpen) {
      barrierInOpen = false;
      Serial.println("BARRIER_IN_CLOSED");
      espSerial.println("BARRIER_IN_CLOSED");
    }
  } else {
    Serial.println("BARRIER_IN_CLOSE_BLOCKED_EMERGENCY");
  }
}

void openBarrierOut() {
  if (!emergencyMode || (emergencyMode && !barrierOutOpen)) {
    servoOut.write(BARRIER_OPENED_ANGLE);
    if (!barrierOutOpen) {
      barrierOutOpen = true;
      Serial.println("BARRIER_OUT_OPENED");
      espSerial.println("BARRIER_OUT_OPENED");
    }
  } else {
    Serial.println("BARRIER_OUT_OPEN_BLOCKED_EMERGENCY");
  }
}

void closeBarrierOut() {
  if (!emergencyMode) {
    servoOut.write(BARRIER_CLOSED_ANGLE);
    if (barrierOutOpen) {
      barrierOutOpen = false;
      Serial.println("BARRIER_OUT_CLOSED");
      espSerial.println("BARRIER_OUT_CLOSED");
    }
  } else {
    Serial.println("BARRIER_OUT_CLOSE_BLOCKED_EMERGENCY");
  }
}