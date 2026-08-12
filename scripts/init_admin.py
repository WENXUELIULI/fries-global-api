#!/usr/bin/env python3
import hashlib
import json
import os
import secrets
from pathlib import Path

project_root = Path.cwd()
private_root = project_root / ".private"
auth_file = private_root / "admin-auth.json"
access_file = project_root / "ADMIN_ACCESS.txt"

private_root.mkdir(parents=True, exist_ok=True)

if auth_file.exists() and access_file.exists():
    print("Admin credentials already exist.")
    raise SystemExit(0)

username = f"fg_admin_{secrets.token_hex(5)}"
password = f"FG-{secrets.token_urlsafe(12)}"
password_salt = secrets.token_hex(24)
password_hash = hashlib.scrypt(
    password.encode(),
    salt=password_salt.encode(),
    n=16384,
    r=8,
    p=1,
    dklen=64,
).hex()
config = {
    "username": username,
    "passwordSalt": password_salt,
    "passwordHash": password_hash,
    "sessionSecret": secrets.token_urlsafe(48),
    "analyticsSecret": secrets.token_urlsafe(48),
}

with open(auth_file, "x", encoding="utf-8") as handle:
    handle.write(json.dumps(config, indent=2) + "\n")
os.chmod(auth_file, 0o600)

access_lines = [
    "FRIES GLOBAL - ADMIN ACCESS",
    "",
    "Admin path: /admin",
    f"Username: {username}",
    f"Password: {password}",
    "",
    "Keep this file private. Do not send it to website visitors.",
    "The public hostname may change; append /admin to the latest public URL.",
    "",
]
with open(access_file, "x", encoding="utf-8", newline="") as handle:
    handle.write("\r\n".join(access_lines))
os.chmod(access_file, 0o600)

print("Admin credentials initialized.")
