# Backend development setup

The backend is part of a local fraud-detection simulator and admin dashboard.
This is a research prototype, not a production financial service. It has not
been validated on real customer transactions or reviewed for regulatory
compliance.

## Python environment

The checked-in model artifacts were serialized with scikit-learn 1.6.1, which
is pinned in `requirements.txt`. Use Python 3.13 for the backend environment;
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

Current evaluation data is synthetic PaySim data. Do not use the reported
metrics as real-world performance evidence, and do not load experimental
artifacts or thresholds into the shipped runtime as calibrated settings.
Further performance validation requires an appropriately authorized,
representative, de-identified dataset with reliable fraud outcomes. See
[ARCHITECTURE.md](../ARCHITECTURE.md) for evaluation results, known gaps, and
prototype limitations.
