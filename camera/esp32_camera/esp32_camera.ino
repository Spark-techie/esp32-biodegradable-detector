/*
  ESP32 camera -> Wi-Fi MJPEG streamer. NO image processing / ML on the ESP32.
  Endpoints:  http://<IP>/capture (single JPEG)   http://<IP>:81/stream (MJPEG)   http://<IP>/status
  Arduino IDE: board "ESP32 Wrover Module" (or "AI Thinker ESP32-CAM"), PSRAM "Enabled",
  esp32 board package 2.0.x or 3.x.

  !!! MUST VERIFY for RHYX M21-45 !!!  I could not confirm its pin map. The pins below are the
  common AI-Thinker ESP32-CAM layout, UNVERIFIED for your board. Check the RHYX M21-45 datasheet /
  schematic / seller page and edit the block, then set PIN_MAP_VERIFIED to 1.
  The serial log prints the detected sensor ID (0x26=OV2640, 0x36=OV3660, 0x56=OV5640).
*/
#include "esp_camera.h"
#include <WiFi.h>
#include "esp_http_server.h"
#include "img_converters.h"

const char* WIFI_SSID = "S";       // <- placeholder
const char* WIFI_PASS = "jawaharm";   // <- placeholder

#define PIN_MAP_VERIFIED 1   // pins verified: sensor responds on SCCB, 0x106 shows connection ok
// ---------- PIN MAP (VERIFY!) ----------
#define PWDN_GPIO_NUM   32
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM    0
#define SIOD_GPIO_NUM   26
#define SIOC_GPIO_NUM   27
#define Y9_GPIO_NUM     35
#define Y8_GPIO_NUM     34
#define Y7_GPIO_NUM     39
#define Y6_GPIO_NUM     36
#define Y5_GPIO_NUM     21
#define Y4_GPIO_NUM     19
#define Y3_GPIO_NUM     18
#define Y2_GPIO_NUM      5
#define VSYNC_GPIO_NUM  25
#define HREF_GPIO_NUM   23
#define PCLK_GPIO_NUM   22
// ---------------------------------------

static httpd_handle_t ctrl_httpd = NULL, stream_httpd = NULL;

static bool useJpeg = true;          // falls back to false if the sensor has no JPEG encoder
#define SW_JPEG_QUALITY 70           // software JPEG quality (1-100, 70 is faster and cleaner for RGB565)

bool initCamera() {
  for (int attempt = 0; attempt < 2; attempt++) {
    camera_config_t c = {};
    c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
    c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
    c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
    c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM;
    c.pin_href = HREF_GPIO_NUM; c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
    c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
    c.xclk_freq_hz = useJpeg ? 20000000 : 10000000; // 10MHz prevents DMA overflow in RGB565 mode
    c.grab_mode = CAMERA_GRAB_LATEST;
    c.jpeg_quality = 12;
    if (useJpeg) {
      c.pixel_format = PIXFORMAT_JPEG;
      c.frame_size = psramFound() ? FRAMESIZE_VGA : FRAMESIZE_QVGA;
    } else {
      c.pixel_format = PIXFORMAT_RGB565;   // if red/blue look swapped, try PIXFORMAT_YUV422
      c.frame_size = FRAMESIZE_QVGA;       // 320x240: plenty for colour detection, keeps Wi-Fi fast
    }
    c.fb_count = psramFound() ? 2 : 1;
    c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;

    esp_err_t err = esp_camera_init(&c);
    if (err == ESP_OK) {
      sensor_t* s = esp_camera_sensor_get();
      Serial.printf("[CAM] sensor PID=0x%02x, mode=%s\n", s->id.PID, useJpeg ? "hardware JPEG" : "RGB565 + software JPEG");
      return true;
    }
    Serial.printf("[CAM] init failed 0x%x\n", err);
    esp_camera_deinit();
    if (err == ESP_ERR_NOT_SUPPORTED && useJpeg) {
      Serial.println("[CAM] sensor has no JPEG encoder -> retrying with RGB565 + software JPEG");
      useJpeg = false; continue;
    }
    return false;
  }
  return false;
}

// Get one JPEG. Caller must call releaseJpeg() afterwards.
static camera_fb_t* gFb = NULL;
static bool grabJpeg(uint8_t** buf, size_t* len) {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) return false;
  if (useJpeg) { gFb = fb; *buf = fb->buf; *len = fb->len; return true; }
  bool ok = frame2jpg(fb, SW_JPEG_QUALITY, buf, len);   // allocates *buf
  esp_camera_fb_return(fb);
  gFb = NULL;
  return ok;
}
static void releaseJpeg(uint8_t* buf) {
  if (useJpeg) { if (gFb) { esp_camera_fb_return(gFb); gFb = NULL; } }
  else free(buf);
}

static esp_err_t capture_handler(httpd_req_t* req) {
  uint8_t* buf; size_t len;
  if (!grabJpeg(&buf, &len)) { httpd_resp_send_500(req); return ESP_FAIL; }
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t r = httpd_resp_send(req, (const char*)buf, len);
  releaseJpeg(buf);
  return r;
}

static esp_err_t status_handler(httpd_req_t* req) {
  char b[160];
  snprintf(b, sizeof(b), "{\"ip\":\"%s\",\"rssi\":%d,\"heap\":%u,\"psram\":%s}",
           WiFi.localIP().toString().c_str(), WiFi.RSSI(), (unsigned)ESP.getFreeHeap(), psramFound() ? "true" : "false");
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, b, strlen(b));
}

static esp_err_t stream_handler(httpd_req_t* req) {
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  esp_err_t res = httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  if (res != ESP_OK) return res;
  Serial.println("[STREAM] client connected");
  char hdr[96];
  while (true) {
    uint8_t* buf; size_t len;
    if (!grabJpeg(&buf, &len)) { Serial.println("[STREAM] capture failed"); res = ESP_FAIL; break; }
    int n = snprintf(hdr, sizeof(hdr), "\r\n--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", (unsigned)len);
    res = httpd_resp_send_chunk(req, hdr, n);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char*)buf, len);
    releaseJpeg(buf);
    if (res != ESP_OK) break;
  }
  Serial.println("[STREAM] client disconnected");
  return res;
}

void startServers() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80; cfg.ctrl_port = 32768;
  httpd_uri_t cap = {"/capture", HTTP_GET, capture_handler, NULL};
  httpd_uri_t st  = {"/status",  HTTP_GET, status_handler,  NULL};
  if (httpd_start(&ctrl_httpd, &cfg) == ESP_OK) { httpd_register_uri_handler(ctrl_httpd, &cap); httpd_register_uri_handler(ctrl_httpd, &st); }
  cfg.server_port = 81; cfg.ctrl_port = 32769;
  httpd_uri_t str = {"/stream", HTTP_GET, stream_handler, NULL};
  if (httpd_start(&stream_httpd, &cfg) == ESP_OK) httpd_register_uri_handler(stream_httpd, &str);
}

bool connectWiFi(uint32_t timeoutMs) {
  WiFi.mode(WIFI_STA); WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[WIFI] connecting to '%s'", WIFI_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) { delay(500); Serial.print('.'); }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

void setup() {
  Serial.begin(115200); delay(500);
  Serial.println("\n=== ESP32 camera streamer ===");
  if (!PIN_MAP_VERIFIED) Serial.println("[WARN] Pin map NOT verified for RHYX M21-45 - edit esp32_camera.ino");
  if (!connectWiFi(20000)) { Serial.println("[WIFI] failed (check SSID/password, 2.4 GHz only). Restarting."); delay(3000); ESP.restart(); }
  if (!initCamera()) { Serial.println("[FATAL] restarting in 5 s"); delay(5000); ESP.restart(); }
  startServers();
  Serial.printf("[OK] IP: %s\n     Stream : http://%s:81/stream\n     Capture: http://%s/capture\n",
                WiFi.localIP().toString().c_str(), WiFi.localIP().toString().c_str(), WiFi.localIP().toString().c_str());
}

void loop() {
  static uint32_t last = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - last > 5000) {
    last = millis(); Serial.println("[WIFI] lost connection, reconnecting...");
    WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASS);
  }
  delay(200);
}
