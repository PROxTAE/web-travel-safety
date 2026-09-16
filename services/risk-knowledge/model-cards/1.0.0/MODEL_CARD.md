# Model Card — route-risk-baseline v1.0.0

- Algorithm: `logreg` (sklearn Pipeline: median imputer → scaler → calibrated classifier)
- Stage: **CANDIDATE** · trained at 2026-09-16T23:26:45.036968+00:00 · artifact sha256 `862ffe244912a91c3279996f5a74489f6bb312fa69fe732057673dc5eed7bebb`
- Feature schema: v1.0.0 (`services/data-integration/config/feature_schema.yaml`), produced by the same online pipeline

## Intended use

Predicts the probability that a planned route/time will experience hazardous conditions (as defined by
`training/label_policy.yaml`) from day-ahead forecast + known events. Output is **evidence** for the decision
engine; it never selects the action. Deterministic safety overrides (`config/overrides.yaml`) apply after it.

## Data

- Routes: bkk-cnx, bkk-hkt, bkk-kkc, bkk-hhq, han-dad, mnl-ceb, jkt-bdo, kul-pen, tyo-osa, tpe-khh, sel-pus, del-jai, bom-pnq, dac-cgp, lax-sfo, mia-mco, hou-dfw, mex-pbc, lon-man, mad-bcn, ath-the, syd-cbr, nbo-mba, lim-cuz
- Period: {"start": "2025-11-01", "end": "2026-08-31"} · dataset sha256 `b84d518531f63648a3f2cea052c044a7187e75c0ddd85913bd64d55dafecdde5`
- Features: Open-Meteo Previous Runs (day-1 forecast), USGS quakes (prior 24 h), GDACS alerts published before departure
- Labels: Open-Meteo Archive actuals during the travel window, GDACS Orange/Red on corridor, USGS M≥5.5 in window
  (documented weak supervision — no human-reviewed labels in v1)
- Split: geography (held-out regions) AND time (dates ≥ split date). Train n=3620 (pos 65), test n=3676 (pos 85)

## Metrics (held-out split, threshold = thresholds.yaml `high`)

```json
{
  "high_recall": 0.6470588235294118,
  "high_recall_ci95": [
    0.541,
    0.74
  ],
  "high_precision": 0.0514018691588785,
  "f1": 0.09523809523809523,
  "false_negative_rate": 0.35294117647058826,
  "pr_auc": 0.29266712126899724,
  "roc_auc": 0.7899094140580208,
  "brier": 0.019187874799121225,
  "ece": 0.004995895990039493,
  "confusion": {
    "tn": 2576,
    "fp": 1015,
    "fn": 30,
    "tp": 55
  },
  "n": 3676,
  "n_positive": 85,
  "thresholds": {
    "high": 0.0139,
    "medium": 0.0084
  }
}
```

Rule-only baseline on the same split:

```json
{
  "high_recall": 0.8470588235294118,
  "high_recall_ci95": [
    0.756,
    0.908
  ],
  "high_precision": 0.05333333333333334,
  "f1": 0.10034843205574913,
  "false_negative_rate": 0.15294117647058825,
  "pr_auc": 0.16523371408186016,
  "roc_auc": 0.8218012351139287,
  "brier": 0.026570768998911858,
  "ece": 0.05947894450489664,
  "confusion": {
    "tn": 2313,
    "fp": 1278,
    "fn": 13,
    "tp": 72
  },
  "n": 3676,
  "n_positive": 85,
  "thresholds": {
    "high": 0.0567,
    "medium": 0.034
  }
}
```

Subgroups (region / mode with ≥ 20 samples):

```json
{
  "region=EAST_ASIA": {
    "n": 369,
    "high_recall": 0.0
  },
  "region=EUROPE": {
    "n": 369,
    "high_recall": 0.0
  },
  "region=NORTH_AMERICA": {
    "n": 492,
    "high_recall": 0.0
  },
  "region=OCEANIA": {
    "n": 304,
    "high_recall": 0.0
  },
  "region=SEA": {
    "n": 984,
    "high_recall": 0.0
  },
  "region=SOUTH_ASIA": {
    "n": 912,
    "high_recall": 0.0
  },
  "mode=BUS": {
    "n": 427,
    "high_recall": 0.0
  },
  "mode=CAR": {
    "n": 1773,
    "high_recall": 0.0
  },
  "mode=TRAIN": {
    "n": 1230,
    "high_recall": 0.0
  }
}
```

Acceptance failures: 
- high_recall=0.647 < 0.8
- pr_auc=0.293 < 0.35
- false_negative_rate=0.353 > 0.2
- high_recall does not beat rule baseline (0.647 < 0.847)

## Limitations

- Labels are threshold-based weak supervision; they capture *hazardous weather / official alerts*, not accidents.
- Route geometry in training is great-circle (same as runtime fallback); road-specific exposure is not modelled.
- Regions outside the training set may be miscalibrated; overrides guarantee official warnings still raise risk.
- Not to be retrained automatically from user feedback (feedback is exported for offline review only).
