# MoMo Sentinel limitations and implementation roadmap

This is a research prototype. It is not yet a production mobile-money service
or a validated fraud-control system. This roadmap distinguishes **cash cost**
(provider or hosting charges) from **engineering time** (implementation,
testing, and operational work). Costs are deliberately marked “quote needed”
where they depend on the user's provider account, country tariff, or plan.
No paid service is enabled by this document.

## Current limitations and what unlocks them

| Capability | Current limitation / why it is not production-ready | What is needed | Cash cost | Engineering effort / priority |
|---|---|---|---|---|
| Dashboard | Counts are based on data stored by this app. `LIVE` means the in-process event stream is connected, not that MTN or a carrier is sending live data. Hosted counts have not been independently checked in this review. | Verify the deployed database and dashboard with an authorized admin; label simulated activity clearly. Use a shared event bus before running multiple backend instances. | No new provider charge for a single-instance check. Shared infrastructure may cost money. | Low for verification; higher for multi-instance events. Do first. |
| IP/network mismatch | The code compares an observed address with trusted history, but a reverse proxy may appear as the client unless trusted proxy ranges are configured correctly. Demo accounts also use synthetic IP baselines. | Confirm Render's actual proxy path, set `TRUSTED_PROXY_IPS` only to trusted proxy ranges, and test with known client networks. Do not trust arbitrary forwarded headers. | Usually no new cost for the existing IP signal; a commercial IP-intelligence feed would cost extra and is optional. | Low for configuration and tests. Do first. |
| Risk rules and thresholds | The active Isolation Forest and thresholds use synthetic PaySim data, not representative Ghana customer traffic. A single heuristic does not always have its own fixed risk tier; rules combine with the model score. Large-amount units are not a validated GHS threshold. | Define policy with domain experts, validate on authorized representative data, measure false positives/negatives, and test changes before production. | Data acquisition/review may cost money; no provider cost for local synthetic tests. | High and ongoing. Do before production decisions. |
| SIM-swap detection | `RECENT_SIM_SWAP` is a simulator input. No carrier or SIM-change feed is connected. | Obtain access to an authorized mobile-network or risk-data provider; agree on a recency window, consent/privacy basis, lookup behavior, and failure handling. | Provider pricing and access terms are quote/account dependent; possible per-lookup charges. | Medium to high. Start with provider quotes before coding the live lookup. |
| Real outgoing calls | The case-review call is a browser ringtone and a recorded simulated outcome. It does not dial a phone. | Choose a voice provider with Ghana coverage, configure a verified caller ID, add a backend-only call API and status callback, and require an analyst action with rate limits and audit records. | Usually usage charges per call/minute and possibly account/sender setup; quote needed. | Medium. Get a provider quote before integration. |
| Real SMS | Sandbox SMS is not delivered to real handsets. Production SMS requires an enabled provider account and credits. | Complete provider activation, verify sender/route, and use production credentials only on the backend. | Per-message charges; exact tariff is shown by the provider account. | Low to medium, depending on activation requirements. Defer until approved. |
| MTN Collection / Disbursement | Separate MTN API clients and admin routes exist, but the hosted Blueprint sets `MOMO_SIMULATION_ONLY=true` and selects sandbox Disbursement. Customer cash-in/cash-out remain simulations. Medium-risk Disbursement has no live step-up path; an MTN callback receiver is not implemented. | Obtain MTN production approval and distinct credentials, finish step-up and callback/status reconciliation, test with controlled accounts, and complete operational and compliance review. Keep production gated until all checks pass. | Provider/business fees and real transaction charges may apply; verify directly with MTN. | High. Do not switch production on yet. |
| Medium-risk Collection gate | A production payment request must not be sent until a medium-risk step-up is completed. This repository now returns `STEP_UP_REQUIRED` without contacting MTN for MEDIUM risk. | Implement and test a challenge/verification flow before permitting any MEDIUM-risk live Collection request. | No new provider cost for the gate itself. | Low for this safety gate; medium for the full step-up flow. Gate now in place. |
| Persistent database | The repository Blueprint specifies SQLite at `/var/data/fraud.db` on a 1 GB disk, but the currently deployed service was confirmed to have no disk and `MOMO_DATABASE_PATH=/opt/render/project/src/fraud.db`. Render's Free filesystem is ephemeral, so the active database file is lost on deploy, restart, or spin-down. The SQLite database file is also tracked in Git; that committed snapshot is not a live backup. | Preserve and inspect any local/backup database before restoring. For persistence, attach a paid disk and point `MOMO_DATABASE_PATH` at its mount (for the Blueprint, `/var/data/fraud.db`), or migrate to a database service. Keep one backend instance with SQLite. | Free web services cannot attach persistent disks. A paid web-service plan and disk cost money; check the current Render upgrade screen for the exact total. Free Render Postgres is limited to 1 GB, expires after 30 days, and has no backups, so it is only a temporary test option. | Low to retain SQLite once storage is configured; high for PostgreSQL migration and data verification. |
| PostgreSQL migration | The backend uses `sqlite3` directly and SQLite-specific SQL/schema behavior; a PostgreSQL URL cannot simply be substituted. No PostgreSQL driver is currently installed. | Choose an adapter/ORM, add migrations, convert SQL and error handling, migrate rows while preserving audit-chain integrity, and run regression/restore tests. | Database hosting is recurring cash cost; engineering time is a separate one-time cost. Exact hosting price is plan-dependent. | High. Get a quote and confirm the need before starting. |

## Work sequence

1. **No-new-provider work:** keep simulation-only enabled; validate hosted
   dashboard metrics and client-IP handling; continue local tests.
2. **Finish transaction safety:** complete medium-risk step-up and provider
   status/callback handling before enabling any production Collection or
   Disbursement route.
3. **Request quotes before integration:** check the account-specific prices
   for Live SMS, Ghana voice calling, SIM-change data, MTN production fees, and
   managed PostgreSQL. Do not share API keys in chat or commit them.
4. **Decide feature by feature:** implement only after the user approves the
   provider and acceptable cost. Keep each paid integration behind a server-side
   configuration gate and test it in the provider sandbox first.

## Important safeguards

- `MOMO_SIMULATION_ONLY=true` is the hosted default. Do not change it to `false`
  until the relevant production path has been reviewed and tested.
- Do not use the Git-tracked SQLite file as a runtime database or commit a
  newer copy containing customer/tester records. Keep backups private and
  separate from source control.
- Keep provider secrets in Render's backend secret settings, never in the
  frontend or this repository.
- A provider accepting a request is not proof of settlement. Reconcile status
  using the same provider reference before retrying.
- Use only authorized test data and obtain appropriate consent and compliance
  guidance before handling real customer data or transactions.

Render's current storage limits are documented at
[Persistent Disks](https://render.com/docs/disks) and
[Free instances](https://render.com/docs/free).
