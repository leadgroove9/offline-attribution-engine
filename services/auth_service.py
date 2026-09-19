import hashlib
import hmac
import uuid
from typing import Optional, Tuple
from fastapi import Request
from database.connection import db_router

def hash_password(password: str) -> str:
    salt = uuid.uuid4().hex
    hashed = hashlib.sha256(salt.encode() + password.encode()).hexdigest()
    return f"{salt}:{hashed}"

def verify_password(stored_password: str, provided_password: str) -> bool:
    try:
        salt, hashed = stored_password.split(":")
        check_hashed = hashlib.sha256(salt.encode() + provided_password.encode()).hexdigest()
        return hmac.compare_digest(hashed, check_hashed)
    except Exception:
        return False

def create_session(email: str) -> str:
    token = uuid.uuid4().hex
    conn = db_router.connect()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO user_sessions (token, email) VALUES (?, ?)", (token, email))
    conn.commit()
    conn.close()
    return token

def get_session_email(token: str) -> Optional[str]:
    conn = db_router.connect()
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM user_sessions WHERE token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def delete_session(token: str):
    conn = db_router.connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()

def is_authenticated(request: Request) -> Optional[str]:
    token = request.cookies.get("session_token")
    if not token:
        return None
    return get_session_email(token)

def get_user_role_and_client(email: str) -> Tuple[str, Optional[int]]:
    conn = db_router.connect()
    cursor = conn.cursor()
    cursor.execute("SELECT role, client_id FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return "full", None
