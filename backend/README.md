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

The Blueprint selects MTN sandbox and disables the bundled demo-admin accounts
for hosted deployments. Set SMTP settings in Render only if using optional
email-ownership verification. MEDIUM-risk transfer verification uses the
security code selected at signup, not an emailed transaction code. Use a
dedicated SMTP sender/mailbox and verify its domain authentication before
testing email delivery. Provision named
analyst/admin users through the protected operator workflow; never publish
operator or MTN credentials. Keep MTN sandbox selected while public testers
are using the application.
Production Disbursement requires separate MTN production credentials and an
explicit, private operator setup; do not enable it for a public testing site.
The hosted Blueprint sets `MOMO_SIMULATION_ONLY=true`: wallet transfers change
only demo balances, and all MTN Collection/Disbursement request and status
endpoints are disabled. Do not add MTN credentials or a callback URL for this
pilot. The application does not currently implement an MTN callback receiver.

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

Before inviting testers, set the required Render secrets in its dashboard and
obtain MTN-issued sandbox test numbers for wallet enrollment. MTN API
credentials and a callback URL are not needed while the hosted app is
simulation-only. Verify SMTP delivery only if optional email-ownership
verification is enabled; SMTP is not needed for MEDIUM-risk transfer
verification. The frontend and backend must use the exact deployed origins and
HTTPS URLs. Never send provider credentials in chat or commit them to the
repository.

Complete a privacy/legal review before a public pilot. The Ghana Data
Protection Commission's [registration guidance](https://dpc.gov.gh/registration)
states that data controllers and processors must register; confirm whether and
how that applies to the pilot with the Commission or qualified counsel.
Synthetic identity details do not remove obligations for any real email
address, IP address, or device identifier.

The pilot operator must record a close date and schedule data removal. Delete
the hosted SQLite database no later than 30 days after that date, and account
for Render disk snapshots and any email/provider logs or backups. Render's
[disk documentation](https://render.com/docs/disks#disk-snapshots) says
snapshots are retained for seven days, so if all copies must be gone by day
30, remove the primary data by day 23 or confirm snapshot deletion timing with
Render. Self-service account deletion removes the
sandbox profile, security-signal history, and active MoMo sessions immediately;
it cannot rewrite the append-only audit chain or linked transaction rows.

## MTN Disbursement API

The sandbox customer flow supports sign-in to the bundled demo registry or
direct enrollment of a new sandbox wallet. Enrollment requires acceptance of
the sandbox privacy notice and collects a fictional name, MTN-issued sandbox
test number, synthetic passport/Ghana Card identifier, controlled test email,
demo PIN, and a separate six-digit security code. Do not enter real identity
documents or another person's data.
Email is stored without ownership verification at registration; the
passport/ID value, PIN, and security code are stored as salted hashes. This
prototype does not validate documents with an official identity provider.
Customers can delete
their sandbox profile and security-signal history in the UI. Linked
transaction/audit records remain until the pilot database is erased, no later
than 30 days after the pilot ends, because the audit log is append-only. The
pilot operator must schedule and perform that database wipe, including
applicable backups and provider logs.

The sender is taken from the authenticated session; the transfer form asks for
the recipient and amount. Wallet-to-wallet transfers require the wallet PIN.
On the hosted simulation-only pilot, transactions update only demo balances
and never call MTN. A separately configured local environment may use MTN
Disbursement sandbox (`sandbox` target, EUR).
Bank-to-wallet and wallet-to-bank flows are local simulations only and use a
demo OTP; they do not call MTN or move money.

The fraud layer learns behavioral baselines only from completed transactions.
Device identity is a persistent browser-generated ID and IP is the address
observed by the backend. Coarse location is optional; the browser requests it
only after an explicit opt-in, rounds coordinates to two decimal places before
sending them, and the backend rejects coordinates without consent. Device,
network, and opted-in location observations are stored in SQLite and compared
with previously trusted enrollment/successful-transfer signals. IP may identify
a reverse proxy when deployed behind one. These signals do not provide carrier
or SIM-swap data. Signal values are not shown in the customer UI.
HIGH-risk transactions are blocked. MEDIUM-risk transfers require the saved
six-digit security code or, for passport profiles, the registered synthetic
passport number. The security code is stored as a salted password hash, is
never emailed, and is requested only after a transaction is rated MEDIUM.
Existing self-enrolled wallets without a code can set one in the transfer
screen after confirming their wallet PIN. Enrollment does not require email
ownership verification. SMTP is not required for MEDIUM-risk step-up.

The frontend does not impose an arbitrary amount cap. The backend enforces the
provider environment's configured transfer limit (EUR 10,000 for this sandbox
client and GHS 5.00 for the current production test configuration). An MTN
`202 Accepted` response is PENDING, not proof that the recipient was credited.
Use **Check MTN transfer status** to reconcile the same provider reference;
the local demo balance changes only after MTN reports success. If a submission
times out or its result is uncertain, check that reference. The backend rejects
another live transfer for that wallet until the pending result is reconciled,
preventing accidental duplicate sends.

The separate admin-authorized Disbursement endpoint assesses the requested
amount and recipient before contacting MTN. HIGH risk is blocked, MEDIUM risk
requires step-up, and live transfers require explicit operator authorization.
Collection is a different product flow: it charges a payer and credits a
merchant, while Disbursement pays a recipient.

The Risk Scenario Simulator on the Send MoMo page compares a normal baseline
with selected synthetic signals: new device, outside-usual pattern, IP/network
mismatch, new recipient, recent SIM change/SIM swap, velocity spike, and
frequency spike. It never calls MTN or moves money. These are injected test
signals, not live customer/device/network/telecom data. Real detection of those
signals requires trusted telemetry and customer history to be integrated before
the production assessment can rely on them.

For local sandbox testing, run `.\backend\run_sandbox.ps1` from the repository
root. It prompts for the three sandbox credentials without echoing them and
keeps them only in the backend process environment. The script selects the
sandbox endpoint and target environment; MTN's token request uses HTTP Basic
authentication, and transfer requests use the returned Bearer token and a
fresh UUID reference per transfer request. Run the frontend separately with
`npm run dev` from `frontend`, then open `http://localhost:5173`. Sandbox
credentials are separate from production and Collection credentials.
Set `MTN_DISBURSEMENT_CALLBACK_URL` in the backend process environment to an
operator-controlled HTTPS URL before using sandbox transfers.

The sandbox launcher requests `MTN_SANDBOX_DISBURSEMENT_SUBSCRIPTION_KEY`,
`MTN_SANDBOX_DISBURSEMENT_API_USER`, and
`MTN_SANDBOX_DISBURSEMENT_API_KEY`. Sandbox is the backend's default when no
environment is selected; Render explicitly selects sandbox and the production
launcher selects production.

Transfer requests include MTN's `X-Callback-Url` header, but this application
currently reconciles results by polling and does not expose an MTN callback
handler. Do not use a public request-inspection service for the callback URL.
Confirm the MTN sandbox's callback requirements with the provider and use an
operator-controlled HTTPS endpoint. The Render Blueprint leaves this variable
unset and reports Disbursement as unconfigured until it is set. Implement and
validate a callback handler before relying on callback notifications.

Production Disbursement must be enabled for the merchant account and Ghana by
MTN. It requires its own production Disbursement subscription key, API User ID,
and API key. The production-only launcher prompts for these secrets without
echoing them and keeps them only in the backend process environment:

```text
MTN_PRODUCTION_DISBURSEMENT_SUBSCRIPTION_KEY=<production Disbursement key>
MTN_PRODUCTION_DISBURSEMENT_API_USER=<production Disbursement API user UUID>
MTN_PRODUCTION_DISBURSEMENT_API_KEY=<production Disbursement API key>
MOMO_ADMIN_TOKEN=<unique secret of at least 32 characters>
```

For local production testing on Windows, stop the running backend with Ctrl+C,
then run `.\backend\run_local.ps1` from the repository root. Type `REAL` and
enter the three production Disbursement credentials provided by MTN plus a
unique admin token of at least 32 characters. Keep that terminal open while
testing. In the app, choose **Direct Token** sign-in and enter that admin token;
demo administrator accounts cannot authorize transfers. Restarting the backend
clears these process-only credentials, so enter them again. Never put secrets
in frontend variables, source control, or chat messages.

The fraud gate is an integration prototype, not a production fraud-control
service. The detector was trained on synthetic PaySim data and is not
calibrated for real MTN Ghana activity. Sandbox profile balances and identity
details are demo data; the browser identifier can be changed or spoofed, and
the server-observed IP may be a proxy address. No carrier/SIM-swap feed,
authoritative identity provider, or production account history is connected.
HIGH-risk results block a transfer, but LOW/MEDIUM results do not prove a
transfer is safe. Review recipient and amount independently. This flow is for
sandbox testing only, not production authentication or customer onboarding.

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

## Adaptive monitoring and signal limits

Adaptive Intelligence monitors scored transactions and records risk-signal
rates, pending step-up activity, analyst outcomes, and offline model-release
history. Customer traffic and analyst feedback do not currently retrain the
active detector; new model versions must be produced and evaluated offline.
The page reports training metadata as unavailable when an artifact does not
contain a reliable training timestamp rather than inferring one.

For deployments behind a reverse proxy, set `TRUSTED_PROXY_IPS` to a
comma-separated list of the proxy IP addresses or CIDR ranges. Forwarded client
addresses are considered only when the connecting peer is in that list.
Without a trusted proxy configuration, the service uses the direct peer
address. Invalid/unavailable peer addresses are omitted rather than treated as
customer input or causing a transaction request to fail.

Device identifiers and coarse location/network observations are supporting
signals, not proof of identity. Browser identifiers can be reset or copied,
and GPS/IP-derived locations can be inaccurate or spoofed. SIM-change signals
in the sandbox are explicit test inputs; no carrier signal is integrated.
Only trusted enrollment and successful-transfer observations contribute to
trusted device/location history, while newly observed values are retained as
untrusted sightings.
