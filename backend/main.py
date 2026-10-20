"""FastAPI server. Run from backend/:  uvicorn main:app --host 0.0.0.0 --port 8000"""
import csv, logging, os, threading, time
from collections import deque
from contextlib import asynccontextmanager
import cv2, numpy as np
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from config import CFG, FRONTEND_DIR, LOG_DIR
from camera_stream import CameraStream
from detector import Detector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")
os.makedirs(LOG_DIR, exist_ok=True)

cam, det = CameraStream(CFG), Detector()
lock, stop = threading.Lock(), threading.Event()
S = dict(result=None, jpg=None, seq=0, ts=0.0, latency=0.0, last_label=None,
         history=deque(maxlen=CFG["history_len"]),
         counts={"NON-BIODEGRADABLE": 0, "BIODEGRADABLE": 0, "GREEN": 0, "WHITE": 0})


def _trigger_servo(url):
    import urllib.request
    try:
        req = urllib.request.Request(url, data=b"", headers={"User-Agent": "FastAPI-Backend"})
        urllib.request.urlopen(req, timeout=2.0)
        log.info("Triggered MG90S servo motor at %s", url)
    except Exception as e:
        log.warning("Failed to trigger servo motor at %s: %s", url, e)


def _placeholder(text):
    img = np.zeros((480, 640, 3), np.uint8)
    cv2.putText(img, text, (150, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (120, 120, 120), 2, cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


NO_SIGNAL = _placeholder("NO CAMERA SIGNAL")
ts_str = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def inference_loop():
    last_seq, last_log, last_logged_label = -1, 0.0, None
    f = open(os.path.join(LOG_DIR, "detections.csv"), "a", newline="")
    w = csv.writer(f)
    if f.tell() == 0: w.writerow(["timestamp", "label", "confidence", "objects", "latency_ms"])
    while not stop.is_set():
        frame, seq, _ = cam.get_frame()
        if frame is None or seq == last_seq:
            time.sleep(0.01); continue
        last_seq, t0 = seq, time.time()
        try:
            res = det.detect(frame)
            jpg = cv2.imencode(".jpg", det.annotate(frame, res), [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
        except Exception:
            log.exception("inference failed"); time.sleep(0.2); continue
        lat = (time.time() - t0) * 1000
        with lock:
            S.update(result=res, jpg=jpg, seq=S["seq"] + 1, ts=time.time(), latency=round(lat, 1))
            if res["label"] != S["last_label"]:
                old_label = S["last_label"]
                S["last_label"] = res["label"]
                if res["label"] in S["counts"]: S["counts"][res["label"]] += 1
                if res["label"] == "NON-BIODEGRADABLE": S["counts"]["GREEN"] = S["counts"]["NON-BIODEGRADABLE"]
                elif res["label"] == "BIODEGRADABLE": S["counts"]["WHITE"] = S["counts"]["BIODEGRADABLE"]
                S["history"].appendleft(dict(time=ts_str(time.time()), label=res["label"], confidence=res["confidence"]))
                servo_target = CFG.get("servo_url") or CFG.get("stepper_url")
                if res["label"] == "BIODEGRADABLE" and old_label != "BIODEGRADABLE" and servo_target:
                    threading.Thread(target=_trigger_servo, args=(servo_target,), daemon=True).start()
        now = time.time()
        if res["label"] != last_logged_label or now - last_log >= CFG["log_interval_s"]:
            w.writerow([ts_str(now), res["label"], res["confidence"], len(res["objects"]), round(lat, 1)]); f.flush()
            last_log, last_logged_label = now, res["label"]
        time.sleep(max(0.0, 1.0 / CFG["max_fps"] - (time.time() - t0)))


@asynccontextmanager
async def lifespan(app):
    cam.start()
    threading.Thread(target=inference_loop, daemon=True, name="inference").start()
    yield
    stop.set(); cam.stop()


app = FastAPI(title="Biodegradable / Non-Biodegradable Detector", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/result")
def result():
    online = cam.online()
    with lock:
        r, ts = S["result"], S["ts"]
        base = dict(label="NO OBJECT", confidence=0.0, bbox=None, objects=[])
        if r and online: base.update(r)
        return dict(label=base["label"], confidence=base["confidence"], bbox=base["bbox"], objects=base["objects"],
                    timestamp=ts_str(ts) if ts else None, latency_ms=S["latency"],
                    camera=dict(online=online, connected=cam.connected, url=CFG["camera_url"], fps=cam.fps(),
                                frames=cam.frames, error=cam.last_error),
                    model=dict(status=det.status(), loaded=det.model is not None),
                    counts=S["counts"], history=list(S["history"])[:20])


@app.get("/api/history")
def history():
    with lock: return list(S["history"])


@app.get("/api/frame.jpg")
def frame_jpg():
    with lock: jpg = S["jpg"] if cam.online() else None
    return Response(jpg or NO_SIGNAL, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/stream")
def stream():
    def gen():
        last = -1
        while not stop.is_set():
            with lock: seq, jpg = S["seq"], S["jpg"]
            if not cam.online(): jpg, seq = NO_SIGNAL, -2
            if seq != last and jpg:
                last = seq
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.04)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/reconnect")
def reconnect():
    cam.reconnect(); return {"ok": True}


@app.post("/api/reload_model")
def reload_model():
    return {"loaded": det.load_model(), "status": det.status()}


@app.post("/api/reset_counts")
def reset_counts():
    with lock:
        S["counts"].update({"NON-BIODEGRADABLE": 0, "BIODEGRADABLE": 0, "GREEN": 0, "WHITE": 0})
        S["history"].clear()
    return {"ok": True}


@app.post("/api/trigger_servo")
@app.post("/api/trigger_stepper")
def trigger_servo_now():
    url = CFG.get("servo_url") or CFG.get("stepper_url")
    if not url:
        return JSONResponse({"ok": False, "error": "No servo_url configured"}, status_code=400)
    threading.Thread(target=_trigger_servo, args=(url,), daemon=True).start()
    return {"ok": True, "servo_url": url}


@app.get("/api/config")
def get_config(): return CFG


@app.post("/api/config")
def set_config(upd: dict = Body(...)):
    bad = [k for k in upd if k not in CFG]
    if bad: return JSONResponse({"error": f"unknown keys: {bad}"}, status_code=400)
    for k, v in upd.items():
        CFG[k] = type(CFG[k])(v) if not isinstance(CFG[k], bool) else bool(v)
    if "stepper_url" in upd and "servo_url" not in upd:
        CFG["servo_url"] = str(upd["stepper_url"])
    if "servo_url" in upd and "stepper_url" not in upd:
        CFG["stepper_url"] = str(upd["servo_url"])
    if "camera_url" in upd: cam.reconnect()
    return CFG



app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
