# GeoClock

Worker attendance app with geofenced clock in/out. Stack: FastAPI, PostgreSQL + PostGIS, Docker. React PWA coming next.

## Run locally

```bash
cp .env.example .env        # then change the passwords and JWT_SECRET
docker compose up --build
```

- API docs: http://localhost:8000/docs
- pgAdmin: http://localhost:5050 (login with PGADMIN_EMAIL / PGADMIN_PASSWORD from .env)

`schema.sql` loads only on the first start with an empty volume. To reload it: `docker compose down -v`.

## First-time setup

Create the first admin:

```bash
docker compose exec api python create_admin.py "Your Name" 0123456789 "password"
```

Add a site (PostGIS takes longitude first, then latitude):

```sql
INSERT INTO sites (name, geom, radius_m)
VALUES ('Klang Depot', ST_SetSRID(ST_MakePoint(101.4455, 3.0449), 4326)::geography, 150);
```

## What works so far

- Create account (admin or supervisor) with a 7-day invite code
- Register with invite code, login (phone + password, JWT)
- Clock in (GPS + selfie) and clock out, with server-side geofence check
- Mock location and low GPS accuracy are rejected and logged for review
- Admin review queue of flagged attempts

## Not built yet

List, view, update and disable users, breaks, shifts, corrections, sites CRUD, attendance export, React PWA.

## Layout

- `main.py`: API
- `schema.sql`: database schema
- `create_admin.py`: bootstrap script for the first admin
- `prototype/geoclock.html`: clickable UI prototype (open in a browser)

Not tested against a real database yet. Dev setup only: remove `--reload`, the code mount and the exposed ports before deploying.
