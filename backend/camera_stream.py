"""Camera acquisition thread with automatic reconnect.
HTTP MJPEG (ESP32) is parsed with stdlib urllib (hard timeouts, no FFmpeg quirks).
A webcam index ("0") or video file path is also supported for testing."""
import logging, threading, time, urllib.request
import cv2, numpy as np

log = logging.getLogger("camera")


class CameraStream:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._frame, self._seq, self._ts = None, 0, 0.0
        self.connected, self.frames, self.last_error = False, 0, ""
        self._stop, self._reconnect = threading.Event(), threading.Event()
        self._times = []

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="camera").start()

    def stop(self): self._stop.set()
    def reconnect(self): self._reconnect.set()

    def get_frame(self):
        with self._lock:
            return self._frame, self._seq, self._ts

    def age(self): return time.time() - self._ts if self._ts else float("inf")
    def online(self): return self.connected and self.age() < self.cfg["stale_timeout_s"]

    def fps(self):
        now = time.time()
        return round(len([t for t in self._times if now - t < 2.0]) / 2.0, 1)

    def _publish(self, frame):
        now = time.time()
        with self._lock:
            self._frame, self._ts = frame, now
            self._seq += 1
        self.frames += 1
        self._times = [t for t in self._times if now - t < 2.0] + [now]

    def _run(self):
        while not self._stop.is_set():
            self._reconnect.clear()
            src = str(self.cfg["camera_url"]).strip()
            try:
                log.info("connecting to %s", src)
                if src.lower().startswith("http"): self._run_http(src)
                else: self._run_cv(int(src) if src.isdigit() else src)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("camera error: %s", self.last_error)
            self.connected = False
            if self._stop.wait(self.cfg["reconnect_delay_s"]):
                break

    def _run_http(self, url):
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.connected, self.last_error = True, ""
            log.info("camera connected")
            buf = b""
            while not self._stop.is_set() and not self._reconnect.is_set():
                chunk = resp.read1(65536)           # raises on timeout
                if not chunk:
                    raise ConnectionError("stream closed by camera")
                buf += chunk
                while True:
                    a = buf.find(b"\xff\xd8")
                    if a < 0: buf = buf[-1:]; break
                    b = buf.find(b"\xff\xd9", a + 2)
                    if b < 0: buf = buf[a:]; break
                    jpg, buf = buf[a:b + 2], buf[b + 2:]
                    f = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                    if f is not None: self._publish(f)
                if len(buf) > 3_000_000: buf = b""

    def _run_cv(self, src):
        cap = cv2.VideoCapture(src)
        try:
            if not cap.isOpened(): raise ConnectionError(f"cannot open {src}")
            self.connected, self.last_error, fails = True, "", 0
            while not self._stop.is_set() and not self._reconnect.is_set():
                ok, f = cap.read()
                if not ok:
                    fails += 1
                    if fails >= 5: raise ConnectionError("read failed")
                    time.sleep(0.1); continue
                fails = 0; self._publish(f); time.sleep(0.01)
        finally:
            cap.release()
