import os, secrets, uuid
from datetime import datetime, timedelta, timezone

import bcrypt, jwt
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from typing import Literal

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
SECRET = os.environ["JWT_SECRET"]
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")  # swap for GCS later
MAX_ACCURACY_M = 50

app = FastAPI(title="GeoClock API")
bearer = HTTPBearer()
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ---------- auth ----------
def make_token(user_id: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode({"sub": user_id, "role": role, "exp": exp}, SECRET, algorithm="HS256")


def current_user(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    try:
        data = jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    with engine.connect() as c:
        u = c.execute(text("SELECT id, role, status, site_id FROM users WHERE id=:i"),
                      {"i": data["sub"]}).mappings().first()
    if not u or u["status"] != "active":
        raise HTTPException(401, "Account not active")
    return dict(u)


def require_admin(u: dict = Depends(current_user)) -> dict:
    if u["role"] not in ("admin", "supervisor"):
        raise HTTPException(403, "Admins only")
    return u


def normalize_phone(raw: str) -> str:
    """Store Malaysian numbers one way: 60123456789 (digits only, country code first)."""
    d = "".join(ch for ch in raw if ch.isdigit())
    if d.startswith("0"):
        d = "6" + d
    if not d.startswith("60") or not (10 <= len(d) <= 12):
        raise HTTPException(422, "Enter a valid Malaysian phone number, e.g. 012-345 6789")
    return d


class NewUser(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    phone: str
    role: Literal["worker", "supervisor", "admin"] = "worker"
    site_id: uuid.UUID | None = None


@app.post("/users", status_code=201)
def create_user(body: NewUser, admin: dict = Depends(require_admin)):
    """Create account. Admin creates anyone; a supervisor can only create workers."""
    if admin["role"] == "supervisor" and body.role != "worker":
        raise HTTPException(403, "Supervisors can only create worker accounts")
    if body.role == "worker" and not body.site_id:
        raise HTTPException(422, "A worker needs a site")
    phone = normalize_phone(body.phone)
    code = secrets.token_urlsafe(6)
    with engine.begin() as c:
        if body.site_id and not c.execute(text("SELECT 1 FROM sites WHERE id=:s"), {"s": str(body.site_id)}).first():
            raise HTTPException(404, "Site not found")
        try:
            uid = c.execute(text("""INSERT INTO users (full_name, phone, role, site_id)
                                    VALUES (:n, :p, :r, :s) RETURNING id"""),
                            {"n": body.full_name.strip(), "p": phone, "r": body.role,
                             "s": str(body.site_id) if body.site_id else None}).scalar_one()
        except IntegrityError:
            raise HTTPException(409, "That phone number already has an account")
        c.execute(text("INSERT INTO invites (code, user_id, expires_at) VALUES (:c, :u, now() + interval '7 days')"),
                  {"c": code, "u": uid})
        c.execute(text("""INSERT INTO audit_log (actor_id, action, detail)
                          VALUES (:a, 'create_user', jsonb_build_object('user', :u::text, 'role', :r::text))"""),
                  {"a": admin["id"], "u": str(uid), "r": body.role})
    return {"user_id": uid, "phone": phone, "invite_code": code, "expires_in_days": 7}


class Register(BaseModel):
    invite_code: str
    password: str
    device_id: str | None = None


@app.post("/auth/register")
def register(body: Register):
    if len(body.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")
    pw = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    with engine.begin() as c:
        row = c.execute(text("""SELECT i.user_id, u.role FROM invites i JOIN users u ON u.id=i.user_id
                                WHERE i.code=:c AND i.expires_at > now()"""), {"c": body.invite_code}).first()
        if not row:
            raise HTTPException(400, "Invalid or expired invite")
        c.execute(text("UPDATE users SET password_hash=:p, status='active', device_id=:d WHERE id=:u"),
                  {"p": pw, "d": body.device_id, "u": row.user_id})
        c.execute(text("DELETE FROM invites WHERE code=:c"), {"c": body.invite_code})
    return {"token": make_token(str(row.user_id), row.role)}


class Login(BaseModel):
    phone: str
    password: str


@app.post("/auth/login")
def login(body: Login):
    with engine.connect() as c:
        u = c.execute(text("SELECT id, role, status, password_hash FROM users WHERE phone=:p"),
                      {"p": normalize_phone(body.phone)}).mappings().first()
    if not u or u["status"] != "active" or not bcrypt.checkpw(body.password.encode(), u["password_hash"].encode()):
        raise HTTPException(401, "Wrong phone or password")
    return {"token": make_token(str(u["id"]), u["role"])}


# ---------- clock in / out ----------
def geofence(c, site_id, lat, lng):
    """Distance in metres from the point to the site, plus the allowed radius. Computed by PostGIS, never the client."""
    return c.execute(text("""
        SELECT ST_Distance(geom, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography) AS d, radius_m
        FROM sites WHERE id=:s"""), {"lat": lat, "lng": lng, "s": str(site_id)}).one()


def reject_if_bad(c, user, action, lat, lng, accuracy, is_mock):
    """Runs the checks shared by clock in and clock out. Logs flagged attempts for admin review."""
    def flag(kind, d=None):
        c.execute(text("""INSERT INTO flagged_events (user_id, kind, action, distance_m, accuracy_m)
                          VALUES (:u, :k, :a, :d, :acc)"""),
                  {"u": user["id"], "k": kind, "a": action, "d": d, "acc": accuracy})
    if is_mock:
        flag("mock_location")
        c.commit()
        raise HTTPException(403, "Mock location detected")
    if accuracy > MAX_ACCURACY_M:
        flag("low_accuracy")
        c.commit()
        raise HTTPException(422, f"GPS accuracy too low ({accuracy:.0f} m). Move to open sky and retry.")
    g = geofence(c, user["site_id"], lat, lng)
    if g.d > g.radius_m:
        flag("outside_zone", g.d)
        c.commit()
        raise HTTPException(403, f"You are {g.d:.0f} m from the site (limit {g.radius_m} m)")
    return g.d


@app.post("/clock/in")
async def clock_in(lat: float = Form(...), lng: float = Form(...), accuracy: float = Form(...),
                   is_mock: bool = Form(False), selfie: UploadFile = File(...),
                   user: dict = Depends(current_user)):
    if not (selfie.content_type or "").startswith("image/"):
        raise HTTPException(422, "Selfie must be an image")
    path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.jpg")
    with open(path, "wb") as f:
        f.write(await selfie.read())
    with engine.connect() as c:
        d = reject_if_bad(c, user, "clock_in", lat, lng, accuracy, is_mock)
        try:
            rec = c.execute(text("""INSERT INTO attendance (user_id, site_id, in_distance_m, in_accuracy_m, selfie_path)
                                    VALUES (:u, :s, :d, :a, :p) RETURNING id, clock_in_at"""),
                            {"u": user["id"], "s": user["site_id"], "d": d, "a": accuracy, "p": path}).mappings().one()
        except Exception:
            c.rollback()
            raise HTTPException(409, "You are already clocked in")
        c.commit()
    return dict(rec)


class Point(BaseModel):
    lat: float
    lng: float
    accuracy: float
    is_mock: bool = False


@app.post("/clock/out")
def clock_out(body: Point, user: dict = Depends(current_user)):
    with engine.connect() as c:
        d = reject_if_bad(c, user, "clock_out", body.lat, body.lng, body.accuracy, body.is_mock)
        rec = c.execute(text("""UPDATE attendance SET clock_out_at=now(), out_distance_m=:d
                                WHERE user_id=:u AND clock_out_at IS NULL
                                RETURNING id, clock_in_at, clock_out_at"""),
                        {"u": user["id"], "d": d}).mappings().first()
        if not rec:
            raise HTTPException(409, "You are not clocked in")
        c.execute(text("UPDATE breaks SET ended_at=now() WHERE attendance_id=:a AND ended_at IS NULL"), {"a": rec["id"]})
        c.commit()
    return dict(rec)


# ---------- admin ----------
@app.get("/admin/review-queue")
def review_queue(_: dict = Depends(require_admin)):
    with engine.connect() as c:
        rows = c.execute(text("""SELECT f.*, u.full_name FROM flagged_events f JOIN users u ON u.id=f.user_id
                                 ORDER BY f.created_at DESC LIMIT 100""")).mappings().all()
    return [dict(r) for r in rows]
