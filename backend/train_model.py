"""Train optional RandomForest colour classifier from dataset/{green,white,other}/*.jpg
Usage: python train_model.py [--dataset ../dataset] [--out ../models/color_rf.joblib]"""
import argparse, glob, json, os
import cv2, joblib, numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from config import CLASSES, DATASET_DIR, MODEL_PATH
from preprocessing import best_features


def load(dataset_dir):
    X, y, P = [], [], []
    for cls in CLASSES:
        for p in sorted(glob.glob(os.path.join(dataset_dir, cls, "*"))):
            if not p.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")): continue
            img = cv2.imread(p)
            if img is None: continue
            X.append(best_features(img)); y.append(cls); P.append(p)
    return np.array(X), np.array(y), P


def train(dataset_dir=DATASET_DIR, out_path=MODEL_PATH, seed=42):
    X, y, P = load(dataset_dir)
    counts = {c: int((y == c).sum()) for c in CLASSES}
    print("images per class:", counts)
    if min(counts.values()) < 10:
        raise SystemExit("Need at least 10 images in EACH of green/white/other (recommended 100+).")
    idx = np.arange(len(y))
    tr, tmp = train_test_split(idx, test_size=0.30, stratify=y, random_state=seed)      # 70 / 30
    va, te = train_test_split(tmp, test_size=0.50, stratify=y[tmp], random_state=seed)  # 15 / 15
    clf = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=seed, n_jobs=-1)
    clf.fit(X[tr], y[tr])
    report, metrics = [], {"counts": counts, "split": {"train": len(tr), "val": len(va), "test": len(te)}}
    for name, ids in (("validation", va), ("test", te)):
        pred = clf.predict(X[ids])
        cm = confusion_matrix(y[ids], pred, labels=CLASSES)
        metrics[name] = {"accuracy": float(accuracy_score(y[ids], pred)), "confusion_matrix": cm.tolist()}
        report.append(f"=== {name.upper()} ({len(ids)} images) ===\n"
                      + classification_report(y[ids], pred, labels=CLASSES, digits=3, zero_division=0)
                      + f"confusion matrix (rows=true, cols=pred, order={CLASSES}):\n{cm}\n")
        if name == "test": test_cm = cm
    from detector import Detector          # HSV baseline on the same test split, for comparison
    base = Detector(model_path="", use_model=False)
    bp = []
    for i in te:
        o = base.analyse_frame(cv2.imread(P[i]))
        bp.append({"NON-BIODEGRADABLE": "green", "BIODEGRADABLE": "white", "GREEN": "green", "WHITE": "white"}.get(o[0]["label"] if o else "", "other"))
    metrics["hsv_baseline_test_accuracy"] = float(accuracy_score(y[te], bp))
    report.append(f"HSV baseline accuracy on same test split: {metrics['hsv_baseline_test_accuracy']:.3f}\n")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    joblib.dump({"model": clf, "classes": list(clf.classes_), "counts": counts}, out_path)
    rep_dir = os.path.dirname(out_path)
    open(os.path.join(rep_dir, "eval_report.txt"), "w").write("\n".join(report))
    json.dump(metrics, open(os.path.join(rep_dir, "eval_metrics.json"), "w"), indent=2)
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(4, 4)); ax.imshow(test_cm, cmap="Blues")
        ax.set_xticks(range(3), labels=CLASSES); ax.set_yticks(range(3), labels=CLASSES)
        ax.set_xlabel("predicted"); ax.set_ylabel("true"); ax.set_title("Test confusion matrix")
        for i in range(3):
            for j in range(3): ax.text(j, i, test_cm[i, j], ha="center", va="center")
        fig.tight_layout(); fig.savefig(os.path.join(rep_dir, "confusion_matrix.png"), dpi=120)
    except Exception as e:
        print("confusion matrix image skipped:", e)
    print("\n".join(report)); print("Model saved to", out_path)
    return metrics


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DATASET_DIR); ap.add_argument("--out", default=MODEL_PATH)
    a = ap.parse_args(); train(a.dataset, a.out)
