"""Train + evaluate + (optionally) promote the local risk model.

Split: time AND geography (test = held-out regions ∪ dates >= test_period_start). Preprocessing + calibration
happen inside the training fold only. Candidates: rule baseline, calibrated LogisticRegression (explainable),
calibrated HistGradientBoosting. Promotion requires config/model_acceptance.yaml and beating the rule baseline.

Usage: uv run python -m training.train [--dataset data/dataset.parquet] [--promote] [--approver team-lead]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import polars as pl
import yaml
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.risk.inference import rule_baseline_probability
from app.risk.thresholds import load_thresholds

ROOT = Path(__file__).resolve().parents[1]
NON_FEATURES = {
    "route_id",
    "region",
    "country",
    "mode",
    "date",
    "departure_utc",
    "label",
    "label_actual_max_precip_mm",
    "label_actual_max_gust_kmh",
    "label_official_alert",
    "label_quake",
}


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=False):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.any():
            total += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(total)


def derive_thresholds(y: np.ndarray, p: np.ndarray, *, high_recall: float, medium_recall: float) -> dict[str, float]:
    """Largest thresholds on OOF predictions reaching the recall objectives (monotone in p)."""
    pos = np.sort(p[y == 1])
    if len(pos) == 0:
        return {"high": 0.55, "medium": 0.25}

    def largest_for(recall: float) -> float:
        k = int(np.ceil(recall * len(pos)))  # need at least k positives above threshold
        idx = max(0, len(pos) - k)
        return float(max(0.01, min(0.95, pos[idx])))

    high = largest_for(high_recall)
    medium = min(high * 0.6, largest_for(medium_recall))
    return {"high": round(high, 4), "medium": round(max(0.005, medium), 4)}


def metrics(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, float]:
    pred = (p >= threshold).astype(int)
    pr, rc, f1, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    n_pos = int(y.sum())
    fnr = float(fn / n_pos) if n_pos else 0.0
    # Wilson 95% interval for recall
    z = 1.96
    if n_pos:
        r = tp / n_pos
        denom = 1 + z**2 / n_pos
        centre = (r + z**2 / (2 * n_pos)) / denom
        half = z * np.sqrt(r * (1 - r) / n_pos + z**2 / (4 * n_pos**2)) / denom
        ci = (max(0.0, centre - half), min(1.0, centre + half))
    else:
        ci = (0.0, 0.0)
    return {
        "high_recall": float(rc),
        "high_recall_ci95": [round(ci[0], 3), round(ci[1], 3)],
        "high_precision": float(pr),
        "f1": float(f1),
        "false_negative_rate": fnr,
        "pr_auc": float(average_precision_score(y, p)) if n_pos else 0.0,
        "roc_auc": float(roc_auc_score(y, p)) if 0 < n_pos < len(y) else 0.5,
        "brier": float(brier_score_loss(y, p)),
        "ece": ece(y, p),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "n": int(len(y)),
        "n_positive": n_pos,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=ROOT / "data" / "dataset.parquet")
    ap.add_argument("--artifacts", type=Path, default=ROOT / "artifacts")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--approver", default="team-lead")
    args = ap.parse_args()

    df = pl.read_parquet(args.dataset)
    manifest = json.loads(args.dataset.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    routes_cfg = yaml.safe_load((ROOT / "training" / "routes.yaml").read_text(encoding="utf-8"))
    acceptance = yaml.safe_load((ROOT / "config" / "model_acceptance.yaml").read_text(encoding="utf-8"))
    thresholds = load_thresholds(ROOT / "config" / "thresholds.yaml")
    # stacked feature: the deterministic rule score (computed identically online in app.risk.model_loader)
    base_names = [c for c in df.columns if c not in NON_FEATURES]
    df = df.with_columns(
        pl.Series("rule_score", [rule_baseline_probability(r) for r in df.select(base_names).to_dicts()])
    )
    feature_names = base_names + ["rule_score"]
    test_regions = set(routes_cfg["split"]["test_regions"])
    test_start = routes_cfg["split"]["test_period_start"]
    is_test = df["region"].is_in(list(test_regions)) | (df["date"] >= test_start)
    train_df, test_df = df.filter(~is_test), df.filter(is_test)
    x_tr = train_df.select(feature_names).to_numpy().astype(float)
    y_tr = train_df["label"].to_numpy().astype(int)
    x_te = test_df.select(feature_names).to_numpy().astype(float)
    y_te = test_df["label"].to_numpy().astype(int)
    print(f"train n={len(y_tr)} pos={y_tr.sum()} | test n={len(y_te)} pos={y_te.sum()} | features={len(feature_names)}")
    if y_te.sum() == 0 or y_tr.sum() == 0:
        raise SystemExit("not enough positive samples; extend the period/routes before training")

    # rule baseline (no fitting)
    rows_te = test_df.select(feature_names).to_dicts()
    p_rule = np.array([rule_baseline_probability(r) for r in rows_te])

    candidates: dict[str, Pipeline] = {
        "logreg": Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                (
                    "clf",
                    CalibratedClassifierCV(
                        LogisticRegression(max_iter=2000, class_weight="balanced", C=0.5), method="sigmoid", cv=5
                    ),
                ),
            ]
        ),
        "hgb": Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                (
                    "clf",
                    CalibratedClassifierCV(
                        HistGradientBoostingClassifier(
                            max_depth=4, learning_rate=0.05, max_iter=300, class_weight="balanced"
                        ),
                        method="isotonic",
                        cv=5,
                    ),
                ),
            ]
        ),
    }
    # Operating points are derived ONLY from out-of-fold predictions on the TRAINING split
    # (objective: HIGH recall >= 0.80, MEDIUM recall >= 0.95). The test split is never used for this.
    derived: dict[str, dict[str, float]] = {}
    results: dict[str, Any] = {}
    fitted: dict[str, Pipeline] = {}
    for name, pipe in candidates.items():
        oof = cross_val_predict(
            pipe, x_tr, y_tr, cv=StratifiedKFold(5, shuffle=True, random_state=42), method="predict_proba"
        )[:, 1]
        derived[name] = derive_thresholds(y_tr, oof, high_recall=0.80, medium_recall=0.95)
        pipe.fit(x_tr, y_tr)
        p = pipe.predict_proba(x_te)[:, 1]
        m = metrics(y_te, p, derived[name]["high"])
        m["thresholds"] = derived[name]
        # subgroup report by region/mode
        sub: dict[str, Any] = {}
        for key in ("region", "mode"):
            for val in sorted(set(test_df[key].to_list())):
                mask = (test_df[key] == val).to_numpy()
                if mask.sum() >= 20 and y_te[mask].sum() > 0:
                    sub[f"{key}={val}"] = {
                        "n": int(mask.sum()),
                        "high_recall": metrics(y_te[mask], p[mask], thresholds.high)["high_recall"],
                    }
        m["subgroups"] = sub
        results[name] = m
        fitted[name] = pipe
        print(
            f"{name}: recall={m['high_recall']:.3f} pr_auc={m['pr_auc']:.3f} roc_auc={m['roc_auc']:.3f} brier={m['brier']:.3f} ece={m['ece']:.3f}"
        )

    # rule baseline evaluated at the chosen candidate's operating point (same threshold => fair comparison)
    chosen = "logreg"
    if (
        results["hgb"]["high_recall"] >= results["logreg"]["high_recall"] + 0.03
        and results["hgb"]["pr_auc"] >= results["logreg"]["pr_auc"] + 0.03
    ):
        chosen = "hgb"
    m = results[chosen]
    # rule baseline gets its own operating point derived on the training split (same objective)
    p_rule_tr = np.array([rule_baseline_probability(r) for r in train_df.select(feature_names).to_dicts()])
    rule_thr = derive_thresholds(y_tr, p_rule_tr, high_recall=0.80, medium_recall=0.95)
    rule_m = metrics(y_te, p_rule, rule_thr["high"])
    rule_m["thresholds"] = rule_thr
    results["rule_baseline"] = rule_m
    failures = []
    for k, v in acceptance["minimums"].items():
        if m[k] < v:
            failures.append(f"{k}={m[k]:.3f} < {v}")
    for k, v in acceptance["maximums"].items():
        if m[k] > v:
            failures.append(f"{k}={m[k]:.3f} > {v}")
    for k in acceptance["must_beat_rule_baseline_on"]:
        if m[k] < rule_m[k]:
            failures.append(f"{k} does not beat rule baseline ({m[k]:.3f} < {rule_m[k]:.3f})")
    stage = "CANDIDATE" if failures else "APPROVED"

    out_dir = args.artifacts / args.version
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.joblib"
    joblib.dump(fitted[chosen], model_path)
    checksum = hashlib.sha256(model_path.read_bytes()).hexdigest()
    thresholds_doc = {
        "version": args.version,
        "derived_from": "out-of-fold predictions on the training split (StratifiedKFold 5)",
        "objective": {"high": "recall >= 0.80", "medium": "recall >= 0.95"},
        "high": round(derived[chosen]["high"], 4),
        "medium": round(derived[chosen]["medium"], 4),
        "uncertainty_band": thresholds.uncertainty_band,
        "delay_improvement_min": thresholds.delay_improvement_min,
    }
    (out_dir / "thresholds.yaml").write_text(yaml.safe_dump(thresholds_doc, sort_keys=False), encoding="utf-8")
    model_manifest = {
        "name": "route-risk-baseline",
        "version": args.version,
        "algorithm": chosen,
        "stage": stage,
        "feature_schema_version": manifest.get("feature_schema_version") or "1.0.0",
        "feature_names": feature_names,
        "checksum": checksum,
        "thresholds_version": args.version,
        "thresholds": thresholds_doc,
        "metrics": {k: v for k, v in m.items() if k not in ("subgroups",)},
        "subgroups": m["subgroups"],
        "all_candidates": {k: {kk: vv for kk, vv in v.items() if kk != "subgroups"} for k, v in results.items()},
        "acceptance": acceptance,
        "acceptance_failures": failures,
        "dataset_manifest": {
            k: manifest[k]
            for k in (
                "built_at",
                "period",
                "routes",
                "split",
                "stats",
                "dataset_sha256",
                "rows",
                "positive_rate",
                "sources",
            )
        },
        "label_policy_version": manifest["label_policy"]["version"],
        "trained_at": datetime.now(UTC).isoformat(),
        "approved_by": None,
        "approved_at": None,
    }
    (out_dir / "manifest.json").write_text(json.dumps(model_manifest, indent=2), encoding="utf-8")
    card = (ROOT / "training" / "model_card_template.md").read_text(encoding="utf-8")
    for k, v in {
        "VERSION": args.version,
        "ALGORITHM": chosen,
        "TRAINED_AT": model_manifest["trained_at"],
        "N_TRAIN": len(y_tr),
        "N_TEST": len(y_te),
        "POS_TRAIN": int(y_tr.sum()),
        "POS_TEST": int(y_te.sum()),
        "METRICS": json.dumps(model_manifest["metrics"], indent=2),
        "RULE_METRICS": json.dumps({k: v for k, v in rule_m.items() if k != "subgroups"}, indent=2),
        "SUBGROUPS": json.dumps(m["subgroups"], indent=2),
        "FAILURES": "\n".join(f"- {f}" for f in failures) or "- none",
        "STAGE": stage,
        "DATASET_SHA": manifest["dataset_sha256"],
        "ROUTES": ", ".join(manifest["routes"]),
        "PERIOD": json.dumps(manifest["period"]),
        "CHECKSUM": checksum,
    }.items():
        card = card.replace("{{" + k + "}}", str(v))
    (out_dir / "MODEL_CARD.md").write_text(card, encoding="utf-8")
    print(json.dumps({"chosen": chosen, "stage": stage, "failures": failures, "checksum": checksum}, indent=2))

    if args.promote:
        if failures:
            raise SystemExit("refusing to promote: acceptance criteria not met")
        model_manifest["stage"] = "ACTIVE"
        model_manifest["approved_by"] = args.approver
        model_manifest["approved_at"] = datetime.now(UTC).isoformat()
        (out_dir / "manifest.json").write_text(json.dumps(model_manifest, indent=2), encoding="utf-8")
        cur = args.artifacts / "current"
        if cur.exists():
            shutil.rmtree(cur)
        shutil.copytree(out_dir, cur)
        # the serving thresholds are the ones this model was accepted with
        shutil.copyfile(out_dir / "thresholds.yaml", ROOT / "config" / "thresholds.yaml")
        print(f"promoted {args.version} -> ACTIVE at {cur}; config/thresholds.yaml updated to v{args.version}")


if __name__ == "__main__":
    main()
