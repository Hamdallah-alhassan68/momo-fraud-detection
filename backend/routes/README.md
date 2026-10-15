# backend/routes/ — Route modules (production path)

This directory is reserved for separating the FastAPI route handlers out of
`backend/app.py` into modules, one per architectural concern:

- `momo.py` — simulate MoMo core + session flow (`/api/momo/*`)
- `scoring.py` — direct transaction scoring (`/api/transactions/score`)
- `admin.py` — cases, alerts, audit, analytics, monitoring (`/api/admin/*`)
- `realtime.py` — Server-Sent Events stream (`/api/events/stream`)

For this prototype the routes live directly in `backend/app.py` (single-file
API layer) to keep the demo small; the split mirrors the architecture the
design document describes.
