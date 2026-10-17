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

## Deploying the frontend

The repository includes a Render Blueprint at `render.yaml` for the FastAPI
backend. Push the repository to GitHub, create a new Blueprint in Render, and
select that repository. During setup, enter `FRONTEND_ORIGINS` as the exact
Vercel frontend origin, such as `https://<project>.vercel.app` (no trailing
slash). The Blueprint selects Python 3.13.7, installs `backend/requirements.txt`,
starts Uvicorn on Render's assigned port, generates persistent authentication
and audit secrets, and stores SQLite at `/var/data/fraud.db` on a 1 GB
persistent disk. Render requires a paid web-service plan for persistent disks.
Keep the service at one instance while using its SQLite database and
in-process event stream.

Once the backend deploy finishes, copy its public HTTPS origin from Render.
For a Vercel frontend deployment, set the project root directory to `frontend`,
use `npm run build` as the build command, and `dist` as the output directory.
Add this Vercel environment variable for the production, preview, and
development environments you use:

```text
VITE_API_URL=https://<your-deployed-backend-origin>
```

Use the backend origin only (no `/api` suffix). The frontend defaults to
`http://127.0.0.1:5001` for local development when this variable is unset.
Redeploy the frontend after changing Vercel environment variables.

The FastAPI backend must be deployed separately. Configure its
`FRONTEND_ORIGINS` with the exact Vercel site origin(s), comma-separated, for
example `https://<project>.vercel.app,https://<custom-domain>`. Set stable,
random server-side secrets for `MOMO_AUTH_SECRET` and `MOMO_AUDIT_HMAC_KEY`;
never put these secrets in Vercel's `VITE_*` variables, which are exposed to
the browser. `MOMO_MODEL_DIR` is optional if the default
`backend/models/paysim-runtime` artifacts are included with the backend.

This is a public demo prototype with documented, built-in demo accounts. Do
not use real customer data, real payment credentials, or treat the deployment
as a production fraud-control service. A production deployment needs
deployment-specific access controls and a security review. SQLite is
configured for Render's persistent disk; do not move the API to stateless
serverless functions without migrating the database and event-stream design.

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
