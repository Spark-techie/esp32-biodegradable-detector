/*
  ESP32 MG90S Micro Servo Controller for Biodegradable Sorter
  ============================================================
  Hardware: ONLY ESP32 + MG90S Micro Servo Motor (NO motor drivers needed!)
  
  Wiring:
  - MG90S Red wire    (VCC)    -> ESP32 5V (or VIN)
  - MG90S Brown/Black (GND)    -> ESP32 GND
  - MG90S Orange wire (Signal) -> ESP32 GPIO 18
  
  Behavior:
  - Operates the servo motor ONE TIME ONCE when BIODEGRADABLE is detected.
  - Rotates from REST position (0 deg) to ACTIVE position (90 deg) to drop/sort,
    holds for 1 second, and returns back to REST position (0 deg).
  - Edge-triggered: Does NOT repeat or jitter while the same item stays in front of the camera.
  - Auto-rearms as soon as the item leaves or scene changes.
  - Auto-turns off PWM signal while idle so the MG90S never buzzes, twitches, or overheats!
  
  Zero Extra Libraries Required:
  Uses ESP32's built-in hardware PWM (LEDC) compatible with ESP32 board packages 2.x and 3.x.
  
  Existing camera/esp32_camera.ino is NOT modified.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>

// ======================== CONFIGURATION ========================
// 1. Wi-Fi Settings (matching your camera / network)
const char* WIFI_SSID = "S";
const char* WIFI_PASS = "jawaharm";

// 2. Python Detector Backend Server URL (your PC's current local IP)
const char* BACKEND_URL = "http://10.235.152.48:8000/api/result";

// 3. Servo Pin and Angles
const int SERVO_PIN          = 18;    // MG90S signal wire (Orange/Yellow)
const int SERVO_REST_ANGLE   = 0;     // Normal resting / closed flap position (0 degrees)
const int SERVO_ACTIVE_ANGLE = 90;    // Open / sort position (90 degrees, adjust 0-180 if needed)
const int SERVO_HOLD_MS      = 1000;  // Time (in ms) to hold flap open before closing

// 4. Timing & Cooldown
const unsigned long MIN_COOLDOWN_MS  = 2500; // Minimum time between consecutive actuations
const unsigned long POLL_INTERVAL_MS = 250;  // How often to check detection API (ms)

// Onboard LED indicator (GPIO 2 on most ESP32 boards)
const int STATUS_LED = 2;
// ===============================================================

WebServer server(80);

// State tracking
bool alreadyTriggeredForCurrentItem = false;
unsigned long lastTriggerTime = 0;
unsigned long lastPollTime    = 0;
uint32_t triggerCount         = 0;
String lastDetectedLabel      = "NONE";
float lastConfidence          = 0.0;
String lastStatusMsg          = "Ready. Waiting for BIODEGRADABLE item.";

// ESP32 Hardware PWM settings for standard 50Hz RC Servo
const int PWM_FREQ        = 50;     // 50 Hz = 20ms period
const int PWM_RESOLUTION  = 16;     // 16-bit resolution (0 to 65535)
const int PWM_CHANNEL     = 0;      // LEDC channel 0 (for Core 2.x)

// Convert angle (0-180) to 16-bit duty cycle for MG90S (~500us to 2400us)
uint32_t angleToDuty(int angle) {
  angle = constrain(angle, 0, 180);
  // Pulse width in microseconds
  long pulseUs = map(angle, 0, 180, 500, 2400);
  // 20,000 microseconds in 50Hz period. 16-bit max is 65535
  return (uint32_t)((pulseUs * 65535ULL) / 20000ULL);
}

void initServoPWM() {
#if defined(ESP_ARDUINO_VERSION) && ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 0)
  ledcAttach(SERVO_PIN, PWM_FREQ, PWM_RESOLUTION);
#else
  ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_RESOLUTION);
  ledcAttachPin(SERVO_PIN, PWM_CHANNEL);
#endif
}

void writeServoPWM(uint32_t duty) {
#if defined(ESP_ARDUINO_VERSION) && ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 0)
  ledcWrite(SERVO_PIN, duty);
#else
  ledcWrite(PWM_CHANNEL, duty);
#endif
}

// Move servo to angle, wait for arrival, then disable PWM to prevent buzzing
void moveServoTo(int targetAngle, int durationMs = 400) {
  uint32_t duty = angleToDuty(targetAngle);
  writeServoPWM(duty);
  delay(durationMs);
  // Sleep PWM output to eliminate MG90S idle twitching/buzzing and save power
  writeServoPWM(0);
}

// Operate servo ONE TIME ONCE: 0 deg -> 90 deg -> wait 1s -> 0 deg
bool actuateServo(const char* reason) {
  unsigned long now = millis();
  if (now - lastTriggerTime < MIN_COOLDOWN_MS) {
    Serial.printf("[SERVO] Skipped (cooldown active, %lu ms remaining)\n", MIN_COOLDOWN_MS - (now - lastTriggerTime));
    return false;
  }

  triggerCount++;
  lastTriggerTime = now;
  lastStatusMsg = String("Actuation #") + triggerCount + " - " + reason;

  digitalWrite(STATUS_LED, HIGH);
  Serial.println("\n************************************************");
  Serial.printf("[SERVO] *** ACTUATING MG90S SERVO (#%u) ***\n", triggerCount);
  Serial.printf("[SERVO] Reason: %s\n", reason);
  Serial.printf("[SERVO] Moving to %d degrees (Sort / Drop)...\n", SERVO_ACTIVE_ANGLE);
  Serial.println("************************************************");

  // Move to active sort position
  moveServoTo(SERVO_ACTIVE_ANGLE, 450);

  // Hold position for waste drop
  Serial.printf("[SERVO] Holding position for %d ms...\n", SERVO_HOLD_MS);
  delay(SERVO_HOLD_MS);

  // Return to resting position
  Serial.printf("[SERVO] Returning to rest position (%d degrees)...\n", SERVO_REST_ANGLE);
  moveServoTo(SERVO_REST_ANGLE, 450);

  digitalWrite(STATUS_LED, LOW);
  Serial.println("[SERVO] Move complete. Idle PWM off (silent).\n");
  return true;
}

// Helper string extraction from JSON response
String extractJsonVal(const String& json, const String& key) {
  int keyPos = json.indexOf("\"" + key + "\"");
  if (keyPos == -1) return "";
  int colonPos = json.indexOf(":", keyPos);
  if (colonPos == -1) return "";
  int q1 = json.indexOf("\"", colonPos);
  if (q1 == -1) return "";
  int q2 = json.indexOf("\"", q1 + 1);
  if (q2 == -1) return "";
  return json.substring(q1 + 1, q2);
}

float extractJsonNum(const String& json, const String& key) {
  int keyPos = json.indexOf("\"" + key + "\"");
  if (keyPos == -1) return 0.0;
  int colonPos = json.indexOf(":", keyPos);
  if (colonPos == -1) return 0.0;
  int start = colonPos + 1;
  while (start < json.length() && (json[start] == ' ' || json[start] == '\t')) start++;
  int end = start;
  while (end < json.length() && (isdigit(json[end]) || json[end] == '.' || json[end] == '-')) end++;
  return json.substring(start, end).toFloat();
}

// Poll the backend /api/result endpoint
void pollBackendDetection() {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  http.begin(BACKEND_URL);
  http.setTimeout(700);
  int httpCode = http.GET();

  if (httpCode == HTTP_CODE_OK) {
    String body = http.getString();
    String label = extractJsonVal(body, "label");
    float conf = extractJsonNum(body, "confidence");

    if (label.length() > 0) {
      lastDetectedLabel = label;
      lastConfidence = conf;

      // ONE TIME ONCE LOGIC:
      if (label == "BIODEGRADABLE" || label == "WHITE") {
        if (!alreadyTriggeredForCurrentItem) {
          // Trigger the MG90S servo motor ONE TIME ONCE
          if (actuateServo("BIODEGRADABLE object detected by camera")) {
            alreadyTriggeredForCurrentItem = true; // Lock until this object leaves
          }
        }
      } else {
        // Label is NO OBJECT, NON-BIODEGRADABLE, or UNKNOWN
        // Automatically re-arm for the NEXT biodegradable object
        if (alreadyTriggeredForCurrentItem) {
          Serial.printf("[SERVO] Object cleared (current: '%s'). Re-armed for next item.\n", label.c_str());
          alreadyTriggeredForCurrentItem = false;
        }
      }
    }
  } else {
    static unsigned long lastLog = 0;
    if (millis() - lastLog > 6000) {
      lastLog = millis();
      Serial.printf("[POLL] Backend HTTP status: %d (verify BACKEND_URL: %s)\n", httpCode, BACKEND_URL);
    }
  }
  http.end();
}

// ======================== WEB HANDLERS ========================
void handleRoot() {
  String html = "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>";
  html += "<title>ESP32 MG90S Servo Sorter</title>";
  html += "<style>body{margin:0;font-family:system-ui,sans-serif;background:#0b1020;color:#e6ebff;display:flex;justify-content:center;padding:20px}";
  html += ".card{background:#131a30;border:1px solid #222c4a;border-radius:14px;padding:24px;max-width:480px;width:100%}";
  html += "h1{font-size:19px;margin-top:0;color:#22c55e}p{color:#8b96b8;font-size:13px}";
  html += ".box{background:#0b1020;padding:12px;border-radius:8px;margin:10px 0;border:1px solid #222c4a;font-size:13px}";
  html += ".box b{color:#fff;font-size:15px;display:block;margin-top:3px}";
  html += "button{background:#2563eb;color:#fff;border:none;border-radius:8px;padding:12px;font-size:14px;font-weight:600;cursor:pointer;width:100%;margin-top:10px}";
  html += "button:hover{filter:brightness(1.15)}</style></head><body>";
  html += "<div class='card'>";
  html += "<h1>ESP32 + MG90S Micro Servo</h1>";
  html += "<p>Hardware: ESP32 + MG90S Servo on <b>GPIO 18</b> (No motor drivers needed).</p>";
  html += "<div class='box'>Detector Backend: <b>" + String(BACKEND_URL) + "</b></div>";
  html += "<div class='box'>Live Camera Label: <b style='color:" + String(lastDetectedLabel == "BIODEGRADABLE" ? "#22c55e" : "#f59e0b") + "'>" + lastDetectedLabel + " (" + String(int(lastConfidence * 100)) + "%)</b></div>";
  html += "<div class='box'>Actuator State: <b>" + String(alreadyTriggeredForCurrentItem ? "TRIGGERED (Waiting for item to leave)" : "ARMED (Ready to trigger)") + "</b></div>";
  html += "<div class='box'>Total Actuations: <b>" + String(triggerCount) + " times</b></div>";
  html += "<div class='box'>Last Action: <b>" + lastStatusMsg + "</b></div>";
  html += "<form action='/trigger' method='POST'><button type='submit'>⚙ Test MG90S Servo Once (0° → 90° → 0°)</button></form>";
  html += "</div></body></html>";
  server.send(200, "text/html", html);
}

void handleTrigger() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  bool ok = actuateServo("Web trigger / API request");
  String resp = String("{\"ok\":") + (ok ? "true" : "false") + ",\"triggers\":" + triggerCount + ",\"message\":\"" + lastStatusMsg + "\"}";
  server.send(ok ? 200 : 429, "application/json", resp);
}

void handleStatus() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  String resp = "{";
  resp += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  resp += "\"backend\":\"" + String(BACKEND_URL) + "\",";
  resp += "\"servo_pin\":18,";
  resp += "\"last_label\":\"" + lastDetectedLabel + "\",";
  resp += "\"triggers\":" + String(triggerCount) + ",";
  resp += "\"armed\":" + String(!alreadyTriggeredForCurrentItem ? "true" : "false");
  resp += "}";
  server.send(200, "application/json", resp);
}

// ======================== SETUP & LOOP ========================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n==================================================");
  Serial.println("  ESP32 MG90S Micro Servo Biodegradable Sorter   ");
  Serial.println("  Pins: 5V, GND, GPIO 18 (Signal)                ");
  Serial.println("==================================================");

  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(STATUS_LED, LOW);

  // Initialize Servo PWM on GPIO 18 and move to resting position (0 deg)
  initServoPWM();
  Serial.println("[SERVO] Initializing MG90S servo to 0 degrees...");
  moveServoTo(SERVO_REST_ANGLE, 500);

  // Connect to Wi-Fi
  Serial.printf("[WIFI] Connecting to '%s'...", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(400);
    Serial.print('.');
    digitalWrite(STATUS_LED, !digitalRead(STATUS_LED));
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    digitalWrite(STATUS_LED, HIGH);
    Serial.printf("[WIFI] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[WIFI] Web Dashboard: http://%s/\n", WiFi.localIP().toString().c_str());
  } else {
    digitalWrite(STATUS_LED, LOW);
    Serial.println("[WIFI] Failed to connect. Will retry automatically.");
  }

  // Setup Web Server
  server.on("/", HTTP_GET, handleRoot);
  server.on("/trigger", HTTP_GET, handleTrigger);
  server.on("/trigger", HTTP_POST, handleTrigger);
  server.on("/status", HTTP_GET, handleStatus);
  server.enableCORS();
  server.begin();
  Serial.println("[SERVER] Web server started on port 80.");
  Serial.println("[READY] Send 'T' in Serial Monitor to test servo anytime.\n");
}

void loop() {
  server.handleClient();

  // 1. Maintain Wi-Fi connection
  static unsigned long lastWifiCheck = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiCheck > 6000) {
    lastWifiCheck = millis();
    Serial.println("[WIFI] Reconnecting to Wi-Fi...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }

  // 2. Poll Backend Detection API
  if (millis() - lastPollTime >= POLL_INTERVAL_MS) {
    lastPollTime = millis();
    pollBackendDetection();
  }

  // 3. Serial command for instant manual testing
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'T' || c == 't' || c == '1') {
      Serial.println("\n[SERIAL] Manual trigger command received!");
      actuateServo("Serial command 'T'");
    } else if (c == 'S' || c == 's') {
      Serial.printf("[STATUS] IP: %s, Label: %s (%d%%), Triggers: %u, Armed: %s\n",
                    WiFi.localIP().toString().c_str(), lastDetectedLabel.c_str(),
                    int(lastConfidence * 100), triggerCount,
                    !alreadyTriggeredForCurrentItem ? "YES" : "NO");
    }
  }

  delay(5);
}
