# System Architecture — MoMo Fraud Detection & Adaptive Control Layer

> Status: research prototype with implemented transaction controls and
> dashboard; several design requirements remain partially implemented or
> unverified. See the gap analysis in section 6.

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
 │   Adaptive Intelligence (drift PSI · model performance · retrain)   │
 │   Case management · Customer-contact simulation · Account controls │
 │   Fraud alerts · IP/device · Append-only audit logs                 │
 └────────────────────────────────────────────────────────────────────┘
```

## 2. Design-document → implementation mapping

| Requirement | Current implementation | Gap / status |
|---|---|---|
| Post-authentication behavioural layer | PIN/OTP precedes scoring in `momo_simulator.py`; low approves, medium challenges, high blocks and opens a case; explicit behavioral/security signals set a minimum medium-risk score | Implemented in the simulator flow; direct scoring endpoint is assessment-only and does not execute transaction controls. Thresholds still require calibration against labelled data. |
| Customer baseline (amount, timing, frequency, recipient, velocity) | Historical customer transaction records provide amount, Ghana-local transaction hour, daily frequency and per-customer recipient counts; velocity counts sessions opened within the configured window | Implemented using available prototype history and normalized to Africa/Accra time. Sparse/new-account history remains a cold-start limitation, and the small fixed demo population is not representative. |
| Isolation Forest trained on PaySim | Runtime loads versioned model/scaler/median artifacts; `backend/scripts/train_evaluate.py` accepts a local PaySim-format CSV, samples eligible transaction rows deterministically, and evaluates on later `step` periods than training and validation; a separate 300,000-row artifact run is recorded | Reproducible temporal evaluation pipeline; supplied-data evaluation is still a synthetic-data experiment, not independent real-world evidence for the shipped model. |
| Security signals | Device, IP/network, SIM-swap, velocity and unusual-hour signals are explicitly selected through repeatable simulator scenarios; typed IP inputs are also evaluated against the demo baseline | Synthetic signals only. These inputs do not originate from a telecom or network provider and must not be described as live telemetry. |
| Medium-risk step-up | Medium-risk sessions request a second simulated code for the demo customer's pre-registered, verified email; no verified email causes a fail-closed block | No email or SMS is actually sent. The demo code is shown in the UI and is not a secure production factor. SIM-swap scenarios must not rely on the affected phone number. |
| Case management, account freeze, and audit trail | Analysts save an investigation decision, then explicitly mark the investigation complete; the admin case list shows new cases in red, active investigations in yellow, and completed investigations in green; resolved cases leave the active review count but remain visible in case history; account freeze/unfreeze is an independent wallet-level action (restricted to admin role) and never changes investigation status; a confirmed HIGH-risk case may be frozen without waiting for customer contact, while lower-risk freezes require recorded simulated consent; the call demo rings audibly/visually inside the admin browser; audit logs record operator identity, timestamps, previous SHA-256 hash, entry hash, and HMAC signature with automated chain verification | No real phone call or SMS is sent. Append-only triggers prevent ordinary SQL UPDATE/DELETE, and cryptographic SHA-256 hash chaining with HMAC signatures detects tampering and gap insertion. Full production deployment would further forward signed audit entries to remote immutable/WORM storage. |
| Adaptive intelligence | PSI compares transaction-level older/recent halves (warning ≥ 0.10, critical ≥ 0.25); threshold notices activate at PSI ≥ 0.25 or 50+ new transactions. The admin-triggered/automatic action records a prototype version marker and advances transaction-volume tracking; it does not refit the detector, change scoring parameters, or reset the PSI baseline. Marker actions are audited, and unmeasured model metrics remain unset. The Threshold Sensitivity Visualizer simulates FPR vs. Recall trade-offs (0.450–0.650). | Threshold triggers, marker workflow, visualizer, and tests are implemented. Actual detector retraining/re-weighting requires a separately validated training pipeline and is not performed by this prototype endpoint. |
| Evaluation | A seeded time-ordered 300,000-row evaluation reports ROC-AUC, PR-AUC, precision/recall with 95% Wilson intervals, F1, false-positive rate, split label counts, and model-only mean/p95 latency in JSON | Preliminary synthetic-data evidence only; not independent real-world evidence. Timing excludes HTTP and feature construction. Analyst decisions are not model precision/recall. |
| Administrative interface | Full-width overview, responsive navigation/footer, KPI/activity view, alerts/cases, separate adaptive and audit pages; monitoring distinguishes blocked transaction count from active admin-frozen and customer-frozen account counts, and reports resolved cases and mean risk score; protected by named-user accounts (Admin, Analyst, Viewer) with PBKDF2-HMAC-SHA256 salted hashing, HMAC session tokens, 15-minute lockout after 5 failed attempts, and role-based access control (RBAC) enforced on FastAPI routes and UI controls; legacy master token supported as development fallback | Implemented for prototype readiness with 3 distinct operational roles (Admin, Analyst, Viewer). Production deployment with real customer accounts would further require corporate SSO/OIDC integration, hardware MFA (FIDO2), TLS termination, and production secrets management. |
| MoMo simulation inputs | Recipient suggestions plus guided free-form fictional wallet, phone, and bank-reference IDs; editable, validated IPv4/IPv6 | Implemented for synthetic simulations; user-entered recipients do not connect to live wallets or networks. |
| API framework | FastAPI routes preserve the `/api/...` surface; Uvicorn serves HTTP and the authenticated SSE endpoint | Implemented for this prototype. Run locally; protect deployment with TLS and environment-managed secrets. |
| Regulatory traceability | Verified Bank of Ghana source metadata and official links are recorded in section 7 | Clause/page-level obligations have not been verified. The BoG Q1 2025 FinTech Sector Report is contextual, not a directive; do not treat this prototype as legally compliant or as regulatory reporting. |
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
| POST | `/api/admin/model/retrain` | Admin only | record prototype baseline marker and transaction-volume count; audited, with no detector refit or PSI baseline reset |
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

To train and evaluate against a locally obtained PaySim-format CSV, run from
`backend`:
```
python scripts/train_evaluate.py C:\path\to\paysim.csv --output-dir C:\path\to\model-run --seed 42
```
Set `MOMO_MODEL_DIR` to the resulting directory when launching the API to use
those artifacts only for an intentional local experiment. Evaluation artifacts
are not calibrated runtime settings; the shipped model remains the default.
The generated evaluation JSON records its dataset filename, seed, split,
metrics, and model-only latency. For the supplied local synthetic dataset, a memory-bounded sample run is
reproducible with:
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
| Partial / scope limitation | Detection and latency evaluation | Chronological synthetic-dataset holdout, 13 explicit synthetic risk-band policy checks, and an in-process ASGI scoring-route benchmark are available | Current candidate has low test precision and was not applied to runtime controls. Further work within this prototype is limited to available synthetic evidence; do not claim real-world effectiveness. Scenario pass rates are not fraud-performance metrics, and production network/deployment latency needs separate measurement. |
| Partial | Regulatory source traceability | An illustrative control-alignment map is listed below against the official source register | It is a prototype design aid, not a verified clause-by-clause mapping, regulatory interpretation, or compliance claim. Confirm exact provisions with a qualified compliance/legal reviewer. |
| Closed for prototype | Production readiness: named-user authentication, RBAC, and cryptographic audit protection | Previous prototype relied on a single shared header token and unhashed audit entries without role segregation | Implemented named users with PBKDF2-HMAC-SHA256 salted hashing (100,000 iterations), HMAC bearer session tokens, 15-minute login lockout, 3 distinct RBAC roles (Admin, Analyst, Viewer) enforced across FastAPI endpoints and React UI, dynamic operator attribution, and tamper-evident SHA-256 hash chaining with continuous verification. (Note: Live telecom/bank integrations, institutional real-customer datasets, and statutory legal reviews remain explicit prototype boundaries). |
| Closed in this update | Audit rows could previously be modified/deleted through SQL | “Immutable” was stronger than the implementation | SQLite triggers now reject ordinary `UPDATE` and `DELETE`; this is append-only within the prototype, not administrator-proof storage. |
| Closed in this update | Drift PSI used daily averages and inclusive overlapping bins | The metric did not compare transaction populations reliably | PSI now uses transaction-level older/recent halves, non-overlapping bins, and reports insufficient data until each half has 10 observations. |
| Closed in this update | Analyst case confirmation was labelled “precision” | The label overstated what selected case decisions establish | The dashboard now labels it “analyst-confirmed share” and explains it is not model precision/recall. |
| Closed in this update | Adaptive monitoring previously only gave passive recommendations without operational thresholds or sensitivity modeling | Static monitoring required manual intervention without governance limits | Added drift/volume threshold notices (critical PSI ≥ 0.25 or 50+ new transactions), an optional automated prototype version-marker action with transaction-volume tracking, and an interactive Threshold Sensitivity Visualizer (0.450–0.650). The marker is not detector retraining: scoring parameters and PSI comparison baselines remain unchanged, and unmeasured performance metrics are not recorded. |

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
  than automatic blocking. The selected 97.5th-percentile training-normal
  threshold was 0.5827. On the untouched later-period test split, it flagged
  2,381 transactions: 160 fraud and 2,221 non-fraud, for 34.7% recall
  (95% Wilson interval 30.5%–39.2%), 6.7% precision (95% Wilson interval
  5.8%–7.8%), 3.7% false-positive rate, ROC-AUC 0.820, and PR-AUC 0.050.
  The test-set fraud prevalence was 0.77%. The 99th-percentile baseline flagged
  1,287 transactions: 102 fraud and 1,185 non-fraud, for 22.1% recall,
  7.9% precision, and 2.0% false-positive rate. This demonstrates a
  recall-versus-friction trade-off; the selected setting still produces many
  false positives and is unsuitable for runtime adoption.

  Full candidate metrics, confidence intervals, data hash, and split ranges are
  recorded in `backend/models/synthetic-paysim-temporal-300k/evaluation.json`;
  its model artifacts are separate from the shipped model. The previous
  random-stratified run remains in `backend/models/synthetic-paysim-100k/` as
  historical evidence and is superseded by this time-ordered evaluation.
  Wilson intervals quantify approximate test-set binomial uncertainty only;
  they do not account for temporal clustering, distribution shift, sampling
  choices, or synthetic-data limitations.
  The shipped model's training-data provenance is unverified, so this result
  must not be described as independent evidence for that model. Runtime hybrid
  thresholds and model artifacts were not changed. No real-world performance
  or compliance claim is made.

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
  the same F2 policy. It reports the configured 0.3 boundary (MEDIUM-or-HIGH)
  and 0.5 boundary (HIGH) as well as same-numeric-cutoff model-only comparisons.
  Per-transaction-type confusion counts and metrics are included. PaySim does
  not provide the app's device, network, SIM-swap, customer-pattern,
  unusual-hour, velocity, or frequency signals, so those rules are not treated
  as present in this comparison.

  In the four-seed mean, validation-selected hybrid candidates reached 21.1%
  precision, 43.7% recall, and 1.45% false-positive rate, versus 7.0%, 33.0%,
  and 3.87% for validation-selected model-only candidates. At the configured
  hybrid HIGH boundary (0.5), the averages were 21.1% precision, 41.5% recall,
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
  detection and remain insufficient for changing runtime controls. Seed-level
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
| Record keeping and traceability | Transaction, risk-assessment, case, and append-only audit records | Local SQLite protections are not administrator-proof retention or tamper-evident production storage. |
| Access control and security monitoring | Shared admin token, protected admin APIs, and authenticated event stream | Prototype-only access control; no named-user roles, MFA, managed secrets, or production hardening. |
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
