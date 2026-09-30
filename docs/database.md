# SECO Database

MongoDB, one shared database for the whole team on **MongoDB Atlas** (free M0 tier).
Atlas runs a replica set, which the API needs for transactions (wallet settlement,
top-up, guest payment).

## Quick start (team members)

1. Get the connection string from the DB owner (sent privately, never in git).
2. In `apps/api`, copy the example env and fill it in:
   ```bash
   cp .env.example .env
   # set MONGO_URI=mongodb+srv://... and MQTT_CLIENT_ID=seco-api-<yourname>
   ```
3. Run the API as usual: `python -m app`. Pending migrations are applied at startup.

To work offline against a local MongoDB instead, keep the default
`MONGO_URI=mongodb://localhost:27017/?replicaSet=rs0`.

## Commands (from `apps/api`, conda env `iad`)

| Command | What it does |
|---|---|
| `python -m app.db.migrate` | Apply pending migrations and list them |
| `python -m app.db.migrate --status` | List migrations without applying |
| `python -m app.db.seed` | Seed demo stations, accounts, RFID cards, vehicles |
| `python -m app.db.seed --reset-passwords` | Also reset every demo account's password to `seco` |
| `python -m scripts.seed_user --email ... --name ... --role USER` | Create one extra account |
| `python -m scripts.seed_rfid --email ... --uid ...` | Link another RFID UID (e.g. the Wokwi card) |
| `python -m pytest` | Tests; same cluster as `MONGO_URI` but always the separate `seco_test` database, never `seco` |

Seeding is safe to re-run on the shared database: it never changes a station's
live state (status, current session, lock) or an existing account's password and
balance.

## Migrations

`app/db/migrations/` holds versioned, idempotent migrations. Each one creates the
collections with a strict `$jsonSchema` validator and their indexes, and is recorded
in `schema_migrations` so it runs once per database.

| Version | Task | Collections |
|---|---|---|
| 0001 | DB-01 | `users`, `vehicles`, `rfid_cards`, `charging_stations` |
| 0002 | DB-02 | `charging_sessions`, `wallet_holds`, `transactions`, `payment_intents` |
| 0003 | DB-03 | `maintenance_records`, `error_logs`, `audit_logs` |

To change the schema, add `m0004_<name>.py` with `VERSION = 4` and an `up(db)`,
and append it to `MIGRATIONS` in `app/db/migrations/__init__.py`. Do not edit a
migration that is already applied.

Key rules enforced by indexes:

- one open (`ACTIVE`/`WAITING_PAYMENT`) session per station and per member;
- a session is charged / paid at most once per transaction type;
- a guest session has at most one open QR and one successful payment.

## Demo data (DB-04)

| Station | Name | State |
|---|---|---|
| EVCS-01 | SECO Station 01 - FPT Đà Nẵng | **Real** (ESP32 / Wokwi); online when it sends heartbeats |
| EVCS-02 | SECO Cầu Rồng | Mock, available |
| EVCS-03 | SECO Biển Mỹ Khê | Mock, available |
| EVCS-04 | SECO Sân bay Đà Nẵng | Mock, maintenance |
| EVCS-05 | SECO Ngũ Hành Sơn | Mock, offline |
| EVCS-06 | SECO Vincom Hải Châu | Mock, error |

Mock stations (`is_simulated: true`) exist for the map; no hardware backs them.

All demo accounts use the password **`seco`**.

| Account | Name | Role | Balance | RFID UID | Vehicle |
|---|---|---|---|---|---|
| admin@seco.com | SECO Admin | ADMIN | – | – | – |
| tranbnb@seco.com | Bùi Ngọc Bảo Trân | ADMIN | – | – | – |
| tunv@seco.com | Nguyễn Văn Tú | ADMIN | – | – | – |
| nguyenlb@seco.com | Lê Bảo Nguyên | ADMIN | – | – | – |
| hieuln@seco.com | Lê Ngọc Hiếu | ADMIN | – | – | – |
| tech@seco.com | SECO Technician | TECHNICIAN | – | `7E1C2A9F` | – |
| user@seco.com | SECO Member | USER | 200 000 | `A1B2C3D4` (+ `D4E5F6A7` locked, + 1 card pending registration) | 43A-123.45 VinFast VF e34 |

## Setting up the shared Atlas cluster (DB owner, once)

1. Sign in at <https://cloud.mongodb.com> and create a project `SECO`.
2. **Create cluster** → **M0 Free**, provider AWS, region **Singapore (ap-southeast-1)**.
3. **Database Access** → add a database user, e.g. `seco_app`, password
   authentication, role **Read and write to any database**. Use a generated password.
4. **Network Access** → add the team's IPs, or `0.0.0.0/0` for a class demo
   (anyone with the password can then connect, so keep the password private).
5. **Connect** → **Drivers** → copy the `mongodb+srv://...` string and insert the password.
6. From `apps/api`, point at Atlas and initialise it:
   ```bash
   export MONGO_URI='mongodb+srv://seco_app:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority&appName=seco'
   python -m app.db.migrate
   python -m app.db.seed
   ```
7. Share the URI with the team privately (Zalo / Messenger), not in git or the task sheet.

## Notes

- Money is stored as integer VND; energy and power as `Decimal128`.
- Business IDs are UUID strings; `_id` stays an `ObjectId`.
- Every API instance that shares the database runs the session ticker and may send
  commands (STOP, LOCK, UNLOCK) to a station. So all API instances **must use the
  same MQTT broker as the station** (the one the Wokwi / ESP32 firmware connects
  to), each with its own `MQTT_CLIENT_ID`. A teammate on a private local broker
  would bill sessions but their commands would never reach the station.
