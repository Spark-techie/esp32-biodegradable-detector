"""Run:  cd backend && python -m pytest ../tests -v     (or: python ../tests/test_pipeline.py)
Uses SYNTHETIC images only. Real-camera/hardware behaviour must be tested manually (see README)."""
import os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import cv2, numpy as np
from config import CFG
from detector import Detector

CFG["smooth_n"] = 1
DARK = (40, 40, 40)
GREEN, WHITE, RED = (40, 200, 40), (240, 240, 240), (30, 30, 220)


def canvas(bg=DARK): return np.full((480, 640, 3), bg, np.uint8)
def circle(img, c, col, x=320, y=240, r=80): cv2.circle(img, (x, y), r, col, -1); return img
def det(): return Detector(model_path="")
def label(img): return det().detect(img)["label"]


def test_green(): assert label(circle(canvas(), 0, GREEN)) == "NON-BIODEGRADABLE"
def test_white(): assert label(circle(canvas(), 0, WHITE)) == "BIODEGRADABLE"
def test_other(): assert label(circle(canvas(), 0, RED)) == "UNKNOWN"
def test_no_object(): assert label(canvas((120, 120, 120))) == "NO OBJECT"


def test_multiple_objects():
    img = circle(canvas(), 0, GREEN, 200, 240, 90); circle(img, 0, WHITE, 480, 240, 40)
    r = det().detect(img)
    assert len(r["objects"]) == 2 and r["label"] == "NON-BIODEGRADABLE"


def test_white_background_green_object():
    img = circle(canvas((250, 250, 250)), 0, GREEN)
    assert label(img) == "NON-BIODEGRADABLE"          # white background must not be treated as the object


def test_green_background_white_object():
    img = circle(canvas((40, 160, 40)), 0, WHITE)
    assert label(img) == "BIODEGRADABLE"


def test_lighting_changes():
    for k in (0.5, 0.7, 1.0):
        g = (circle(canvas(), 0, GREEN).astype(np.float32) * k).astype(np.uint8)
        assert label(g) == "NON-BIODEGRADABLE", k
    w = (circle(canvas(), 0, WHITE).astype(np.float32) * 0.9).astype(np.uint8)
    assert label(w) == "BIODEGRADABLE"


def test_missing_model_falls_back():
    d = Detector(model_path="/nonexistent/model.joblib")
    assert d.model is None and "not trained" in d.status()
    assert d.detect(circle(canvas(), 0, GREEN))["label"] == "NON-BIODEGRADABLE"


def test_training_pipeline_runs_on_synthetic_data():
    from train_model import train
    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory() as t:
        for cls, mk in (("green", lambda: (rng.integers(30, 80), rng.integers(150, 255), rng.integers(100, 255))),
                        ("white", lambda: (rng.integers(0, 180), rng.integers(0, 25), rng.integers(200, 255))),
                        ("other", lambda: (rng.choice([5, 100, 130, 160]), rng.integers(150, 255), rng.integers(100, 255)))):
            os.makedirs(f"{t}/{cls}")
            for i in range(24):
                hsv = np.full((120, 160, 3), (0, 0, 40), np.uint8)
                cv2.circle(hsv, (int(rng.integers(60, 100)), 60), int(rng.integers(25, 40)), tuple(int(v) for v in mk()), -1)
                cv2.imwrite(f"{t}/{cls}/{i}.jpg", cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR))
        out = f"{t}/models/m.joblib"
        m = train(t, out)
        assert os.path.exists(out) and "test" in m
        d = Detector(model_path=out, use_model=True)
        assert d.model is not None and d.status() == "RandomForest active"
        assert d.detect(circle(canvas(), 0, GREEN))["label"] in ("NON-BIODEGRADABLE", "UNKNOWN")  # runs end-to-end


# ---- camera reconnect against a local fake MJPEG server ----
JPG = cv2.imencode(".jpg", circle(canvas(), 0, GREEN))[1].tobytes()
RUN = threading.Event()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=frame"); self.end_headers()
        try:
            while RUN.is_set():
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(JPG) + JPG + b"\r\n")
                time.sleep(0.05)
        except Exception:
            pass
    def log_message(self, *a): pass


def _serve(port=0):
    RUN.set(); s = ThreadingHTTPServer(("127.0.0.1", port), H); s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start(); return s


def _wait(cond, t=8.0):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond(): return True
        time.sleep(0.05)
    return False


def test_camera_disconnect_and_reconnect():
    from camera_stream import CameraStream
    s = _serve(); port = s.server_address[1]
    cfg = dict(CFG, camera_url=f"http://127.0.0.1:{port}/stream", reconnect_delay_s=0.3, stale_timeout_s=1.0)
    cam = CameraStream(cfg); cam.start()
    try:
        assert _wait(lambda: cam.online()), "never connected"
        f, seq, _ = cam.get_frame(); assert f is not None and f.shape == (480, 640, 3)
        RUN.clear(); s.shutdown(); s.server_close()                      # camera "unplugged"
        assert _wait(lambda: not cam.online()), "disconnect not detected"
        s = _serve(port)                                                 # camera "back"
        assert _wait(lambda: cam.online()), "did not reconnect"
    finally:
        cam.stop(); RUN.clear(); s.shutdown()


def test_camera_unreachable_does_not_crash():
    from camera_stream import CameraStream
    cam = CameraStream(dict(CFG, camera_url="http://127.0.0.1:9/stream", reconnect_delay_s=0.2)); cam.start()
    try:
        assert _wait(lambda: not cam.online() and bool(cam.last_error), 4.0)
    finally:
        cam.stop()



if __name__ == "__main__":           # pytest-free runner
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            try: f(); print("PASS", n)
            except Exception as e: fails += 1; print("FAIL", n, type(e).__name__, e)
    sys.exit(1 if fails else 0)
