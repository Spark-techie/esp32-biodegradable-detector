"""Save frames from the camera into dataset/<class>/ .
Usage: python collect_data.py green 60 --interval 0.5   (class: green|white|other)"""
import argparse, os, time
import cv2
from config import CFG, DATASET_DIR, CLASSES
from camera_stream import CameraStream

ap = argparse.ArgumentParser()
ap.add_argument("cls", choices=CLASSES); ap.add_argument("count", type=int, nargs="?", default=50)
ap.add_argument("--interval", type=float, default=0.5); ap.add_argument("--url", default=CFG["camera_url"])
a = ap.parse_args()
CFG["camera_url"] = a.url
cam = CameraStream(CFG); cam.start()
out = os.path.join(DATASET_DIR, a.cls); os.makedirs(out, exist_ok=True)
print(f"Saving {a.count} frames to {out}. Move the object slightly between shots / vary lighting.")
n, last = 0, -1
while n < a.count:
    f, seq, _ = cam.get_frame()
    if f is None or seq == last or not cam.online():
        time.sleep(0.05); continue
    last = seq
    cv2.imwrite(os.path.join(out, f"{a.cls}_{int(time.time()*1000)}.jpg"), f); n += 1
    print(f"\r{n}/{a.count}", end="", flush=True); time.sleep(a.interval)
print("\nDone."); cam.stop()
