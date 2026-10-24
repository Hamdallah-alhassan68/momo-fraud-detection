# System Architecture — MoMo Fraud Detection & Adaptive Control Layer

> Status: research prototype with implemented transaction controls and
> dashboard; several design requirements remain partially implemented or
> unverified. See the gap analysis in section 6.

For the project’s full system-analysis hand-in—use-case, activity, sequence,
class, database/ERD, misuse-case, and STRIDE threat diagrams—see
[SYSTEM_ANALYSIS_AND_DESIGN.md](./SYSTEM_ANALYSIS_AND_DESIGN.md).

## 1. Architecture overview

```
 ┌────────────────────────────────────────────────────────────────────┐
 │                        CUSTOMER (MoMo Simulator)                   │
 │   Wallet-to-Wallet · Wallet-to-Bank · Bank-to-Wallet               │
 │   PIN (W2W) / OTP (W2B, B2W) · self-service account freeze         │
 └───────────────┬────────────────────────────────────────────────────┘
                 │  HTTP JSON
                 ▼
 ┌────────────────────────────────────────────────────────────────────┐
 │                           API LAYER (FastAPI)                      │
 │  /api/momo/*            ── simulated MoMo core / session flow      │
 │  /api/transactions/score ── direct scoring (PaySim-style input)    │
 │  /api/admin/*           ── cases, contact, account controls, audit │
 │  /api/events/stream     ── Server-Sent Events (live push)          │
 └───────┬───────────────────────────┬────────────────────────────────┘
         ▼                           ▼
 ┌──────────────────────┐   ┌──────────────────────────────────────────┐
 │  FRAUD INTELLIGENCE  │   │     DATA LAYER (SQLite fraud.db)          │
 │  feature engineering │   │  transactions · risk_assessments          │
 │  Isolation Forest +  │──▶│  fraud_alerts · fraud_cases · audit_logs  │
 │  rule heuristics     │   │  momo_sessions · account_status           │
 │  hybrid risk score   │   │  model_monitoring (AFI snapshots)         │
 └──────────────────────┘   └──────────────────────────────────────────┘
         │
         ▼
 ┌────────────────────────────────────────────────────────────────────┐
 │   AUTOMATED CONTROLS                                                │
 │   LOW → approve · MEDIUM → simulated registered-email step-up       │
 │   HIGH → block + auto fraud case + session freeze                   │
 │   CUSTOMER REQUEST → self-service account freeze (PIN confirmed)    │
 └────────────────────────────────────────────────────────────────────┘
         │
         ▼
 ┌────────────────────────────────────────────────────────────────────┐
 │   ADMIN CONSOLE (React SPA, live over SSE)                          │
 │   LiveDashboard (KPIs · charts · risk signals · IP/device)          │
 │   Adaptive Intelligence (drift PSI · outcome feedback · baselines)   │
 │   Case management · Customer-contact simulation · Account controls │
 │   Fraud alerts · IP/device · Append-only audit logs                 │
 └────────────────────────────────────────────────────────────────────┘
```

## 2. Design-document → implementation mapping

| Requirement | Current implementation | Gap / status |
|---|---|---|
| Post-authentication behavioural layer | PIN/OTP precedes scoring in `momo_simulator.py`; low approves, medium challenges, high blocks and opens a case; explicit behavioral/security signals set a minimum medium-risk score | Implemented in the simulator flow; direct scoring endpoint is assessment-only and does not execute transaction controls. Thresholds still require calibration against labelled data. |
| Customer baseline (amount, timing, frequency, recipient, velocity) | Historical customer transaction records provide amount, Ghana-local transaction hour, daily frequency and per-customer recipient counts; velocity counts sessions opened within the configured window | Implemented using available prototype history and normalized to Africa/Accra time. Sparse/new-account history remains a cold-start limitation, and the small fixed demo population is not representative. |
| Isolation Forest trained on PaySim | The default runtime loads the model, scaler, medians, and evaluation manifest from `backend/models/paysim-runtime`; the manifest identifies the local PaySim dataset hash, scikit-learn 1.6.1, seed 42, and chronological split. The training pipeline reproduces the run. | The active model's Isolation Forest holdout metrics are visible in Adaptive Intelligence. This is synthetic-data evidence only and does not establish real-customer performance or calibrate the separate hybrid policy. |
| Security signals | Device, IP/network, SIM-swap, velocity and unusual-hour signals are explicitly selected through repeatable simulator scenarios; typed IP inputs are also evaluated against the demo baseline | Synthetic signals only. These inputs do not originate from a telecom or network provider and must not be described as live telemetry. |
| Medium-risk step-up | Medium-risk sessions request a second simulated code for the demo customer's pre-registered, verified email; no verified email causes a fail-closed block | No email or SMS is actually sent. The demo code is shown in the UI and is not a secure production factor. SIM-swap scenarios must not rely on the affected phone number. |
| Case management, account freeze, and audit trail | Analysts save an investigation decision, then explicitly mark the investigation complete; the admin case list shows new cases in red, active investigations in yellow, and completed investigations in green; resolved cases leave the active review count but remain visible in case history; account freeze/unfreeze is an independent wallet-level action (restricted to admin role) and never changes investigation status; a confirmed HIGH-risk case may be frozen without waiting for customer contact, while lower-risk freezes require recorded simulated consent; the call demo rings audibly/visually inside the admin browser; audit logs record operator identity, timestamps, previous SHA-256 hash, entry hash, and HMAC signature with automated chain verification | No real phone call or SMS is sent. Append-only triggers prevent ordinary SQL UPDATE/DELETE, and cryptographic SHA-256 hash chaining with HMAC signatures detects tampering and gap insertion. Full production deployment would further forward signed audit entries to remote immutable/WORM storage. |
| Adaptive intelligence | PSI compares transaction-level amount and risk-score distributions against a captured reference (or, until captured, older/recent halves of a rolling sample); at least 10 values per side are required. Warnings start at 0.10 and critical alerts at 0.25. The dashboard shows fixed PaySim holdout metrics, four chronological labelled test-window slices with cumulative replay metrics, four-seed policy sensitivity, validation-split threshold sensitivity, and a 14-day trend of resolved analyst case outcomes. A volume threshold recommends baseline review after 50 new transactions. | Capturing a new reference resets the PSI comparison and volume counter, but does not retrain or change the model. The labelled performance replay is computed offline from the fixed synthetic PaySim test split; it demonstrates performance-metric calculation as labelled outcomes accumulate in chronological windows, not a live incoming evaluation stream. Analyst outcomes are selection-biased because only investigated/flagged cases are labelled; they are not model precision or recall. Only amount and risk-score distributions are used for PSI. The what-if threshold control uses validation evidence and does not alter runtime policy. |
| Evaluation | The active PaySim model's 59,850-row later-period holdout (461 fraud labels) reports Isolation Forest precision 6.9%, recall 35.4%, and false-positive rate 3.7%. At current MEDIUM-or-higher boundary 0.45, the measured hybrid policy reports 17.3% precision, 71.4% recall, and 2.7% false-positive rate; at HIGH boundary 0.525 it reports 23.4% precision, 36.4% recall, and 0.9% false-positive rate. Relative to previous 0.4/0.5 boundaries, false-positive rates fell 52% and 27%, respectively, with associated recall trade-offs. | Preliminary synthetic-data evidence only; these figures are not real-world performance or calibration evidence. Analyst feedback is limited to resolved cases. The validation curve informs prototype thresholds; the later-period holdout evaluates the selected policy. |
| Administrative interface | Full-width overview, responsive navigation/footer, KPI/activity view, alerts/cases, separate adaptive and audit pages; monitoring distinguishes blocked transaction count from active admin-frozen and customer-frozen account counts, and reports resolved cases and mean risk score; protected by named-user accounts (Admin, Analyst, Viewer) with PBKDF2-HMAC-SHA256 salted hashing, HMAC session tokens, 15-minute lockout after 5 failed attempts, and role-based access control (RBAC) enforced on FastAPI routes and UI controls; legacy master token supported as development fallback | Implemented for prototype readiness with 3 distinct operational roles (Admin, Analyst, Viewer). Production deployment with real customer accounts would further require corporate SSO/OIDC integration, hardware MFA (FIDO2), TLS termination, and production secrets management. |
| MoMo simulation inputs | Recipient suggestions plus guided free-form fictional wallet, phone, and bank-reference IDs; editable, validated IPv4/IPv6 | Implemented for synthetic simulations; user-entered recipients do not connect to live wallets or networks. |
| API framework | FastAPI routes preserve the `/api/...` surface; Uvicorn serves HTTP and the authenticated SSE endpoint | Implemented for this prototype. Run locally; protect deployment with TLS and environment-managed secrets. |
| Regulatory traceability | Verified Bank of Ghana source metadata and official links are recorded in section 7 | Clause/page-level obligations have not been verified. The BoG Q1 2025 FinTech Sector Report is contextual, not a directive; do not treat this prototype as legally compliant or as regulatory reporting. |

### SIM-swap demonstration walkthrough

1. In the simulator, select a demo wallet with a verified demo email, choose a
   wallet-to-wallet transfer, and use a modest amount, a known device, and the
   wallet's usual IP/network. Keep other synthetic risk scenarios off.
2. Complete the normal transaction with the displayed demo PIN. This is the
   control run; note its decision and reasons.
3. Repeat the same setup and click **SIM-swap demo**. This injects only the
   `RECENT_SIM_SWAP` test flag; it does not query a telecom provider or verify
   that a real SIM was changed.
4. Confirm the PIN. The isolated signal should produce MEDIUM risk and request
   the additional simulated email code. Use the UI's displayed demo code, then
   point out the “Recent SIM change” reason and step-up decision in the result.
   The code is not emailed or sent by SMS; avoiding SMS to the affected number
   is part of the scenario being illustrated.
5. Explain that this demonstrates the prototype's response to a supplied SIM
   swap signal, not detection of a real SIM swap. Optionally use **Select all
   risks** to show compound-risk blocking and case creation, but distinguish
   that combined test from the single-signal SIM-swap demonstration.

### Labelled-performance replay walkthrough

1. Sign in as an administrator and open **Adaptive intelligence**.
2. In **Synthetic labelled-performance replay**, click **Start labelled replay**.
   The four chronological PaySim test windows appear in sequence; each reports
   the current window and cumulative MEDIUM+/HIGH precision, recall, false-
   positive rate, transaction count, and known fraud-label count.
3. Explain that labels are known only because this is a fixed, retrospective
   synthetic test split. This demonstrates metric calculation as labels accrue;
   it does not replay current customer traffic, feed labels back into the
   detector, retrain the model, or establish live customer performance.

For repeated offline policy sensitivity checks against the same synthetic data
source, with the current runtime policy cutoffs applied across each seeded
model run:

```powershell
py -3.14 scripts/evaluate_hybrid_vs_model_only.py ..\SYINTHETIC_FINANCIAL_DATASET\paysim.csv --models-dir models --policy-config models\hybrid_model_config.json --output reports\hybrid-policy-seed-stability.json
```

This retrains no runtime artifact. Each seed is a separate fitted model; the
report is diagnostic seed sensitivity, not a repeated test of the exact
deployed model or independent real-world validation. The current seed model
artifacts were serialized with scikit-learn 1.9.1; run this analysis with a
matching scikit-learn version to avoid incompatible model-loading warnings.
With the fixed 0.45/0.525 policy, the current four runs had MEDIUM+ FPRs of
2.43–2.78% and recall of 60.8–70.3%; HIGH FPRs were 0.82–0.95% and recall was
29.9–37.9%. These are ranges across separately fitted models on the same
synthetic source, not external validation.

## 3. HTTP API surface

| Method | Route | Role / Requirement | Purpose |
|---|---|---|---|
| GET | `/` · `/api/health` | Public | heartbeat |
| POST | `/api/momo/transaction/initiate` | Public | start txn → PIN/OTP challenge |
| POST | `/api/momo/transaction/verify` | Public | PIN/OTP + fraud layer decision |
| POST | `/api/momo/account/freeze` · `unfreeze` | Public (PIN) | self-service freeze (PIN) |
| GET | `/api/momo/customers` | Public | demo wallets + freeze status |
| POST | `/api/admin/login` | Public | authenticate named user (PBKDF2); returns HMAC bearer token & role profile |
| GET | `/api/admin/me` | Authenticated | return current user identity, role, and permissions |
| GET | `/api/admin/demo-users` | Public | list pre-configured demo user accounts for evaluation |
| POST | `/api/admin/auth` | Public (Header) | verify legacy configured local admin token |
| POST | `/api/transactions/score` | Analyst / Admin | authenticated direct scoring (matches V3 model) |
| GET | `/api/admin/fraud-cases` · PATCH `/api/admin/fraud-cases/{id}` | Viewer (GET) / Analyst (PATCH) | case list / review notes & decisions |
| POST | `/api/admin/fraud-cases/{id}/contact` | Analyst / Admin | record simulated contact outcome during an active review |
| POST | `/api/admin/fraud-cases/{id}/freeze-account` · `/unfreeze-account` | Admin only | freeze or unfreeze a demo wallet from case review |
| GET | `/api/admin/accounts` | Viewer / Analyst / Admin | list demo accounts and freeze status |
| POST | `/api/admin/accounts/{customer_ref}/freeze` · `/unfreeze` | Admin only | directly administer any demo wallet; changes are audited |
| GET | `/api/admin/accounts/frozen` | Viewer / Analyst / Admin | list currently frozen demo wallets |
| GET | `/api/admin/fraud-alerts` · `/api/admin/audit-logs` | Viewer / Analyst / Admin | alerts / append-only cryptographic audit trail |
| GET | `/api/admin/audit-logs/verify` | Viewer / Analyst / Admin | cryptographically verify the audit log SHA-256 hash chain |
| GET | `/api/admin/users` · POST `/api/admin/users` · PATCH `/api/admin/users/{id}` | Admin only | user management & credential administration |
| GET | `/api/admin/dashboard-stats` · `/api/admin/analytics` | Viewer / Analyst / Admin | KPIs / charts data |
| GET | `/api/admin/monitoring` | Viewer / Analyst / Admin | AFI drift + performance metrics |
| POST | `/api/admin/model/baseline` | Admin only | capture an audited amount/risk reference distribution; resets the PSI comparison and volume counter, without refitting or versioning the model |
| POST | `/api/admin/model/retrain` | Admin only (legacy route) | compatibility alias for baseline capture; it does not retrain the model |
| GET | `/api/events/stream` | Viewer / Analyst / Admin | Server-Sent Events (live updates) |

## 4. Conventions
- Backend: FastAPI + SQLite; model artifacts in `backend/models/` (or
  `MOMO_MODEL_DIR`); services in
  `backend/services/`; persistence through `backend/database/database.py`.
- Frontend: React 19 + Vite; one component + CSS per concern; all API calls
  via `frontend/src/adminApi.js` for protected admin routes.
- Real-time: in-process event bus (`services/events.py`) → authenticated SSE
  stream consumed using fetch because browser `EventSource` cannot set the
  prototype token header.

## 5. Run
```
cd backend
```
In PowerShell, generate a temporary strong admin token and start the API in the
same terminal:
```
$env:MOMO_ADMIN_TOKEN = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$env:MOMO_ADMIN_TOKEN
python -m uvicorn app:app --host 127.0.0.1 --port 5001
```
In a second terminal:
```
cd frontend
npm run dev
```
Demo PINs: WALLET-1001 → 2233 · WALLET-1002 → 7788 · WALLET-1003 → 9911.
Copy the displayed token into the protected admin sign-in. Keep the token
private; it is not stored in the repository and is lost when that terminal
closes.

To train and evaluate a separate candidate against a locally obtained
PaySim-format CSV, run from `backend`:
```
python scripts/train_evaluate.py C:\path\to\paysim.csv --output-dir C:\path\to\model-run --seed 42
```
Set `MOMO_MODEL_DIR` to the resulting directory only when intentionally
testing another artifact; the default runtime is
`models\paysim-runtime`. Evaluation thresholds are not production calibration.
The generated evaluation JSON records its dataset hash, seed, split, metrics,
and scikit-learn version. For the supplied local synthetic dataset, a
memory-bounded sample run is reproducible with:
```
python scripts/train_evaluate.py ..\SYINTHETIC_FINANCIAL_DATASET\paysim.csv --sample-size 300000 --output-dir models\synthetic-paysim-temporal-300k --seed 42
```
`--sample-size` selects exactly that many eligible TRANSFER/CASH_OUT rows
deterministically. Evaluation splits later `step` periods into validation and
test, keeping transactions from one step together; recipient-history features
only use earlier sampled steps. Precision and recall include 95% Wilson
intervals to show uncertainty. The evaluation is preliminary; do not load
resulting artifacts into the simulator as calibrated production thresholds.

To check the hybrid scorer against the repeatable synthetic security scenarios:
```
python scripts/evaluate_synthetic_scenarios.py --runs 100 --output reports/synthetic-scenario-evaluation.json
```
The 13 checks cover a normal control, individual security signals, recipient
novelty, balance/recipient rule interactions, and combined security signals.
The report compares each observed risk band with its expected policy band and
records mean/p95 scoring latency including feature construction, model
inference, and rule evaluation. The pass rate is a deterministic policy-check
result, not statistical precision, recall, or fraud detection performance.
The current 100-run-per-scenario check passed all 13 expected risk-band
outcomes. A high balance-share signal by itself remains LOW, while combined
security signals reach HIGH; this verifies current policy behavior only and
does not establish whether those bands are appropriate for customers.

To measure the scoring API route end to end using an isolated temporary
database:
```
python scripts/benchmark_scoring_api.py --runs 100 --warmup-runs 10 --output reports/api-scoring-benchmark.json
```
This includes in-process ASGI request handling, admin-token authentication,
hybrid scoring, and SQLite persistence. It excludes network sockets, reverse
proxies, and deployment overhead, so deployment-specific latency still needs
to be measured separately.

## 6. Gap analysis and remediation

The proposed scope is broader than the current prototype. The completed items
below are limited to changes implemented in this repository; remaining items
are explicit work, not assumed capabilities.

| Priority | Gap | Effect | Remediation |
|---|---|---|---|
| Closed for prototype | Flask is used where the project scope requires FastAPI | Stack did not meet the stated implementation requirement | FastAPI/Uvicorn API and authenticated event streaming are implemented; validate all UI and API flows before release. |
| Partial / data-access limitation | Dataset training/evaluation evidence | Reproducible chronological evaluation and multi-seed hybrid comparisons use the local synthetic `SYINTHETIC_FINANCIAL_DATASET/paysim.csv`; a 13-case synthetic policy suite also checks expected risk bands | No authorized real-customer dataset is available, and the project will not collect or use one. Treat the IEEE dataset as out of scope until its provenance, license, and permitted use are verified; its schema also does not match the PaySim evaluator. Report the lack of real-world validation as a project limitation, not as a completed validation gap. |
| Partial | Customer behavior profiles | Amount, Ghana-local time-of-day, frequency and recipient profiles are customer-specific; timestamp offsets are normalized to Africa/Accra | Sparse/new-account history and the small fixed demo population still limit profile confidence; synthetic scenario coverage does not replace validation on representative customer populations. |
| Closed for prototype | Controllable synthetic risk signals | Explicit deterministic scenarios replace session-ID-derived SIM-swap behavior | Verify each scenario against outcomes and document that signals remain simulated. |
| Partial / scope limitation | Detection and latency evaluation | The active runtime Isolation Forest is trained on the local synthetic PaySim dataset and has a chronological holdout manifest; 13 explicit synthetic risk-band policy checks and an in-process ASGI scoring-route benchmark are available | The hybrid holdout still reports 17.3% precision at MEDIUM-or-higher and 23.4% at HIGH. Current thresholds reduce false positives versus the prior policy but are not production-calibrated, and synthetic results are not real-world effectiveness evidence. Scenario pass rates are policy checks, not fraud-performance metrics; production network/deployment latency needs separate measurement. |
| Partial | Regulatory source traceability | An illustrative control-alignment map is listed below against the official source register | It is a prototype design aid, not a verified clause-by-clause mapping, regulatory interpretation, or compliance claim. Confirm exact provisions with a qualified compliance/legal reviewer. |
| Closed for prototype | Production readiness: named-user authentication, RBAC, and cryptographic audit protection | Previous prototype relied on a single shared header token and unhashed audit entries without role segregation | Implemented named users with PBKDF2-HMAC-SHA256 salted hashing (100,000 iterations), HMAC bearer session tokens, 15-minute login lockout, 3 distinct RBAC roles (Admin, Analyst, Viewer) enforced across FastAPI endpoints and React UI, dynamic operator attribution, and tamper-evident SHA-256 hash chaining with continuous verification. (Note: Live telecom/bank integrations, institutional real-customer datasets, and statutory legal reviews remain explicit prototype boundaries). |
| Closed in this update | Audit rows could previously be modified/deleted through SQL | “Immutable” was stronger than the implementation | SQLite triggers now reject ordinary `UPDATE` and `DELETE`; this is append-only within the prototype, not administrator-proof storage. |
| Closed in this update | Drift PSI used daily averages and inclusive overlapping bins | The metric did not compare transaction populations reliably | PSI now uses transaction-level older/recent halves, non-overlapping bins, and reports insufficient data until each half has 10 observations. |
| Closed in this update | Analyst case confirmation was labelled “precision” | The label overstated what selected case decisions establish | The dashboard now labels it “analyst-confirmed share” and explains it is not model precision/recall. |
| Closed in this update | Baseline action previously created a synthetic model-version record without changing the PSI reference; threshold sensitivity used fabricated curves | The dashboard could imply a model change and display unsupported precision/FPR estimates | Added a persisted, audited amount/risk reference distribution and a measured validation-split threshold curve; baseline capture resets PSI comparison and volume tracking without changing model version or claiming retraining. The MEDIUM/HIGH boundaries were adjusted to 0.45/0.525 using validation data to reduce holdout false-positive rates; the curve's what-if control does not change live decisions. These remain prototype controls, not BoG/ISO compliance claims. |

### 6.1 Suggested 14-week completion order

1. Weeks 1–2: review the official BoG source provisions with an appropriate
   compliance/legal reviewer if one is available; otherwise retain the
   clause-verification limitation and finalize the illustrative synthetic
   threat matrix without claiming compliance.
2. Weeks 3–5: review the local synthetic-dataset evaluation, document that
   real-customer data is unavailable and out of scope for legal/ethical
   reasons, and preserve the synthetic model/data provenance. Do not represent
   repeated samples from the same source as independent external validation.
3. Weeks 6–8: test customer-specific profiles, synthetic scenarios, FastAPI
   contracts, and protected admin workflows.
4. Weeks 9–11: use synthetic holdouts only for exploratory threshold
   sensitivity; measure end-to-end decision latency and scenario-level
   policy-check failures. Do not promote these results to operational
   calibration.
5. Weeks 12–14: validate case/audit workflows, document limitations, and
   perform a small convenience evaluation using fictional accounts only.

## 7. Notes / limitations
- Demo credentials (PIN/OTP) shown in UI for usability; production would use
  secure OOB channels.
- Simulator balances are in-memory; persistent store is transactional history.
- `notebooks/` contains only a README; it is not a PaySim training pipeline.
- `backend/routes/` contains only a README; routes remain in `backend/app.py`.
- The simulator and unrestricted admin API are for local use with fictional
  accounts. Do not expose this prototype to real customer data or public
  production traffic.
- The custom recipient input accepts fictional wallet identifiers,
  Ghana-style phone-number formats, and bank references for simulation only;
  it does not resolve or transfer money to an actual account.
- Admin authentication supports named-user login with PBKDF2-HMAC-SHA256 salted hashing and role-based permissions (`admin`, `analyst`, `viewer`), alongside a development fallback using `MOMO_ADMIN_TOKEN`. Production deployment with real customer accounts would further require corporate SSO/OIDC integration, hardware MFA, and remote immutable log forwarding.
- The supplied local PaySim dataset is treated as synthetic. A seeded sample of
  300,000 eligible TRANSFER/CASH_OUT rows was selected from 2,770,409 eligible
  rows (856 fraud labels in the sample). The split is chronological by PaySim
  `step`, approximately 60% train / 20% validation / 20% test, with every
  transaction from a step kept in one split. The resulting ranges are steps
  1–281 for training (181,044 rows; 319 fraud labels), 282–354 for validation
  (59,106 rows; 76 fraud labels), and 355–742 for test (59,850 rows; 461 fraud
  labels). The uneven fraud counts are retained rather than stratified away;
  fraud prevalence varies substantially across the simulated timeline.

  Candidate thresholds are ranked on validation F2, which weights recall over
  precision for a prototype where anomaly flags prompt step-up/review rather
  than automatic blocking. The active runtime model uses the 97.5th-percentile
  training-normal threshold of 0.5883. On the untouched later-period test
  split, it flagged 2,358 transactions: 163 fraud and 2,195 non-fraud, for
  35.4% recall (95% Wilson interval 31.1%–39.8%), 6.9% precision (95% Wilson
  interval 6.0%–8.0%), 3.7% false-positive rate, ROC-AUC 0.822, and PR-AUC
  0.052. The test-set fraud prevalence was 0.77%. This demonstrates that the
  PaySim-trained Isolation Forest itself still produces many false positives.

  The active artifacts and their evaluation manifest are in
  `backend/models/paysim-runtime/`. The dataset hash, seed, library version,
  split ranges, confidence intervals, and holdout metrics are recorded with
  the artifacts. Wilson intervals quantify approximate test-set binomial
  uncertainty only; they do not account for temporal clustering, distribution
  shift, sampling choices, or synthetic-data limitations. These Isolation
  Forest metrics do not calibrate the hybrid policy or establish real-world
  performance.

  To check sensitivity to deterministic sampling/model seeds, the same temporal
  evaluation was repeated with seeds 7, 21, and 99 in addition to seed 42.
  Across the four 300,000-row runs, validation-selected thresholds varied from
  the 95th to the 99th training-normal percentile. The selected operating point
  averaged 7.0% precision, 33.0% recall, and 3.9% false-positive rate; observed
  ranges were 4.5%–9.9% precision, 26.7%–39.7% recall, and 2.0%–6.2%
  false-positive rate. The fixed 99th-percentile baseline averaged 7.6%
  precision, 20.4% recall, and 2.0% false-positive rate. The seed-level
  summary is in `backend/reports/synthetic-paysim-temporal-seed-stability.json`,
  with each underlying evaluation saved in a separate directory named
  `synthetic-paysim-temporal-300k[-seed-N]`.

  To compare the weighted hybrid score with its Isolation Forest component on
  the same temporal samples, run from `backend`:
  ```
  python scripts/evaluate_hybrid_vs_model_only.py ..\SYINTHETIC_FINANCIAL_DATASET\paysim.csv --sample-size 300000 --seeds 42 7 21 99 --output reports\synthetic-paysim-hybrid-comparison.json
  ```
  This uses the per-seed temporal training artifacts and the configured rule
  weights, amount threshold, and novel-recipient floor. For both model-only and
  hybrid scores, candidate thresholds are selected on validation data only by
  the same F2 policy. This archived report uses an older per-seed configuration
  with a 0.3 MEDIUM-or-HIGH boundary and a 0.5 HIGH boundary; those are not the
  current runtime boundaries. It also includes same-numeric-cutoff model-only
  comparisons.
  Per-transaction-type confusion counts and metrics are included. PaySim does
  not provide the app's device, network, SIM-swap, customer-pattern,
  unusual-hour, velocity, or frequency signals, so those rules are not treated
  as present in this comparison.

  In the four-seed mean, validation-selected hybrid candidates reached 21.1%
  precision, 43.7% recall, and 1.45% false-positive rate, versus 7.0%, 33.0%,
  and 3.87% for validation-selected model-only candidates. These are
  historical per-seed experiments, not current runtime-policy metrics. At the
  archived hybrid HIGH boundary (0.5), the averages were 21.1% precision, 41.5% recall,
  and 1.23% false-positive rate. The MEDIUM-or-HIGH boundary (0.3) flagged an
  average of 41,445 transactions per test split, with 96.6% recall but only
  1.08% precision and a 69.7% false-positive rate. At the HIGH boundary by
  type, TRANSFER averaged 58.6% recall and 5.95% false-positive rate; CASH_OUT
  averaged 24.5% recall and 0.029% false-positive rate. At the same numeric
  cutoffs, model-only scoring averaged 2.8% precision, 71.4% recall, and 19.5%
  false-positive rate at 0.3; at 0.5 it averaged 4.9% precision, 40.7% recall,
  and 6.2% false-positive rate. These same-number comparisons are diagnostic
  only because model-only and hybrid scores are not calibrated to the same
  scale. Results suggest the balance/recipient rules may add useful signal on
  these synthetic samples, while the 0.3 alert volume demonstrates substantial
  operational-noise risk. They are not comparable to real operational
  detection; they are not current runtime-policy metrics and are insufficient
  for a real-world claim. Seed-level
  metrics are in `backend/reports/synthetic-paysim-hybrid-comparison.json`.

  These repeated runs are sensitivity checks on samples from the same synthetic
  dataset, not independent external validations. The per-run Wilson intervals
  do not capture seed-to-seed variation, temporal clustering, or domain shift.
  Observed variation and false-positive volumes reinforce that these candidate
  thresholds should not be adopted in the runtime model.

  ### 7.1 Illustrative Bank of Ghana control alignment (not clause-verified)

This map shows how prototype controls relate to broad financial-control themes
in the listed sources. It intentionally does not invent clause numbers, quote
requirements, or claim that the prototype satisfies Bank of Ghana obligations.
The source text and exact obligations still require compliance/legal review.

| Illustrative control area | Prototype feature | Evidence / limitation |
|---|---|---|
| Risk-based transaction monitoring | Hybrid risk scoring with behavioral signals; medium creates an alert and high blocks/opens a case | Scores and signals are synthetic; thresholds need independent validation. |
| Suspicious activity review and escalation | Fraud alerts, analyst case workflow, investigation outcome, and audit events | Demonstrates a workflow only; it does not submit a regulatory report or determine a legal reporting duty. |
| Record keeping and traceability | Transaction, risk-assessment, case, and append-only audit records with SHA-256 hash chaining and HMAC signatures | Provides tamper evidence within the local prototype; it is not administrator-proof retention or independently managed immutable/WORM storage. |
| Access control and security monitoring | Named Admin, Analyst, and Viewer accounts; PBKDF2-HMAC-SHA256 password hashing; HMAC session tokens; role-based API authorization; authenticated event stream | Prototype controls only; production deployment still requires managed secrets, TLS, stronger MFA, and security hardening. |
| Customer authentication and fraud response | PIN/OTP simulation, verified-email step-up simulation for medium risk, transaction block, and account/session freeze | Demo credentials and step-up codes are exposed to the simulator UI; no real identity provider, email, or SMS delivery is integrated. |

The links below are official Bank of Ghana source pages/PDFs. Source
metadata/title is recorded, but exact clauses, pages, reporting triggers,
and customer-consent requirements have not been verified.

- [Payment Systems and Services Act, 2019 (Act 987)](https://www.bog.gov.gh/fintech_publications/payment-systems-and-services-act-2019-act-987/) — statutory source; its applicable sections require legal review.
- [Anti-Money Laundering / Combating the Financing of Terrorism Guidelines](https://www.bog.gov.gh/reg_directives/anti-money-laundering-combating-the-financing-of-terrorism-guidelines/) — Bank listing identifies the guideline dated 23 December 2022; specific clauses require verification.
- [Cyber and Information Security Directive](https://www.bog.gov.gh/wp-content/uploads/2019/09/CYBER-AND-INFORMATION-SECURITY-DIRECTIVE.pdf) — source metadata dates it to 5 September 2019; relevant controls require clause-level review.
- [FinTech Sector Report — 2025 Q1](https://www.bog.gov.gh/fintech_publications/fintech-sector-report-2025-q1) — sector context, not a normative directive.
- [2022 Fraud Report for Banks, SDIs and PSPs](https://www.bog.gov.gh/reg_directives/2022-banks-sdis-and-psps-fraud-report/) — report listing; no reporting obligations are inferred from its title.

### 7.2 Data access, provenance, and ethical boundary

The project does not have institutional authorization to access operational
mobile-money or bank transaction records. It will not seek, collect, or use
real customer data for this prototype because the necessary legal, privacy,
and ethics approvals are unavailable. This is a scope limitation: the
prototype's detection results cannot establish effectiveness on real MoMo
customers, and the limitation is not resolved by repeating experiments on
synthetic samples.

The current PaySim evaluation uses the supplied local synthetic
`SYINTHETIC_FINANCIAL_DATASET/paysim.csv`. A separate local
`ieee-fraud-detection/` folder was inspected for suitability. Its
`train_transaction.csv` contains 590,540 rows with 20,663 positive `isFraud`
labels; `test_transaction.csv` contains 506,691 rows and has no fraud-label
column. The files include `TransactionDT`, transaction amount, product/card
fields, and anonymized V/D feature columns rather than PaySim's `step`,
transaction type, and origin/destination balance fields. The existing
PaySim evaluator therefore cannot be run on it as-is, and the unlabeled test
file cannot support labelled test metrics.

The local IEEE folder contains no source, license, or permitted-use
documentation. Its origin, anonymization guarantees, and allowed use cannot
be verified from the supplied files, so it is not used in this project’s
evaluation. If that provenance and the original dataset terms are later
verified as acceptable for the project, it could support a separate,
clearly-labeled e-commerce fraud benchmark using only its labelled training
data and a chronological holdout. Such a benchmark would still not represent
MoMo transaction performance or validate the runtime hybrid scorer.
