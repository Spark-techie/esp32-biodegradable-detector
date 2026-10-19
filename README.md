# ESP32-CAM Biodegradable / Non-Biodegradable Waste Detector & Stepper Sorter

## Architecture
```
 ESP32 camera (RHYX M21-45)            Laptop / lab PC (Python)                      Browser
┌────────────────────────┐  Wi-Fi  ┌──────────────────────────────────────────┐  HTTP  ┌───────────┐
│ capture JPEG only      │ MJPEG   │ camera_stream.py  (thread, auto-reconnect)│        │ Dashboard │
│ http://IP:81/stream    ├────────►│  -> preprocessing.py (HSV masks, ROI)     │  REST  │ index.html│
│ http://IP/capture      │         │  -> detector.py (HSV rule / RandomForest) ├───────►│ style.css │
│ NO ML on the ESP32     │         │  -> main.py FastAPI (/api/*, MJPEG, logs) │ + MJPEG│ app.js    │
└────────────────────────┘         └───────────────┬──────────────────────────┘        └─────┬─────┘
                                                   │                                         │
                                                   │ Optional Webhook / Poll                 │ Config
                                                   ▼                                         ▼
                                       ┌────────────────────────────────────────────────────────┐
                                       │ ESP32 Stepper Sorter (stepper/esp32_stepper.ino)      │
                                       │ -> Operates stepper 1x when BIODEGRADABLE is detected  │
                                       └────────────────────────────────────────────────────────┘
```
**How it detects:** frame is downscaled -> HSV masks for green (NON-BIODEGRADABLE) / white (BIODEGRADABLE) / saturated-other -> contours = candidate
objects. Blobs that are too small, too large (>60%) or touch >=3 frame edges are treated as *background*. Each region is classified by the fraction of its own pixels:
- **BIODEGRADABLE** (white objects)
- **NON-BIODEGRADABLE** (green objects)
- **UNKNOWN** (other colours) / **NO OBJECT** (empty scene).
Results are majority-voted over 5 frames. Optional RandomForest uses HSV histograms + stats of the region. Everything is in `backend/config.py`.

## Missing hardware info (you must check)
I could not verify the **RHYX M21-45 pin map or sensor**. `camera/esp32_camera.ino` uses the common AI-Thinker pin layout
**as an unverified placeholder**. Check your board's datasheet/seller page/schematic for: XCLK, SIOD/SIOC (SDA/SCL), Y2..Y9, VSYNC,
HREF, PCLK, PWDN, RESET, PSRAM present. Edit the pin block, set `PIN_MAP_VERIFIED 1`. The serial monitor prints the sensor ID
(0x26 OV2640 / 0x36 OV3660 / 0x56 OV5640); "camera init failed" almost always = wrong pins.

## Windows 10/11 setup
1. Install Python 3.10-3.12 from python.org (tick **Add python.exe to PATH**). Check: `python --version`
2. Open PowerShell in the project folder:
```
cd green_white_detector\backend
python -m venv ..\.venv
..\.venv\Scripts\Activate.ps1        (CMD: ..\.venv\Scripts\activate.bat)
pip install -r requirements.txt
```
(If PowerShell blocks activation: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.)
3. **Firmware:** install Arduino IDE 2.x -> Boards Manager: "esp32 by Espressif" (2.0.x or 3.x). Open `camera\esp32_camera.ino`,
   set `WIFI_SSID`/`WIFI_PASS` (2.4 GHz only), fix pins. Tools: Board "ESP32 Wrover Module" (or AI Thinker ESP32-CAM), PSRAM "Enabled",
   pick COM port, Upload (hold BOOT/IO0 low while resetting if your board has no auto-download). Open Serial Monitor at 115200 and read the IP.
4. Put laptop and ESP32 on the **same Wi-Fi/LAN** (or laptop hotspot). Test in a browser: `http://<ESP32_IP>/capture` shows a photo.
5. Start backend (venv active, in `backend\`):
```
set CAMERA_URL=http://<ESP32_IP>:81/stream           (PowerShell: $env:CAMERA_URL="http://<ESP32_IP>:81/stream")
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```
6. Open `http://localhost:8000` (phone: `http://<laptop-IP>:8000`, allow Python through Windows Firewall).
   No ESP32 yet? `set CAMERA_URL=0` uses the laptop webcam to test everything.
7. **Works immediately with the HSV baseline** (no training). Optional ML:
```
python collect_data.py green 100 --interval 0.5      (hold green objects, vary angle/light/background)
python collect_data.py white 100
python collect_data.py other 100                      (other colours AND hard cases: grey, beige, yellow, green-ish)
python train_model.py                                 (prints accuracy/precision/recall/F1/confusion matrix, saves models\color_rf.joblib)
```
   Restart the backend (or `curl -X POST http://localhost:8000/api/reload_model`). Dashboard badge shows "RandomForest active".
   Dataset layout: `dataset/{green,white,other}/*.jpg`; split is automatic 70/15/15 stratified. Capture train and test under
   different sessions/lighting if possible - consecutive frames are near-duplicates and inflate accuracy.
   Reports: `models\eval_report.txt`, `eval_metrics.json`, `confusion_matrix.png`. The report also shows the HSV baseline on the same test set.

## Tuning (backend/config.py, or live `POST /api/config` with JSON like `{"white_v_min":150}`)
- White seen as UNKNOWN/no object (dim light): lower `white_v_min`, raise `white_s_max`.
- Green missed: widen `green_h_min/max`, lower `green_s_min`. Too jumpy: raise `smooth_n`.
- Lower `min_confidence` / `model_min_conf` = fewer UNKNOWN.
- Camera auto-exposure/white-balance shift colours: keep lighting steady; prefer a plain non-green, non-white background.

## API
`GET /api/result` (label, confidence, bbox, objects, camera, model, counts, history) · `/api/stream` (annotated MJPEG) · `/api/frame.jpg` ·
`/api/history` · `POST /api/reconnect` · `/api/reload_model` · `/api/reset_counts` · `GET/POST /api/config`. Log: `logs/detections.csv`.

## Known limitations
- A **white object on a white background** (or green on green) merges into the background and is rejected -> NO OBJECT.
- Shadowed white (grey) is not white by design; lower `white_v_min` carefully.
- Single-primary-object decision (largest/most central); other objects are listed in `objects` and boxed thinly.

## Troubleshooting
| Symptom | Fix |
|---|---|
| ESP32 "camera init failed 0x..." | Wrong pin map / loose ribbon / weak USB power. Verify pins. |
| Wi-Fi never connects | 2.4 GHz only; recheck SSID/password. |
| Dashboard "CAMERA OFFLINE" | Wrong `CAMERA_URL`; open `http://IP:81/stream` in a browser; same network; firewall. Click Reconnect. |
| Low FPS / drops | Move closer to router; keep VGA/QVGA; raise `jpeg_quality` number (lower quality) in the .ino. |
| `ModuleNotFoundError` | venv not active: re-run the Activate step, `pip install -r requirements.txt`. |
| Port 8000 busy | use `--port 8001`. |
| Model badge "not trained" | Expected before training; HSV baseline is active. |

## Tests
`cd backend && python -m pytest ../tests -v` (or `python ../tests/test_pipeline.py`).

| Scenario | Status |
|---|---|
| Green / white / other-colour object, no object, multiple objects, white & green backgrounds, brightness 50-100% | **Executed on synthetic images: pass** |
| Missing model -> HSV fallback; training/eval pipeline end-to-end | **Executed on synthetic data: pass** (not a real accuracy figure) |
| Camera disconnect + reconnect, unreachable camera | **Executed against a local fake MJPEG server: pass** |
| FastAPI server + dashboard in a browser, backend restart | **Not executed** (FastAPI not installed in my sandbox) - test on your PC |
| ESP32 firmware compile/flash, real stream, real objects/lighting, model accuracy on real data | **Not executed - needs your hardware** |

Manual hardware checklist: (1) green object -> NON-BIODEGRADABLE, (2) white -> BIODEGRADABLE, (3) red/blue -> UNKNOWN, (4) empty scene -> NO OBJECT,
(5) two objects -> larger/centred wins, (6) dim/bright light, (7) unplug ESP32 -> OFFLINE then auto-recovers, (8) stop/start backend -> page recovers,
(9) delete `models\*.joblib` -> badge "not trained", detection still works.

## ESP32 Stepper Motor Sorter (`stepper/esp32_stepper/esp32_stepper.ino`)
When a **BIODEGRADABLE** object is detected by the camera, this ESP32 sketch actuates the stepper motor **one time once**:
1. **Auto-Poll:** Periodically queries `http://<LAPTOP_IP>:8000/api/result`.
2. **Edge-Triggered:** Only triggers once when `label == "BIODEGRADABLE"`. If the item remains in view, it does not continuously spin. As soon as the object leaves the camera frame, it rearms for the next item.
3. **Driver Support:**
   - ULN2003 + 28BYJ-48 stepper (default 4-pin: GPIO 19, 18, 5, 17). Coils are automatically depowered while idle to prevent heat.
   - A4988 / DRV8825 (STEP on GPIO 18, DIR on GPIO 19).
4. **Web UI & Direct Trigger:**
   - Open `http://<STEPPER_IP>/` in any browser to see live actuation count and trigger the motor manually.
   - Or configure `stepper_url` in the web dashboard (e.g. `http://192.168.1.51/trigger`) to push triggers directly from the backend.

