# Model Card — route-risk-baseline v{{VERSION}}

- Algorithm: `{{ALGORITHM}}` (sklearn Pipeline: median imputer → scaler → calibrated classifier)
- Stage: **{{STAGE}}** · trained at {{TRAINED_AT}} · artifact sha256 `{{CHECKSUM}}`
- Feature schema: v1.0.0 (`services/data-integration/config/feature_schema.yaml`), produced by the same online pipeline

## Intended use

Predicts the probability that a planned route/time will experience hazardous conditions (as defined by
`training/label_policy.yaml`) from day-ahead forecast + known events. Output is **evidence** for the decision
engine; it never selects the action. Deterministic safety overrides (`config/overrides.yaml`) apply after it.

## Data

- Routes: {{ROUTES}}
- Period: {{PERIOD}} · dataset sha256 `{{DATASET_SHA}}`
- Features: Open-Meteo Previous Runs (day-1 forecast), USGS quakes (prior 24 h), GDACS alerts published before departure
- Labels: Open-Meteo Archive actuals during the travel window, GDACS Orange/Red on corridor, USGS M≥5.5 in window
  (documented weak supervision — no human-reviewed labels in v1)
- Split: geography (held-out regions) AND time (dates ≥ split date). Train n={{N_TRAIN}} (pos {{POS_TRAIN}}), test n={{N_TEST}} (pos {{POS_TEST}})

## Metrics (held-out split, threshold = thresholds.yaml `high`)

```json
{{METRICS}}
```

Rule-only baseline on the same split:

```json
{{RULE_METRICS}}
```

Subgroups (region / mode with ≥ 20 samples):

```json
{{SUBGROUPS}}
```

Acceptance failures: 
{{FAILURES}}

## Limitations

- Labels are threshold-based weak supervision; they capture *hazardous weather / official alerts*, not accidents.
- Route geometry in training is great-circle (same as runtime fallback); road-specific exposure is not modelled.
- Regions outside the training set may be miscalibrated; overrides guarantee official warnings still raise risk.
- Not to be retrained automatically from user feedback (feedback is exported for offline review only).
