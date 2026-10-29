"""One-off: create the first admin (there is no admin yet to invite one).
Usage: DATABASE_URL=... python create_admin.py "Full Name" 0123456789 "password"
"""
import os, sys
import bcrypt
from sqlalchemy import create_engine, text
from main import normalize_phone

name, phone, pw = sys.argv[1], normalize_phone(sys.argv[2]), sys.argv[3]
h = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as c:
    c.execute(text("""INSERT INTO users (full_name, phone, password_hash, role, status)
                      VALUES (:n, :p, :h, 'admin', 'active')"""), {"n": name, "p": phone, "h": h})
print("Admin created:", phone)
