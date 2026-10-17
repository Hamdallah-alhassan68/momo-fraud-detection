# Backend development setup

The backend is part of a local fraud-detection simulator and admin dashboard.
This is a research prototype, not a production financial service. It has not
been validated on real customer transactions or reviewed for regulatory
compliance.

## Python environment

The default runtime loads `models/paysim-runtime`, a scikit-learn 1.6.1
Isolation Forest trained from the local synthetic PaySim dataset using a
seeded chronological split. Its evaluation manifest records the dataset hash,
seed, split ranges, holdout metrics, and library version. Use Python 3.13 for
the backend environment;
Python 3.14 may not have a compatible wheel for that pinned scikit-learn
release, and loading the artifacts with another scikit-learn version can emit
compatibility warnings.

From the repository root in PowerShell, create a separate environment so an
existing `.venv` is not overwritten:

```powershell
py -3.13 -m venv .venv-py313
.\.venv-py313\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Set-Location backend
python -m unittest discover -s tests
```

The backend tests were verified with Python 3.13 and scikit-learn 1.6.1. If
the Python launcher does not expose Python 3.13, create the environment with
the full path to that interpreter instead:

```powershell
C:\Path\To\Python313\python.exe -m venv .venv-py313
```

## Validation limitations

The runtime evaluation data is synthetic PaySim data. Its reported metrics
describe both the Isolation Forest and the hybrid policy on a fixed temporal
holdout; they are not real-world performance evidence or a calibration
guarantee for customer behavior or security signals absent from PaySim. The
current runtime MEDIUM boundary is 0.45 and HIGH boundary is 0.525. On the
fixed temporal test split, the hybrid MEDIUM-or-higher policy reports 17.3%
precision, 71.4% recall, and a 2.7% false-positive rate; HIGH reports 23.4%
precision, 36.4% recall, and a 0.9% false-positive rate. Compared with the
previous 0.4/0.5 boundaries, the holdout false-positive rates fell from 5.5%
to 2.7% for MEDIUM-or-higher and from 1.3% to 0.9% for HIGH, with recall
falling from 73.8% to 71.4% and 43.6% to 36.4%, respectively. These remain
prototype policy thresholds selected from synthetic validation evidence, not
production-calibrated settings. The dashboard's threshold curve uses the
validation split and does not apply its what-if cutoff to live decisions. The
four cumulative performance windows in Adaptive Intelligence replay labelled
rows from this same held-out test period in chronological order; they
demonstrate metric calculation as synthetic outcomes accumulate, not live
incoming transactions or live fraud labels.
Further performance validation requires an appropriately authorized,
representative, de-identified dataset with reliable outcomes. See
[ARCHITECTURE.md](../ARCHITECTURE.md) for evaluation results, known gaps, and
prototype limitations.
