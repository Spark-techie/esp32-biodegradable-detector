"""All tunable settings. Override camera URL with env var CAMERA_URL.
Values in CFG can also be changed live via POST /api/config (not persisted)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = str(ROOT / "models" / "color_rf.joblib")
DATASET_DIR = str(ROOT / "dataset")
LOG_DIR = str(ROOT / "logs")
FRONTEND_DIR = str(ROOT / "frontend")
CLASSES = ["green", "white", "other"]

CFG = {
    # --- camera ---  (ESP32 stream: http://<ESP32_IP>:81/stream ; "0" = laptop webcam for testing)
    "camera_url": os.getenv("CAMERA_URL", "http://10.235.152.15:81/stream"),
    "reconnect_delay_s": 2.0,
    "stale_timeout_s": 3.0,
    # --- preprocessing ---
    "process_width": 320,          # frames are downscaled to this width for analysis
    # --- HSV thresholds (OpenCV: H 0-179, S/V 0-255) ---
    "green_h_min": 35, "green_h_max": 85, "green_s_min": 60, "green_v_min": 40,
    "white_s_max": 40, "white_v_min": 170,
    "other_s_min": 70, "other_v_min": 50,   # saturated non-green pixels = "other" candidates
    # --- object localisation ---
    "min_area_frac": 0.01,         # ignore blobs smaller than 1% of frame
    "max_area_frac": 0.60,         # ignore blobs bigger than 60% (background)
    "max_border_sides": 3,         # blob touching >= this many frame edges = background
    "max_objects": 5,
    # --- classification ---
    "min_confidence": 0.60,        # below -> UNKNOWN (HSV mode)
    "use_model": True,             # use trained RandomForest if models/color_rf.joblib exists
    "model_min_conf": 0.60,        # below -> UNKNOWN (model mode)
    "smooth_n": 5,                 # majority vote over last N frames (1 = off)
    # --- runtime ---
    "max_fps": 15,
    "log_interval_s": 1.0,
    "history_len": 100,
    # --- servo motor actuator (MG90S micro servo, e.g. http://192.168.1.51/trigger) ---
    "servo_url": os.getenv("SERVO_URL", os.getenv("STEPPER_URL", "")),
    "stepper_url": os.getenv("STEPPER_URL", os.getenv("SERVO_URL", "")),
}

