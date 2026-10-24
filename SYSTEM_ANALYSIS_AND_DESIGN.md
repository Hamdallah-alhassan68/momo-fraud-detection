# System Analysis and Design — MoMo Sentinel

> **Project:** Ghana Mobile Money (MoMo) fraud detection and adaptive control
> prototype
> **System boundary:** React web interface, FastAPI application, fraud scoring
> services, and SQLite persistence. Wallets, bank accounts, PIN/OTP delivery,
> device/IP signals, and customer activity are simulated; this is not a live
> mobile-money integration.

This design document describes the implementation in this repository. The
diagrams use Mermaid so they render inline on GitHub and in compatible Markdown
viewers. Use-case and misuse-case views use UML-style actors, boundaries, and
relationships; the database design is an entity-relationship diagram (ERD)
alongside the UML class diagram.

## 1. System analysis

### 1.1 Problem and purpose

Mobile-money transfers can be exposed to account takeover, suspicious new
recipients, unusual transaction amounts or timing, and rapid repeated
transactions. MoMo Sentinel demonstrates a post-authentication fraud-control
layer: after a customer verifies a PIN or OTP, the system combines an
Isolation Forest anomaly score with security and behavioral rules. It approves
low-risk activity, requests simulated additional verification for medium risk,
and blocks high-risk activity while opening an investigation case.

The prototype also provides analysts with alerts and case-management tools,
administrators with account and user controls, and compliance viewers with
read-only dashboards and audit-log verification.

### 1.2 Actors

| Actor | Responsibilities |
|---|---|
| Customer | Initiates a simulated transfer, completes PIN/OTP verification, responds to a step-up check, and can freeze or unfreeze their own demo wallet using a PIN. |
| Fraud analyst | Reviews alerts and cases, records investigation decisions and simulated contact outcomes, and can submit a direct transaction for scoring. |
| Administrator | Performs analyst actions and manages user roles, demo-wallet freezes, and the model-drift reference baseline. |
| Compliance viewer | Reads dashboards, cases, alerts, monitoring, accounts, and audit records; verifies audit-chain integrity. |
| Attacker (misuse actor) | Represents an unauthorized party attempting account misuse, request manipulation, or unauthorized access to the operations console. This is a threat-model actor, not a supported system user. |

### 1.3 Key requirements

**Functional**

1. Start a wallet transfer, cash-out, or cash-in session and select the
   corresponding simulated PIN or OTP challenge.
2. Verify the customer's initial credential before scoring a transaction.
3. Build behavioral/security features and return a hybrid risk score, risk
   level, decision, and reasons.
4. Approve low-risk activity; require a simulated email step-up for medium-risk
   activity; block high-risk activity and create an alert, case, and audit
   entry.
5. Persist transaction assessments and support role-restricted investigation,
   account controls, dashboard monitoring, and audit verification.

**Security and quality**

1. Enforce role-based access for administrative functions.
2. Record consequential system/operator actions in an append-only,
   hash-chained audit log.
3. Validate transaction inputs and fail closed when required step-up
   verification is unavailable.
4. Keep the design explicit about simulated signals and credentials; no
   external telecom, bank, email, or SMS service is integrated.

### 1.4 Scope and assumptions

- This is an educational/research prototype using synthetic/demo data.
- The customer identity, wallets, balances, PINs, device/IP baselines, and
  second-factor codes are simulator data. The step-up code is displayed for
  demonstration and is not actually sent.
- Direct `/api/transactions/score` is an analyst-only assessment endpoint; it
  does not itself approve or execute a simulated transfer.
- The database uses SQLite. Entity relationships below describe the intended
  logical relationships and declared schema references.
- Fraud thresholds and model behavior are research controls, not production
  calibration or evidence of real-customer performance.

## 2. Use case diagram

```mermaid
flowchart LR
  Customer([Customer])
  Analyst([Fraud Analyst])
  Admin([Administrator])
  Viewer([Compliance Viewer])

  subgraph System["MoMo Sentinel"]
    UC1([Initiate MoMo transaction])
    UC2([Verify PIN / initial OTP])
    UC3([Assess transaction risk])
    UC4([Complete simulated step-up])
    UC5([Approve or block transaction])
    UC6([Self-freeze / unfreeze wallet])
    UC7([Review alerts and cases])
    UC8([Record investigation decision and contact outcome])
    UC9([Directly score transaction])
    UC10([View dashboards and monitoring])
    UC11([Inspect / verify audit trail])
    UC12([Manage operator accounts and roles])
    UC13([Freeze / unfreeze demo wallet])
    UC14([Capture drift reference baseline])
  end

  Customer --> UC1
  Customer --> UC2
  Customer --> UC4
  Customer --> UC6
  Analyst --> UC7
  Analyst --> UC8
  Analyst --> UC9
  Analyst --> UC10
  Analyst --> UC11
  Admin --> UC7
  Admin --> UC8
  Admin --> UC9
  Admin --> UC10
  Admin --> UC11
  Admin --> UC12
  Admin --> UC13
  Admin --> UC14
  Viewer --> UC7
  Viewer --> UC10
  Viewer --> UC11

  UC1 -. "include" .-> UC2
  UC2 -. "include" .-> UC3
  UC3 -. "include" .-> UC5
  UC4 -. "extend: medium risk" .-> UC5
  UC4 -. "simulated code; not emailed" .-> UC4
```

## 3. Activity diagram — transaction assessment

```mermaid
flowchart TD
  Start((Start)) --> Enter[Customer enters transaction details]
  Enter --> Validate[Validate wallet, recipient, amount, and account/session]
  Validate --> Valid{Input valid and account active?}
  Valid -- No --> InputError[Return validation error] --> End((End))
  Valid -- Yes --> Session[Create pending MoMo session]
  Session --> Credential[Customer submits PIN or initial OTP]
  Credential --> CredValid{Initial credential valid?}
  CredValid -- No --> CredError[Reject verification; do not score or complete] --> End
  CredValid -- Yes --> Features[Build transaction and behavioral features]
  Features --> IF[Calculate Isolation Forest score]
  IF --> Rules[Calculate security-rule score]
  Rules --> Hybrid[Combine scores and classify risk]
  Hybrid --> Risk{Risk level?}

  Risk -- LOW --> LowSave[Persist completed transaction, assessment, and audit entry]
  LowSave --> LowBalance[Update simulated balances]
  LowBalance --> LowApprove[Return APPROVED] --> End

  Risk -- MEDIUM --> Email{Verified demo email available?}
  Email -- No --> MedBlock[Persist blocked transaction, alert, case, and audit entry]
  MedBlock --> MedBlocked[Return BLOCKED] --> End
  Email -- Yes --> OTP[Create simulated step-up code and set session to STEP_UP]
  OTP --> Challenge[Ask customer to enter code]
  Challenge --> OTPValid{Step-up code valid?}
  OTPValid -- No --> OTPError[Do not complete transfer; return verification error] --> End
  OTPValid -- Yes --> MedSave[Persist completed transaction, assessment, and audit entry]
  MedSave --> MedBalance[Update simulated balances]
  MedBalance --> MedApprove[Return APPROVED after step-up] --> End

  Risk -- HIGH --> HighSave[Persist blocked transaction, alert, case, and audit entry]
  HighSave --> Freeze[Temporarily freeze customer session]
  Freeze --> HighBlock[Return BLOCKED] --> End
```

**Note:** A failed second-factor attempt is not described as a completed
transaction; the transfer is not booked. The simulator returns a verification
error for an incorrect code.

## 4. Sequence diagram — medium-risk transfer

```mermaid
sequenceDiagram
  actor Customer
  participant UI as React MoMo Simulator
  participant API as FastAPI API
  participant MoMo as MoMo Simulator Service
  participant Detector as Fraud Detector
  participant DB as SQLite

  Customer->>UI: Enter transfer and submit
  UI->>API: POST /api/momo/transaction/initiate
  API->>MoMo: initiate_transaction(details)
  MoMo->>DB: Save pending session
  DB-->>MoMo: session_id
  MoMo-->>API: Session and PIN/OTP challenge
  API-->>UI: Challenge response (demo hint)
  UI-->>Customer: Request initial PIN/OTP

  Customer->>UI: Submit initial credential
  UI->>API: POST /api/momo/transaction/verify
  API->>MoMo: verify_transaction(session_id, code)
  MoMo->>DB: Load pending session and customer history
  DB-->>MoMo: Session and baseline data
  MoMo->>MoMo: Verify initial credential and derive signals
  MoMo->>Detector: assess_transaction(transaction, history)
  Detector-->>MoMo: MEDIUM score, reasons, decision
  MoMo->>DB: Save STEP_UP status and simulated code
  MoMo-->>API: STEP_UP_REQUIRED
  API-->>UI: Step-up challenge (not actually sent)
  UI-->>Customer: Display demo code prompt

  Customer->>UI: Submit step-up code
  UI->>API: POST /api/momo/transaction/verify (step_up=true)
  API->>MoMo: verify_transaction(session_id, code, step_up=true)
  MoMo->>MoMo: Verify step-up code
  MoMo->>DB: Save completed transaction, assessment, audit entry
  DB-->>MoMo: Persisted transaction
  MoMo-->>API: APPROVED after step-up
  API-->>UI: Transaction result
  UI-->>Customer: Show approval and risk explanation
```

## 5. Class diagram — domain and service model

```mermaid
classDiagram
  class Customer {
    <<simulated actor>>
    customer_ref
    name
    phone
    email_verified
  }
  class MomoSession {
    session_id
    customer_ref
    transaction_type
    amount
    recipient
    verification_method
    status
    risk_level
    risk_score
  }
  class Transaction {
    id
    transaction_reference
    amount
    transaction_type
    origin_account
    destination_account
    status
    timestamp
  }
  class RiskAssessment {
    id
    risk_score
    risk_level
    decision
    isolation_forest_score
    rule_score
    reasons
  }
  class FraudAlert {
    id
    severity
    status
    reason
  }
  class FraudCase {
    id
    case_status
    assigned_to
    analyst_decision
    analyst_notes
  }
  class AdminUser {
    id
    username
    password_hash
    role
    is_active
  }
  class AccountStatus {
    customer_ref
    frozen
    reason
  }
  class AuditLog {
    id
    action
    performed_by
    timestamp
    prev_hash
    entry_hash
    hmac_signature
  }
  class FraudDetector {
    <<service>>
    assess_transaction()
    calculate_if_score()
    calculate_rule_score()
  }
  class MoMoSimulator {
    <<service>>
    initiate_transaction()
    verify_transaction()
    freeze_account()
    unfreeze_account()
  }
  class AuthService {
    <<service>>
    authenticate_user()
    create_access_token()
    decode_access_token()
  }

  Customer "1" -- "0..*" MomoSession : starts
  Customer "1" -- "0..1" AccountStatus : has
  MomoSession "0..1" --> "0..1" Transaction : finalized as
  Transaction "1" -- "1..*" RiskAssessment : assessed by
  Transaction "1" -- "0..*" FraudAlert : raises
  Transaction "1" -- "0..*" FraudCase : investigated as
  FraudAlert "0..1" <-- "0..*" FraudCase : linked alert
  Transaction "0..1" <-- "0..*" AuditLog : refers to
  MoMoSimulator ..> FraudDetector : requests assessment
  MoMoSimulator ..> MomoSession : manages
  AuthService ..> AdminUser : authenticates
```

**Interpretation:** `Customer` and `MoMoSimulator` describe the simulator's
logical domain, not persistent customer tables. The sessions, transactions,
assessments, alerts, cases, operator accounts, account statuses, and audit
entries map to SQLite tables.

## 6. Database design (ERD)

```mermaid
erDiagram
  transactions {
    INTEGER id PK
    TEXT transaction_reference UK
    REAL amount
    TEXT transaction_type
    TEXT origin_account
    TEXT destination_account
    TEXT status
    TEXT timestamp
    TEXT ip_address
    TEXT device_id
  }
  risk_assessments {
    INTEGER id PK
    INTEGER transaction_id FK
    REAL risk_score
    TEXT risk_level
    TEXT decision
    TEXT reasons
  }
  fraud_alerts {
    INTEGER id PK
    INTEGER transaction_id FK
    TEXT severity
    TEXT status
    TEXT reason
  }
  fraud_cases {
    INTEGER id PK
    INTEGER transaction_id FK
    INTEGER alert_id FK
    TEXT case_status
    TEXT assigned_to
    TEXT analyst_decision
    TEXT analyst_notes
  }
  audit_logs {
    INTEGER id PK
    INTEGER transaction_id FK
    TEXT action
    TEXT performed_by
    TEXT timestamp
    TEXT prev_hash
    TEXT entry_hash
    TEXT hmac_signature
  }
  momo_sessions {
    INTEGER id PK
    TEXT session_id UK
    TEXT customer_ref
    TEXT transaction_type
    REAL amount
    TEXT recipient
    TEXT verification_method
    TEXT status
    REAL risk_score
  }
  admin_users {
    INTEGER id PK
    TEXT username UK
    TEXT password_hash
    TEXT role
    INTEGER is_active
    INTEGER failed_login_count
    TEXT locked_until
  }
  account_status {
    TEXT customer_ref PK
    INTEGER frozen
    TEXT reason
    TEXT frozen_at
  }
  model_monitoring {
    INTEGER id PK
    TEXT day UK
    INTEGER total_transactions
    REAL anomaly_rate
    REAL psi_amount
    REAL psi_risk
  }
  model_drift_baseline {
    INTEGER id PK
    TEXT samples
    INTEGER transaction_count
    TEXT recorded_by
    TEXT recorded_at
  }

  transactions ||--o{ risk_assessments : has
  transactions ||--o{ fraud_alerts : generates
  transactions ||--o{ fraud_cases : investigated_by
  fraud_alerts o|--o{ fraud_cases : may_link
  transactions o|--o{ audit_logs : referenced_by
```

**Database notes**

- `transactions` is the central record. Each fraud assessment refers to a
  transaction; alerts and cases also refer to the event that caused them.
- Audit records may be system-wide (no transaction) or transaction-specific.
  The database has triggers preventing ordinary updates/deletes to
  `audit_logs`; the entry hash links to the previous signed hash and an HMAC
  signature supports verification.
- `momo_sessions` represents a pending/step-up/completed/blocked simulator
  flow. Customer profiles and simulated wallet balances are held in service
  memory rather than a normalized customer table.
- `model_monitoring` and `model_drift_baseline` support dashboard monitoring;
  the baseline captures reference distributions and does not itself retrain
  the fraud model.
- The diagram shows core columns for readability; consult
  `backend/database/database.py` for the full schema and migration-added
  fields.

## 7. Misuse-case diagram — cybersecurity view

This is the UML-oriented cybersecurity diagram likely meant by the professor:
a **misuse-case diagram** shows what an attacker could try to do and which
security controls mitigate those attempts. It complements (rather than
replaces) the functional use-case diagram.

```mermaid
flowchart LR
  Customer([Customer])
  Analyst([Fraud Analyst])
  Admin([Administrator])
  Attacker([Attacker])

  subgraph System["MoMo Sentinel"]
    M1([Misuse: use stolen customer credential])
    M2([Misuse: replay or alter transaction request])
    M3([Misuse: access analyst/admin functions without permission])
    M4([Misuse: change or erase audit evidence])
    C1([Verify PIN / OTP - simulator only])
    C2([Validate transaction inputs and session state])
    C3([Authenticate operator and enforce RBAC])
    C4([Append-only signed audit chain and verification])
    C5([Assess risk and apply step-up / block controls])
  end

  Customer --> C1
  Analyst --> C3
  Admin --> C3
  Attacker --> M1
  Attacker --> M2
  Attacker --> M3
  Attacker --> M4

  M1 -. "mitigated by" .-> C1
  M1 -. "mitigated by" .-> C5
  M2 -. "mitigated by" .-> C2
  M2 -. "mitigated by" .-> C5
  M3 -. "mitigated by" .-> C3
  M4 -. "mitigated by" .-> C4

  NoteC1["Demo PINs and visible OTP hints are not production authentication."]
  NoteC4["Hash chaining detects tampering; local SQLite is not WORM storage."]
  C1 -.-> NoteC1
  C4 -.-> NoteC4
```

## 8. STRIDE threat summary

| STRIDE category | Example threat to this system | Existing mitigation / design response | Residual risk or limitation |
|---|---|---|---|
| Spoofing | Attacker pretends to be a customer or operator. | Simulated PIN/OTP checks; named operator accounts; signed, expiring bearer tokens. | Customer credentials and email step-up are demo-only; no real identity provider, MFA delivery, or live customer authentication. |
| Tampering | Alter a transaction request, stored assessment, or risk signal. | Input validation, session-state checks, parameterized database writes, and persisted assessment/audit records. | Browser-supplied simulator data and synthetic signals are not trusted telemetry; production APIs need deployment-grade controls and validation. |
| Repudiation | Operator denies making a case or account-control change. | Operator attribution and signed, hash-chained audit entries; audit-chain verification endpoint. | Local database administrators can still affect the underlying file or secret; remote immutable retention is not implemented. |
| Information disclosure | Unauthorized access to transaction, wallet, or investigation data. | Role-based access distinguishes admin, analyst, and viewer functions. | Deployment/network hardening, data minimization, encryption-at-rest policy, and production privacy controls are outside this prototype. |
| Denial of service | Excessive requests or long-lived event-stream connections exhaust the API. | Validation and bounded per-listener event queues exist in the implementation. | No documented production rate-limiting, distributed event service, or high-availability capacity controls. |
| Elevation of privilege | Viewer or analyst attempts admin-only actions. | Route dependencies enforce role checks; sensitive admin actions are audited. | Must protect signing secrets and deployment configuration; role tests and deployment configuration need continued review. |

## 9. Security and research limitations

- Do not use real customer details, PINs, bank credentials, or financial
  transactions with this prototype.
- Built-in accounts and simulator credentials are public demo fixtures, not
  secure defaults for a real deployment.
- Device, IP, SIM-swap, velocity, frequency, and unusual-hour signals in the
  simulator are synthetic or derived from demo data.
- The email step-up is not delivered externally; displayed codes must not be
  treated as a real second factor.
- Model metrics and thresholds are based on synthetic data and do not
  demonstrate real-world fraud-detection performance.
- A production system would require authorized representative data, real
  identity and telecom/bank integrations, secure secret management, stronger
  operational controls, independent security testing, and appropriate
  compliance review.

For implemented route and architecture details, see
[ARCHITECTURE.md](./ARCHITECTURE.md).
