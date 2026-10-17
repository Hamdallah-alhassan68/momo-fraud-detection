from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parent

MODEL_DIR = Path(
    os.getenv("MOMO_MODEL_DIR", BASE_DIR / "models" / "paysim-runtime")
).resolve()

ISOLATION_FOREST_PATH = MODEL_DIR / "isolation_forest_model.joblib"
SCALER_PATH = MODEL_DIR / "robust_scaler.joblib"
MEDIANS_PATH = MODEL_DIR / "train_medians.joblib"
CONFIG_PATH = MODEL_DIR / "hybrid_model_config.json"