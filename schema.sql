CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE sites (
  id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name      text NOT NULL,
  geom      geography(Point, 4326) NOT NULL,
  radius_m  int NOT NULL DEFAULT 150
);

-- one table for admins, supervisors and workers
CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name      text NOT NULL,
  phone          text UNIQUE NOT NULL,          -- login id (many workers have no email)
  password_hash  text,                          -- NULL until the worker accepts the invite
  role           text NOT NULL DEFAULT 'worker' CHECK (role IN ('worker','supervisor','admin')),
  status         text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','active','disabled')),
  site_id        uuid REFERENCES sites(id),
  device_id      text,                          -- bound on first login
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE invites (
  code        text PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at  timestamptz NOT NULL
);

CREATE TABLE shifts (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES users(id),
  site_id    uuid NOT NULL REFERENCES sites(id),
  work_date  date NOT NULL,
  start_time time NOT NULL,
  end_time   time NOT NULL,
  UNIQUE (user_id, work_date)
);

CREATE TABLE attendance (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id),
  site_id          uuid NOT NULL REFERENCES sites(id),
  clock_in_at      timestamptz NOT NULL DEFAULT now(),
  clock_out_at     timestamptz,
  in_distance_m    real,
  out_distance_m   real,
  in_accuracy_m    real,
  selfie_path      text,
  flags            text[] NOT NULL DEFAULT '{}'
);
-- a worker can only have one open record
CREATE UNIQUE INDEX one_open_record ON attendance(user_id) WHERE clock_out_at IS NULL;

CREATE TABLE breaks (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attendance_id  uuid NOT NULL REFERENCES attendance(id) ON DELETE CASCADE,
  started_at     timestamptz NOT NULL DEFAULT now(),
  ended_at       timestamptz
);

-- rejected or suspicious attempts: the admin review queue
CREATE TABLE flagged_events (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id),
  kind        text NOT NULL,       -- outside_zone | mock_location | low_accuracy
  action      text NOT NULL,       -- clock_in | clock_out
  distance_m  real,
  accuracy_m  real,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE corrections (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attendance_id  uuid NOT NULL REFERENCES attendance(id),
  user_id        uuid NOT NULL REFERENCES users(id),
  requested_out  timestamptz NOT NULL,
  reason         text NOT NULL,
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  decided_by     uuid REFERENCES users(id),
  decided_at     timestamptz
);

CREATE TABLE audit_log (
  id         bigserial PRIMARY KEY,
  actor_id   uuid REFERENCES users(id),
  action     text NOT NULL,
  detail     jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ON attendance (user_id, clock_in_at DESC);
CREATE INDEX ON flagged_events (created_at DESC);
