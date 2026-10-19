"""OpenCV preprocessing: colour masks -> object regions -> per-region colour stats/features."""
import cv2
import numpy as np
from config import CFG

_K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def _prep(bgr):
    oh, ow = bgr.shape[:2]
    s = min(1.0, CFG["process_width"] / ow)
    small = cv2.resize(bgr, (max(1, int(ow * s)), max(1, int(oh * s))), interpolation=cv2.INTER_AREA) if s < 1 else bgr
    hsv = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2HSV)
    c = CFG
    green = cv2.inRange(hsv, (c["green_h_min"], c["green_s_min"], c["green_v_min"]), (c["green_h_max"], 255, 255))
    white = cv2.inRange(hsv, (0, 0, c["white_v_min"]), (180, c["white_s_max"], 255))
    colored = cv2.inRange(hsv, (0, c["other_s_min"], c["other_v_min"]), (180, 255, 255))
    other = cv2.bitwise_and(colored, cv2.bitwise_not(green))
    masks = {k: cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, _K), cv2.MORPH_CLOSE, _K)
             for k, m in (("green", green), ("white", white), ("other", other))}
    return s, hsv, masks


def _features(hsv, rm, gf, wf):
    parts = []
    for ch, bins, rng in ((0, 16, [0, 180]), (1, 8, [0, 256]), (2, 8, [0, 256])):
        h = cv2.calcHist([hsv], [ch], rm, [bins], rng).ravel()
        parts.append(h / max(h.sum(), 1))
    px = hsv[rm > 0].astype(np.float32)
    if len(px) == 0:
        px = np.zeros((1, 3), np.float32)
    parts += [px.mean(0) / 255.0, px.std(0) / 255.0, np.array([gf, wf])]
    return np.concatenate(parts).astype(np.float32)


def analyse(bgr):
    """Return candidate object regions (best first). Large blobs touching the frame border
    (walls, tables, backgrounds) are rejected, so a white/green BACKGROUND is not an object."""
    s, hsv, masks = _prep(bgr)
    H, W = hsv.shape[:2]
    regions = []
    for kind, m in masks.items():
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            f = cv2.contourArea(c) / (W * H)
            if f < CFG["min_area_frac"] or f > CFG["max_area_frac"]:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            sides = int(x <= 1) + int(y <= 1) + int(x + bw >= W - 1) + int(y + bh >= H - 1)
            if sides >= CFG["max_border_sides"]:
                continue
            rm = np.zeros((H, W), np.uint8)
            cv2.drawContours(rm, [c], -1, 255, -1)
            n = max(cv2.countNonZero(rm), 1)
            gf = cv2.countNonZero(cv2.bitwise_and(masks["green"], rm)) / n
            wf = cv2.countNonZero(cv2.bitwise_and(masks["white"], rm)) / n
            cx, cy = (x + bw / 2) / W, (y + bh / 2) / H
            d = min(1.0, float(np.hypot(cx - .5, cy - .5)) / 0.7071)
            regions.append(dict(kind=kind, area_frac=f, gf=gf, wf=wf,
                                bbox=[int(x / s), int(y / s), int(bw / s), int(bh / s)],
                                score=f * (1 - 0.5 * d), feat=_features(hsv, rm, gf, wf)))
    regions.sort(key=lambda r: r["score"], reverse=True)
    return regions


def best_features(bgr):
    """Feature vector for training/inference: best region, else centre crop fallback."""
    regs = analyse(bgr)
    if regs:
        return regs[0]["feat"]
    s, hsv, masks = _prep(bgr)
    H, W = hsv.shape[:2]
    rm = np.zeros((H, W), np.uint8)
    rm[H // 4: 3 * H // 4, W // 4: 3 * W // 4] = 255
    n = max(cv2.countNonZero(rm), 1)
    gf = cv2.countNonZero(cv2.bitwise_and(masks["green"], rm)) / n
    wf = cv2.countNonZero(cv2.bitwise_and(masks["white"], rm)) / n
    return _features(hsv, rm, gf, wf)
