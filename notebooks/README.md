# notebooks/ — Model Training (production path)

This directory is reserved for the **offline model-training pipeline** that
produces the deployed artifacts in `backend/models/`:

- `isolation_forest_model.joblib`
- `robust_scaler.joblib`
- `train_medians.joblib`
- `hybrid_model_config.json`

In the design document the model is trained on the **PaySim** synthetic
dataset (Lopez-Rojas et al., 2016) and is periodically **re-trained** when the
Adaptive Fraud Intelligence module detects distribution drift (BoG, 2019). For
this prototype the training step was performed externally; the runtime API
loads the frozen V3 artifacts directly from `backend/models/`.

Planned contents (future):
- `train_and_export.py` — feature engineering → Isolation Forest fit → export.
- `retrain_trigger.py` — consumes `/api/admin/monitoring` drift signals and
  schedules a re-fit.
