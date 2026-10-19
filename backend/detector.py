"""Object colour classification: HSV baseline + optional RandomForest model."""
import logging, os
from collections import Counter, deque
import cv2, numpy as np
import joblib
from config import CFG, MODEL_PATH
from preprocessing import analyse

log = logging.getLogger("detector")
LABELS = {"green": "NON-BIODEGRADABLE", "white": "BIODEGRADABLE"}
COLORS = {
    "NON-BIODEGRADABLE": (0, 255, 0),
    "BIODEGRADABLE": (255, 255, 255),
    "UNKNOWN": (0, 165, 255),
    "GREEN": (0, 255, 0),
    "WHITE": (255, 255, 255)
}


class Detector:
    def __init__(self, model_path=MODEL_PATH, use_model=None):
        self.model_path, self.force_use_model = model_path, use_model
        self.model, self.classes, self.error = None, [], ""
        self._hist = deque(maxlen=64)
        self.load_model()

    def load_model(self):
        self.model, self.classes, self.error = None, [], ""
        if not self.model_path or not os.path.exists(self.model_path):
            return False
        try:
            b = joblib.load(self.model_path)
            self.model, self.classes = b["model"], list(b["classes"])
            log.info("model loaded: %s", self.model_path)
            return True
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.error("model load failed: %s", self.error)
            return False

    def _use_model(self):
        want = CFG["use_model"] if self.force_use_model is None else self.force_use_model
        return want and self.model is not None

    def status(self):
        if self.error: return "error: " + self.error
        if self.model is None: return "not trained (HSV baseline active)"
        return "RandomForest active" if self._use_model() else "RandomForest loaded (disabled; HSV active)"

    def _classify(self, r):
        if self._use_model():
            p = self.model.predict_proba([r["feat"]])[0]
            i = int(np.argmax(p)); conf = float(p[i])
            lab = LABELS.get(self.classes[i], "UNKNOWN") if conf >= CFG["model_min_conf"] else "UNKNOWN"
            return lab, conf, "model"
        gf, wf = r["gf"], r["wf"]
        if gf >= CFG["min_confidence"]: return "NON-BIODEGRADABLE", gf, "hsv"
        if wf >= CFG["min_confidence"]: return "BIODEGRADABLE", wf, "hsv"
        return "UNKNOWN", 1.0 - max(gf, wf), "hsv"

    def analyse_frame(self, bgr):
        """Un-smoothed per-object results (best object first)."""
        out = []
        for r in analyse(bgr)[: int(CFG["max_objects"])]:
            lab, conf, m = self._classify(r)
            out.append(dict(label=lab, confidence=round(conf, 3), bbox=r["bbox"], kind=r["kind"], method=m))
        return out

    def detect(self, bgr):
        objs = self.analyse_frame(bgr)
        raw = (objs[0]["label"], objs[0]["confidence"]) if objs else ("NO OBJECT", 0.0)
        self._hist.append(raw)
        win = list(self._hist)[-max(1, int(CFG["smooth_n"])):]
        label = Counter(l for l, _ in win).most_common(1)[0][0]
        conf = float(np.mean([c for l, c in win if l == label]))
        bbox = objs[0]["bbox"] if objs and label != "NO OBJECT" else None
        return dict(label=label, confidence=round(conf, 3), bbox=bbox, objects=objs,
                    model=self.status())

    @staticmethod
    def annotate(bgr, res):
        img = bgr.copy()
        for o in res["objects"][1:]:
            x, y, w, h = o["bbox"]
            cv2.rectangle(img, (x, y), (x + w, y + h), COLORS.get(o["label"], (128, 128, 128)), 1)
        if res["bbox"]:
            x, y, w, h = res["bbox"]; col = COLORS.get(res["label"], (128, 128, 128))
            cv2.rectangle(img, (x, y), (x + w, y + h), col, 3)
            cv2.putText(img, f'{res["label"]} {res["confidence"]*100:.0f}%', (x, max(18, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2, cv2.LINE_AA)
        return img
