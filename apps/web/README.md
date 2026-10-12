# SECO Web Admin

React + Vite + TypeScript. The UI reuses the design (CSS and tokens) of the
screen-flow prototype `index.html` at the repo root.

## Run

The deployed app is https://iad-seco.vercel.app (Vercel), on the backend at
https://seco-backend-api.onrender.com (Render). Nothing has to run locally:
no Docker, no local API, database or broker.

To work on the UI, from the repo root:

```bash
npm install
npm run dev:web
```

Open http://localhost:5173 and sign in with an ADMIN account
(e.g. `tranbnb@seco.com` / `seco`). Member and technician accounts are rejected.

`/api` and `/ws` are proxied to the Render backend (`SECO_API_URL` overrides
it), so no CORS setup is needed in development.

## Screens

| Task | Route |
|---|---|
| FE-A01 Dashboard | `/` |
| FE-A02 Station List | `/stations` |
| FE-A03 Station Detail | `/stations/:stationId` |
| FE-A04 User Management | `/users` |
| FE-A05 User Detail | `/users/:userId` |
| FE-A06 Charging Sessions | `/sessions` |
| FE-A07 Session Detail | `/sessions/:sessionId` |
| FE-A08 Transactions | `/transactions` |
| FE-A09 Fault Management | `/faults` |
| FE-A10 Maintenance History | `/maintenance` |

Data comes from `/api/v1/admin/*`; pages refresh themselves from WebSocket
events (`/ws?token=…`), so station status, telemetry and sessions update live.

## Checks

```bash
npm --workspace apps/web run typecheck
npm --workspace apps/web run build
```
